"""Adversarial tests for audit of the physical 1mV rejection fixture.

Fixture records are actual source evidence when available to build the tests,
not checked into the repository: tests do not depend on the operator's journal.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('r45_rejection_audit', ROOT/'tools/audit_r4_5_step_rejection.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def fixture():
    def event(name, **fields):
        return {'event':name,'test_id':'R45STEPMUT', 'monotonic_s':0.0,
                'utc':'2026-10-30T00:00:00+00:00', **fields}
    return event


class AuditorNegativeTests(unittest.TestCase):
    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.unique_keys([('event','write'),('event','query')])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            audit.audit_file('/this/not/existing/audit_file.jsonl')

    def test_empty_and_wrong_stage_rejected(self):
        with self.assertRaises(audit.AuditError): audit.audit([])
        with self.assertRaises(audit.AuditError):
            audit.audit([{'event':'outcome','test_id':'X'}]*101)

    def test_invalid_monotonic_rejected_before_looking_for_gates(self):
        x=fixture()
        rows=[x('query',command='*IDN?') for _ in range(101)]
        with self.assertRaisesRegex(audit.AuditError, 'monotonic'):
            audit.audit(rows)

    def test_unknown_nonfinite_timestamp_rejected(self):
        x=fixture()
        rows=[x('query',command='*IDN?') for _ in range(101)]
        rows[2]['monotonic_s']=float('nan')
        with self.assertRaisesRegex(audit.AuditError, 'monotonic'):
            audit.audit(rows)

    def test_wrong_utc_rejected_even_when_monotonic_ok(self):
        x=fixture()
        rows=[x('query',command='*IDN?') for _ in range(101)]
        for i,r in enumerate(rows):r['monotonic_s']=float(i)
        rows[60]['utc']='not-a-timestamp'
        with self.assertRaisesRegex(audit.AuditError, 'UTC'):
            audit.audit(rows)

    def test_missing_case_gate_rejected(self):
        x=fixture()
        rows=[x('query',command='*IDN?') for _ in range(101)]
        for i,r in enumerate(rows):r['monotonic_s']=float(i)
        with self.assertRaisesRegex(audit.AuditError, 'exactly one start'):
            audit.audit(rows)


if __name__ == '__main__': unittest.main()
