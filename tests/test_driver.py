import math
import unittest
from dataclasses import replace
from keithley2231a import (Profile, ChannelProfile, LimitsError, SafetyError,
                          Keithley2231A, Acquisition, ChannelStats, Reading)

class FakeTransport:
    """Stateful minimal SCPI emulator; validates command sequences, no hardware."""
    def __init__(self):
        self.cmds = []
        self.active_ch = 1
        self.values = {ch: {'VOLT': 0., 'CURR': 0., 'VOLT:LIM': 30.,
                            'VOLT:LIM:STAT': 0., 'CHAN:OUTP': 0.,
                            'OUTP:TIM:DEL': 10., 'OUTP:TIM:STAT': 0.} for ch in (1,2,3)}
        self.flags = {'OUTP:SER': 0., 'OUTP:PARALLEL': 0., 'OUTP:TRAC': 0., 'OUTP:STAT:ALL': 0.}
        self.operation_status_masks = {ch: None for ch in (1, 2, 3)}
        self.questionable_status_masks = {ch: 0 for ch in (1, 2, 3)}
        self.failed_query = None
        self.closed = False

    def write(self, cmd):
        self.cmds.append(cmd)
        arg = cmd.split(' ', 1)
        key = arg[0].upper()
        if key == 'INST:NSEL': self.active_ch = int(arg[1]); return
        if key in self.values[self.active_ch]:
            self.values[self.active_ch][key] = float(arg[1]); return
        if key in self.flags:
            self.flags[key] = float(arg[1])
            if key == 'OUTP:STAT:ALL':
                for ch in self.values: self.values[ch]['CHAN:OUTP'] = float(arg[1])
            return
        if key == 'OUTP':
            self.flags['OUTP:STAT:ALL'] = float(arg[1])
            for ch in self.values: self.values[ch]['CHAN:OUTP'] = float(arg[1])
            return
        if key in ('SYST:REM','SYST:RWLOCK','SYST:LOC','SYST:BEEP'): return
        raise AssertionError(f'Unexpected SCPI: {cmd}')

    def query(self, cmd):
        self.cmds.append(cmd)
        if cmd == self.failed_query: raise RuntimeError('simulated link failure')
        key = cmd.upper().removesuffix('?')
        if key == '*IDN': return 'Keithley instruments, 2231A-30-3, TEST, 1.04-1.04'
        if key == 'INST:NSEL': return str(self.active_ch)
        if key in self.values[self.active_ch]:
            val = self.values[self.active_ch][key]
            return str(int(val)) if key in ('VOLT:LIM:STAT','CHAN:OUTP','OUTP:TIM:STAT') else str(val)
        if key in self.flags: return str(int(self.flags[key]))
        if key == 'MEAS:VOLT': return str(self.values[self.active_ch]['VOLT'])
        if key == 'MEAS:CURR': return str(self.values[self.active_ch]['CURR']/2)
        if key.startswith('STAT:OPER:INST:ISUM') and key.endswith(':COND'):
            ch = int(key.split('ISUM')[1].split(':')[0])
            if self.operation_status_masks[ch] is not None:
                return str(self.operation_status_masks[ch])
            return '9' if self.values[ch]['CHAN:OUTP'] else '0'
        if key.startswith('STAT:QUES:INST:ISUM') and key.endswith(':COND'):
            ch = int(key.split('ISUM')[1].split(':')[0])
            return str(self.questionable_status_masks[ch])
        if key == 'SYST:ERR': return '0,No error'
        if key == 'SYST:VERS': return '1999.0'
        if key in ('*STB','STAT:QUES:COND','STAT:OPER:COND'): return '0'
        raise AssertionError(f'Unexpected query: {cmd}')

    def close(self): self.closed = True

def profile(wiring='independent', expected='auto'):
    return Profile('demo', (
        ChannelProfile(3.3,.2,1,.1,expected),
        ChannelProfile(5,.3,1,.1),
        ChannelProfile(3.3,.1,1,.05)), wiring=wiring)

class TestProfile(unittest.TestCase):
    def test_limits(self):
        with self.assertRaises(LimitsError): ChannelProfile(30, 4).validate(1)
        with self.assertRaises(LimitsError): ChannelProfile(6, 1).validate(3)
        with self.assertRaises(LimitsError): ChannelProfile(2, 1, 3).validate(1)
        with self.assertRaises(LimitsError): ChannelProfile(3, .2, .01, .3).validate(1)
        with self.assertRaises(Exception): ChannelProfile(float('nan'), .2).validate(1)
        with self.assertRaises(Exception): ChannelProfile(3.33, .2, .001, .1).validate(1)

    def test_json(self):
        p = Profile.from_json('examples/safe_3ch.json')
        self.assertEqual(p.channels[2].max_voltage, 3.3)
        self.assertEqual(p.wiring, 'independent')

class TestDevice(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTransport()
        self.dev = Keithley2231A(self.fake)
        self.dev.apply_profile(profile())

    def test_outputs_off_after_apply(self):
        self.assertEqual(self.fake.flags['OUTP:STAT:ALL'],0)
        self.assertTrue(self.dev.armed)
        self.dev.verify_profile()
        self.assertEqual(self.fake.values[1]['VOLT:LIM'],3.3)

    def test_set_allowed_then_forbid_overshoot(self):
        self.dev.set_channel(1, voltage=2.5, current=.15)
        self.assertEqual(self.fake.values[1]['VOLT'], 2.5)
        before = len(self.fake.cmds)
        with self.assertRaises(LimitsError): self.dev.set_channel(1, voltage=3.31)
        self.assertEqual(len(self.fake.cmds), before)

    def test_on_off_and_profile_lock(self):
        self.dev.output(1, True)
        self.assertTrue(self.fake.values[1]['CHAN:OUTP'])
        with self.assertRaises(SafetyError): self.dev.apply_profile(profile())
        self.dev.output(1, False)
        self.assertFalse(self.fake.values[1]['CHAN:OUTP'])

    def test_guard_detects_external_change(self):
        self.fake.values[2]['VOLT:LIM'] = 5.01
        with self.assertRaises(SafetyError): self.dev.verify_profile()
        self.assertFalse(self.dev.armed)
        self.assertTrue(self.dev.faulted)
        self.assertEqual(self.fake.flags['OUTP:STAT:ALL'],0)

    def test_trips_on_measured_excess(self):
        self.dev.output(1, True)
        self.fake.values[1]['VOLT'] = 5.0
        with self.assertRaises(SafetyError): self.dev.read_channel(1)
        self.assertEqual(self.fake.flags['OUTP:STAT:ALL'], 0)

    def test_stage_then_measure(self):
        sample = self.dev.read_channel(1)
        self.assertEqual(sample.voltage_v, 1.0)
        self.assertEqual(sample.current_a, .05)
        self.assertEqual(sample.power_w, .05)
        self.assertEqual(sample.voltage_set_v, 1.0)
        self.assertEqual(sample.current_limit_set_a, .1)
        self.assertEqual(sample.regulation, 'OFF')

    def test_regulation_uses_documented_questionable_status_when_operation_mode_is_clear(self):
        self.dev.output(3, True)
        self.fake.operation_status_masks[3] = 8
        self.fake.questionable_status_masks[3] = 1

        sample = self.dev.read_channel(3)

        self.assertEqual(sample.regulation, 'CV')
        self.assertEqual(sample.operation_status_mask, 8)
        self.assertEqual(sample.questionable_status_mask, 1)
        self.assertEqual(sample.regulation_source, 'questionable')

        self.fake.questionable_status_masks[3] = 2
        cc_sample = self.dev.read_channel(3)
        self.assertEqual(cc_sample.regulation, 'CC')
        self.assertEqual(cc_sample.regulation_source, 'questionable')
        self.assertEqual(cc_sample.operation_status_mask, 8)

        self.fake.operation_status_masks[3] = 9
        self.fake.questionable_status_masks[3] = 2
        conflicting = self.dev.read_channel(3)
        self.assertEqual(conflicting.regulation, 'UNKNOWN')
        self.assertEqual(conflicting.operation_status_mask, 9)
        self.assertEqual(conflicting.questionable_status_mask, 2)
        self.assertIsNone(conflicting.regulation_source)

        self.fake.operation_status_masks[3] = 9
        self.fake.questionable_status_masks[3] = 3
        ambiguous = self.dev.read_channel(3)
        self.assertEqual(ambiguous.regulation, 'UNKNOWN')
        self.assertIsNone(ambiguous.regulation_source)

    def test_regulation_source_reports_evidence_without_extra_queries(self):
        self.dev.output(3, True)
        for operation, questionable, mode, source in (
                (9, 1, 'CV', 'both'), (10, 2, 'CC', 'both'),
                (9, 0, 'CV', 'operation'), (10, 0, 'CC', 'operation'),
                (8, 1, 'CV', 'questionable'), (8, 2, 'CC', 'questionable'),
                (8, 0, 'UNKNOWN', None), (8, 3, 'UNKNOWN', None),
                (11, 1, 'UNKNOWN', None), (9, 2, 'UNKNOWN', None)):
            with self.subTest(operation=operation, questionable=questionable):
                self.fake.operation_status_masks[3] = operation
                self.fake.questionable_status_masks[3] = questionable
                before = len(self.fake.cmds)
                sample = self.dev.read_channel(3)
                self.assertEqual(sample.regulation, mode)
                self.assertEqual(sample.regulation_source, source)
                status_queries = [cmd for cmd in self.fake.cmds[before:]
                                  if cmd.startswith('STAT:')]
                self.assertEqual(status_queries, [
                    'STAT:OPER:INST:ISUM3:COND?', 'STAT:QUES:INST:ISUM3:COND?'])
        self.dev.output(3, False)
        off = self.dev.read_channel(3)
        self.assertEqual(off.regulation, 'OFF')
        self.assertIsNone(off.regulation_source)
        unchecked = self.dev.read_channel(3, with_regulation=False)
        self.assertIsNone(unchecked.regulation_source)
        self.assertIsNone(unchecked.operation_status_mask)
        self.assertIsNone(unchecked.questionable_status_mask)

    def test_ch3_questionable_workaround_preserves_expected_regulation_trip(self):
        for expected, mask in (('cv', 1), ('cc', 2)):
            with self.subTest(expected=expected):
                settings = profile()
                settings = replace(settings, channels=(
                    settings.channels[0], settings.channels[1],
                    replace(settings.channels[2], expected_regulation=expected)))
                fake = FakeTransport()
                dev = Keithley2231A(fake)
                dev.apply_profile(settings)
                dev.set_channel(3, voltage=2.0, current=.08)
                dev.output(3, True)
                fake.operation_status_masks[3] = 8
                fake.questionable_status_masks[3] = mask
                sample = dev.read_channel(3)
                self.assertTrue(sample.enabled)
                self.assertEqual(sample.voltage_set_v, 2.0)
                self.assertEqual(sample.current_limit_set_a, .08)
                self.assertEqual(sample.regulation, expected.upper())
                self.assertEqual(sample.regulation_source, 'questionable')
                # A clear error queue must not turn absent mode evidence into success.
                fake.questionable_status_masks[3] = 0
                with self.assertRaises(SafetyError):
                    dev.read_channel(3)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertTrue(all(not value['CHAN:OUTP'] for value in fake.values.values()))

    def test_sequence_and_timer(self):
        self.dev.sequence([(0,1,True),(0,2,True),(0,1,False)])
        self.assertEqual(self.fake.values[1]['CHAN:OUTP'],0)
        self.assertEqual(self.fake.values[2]['CHAN:OUTP'],1)
        self.dev.timer(2, 2.2)
        self.assertEqual(self.fake.values[2]['OUTP:TIM:DEL'],2.2)
        self.dev.timer(2, None)

    def test_emergency_off_enters_remote_before_output_command(self):
        before = len(self.fake.cmds)
        self.dev.emergency_off()
        self.assertEqual(self.fake.cmds[before:before + 2], ['SYST:REM', 'OUTP 0'])

    def test_profile_forbids_releasing_lock(self):
        with self.assertRaises(SafetyError): self.dev.remote(lock_panel=False)
        self.dev.remote(lock_panel=True)

    def test_regulation_check_cannot_be_bypassed(self):
        self.dev.emergency_off()
        self.dev.apply_profile(profile(expected='cv'))
        with self.assertRaises(SafetyError): self.dev.read_channel(1, with_regulation=False)

    def test_connection_failure_attempts_off(self):
        self.fake.failed_query = 'VOLT:LIM?'
        with self.assertRaises(SafetyError): self.dev.verify_profile()
        self.assertFalse(self.dev.armed)
        self.assertIn('OUTP 0', self.fake.cmds)

    def test_tracking_requires_fresh_profile(self):
        self.dev.emergency_off()
        self.dev.apply_profile(profile('tracking'))
        self.assertTrue(self.fake.flags['OUTP:TRAC'])
        with self.assertRaises(SafetyError): self.dev.set_channel(1, voltage=2)
        self.dev.combined_outputs(True, confirm_all_channels_safe=True)
        self.assertTrue(self.fake.flags['OUTP:STAT:ALL'])

    def test_combined_physical_wiring_confirmation(self):
        self.dev.emergency_off()
        with self.assertRaises(SafetyError): self.dev.apply_profile(profile('series'))
        self.dev.apply_profile(profile('series'), confirmed_wiring='series')
        with self.assertRaises(SafetyError): self.dev.combined_outputs(True)
        with self.assertRaises(SafetyError): self.dev.read_channel(2)
        r = self.dev.read_channel(1)
        self.assertEqual(r.label,'CH1+CH2')
        self.assertIsNone(r.voltage_set_v)
        self.assertIsNone(r.current_limit_set_a)

    def test_fixed_setpoint_tampering_within_caps_trips(self):
        for key, value in (('VOLT', 1.01), ('CURR', .101)):
            with self.subTest(key=key):
                fake = FakeTransport(); dev = Keithley2231A(fake)
                base = profile()
                dev.apply_profile(Profile('fixed', base.channels, access='power_only'))
                fake.values[1][key] = value
                with self.assertRaises(SafetyError): dev.verify_profile()
                self.assertFalse(dev.armed)
                self.assertFalse(fake.values[1]['CHAN:OUTP'])

    def test_control_remembers_authorized_setpoints(self):
        self.dev.set_channel(1, voltage=2.5, current=.15)
        self.dev.verify_profile()
        self.fake.values[1]['VOLT'] = 2.49
        with self.assertRaises(SafetyError): self.dev.verify_profile()

    def test_global_off_must_check_each_channel(self):
        original = self.fake.write
        def stuck_off(command):
            original(command)
            if command in ('OUTP 0', 'CHAN:OUTP 0'):
                self.fake.values[2]['CHAN:OUTP'] = 1
        self.fake.write = stuck_off
        with self.assertRaises(SafetyError): self.dev.emergency_off()
        self.assertFalse(self.dev.armed)
        self.assertTrue(self.dev.faulted)

    def test_emergency_off_falls_back_to_individual_channel_off(self):
        self.dev.output(1, True)
        original = self.fake.write
        def global_off_ignored(command):
            original(command)
            if command == 'OUTP 0': self.fake.values[1]['CHAN:OUTP'] = 1
        self.fake.write = global_off_ignored
        self.dev.emergency_off()
        self.assertFalse(self.fake.values[1]['CHAN:OUTP'])
        self.assertIn('CHAN:OUTP 0', self.fake.cmds)

    def test_external_output_on_is_not_authorized(self):
        self.fake.values[3]['CHAN:OUTP'] = 1
        with self.assertRaises(SafetyError): self.dev.verify_profile()
        self.assertFalse(self.dev.armed)

    def test_status_io_failure_disarms(self):
        self.fake.failed_query = 'SYST:VERS?'
        with self.assertRaises(SafetyError): self.dev.status()
        self.assertTrue(self.dev.faulted)
        with self.assertRaises(SafetyError): self.dev.output(1, True)

    def test_output_requires_boolean_without_io(self):
        before = len(self.fake.cmds)
        with self.assertRaises(ValueError): self.dev.output(1, 'OFF')
        self.assertEqual(len(self.fake.cmds), before)

    def test_identity_rejects_similar_model_and_channel_requires_integer(self):
        from keithley2231a import InstrumentError
        for channel in (1.0, True, '1'):
            with self.subTest(channel=channel):
                with self.assertRaises(ValueError): self.dev.output(channel, True)
        original = self.fake.query
        self.fake.query = lambda cmd: 'Keithley instruments, 2231A-30-30, TEST, fw' if cmd == '*IDN?' else original(cmd)
        with self.assertRaises(InstrumentError): self.dev.identity()
        self.assertFalse(self.dev.armed)

    def test_identity_change_aborts_profile_before_setpoint_writes(self):
        from keithley2231a import InstrumentError
        fake = FakeTransport()
        dev = Keithley2231A(fake)
        original = fake.query
        self.assertEqual(dev.identity(), 'Keithley instruments, 2231A-30-3, TEST, 1.04-1.04')
        fake.cmds.clear()
        fake.query = lambda cmd: 'Keithley instruments, 2231A-30-3, TEST, 1.04-0.01' if cmd == '*IDN?' else original(cmd)
        with self.assertRaisesRegex(InstrumentError, 'identity changed'):
            dev.apply_profile(profile())
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertNotIn('VOLT 0', fake.cmds)
        self.assertNotIn('CURR 0', fake.cmds)
        self.assertFalse(any(cmd.startswith(('VOLT:LIM ', 'VOLT:LIM:STAT ', 'CURR ', 'VOLT '))
                             for cmd in fake.cmds))
        self.assertIn('OUTP 0', fake.cmds)

    def test_apply_disables_inherited_timers(self):
        self.dev.emergency_off()
        for values in self.fake.values.values(): values['OUTP:TIM:STAT'] = 1
        self.dev.apply_profile(profile())
        self.assertTrue(all(values['OUTP:TIM:STAT'] == 0 for values in self.fake.values.values()))

    def test_combined_enable_is_blocked_pending_setpoint_qualification(self):
        for mode in ('series', 'parallel'):
            with self.subTest(mode=mode):
                self.dev.emergency_off()
                self.dev.apply_profile(profile(mode), confirmed_wiring=mode)
                with self.assertRaises(SafetyError):
                    self.dev.combined_outputs(True, confirm_all_channels_safe=True)
                self.assertFalse(self.fake.values[3]['CHAN:OUTP'])

    def test_close_safely_disarms(self):
        self.dev.output(1, True)
        self.dev.close()
        self.assertEqual(self.fake.flags['OUTP:STAT:ALL'], 0)
        self.assertTrue(self.fake.closed)

class TestStats(unittest.TestCase):
    def make(self, timestamp, amps, volts=2, enabled=True):
        return Reading(1, 'utc', timestamp, volts, amps, volts*amps, 'CV', enabled, 'CH1')

    def test_welford_and_integration(self):
        s = ChannelStats()
        s.add(self.make(0,1))
        s.add(self.make(3600,3), max_gap_s=4000)
        self.assertAlmostEqual(s.current.mean,2)
        self.assertAlmostEqual(s.current.stddev,1)
        self.assertAlmostEqual(s.ampere_hours,2)
        self.assertAlmostEqual(s.watt_hours,4)
        s.reset_statistics()
        self.assertEqual(s.current.count,0)
        self.assertEqual(s.ampere_hours,2)
        s.reset_statistics(reset_energy=True)
        self.assertEqual(s.ampere_hours,0)

    def test_disconnected_gaps_are_not_integrated(self):
        s = ChannelStats()
        s.add(self.make(0,1))
        s.add(self.make(100,1), max_gap_s=10)
        self.assertEqual(s.skipped_gaps,1)
        self.assertEqual(s.ampere_hours,0)
        s.add(self.make(101,1,enabled=False), max_gap_s=10)
        self.assertEqual(s.ampere_hours,0)

    def test_acquisition(self):
        fake=FakeTransport(); dev=Keithley2231A(fake); dev.apply_profile(profile())
        a=Acquisition(dev,interval_s=.1)
        self.assertEqual(len(a.sample()),3)
        self.assertEqual(a.stats[1].voltage.count,1)


class TestTransportNetwork(unittest.TestCase):
    def test_tcp_transparent_round_trip(self):
        import socket
        import threading
        from keithley2231a import TcpTransport
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0)); server.listen(1)
        port = server.getsockname()[1]
        got = []
        def worker():
            try:
                client, _ = server.accept()
                with client:
                    buf=b''
                    while not buf.endswith(b'\r\n'):
                        buf += client.recv(256)
                    got.append(buf)
                    client.sendall(b'Keithley,OK\n')
            finally: server.close()
        th=threading.Thread(target=worker,daemon=True); th.start()
        t=TcpTransport('127.0.0.1',port,timeout=1)
        try: self.assertEqual(t.query('*IDN?'), 'Keithley,OK')
        finally: t.close(); th.join(timeout=1)
        self.assertEqual(got, [b'*IDN?\r\n'])

    def test_serial_timeout_and_stale_input_require_new_transport(self):
        from unittest.mock import Mock, patch
        from keithley2231a import SerialTransport, TransportError
        for stale in (False, True):
            with self.subTest(stale=stale):
                port = Mock()
                port.in_waiting = int(stale)
                port.readline.return_value = b'partial'
                port.write.side_effect = lambda value: len(value)
                with patch('serial.Serial', return_value=port):
                    io = SerialTransport('SIMULATED')
                with self.assertRaises(TransportError): io.query('*IDN?')
                port.readline.return_value = b'1\n'
                with self.assertRaises(TransportError): io.query('CHAN:OUTP?')
                with self.assertRaises(TransportError): io.write('CHAN:OUTP 1')
                io.close()

    def test_tcp_timeout_cannot_reuse_late_reply(self):
        from unittest.mock import Mock, patch
        from keithley2231a import TcpTransport, TransportError
        sock = Mock()
        sock.recv.side_effect = [TimeoutError('timeout'), b'1\n']
        with patch('socket.create_connection', return_value=sock):
            io = TcpTransport('SIMULATED', 5025)
        with self.assertRaises(TransportError): io.query('*IDN?')
        with self.assertRaises(TransportError): io.query('CHAN:OUTP?')
        with self.assertRaises(TransportError): io.write('CHAN:OUTP 1')
        io.close()

    def test_unrecorded_still_checked(self):
        p=Profile('record selective',(
            ChannelProfile(3.3,.2,1,.1,record=False),
            ChannelProfile(5.,.2,1,.1),
            ChannelProfile(3.3,.1,1,.05)))
        fake=FakeTransport(); dev=Keithley2231A(fake); dev.apply_profile(p)
        dev.output(1, True)
        fake.values[1]['VOLT'] = 9.
        a=Acquisition(dev)
        with self.assertRaises(SafetyError): a.sample()
        self.assertEqual(fake.flags['OUTP:STAT:ALL'], 0)

if __name__ == '__main__': unittest.main()
