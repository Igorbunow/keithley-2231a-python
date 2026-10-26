"""Regression checks for qualification gates and evidence, using simulated I/O."""
import contextlib
import io
import json
import runpy
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class TestQualification(unittest.TestCase):
    def assert_driver_path(self, evidence, transport):
        """Every simulated wire command must come from the common driver dispatch."""
        records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
        sent = [r['trace']['command'] for r in records if r['event'] == 'driver_trace'
                and r['trace']['event'] in ('write', 'query')]
        self.assertEqual(sent, transport.cmds)

    def module(self):
        import qualification
        return qualification

    def test_dry_run_never_constructs_transport(self):
        q = self.module()
        for args in (['list'], ['preflight', 'H01'], ['run', 'H01'],
                     ['preflight', 'H02'], ['run', 'H02'],
                     ['preflight', 'H05'], ['run', 'H05'],
                     ['run', 'H09'], ['abort'], ['report']):
            with self.subTest(args=args), patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
                with contextlib.redirect_stdout(io.StringIO()):
                    q.main(args)

    def test_h05_preflight_is_explicit_and_offline(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.preflight('H05', '/dev/ttyUSB3')
            text = output.getvalue()
            self.assertIn('CH1→CH2→CH3', text)
            self.assertIn('22 Ω', text)
            self.assertIn('×10', text)
            self.assertIn('НЕ ВКЛЮЧАТЬ ВЫХОДЫ', text)

    def test_h05_scope_edge_analysis_and_transport_allowlist(self):
        from h05_stage import H05Scope, decode_isf, transitions

        self.assertEqual(transitions([(0, 0), (1, 1), (2, 1), (3, 0)]),
                         [(0.5, 'RISE'), (2.5, 'FALL')])
        header = (b':WFMPRE:BYT_NR 1;BIT_NR 8;ENCDG BIN;BN_FMT RI;BYT_OR MSB;NR_PT 4;'
                  b'PT_FMT Y;XINCR 1;XZERO 0;PT_OFF 0;YMULT 1;YOFF 0;YZERO 0;'
                  b'XUNIT "s";YUNIT "V";WFID "CH1, synthetic";:CURVE #14')
        raw = header + struct.pack('>4b', -1, 0, 1, 0) + b'\n'
        rows = decode_isf(raw, 1)
        self.assertEqual(transitions(rows), [(1.5, 'RISE'), (2.5, 'FALL')])

        class Raw:
            def __init__(self): self.calls = []
            def write(self, command): self.calls.append(('write', command))
            def query(self, command): self.calls.append(('query', command)); return '0, No error'

        raw = Raw()
        with tempfile.TemporaryDirectory() as tmp:
            guarded = H05Scope(raw, self.module().Evidence(
                Path(tmp) / 'events.jsonl', 'H05'))
            with self.assertRaises(ValueError):
                guarded.write('OUTP 1')
            self.assertEqual(raw.calls, [])

    def test_h05_enters_remote_before_channel_queries(self):
        from h05_stage import h05
        from test_driver import FakeTransport

        class Load:
            enabled = False
            def identify(self): return 'KORAD KEL103'
            def input_state(self): return 'ON' if self.enabled else 'OFF'
            def configure_h04p_cr(self, resistance): pass
            def set_input(self, enabled): self.enabled = enabled
            def measurements(self): return {'kel103_voltage_v': 1.0, 'kel103_current_a': 0.045}
            def close(self): pass

        class Scope:
            def __init__(self, *args): pass
            def configure(self): return {}
            def arm(self): pass
            def collect(self):
                return {ch: [(t, float(start <= t < stop)) for t in range(10)]
                        for ch, start, stop in ((1, 1, 8), (2, 2, 7), (3, 3, 6))}
            def number(self, command): return 0

        with tempfile.TemporaryDirectory() as tmp:
            raw, load = FakeTransport(), Load()
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H05')
            with patch('h05_stage.TDS3064B', Scope), patch('keithley2231a.device.time.sleep'), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h05(raw, evidence, load)
            self.assertEqual(outcome, 'PASS')
            self.assert_driver_path(evidence, raw)
            self.assertTrue(raw.closed)
            self.assertFalse(load.enabled)
            self.assertFalse(any(v['CHAN:OUTP'] for v in raw.values.values()))

    def test_probe_uses_driver_remote_setup_without_changing_outputs(self):
        from test_driver import FakeTransport

        fake = FakeTransport()
        fake.values[2]['CHAN:OUTP'] = 1.0
        initial = {ch: values.copy() for ch, values in fake.values.items()}
        output = io.StringIO()
        with patch('keithley2231a.SerialTransport', return_value=fake), \
                patch.object(sys, 'argv', ['probe.py', '--port', 'SIMULATED']), \
                contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).resolve().parents[2] / 'examples/probe.py'),
                           run_name='__main__')
        self.assertNotIn('ERROR', output.getvalue())
        self.assertEqual(fake.values, initial)
        self.assertTrue(fake.closed)
        self.assertNotIn('OUTP 0', fake.cmds)

    def test_h03_scope_forbids_global_on(self):
        from channel_stage import H03Scope
        from test_driver import FakeTransport
        with tempfile.TemporaryDirectory() as tmp:
            raw = FakeTransport()
            guarded = H03Scope(raw, self.module().Evidence(Path(tmp) / 'scope.jsonl', 'H03'))
            with self.assertRaises(ValueError): guarded.write('OUTP 1')
            self.assertEqual(raw.cmds, [])

    def test_h03_individual_on_requires_authorization_before_transmission(self):
        from channel_stage import H03Scope
        from test_driver import FakeTransport

        with tempfile.TemporaryDirectory() as tmp:
            for authorized in (None, 1, 3):
                with self.subTest(authorized=authorized):
                    fake = FakeTransport()
                    # This test isolates the authorized ON step after Remote setup.
                    fake.write('SYST:REM')
                    fake.cmds.clear()
                    fake.active_ch = 3
                    evidence = self.module().Evidence(
                        Path(tmp) / f'gate_{authorized}.jsonl', 'H03')
                    guarded = H03Scope(fake, evidence, start_channel=3)
                    # Isolate the ON step of an otherwise approved plan.
                    guarded.selected = 3
                    guarded.authorized_channel = authorized
                    if authorized == 3:
                        with contextlib.redirect_stdout(io.StringIO()):
                            guarded.write('CHAN:OUTP 1')
                        self.assertEqual(fake.values[3]['CHAN:OUTP'], 1)
                    else:
                        with self.assertRaises(ValueError):
                            guarded.write('CHAN:OUTP 1')
                        self.assertEqual(fake.cmds, [])
                        self.assertEqual(fake.values[3]['CHAN:OUTP'], 0)

    def test_h03_mistyped_gate_waits_for_valid_confirmation(self):
        from common import operator_gate

        with patch('builtins.input', side_effect=('mistyped confirmation', 'CONFIRM TEST')), \
                contextlib.redirect_stdout(io.StringIO()):
            operator_gate('CONFIRM TEST', 'Verify OFF')

    def test_h03_diagnostic_preflight_is_offline_and_restricts_scope(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.preflight('H03', '/dev/ttyUSB1', start_channel=3,
                            load_transport='serial', diagnose_status=True)
            self.assertIn('DIAGNOSTIC_CH3', output.getvalue())
            for args in (['run', 'H01', '--diagnose-status', '--execute'],
                         ['run', 'H03', '--diagnose-status', '--execute']):
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        q.main(args)

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

    def test_driver_status_wire_trace_preserves_bytes_and_excludes_identity(self):
        from types import SimpleNamespace
        from keithley2231a import Keithley2231A, SerialTransport

        class SerialFake:
            in_waiting = 0
            def write(self, data): self.command = data.decode().strip(); return len(data)
            def flush(self): pass
            def close(self): pass
            def readline(self, size):
                return (b'Keithley instruments, 2231A-30-3, PRIVATE-SERIAL, 1.04-0.01\r\n'
                        if self.command == '*IDN?' else b'8\r\n')

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'wire.jsonl', 'H03')
            with patch.dict(sys.modules, {'serial': SimpleNamespace(Serial=lambda **kw: SerialFake())}):
                wire = SerialTransport('SIMULATED')
            dev = Keithley2231A(wire, debug=True, debug_wire_trace=lambda record: evidence.add(**record))
            dev.diagnostic_query('*IDN?')
            self.assertEqual(dev.diagnostic_query('STAT:OPER:INST:ISUM3:COND?'), '8')
            content = evidence.path.read_text()
            self.assertNotIn('PRIVATE-SERIAL', content)
            records = [json.loads(line) for line in content.splitlines()]
            self.assertEqual([record['event'] for record in records], ['status_wire_write', 'status_wire_read'])
            self.assertEqual(records[0]['tx_hex'], b'STAT:OPER:INST:ISUM3:COND?\r\n'.hex(' '))
            self.assertEqual(records[1]['rx_hex'], '38 0d 0a')
            Keithley2231A(wire).output_states()
            self.assertEqual(evidence.path.read_text(), content)
            wire.close()

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

        h04p = DatagramFake((b'OFF', b'CR', b'CR', b'11.000OHM', b'OFF', b'ON'))
        load = KEL103UDP('192.168.22.106', sock=h04p)
        self.assertEqual(load.configure_h04p_cr(11.0), 11.0)
        load.set_input(True)
        self.assertEqual(h04p.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                     b':RES 11OHM\n', b':FUNC?\n', b':RES?\n',
                                     b':INP?\n', b':INP ON\n', b':INP?\n'])

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
        self.assertTrue(q.stage_passed('**PASS** (reviewed functional scope; caveat)'))
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

    def test_reviewed_pass_and_requalification_are_distinct(self):
        q = self.module()
        self.assertTrue(q.stage_passed('**PASS (OFF-only; reviewed)**'))
        self.assertTrue(q.stage_passed(q.stages()['H01'][-1]))
        self.assertTrue(q.stage_passed(
            '**PASS (HW OBSERVED; reviewed; [evidence](report.md))**'))
        self.assertFalse(q.stage_passed('NOT_RUN (requalification; historical PASS)'))

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
            for records in (ch1_events, ch2_events):
                records.insert(0, {'test_id': 'H03', 'event': 'metadata',
                                   'source_sha256': q.software_metadata()['source_sha256']})
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            ch2_path.write_text('\n'.join(json.dumps(event) for event in ch2_events), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)
            source = ch1_events[0]['source_sha256']
            ch1_events[0]['source_sha256'] = 'older-driver-source'
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current source'):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)
            ch1_events[0]['source_sha256'] = source
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
                for long, short in (('STATUS', 'STAT'), ('OPERATION', 'OPER'),
                                    ('QUESTIONABLE', 'QUES'), ('INSTRUMENT', 'INST'),
                                    ('ISUMMARY', 'ISUM'), ('CONDITION', 'COND')):
                    key = key.replace(long, short)
                if key in ('STAT:OPER:INST:ISUM3:ENAB', 'STAT:QUES:INST:ISUM3:ENAB'):
                    self.cmds.append(command)
                    return '0'
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
                if (command.upper().startswith(('STAT:OPER:', 'STATUS:OPERATION:')) and channel == 3
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
            self.assert_driver_path(evidence, fake)
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
            fake = MeterFake()
            fake.channel3_status_override = 8
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'status_diagnosis.jsonl', 'H03')
            answers = operator_answers()[-6:]
            with patch('builtins.input', side_effect=answers), \
                    patch('channel_stage.time.sleep'), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3, diagnose_status=True)
            self.assertEqual(outcome, 'PARTIAL')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            snapshots = [record for record in records if record['event'] == 'status_diagnostic']
            self.assertEqual([(record['mode'], record['phase'], record['selected_channel'])
                              for record in snapshots],
                             [('CV', 'early', 3), ('CV', 'after_display', 3),
                              ('CV', 'selected_other_channel', 1),
                              ('CC', 'early', 3), ('CC', 'after_display', 3),
                              ('CC', 'selected_other_channel', 1)])
            for record in snapshots:
                self.assertEqual(record['responses']['STAT:OPER:INST:ISUM3:COND?'], '8')
                self.assertEqual(record['responses'][
                    'STATus:OPERation:INSTrument:ISUMmary3:CONDition?'], '8')
            self.assertEqual(fake.cmds.count('CHAN:OUTP 1'), 2)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(load.input, 'OFF')

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
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', 'yes', EOFError()]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', q.TOKEN, EOFError()]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)

    def test_h02_profile_trace_and_stop_on_first_error(self):
        """Exercise the exact profile trace using simulated I/O only."""
        q = self.module()
        from profile_stage import H02Scope, h02
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
                                return 'Keithley instruments, 2231A-30-3, OTHER, 1.04-1.04'
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
                    self.assert_driver_path(evidence, fake)
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
            io_guard = H02Scope(fake, q.Evidence(Path(tmp) / 'events.jsonl', 'H02'))
            with self.assertRaises(ValueError): io_guard.write('OUTP 1')
            self.assertEqual(fake.cmds, [])
            self.assertEqual(io_guard.commands, 1)
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

    def test_h04_offline_runner_stays_off_and_detects_power_only_tampering(self):
        q = self.module()
        from profile_stage import H04Scope, H04_MAXV_TOKEN, h04
        from test_driver import FakeTransport

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            evidence = q.Evidence(Path(tmp) / 'h04.jsonl', 'H04')
            gate_answers = iter(('wrong token', H04_MAXV_TOKEN))
            def panel_clamps_to_limit(_prompt):
                answer = next(gate_answers)
                if answer == H04_MAXV_TOKEN:
                    for channel, value in ((1, 2.0), (2, 3.0), (3, 4.0)):
                        fake.values[channel]['VOLT'] = value
                return answer
            with patch('builtins.input', side_effect=panel_clamps_to_limit), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                outcome = h04(fake, evidence)
            self.assertEqual(outcome, 'PASS')
            self.assertIn('Max Volt Set', output.getvalue())
            self.assert_driver_path(evidence, fake)
            self.assertIn('4.01 V', output.getvalue())
            self.assertTrue(fake.closed)
            self.assertEqual(fake.values[3]['CURR'], .101)
            self.assertEqual(fake.values[1]['VOLT:LIM'], 4.0)
            self.assertEqual([fake.values[ch]['VOLT'] for ch in (1, 2, 3)], [1.0] * 3)
            self.assertTrue(all(value['CHAN:OUTP'] == 0 for value in fake.values.values()))
            self.assertFalse(any(command in ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK',
                                             '*RST', '*CLS') for command in fake.cmds))
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertEqual(records[-1]['event'], 'query_outcome')
            self.assertEqual(records[-1]['outcome'], 'PASS')
            self.assertIn('CURR 0.101', fake.cmds)
            self.assertIn('VOLT:LIM 5.00', fake.cmds)
            self.assertNotIn('VOLT 4.01', fake.cmds)
            self.assertIn('max voltage front-panel setpoint clamped', [
                record.get('check') for record in records])
            maxv_cases = [record for record in records
                          if record.get('check') == 'max voltage boundary rejected by API']
            self.assertEqual([record['channel'] for record in maxv_cases], [1, 2, 3])
            self.assertEqual([record['configured_max_v'] for record in maxv_cases],
                             [2.0, 3.0, 4.0])
            self.assertTrue(all(record['scpi_sent'] is False for record in maxv_cases))
            current_cases = [record for record in records
                             if record.get('check') == 'profile current boundary rejected by API']
            self.assertEqual([record['channel'] for record in current_cases], [1, 2, 3])
            self.assertEqual([record['configured_max_a'] for record in current_cases],
                             [.2, .3, .4])
            self.assertTrue(all(record['scpi_sent'] is False for record in current_cases))

        with tempfile.TemporaryDirectory() as tmp:
            io_guard = H04Scope(FakeTransport(), q.Evidence(
                Path(tmp) / 'pause.jsonl', 'H04'))
            io_guard.started = 0.0
            with patch('profile_stage.time.monotonic', side_effect=(10.0, 310.0)), \
                    patch('builtins.input', return_value='operator response'):
                io_guard.operator_input('manual gate: ')
            self.assertEqual(310.0 - io_guard.started, 10.0)

            io_guard.cleanup = True
            fake = io_guard.transport
            with patch('profile_stage.time.monotonic', return_value=500.0):
                io_guard.write('SYST:REM')
                io_guard.write('OUTP 0')
            self.assertIn('OUTP 0', fake.cmds)

        with tempfile.TemporaryDirectory() as tmp:
            io_guard = H04Scope(FakeTransport(), q.Evidence(
                Path(tmp) / 'guard.jsonl', 'H04'))
            for command in ('OUTP 1', 'CHAN:OUTP 1', 'VOLT 30.00',
                            'SYST:RWLOCK', 'STAT:OPER:EVENT?'):
                before = io_guard.transport.cmds[:]
                with self.subTest(command=command), self.assertRaises(ValueError):
                    (io_guard.query(command) if command.endswith('?') else io_guard.write(command))
                self.assertEqual(io_guard.transport.cmds, before)

    def test_h04_preflight_is_manual_gated_and_dry(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H04']), 0)
        card = output.getvalue()
        self.assertIn('CONFIRM H04 INDEPENDENT ALL_OFF', card)
        self.assertIn('CURR 0.101', card)
        self.assertIn('VOLT:LIM 5.00', card)
        self.assertIn('Max Volt Set', card)
        self.assertIn('H04 MAXV', card)
        self.assertIn('V-cap=2.00 V', card)
        self.assertIn('CH2=2.00/0.200/3.00/0.300', card)
        self.assertIn('CH3=3.00/0.300/4.00/0.400', card)
        self.assertIn('выход', card.lower())

    def test_h04p_preflight_is_dry_and_explains_load_moves(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H04P']), 0)
        card = output.getvalue()
        self.assertIn('CONFIRM H04P', card)
        self.assertIn('KEL103', card)
        self.assertIn('2.20', card)
        self.assertIn('3.30', card)
        self.assertIn('4.40', card)
        self.assertIn('CR 22', card)
        self.assertIn('CR 22/11/5', card)
        self.assertIn('Max Volt Set', card)
        self.assertIn('H04P MAXV', card)
        self.assertIn('DRY RUN', card)

    def test_h04p_offline_runner_auto_measures_and_pauses_only_for_load_moves(self):
        q = self.module()
        from powered_profile_stage import H04P_MAXV_TOKEN, H04PoweredScope, h04_powered
        from test_driver import FakeTransport

        class LoadFake:
            def __init__(self):
                self.input = 'OFF'
                self.resistance = None
                self.live = True
                self.closed = False
            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_h04p_cr(self, resistance):
                if self.input != 'OFF': raise RuntimeError('load input active')
                self.resistance = resistance
                return resistance
            def set_input(self, enabled): self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                voltage, current, mode = self.transport.metrics(self.transport.active_ch)
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        class PoweredFake(FakeTransport):
            def __init__(self, load):
                super().__init__()
                self.load = load
                self.load.transport = self
                self.modes = {}
                self.pending_error = None
                self.status_warmup = {channel: 0 for channel in (1, 2, 3)}
            def metrics(self, channel):
                values = self.values[channel]
                if values['CHAN:OUTP'] == 0 or self.load.input != 'ON':
                    return 0.0, 0.0, 'OFF'
                target_v, limit_a = values['VOLT'], values['CURR']
                demand_a = target_v / self.load.resistance
                if demand_a >= limit_a:
                    return limit_a * self.load.resistance, limit_a, 'CC'
                return target_v, demand_a, 'CV'
            def write(self, command):
                if command == 'CHAN:OUTP 1':
                    self.status_warmup[self.active_ch] = 2
                if command == 'VOLT 4.51':
                    self.cmds.append(command)
                    if (self.values[self.active_ch]['VOLT:LIM:STAT'] != 1 or
                            self.values[self.active_ch]['CHAN:OUTP'] != 0):
                        raise AssertionError('unsafe remote max-voltage probe')
                    self.pending_error = '120,Parameter overflowed'
                    return
                return super().write(command)
            def query(self, command):
                if command == 'SYST:ERR?' and self.pending_error:
                    self.cmds.append(command)
                    response = self.pending_error
                    self.pending_error = None
                    return response
                key = command.upper().removesuffix('?')
                if key in ('MEAS:VOLT', 'MEAS:CURR', 'MEAS:POW'):
                    self.cmds.append(command)
                    voltage, current, mode = self.metrics(self.active_ch)
                    return str({'MEAS:VOLT': voltage, 'MEAS:CURR': current,
                                'MEAS:POW': voltage * current}[key])
                if key.startswith(('STAT:OPER:INST:ISUM', 'STAT:QUES:INST:ISUM')) and key.endswith(':COND'):
                    self.cmds.append(command)
                    channel = int(key.split('ISUM')[1].split(':')[0])
                    enabled = bool(self.values[channel]['CHAN:OUTP'])
                    voltage, current, mode = self.metrics(channel)
                    self.modes[channel] = mode
                    if enabled and self.status_warmup[channel]:
                        self.status_warmup[channel] -= 1
                        return '0'
                    return str((8 if enabled else 0) |
                               (1 if enabled and mode == 'CV' else
                                2 if enabled and mode == 'CC' else 0))
                return super().query(command)

        load = LoadFake()
        fake = PoweredFake(load)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'h04p.jsonl', 'H04P')
            gate_answers = [
                H04P_MAXV_TOKEN,
                'CONFIRM H04P CONNECT CH1 ALL_OFF LOAD=OFF',
                'CONFIRM H04P MOVE CH1->CH2 ALL_OFF LOAD=OFF',
                'CONFIRM H04P MOVE CH2->CH3 ALL_OFF LOAD=OFF',
                'CONFIRM H04P DISCONNECT CH3 ALL_OFF LOAD=OFF',
            ]
            answers = iter(gate_answers)
            def panel_clamps_to_limit(_prompt):
                answer = next(answers)
                if answer == H04P_MAXV_TOKEN:
                    for channel in (1, 2, 3):
                        fake.values[channel]['VOLT'] = 4.5
                return answer
            with patch('builtins.input', side_effect=panel_clamps_to_limit), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = h04_powered(fake, evidence, load)
            self.assertEqual(result, 'PASS', evidence.path.read_text())
            self.assert_driver_path(evidence, fake)
            self.assertTrue(fake.closed)
            self.assertTrue(load.closed)
            self.assertEqual(load.input, 'OFF')
            self.assertTrue(all(values['CHAN:OUTP'] == 0
                                for values in fake.values.values()))
            self.assertFalse(any(command in ('OUTP 1', 'SYST:RWLOCK', '*RST', '*CLS')
                                 for command in fake.cmds))
            self.assertEqual(fake.cmds.count('VOLT 4.51'), 3)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            points = [record for record in records if record['event'] == 'measurement']
            self.assertEqual(len(points), 21)
            maxv_cases = [record for record in records
                          if record.get('check') == 'max voltage boundary rejected by API']
            self.assertEqual([record['channel'] for record in maxv_cases], [1, 2, 3])
            self.assertTrue(all(record['scpi_sent'] is False for record in maxv_cases))
            remote_rejections = [record for record in records
                                 if record.get('check') == 'remote SCPI max-voltage rejection']
            self.assertEqual([record['channel'] for record in remote_rejections], [1, 2, 3])
            self.assertEqual([record['actual_voltage_v'] for record in remote_rejections],
                             [3.3, 3.3, 3.3])
            self.assertTrue(all(record['error_code'] == 120 for record in remote_rejections))
            panel_gate_index = next(index for index, record in enumerate(records)
                                    if record.get('phase') == 'front_panel_max_voltage_clamp')
            remote_probe_index = next(index for index, record in enumerate(records)
                                      if record.get('check') == 'remote SCPI max-voltage rejection')
            self.assertLess(remote_probe_index, panel_gate_index)
            self.assertEqual({point['channel'] for point in points}, {1, 2, 3})
            self.assertEqual({point['mode'] for point in points}, {'CV', 'CC'})
            self.assertEqual(sum(record['event'] == 'operator_gate' for record in records), 5)
            self.assertTrue(any(record.get('phase') == 'front_panel_max_voltage_clamp'
                                for record in records))
            panel_clamps = [record for record in records
                            if record.get('check') == 'max voltage front-panel setpoint clamped']
            self.assertEqual([record['voltage_set_v'] for record in panel_clamps],
                             [4.5, 4.5, 4.5])

            timed_io = H04PoweredScope(FakeTransport(), q.Evidence(
                Path(tmp) / 'pause.jsonl', 'H04P'))
            timed_io.started = 0.0
            with patch('powered_profile_stage.time.monotonic', side_effect=(10., 910.)), \
                    patch('builtins.input', return_value='manual gate'):
                timed_io.operator_input('manual gate: ')
            self.assertEqual(910. - timed_io.started, 10.)

    def test_h01_validates_off_identity_and_error_without_output_writes(self):
        q = self.module()
        from test_driver import FakeTransport
        for bad in ('none', 'old170', 'new170', 'on', 'model', 'error', 'timeout'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                fake = FakeTransport()
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
                    if bad == 'timeout' and command == 'SYST:VERS?':
                        fake.cmds.append(command)
                        raise OSError('link lost')
                    return base(command)
                fake.query = query
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = q.h01(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL')
                if bad == 'none': self.assert_driver_path(evidence, fake)
                content = evidence.path.read_text()
                self.assertNotIn('SECRET', content)
                self.assertNotIn(', TEST,', content)
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ')) for cmd in fake.cmds))
                if bad == 'none':
                    self.assertEqual(fake.active_ch, 1)
                    snapshots = [json.loads(line) for line in content.splitlines()
                                 if json.loads(line)['event'] == 'channel_snapshot']
                    self.assertEqual([r['channel'] for r in snapshots], [1, 2, 3])
                if bad == 'timeout':
                    self.assertEqual(fake.cmds[-1], 'SYST:VERS?')  # No follow-up query.
                    self.assertIn('io_error', content)
                    self.assertIn('OSError', content)

    def test_evidence_redacts_before_serializing(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-1.04')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-0.01')
            evidence.add('outcome', outcome='PASS')
            self.assertNotIn('PRIVATE123', evidence.path.read_text())
            self.assertIn('1.04-1.04', evidence.path.read_text())
            self.assertIn('1.04-0.01', evidence.path.read_text())
            self.assertIn('"firmware_observations": ["1.04-1.04", "1.04-0.01"]',
                          evidence.path.read_text())
            rendered = io.StringIO()
            with contextlib.redirect_stdout(rendered): q.report(evidence.path)
            self.assertNotIn('PRIVATE123', rendered.getvalue())
            self.assertIn('1.04-1.04', rendered.getvalue())
