"""Regression: only Keithley 2231A identity may change PSU firmware evidence.

The instruments are simulated; no transport or physical hardware is opened.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from qualification import Evidence


PSU1 = 'Keithley instruments, 2231A-30-3, PSU_SECRET, 1.04-1.04'
PSU3 = 'Keithley instruments, 2231A-30-3, PSU_SECRET, 1.04-0.01'
TDS = 'TEKTRONIX,TDS 3064B,SCOPE_SECRET,CF:91.1CT FV:v3.41'
DMM = 'KEITHLEY INSTRUMENTS,MODEL DMM7510,DMM_SECRET,1.7.5'


class IdentityEvidenceTests(unittest.TestCase):
    def make_evidence(self, folder):
        return Evidence(Path(folder) / 'events.jsonl', 'H05')

    @staticmethod
    def saved(evidence):
        raw = evidence.path.read_text(encoding='utf-8')
        return [json.loads(line) for line in raw.splitlines()], raw

    def test_h05_scope_idn_does_not_create_false_psu_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.make_evidence(folder)
            evidence.add('read', command='*IDN?', response=PSU3)
            evidence.add('psu_identity', command='*IDN?', response=PSU3)
            evidence.add('scope_query', command='*IDN?', response=TDS)
            evidence.add('outcome', outcome='PASS')
            records, raw = self.saved(evidence)
        self.assertEqual(evidence.firmware, '1.04-0.01')
        self.assertEqual(records[-1]['firmware_observations'], ['1.04-0.01'])
        self.assertFalse(any(r['event'] == 'firmware_revision_drift' for r in records))
        self.assertIn('CF:91.1CT FV:v3.41', raw)  # Scope firmware retained in scope record.
        self.assertNotIn('PSU_SECRET', raw)
        self.assertNotIn('SCOPE_SECRET', raw)
        scope = next(r for r in records if r['event'] == 'scope_query')
        self.assertEqual(scope['response'],
                         'TEKTRONIX,TDS 3064B, [REDACTED],CF:91.1CT FV:v3.41')

    def test_real_channel_dependent_psu_change_is_still_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.make_evidence(folder)
            evidence.add('read', command='*IDN?', response=PSU1)
            evidence.add('scope_query', command='*IDN?', response=TDS)
            evidence.add('psu_identity', command='*IDN?', response=PSU3)
            evidence.add('query_outcome', outcome='PASS')
            records, raw = self.saved(evidence)
        drifts = [r for r in records if r['event'] == 'firmware_revision_drift']
        self.assertEqual([(r['before'], r['after']) for r in drifts],
                         [('1.04-1.04', '1.04-0.01')])
        self.assertEqual(records[-1]['firmware_observations'],
                         ['1.04-1.04', '1.04-0.01'])
        self.assertNotIn('PSU_SECRET', raw)
        self.assertNotIn('SCOPE_SECRET', raw)

    def test_meter_and_scope_revisions_do_not_overwrite_psu(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.make_evidence(folder)
            evidence.add('read', command='*IDN?', response=PSU1)
            evidence.add('dmm7510_read', command='*IDN?', response=DMM)
            evidence.add('scope_query', command='*IDN?', response=TDS)
            evidence.add('outcome', outcome='PASS')
            records, raw = self.saved(evidence)
        self.assertEqual(evidence.firmware, '1.04-1.04')
        self.assertEqual(records[-1]['firmware_observations'], ['1.04-1.04'])
        self.assertFalse(any(r['event'] == 'firmware_revision_drift' for r in records))
        self.assertNotIn('DMM_SECRET', raw)
        self.assertNotIn('SCOPE_SECRET', raw)

    def test_generic_read_with_wrong_device_is_not_accepted_as_psu_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.make_evidence(folder)
            evidence.add('read', command='*IDN?', response=TDS)
            evidence.add('read', command='*IDN?', response=DMM)
            evidence.add('read', command='*IDN?', response=PSU3)
            evidence.add('outcome', outcome='PASS')
            records, raw = self.saved(evidence)
        self.assertEqual(evidence.firmware, '1.04-0.01')
        self.assertEqual(records[-1]['firmware_observations'], ['1.04-0.01'])
        self.assertNotIn('SCOPE_SECRET', raw)
        self.assertNotIn('DMM_SECRET', raw)
        self.assertNotIn('PSU_SECRET', raw)

    def test_malformed_scope_identity_redacted_without_changing_firmware(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.make_evidence(folder)
            evidence.add('read', command='*IDN?', response=PSU3)
            evidence.add('scope_query', command='*IDN?', response='INCOMPLETE SECRET123')
            evidence.add('outcome', outcome='ABORTED')
            records, raw = self.saved(evidence)
        scope = next(r for r in records if r['event'] == 'scope_query')
        self.assertEqual(scope['response'], '[REDACTED MALFORMED IDN]')
        self.assertEqual(records[-1]['firmware_observations'], ['1.04-0.01'])
        self.assertNotIn('SECRET123', raw)


if __name__ == '__main__':
    unittest.main()
