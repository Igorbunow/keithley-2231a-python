"""Offline-only R4.4-C evidence integrity and privacy regressions."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
sys.path.insert(0, str(ROOT/'tools'))
from qualification import Evidence
from audit_r4_4_scalar_evidence import (AuditError, audit, load_events,
                                        main, numeric, unique_keys)


class ScalarEvidenceAuditOffline(unittest.TestCase):
    def test_psu_identity_is_redacted_by_real_evidence_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            evidence = Evidence(path, 'R44SCALAR')
            evidence.add('psu_identity', command='*IDN?',
                         response='Keithley instruments, 2231A-30-3, SYNTHETIC-SECRET, 1.04-1.04')
            stored = path.read_text()
            self.assertNotIn('SYNTHETIC-SECRET', stored)
            value = json.loads(stored)['response']
            self.assertIn('[REDACTED]', value)
            self.assertIn('1.04-1.04', value)

    def test_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('{"event":"start", "event":"outcome"}\n')
            with self.assertRaisesRegex(AuditError, 'duplicate JSON key'):
                load_events(path)

    def test_invalid_nonfinite_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('{"event":"start", "monotonic_s":NaN}\n')
            with self.assertRaises(AuditError):
                load_events(path)

    def test_hash_mismatch_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            path.write_text('{"event":"start"}\n')
            with contextlib.redirect_stderr(io.StringIO()):
                code = main([str(path), '--expected-sha256', '0'*64])
            self.assertEqual(code, 2)

    def test_partial_evidence_cannot_pass(self):
        with self.assertRaises(AuditError):
            audit([{'event':'start','test_id':'R44SCALAR',
                    'utc':'2026-10-29T00:00:00+00:00', 'monotonic_s':1.0}])

    def test_numeric_guards_reject_bool_nan_overlimit(self):
        for value in (True, float('nan'), float('inf'), -1, .61, '0.2'):
            with self.subTest(value=value):
                with self.assertRaises(AuditError):
                    numeric(value, 'power', .60)


if __name__ == '__main__':
    unittest.main()
