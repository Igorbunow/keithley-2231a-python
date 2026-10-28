"""End-to-end CLI test for R3.5.7. Mocks every physical port and operator input."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from bench_config import EXAMPLE, load_bench
from qualification import main
from tests.offline.test_h03_single_config import POINT
from tests.offline.test_h03_single_runner import Source, Load


class SingleCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'bench.local.toml'
        self.config.write_text(EXAMPLE.read_text(encoding='utf-8') + '\n' + POINT, encoding='utf-8')
        self.bench = load_bench(self.config)
        self.args = ['run', 'H03', '--single-channel-cvcc', '--config', str(self.config), '--execute', '--allow-single-cvcc-on']

    @staticmethod
    def operator_response(prompt):
        # Fake only: no test is authorized to contact real hardware.
        lines = [line.strip() for line in prompt.splitlines() if line.strip()]
        matches = [l for l in lines if l.startswith('CONFIRM ')]
        if len(matches) != 1:
            raise AssertionError(f'Gate not uniquely tokenized: {prompt}')
        return matches[0]

    def run_cli(self, *, source=None, refuse=None, initial_gate_fail=False, final_fail=False):
        import single_cvcc_cli
        source = source or Source()
        class LoggedLoad(Load):
            evidence = None
            def configure_cr(self, value):
                self.evidence.add('kel103_write', command=':FUNC CR')
                self.evidence.add('kel103_write', command=f':RES {value:g}OHM')
                return super().configure_cr(value)
            def set_input(self, state):
                self.evidence.add('kel103_write', command=':INP ON' if state else ':INP OFF')
                return super().set_input(state)
        load = LoggedLoad(source)
        def open_fake_load(config, evidence):
            load.evidence = evidence
            return load
        gates = []

        def operator(prompt):
            token = self.operator_response(prompt)
            gates.append(token)
            if initial_gate_fail and 'CH1 CVCC ALL_OFF' in token:
                return 'WRONG TOKEN'
            if refuse and refuse in token:
                return 'WRONG TOKEN'
            if final_fail and 'CH1=OFF CH2=OFF CH3=OFF KEL103=OFF' in token:
                return 'WRONG TOKEN'
            return token

        out = StringIO()
        with mock.patch.object(single_cvcc_cli.sys.stdin, 'isatty', return_value=True), \
             mock.patch.object(single_cvcc_cli, 'verify_serial_adapter', return_value=True) as usb, \
             mock.patch.object(single_cvcc_cli, 'open_psu', return_value=source) as psu, \
             mock.patch.object(single_cvcc_cli, 'open_load', side_effect=open_fake_load) as opened_load, \
             mock.patch('qualification.ROOT', self.root), \
             mock.patch('qualification.software_metadata', return_value={'sha':'offline-fixture', 'dirty':True,
                  'source_sha256':'offline-test'}), \
             mock.patch('builtins.input', side_effect=operator), redirect_stdout(out):
            result = main(self.args)
        records = []
        for path in (self.root / 'reports/hardware').glob('*/events.jsonl'):
            records.extend(json.loads(line) for line in path.read_text().splitlines())
        return result, source, load, records, gates, out.getvalue(), usb, psu, opened_load

    def test_two_points_only_ch1_with_final_approval_and_independent_evidence(self):
        ret, psu, load, events, gates, output, usb, opened_psu, opened_load = self.run_cli()
        self.assertEqual(ret, 0, output[-2000:])
        self.assertEqual(usb.call_count, 2)
        self.assertEqual(opened_psu.call_count, 1)
        self.assertEqual(opened_load.call_count, 1)
        self.assertEqual(len(gates), 11)
        self.assertEqual(sum('ENABLE PSU=CH1_ONLY' in t for t in gates), 2)
        self.assertEqual(sum(x == 'CHAN:OUTP 1' for x in psu.cmds), 2)
        self.assertFalse(any(x == 'OUTP 1' for x in psu.cmds))
        self.assertTrue(all(psu.values[i]['CHAN:OUTP'] == 0 for i in (1,2,3)))
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(load.closed and psu.closed)
        self.assertEqual([e['mode'] for e in events if e['event']=='single_cvcc_measurement'], ['CV','CC'])
        self.assertEqual(events[-1]['event'], 'outcome')
        self.assertEqual(events[-1]['outcome'], 'PASS')
        self.assertTrue(events[-1]['physical_off_confirmed'])
        self.assertEqual([e['event'] for e in events].count('bench_connection_confirmation'), 1)
        self.assertEqual([e['event'] for e in events].count('approval'), 1)
        self.assertFalse(any(a == b == 'SYST:ERR?' for a,b in zip(psu.cmds, psu.cmds[1:])))
        self.assertIn('H03_SINGLE_CVCC_qualification.md', output)
        self.assertIn('Keithley 2231A', output)
        self.assertIn('KEL103', output)
        self.assertIn('4.00000', output)
        self.assertIn('3.00000', output)
        from tools.audit_hw_scpi import audit_single_records
        self.assertEqual(audit_single_records(events)['status'], 'PASS')

    def test_wrong_wiring_gate_refuses_before_any_device_open(self):
        ret, psu, load, events, gates, out, usb, opened_psu, opened_load = self.run_cli(initial_gate_fail=True)
        self.assertEqual(ret, 2)
        opened_psu.assert_not_called()
        opened_load.assert_not_called()
        self.assertEqual(psu.cmds, [])
        self.assertEqual(events, [])
        self.assertIn('wiring gate mismatch', out)

    def test_refused_enable_never_powers_psu(self):
        # An operator refusing the first ENABLE gate does not authorize ON;
        # the standard production operator gate loops, so interrupt instead.
        import single_cvcc_stage
        def interrupted_gate(token, prompt, adapter, **kwargs):
            if 'ENABLE' in token:
                raise InterruptedError('operator declined output enable')
        with mock.patch.object(single_cvcc_stage, 'operator_gate', side_effect=interrupted_gate):
            ret, psu, load, events, gates, out, usb, opened_psu, opened_load = self.run_cli()
        self.assertEqual(ret, 2)
        self.assertNotIn('CHAN:OUTP 1', psu.cmds)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(any(e['event']=='stage_error' for e in events))

    def test_final_off_decline_never_promotes_to_pass(self):
        ret, psu, load, events, gates, output, *_ = self.run_cli(final_fail=True)
        self.assertEqual(ret, 2)
        self.assertEqual(events[-1]['outcome'], 'PARTIAL')
        self.assertFalse(events[-1]['physical_off_confirmed'])
        self.assertEqual(load.state, 'OFF')
        from tools.audit_hw_scpi import audit_single_records
        self.assertEqual(audit_single_records(events)['status'], 'FAIL')

    def test_saved_trace_rejects_adjacent_polls_and_unsafe_on(self):
        from tools.audit_hw_scpi import audit_single_records
        ret, psu, load, events, *_ = self.run_cli()
        self.assertEqual(ret, 0)
        import copy
        changed = copy.deepcopy(events)
        for event in changed:
            if event.get('event') == 'driver_trace' and event.get('trace', {}).get('command') == 'CHAN:OUTP 1':
                event['trace']['command'] = 'OUTP 1'
                break
        self.assertEqual(audit_single_records(changed)['status'], 'FAIL')
        no_confirmation = copy.deepcopy(events)
        no_confirmation[-1]['physical_off_confirmed'] = False
        self.assertEqual(audit_single_records(no_confirmation)['status'], 'FAIL')
        missing_check = copy.deepcopy(events)
        for i,event in enumerate(missing_check):
            if event.get('event') == 'driver_trace' and event.get('trace', {}).get('command') == 'SYST:ERR?':
                del missing_check[i]
                break
        self.assertEqual(audit_single_records(missing_check)['status'], 'FAIL')

    def test_noninteractive_refuses_before_usb_enumeration(self):
        from single_cvcc_cli import verify_serial_adapter
        out = StringIO()
        with mock.patch('single_cvcc_cli.verify_serial_adapter', side_effect=AssertionError('USB touched')), \
             mock.patch('single_cvcc_cli.open_psu', side_effect=AssertionError('PSU touched')), \
             mock.patch('single_cvcc_cli.open_load', side_effect=AssertionError('load touched')), \
             mock.patch('single_cvcc_cli.sys.stdin.isatty', return_value=False), \
             redirect_stdout(out):
            self.assertEqual(main(self.args), 2)
        self.assertIn('interactive local terminal required', out.getvalue())


if __name__ == '__main__':
    unittest.main()
