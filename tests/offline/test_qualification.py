"""Regression checks for qualification gates and evidence, using simulated I/O."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class TestQualification(unittest.TestCase):
    def module(self):
        import qualification
        return qualification

    def test_dry_run_never_constructs_transport(self):
        q = self.module()
        for args in (['list'], ['preflight', 'H01'], ['run', 'H01'],
                     ['preflight', 'H02'], ['run', 'H02'],
                     ['run', 'H09'], ['abort'], ['report']):
            with self.subTest(args=args), patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
                with contextlib.redirect_stdout(io.StringIO()):
                    q.main(args)

    def test_h03_plan_covers_six_individual_cases_without_global_on(self):
        q = self.module()
        from channel_stage import FINAL_TOKEN, TOKEN, StageIO, command_plan
        from test_driver import FakeTransport
        plan = command_plan()
        resumed_plan = command_plan(2)
        self.assertEqual(plan.count('CHAN:OUTP 1'), 6)
        self.assertEqual(resumed_plan.count('CHAN:OUTP 1'), 4)
        self.assertEqual(resumed_plan.count('CHAN:OUTP 0'), 4)
        self.assertEqual(plan.count('CHAN:OUTP 0'), 6)
        self.assertEqual(plan.count('MEAS:POW?'), 6)
        self.assertEqual(plan.count('STAT:OPER:INST:ISUM1:COND?'), 29)
        self.assertEqual(plan.count('STAT:OPER:INST:ISUM2:COND?'), 29)
        self.assertEqual(plan.count('STAT:OPER:INST:ISUM3:COND?'), 29)
        self.assertEqual(plan.count('STAT:QUES:INST:ISUM1:COND?'), 4)
        self.assertEqual(plan.count('STAT:QUES:INST:ISUM2:COND?'), 4)
        self.assertEqual(plan.count('STAT:QUES:INST:ISUM3:COND?'), 4)
        self.assertNotIn('OUTP 1', plan)
        self.assertEqual(TOKEN, 'CONFIRM H03 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED LAN=UDP:18190 RECALL=OFF VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')
        self.assertEqual(FINAL_TOKEN, 'CONFIRM H03 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H03')
            guarded = StageIO(fake, evidence)
            with self.assertRaises(ValueError): guarded.write('OUTP 1')
            self.assertEqual(fake.cmds, [])

    def test_kel103_udp_is_lf_terminated_and_rejects_unknown_commands(self):
        from channel_stage import KEL103UDP

        class DatagramFake:
            def __init__(self): self.sent, self.closed = [], False
            def settimeout(self, timeout): self.timeout = timeout
            def sendto(self, payload, address): self.sent.append((payload, address))
            def recvfrom(self, size): return b'OFF\n', ('192.168.22.106', 18190)
            def close(self): self.closed = True

        fake = DatagramFake()
        load = KEL103UDP('192.168.22.106', sock=fake)
        self.assertEqual(load.query(':INP?'), 'OFF')
        self.assertEqual(fake.sent, [(b':INP?\n', ('192.168.22.106', 18190))])
        with self.assertRaises(ValueError): load.write(':SYST:FACTRESET')
        self.assertEqual(len(fake.sent), 1)
        load.close()
        self.assertTrue(fake.closed)

    def test_kel103_cr_setup_is_input_off_only_and_verified(self):
        from channel_stage import KEL103UDP

        class DatagramFake:
            def __init__(self, replies): self.replies, self.sent = list(replies), []
            def settimeout(self, timeout): pass
            def sendto(self, payload, address): self.sent.append(payload)
            def recvfrom(self, size): return self.replies.pop(0), ('192.168.22.106', 18190)
            def close(self): pass

        fake = DatagramFake((b'OFF', b'CR', b'CR', b'40.000OHM'))
        load = KEL103UDP('192.168.22.106', sock=fake)
        load.configure_cr(40.0)
        self.assertEqual(fake.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                     b':RES 40OHM\n', b':FUNC?\n', b':RES?\n'])

        active = DatagramFake((b'ON',))
        load = KEL103UDP('192.168.22.106', sock=active)
        with self.assertRaises(Exception): load.configure_cr(12.0)
        self.assertEqual(active.sent, [b':INP?\n'])

        unconfigured = DatagramFake(())
        load = KEL103UDP('192.168.22.106', sock=unconfigured)
        with self.assertRaises(Exception): load.set_input(True)
        self.assertEqual(unconfigured.sent, [])

    def test_kel103_serial_uses_bounded_lf_protocol_and_latches_link_errors(self):
        from channel_stage import KEL103Serial

        class SerialFake:
            def __init__(self): self.sent, self.replies, self.closed = [], [b'OFF\r\n'], False
            @property
            def in_waiting(self): return 0
            def write(self, payload): self.sent.append(payload); return len(payload)
            def flush(self): pass
            def readline(self, size): return self.replies.pop(0)
            def close(self): self.closed = True

        fake = SerialFake()
        load = KEL103Serial('/dev/ttyACM0', serial_port=fake)
        self.assertEqual(load.query(':INP?'), 'OFF')
        load.write(':INP OFF')
        self.assertEqual(fake.sent, [b':INP?\n', b':INP OFF\n'])
        with self.assertRaises(ValueError): load.write(':SYST:FACTRESET')
        load.close()
        self.assertTrue(fake.closed)

        failed = SerialFake()
        failed.replies = [b'']
        load = KEL103Serial('/dev/ttyACM0', serial_port=failed)
        with self.assertRaisesRegex(OSError, 'incomplete/oversized'):
            load.query(':INP?')
        with self.assertRaisesRegex(OSError, 'link uncertain'):
            load.query(':INP?')
        self.assertEqual(failed.sent, [b':INP?\n'])

    def test_kel103_serial_refuses_wrong_usb_device_before_open(self):
        from channel_stage import KEL103Serial
        from serial.tools import list_ports
        with patch.object(list_ports, 'comports', return_value=[
                type('Port', (), {'device': '/dev/ttyACM0', 'vid': 0x10C4,
                                  'pid': 0xEA60})()]), \
             patch('serial.Serial') as serial_open:
            with self.assertRaisesRegex(OSError, 'identity mismatch'):
                KEL103Serial('/dev/ttyACM0')
            serial_open.assert_not_called()

    def test_h03_usb_load_preflight_requires_explicit_usb_identity(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('PSU port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H03', '--load-transport', 'serial',
                                         '--load-serial-port', '/dev/ttyACM0']), 0)
        text = output.getvalue()
        self.assertIn('USB /dev/ttyACM0', text)
        self.assertIn('115200 8N1', text)
        self.assertIn('USB=/dev/ttyACM0 BAUD=115200', text)
        self.assertIn('ERRBASE=CHECK', text)

    def test_h03_preflight_is_offline_and_states_manual_load_points(self):
        q = self.module()
        self.assertTrue(q.stage_passed('PASS'))
        self.assertTrue(q.stage_passed('PASS (second attempt; first FAIL)'))
        self.assertFalse(q.stage_passed('PASSING'))
        self.assertFalse(q.stage_passed('NOT_PASS'))
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H03']), 0)
        text = output.getvalue()
        self.assertIn('KEL103 UDP 192.168.22.106:18190', text)
        self.assertIn('CR 40.0 Ω', text)
        self.assertIn('CR 12.0 Ω', text)
        self.assertIn('считывает U/I/P', text)
        self.assertIn('перестановки CH1→CH2 и CH2→CH3', text)
        self.assertIn('DRY RUN', text)
        with contextlib.redirect_stdout(output := io.StringIO()):
            q.preflight('H03', '/dev/ttyUSB1', start_channel=3)
        self.assertIn('CONFIRM H03 RESUME_CH3 INDEPENDENT ALL_OFF', output.getvalue())
        self.assertIn('questionable', output.getvalue().lower())

    def test_h03_resume_requires_reviewed_prior_points_and_off_confirmation(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            ch1_path, ch2_path = Path(tmp) / 'ch1.jsonl', Path(tmp) / 'ch2.jsonl'
            ch1_events = [
                {'test_id': 'H03', 'event': 'measurement_review', 'channel': 1,
                 'mode': mode, 'review_outcome': 'PASS'}
                for mode in ('CV', 'CC')
            ]
            ch1_events.append({'test_id': 'H03', 'event': 'outcome',
                               'physical_off_confirmed': True})
            ch2_events = [
                {'test_id': 'H03', 'event': 'measurement_review', 'channel': 2,
                 'mode': mode, 'review_outcome': 'PASS'}
                for mode in ('CV', 'CC')
            ]
            ch2_events.append({'test_id': 'H03', 'event': 'outcome',
                               'physical_off_confirmed': True})
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            ch2_path.write_text('\n'.join(json.dumps(event) for event in ch2_events), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)
            ch1_events.pop()
            ch2_events.pop()
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            ch2_path.write_text('\n'.join(json.dumps(event) for event in ch2_events), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'OFF confirmation'):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)

    def test_h03_success_trace_and_error_170_stop(self):
        """Exercise the bounded H03 source/load flow using simulated I/O only."""
        q = self.module()
        from channel_stage import h03
        from test_driver import FakeTransport

        def operator_answers(disagree_cc=False):
            answers = []
            for channel in (1, 2, 3):
                if channel > 1:
                    answers.append(f'CONFIRM H03 MOVE CH{channel - 1}->CH{channel} ALL_OFF LOAD=OFF')
                for mode, resistance in (('CV', '40.0'), ('CC', '12.0')):
                    answers.extend((
                        f'CONFIRM H03 CH{channel} {mode} CR={resistance} CONNECTED PSU=OFF',
                        f'CONFIRM H03 DISPLAY CH{channel} PSU={mode} KEL=CR INPUT=ON',
                        f'CONFIRM H03 CH{channel} {mode} PSU=OFF LOAD=OFF'))
            return answers

        class LoadFake:
            def __init__(self):
                self.port = 18190
                self.input = 'OFF'
                self.resistance = None
                self.cc_voltage = 3.0
                self.live = True
                self.closed = False
            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_cr(self, resistance):
                if self.input != 'OFF': raise RuntimeError('load input active')
                self.resistance = resistance
                return resistance
            def set_input(self, enabled): self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                return ({'kel103_voltage_v': 4.0, 'kel103_current_a': .1, 'kel103_power_w': .4}
                        if self.resistance == 40.0 else
                        {'kel103_voltage_v': self.cc_voltage, 'kel103_current_a': .25,
                         'kel103_power_w': self.cc_voltage * .25})
            def close(self): self.closed = True

        class MeterFake(FakeTransport):
            def query(self, command):
                key = command.upper().removesuffix('?')
                if key == 'SYST:ERR':
                    self.cmds.append(command)
                    if getattr(self, 'initial_170', False) and not self.initial_170_consumed:
                        self.initial_170_consumed = True
                        return '170,"Invalid command"'
                    if getattr(self, 'inject_170', False):
                        self.inject_170 = False
                        return '170,"Invalid command"'
                    return '0,"No error"'
                if key == 'STAT:OPER:INST:ISUM1:COND':
                    return self._status(command, 1)
                if key == 'STAT:OPER:INST:ISUM2:COND':
                    return self._status(command, 2)
                if key == 'STAT:OPER:INST:ISUM3:COND':
                    return self._status(command, 3)
                if key in ('STAT:QUES:INST:ISUM1:COND',
                           'STAT:QUES:INST:ISUM2:COND',
                           'STAT:QUES:INST:ISUM3:COND'):
                    channel = int(key.split('ISUM')[1].split(':')[0])
                    return self._status(command, channel)
                if key in ('MEAS:VOLT', 'MEAS:CURR', 'MEAS:POW'):
                    self.cmds.append(command)
                    values = self.values[self.active_ch]
                    cc = values['CURR'] == .25
                    volts, amps = (getattr(self, 'cc_voltage', 3.0), .25) if cc else (4.0, .1)
                    if key == 'MEAS:VOLT': return str(volts)
                    if key == 'MEAS:CURR': return str(amps)
                    return str(volts * amps)
                return super().query(command)

            def _status(self, command, channel):
                self.cmds.append(command)
                if not self.values[channel]['CHAN:OUTP']:
                    return '0'
                if (command.upper().startswith('STAT:OPER:') and channel == 3
                        and getattr(self, 'channel3_status_override', None) is not None):
                    return str(self.channel3_status_override)
                return '10' if self.values[channel]['CURR'] == .25 else '9'

            def write(self, command):
                super().write(command)
                if command == 'CHAN:OUTP 1': self.had_output_on = True
                if (command == 'OUTP 0' and getattr(self, 'ignore_global_off', False)
                        and getattr(self, 'had_output_on', False)):
                    self.values[1]['CHAN:OUTP'] = 1.
                if command == 'VOLT:LIM 4.00' and getattr(self, 'fail_next_limit', False):
                    self.inject_170 = True

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'success.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'PASS', [line for line in evidence.path.read_text().splitlines()
                                               if 'stage_error' in line])
            self.assertTrue(fake.closed)
            self.assertTrue(load.closed)
            self.assertEqual(load.input, 'OFF')
            self.assertEqual(sum(command == 'CHAN:OUTP 1' for command in fake.cmds), 6)
            self.assertEqual(sum(command == 'CHAN:OUTP 0' for command in fake.cmds), 6)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(evidence.path.read_text().count('"event": "measurement"'), 6)
            self.assertIn('"outcome": "PASS"', evidence.path.read_text())

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            fake.initial_170 = True
            fake.initial_170_consumed = False
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'preexisting170.jsonl', 'H03')
            with contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertEqual(fake.cmds[0], 'SYST:ERR?')
            self.assertNotIn('OUTP:SER 0', fake.cmds)
            self.assertIn('pre-existing instrument error', evidence.path.read_text())

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.fail_next_limit = True
            evidence = q.Evidence(Path(tmp) / 'error170.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertFalse(any(command == 'CHAN:OUTP 1' for command in fake.cmds))
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertTrue(any(event.get('response') == '170,"Invalid command"' for event in events))

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.cc_voltage = 3.08919
            load.cc_voltage = 3.008
            fake.ignore_global_off = True
            evidence = q.Evidence(Path(tmp) / 'measurement_mismatch.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers(disagree_cc=True)), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertIn('CHAN:OUTP 0', fake.cmds)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            failed_measurement = next(event for event in events
                                      if event.get('event') == 'measurement'
                                      and event.get('channel') == 1 and event.get('mode') == 'CC')
            self.assertEqual(failed_measurement['kel103_voltage_v'], 3.008)
            self.assertTrue(any(event.get('event') == 'cleanup_error' for event in events) is False)

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.channel3_status_override = 8
            evidence = q.Evidence(Path(tmp) / 'channel3_status_mismatch.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'PARTIAL')
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(load.input, 'OFF')
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            diagnostic = next(event for event in events
                              if event.get('event') == 'measurement'
                              and event.get('channel') == 3 and event.get('mode') == 'CV')
            self.assertEqual(diagnostic['keithley_status_mask'], 8)
            self.assertEqual(diagnostic['keithley_questionable_status_mask'], 9)
            self.assertTrue(any(event.get('event') == 'operator_display_confirmation'
                                and event.get('channel') == 3 and event.get('keithley_panel_mode') == 'CV'
                                for event in events))
            self.assertEqual([event['mode'] for event in events
                              if event.get('event') == 'measurement'
                              and event.get('channel') == 3], ['CV', 'CC'])
            self.assertEqual(sum(event.get('event') == 'status_unqualified'
                                 and event.get('channel') == 3 for event in events), 2)
            self.assertFalse(any(event.get('event') == 'stage_error' for event in events))

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'resume_ch3.jsonl', 'H03')
            answers = (
                'CONFIRM H03 CH3 CV CR=40.0 CONNECTED PSU=OFF',
                'CONFIRM H03 DISPLAY CH3 PSU=CV KEL=CR INPUT=ON',
                'CONFIRM H03 CH3 CV PSU=OFF LOAD=OFF',
                'CONFIRM H03 CH3 CC CR=12.0 CONNECTED PSU=OFF',
                'CONFIRM H03 DISPLAY CH3 PSU=CC KEL=CR INPUT=ON',
                'CONFIRM H03 CH3 CC PSU=OFF LOAD=OFF',
            )
            with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3)
            self.assertEqual(outcome, 'PASS')
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertFalse(any(event.get('action') == 'load relocation' for event in events))
            self.assertEqual([event['mode'] for event in events
                              if event.get('event') == 'measurement'], ['CV', 'CC'])

        with tempfile.TemporaryDirectory() as tmp:
            class TimedOutLoad(LoadFake):
                def identify(self): raise TimeoutError('simulated UDP timeout')

            fake = MeterFake()
            load = TimedOutLoad()
            evidence = q.Evidence(Path(tmp) / 'load_timeout.jsonl', 'H03')
            with contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3)
            self.assertEqual(outcome, 'ABORTED')
            self.assertEqual(fake.cmds[0], 'SYST:REM')
            self.assertGreater(fake.cmds.index('OUTP 0'), fake.cmds.index('SYST:REM'))
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))

    def test_unattended_and_future_stages_reject_before_open(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H03', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', 'yes']):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', q.TOKEN]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)

    def test_h02_profile_trace_and_stop_on_first_error(self):
        """Exercise the exact profile trace using simulated I/O only."""
        q = self.module()
        from profile_stage import CheckedIO, command_plan, h02
        from test_driver import FakeTransport
        for bad in ('none', 'error170', 'timeout', 'cap_mismatch', 'initial_on', 'identity_change'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                class ProfileFake(FakeTransport):
                    pending_error = False
                    reduced_cap_written = False
                    idn_count = 0
                    def write(self, command):
                        super().write(command)
                        if bad == 'error170' and command == 'CURR 0': self.pending_error = True
                        if command == 'VOLT:LIM 4.00': self.reduced_cap_written = True
                    def query(self, command):
                        if bad == 'identity_change' and command == '*IDN?':
                            self.idn_count += 1
                            if self.idn_count == 2:
                                self.cmds.append(command)
                                return 'Keithley instruments, 2231A-30-3, TEST, 1.04-0.01'
                        answer = super().query(command)
                        if command == 'SYST:ERR?' and self.pending_error:
                            self.pending_error = False
                            return '170,"Invalid command"'
                        if command == 'VOLT:LIM?' and self.reduced_cap_written:
                            if bad == 'timeout': raise OSError('simulated uncertain link')
                            if bad == 'cap_mismatch': return '3.99'
                        return answer
                fake = ProfileFake()
                if bad == 'initial_on': fake.values[2]['CHAN:OUTP'] = 1.
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H02')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = h02(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL')
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd in ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK', '*CLS', '*RST') for cmd in fake.cmds))
                if bad == 'none':
                    self.assertEqual(tuple(fake.cmds), command_plan())
                    self.assertEqual(fake.active_ch, 1)
                    for ch in (1, 2, 3):
                        self.assertEqual(fake.values[ch]['VOLT'], 1.)
                        self.assertEqual(fake.values[ch]['CURR'], .1)
                        self.assertEqual(fake.values[ch]['VOLT:LIM'], 4.)
                        self.assertEqual(fake.values[ch]['VOLT:LIM:STAT'], 1.)
                        self.assertEqual(fake.values[ch]['CHAN:OUTP'], 0.)
                    self.assertEqual(evidence.path.read_text().count('"event": "case"'), 21)
                if bad == 'timeout': self.assertEqual(fake.cmds[-1], 'VOLT:LIM?')
                if bad in ('error170', 'initial_on', 'identity_change'):
                    self.assertNotIn('VOLT:LIM 4.00', fake.cmds)
                self.assertNotIn(', TEST,', evidence.path.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            io_guard = CheckedIO(fake, q.Evidence(Path(tmp) / 'events.jsonl', 'H02'))
            with self.assertRaises(ValueError): io_guard.write('OUTP 1')
            self.assertEqual(fake.cmds, [])
            self.assertEqual(io_guard.attempts, 1)
        with tempfile.TemporaryDirectory() as tmp:
            from keithley2231a import SafetyError
            fake = FakeTransport()
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H02')
            def broken_output(dev, channel, enabled):
                try: dev._io.write('OUTP 1')
                except ValueError: raise SafetyError('simulated driver relying on stage guard')
            with patch('profile_stage.Keithley2231A.output', new=broken_output):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(h02(fake, evidence), 'FAIL')
            self.assertNotIn('OUTP 1', fake.cmds)

    def test_h01_validates_off_identity_and_error_without_output_writes(self):
        q = self.module()
        from test_driver import FakeTransport
        class ProbeFake(FakeTransport):
            def query(self, command):
                if command == 'INST:NSEL?': return '1'
                if command == 'OUTP:ENABLE?': return '1'
                return super().query(command)
        for bad in ('none', 'old170', 'new170', 'on', 'model', 'error', 'timeout'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                fake = ProbeFake()
                base = fake.query
                error_reads = 0
                def query(command):
                    nonlocal error_reads
                    if command == 'SYST:ERR?':
                        error_reads += 1
                        if bad == 'old170' and error_reads == 1: return '170,"Invalid command"'
                        if bad == 'new170' and error_reads == 2: return '170,"Invalid command"'
                    if bad == 'on' and command == 'STAT:OPER:INST:ISUM2:COND?': return '8'
                    if bad == 'model' and command == '*IDN?': return 'Other, MODEL, SECRET, fw'
                    if bad == 'error' and command == 'SYST:ERR?': return '-100,"Command error"'
                    if bad == 'timeout' and command == 'SYST:VERS?': raise OSError('link lost')
                    return base(command)
                fake.query = query
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = q.h01(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad in ('none', 'old170') else 'ABORTED' if bad == 'timeout' else 'FAIL')
                content = evidence.path.read_text()
                self.assertNotIn('SECRET', content)
                self.assertNotIn(', TEST,', content)
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ', 'INST:NSEL ')) for cmd in fake.cmds))
                if bad == 'timeout':
                    self.assertEqual(error_reads, 1)  # No follow-up query on the failed link.
                    self.assertIn('io_error', content)
                    self.assertIn('OSError', content)
                if bad == 'old170': self.assertIn('preexisting_error', content)

    def test_evidence_redacts_before_serializing(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-1.04')
            self.assertNotIn('PRIVATE123', evidence.path.read_text())
            self.assertIn('1.04-1.04', evidence.path.read_text())
            rendered = io.StringIO()
            with contextlib.redirect_stdout(rendered): q.report(evidence.path)
            self.assertNotIn('PRIVATE123', rendered.getvalue())
            self.assertIn('1.04-1.04', rendered.getvalue())
