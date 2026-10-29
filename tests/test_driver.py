import math
import unittest
from dataclasses import replace
from keithley2231a import (Profile, ProfileError, ChannelProfile, InstrumentError, LimitsError, SafetyError,
                          SeriesProfile, ParallelProfile, Keithley2231A, Acquisition, ChannelStats, Reading)

class FakeTransport:
    """Stateful minimal SCPI emulator; validates command sequences, no hardware."""
    def __init__(self):
        self.cmds = []
        self.remote = False
        self.active_ch = 1
        self.values = {ch: {'VOLT': 0., 'CURR': 0., 'VOLT:LIM': 30.,
                            'VOLT:LIM:STAT': 0., 'CHAN:OUTP': 0.,
                            'OUTP:TIM:DEL': 10., 'OUTP:TIM:STAT': 0.} for ch in (1,2,3)}
        self.flags = {'OUTP:SER': 0., 'OUTP:PARALLEL': 0., 'OUTP:TRAC': 0., 'OUTP:STAT:ALL': 0.}
        self.operation_status_masks = {ch: None for ch in (1, 2, 3)}
        self.questionable_status_masks = {ch: 0 for ch in (1, 2, 3)}
        self.failed_query = None
        self.closed = False
        self.tracking_ratio = None
        self.tracking_fault = False

    def write(self, cmd):
        self.cmds.append(cmd)
        if cmd in ('SYST:REM', 'SYST:RWLOCK'):
            self.remote = True
            return
        if cmd == 'SYST:LOC':
            self.remote = False
            return
        if not self.remote:
            raise RuntimeError('SCPI command requires Remote mode')
        arg = cmd.split(' ', 1)
        key = arg[0].upper()
        if key == 'INST:NSEL': self.active_ch = int(arg[1]); return
        if key in self.values[self.active_ch]:
            value = float(arg[1])
            self.values[self.active_ch][key] = value
            if (key == 'VOLT' and value and self.active_ch in (1, 2)
                    and self.flags['OUTP:TRAC'] and self.tracking_ratio is not None
                    and not self.tracking_fault):
                self.values[3 - self.active_ch]['VOLT'] = (
                    value * self.tracking_ratio if self.active_ch == 1 else value / self.tracking_ratio)
            return
        if key in self.flags:
            self.flags[key] = float(arg[1])
            if key == 'OUTP:TRAC' and float(arg[1]):
                v1, v2 = (self.values[ch]['VOLT'] for ch in (1, 2))
                self.tracking_ratio = v2 / v1 if v1 and v2 else None
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
        if not self.remote and cmd not in ('*IDN?', 'SYST:ERR?'):
            raise RuntimeError('SCPI query requires Remote mode')
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

    def set_debug_status_trace(self, observer): self.debug_status_trace = observer

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

    def test_series_profile_models_total_setpoint_and_soft_cap(self):
        series = SeriesProfile('series', (2., 3.), 4.5, .3, 2.5, .1,
                               ChannelProfile(4., .5, 2., .3))
        self.assertEqual(series.wiring, 'series')
        self.assertEqual(series.max_voltage, 4.5)
        with self.assertRaises(ProfileError):
            SeriesProfile('spoofed mode', (2., 3.), 4.5, .3, 2., .1,
                          ChannelProfile(4., .5, 0., .1), wiring='parallel')
        with self.assertRaises(ProfileError):
            SeriesProfile('spoofed access', (2., 3.), 4.5, .3, 2., .1,
                          ChannelProfile(4., .5, 0., .1), access='power_only')
        for kwargs in (
                {'max_voltage': 5.1},
                {'max_voltage': 5.0, 'initial_voltage': 5.01},
                {'max_current': 0.0},
                {'channel_voltage_limits': (31., 3.)},
                {'initial_voltage': 2.501},):
            values = dict(name='invalid', channel_voltage_limits=(2., 3.),
                          max_voltage=5., max_current=.3, initial_voltage=2.5,
                          initial_current=.1, channel3=ChannelProfile(4., .5, 2., .3))
            values.update(kwargs)
            with self.assertRaises((ValueError, TypeError)):
                SeriesProfile(**values)

    def test_json(self):
        p = Profile.from_json('examples/safe_3ch.json')
        self.assertEqual(p.channels[2].max_voltage, 3.3)
        self.assertEqual(p.wiring, 'independent')

class TestDevice(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTransport()
        self.dev = Keithley2231A(self.fake)
        self.dev.apply_profile(profile(), confirmed_wiring='independent')

    def test_output_states_handles_initial_local_without_changing_outputs(self):
        fake = FakeTransport()
        fake.values[2]['CHAN:OUTP'] = 1.0
        # The physical output query is authoritative. The status-register ON
        # bit can be absent while CC is active on this instrument firmware.
        fake.operation_status_masks[2] = 0
        dev = Keithley2231A(fake)
        self.assertEqual(dev.output_states(), {1: False, 2: True, 3: False})
        self.assertEqual(fake.values[2]['CHAN:OUTP'], 1.0)
        self.assertFalse(dev.armed)
        self.assertEqual(fake.cmds, ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?',
                                   'INST:NSEL 1', 'INST:NSEL?', 'CHAN:OUTP?',
                                   'INST:NSEL 2', 'INST:NSEL?', 'CHAN:OUTP?',
                                   'INST:NSEL 3', 'INST:NSEL?', 'CHAN:OUTP?',
                                   'INST:NSEL 1'])

    def test_output_states_preserves_profile_and_does_not_retry_faults(self):
        self.fake.cmds.clear()
        self.assertEqual(self.dev.output_states(), {1: False, 2: False, 3: False})
        self.assertNotIn('SYST:REM', self.fake.cmds)
        self.assertTrue(self.dev.armed)
        self.fake.failed_query = 'CHAN:OUTP?'
        with self.assertRaises(RuntimeError): self.dev.output_states()
        self.assertTrue(self.dev.faulted)
        before = list(self.fake.cmds)
        with self.assertRaises(SafetyError): self.dev.output_states()
        self.assertEqual(self.fake.cmds, before)

    def test_output_states_restores_the_live_channel_selection(self):
        self.fake.active_ch = 2
        self.assertEqual(self.dev.output_states(), {1: False, 2: False, 3: False})
        self.assertEqual(self.fake.active_ch, 2)
        self.assertEqual(self.fake.cmds[-1], 'INST:NSEL 2')

    def test_output_states_rejects_wrong_identity_before_remote_entry(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake, expected_serial='OTHER')
        with self.assertRaisesRegex(InstrumentError, 'serial number mismatch'):
            dev.output_states()
        self.assertEqual(fake.cmds, ['*IDN?'])
        self.assertTrue(dev.faulted)

    def test_debug_queries_share_remote_setup_and_are_disabled_by_default(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake)
        with self.assertRaises(SafetyError): dev.diagnostic_query('VOLT?')
        self.assertEqual(fake.cmds, [])
        events = []
        dev = Keithley2231A(fake, debug=True, debug_trace=events.append)
        self.assertEqual(dev.diagnostic_query('VOLT?'), '0.0')
        self.assertEqual(fake.cmds, ['*IDN?', 'SYST:REM', 'VOLT?'])
        self.assertEqual([e['command'] for e in events if e['event'] in ('write', 'query')], fake.cmds)
        self.assertNotIn('TEST', repr(events))
        with self.assertRaises(ValueError): dev.diagnostic_query('VOLT?;OUTP 1')
        dev.diagnostic_write('SYSTem:LOCal')
        self.assertEqual(dev.diagnostic_query('VOLT?'), '0.0')
        self.assertEqual(fake.cmds[-3:], ['SYST:LOC', 'SYST:REM', 'VOLT?'])

    def test_debug_profile_bypass_is_explicit_and_soft_rejection_sends_nothing(self):
        class VoltageLimitedFake(FakeTransport):
            pending_error = False

            def write(self, command):
                values = self.values[self.active_ch]
                before = values['VOLT']
                super().write(command)
                if (command.startswith('VOLT ') and values['VOLT:LIM:STAT'] and
                        values['VOLT'] > values['VOLT:LIM']):
                    values['VOLT'] = before
                    self.pending_error = True

            def query(self, command):
                answer = super().query(command)
                if command == 'SYST:ERR?' and self.pending_error:
                    self.pending_error = False
                    return '120,"Out of voltage setting range"'
                return answer

        fake = VoltageLimitedFake()
        dev = Keithley2231A(fake, debug=True)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        before = list(fake.cmds)
        with self.assertRaises(LimitsError): dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(fake.cmds, before)
        dev.debug_bypass_profile = True
        with self.assertRaises(LimitsError): dev.set_channel(3, voltage=3.31)
        self.assertEqual(fake.cmds, before)
        dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(fake.cmds[-1], 'VOLT 3.31')
        self.assertEqual(dev.diagnostic_query('SYST:ERR?'), '120,"Out of voltage setting range"')
        self.assertEqual(fake.values[3]['VOLT'], 1.0)
        dev.debug_bypass_profile = False
        before = list(fake.cmds)
        with self.assertRaises(LimitsError): dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(fake.cmds, before)
        ordinary = Keithley2231A(FakeTransport())
        with self.assertRaises(SafetyError): ordinary.debug_bypass_profile = True
        wrong = FakeTransport()
        dev = Keithley2231A(wrong, debug=True, debug_bypass_profile=True, expected_serial='OTHER')
        dev.diagnostic_write('SYST:REM')
        with self.assertRaises(InstrumentError): dev.diagnostic_write('VOLT 1.00')
        self.assertEqual(wrong.cmds, ['SYST:REM', '*IDN?'])

    def test_normal_and_debug_profiles_use_identical_wire_sequences(self):
        normal, diagnostic = FakeTransport(), FakeTransport()
        events = []
        Keithley2231A(normal).apply_profile(profile(), confirmed_wiring='independent')
        Keithley2231A(diagnostic, debug=True, debug_trace=events.append).apply_profile(profile(), confirmed_wiring='independent')
        self.assertEqual(normal.cmds, diagnostic.cmds)
        self.assertEqual([e['command'] for e in events if e['event'] in ('write', 'query')], normal.cmds)

    def test_debug_adapter_cannot_send_outside_driver_dispatch(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake, debug=True, debug_adapter=lambda channel: channel)
        with self.assertRaises(SafetyError): dev.debug_transport.query('VOLT?')
        self.assertEqual(fake.cmds, [])
        self.assertEqual(dev.diagnostic_query('VOLT?'), '0.0')

    def test_malformed_numeric_measurement_blocks_shutdown_on_uncertain_link(self):
        class InvalidMeasurement(FakeTransport):
            corrupt = False

            def query(self, command):
                reply = super().query(command)
                if self.corrupt and command == 'MEAS:VOLT?':
                    return 'not-a-voltage'
                return reply

        fake = InvalidMeasurement()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, True)
        fake.corrupt = True
        off_before = fake.cmds.count('OUTP 0')
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE.*link uncertain'):
            dev.read_channel(1)
        self.assertEqual(fake.cmds[-1], 'MEAS:VOLT?')
        self.assertEqual(fake.cmds.count('OUTP 0'), off_before)
        self.assertTrue(fake.values[1]['CHAN:OUTP'])  # Physical OFF is NOT proved.
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        commands = list(fake.cmds)
        from keithley2231a import TransportError
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.assertEqual(fake.cmds, commands)

    def test_non_string_scpi_reply_fails_closed_before_trace_or_more_io(self):
        class NonStringStatus(FakeTransport):
            def query(self, command):
                reply = super().query(command)
                return None if command == 'SYST:VERS?' else reply

        fake = NonStringStatus()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, True)
        off_before = fake.cmds.count('OUTP 0')
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.status()
        self.assertEqual(fake.cmds[-1], 'SYST:VERS?')
        self.assertEqual(fake.cmds.count('OUTP 0'), off_before)
        self.assertTrue(fake.values[1]['CHAN:OUTP'])

    def test_fractional_status_register_is_not_silently_truncated(self):
        class FractionalStatus(FakeTransport):
            def query(self, command):
                reply = super().query(command)
                if command == 'STAT:OPER:INST:ISUM1:COND?':
                    return '1.5'
                return reply

        fake = FractionalStatus()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.read_channel(1)
        self.assertEqual(fake.cmds[-1], 'STAT:OPER:INST:ISUM1:COND?')
        self.assertTrue(dev.faulted)

    def test_status_masks_reject_float_rounding_and_out_of_range_without_next_scpi(self):
        # The old _uint() already rejected 1.5, but not decimal strings that
        # lose their fractional part during float conversion.
        for command, malformed in (
                ('STAT:OPER:INST:ISUM3:COND?', '1.0000000000000001'),
                ('STAT:OPER:INST:ISUM3:COND?', '256'),
                ('STAT:QUES:INST:ISUM3:COND?', '2.0000000000000001'),
                ('STAT:QUES:INST:ISUM3:COND?', '999999'),
                ('STAT:OPER:INST:ISUM3:COND?', '-1'),
                ('STAT:OPER:INST:ISUM3:COND?', 'NaN'),
                ('STAT:OPER:INST:ISUM3:COND?', '9' * 5000)):
            with self.subTest(command=command, raw=malformed[:25]):
                class BadRegister(FakeTransport):
                    def query(self, sent):
                        reply = super().query(sent)
                        return malformed if sent == command else reply

                fake = BadRegister()
                dev = Keithley2231A(fake)
                dev.apply_profile(profile(), confirmed_wiring='independent')
                dev.output(3, True)
                with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
                    dev.read_channel(3)
                self.assertEqual(fake.cmds[-1], command)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertTrue(fake.values[3]['CHAN:OUTP'])

    def test_status_api_byte_must_be_bounded_integer(self):
        class BadStatus(FakeTransport):
            def query(self, command):
                answer = super().query(command)
                return '256' if command == '*STB?' else answer

        fake = BadStatus()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.status()
        self.assertEqual(fake.cmds[-1], '*STB?')
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)

    def test_scpi_error_shutdown_is_checked_and_never_retried(self):
        class ErrorReply(FakeTransport):
            def query(self, command):
                answer = super().query(command)
                return '120,"Out of range"' if command == 'SYST:ERR?' else answer

        fake = ErrorReply()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, True)
        before_errors = fake.cmds.count('SYST:ERR?')
        with self.assertRaisesRegex(InstrumentError, 'output OFF confirmed'):
            dev.check_errors()
        self.assertEqual(fake.cmds.count('SYST:ERR?'), before_errors + 1)
        self.assertIn('OUTP 0', fake.cmds)
        self.assertTrue(all(not value['CHAN:OUTP'] for value in fake.values.values()))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        before = list(fake.cmds)
        with self.assertRaises(SafetyError):
            dev.output(1, True)
        self.assertEqual(fake.cmds, before)

    def test_scpi_error_never_claims_shutdown_without_readback(self):
        class ErrorReply(FakeTransport):
            def query(self, command):
                answer = super().query(command)
                return '120,Invalid setpoint' if command == 'SYST:ERR?' else answer
            def write(self, command):
                super().write(command)
                if getattr(self, 'ignore_shutdown', False) and command in ('OUTP 0', 'CHAN:OUTP 0'):
                    self.values[1]['CHAN:OUTP'] = 1.0

        fake = ErrorReply()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, True)
        fake.ignore_shutdown = True
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            dev.check_errors()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(fake.values[1]['CHAN:OUTP'])

    def test_malformed_scpi_error_latches_uncertain_link_without_followup_io(self):
        class BadReply(FakeTransport):
            def query(self, command):
                answer = super().query(command)
                return 'not-an-error-code' if command == 'SYST:ERR?' else answer

        fake = BadReply()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, True)
        off_before = fake.cmds.count('OUTP 0')
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            dev.check_errors()
        self.assertEqual(fake.cmds.count('OUTP 0'), off_before)
        self.assertTrue(fake.values[1]['CHAN:OUTP'])
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        before = list(fake.cmds)
        from keithley2231a import TransportError
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.assertEqual(fake.cmds, before)

    def test_interrupted_wire_query_latches_uncertain_link_for_both_modes(self):
        from keithley2231a import TransportError

        class InterruptedFake(FakeTransport):
            def query(self, command):
                if command == 'CHAN:OUTP?':
                    self.cmds.append(command)
                    raise KeyboardInterrupt()
                return super().query(command)

        for debug in (False, True):
            with self.subTest(debug=debug):
                fake = InterruptedFake()
                dev = Keithley2231A(fake, debug=debug)
                with self.assertRaises(KeyboardInterrupt): dev.output_states()
                before = list(fake.cmds)
                with self.assertRaises(TransportError): dev.emergency_off()
                self.assertEqual(fake.cmds, before)
                self.assertTrue(dev.faulted)

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

    def test_set_channel_uses_shared_validation_before_io(self):
        for ch, kwargs in (
                (1, {'voltage': True}),
                (1, {'current': float('nan')}),
                (2, {'voltage': 5.001}),
                (3, {'current': .1001}),
                (3, {'voltage': -0.01}),
                (3, {'voltage': float('inf')})):
            with self.subTest(channel=ch, kwargs=kwargs):
                before = list(self.fake.cmds)
                with self.assertRaises(LimitsError):
                    self.dev.set_channel(ch, **kwargs)
                self.assertEqual(self.fake.cmds, before)
                self.assertTrue(self.dev.armed)

        before = len(self.fake.cmds)
        self.dev.set_channel(1, voltage=2.5, current=.15)
        commands = self.fake.cmds[before:]
        self.assertLess(commands.index('CURR 0.150'), commands.index('VOLT 2.50'))
        self.assertTrue(self.dev.armed)
        self.dev.verify_profile()

    def test_set_channel_detects_readback_change_and_trips(self):
        class BadReadback(FakeTransport):
            corrupt = False
            def query(self, command):
                response = super().query(command)
                if self.corrupt and command == 'VOLT?' and self.active_ch == 3:
                    return str(float(response) + .01)
                return response

        fake = BadReadback()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        fake.corrupt = True
        with self.assertRaises(SafetyError):
            dev.set_channel(3, voltage=2.0, current=.08)
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertIn('OUTP 0', fake.cmds)

    def test_on_off_and_profile_lock(self):
        self.dev.output(1, True)
        self.assertTrue(self.fake.values[1]['CHAN:OUTP'])
        with self.assertRaises(SafetyError): self.dev.apply_profile(profile(), confirmed_wiring='independent')
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

    def test_read_channel_preserves_unknown_when_both_mode_registers_are_empty(self):
        self.fake.values[1]['CHAN:OUTP'] = 1.0
        self.fake.operation_status_masks[1] = 0
        self.fake.questionable_status_masks[1] = 0
        reading = self.dev.read_channel(1)
        self.assertTrue(reading.enabled)
        self.assertEqual(reading.regulation, 'UNKNOWN')
        self.assertEqual(reading.operation_status_mask, 0)
        self.assertEqual(reading.questionable_status_mask, 0)

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
                dev.apply_profile(settings, confirmed_wiring='independent')
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

    def test_tracking_timers_require_off_validate_both_and_read_back(self):
        before = list(self.fake.cmds)
        with self.assertRaises(SafetyError): self.dev.tracking_timers(3, 5)
        self.assertEqual(self.fake.cmds, before)
        self.dev.emergency_off()
        self.dev.apply_profile(profile('tracking'), confirmed_wiring='independent')
        for pair in ((3, float('nan')), (3, .01), (True, 5), (3, 5.01)):
            before = list(self.fake.cmds)
            with self.assertRaises(ValueError): self.dev.tracking_timers(*pair)
            self.assertEqual(self.fake.cmds, before)
        self.dev.tracking_timers(3, 5)
        for ch, delay in ((1, 3), (2, 5)):
            self.assertEqual(self.fake.values[ch]['OUTP:TIM:DEL'], delay)
            self.assertEqual(self.fake.values[ch]['OUTP:TIM:STAT'], 1)
        self.dev.combined_outputs(True, confirm_all_channels_safe=True)
        before = list(self.fake.cmds)
        with self.assertRaises(SafetyError): self.dev.tracking_timers(None, None)
        self.assertFalse(any(cmd.startswith('OUTP:TIM:') and not cmd.endswith('?')
                             for cmd in self.fake.cmds[len(before):]))
        self.dev.combined_outputs(False)
        self.dev.tracking_timers(None, None)
        self.assertEqual(self.fake.values[1]['OUTP:TIM:STAT'], 0)
        self.assertEqual(self.fake.values[2]['OUTP:TIM:STAT'], 0)
        self.fake.failed_query = 'OUTP:TIM:DEL?'
        with self.assertRaises(SafetyError): self.dev.tracking_timers(3, 5)
        self.assertFalse(self.dev.armed)
        self.assertTrue(self.dev.faulted)
        class WrongTimerReadback(FakeTransport):
            def query(self, command):
                result = super().query(command)
                return str(float(result) + .1) if command == 'OUTP:TIM:DEL?' else result
        source = WrongTimerReadback()
        dev = Keithley2231A(source)
        dev.apply_profile(profile('tracking'), confirmed_wiring='independent')
        with self.assertRaises(SafetyError): dev.tracking_timers(3, 5)
        self.assertFalse(dev.armed)
        self.assertTrue(all(not values['CHAN:OUTP'] for values in source.values.values()))

    def test_emergency_off_enters_remote_before_output_command(self):
        before = len(self.fake.cmds)
        self.dev.emergency_off()
        self.assertEqual(self.fake.cmds[before:before + 2], ['SYST:REM', 'OUTP 0'])

    def test_profile_forbids_releasing_lock(self):
        with self.assertRaises(SafetyError): self.dev.remote(lock_panel=False)
        self.dev.remote(lock_panel=True)

    def test_regulation_check_cannot_be_bypassed(self):
        self.dev.emergency_off()
        self.dev.apply_profile(profile(expected='cv'), confirmed_wiring='independent')
        with self.assertRaises(SafetyError): self.dev.read_channel(1, with_regulation=False)

    def test_connection_failure_attempts_off(self):
        self.fake.failed_query = 'VOLT:LIM?'
        with self.assertRaises(SafetyError): self.dev.verify_profile()
        self.assertFalse(self.dev.armed)
        self.assertIn('OUTP 0', self.fake.cmds)

    def test_tracking_requires_explicit_voltage_and_output_apis(self):
        self.dev.emergency_off()
        self.dev.apply_profile(profile('tracking'), confirmed_wiring='independent')
        self.assertTrue(self.fake.flags['OUTP:TRAC'])
        with self.assertRaises(SafetyError): self.dev.set_channel(1, voltage=2)
        self.dev.combined_outputs(True, confirm_all_channels_safe=True)
        self.assertTrue(self.fake.flags['OUTP:STAT:ALL'])

    def test_tracking_voltage_checks_both_channels_and_preserves_ratio(self):
        for ratio in (1, 2):
            with self.subTest(ratio=ratio):
                fake = FakeTransport()
                dev = Keithley2231A(fake)
                dev.apply_profile(Profile('tracking', (
                    ChannelProfile(2, .3, 1, .1),
                    ChannelProfile(3, .4, ratio, .2),
                    ChannelProfile(4, .5, .5, .3)), wiring='tracking'), confirmed_wiring='independent')
                dev.set_tracking_voltage(1, 1.2)
                self.assertAlmostEqual(fake.values[2]['VOLT'], 1.2 * ratio)
                dev.set_tracking_voltage(2, ratio)
                dev.verify_profile()
                self.assertAlmostEqual(fake.values[1]['VOLT'], 1)
                # CH1 is within its own cap; the linked CH2 is outside its cap.
                for ch, value in ((1, 1.6 if ratio == 2 else 2.01),
                                  (1, 0), (1, float('nan')), (1, .001), (3, 1),
                                  (2, .01 if ratio == 2 else 3.01)):
                    before = list(fake.cmds)
                    with self.assertRaises((LimitsError, SafetyError)):
                        dev.set_tracking_voltage(ch, value)
                    self.assertEqual(fake.cmds, before)
                fake.tracking_fault = True
                with self.assertRaises(SafetyError): dev.set_tracking_voltage(1, 1.2)
                self.assertFalse(dev.armed)
                self.assertIn('OUTP 0', fake.cmds)

    def test_independent_and_tracking_require_physical_no_jumper_confirmation(self):
        """No physical wiring assertion means NO SCPI, even on a new session."""
        for wiring in ('independent', 'tracking'):
            for confirmation in (None, 'series', 'parallel', 'tracking', ''):
                with self.subTest(wiring=wiring, confirmation=confirmation):
                    source = FakeTransport()
                    dev = Keithley2231A(source)
                    setting = profile(wiring)
                    before = list(source.cmds)
                    with self.assertRaisesRegex(SafetyError, 'NO external Series/Parallel jumpers'):
                        dev.apply_profile(setting, confirmed_wiring=confirmation)
                    self.assertEqual(source.cmds, before)
                    self.assertFalse(dev.armed)
                    self.assertFalse(dev.faulted)
                    self.assertIsNone(dev.profile)

    def test_parallel_to_independent_requires_new_manual_confirmation(self):
        """An OFF command does not imply physical jumpers were removed."""
        source = FakeTransport()
        dev = Keithley2231A(source)
        combined = ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                                   ChannelProfile(4.0, .5, 1.0, .1))
        dev.apply_profile(combined, confirmed_wiring='parallel')
        dev.emergency_off()
        before = list(source.cmds)
        for confirmed in (None, 'parallel', 'series'):
            with self.subTest(confirmed=confirmed):
                with self.assertRaisesRegex(SafetyError, 'NO external Series/Parallel jumpers'):
                    dev.apply_profile(profile(), confirmed_wiring=confirmed)
                self.assertEqual(source.cmds, before)
                self.assertIs(dev.profile, combined)
        # The following represents an operator who has physically checked and
        # removed both Parallel jumpers while PSU outputs were OFF.
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertFalse(source.flags['OUTP:PARALLEL'])
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})

    def test_combined_physical_wiring_confirmation(self):
        class SeriesSource(FakeTransport):
            def write(self, command):
                if command == 'INST:NSEL 2' and self.flags['OUTP:SER']:
                    self.cmds.append(command)
                    self.active_ch = 1
                    return
                if command == 'OUTP:SER 1':
                    super().write(command)
                    self.values[1]['VOLT:LIM'] = self.values[1]['VOLT:LIM'] + self.values[2]['VOLT:LIM']
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    return
                if command == 'OUTP:SER 0' and self.flags['OUTP:SER']:
                    combined_voltage = self.values[1]['VOLT']
                    super().write(command)
                    for ch in (1, 2):
                        self.values[ch]['VOLT'] = combined_voltage / 2
                        self.values[ch]['VOLT:LIM:STAT'] = 1.
                    return
                if command == 'VOLT:LIM:STAT 1' and self.flags['OUTP:SER']:
                    self.cmds.append(command)
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    return
                super().write(command)

            def query(self, command):
                if command == 'VOLT:LIM?' and self.flags['OUTP:SER'] and self.active_ch == 1:
                    self.cmds.append(command)
                    return str(self.values[1]['VOLT:LIM'])
                if command == 'VOLT:LIM:STAT?' and self.flags['OUTP:SER'] and self.active_ch == 1:
                    self.cmds.append(command)
                    return '0'
                return super().query(command)

        source = SeriesSource()
        dev = Keithley2231A(source)
        series = SeriesProfile('safe series', (2., 3.), 4.5, .3, 2., .1,
                               ChannelProfile(4., .5, 2., .3))
        with self.assertRaises(SafetyError): dev.apply_profile(series)
        dev.apply_profile(series, confirmed_wiring='series')
        self.assertTrue(dev.armed)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        with self.assertRaises(SafetyError): dev.combined_outputs(True)
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        reading = dev.read_channel(1)
        self.assertEqual(reading.label, 'CH1+CH2')
        self.assertEqual(reading.voltage_set_v, 2.)
        self.assertEqual(reading.current_limit_set_a, .1)
        with self.assertRaises(SafetyError): dev.read_channel(2)
        self.assertEqual(dev.read_channel(3).label, 'CH3')
        before = list(source.cmds)
        with self.assertRaises(LimitsError): dev.set_channel(1, voltage=4.51)
        with self.assertRaises(SafetyError): dev.set_channel(2, voltage=1.)
        self.assertEqual(source.cmds, before)
        dev.set_channel(1, voltage=2.5, current=.2)
        before = list(source.cmds)
        with self.assertRaises(LimitsError):
            dev.set_channel(3, voltage=4.01)
        self.assertEqual(source.cmds, before)
        dev.set_channel(3, voltage=2.5, current=.4)
        self.assertEqual(source.values[3]['VOLT'], 2.5)
        self.assertEqual(dev.read_channel(1).voltage_set_v, 2.5)
        dev.emergency_off()
        self.assertEqual(source.flags['OUTP:STAT:ALL'], 0.)
        self.assertFalse(dev.armed)
        # A single changed aggregate step must trip for Series, as in Parallel.
        dev.apply_profile(series, confirmed_wiring='series')
        source.values[1]['VOLT'] += .01
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)

    def test_all_profile_application_failures_share_fault_cleanup(self):
        """All topologies clear the profile and attempt only best-effort OFF."""
        candidates = (
            ('independent', profile(), 'independent'),
            ('series', SeriesProfile('series', (2., 3.), 4.5, .3, 2., .1,
                                     ChannelProfile(4., .5, 2., .3)), 'series'),
            ('parallel', ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                                         ChannelProfile(4., .5, 1., .1)), 'parallel'),
        )
        for name, configured, wiring in candidates:
            with self.subTest(wiring=name):
                source = FakeTransport()
                dev = Keithley2231A(source)

                def fail_wiring():
                    raise SafetyError('injected profile validation failure')

                dev._check_wiring_mode = fail_wiring
                with self.assertRaisesRegex(SafetyError, 'injected profile validation failure'):
                    dev.apply_profile(configured, confirmed_wiring=wiring)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertIsNone(dev.profile)
                self.assertEqual(source.cmds[-1], 'OUTP 0')
                self.assertNotIn('OUTP 1', source.cmds)

    def test_both_combined_modes_check_ch3_limit_integrity(self):
        """Shared CH3 guard must detect one-step external changes in both modes."""
        class SeriesTransport(FakeTransport):
            def query(self, command):
                if (command == 'VOLT:LIM?' and self.flags['OUTP:SER']
                        and self.active_ch == 1):
                    self.cmds.append(command)
                    return '5.0'
                return super().query(command)

        candidates = (
            ('series', SeriesTransport(),
             SeriesProfile('series', (2., 3.), 4.5, .3, 2., .1,
                           ChannelProfile(4., .5, 2., .3))),
            ('parallel', FakeTransport(),
             ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                             ChannelProfile(4., .5, 1., .1))),
        )
        for wiring, source, configured in candidates:
            with self.subTest(wiring=wiring):
                dev = Keithley2231A(source)
                dev.apply_profile(configured, confirmed_wiring=wiring)
                source.values[3]['VOLT:LIM'] -= .01
                with self.assertRaisesRegex(SafetyError, 'CH3 hardware voltage limit changed'):
                    dev.verify_profile()
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)

    def test_series_profile_fails_closed_when_combined_limit_readback_changes(self):
        class ChangedCombinedLimit(FakeTransport):
            def query(self, command):
                value = super().query(command)
                if self.flags['OUTP:SER'] and self.active_ch == 1 and command == 'VOLT:LIM?':
                    return '4.99'
                return value
        source = ChangedCombinedLimit()
        dev = Keithley2231A(source)
        series = SeriesProfile('safe series', (2., 3.), 4.5, .3, 2., .1,
                               ChannelProfile(4., .5, 2., .3))
        with self.assertRaises(SafetyError):
            dev.apply_profile(series, confirmed_wiring='series')
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertNotIn('OUTP 1', source.cmds)

    def test_output_states_refuses_a_channel_selection_alias(self):
        class AliasedSource(FakeTransport):
            def write(self, command):
                super().write(command)
                if command == 'INST:NSEL 2': self.active_ch = 1
        source = AliasedSource()
        dev = Keithley2231A(source)
        with self.assertRaises(SafetyError): dev.output_states()
        self.assertTrue(dev.faulted)

    def test_fixed_setpoint_tampering_within_caps_trips(self):
        for key, value in (('VOLT', 1.01), ('CURR', .101)):
            with self.subTest(key=key):
                fake = FakeTransport(); dev = Keithley2231A(fake)
                base = profile()
                dev.apply_profile(Profile('fixed', base.channels, access='power_only'), confirmed_wiring='independent')
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
        fake.query = lambda cmd: 'Keithley instruments, 2231A-30-3, OTHER, 1.04-1.04' if cmd == '*IDN?' else original(cmd)
        with self.assertRaisesRegex(InstrumentError, 'identity changed'):
            dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertNotIn('VOLT 0', fake.cmds)
        self.assertNotIn('CURR 0', fake.cmds)
        self.assertFalse(any(cmd.startswith(('VOLT:LIM ', 'VOLT:LIM:STAT ', 'CURR ', 'VOLT '))
                             for cmd in fake.cmds))
        # A different physical identity is not a trustworthy OFF command target.
        self.assertNotIn('OUTP 0', fake.cmds)
        before = list(fake.cmds)
        from keithley2231a import TransportError
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.assertEqual(fake.cmds, before)

    def test_firmware_revision_is_reported_but_not_used_as_identity(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake)
        first = dev.identity()
        original = fake.query
        fake.query = lambda cmd: ('Keithley instruments, 2231A-30-3, TEST, 1.04-0.01'
                                  if cmd == '*IDN?' else original(cmd))
        self.assertEqual(dev.identity(), 'Keithley instruments, 2231A-30-3, TEST, 1.04-0.01')
        self.assertNotEqual(first, dev.instrument_id)
        self.assertEqual(dev.instrument_key,
                         ('keithley instruments', '2231A-30-3', 'TEST'))
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)

    def test_aux_version_drift_is_nonblocking_with_stable_physical_identity(self):
        # Some real qualification runs changed 1.04 -> 0.01 after a CH3 sweep.
        # Model that correlation without declaring it a firmware contract.
        fake = FakeTransport()
        original = fake.query
        versions = {1: '1.04-1.04', 2: '1.04-1.04', 3: '1.04-0.01'}
        def correlated_reply(cmd):
            if cmd == '*IDN?':
                return ('Keithley instruments, 2231A-30-3, TEST, '
                        + versions[fake.active_ch])
            return original(cmd)
        fake.query = correlated_reply
        dev = Keithley2231A(fake)
        key = ('keithley instruments', '2231A-30-3', 'TEST')
        self.assertTrue(dev.identity().endswith('1.04-1.04'))
        dev.remote()
        dev._select(3)
        self.assertTrue(dev.identity().endswith('1.04-0.01'))
        self.assertEqual(dev.instrument_key, key)
        # Synthetic alternate valid version, NOT observed on the actual front panel;
        # ensure arbitrary nonempty revisions are preserved without normalization.
        versions[3] = '1.04-1.01'
        self.assertTrue(dev.identity().endswith('1.04-1.01'))
        self.assertEqual(dev.instrument_key, key)
        dev._select(1)
        self.assertTrue(dev.identity().endswith('1.04-1.04'))
        self.assertEqual(dev.instrument_key, key)
        self.assertFalse(dev.faulted)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)

    def test_optional_serial_binding_rejects_another_supply_before_profile_writes(self):
        from keithley2231a import InstrumentError
        fake = FakeTransport()
        dev = Keithley2231A(fake, expected_serial='OTHER-PSU')
        with self.assertRaisesRegex(InstrumentError, 'serial number mismatch'):
            dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertFalse(dev.armed)
        self.assertNotIn('VOLT 0', fake.cmds)
        self.assertFalse(any(cmd.startswith(('VOLT:LIM ', 'VOLT:LIM:STAT ', 'CURR ', 'VOLT '))
                             for cmd in fake.cmds))

        with self.assertRaises((TypeError, ValueError)):
            Keithley2231A(FakeTransport(), expected_serial='')

    def test_apply_disables_inherited_timers(self):
        self.dev.emergency_off()
        for values in self.fake.values.values(): values['OUTP:TIM:STAT'] = 1
        self.dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(all(values['OUTP:TIM:STAT'] == 0 for values in self.fake.values.values()))


    def test_parallel_profile_uses_aggregate_setpoint_and_software_voltage_cap(self):
        class ParallelTransport(FakeTransport):
            def write(self, cmd):
                if cmd.startswith('CURR '):
                    requested = float(cmd.split()[1])
                    if not self.flags['OUTP:PARALLEL'] and requested > 3.0:
                        raise AssertionError('independent channel current exceeds 3 A')
                    if self.flags['OUTP:PARALLEL'] and requested > 6.0:
                        raise AssertionError('parallel aggregate current exceeds 6 A')
                super().write(cmd)
                if cmd == 'OUTP:PARALLEL 1':
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.
                elif cmd == 'OUTP:PARALLEL 0':
                    total_i = self.values[1]['CURR']
                    self.values[1]['CURR'] = total_i / 2
                    self.values[2]['CURR'] = total_i / 2
                    self.values[2]['VOLT'] = self.values[1]['VOLT']
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.
            def query(self, cmd):
                if self.flags['OUTP:PARALLEL'] and cmd == 'INST:NSEL?':
                    self.cmds.append(cmd)
                    return '1' if self.active_ch == 2 else str(self.active_ch)
                if self.flags['OUTP:PARALLEL'] and self.active_ch in (1, 2) and cmd == 'VOLT:LIM:STAT?':
                    self.cmds.append(cmd)
                    return '0'
                return super().query(cmd)

        fake = ParallelTransport()
        dev = Keithley2231A(fake)
        p = ParallelProfile('bounded parallel', 3.0, .300, 2.0, .100,
                            ChannelProfile(4., .5, 1., .1), lock_front_panel=False)
        dev.apply_profile(p, confirmed_wiring='parallel')
        self.assertTrue(dev.armed)
        self.assertEqual(fake.flags['OUTP:PARALLEL'], 1)
        self.assertEqual(dev.read_channel(1, with_regulation=False).label, 'CH1+CH2')
        with self.assertRaises(SafetyError): dev.read_channel(2)
        dev.set_channel(1, voltage=2.5, current=.2)
        before = list(fake.cmds)
        with self.assertRaises(LimitsError):
            dev.set_channel(3, current=.501)
        self.assertEqual(fake.cmds, before)
        dev.set_channel(3, voltage=2.0, current=.2)
        self.assertEqual(fake.values[3]['VOLT'], 2.0)
        with self.assertRaises(SafetyError): dev.set_channel(2, voltage=2.5)
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        self.assertEqual(dev.output_states(), {1: True, 2: True, 3: True})
        with self.assertRaises(LimitsError): dev.set_channel(1, voltage=3.01)
        dev.combined_outputs(False)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        dev.emergency_off()
        # Both combined modes use the same aggregate setpoint integrity guard.
        dev.apply_profile(p, confirmed_wiring='parallel')
        fake.values[1]['CURR'] += .001
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)

    def test_parallel_profile_sets_aggregate_current_only_after_parallel_mode(self):
        class ParallelCurrentTransport(FakeTransport):
            def write(self, cmd):
                if cmd.startswith('CURR '):
                    requested = float(cmd.split()[1])
                    ceiling = 6.0 if self.flags['OUTP:PARALLEL'] else 3.0
                    if requested > ceiling:
                        raise AssertionError(f'current {requested} exceeds active mode ceiling {ceiling}')
                super().write(cmd)
                if cmd == 'OUTP:PARALLEL 1':
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.
            def query(self, cmd):
                if self.flags['OUTP:PARALLEL'] and self.active_ch in (1, 2) and cmd == 'VOLT:LIM:STAT?':
                    self.cmds.append(cmd)
                    return '0'
                return super().query(cmd)

        fake = ParallelCurrentTransport()
        dev = Keithley2231A(fake)
        p = ParallelProfile('aggregate 4 A', 3.0, 4.0, 2.0, 4.0,
                            ChannelProfile(4., .5, 1., .1), lock_front_panel=False)
        dev.apply_profile(p, confirmed_wiring='parallel')
        mode_index = fake.cmds.index('OUTP:PARALLEL 1')
        current_index = fake.cmds.index('CURR 4.000')
        self.assertLess(mode_index, current_index)
        self.assertTrue(dev.armed)
        self.assertEqual(fake.values[1]['CURR'], 4.0)
        dev.emergency_off()

    def test_generic_profile_rejects_parallel_mode(self):
        p = profile()
        with self.assertRaisesRegex(ProfileError, 'ParallelProfile'):
            Profile('legacy parallel', p.channels, wiring='parallel')

    def test_combined_output_off_waits_for_delayed_instrument_status(self):
        class DelayedOff(FakeTransport):
            stale_off_reads = 0

            def write(self, command):
                was_on = self.flags['OUTP:STAT:ALL']
                super().write(command)
                if command == 'OUTP 0' and was_on:
                    self.stale_off_reads = 2

            def query(self, command):
                if command == 'OUTP:STAT:ALL?' and self.stale_off_reads:
                    self.cmds.append(command)
                    self.stale_off_reads -= 1
                    return '1'
                return super().query(command)

        source = DelayedOff()
        dev = Keithley2231A(source)
        dev.apply_profile(profile('tracking'), confirmed_wiring='independent')
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        dev.combined_outputs(False)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        self.assertFalse(dev.faulted)

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
        fake=FakeTransport(); dev=Keithley2231A(fake); dev.apply_profile(profile(), confirmed_wiring='independent')
        a=Acquisition(dev,interval_s=.1)
        self.assertEqual(len(a.sample()),3)
        self.assertEqual(a.stats[1].voltage.count,1)

    def test_acquisition_does_not_start_sample_after_duration(self):
        from unittest.mock import patch

        class ClockStop:
            now = 0.0
            def is_set(self): return False
            def wait(self, seconds):
                self.now += seconds
                return False

        fake = FakeTransport()
        dev = Keithley2231A(fake)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        stop = ClockStop()
        acquisition = Acquisition(dev, interval_s=2.0)
        with patch('keithley2231a.monitor.time.monotonic', lambda: stop.now):
            acquisition.run(duration_s=1.0, stop_event=stop)
        self.assertEqual(acquisition.stats[1].voltage.count, 1)
        self.assertEqual(stop.now, 1.0)


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
        try:
            import serial
        except ImportError:
            self.skipTest('pyserial is not installed')
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
        fake=FakeTransport(); dev=Keithley2231A(fake); dev.apply_profile(p, confirmed_wiring='independent')
        dev.output(1, True)
        fake.values[1]['VOLT'] = 9.
        a=Acquisition(dev)
        with self.assertRaises(SafetyError): a.sample()
        self.assertEqual(fake.flags['OUTP:STAT:ALL'], 0)

if __name__ == '__main__': unittest.main()
