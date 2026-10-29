"""O01 staged hardware runner has NO live CLI; all tests are in-memory."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from support.qualification_transport import FakeTransport
from qualification import Evidence, main, stages
from bench_config import EXAMPLE, activate_bench, load_bench
from connection_gate import required_devices
from o01_stage import CONNECT_TOKEN, O01Scope, o01
from keithley2231a import SafetyError


class SimDMM:
    def __init__(self, psu):
        self.psu = psu
        self.configured = False
        self.live = True
        self.range_v = None
        self.calls = 0
        self.fail_on = None
        self.disconnect_during_read = False
        self.closed = False
        self.config_calls = 0

    def configure_voltage(self, range_v):
        if self.psu.flags['OUTP:STAT:ALL']:
            raise AssertionError('DMM reset with powered output')
        self.config_calls += 1
        self.configured = True
        self.range_v = range_v

    def measure(self):
        self.calls += 1
        if self.fail_on == self.calls:
            self.live = False
            raise OSError('Synthetic DMM lost TCP')
        if self.disconnect_during_read:
            self.configured = False
        on = self.psu.values[1]['CHAN:OUTP']
        return {'voltage_v': self.psu.values[1]['VOLT'] - .001 if on else 0.0}

    def close(self):
        self.live = False
        self.configured = False
        self.closed = True


class O01StagePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        activate_bench(load_bench(EXAMPLE))

    def test_registry_and_preflight_are_offline(self):
        self.assertIn('O01', stages())
        self.assertEqual(required_devices('O01'), ('psu', 'meter'))
        for argv in (['preflight', 'O01'], ['run', 'O01', '--dry-run']):
            with self.subTest(argv=argv), \
                 patch('qualification.open_transport', side_effect=AssertionError('opened')), \
                 patch('qualification.open_meter', side_effect=AssertionError('opened')):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main([*argv, '--config', str(EXAMPLE)]), 0)
                self.assertIn('HW NOT_RUN', output.getvalue())
                self.assertIn('DRY RUN:', output.getvalue())

    def test_execute_is_blocked_before_instruments_or_usb_discovery(self):
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / 'bench.local.toml'
            local.write_bytes(EXAMPLE.read_bytes())
            with patch('qualification.open_transport', side_effect=AssertionError('opened')), \
                 patch('qualification.open_meter', side_effect=AssertionError('opened')), \
                 patch('qualification.verify_serial_adapter', side_effect=AssertionError('USB probed')):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(main(['run', 'O01', '--config', str(local), '--execute']), 2)
                self.assertIn('BLOCKED: O01 live run requires --allow-o01-on', out.getvalue())

    def _run(self, raw=None, meter=None, gate=True):
        raw = raw or FakeTransport()
        meter = meter or SimDMM(raw)
        with tempfile.TemporaryDirectory() as folder:
            ev = Evidence(Path(folder)/'events.jsonl', 'O01')
            gates = []
            def approve(token, desc, io):
                gates.append(token)
                if not gate:
                    raise KeyboardInterrupt('operator declined')
                if any(io.outputs.values()) or raw.flags['OUTP:STAT:ALL']:
                    raise AssertionError('O01 connection gate with output ON')
            with patch('o01_stage.operator_gate', side_effect=approve), \
                 patch('keithley2231a.sweep.time.sleep'), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = o01(raw, ev, meter)
            records = [json.loads(x) for x in ev.path.read_text().splitlines()]
            csv_path = Path(folder)/'o01_sweep.csv'
            csv_rows = csv_path.read_text() if csv_path.exists() else None
        return result, raw, meter, gates, records, csv_rows

    def test_integrated_real_dispatch_and_csv(self):
        result, raw, meter, gates, events, csv_text = self._run()
        self.assertEqual(result, 'PASS')
        self.assertEqual(gates, [CONNECT_TOKEN])
        self.assertEqual(meter.calls, 4)  # OFF proof + three powered points
        self.assertEqual(meter.config_calls, 1)
        self.assertTrue(meter.closed and raw.closed)
        self.assertEqual(raw.cmds.count('CHAN:OUTP 1'), 1)
        self.assertEqual(raw.flags['OUTP:STAT:ALL'], 0)
        self.assertTrue(all(raw.values[ch]['CHAN:OUTP'] == 0 for ch in (1,2,3)))
        self.assertIn('dmm_query_duration_s', csv_text)
        self.assertEqual(len([e for e in events if e['event']=='coordinated_measurement']), 3)
        self.assertEqual(len([e for e in events if e['event']=='csv_verified']), 1)
        self.assertEqual(len([e for e in events if e['event']=='electrical_shutdown' and e['state']=='VERIFIED_OFF']), 1)
        self.assertFalse([e for e in events if e['event']=='stage_error'])
        self.assertEqual(events[-1]['outcome'], 'PASS')
        self.assertTrue(all(e['response'].lstrip().startswith('0,') for e in events
                        if e['event']=='read' and e.get('command')=='SYST:ERR?'))

    def test_second_powered_dmm_failure_does_not_continue_sweep(self):
        raw = FakeTransport()
        meter = SimDMM(raw)
        meter.fail_on = 3  # 1 OFF and 1 CV sample then second powered sample
        outcome, raw, meter, gates, events, _ = self._run(raw, meter)
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(meter.calls, 3)
        self.assertEqual(raw.flags['OUTP:STAT:ALL'], 0)
        self.assertNotIn('VOLT 1.50', raw.cmds[raw.cmds.index('CHAN:OUTP 1'):])
        self.assertFalse([e for e in events if e['event']=='query_outcome' and e['outcome']=='PASS'])

    def test_refused_gate_never_enables_output(self):
        outcome, raw, meter, gates, events, _ = self._run(gate=False)
        self.assertEqual(outcome, 'ABORTED')
        self.assertNotIn('CHAN:OUTP 1', raw.cmds)
        self.assertEqual(raw.flags['OUTP:STAT:ALL'],0)
        self.assertEqual(meter.calls, 0)

    def test_meter_configuration_fault_never_enables_and_closes_everything(self):
        raw=FakeTransport()
        class BrokenSetup(SimDMM):
            def configure_voltage(self, range_v):
                self.live = False
                raise OSError('DMM setup failed')
        meter=BrokenSetup(raw)
        status, raw, meter, gates, events, _ = self._run(raw, meter)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(gates, [])
        self.assertNotIn('CHAN:OUTP 1', raw.cmds)
        self.assertTrue(raw.closed and meter.closed)
        self.assertTrue(any(e['event']=='electrical_shutdown' and e['state']=='VERIFIED_OFF' for e in events))

    def test_uncertain_psu_link_must_not_send_any_more_scpi(self):
        class BrokenOnceLive(FakeTransport):
            def query(self, cmd):
                if cmd == 'MEAS:VOLT?' and self.values[1]['CHAN:OUTP']:
                    self.cmds.append(cmd)
                    raise OSError('UART interrupted during live measurement')
                return super().query(cmd)
        raw=BrokenOnceLive()
        result, raw, meter, gates, events, _ = self._run(raw)
        self.assertEqual(result,'ABORTED')
        self.assertTrue(raw.closed and meter.closed)
        self.assertEqual(raw.cmds[-1], 'MEAS:VOLT?')
        self.assertTrue(any(e['event']=='electrical_shutdown' and e['state']=='UNKNOWN' for e in events))
        self.assertFalse(any(e['event']=='query_outcome' and e['outcome']=='PASS' for e in events))

    def test_real_dmm7510_adapter_emits_exact_scpi_sequence_before_ch1_on(self):
        from equipment.dmm7510 import DMM7510
        raw = FakeTransport()
        class FakeDmmWire:
            def __init__(self):
                self.commands=[]
                self.closed=False
                self.rng=10
            def query(self, cmd):
                self.commands.append(cmd)
                return {'*IDN?':'Keithley Instruments,MODEL DMM7510,TEST,1.0',
                        '*LANG?':'SCPI',':SYST:ERR?':'0,"No error"',
                        ':ROUT:TERM?':'FRON',':SENS:FUNC?':'"VOLT:DC"',
                        ':SENS:VOLT:RANG?':str(self.rng),
                        ':SENS:VOLT:NPLC?':'1'}.get(
                            cmd, str(raw.values[1]['VOLT']-.001 if raw.values[1]['CHAN:OUTP'] else 0.0)
                            if cmd==':READ?' else 'UNEXPECTED')
            def write(self, cmd):
                self.commands.append(cmd)
                if cmd.startswith(':SENS:VOLT:RANG '):
                    self.rng=int(cmd.split()[-1])
            def close(self):
                self.closed=True
        wire=FakeDmmWire()
        with tempfile.TemporaryDirectory() as td:
            ev=Evidence(Path(td)/'events.jsonl', 'O01')
            meter=DMM7510(wire,ev)
            gate=[]
            def check_gate(token, prompt, io):
                gate.append(token)
                self.assertNotIn('CHAN:OUTP 1', raw.cmds)
                self.assertEqual(raw.flags['OUTP:STAT:ALL'],0)
                self.assertIn(':SENS:VOLT:AZER ON',wire.commands)
            with patch('o01_stage.operator_gate', side_effect=check_gate), \
                 patch('keithley2231a.sweep.time.sleep'), \
                 contextlib.redirect_stdout(io.StringIO()):
                status=o01(raw,ev,meter)
            records=[json.loads(x) for x in ev.path.read_text().splitlines()]
        self.assertEqual(status,'PASS')
        self.assertEqual(gate,[CONNECT_TOKEN])
        self.assertEqual(wire.commands.count('*RST'),1)
        self.assertEqual(wire.commands.count(':READ?'),4)
        self.assertEqual(wire.commands[0:2],['*IDN?','*LANG?'])
        self.assertLess(wire.commands.index(':ROUT:TERM?'),wire.commands.index('*RST'))
        self.assertTrue(wire.closed and raw.closed)
        self.assertEqual(raw.cmds.count('CHAN:OUTP 1'),1)
        self.assertTrue(all(e['response'].startswith('0,') for e in records
                            if e['event']=='dmm7510_read' and e.get('command')==':SYST:ERR?'))
        self.assertTrue(all(e['response'].startswith('0,') for e in records
                            if e['event']=='read' and e.get('command')=='SYST:ERR?'))

    def test_scope_cannot_enable_other_channels_or_without_gate(self):
        from unittest.mock import Mock
        io_adapter = O01Scope(FakeTransport(), Mock())
        io_adapter.selected = 1
        with self.assertRaises(SafetyError):
            io_adapter._validate('CHAN:OUTP 1', False)
        io_adapter.authorized_channel = 1
        io_adapter.selected = 2
        with self.assertRaises(SafetyError):
            io_adapter._validate('CHAN:OUTP 1', False)
        io_adapter.selected = 1
        with self.assertRaises(ValueError):
            io_adapter._validate('OUTP 1', False)
        io_adapter._validate('CHAN:OUTP 1', False)
        io_adapter._after_write('CHAN:OUTP 1')
        io_adapter._after_write('CHAN:OUTP 0')
        with self.assertRaises(SafetyError):
            io_adapter._validate('CHAN:OUTP 1', False)

    def test_dmm_unready_during_read_stops_with_off(self):
        raw=FakeTransport()
        meter=SimDMM(raw)
        meter.disconnect_during_read=True
        outcome, raw, meter, _, events, _ = self._run(raw,meter)
        self.assertEqual(outcome, 'FAIL')
        self.assertEqual(raw.flags['OUTP:STAT:ALL'],0)
        self.assertFalse([e for e in events if e['event']=='query_outcome' and e['outcome']=='PASS'])




class O01GatedCliTests(unittest.TestCase):
    """The production O01 CLI entry point, with every instrument faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'bench.local.toml'
        self.config.write_bytes(EXAMPLE.read_bytes())
        self.bench = load_bench(self.config)
        (self.root / 'docs/hardware_reports').mkdir(parents=True)
        self.args = ['run', 'O01', '--config', str(self.config), '--execute', '--allow-o01-on']

    def _run(self, *, fail_meter=None, final_refuse=False, bench_refuse=False):
        from connection_gate import connection_token
        from o01_stage import approval_token, CONNECT_TOKEN, FINAL_TOKEN
        from unittest.mock import Mock
        raw = FakeTransport()
        meter = SimDMM(raw)
        meter.fail_on = fail_meter
        bench_token = connection_token(self.bench, 'O01')
        stage_token = approval_token(self.bench.meter.host)
        expected = [bench_token, stage_token, CONNECT_TOKEN, FINAL_TOKEN]
        confirmations = []
        def input_fake(prompt):
            token = next((line.strip() for line in prompt.splitlines()
                         if line.strip().startswith('CONFIRM ')), None)
            if token is None:
                raise AssertionError('O01 prompt without a unique token')
            confirmations.append(token)
            if bench_refuse and token == bench_token:
                return 'DENIED'
            if final_refuse and token == FINAL_TOKEN:
                raise EOFError('operator does not confirm physical OFF')
            return token
        output = io.StringIO()
        with patch('qualification.ROOT', self.root), \
             patch('qualification.open_transport', return_value=raw) as source_open, \
             patch('qualification.open_meter', return_value=meter) as meter_open, \
             patch('qualification.open_load', side_effect=AssertionError('KEL must not open')), \
             patch('qualification.verify_serial_adapter', return_value=True) as usb_probe, \
             patch('qualification.sys.stdin.isatty', return_value=True), \
             patch('qualification.software_metadata', return_value={'source_sha256':'simulated', 'dirty':True}), \
             patch('builtins.input', side_effect=input_fake), \
             patch('keithley2231a.sweep.time.sleep'), \
             contextlib.redirect_stdout(output):
            result = main(self.args)
        records = []
        reports = list((self.root / 'docs/hardware_reports').glob('*_O01_qualification.md'))
        if source_open.call_count:
            event_files = list((self.root / 'reports/hardware').glob('*/events.jsonl'))
            self.assertEqual(len(event_files), 1)
            records = [json.loads(x) for x in event_files[0].read_text().splitlines()]
        return result, raw, meter, confirmations, expected, records, reports, output.getvalue(), source_open, meter_open, usb_probe

    def test_full_cli_hardware_path_is_one_gated_ch1_sweep(self):
        result, raw, meter, gates, expected, events, reports, output, opened_psu, opened_meter, usb = self._run()
        self.assertEqual(result, 0, output[-1200:])
        self.assertEqual(gates, expected)
        self.assertEqual(opened_psu.call_count, 1)
        self.assertEqual(opened_meter.call_count, 1)
        self.assertEqual(usb.call_count, 1)
        self.assertEqual(raw.cmds.count('CHAN:OUTP 1'), 1)
        self.assertNotIn('OUTP 1', raw.cmds)
        self.assertTrue(raw.closed and meter.closed)
        self.assertEqual([e['requested_v'] for e in events if e['event']=='coordinated_measurement'], [.5, 1., 1.5])
        self.assertEqual(events[-1]['outcome'], 'PASS')
        self.assertTrue(events[-1]['physical_off_confirmed'])
        self.assertEqual(len(reports), 1)
        self.assertEqual(len(list((self.root/'reports/hardware').glob('*/o01_sweep.csv'))), 1)
        self.assertTrue(any(e.get('event')=='electrical_shutdown' and e.get('state')=='VERIFIED_OFF' for e in events))

    def test_final_operator_confirmation_is_required_for_pass(self):
        status, raw, meter, gates, expected, events, _, _, *_ = self._run(final_refuse=True)
        self.assertEqual(status, 2)
        self.assertEqual(gates, expected)
        self.assertEqual(events[-1]['outcome'], 'PARTIAL')
        self.assertFalse(events[-1]['physical_off_confirmed'])
        self.assertEqual(raw.flags['OUTP:STAT:ALL'], 0)

    def test_bench_gate_rejection_does_not_open_any_device(self):
        status, raw, meter, gates, expected, events, reports, output, opened_psu, opened_meter, usb = self._run(bench_refuse=True)
        self.assertEqual(status, 2)
        self.assertEqual(gates, expected[:1])
        opened_psu.assert_not_called()
        opened_meter.assert_not_called()
        self.assertFalse(events)
        self.assertNotIn('CHAN:OUTP 1', raw.cmds)

    def test_malfunctioning_dmm_does_not_get_cli_pass(self):
        status, raw, meter, gates, _, events, _, _, *_ = self._run(fail_meter=3)
        self.assertEqual(status, 2)
        self.assertEqual(events[-1]['outcome'], 'ABORTED')
        self.assertTrue(events[-1]['physical_off_confirmed'])
        self.assertEqual(raw.flags['OUTP:STAT:ALL'], 0)
        self.assertEqual(raw.cmds.count('CHAN:OUTP 1'), 1)
        self.assertNotIn('VOLT 1.50', raw.cmds[raw.cmds.index('CHAN:OUTP 1'):])
        self.assertTrue(raw.closed and meter.closed)

    def test_opt_in_refuses_noninteractive_even_before_usb_discovery(self):
        with patch('qualification.verify_serial_adapter', side_effect=AssertionError('USB reached')), \
             patch('qualification.open_transport', side_effect=AssertionError('PSU reached')), \
             patch('qualification.open_meter', side_effect=AssertionError('DMM reached')), \
             patch('qualification.sys.stdin.isatty', return_value=False), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(self.args), 2)
        self.assertIn('интерактивный терминал', out.getvalue())

    def test_opt_in_is_exclusive_to_o01_and_requires_execute(self):
        for args in (['run','H01','--execute','--allow-o01-on'],
                     ['run','O01','--allow-o01-on'],
                     ['preflight','O01','--execute','--allow-o01-on'],
                     ['run','O01','--execute','--allow-o01-on','--start-channel','2']):
            with self.subTest(args=args), \
                 contextlib.redirect_stderr(io.StringIO()), \
                 self.assertRaises(SystemExit) as raised:
                main([*args, '--config',str(self.config)])
            self.assertEqual(raised.exception.code, 2)


if __name__=='__main__': unittest.main()
