"""H09/H10 OFF-only fault injection; no physical equipment is opened."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from support.qualification_transport import FakeTransport
from qualification import Evidence
from combined_off_stage import run_combined_off


class CombinedOffFailClosedTests(unittest.TestCase):
    def run_case(self, mode='SER', source=None, fault=None):
        source = source or FakeTransport()
        test_id = 'H09' if mode == 'SER' else 'H10'
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Evidence(Path(tmp) / 'events.jsonl', test_id)
            original = evidence.add
            status = {'broken': False}

            def injected_add(event, **fields):
                if fault == 'during_combined' and (event == 'read' and
                    fields.get('command') == f'OUTP:{mode}?' and
                    source.flags[f'OUTP:{mode}']):
                    status['broken'] = True
                if fault == 'stage_error' and event == 'stage_error':
                    raise OSError('simulated log write failure at error event')
                if fault == 'outcome' and event == 'query_outcome':
                    raise OSError('simulated final report write failure')
                if fault == 'during_combined' and status['broken']:
                    raise OSError('evidence filesystem permanently unavailable')
                return original(event, **fields)

            if fault:
                evidence.add = injected_add
            with contextlib.redirect_stdout(io.StringIO()):
                result = run_combined_off(source, evidence, test_id)
            events = [json.loads(row) for row in evidence.path.read_text().splitlines()]
            return result, source, events

    def test_success_is_only_partial_both_modes(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                result, raw, events = self.run_case(mode)
                self.assertEqual(result, 'PARTIAL')
                self.assertEqual(raw.flags[f'OUTP:{mode}'], 0)
                self.assertFalse(any(raw.values[ch]['CHAN:OUTP'] for ch in (1,2,3)))
                self.assertTrue(all(raw.values[ch]['OUTP:TIM:STAT'] == 0 for ch in (1,2,3)))
                self.assertNotIn('OUTP 1', raw.cmds)
                self.assertNotIn('CHAN:OUTP 1', raw.cmds)
                self.assertEqual(events[-1]['event'], 'query_outcome')
                self.assertEqual(events[-1]['outcome'], 'PARTIAL')
                self.assertTrue(raw.closed)

    def test_filesystem_fails_after_mode_activation_but_off_still_sent(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                result, raw, events = self.run_case(mode, fault='during_combined')
                self.assertEqual(result, 'ABORTED')
                on_index = raw.cmds.index(f'OUTP:{mode} 1')
                after = raw.cmds[on_index + 1:]
                self.assertIn('OUTP 0', after,
                              'Evidence storage failure must not suppress global OFF')
                self.assertIn(f'OUTP:{mode} 0', after)
                self.assertEqual(after.count('OUTP:TIM:STAT 0'), 3)
                self.assertEqual(raw.flags[f'OUTP:{mode}'], 0)
                self.assertTrue(all(raw.values[ch]['OUTP:TIM:STAT'] == 0 for ch in (1,2,3)))
                self.assertTrue(raw.closed)
                self.assertNotIn('OUTP 1', raw.cmds)

    def test_final_outcome_evidence_failure_never_claims_partial(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                result, raw, events = self.run_case(mode, fault='outcome')
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(raw.flags[f'OUTP:{mode}'], 0)
                self.assertTrue(raw.closed)
                self.assertNotIn('query_outcome', [e['event'] for e in events])

    def test_stage_error_evidence_failure_never_skips_global_off(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                class FailureSource(FakeTransport):
                    def query(self, command):
                        if command == f'OUTP:{mode}?' and self.flags[f'OUTP:{mode}']:
                            self.cmds.append(command)
                            return '0'  # Valid but wrong mode flag; link stays trusted.
                        return super().query(command)
                raw = FailureSource()
                result, raw, _ = self.run_case(mode, raw, 'stage_error')
                self.assertEqual(result, 'ABORTED')
                idx = raw.cmds.index(f'OUTP:{mode} 1')
                self.assertIn('OUTP 0', raw.cmds[idx+1:])
                self.assertEqual(raw.flags[f'OUTP:{mode}'], 0)
                self.assertTrue(raw.closed)

    def test_stuck_timer_on_final_readback_is_not_partial(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                class TimerSource(FakeTransport):
                    timer_queries = 0
                    def query(self, command):
                        if command == 'OUTP:TIM:STAT?':
                            self.timer_queries += 1
                            if self.timer_queries == 19:
                                self.cmds.append(command)
                                return '1'  # Final explicit timer check after baseline, apply and 4 snapshots.
                        return super().query(command)
                raw = TimerSource()
                result, raw, events = self.run_case(mode, raw)
                self.assertEqual(result, 'FAIL')
                self.assertEqual(events[-1]['outcome'], 'FAIL')
                self.assertEqual(raw.flags[f'OUTP:{mode}'], 0)
                self.assertTrue(all(raw.values[ch]['OUTP:TIM:STAT'] == 0 for ch in (1,2,3)))
                self.assertTrue(raw.closed)

    def test_false_global_off_readback_aborts_without_mode_change(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                class FalseOffSource(FakeTransport):
                    def query(self, command):
                        if command == 'OUTP:STAT:ALL?' and self.flags[f'OUTP:{mode}']:
                            self.cmds.append(command)
                            return '1'  # Do not trust OUTP 0 without readback.
                        if command == f'OUTP:{mode}?' and self.flags[f'OUTP:{mode}']:
                            self.cmds.append(command)
                            return '0'  # Induce a recoverable stage error.
                        return super().query(command)
                raw = FalseOffSource()
                result, raw, events = self.run_case(mode, raw)
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(events[-1]['outcome'], 'ABORTED')
                self.assertTrue(raw.closed)

    def test_uncertain_link_has_no_follow_up_commands(self):
        for mode in ('SER', 'PARALLEL'):
            with self.subTest(mode=mode):
                class LostSource(FakeTransport):
                    failed = False
                    def query(self, command):
                        if (not self.failed and command == 'VOLT?' and
                                self.flags[f'OUTP:{mode}']):
                            self.failed = True
                            self.cmds.append(command)
                            raise OSError('lost UART while in combined mode')
                        return super().query(command)
                raw = LostSource()
                result, raw, events = self.run_case(mode, raw)
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(raw.cmds[-1], 'VOLT?')
                self.assertNotIn('OUTP 0', raw.cmds[raw.cmds.index(f'OUTP:{mode} 1')+1:])
                self.assertTrue(raw.closed)
