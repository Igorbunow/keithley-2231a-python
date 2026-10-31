"""R5-D3: evidence index is bounded, complete and cannot authorize SCPI."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from keithley2231a import get_capabilities

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from audit_r5_d3_step_matrix import verify_matrix


class R5D3MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / 'docs/reviews/R5_D3_STEP_HW_EVIDENCE_INDEX_2026-10-31.json').read_text())

    def test_complete_six_case_index(self):
        cases = self.manifest['cases']
        self.assertEqual(len(cases), 6)
        self.assertEqual({(c['channel'], c['mode']) for c in cases},
                         {(ch, m) for ch in (1,2,3) for m in ('CV','CC')})
        self.assertTrue(all(c['events'] == 7344 and c['snapshots'] == 5 and
                            c['mutations'] == 4 and c['scpi_queries'] == 1634
                            for c in cases))

    def test_digest_and_provenance_format(self):
        self.assertTrue(self.manifest['software']['dirty'])
        for c in self.manifest['cases']:
            self.assertEqual(len(c['original_sha256']), 64)
            self.assertEqual(set(c['original_sha256']) - set('0123456789abcdef'), set())
            self.assertEqual(c['scpi_nonzero_error_count'], 0)
            self.assertTrue(c['digital_off_confirmed'] and c['physical_off_confirmed'])
            self.assertEqual(c['outcome'], 'PASS')
            self.assertEqual(c['load_cr_ohm'], 40.0 if c['mode']=='CV' else 11.0)
            self.assertEqual(c['step'], .010 if c['mode']=='CV' else .005)
            self.assertEqual(len(c['dmm_voltage_v']), 5)
            self.assertEqual(len(c['kel_current_a']), 5)
            self.assertLess(c['kel_power_max_w'], .60)

    def test_powered_caps_are_all_channel_but_still_bounded(self):
        f = get_capabilities().feature('source_step_powered_qualification')
        self.assertEqual(f.evidence, 'bounded_hw')
        triples = {(e.channels, e.operation, e.regulation) for e in f.hardware_evidence}
        self.assertIn(((1,2,3), 'voltage_step_up_down', 'CV'), triples)
        self.assertIn(((1,2,3), 'current_step_up_down', 'CC'), triples)
        self.assertTrue(all(e.output_state == 'POWERED' for e in f.hardware_evidence))
        self.assertTrue(any('not general' in s.lower() or 'not qualify general' in s.lower()
                            for s in f.constraints))
        for name in ('hardware_triggered_setpoint','trigger_channel_coupling',
                     'bulk_apply_setpoints','enable_channels_function'):
            self.assertFalse(get_capabilities().feature(name).driver_exposed)

    def test_off_voltage_scope_not_confused_with_powered(self):
        for name in ('source_step_query','source_step_off_configuration',
                     'source_step_off_mutation'):
            f = get_capabilities().feature(name)
            self.assertTrue(any(e.channels == (1,2,3) and
                                e.operation.startswith('voltage_step')
                                for e in f.hardware_evidence))
            self.assertTrue(all(e.output_state == 'OFF' for e in f.hardware_evidence))

    def test_metadata_does_not_claim_calibration_or_new_permission(self):
        self.assertFalse(self.manifest['qualification_scope']['metrology_calibrated'])
        self.assertFalse(self.manifest['qualification_scope']['analog_synchronized'])
        self.assertFalse(self.manifest['qualification_scope']['powered_general_api_qualified'])
        self.assertFalse(get_capabilities().feature('safe_off_on_link_loss').instrument_native)

    def test_matrix_checker_rejects_missing_and_duplicated_case(self):
        with self.assertRaisesRegex(ValueError, 'exactly six'):
            verify_matrix(self.manifest, [])
        bad = deepcopy(self.manifest)
        bad['cases'][0]['channel'] = bad['cases'][1]['channel']
        with self.assertRaisesRegex(ValueError, 'Duplicate or absent'):
            verify_matrix(bad, [Path('missing')] * 6)

    def test_matrix_checker_requires_actual_original_files(self):
        with patch('audit_r5_d3_step_matrix.read_and_audit', side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                verify_matrix(self.manifest, [Path('nonexistent')]*6)


if __name__ == '__main__':
    unittest.main()
