"""Fault injection for the shared powered H09/H10 shutdown boundary (offline)."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ShutdownEvidence, finish_combined_powered
from qualification import Evidence


class Source:
    def __init__(self, *, fail_off=False, false_off=False, live=True):
        self.calls = []
        self.output_on = True
        self.fail_off = fail_off
        self.false_off = false_off
        self.link = SimpleNamespace(live=live, cleanup=False)
        self.closed = False

    def emergency_off(self):
        self.calls.append('emergency_off')
        if self.fail_off:
            raise OSError('PSU OFF command failed')
        self.output_on = False

    def output_states(self):
        self.calls.append('output_states')
        return {1: self.output_on or self.false_off, 2: False, 3: False}

    def diagnostic_write(self, command):
        self.calls.append(command)

    def close(self):
        self.closed = True


class Load:
    def __init__(self, evidence, *, fail_off=False, false_off=False, live=True):
        self.evidence = evidence
        self.live = live
        self.state = 'ON'
        self.fail_off = fail_off
        self.false_off = false_off
        self.calls = []
        self.closed = False

    def input_state(self):
        self.evidence.add('kel103_query', command=':INP?')
        self.calls.append('input_state')
        return 'ON' if self.false_off else self.state

    def set_input(self, enabled):
        self.evidence.add('kel103_write', command=':INP OFF')
        self.calls.append('set_input')
        if self.fail_off:
            raise OSError('KEL OFF command failed')
        self.state = 'ON' if enabled else 'OFF'

    def close(self):
        self.closed = True


class Meter:
    def __init__(self): self.closed = False
    def close(self): self.closed = True


class FailEvidence(Evidence):
    broken = False
    def add(self, event, **fields):
        if self.broken:
            raise OSError('evidence disk full')
        return super().add(event, **fields)


class PoweredCombinedShutdownTests(unittest.TestCase):
    def run_case(self, *, fail_source=False, false_source=False, live_source=True,
                 fail_load=False, false_load=False, live_load=True,
                 broken=False, fail_final_record=False):
        with tempfile.TemporaryDirectory() as temp:
            raw = FailEvidence(Path(temp) / 'events.jsonl', 'H09')
            evidence = ShutdownEvidence(raw)
            source = Source(fail_off=fail_source, false_off=false_source,
                            live=live_source)
            load = Load(raw, fail_off=fail_load, false_off=false_load,
                        live=live_load)
            meter = Meter()
            raw.broken = broken
            with contextlib.redirect_stdout(io.StringIO()):
                if fail_final_record:
                    original = raw.add
                    def break_only_outcome(event, **fields):
                        if event == 'query_outcome':
                            raise OSError('cannot record final outcome')
                        return original(event, **fields)
                    with patch.object(raw, 'add', side_effect=break_only_outcome):
                        result = finish_combined_powered(
                            source, source.link, load, meter, source, evidence,
                            outcome='PASS', reason='samples validated', scope='test')
                else:
                    result = finish_combined_powered(
                        source, source.link, load, meter, source, evidence,
                        outcome='PASS', reason='samples validated', scope='test')
            records = raw.path.read_text(encoding='utf-8') if not broken else ''
            return result, source, load, meter, evidence, records

    def test_success_requires_independent_off_and_persists_pass(self):
        result, src, load, meter, evidence, records = self.run_case()
        self.assertEqual(result, 'PASS')
        self.assertEqual(src.calls.count('emergency_off'), 1)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(src.closed and load.closed and meter.closed)
        self.assertIn('"event": "query_outcome"', records)
        self.assertFalse(evidence.lost)

    def test_psu_shutdown_failure_does_not_skip_kel(self):
        result, src, load, meter, _, _ = self.run_case(fail_source=True)
        self.assertEqual(result, 'ABORTED')
        self.assertIn('emergency_off', src.calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(src.closed and load.closed and meter.closed)

    def test_load_shutdown_failure_does_not_skip_psu(self):
        result, src, load, meter, _, _ = self.run_case(fail_load=True)
        self.assertEqual(result, 'ABORTED')
        self.assertFalse(src.output_on)
        self.assertIn('set_input', load.calls)
        self.assertTrue(src.closed and load.closed and meter.closed)

    def test_live_readback_disagreement_blocks_pass(self):
        for kw in ({'false_source': True}, {'false_load': True}):
            with self.subTest(kw=kw):
                result, src, load, meter, _, _ = self.run_case(**kw)
                self.assertEqual(result, 'ABORTED')
                self.assertTrue(src.closed and load.closed and meter.closed)

    def test_uncertain_psu_does_not_receive_commands_but_kel_stops(self):
        result, src, load, _, _, _ = self.run_case(live_source=False)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(src.calls, [])
        self.assertEqual(load.state, 'OFF')

    def test_uncertain_kel_does_not_receive_commands_but_psu_stops(self):
        result, src, load, _, _, _ = self.run_case(live_load=False)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(load.calls, [])
        self.assertFalse(src.output_on)

    def test_disk_full_during_powered_run_must_not_block_usb_off(self):
        result, src, load, meter, evidence, _ = self.run_case(broken=True)
        self.assertEqual(result, 'ABORTED')
        self.assertFalse(src.output_on)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(src.closed and load.closed and meter.closed)
        self.assertTrue(evidence.lost)

    def test_final_outcome_record_failure_cannot_report_pass(self):
        result, src, load, _, evidence, _ = self.run_case(fail_final_record=True)
        self.assertEqual(result, 'ABORTED')
        self.assertFalse(src.output_on)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(evidence.lost)


if __name__ == '__main__':
    unittest.main()
