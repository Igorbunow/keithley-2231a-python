"""H10 full-run dispatcher: offline orchestration, evidence, and fail-closed gates."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import qualification as q


class H10FullSequenceTests(unittest.TestCase):
    @staticmethod
    def _events(subgroup, expected, *, bad=None):
        events = [
            {'event': 'metadata', 'test_id': 'H10', 'subgroup': subgroup,
             'source_sha256': 'source_fingerprint'},
            {'event': 'bench_configuration', 'sha256': 'bench_fingerprint'},
            {'event': 'query_outcome', 'outcome': expected, 'firmware': '1.04-0.01'},
            {'event': 'outcome', 'outcome': expected, 'physical_off_confirmed': True},
        ]
        if subgroup == 'parallel_off_diagnostic':
            events += [
                {'event': 'write', 'command': 'OUTP:PARALLEL 1'},
                {'event': 'write', 'command': 'OUTP:PARALLEL 0'},
                {'event': 'parallel_off_snapshot', 'phase': 'independent_after_restoration'},
            ]
        else:
            events.append({'event': 'electrical_shutdown', 'state': 'VERIFIED_OFF',
                           'devices': ['Keithley', 'KEL103']})
            events.append({'event': 'topology_restore', 'state': 'VERIFIED_INDEPENDENT'})
            events.append({'event': 'operator_gate', 'action': 'disconnect_parallel_wiring'})
            points = ('CV3A', 'CC4A') if subgroup == 'parallel_high_current_cv_cc' else ('CV', 'CC')
            for index, point in enumerate(points):
                if index: events.append({'event': 'write', 'command': 'OUTP 0'})
                events.append({'event': 'write', 'command': 'OUTP 1'})
                events.extend({'event': 'parallel_measurement_sample', 'point': point,
                               'sample': i,
                               'psu_regulation': 'CV' if point.startswith('CV') else 'CC'} for i in range(1, 5))
                events.append({'event': 'operator_display_review', 'point': point,
                               'outcome': 'confirmed'})
        if bad == 'physical_off':
            events[3]['physical_off_confirmed'] = False
        if bad == 'wrong_source':
            events[0]['source_sha256'] = 'other'
        if bad == 'missing_cc':
            events = [event for event in events if
                      not (event['event'] == 'parallel_measurement_sample' and
                           event.get('point') in ('CC', 'CC4A'))]
        if bad == 'missing_middle_off':
            events = [event for event in events if not (event['event'] == 'write' and
                                                        event.get('command') == 'OUTP 0')]
        if bad == 'unknown_mode':
            for event in events:
                if event['event'] == 'parallel_measurement_sample':
                    event['psu_regulation'] = 'UNKNOWN'
                    break
        if bad == 'duplicate_sample':
            for event in events:
                if event['event'] == 'parallel_measurement_sample':
                    event['sample'] = 2
                    break
        if bad == 'missing_verified_shutdown':
            events = [event for event in events if event['event'] != 'electrical_shutdown']
        if bad == 'stage_error':
            events.append({'event': 'stage_error', 'exception_type': 'ValueError'})
        return events

    def _synthetic_full(self, *, fail_step=None, bad=None):
        """Run only the orchestrator, substituting fully offline subrun evidence."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'docs/hardware_reports').mkdir(parents=True)
            calls = []
            def subrun(argv, *, _h10_full_off_proof=None, _subrun_result=None):
                calls.append((tuple(argv), _h10_full_off_proof))
                n = len(calls)
                subgroup, expected = q.H10_FULL_GROUPS[n-1][2:]
                event_path = root / f'step_{n}.jsonl'
                events = self._events(subgroup, expected,
                                      bad=bad if fail_step == n else None)
                event_path.write_text(''.join(json.dumps(e) + '\n' for e in events))
                _subrun_result['evidence'] = event_path
                return 0
            bench = SimpleNamespace(load=SimpleNamespace(transport='serial'),
                                    sha256='bench_fingerprint')
            args = SimpleNamespace(command='run', execute=True,
                                   config=Path('config/bench.local.toml'))
            with patch.object(q, 'ROOT', root), patch.object(q, 'software_metadata',
                      return_value={'source_sha256': 'source_fingerprint'}), \
                    patch.object(q, 'main', side_effect=subrun), \
                    patch.object(q.sys, 'stdin') as stdin, \
                    patch('builtins.input', return_value=q.H10_FULL_TOKEN), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                stdin.isatty.return_value = True
                rc = q._h10_full_sequence(args, bench)
            summaries = list(root.glob('reports/hardware/*_H10_FULL/events.jsonl'))
            self.assertEqual(len(summaries), 1)
            records = [json.loads(line) for line in summaries[0].read_text().splitlines()]
            self.assertEqual(sum(e['event'] == 'outcome' for e in records), 1)
            self.assertTrue(list(root.glob('docs/hardware_reports/*_H10_FULL_qualification.md')))
            return rc, calls, records, output.getvalue()

    def test_full_sequence_runs_three_explicitly_gated_subruns_once(self):
        rc, calls, records, output = self._synthetic_full()
        self.assertEqual(rc, 0)
        self.assertEqual([call[0][2] for call in calls],
                         ['--off-only', '--powered', '--high-current'])
        self.assertIsNone(calls[0][1])
        self.assertIsNotNone(calls[1][1])
        self.assertEqual(calls[1][1], calls[2][1])
        self.assertTrue(all('--execute' in call[0] for call in calls))
        self.assertEqual(records[-1]['outcome'], 'PASS')
        self.assertEqual(len(records[-1]['completed']), 3)
        self.assertEqual(records[-1]['completed'][0]['outcome'], 'PARTIAL')
        self.assertIn('H10 FULL PASS', output)

    def test_unknown_regulation_in_saved_evidence_rejects_full_pass(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=3, bad='unknown_mode')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(records[-1]['outcome'], 'FAIL')

    def test_duplicate_sample_number_rejects_full_pass(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=2, bad='duplicate_sample')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)

    def test_missing_powered_verified_shutdown_rejects_full_pass(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=2, bad='missing_verified_shutdown')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)

    def test_incomplete_off_never_reaches_any_powered_step(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=1, bad='physical_off')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(records[-1]['outcome'], 'FAIL')

    def test_low_current_failure_forbids_high_current_step(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=2, bad='stage_error')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(records[-1]['completed']), 1)

    def test_absent_intermediate_output_off_blocks_full_pass(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=3, bad='missing_middle_off')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 3)
        self.assertIn('intermediate OFF', records[-1]['stop_reason'])

    def test_high_current_incomplete_point_never_passes_full(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=3, bad='missing_cc')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(records[-1]['outcome'], 'FAIL')
        self.assertEqual(len(records[-1]['completed']), 2)

    def test_source_drift_before_low_current_is_fail_closed(self):
        rc, calls, records, _ = self._synthetic_full(fail_step=2, bad='wrong_source')
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 2)
        self.assertIn('source/config drift', records[-1]['stop_reason'])

    def test_default_cli_preflight_and_dry_run_show_all_three_without_io(self):
        from qualification import EXAMPLE
        for command in ('preflight', 'run'):
            with self.subTest(command=command), \
                    patch.object(q, 'open_transport', side_effect=AssertionError('PSU opened')), \
                    patch.object(q, 'open_meter', side_effect=AssertionError('DMM opened')), \
                    patch.object(q, 'open_load', side_effect=AssertionError('load opened')), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                args = [command, 'H10', '--config', str(EXAMPLE)]
                if command == 'run': args.append('--dry-run')
                code = q.main(args)
            self.assertEqual(code, 0)
            card = output.getvalue()
            self.assertIn('H10 FULL PREVIEW: off', card)
            self.assertIn('H10 FULL PREVIEW: powered', card)
            self.assertIn('H10 FULL PREVIEW: high_current', card)
            self.assertIn('DRY RUN H10 FULL', card)

    def test_explicit_off_only_is_not_full_and_does_not_open_hardware(self):
        from qualification import EXAMPLE
        with patch.object(q, 'open_transport', side_effect=AssertionError('PSU opened')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(q.main(['preflight', 'H10', '--off-only',
                                     '--config', str(EXAMPLE)]), 0)
        card = output.getvalue()
        self.assertIn('только OFF-диагностика PARALLEL', card)
        self.assertNotIn('H10 FULL PREVIEW', card)

    def test_full_cli_runs_actual_stage_runners_on_fake_hardware(self):
        """Integration: real dispatch + OFF/low/high runners, no physical transports."""
        from qualification import EXAMPLE
        from test_driver import FakeTransport
        import h10_powered_stage
        class Source(FakeTransport):
            def __init__(self):
                super().__init__()
                self.load = None
            def write(self, command):
                super().write(command)
                if command in ('OUTP:PARALLEL 1', 'OUTP:PARALLEL 0'):
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.
            def actual_pair(self):
                if not self.flags['OUTP:STAT:ALL'] or self.load is None or self.load.state != 'ON':
                    return 0., 0.
                voltage = min(self.values[1]['VOLT'],
                              self.values[1]['CURR'] * self.load.resistance)
                return voltage, voltage / self.load.resistance
            def query(self, command):
                if command in ('MEAS:VOLT?', 'MEAS:CURR?') and self.active_ch in (1, 3):
                    self.cmds.append(command)
                    if self.active_ch == 3: return '0'
                    v, i = self.actual_pair()
                    return str(v if command == 'MEAS:VOLT?' else i)
                if command == 'STAT:OPER:INST:ISUM1:COND?':
                    self.cmds.append(command)
                    voltage, _ = self.actual_pair()
                    return ('10' if voltage + .001 < self.values[1]['VOLT'] else '9') \
                        if self.flags['OUTP:STAT:ALL'] else '0'
                return super().query(command)
        class Load:
            port = '/dev/ttyACM0'
            live = True
            def __init__(self):
                self.source, self.state, self.resistance, self.closed = None, 'OFF', None, False
            def input_state(self): return self.state
            def configure_cr(self, resistance):
                from equipment.kel103 import KEL103Serial
                if resistance not in KEL103Serial.VERIFIED_RESISTANCES:
                    raise ValueError('KEL103 CR not approved')
                self.resistance = resistance
                return resistance
            def set_input(self, state): self.state = 'ON' if state else 'OFF'
            def measurements(self):
                v, i = self.source.actual_pair()
                return {'kel103_voltage_v': v, 'kel103_current_a': i,
                        'kel103_power_w': v*i}
            def close(self): self.closed = True
        class Meter:
            live = True
            def __init__(self): self.source, self.closed = None, False
            def configure_voltage(self, range_v=10): pass
            def measure(self): return {'voltage_v': self.source.actual_pair()[0]}
            def close(self): self.closed = True
        rig = {'load': None, 'meter': None}
        sources = []
        def open_meter(host, evidence):
            rig['meter'] = Meter()
            return rig['meter']
        def open_load(bench, evidence):
            rig['load'] = Load()
            return rig['load']
        def open_transport(port):
            source = Source()
            source.load = rig['load']
            if rig['load']: rig['load'].source = source
            if rig['meter']: rig['meter'].source = source
            sources.append(source)
            return source
        def approve(prompt):
            return prompt.split('\n> ')[0].splitlines()[-1]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'docs/hardware_reports').mkdir(parents=True)
            conf = root / 'bench.local.toml'
            conf.write_bytes(EXAMPLE.read_bytes())
            with patch.object(q, 'ROOT', root), \
                    patch.object(q, 'verify_serial_adapter', return_value=True), \
                    patch.object(q, 'open_transport', side_effect=open_transport), \
                    patch.object(q, 'open_meter', side_effect=open_meter), \
                    patch.object(q, 'open_load', side_effect=open_load), \
                    patch.object(q.sys, 'stdin') as stdin, \
                    patch('builtins.input', side_effect=approve), \
                    patch.object(h10_powered_stage, 'operator_gate'), \
                    patch.object(h10_powered_stage.time, 'sleep'), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                stdin.isatty.return_value = True
                code = q.main(['run', 'H10', '--config', str(conf), '--execute'])
            self.assertEqual(code, 0, output.getvalue()[-2200:])
            self.assertEqual(len(sources), 3)
            self.assertTrue(all(source.closed for source in sources))
            self.assertNotIn('OUTP 1', sources[0].cmds)
            self.assertEqual(sources[1].cmds.count('OUTP 1'), 2)
            self.assertEqual(sources[2].cmds.count('OUTP 1'), 2)
            self.assertTrue(all(source.flags['OUTP:STAT:ALL'] == 0 for source in sources))
            self.assertTrue(all(source.flags['OUTP:PARALLEL'] == 0 for source in sources))
            self.assertIn('H10 FULL PASS', output.getvalue())

    def test_full_execute_noninteractive_stops_before_open_and_even_usb_discovery(self):
        from qualification import EXAMPLE
        with patch.object(q, 'open_transport', side_effect=AssertionError('PSU opened')), \
                patch.object(q, 'verify_serial_adapter',
                             side_effect=AssertionError('USB discovery')), \
                patch.object(q.sys, 'stdin') as stdin, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            stdin.isatty.return_value = False
            # Use a non-example path in a temporary copy for the live-argument check.
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / 'bench.local.toml'
                config.write_bytes(EXAMPLE.read_bytes())
                rc = q.main(['run', 'H10', '--config', str(config), '--execute'])
        self.assertEqual(rc, 2)
        self.assertIn('interactive terminal', output.getvalue())


if __name__ == '__main__':
    unittest.main()
