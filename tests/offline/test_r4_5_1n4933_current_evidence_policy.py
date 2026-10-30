"""Cross-method diagnostic policy: data integrity, safety/metrology separation."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from audit_r4_5_powered_step_evidence import AuditError
import r4_5_1n4933_current_evidence_policy as policy


def fixtures():
    f = {'forward':'bdb2859180a8aaadf13a0d6e76e46111dbdb0d2b606977b2dbc016e7a87fd56b',
         'ballast_reversed':'de6138efceaa41260acb6289de6790805d2978ccb40d305f60b74b988583b5db',
         'ballast_positive':'7faf60f03ce660027261ee98ba4cc4e8352fb67c95b18354e77e811216af7a2b',
         'series':'464fe805b432489c29c808094e66d2f7f1b0430c5168b8a1a1fe48af2b361fae'}
    return f


class CrossMethodPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fixed audited reviews exercise the pure metrology policy without repeated
        # reads of any attached private hardware evidence.
        cls.sha = fixtures()
        cls.forward = {'status':'INDEPENDENT_EVIDENCE_REVIEW_PASS',
                       'current_accuracy':'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER',
                       'qualification':'1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY',
                       'points':[{'preset_v':.8} ,
                                 {'preset_v':.9,'screening_current_ma':3.3539535349489293,
                                  'psu_current_ma':2.30122},
                                 {'preset_v':1.}, {}, {}]}
        cls.positive = {'status':'INDEPENDENT_EVIDENCE_REVIEW_PASS',
                        'run':'POSITIVE_LEADS_PASS','current_accuracy':'UNQUALIFIED',
                        'ballast_estimated_current_ma':3.320144201882636,
                        'psu_current_ma':2.26021}
        cls.negative = {'status':'INDEPENDENT_EVIDENCE_REVIEW_PASS',
                        'run':'REVERSED_LEADS_SAFE_ABORT','current_accuracy':'UNQUALIFIED',
                        'ballast_estimated_current_ma':None,
                        'ballast_voltage_v':-.3314382}
        cls.series = {'status':'INDEPENDENT_SERIES_DCI_EVIDENCE_REVIEW_PASS',
                      'source_current_accuracy':'UNQUALIFIED',
                      'scope':'ONE_POINT_DIAGNOSTIC_NOT_CURRENT_CALIBRATION',
                      'physical_off_confirmed':True, 'dmm_direct_current_ma':3.189844,
                      'psu_current_ma':2.03514,
                      'source_readback_spec_at_25C_pm5C_ma':10.00407028,
                      'observed_difference_within_nominal_spec':True}

    def report(self, target=None, change=None):
        r=[copy.deepcopy(self.forward),copy.deepcopy(self.positive),
           copy.deepcopy(self.negative),copy.deepcopy(self.series),copy.deepcopy(self.sha)]
        if change: change(r)
        if target:
            with self.assertRaises(AuditError): policy.build_policy(*r)
            return None
        return policy.build_policy(*r)

    def test_diagnostic_roles_and_values(self):
        rows,summary = self.report()
        self.assertEqual(summary['status'],policy.STATUS)
        self.assertEqual(len(rows),3)
        self.assertEqual([p['method'] for p in rows],[x[0] for x in policy.SOURCE_ROLES])
        self.assertEqual([round(p['diagnostic_current_ma'],6) for p in rows],
                         [3.353954,3.320144,3.189844])
        self.assertAlmostEqual(rows[-1]['psu_minus_diagnostic_ma'],-1.154704)
        self.assertEqual(summary['safety_evidence']['ballast_reversed'],
                         'EXPECTED_SAFETY_ABORT_VERIFIED_OFF')

    def test_fully_unqualified_and_no_cross_run_correction(self):
        rows,summary=self.report()
        self.assertTrue(all(p['metrology_qualification']=='UNQUALIFIED' for p in rows))
        self.assertFalse(summary['metrology_policy']['calibrated_curve_allowed'])
        self.assertFalse(summary['metrology_policy']['cross_run_bias_correction_allowed'])
        self.assertFalse(summary['metrology_policy']['nominal_psu_spec_conditions_verified'])
        self.assertEqual(summary['hardware_stage_status'],'UNCHANGED')
        self.assertNotIn('correction_ma',summary)

    def test_csv_schema_reuses_existing_exporter(self):
        rows,_=self.report()
        content=policy.csv_text(rows, policy.COLUMNS)
        self.assertEqual(len(content.splitlines()),4)
        self.assertNotIn('192.168.',content)
        self.assertNotIn('CALIBRATED',content)

    def test_reject_forward_overclaim(self):
        self.report('reject',lambda x:x[0].update(current_accuracy='CALIBRATED'))

    def test_reject_wrong_forward_shape(self):
        self.report('reject',lambda x:x[0]['points'].pop())

    def test_reject_other_voltage(self):
        self.report('reject',lambda x:x[0]['points'][1].update(preset_v=1.0))

    def test_reject_ballast_overclaim(self):
        self.report('reject',lambda x:x[1].update(current_accuracy='CALIBRATED'))

    def test_reject_reversed_polarity_as_valid_current(self):
        self.report('reject',lambda x:x[2].update(ballast_estimated_current_ma=3.3))

    def test_reject_reversed_not_safety_abort(self):
        self.report('reject',lambda x:x[2].update(run='POSITIVE_LEADS_PASS'))

    def test_reject_series_overclaim(self):
        self.report('reject',lambda x:x[3].update(source_current_accuracy='CALIBRATED'))

    def test_reject_series_wrong_scope(self):
        self.report('reject',lambda x:x[3].update(scope='CALIBRATED_IV'))

    def test_reject_series_missing_off(self):
        self.report('reject',lambda x:x[3].update(physical_off_confirmed=False))

    def test_reject_faked_spec_classification(self):
        self.report('reject',lambda x:x[3].update(observed_difference_within_nominal_spec=False))

    def test_reject_bad_spec_limit(self):
        self.report('reject',lambda x:x[3].update(source_readback_spec_at_25C_pm5C_ma=.5))

    def test_reject_nonfinite_current(self):
        self.report('reject',lambda x:x[3].update(dmm_direct_current_ma=float('nan')))

    def test_reject_duplicate_source_hash(self):
        self.report('reject',lambda x:x[4].update(series=x[4]['forward']))

    def test_reject_missing_source(self):
        self.report('reject',lambda x:x[4].pop('ballast_reversed'))

    def test_reject_invalid_source_hash(self):
        self.report('reject',lambda x:x[4].update(series='xyz'))

    def test_reject_wrong_current_envelope(self):
        self.report('reject',lambda x:x[1].update(ballast_estimated_current_ma=20.))

    def test_exclusive_export_directory_and_no_overwrite(self):
        rows,summary=self.report()
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'export'
            data={'points.csv':policy.csv_text(rows,policy.COLUMNS),
                  'summary.json':policy.json.dumps(summary,allow_nan=False)+'\n'}
            policy.export_files(out,data)
            self.assertEqual({p.name for p in out.iterdir()},{'summary.json','points.csv'})
            with self.assertRaisesRegex(AuditError,'already exists'):
                policy.export_files(out,data)
            self.assertIn('UNCHANGED',(out/'summary.json').read_text())

    def test_bad_source_hash_rejected_before_any_audit(self):
        fake={x:(Path('/no/secret/file'),self.sha[x]) for x in self.sha}
        fake['series']=(Path('/no/secret/file'),'0'*64)
        with patch.object(policy,'audit_forward') as forward:
            with self.assertRaises(OSError): policy.review_originals(fake)
            forward.assert_not_called()

    def test_mismatched_bench_rejected(self):
        fake={x:(Path('/tmp/x'),self.sha[x]) for x in self.sha}
        def load_mock(path, digest, *, allowed_events=None):
            return ([{'config_sha256':'x'*64}],digest)
        with patch.object(policy,'load_original',side_effect=load_mock),\
             patch.object(policy,'audit_forward') as forward:
            with self.assertRaisesRegex(AuditError,'bench'):
                policy.review_originals(fake)
            forward.assert_not_called()

    def test_dry_run_bypasses_instrument_io(self):
        # No instrument module imports in policy tool; CLI in dry run is pure audited I/O.
        self.assertNotIn('serial',policy.__dict__)
        self.assertNotIn('socket',policy.__dict__)
        self.assertNotIn('pyvisa',policy.__dict__)


if __name__ == '__main__':
    unittest.main()
