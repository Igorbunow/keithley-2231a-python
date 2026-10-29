"""O02-A: in-memory dual session, strict fault containment and evidence (no hardware)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from experiments.o02_dual_psu.dual_offline import (SimIdentity, SimStep, SimulatedSource,
                                       DualOfflineSimulation, simulate_pair,
                                       audit_dual_simulation_evidence)
from experiments.o02_dual_psu.dual_offline import SimulationSafetyError as SafetyError


class O02AOfflineTests(unittest.TestCase):
    def _run(self, sim=None, *, a=None, b=None, steps=None, path=None):
        sim = sim or simulate_pair()
        if path is not None:
            result = sim.run(approval_a=sim.approval_token('A') if a is None else a,
                             approval_b=sim.approval_token('B') if b is None else b,
                             steps=steps or (SimStep('A', .5, .04), SimStep('B', .75, .05)),
                             evidence_path=path)
            return sim, result
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'evidence.jsonl'
            result = sim.run(approval_a=sim.approval_token('A') if a is None else a,
                             approval_b=sim.approval_token('B') if b is None else b,
                             steps=steps or (SimStep('A', .5, .04), SimStep('B', .75, .05)),
                             evidence_path=path)
            rows = [json.loads(s) for s in path.read_text().splitlines()]
            return sim, result, rows

    def test_nominal_exactly_one_on_per_source_and_both_verified_off(self):
        sim, result, rows = self._run()
        self.assertEqual(result, 'PASS')
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})
        self.assertEqual([e['source'] for e in rows if e['event'] == 'enabled'], ['A', 'B'])
        self.assertEqual([e['source'] for e in rows if e['event'] == 'final_readback'], ['B', 'A'])
        self.assertEqual(rows[-1]['outcome'], 'PASS')
        self.assertTrue(all(e['simulation_only'] for e in rows))
        self.assertEqual([r['seq'] for r in rows[:-1]], list(range(len(rows)-1)))
        self.assertEqual([s.commands.count('ENABLE') for s in sim.sources.values()], [1, 1])
        self.assertEqual([s.commands.count('DISABLE') for s in sim.sources.values()], [1, 1])
        self.assertFalse(any(s.simulated_physical_on for s in sim.sources.values()))
        self.assertLess([r['event'] for r in rows].index('two_distinct_approvals'),
                        [r['event'] for r in rows].index('enabled'))

    def test_pair_fault_requires_explicit_target(self):
        with self.assertRaises(ValueError):
            simulate_pair(fault='off_lost')
        with self.assertRaises(ValueError):
            simulate_pair(fault_on='A')

    def test_no_electrical_or_real_transport_api_acceptance(self):
        class Malicious(SimulatedSource):
            def enable(self, *args):
                raise AssertionError('should never dispatch to override')
        sim = simulate_pair()
        with self.assertRaises(TypeError):
            DualOfflineSimulation(sim.expected['A'], sim.expected['B'],
                                  Malicious(sim.expected['A']), sim.sources['B'])
        with self.assertRaises(TypeError):
            DualOfflineSimulation(sim.expected['A'], sim.expected['B'], object(), sim.sources['B'])
        with self.assertRaises(ValueError):
            SimIdentity('A', 'REAL-PSU', '/dev/ttyUSB1')
        with self.assertRaises(ValueError):
            SimIdentity('B', 'REAL-PSU', '192.168.22.100:5025')

    def test_duplicate_serial_and_endpoints_rejected_before_any_sim_command(self):
        a = SimIdentity('A', 'SIM-SAME', 'sim://a')
        b = SimIdentity('B', 'SIM-SAME', 'sim://b')
        sa, sb = SimulatedSource(a), SimulatedSource(b)
        with self.assertRaises(SafetyError):
            DualOfflineSimulation(a, b, sa, sb)
        self.assertEqual(sa.commands + sb.commands, [])
        b = SimIdentity('B', 'SIM-OTHER', 'sim://a')
        with self.assertRaises(SafetyError):
            DualOfflineSimulation(a, b, sa, SimulatedSource(b))
        self.assertEqual(sa.commands, [])

    def test_swapped_sources_abort_without_enabling_anything(self):
        sim = simulate_pair()
        swap = DualOfflineSimulation(sim.expected['A'], sim.expected['B'],
                                     sim.sources['B'], sim.sources['A'])
        _, result, rows = self._run(swap)
        self.assertEqual(result, 'ABORTED')
        self.assertNotIn('ENABLE', sim.sources['A'].commands + sim.sources['B'].commands)
        self.assertEqual(swap.final_states, {'A': 'UNKNOWN', 'B': 'UNKNOWN'})
        self.assertEqual(rows[-1]['outcome'], 'ABORTED')

    def test_wrong_identity_fails_closed(self):
        sim, result, rows = self._run(simulate_pair(fault_on='A', fault='identify_wrong'))
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(sim.sources['A'].commands, ['IDENTIFY'])
        self.assertNotIn('ENABLE', sim.sources['B'].commands)
        self.assertIn('identity_mismatch', [r['event'] for r in rows])

    def test_identity_link_loss_does_not_retry(self):
        sim, result, _ = self._run(simulate_pair(fault_on='A', fault='identify_lost'))
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(sim.sources['A'].commands, ['IDENTIFY'])
        self.assertTrue(sim.sources['A'].uncertain)

    def test_refusing_each_identity_bound_approval_prevents_both_on(self):
        for bad in ('A', 'B'):
            sim = simulate_pair()
            _, result, rows = self._run(sim, **{bad.lower(): 'CONFIRM SIM'})
            self.assertEqual(result, 'ABORTED')
            self.assertEqual([e for e in rows if e['event'] == 'enabled'], [])
            self.assertFalse(any('ENABLE' in s.commands for s in sim.sources.values()))
            self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})

    def test_initial_on_aborts_but_attempts_off_on_both(self):
        for alias in ('A', 'B'):
            sim, result, rows = self._run(simulate_pair(initial_on=alias))
            self.assertEqual(result, 'ABORTED')
            self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})
            self.assertNotIn('ENABLE', sim.sources[alias].commands)
            self.assertIn('initially ON', sim.reason)

    def test_preflight_link_failure_never_writes_on(self):
        sim, result, _ = self._run(simulate_pair(fault_on='B', fault='preflight_lost'))
        self.assertEqual(result, 'ABORTED')
        self.assertNotIn('ENABLE', sim.sources['A'].commands + sim.sources['B'].commands)
        self.assertEqual(sim.final_states['B'], 'UNKNOWN')
        self.assertEqual(sim.final_states['A'], 'OFF')

    def test_lost_ack_after_first_on_no_retry_and_other_attempts_off(self):
        sim, result, rows = self._run(simulate_pair(fault_on='A', fault='enable_lost_after_on'))
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(sim.sources['A'].simulated_physical_on)
        self.assertEqual(sim.sources['A'].commands.count('ENABLE'), 1)
        self.assertNotIn('DISABLE', sim.sources['A'].commands)
        self.assertNotIn('ENABLE', sim.sources['B'].commands)
        self.assertIn('DISABLE', sim.sources['B'].commands)
        self.assertEqual(sim.final_states, {'A': 'UNKNOWN', 'B': 'OFF'})
        self.assertIn('shutdown_skipped_unknown', [e['event'] for e in rows])

    def test_lost_ack_after_second_on_other_session_still_off(self):
        sim, result, rows = self._run(simulate_pair(fault_on='B', fault='enable_lost_after_on'))
        self.assertEqual(result, 'ABORTED')
        self.assertTrue(sim.sources['B'].simulated_physical_on)
        self.assertFalse(sim.sources['A'].simulated_physical_on)
        self.assertEqual(sim.sources['A'].commands.count('DISABLE'), 1)
        self.assertNotIn('DISABLE', sim.sources['B'].commands)
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'UNKNOWN'})

    def test_on_readback_stale_and_no_second_source_on(self):
        sim, result, _ = self._run(simulate_pair(fault_on='A', fault='enable_verify_stale_off'))
        self.assertEqual(result, 'ABORTED')
        self.assertNotIn('ENABLE', sim.sources['B'].commands)
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})

    def test_stuck_off_ack_blocks_second_on(self):
        sim, result, _ = self._run(simulate_pair(fault_on='A', fault='enable_stuck_off'))
        self.assertEqual(result, 'ABORTED')
        self.assertNotIn('ENABLE', sim.sources['B'].commands)
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})

    def test_shutdown_link_loss_on_each_but_other_still_off(self):
        for alias in ('A', 'B'):
            sim, result, rows = self._run(simulate_pair(fault_on=alias, fault='off_lost'))
            other = 'B' if alias == 'A' else 'A'
            self.assertEqual(result, 'ABORTED')
            self.assertEqual(sim.final_states[alias], 'UNKNOWN')
            self.assertEqual(sim.final_states[other], 'OFF')
            self.assertTrue(sim.sources[alias].simulated_physical_on)
            self.assertEqual(sim.sources[alias].commands.count('DISABLE'), 1)
            self.assertEqual(sim.sources[other].commands.count('DISABLE'), 1)
            self.assertNotIn('PASS', [e.get('outcome') for e in rows])

    def test_stuck_on_off_readback_does_not_say_pass(self):
        sim, result, rows = self._run(simulate_pair(fault_on='B', fault='off_stuck_on'))
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'ON'})
        self.assertEqual(sim.sources['B'].commands.count('DISABLE'), 1)
        self.assertEqual(rows[-1]['outcome'], 'ABORTED')

    def test_off_ack_lost_with_actual_off_still_unknown(self):
        sim, result, _ = self._run(simulate_pair(fault_on='A', fault='off_verify_lost'))
        self.assertEqual(result, 'ABORTED')
        self.assertFalse(sim.sources['A'].simulated_physical_on)
        self.assertEqual(sim.final_states['A'], 'UNKNOWN')
        self.assertEqual(sim.final_states['B'], 'OFF')

    def test_failed_evidence_path_cannot_report_pass_after_off(self):
        sim = simulate_pair()
        with tempfile.TemporaryDirectory() as folder:
            # Existing directory is not a writable JSONL file.
            out = Path(folder) / 'already_dir'
            out.mkdir()
            sim, result = self._run(sim, path=out)
            self.assertEqual(result, 'ABORTED')
            self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})
            self.assertFalse(any(s.simulated_physical_on for s in sim.sources.values()))
            self.assertIn('Evidence persistence failed', sim.reason)

    def test_existing_evidence_is_not_overwritten(self):
        sim = simulate_pair()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'existing.jsonl'
            out.write_text('DO NOT REPLACE')
            _, result = self._run(sim, path=out)
            self.assertEqual(result, 'ABORTED')
            self.assertEqual(out.read_text(), 'DO NOT REPLACE')
            self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})

    def test_one_shot_no_approval_reuse(self):
        sim, result, _ = self._run()
        self.assertEqual(result, 'PASS')
        old = [list(s.commands) for s in sim.sources.values()]
        with self.assertRaises(SafetyError):
            self._run(sim)
        self.assertEqual(old, [s.commands for s in sim.sources.values()])

    def test_invalid_step_caps_order_nan_bool_no_operations(self):
        bad = ((SimStep('A', 5., .04), SimStep('B', .75, .05)),
               (SimStep('B', .75, .05), SimStep('A', .5, .04)),
               (SimStep('A', True, .04), SimStep('B', .75, .05)),
               (SimStep('A', float('nan'), .04), SimStep('B', .75, .05)),
               (SimStep('A', .5, .0405), SimStep('B', .75, .05)),
               (SimStep('A', .5, .04), SimStep('A', .75, .05)))
        for steps in bad:
            with self.subTest(steps=steps):
                sim = simulate_pair()
                with self.assertRaises(ValueError):
                    self._run(sim, steps=steps)
                self.assertEqual(sim.sources['A'].commands + sim.sources['B'].commands, [])

    def test_independent_jsonl_replay_rejects_tampering(self):
        sim = simulate_pair()
        with tempfile.TemporaryDirectory() as folder:
            evidence = Path(folder) / 'original.jsonl'
            _, outcome = self._run(sim, path=evidence)
            self.assertEqual(outcome, 'PASS')
            self.assertEqual(audit_dual_simulation_evidence(evidence, expected_outcome='PASS'), 'PASS')
            rows = [json.loads(x) for x in evidence.read_text().splitlines()]
            changes = (
                lambda r: r[-1]['final_states'].update(B='UNKNOWN'),
                lambda r: r[-1].update(simulation_only=False),
                lambda r: r[0].update(seq=4),
                lambda r: r[3].update(monotonic_s=-5),
                lambda r: next(x for x in r if x.get('event')=='identity_ok' and x.get('source')=='A').update(serial='real-device'),
                lambda r: next(x for x in r if x.get('event')=='enabled' and x.get('source')=='B').update(source='A'),
                lambda r: next(x for x in r if x.get('event')=='final_readback').update(state='ON'),
            )
            for mutate in changes:
                changed = json.loads(json.dumps(rows))
                mutate(changed)
                tampered = Path(folder) / 'tamper.jsonl'
                tampered.write_text('\n'.join(json.dumps(row) for row in changed) + '\n')
                with self.assertRaises(SafetyError):
                    audit_dual_simulation_evidence(tampered)

    def test_simulated_clock_regression_is_aborted_without_failure_to_off(self):
        from unittest.mock import patch
        sim = simulate_pair()
        n = [400.0]
        def fake_clock():
            n[0] -= 0.25
            return n[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'regressed.jsonl'
            with patch('experiments.o02_dual_psu.dual_offline.time.monotonic', side_effect=fake_clock):
                sim, result = self._run(sim, path=path)
            # Regressed evidence is rejected BEFORE it is published.
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(folder).glob('.o02-a-*')), [])
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(sim.final_states, {'A': 'OFF', 'B': 'OFF'})
        self.assertFalse(any(s.simulated_physical_on for s in sim.sources.values()))
        self.assertIn('clock regressed', sim.reason)

    def test_aborted_outcome_independently_replayed(self):
        sim = simulate_pair(fault_on='B', fault='off_stuck_on')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'aborted.jsonl'
            _, result = self._run(sim, path=path)
            self.assertEqual(result, 'ABORTED')
            self.assertEqual(audit_dual_simulation_evidence(path, expected_outcome='ABORTED'), 'ABORTED')
            with self.assertRaises(SafetyError):
                audit_dual_simulation_evidence(path, expected_outcome='PASS')

    def test_cli_nominal_faults_and_reject_execute_flag(self):
        root = Path(__file__).resolve().parents[2]
        cli = root / 'tools' / 'o02_offline.py'
        with tempfile.TemporaryDirectory() as folder:
            okay = Path(folder) / 'nominal.jsonl'
            cmd = [sys.executable, str(cli), '--evidence', str(okay)]
            good = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr + good.stdout)
            self.assertIn('SIMULATION ONLY: PASS', good.stdout)
            self.assertEqual(json.loads(okay.read_text().splitlines()[-1])['outcome'], 'PASS')
            bad = subprocess.run([*cmd, '--execute'], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('unrecognized arguments', bad.stderr)
            lost = Path(folder) / 'fault.jsonl'
            bad = subprocess.run([sys.executable, str(cli), '--evidence', str(lost),
                                  '--fault-source', 'B', '--fault', 'enable_lost_after_on'],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn('SIMULATION ONLY: ABORTED', bad.stdout)
            self.assertEqual(json.loads(lost.read_text().splitlines()[-1])['final_states'],
                             {'A': 'OFF', 'B': 'UNKNOWN'})


if __name__ == '__main__':
    unittest.main()
