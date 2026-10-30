"""R4.5-F-I offline-only, adversarial current-source selection contract tests."""
from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from audit_r4_5_powered_step_evidence import AuditError
import iv_current_source_policy as policy
import r4_5_1n4933_iv_current_select as cli
from test_r4_5_1n4933_export import fixture

SHA = 'f' * 64
OTHER = 'a' * 64


def sample(idx, method=policy.SEQUENTIAL_DIODE_DCV_OVER_R, sha=SHA, *,
           value=3.5, role='DIAGNOSTIC_SCREENING',
           qualification='UNQUALIFIED', sampling='SEQUENTIAL_NOT_SYNCHRONIZED'):
    return policy.CurrentCandidate(sha, idx, method, value, role, sampling, qualification)


class PureSelectionContract(unittest.TestCase):
    def offered(self, n=5):
        return [[sample(i),sample(i,policy.PSU_READBACK,value=2.5,
                                   role='PSU_CONTROL_TELEMETRY')] for i in range(n)]

    def choose(self, offered=None, **kwargs):
        return policy.select_current_series(self.offered() if offered is None else offered,
                                            independently_audited=True, **kwargs)

    def test_auto_selects_complete_series_derived_not_psu(self):
        points, meta=self.choose()
        self.assertEqual(len(points),5)
        self.assertEqual(meta['selection'],policy.SEQUENTIAL_DIODE_DCV_OVER_R)
        self.assertEqual({v.method for v in points},{policy.SEQUENTIAL_DIODE_DCV_OVER_R})
        self.assertFalse(meta['selection_is_calibration'])
        self.assertFalse(meta['psu_readback_is_reference'])
        self.assertEqual(meta['run_sha256'],SHA)

    def test_explicit_selects_same_source(self):
        _, meta=self.choose(source=policy.SEQUENTIAL_DIODE_DCV_OVER_R)
        self.assertEqual(meta['requested_source'],policy.SEQUENTIAL_DIODE_DCV_OVER_R)

    def test_auto_prioritizes_direct_dmm_when_complete_in_same_run(self):
        offered=self.offered()
        for i,p in enumerate(offered): p.append(sample(i,policy.DMM_SERIES_DCI,value=3.2))
        vals,meta=self.choose(offered)
        self.assertEqual(meta['selection'],policy.DMM_SERIES_DCI)
        self.assertEqual(len(vals),5)

    def test_auto_prioritizes_direct_ballast_over_sequential(self):
        offered=self.offered()
        for i,p in enumerate(offered): p.append(sample(i,policy.DIRECT_BALLAST_DCV_OVER_R))
        _,meta=self.choose(offered)
        self.assertEqual(meta['selection'],policy.DIRECT_BALLAST_DCV_OVER_R)

    def test_auto_cannot_mix_methods_across_points(self):
        offered=[[sample(0,policy.DMM_SERIES_DCI)],
                 [sample(1,policy.DIRECT_BALLAST_DCV_OVER_R)]]
        with self.assertRaisesRegex(AuditError,'unavailable'):
            self.choose(offered)

    def test_sparse_dmm_not_used_for_curve(self):
        offered=self.offered()
        offered[2].append(sample(2,policy.DMM_SERIES_DCI))
        _,meta=self.choose(offered)
        self.assertEqual(meta['selection'],policy.SEQUENTIAL_DIODE_DCV_OVER_R)
        with self.assertRaisesRegex(AuditError,'unavailable'):
            self.choose(offered,source=policy.DMM_SERIES_DCI)

    def test_dmm_from_other_run_rejected_even_if_only_one_point(self):
        offered=self.offered()
        offered[0].append(sample(0,policy.DMM_SERIES_DCI,OTHER))
        with self.assertRaisesRegex(AuditError,'different original runs'):
            self.choose(offered)

    def test_single_source_mixed_fingerprints_rejected(self):
        offered=self.offered()
        offered[2][0]=sample(2,sha=OTHER)
        with self.assertRaises(AuditError): self.choose(offered)

    def test_psu_readback_unsafe_for_iv_explicit(self):
        with self.assertRaisesRegex(AuditError,'control telemetry'):
            self.choose(source=policy.PSU_READBACK)

    def test_only_psu_readback_is_not_a_current_curve(self):
        offered=[[sample(i,policy.PSU_READBACK,role='PSU_CONTROL_TELEMETRY')]
                 for i in range(5)]
        with self.assertRaisesRegex(AuditError,'unavailable'):
            self.choose(offered)

    def test_no_silent_fallback_on_requested_dmm(self):
        with self.assertRaisesRegex(AuditError,'unavailable'):
            self.choose(source=policy.DMM_SERIES_DCI)

    def test_metrology_promotion_rejected(self):
        with self.assertRaisesRegex(AuditError,'metrological'):
            self.choose(intent='metrology')

    def test_no_audit_rejected(self):
        with self.assertRaisesRegex(AuditError,'Independent'):
            policy.select_current_series(self.offered())

    def test_faked_qualification_rejected(self):
        offered=self.offered()
        offered[1][0]=sample(1,qualification='CALIBRATED')
        with self.assertRaisesRegex(AuditError,'calibrated'):
            self.choose(offered)

    def test_faked_synchronous_sampling_rejected(self):
        offered=self.offered()
        offered[1][0]=sample(1,sampling='SYNCHRONOUS')
        with self.assertRaisesRegex(AuditError,'synchronization'):
            self.choose(offered)

    def test_changed_role_rejected(self):
        offered=self.offered()
        offered[0][0]=sample(0,role='PSU_CONTROL_TELEMETRY')
        with self.assertRaisesRegex(AuditError,'role'):
            self.choose(offered)

    def test_duplicate_source_rejected(self):
        offered=self.offered()
        offered[0].append(sample(0))
        with self.assertRaisesRegex(AuditError,'duplicated'):
            self.choose(offered)

    def test_wrong_point_index_rejected(self):
        offered=self.offered()
        offered[1][0]=sample(3)
        with self.assertRaisesRegex(AuditError,'identity'):
            self.choose(offered)

    def test_non_finite_and_bool_rejected(self):
        for value in (float('nan'),float('inf'),0,-2,True):
            with self.subTest(value=value):
                offered=self.offered()
                offered[2][0]=sample(2,value=value)
                with self.assertRaises(AuditError): self.choose(offered)

    def test_invalid_hash_rejected(self):
        offered=self.offered()
        offered[0][0]=sample(0,sha='bad')
        with self.assertRaisesRegex(AuditError,'fingerprint'):
            self.choose(offered)

    def test_unrecognized_source_rejected(self):
        with self.assertRaisesRegex(AuditError,'Unknown'):
            self.choose(source='IMAGINARY_AMMETER')

    def test_empty_series_rejected(self):
        with self.assertRaises(AuditError): self.choose([])


class AuditedAdapterIntegration(unittest.TestCase):
    def setUp(self):
        self.rows, self.review=fixture()

    def test_real_fixture_five_points_and_four_segments(self):
        points,segs,summary=cli.select_audited_export(self.rows,self.review,SHA)
        self.assertEqual(len(points),5)
        self.assertEqual(len(segs),4)
        self.assertAlmostEqual(points[1]['selected_current_ma'],3.3539535349489293)
        self.assertAlmostEqual(points[1]['psu_control_readback_ma'],2.30122)
        self.assertTrue(all(p['run_sha256']==SHA for p in points))
        self.assertEqual(summary['hardware_stage_status'],'UNCHANGED')
        self.assertFalse(summary['calibrated_iv_allowed'])
        self.assertEqual(summary['selected_current_source']['selection'],
                         policy.SEQUENTIAL_DIODE_DCV_OVER_R)

    def test_no_unsupported_dmm_dci_substitution(self):
        with self.assertRaisesRegex(AuditError,'unavailable'):
            cli.select_audited_export(self.rows,self.review,SHA,
                                      source=policy.DMM_SERIES_DCI)

    def test_no_other_run_ballast_substitution(self):
        with self.assertRaisesRegex(AuditError,'unavailable'):
            cli.select_audited_export(self.rows,self.review,SHA,
                                      source=policy.DIRECT_BALLAST_DCV_OVER_R)

    def test_no_psu_control_readback_curve(self):
        with self.assertRaisesRegex(AuditError,'control telemetry'):
            cli.select_audited_export(self.rows,self.review,SHA,
                                      source=policy.PSU_READBACK)

    def test_metrology_intent_never_allowed(self):
        with self.assertRaisesRegex(AuditError,'metrological'):
            cli.select_audited_export(self.rows,self.review,SHA,intent='metrology')

    def test_invalid_audit_rejected(self):
        self.review['status']='FAIL'
        with self.assertRaises(AuditError): cli.select_audited_export(self.rows,self.review,SHA)

    def test_forged_independent_current_rejected(self):
        self.rows[1]['independent_current_sensor']=True
        with self.assertRaises(AuditError): cli.select_audited_export(self.rows,self.review,SHA)

    def test_missing_source_point_rejected(self):
        self.rows.pop()
        with self.assertRaises(AuditError): cli.select_audited_export(self.rows,self.review,SHA)

    def test_csv_schema_and_no_identity_leak(self):
        rows,segs,summary=cli.select_audited_export(self.rows,self.review,SHA)
        raw='\n'.join((cli.csv_text(rows,cli.POINT_COLUMNS),
                       cli.csv_text(segs,cli.SEGMENT_COLUMNS),
                       json.dumps(summary)))
        self.assertNotIn('SECRET_',raw)
        self.assertNotIn('192.168.',raw)
        self.assertNotIn('SCPI WRITE',raw)
        self.assertIn('UNQUALIFIED',raw)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(cli.csv_text(rows,cli.POINT_COLUMNS))))),5)

    def test_cli_exports_exclusive_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)/'out'
            evidence=Path(folder)/'source.jsonl'
            evidence.write_text('{"event":"placeholder"}\n')
            with patch.object(cli,'load_original',return_value=(self.rows,SHA)),\
                 patch.object(cli,'audit',return_value=self.review):
                self.assertEqual(cli.main([str(evidence),'--expected-sha256',SHA,
                                           '--output-dir',str(destination)]),0)
            self.assertEqual({x.name for x in destination.iterdir()},
                             {'points.csv','segments.csv','summary.json'})
            with (destination/'points.csv').open(encoding='utf-8') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))),5)
            with patch.object(cli,'load_original',return_value=(self.rows,SHA)),\
                 patch.object(cli,'audit',return_value=self.review):
                self.assertEqual(cli.main([str(evidence),'--expected-sha256',SHA,
                                           '--output-dir',str(destination)]),2)
            self.assertTrue((destination/'points.csv').exists())

    def test_cli_no_side_effect_on_rejected_method(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/'do_not_create'
            evidence=Path(folder)/'source.jsonl'
            evidence.write_text('{}\n')
            with patch.object(cli,'load_original',return_value=(self.rows,SHA)),\
                 patch.object(cli,'audit',return_value=self.review):
                self.assertEqual(cli.main([str(evidence),'--expected-sha256',SHA,
                                           '--current-source','DMM_SERIES_DCI',
                                           '--output-dir',str(dest)]),2)
            self.assertFalse(dest.exists())

    def test_cli_dry_run_no_files(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence=Path(folder)/'source.jsonl'
            evidence.write_text('{}\n')
            with patch.object(cli,'load_original',return_value=(self.rows,SHA)),\
                 patch.object(cli,'audit',return_value=self.review):
                self.assertEqual(cli.main([str(evidence),'--expected-sha256',SHA]),0)
            self.assertEqual([p.name for p in Path(folder).iterdir()],['source.jsonl'])

    def test_cli_wrong_hash_actual_loader_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence=Path(folder)/'source.jsonl'
            evidence.write_text('{}\n')
            self.assertEqual(cli.main([str(evidence),'--expected-sha256','0'*64]),2)

    def test_no_hardware_transport_imports(self):
        for source in (Path(cli.__file__).read_text(),Path(policy.__file__).read_text()):
            for forbidden in ('import serial','import socket','import pyvisa',
                              'SerialTransport(','.output(1, True)'):
                self.assertNotIn(forbidden,source)


if __name__ == '__main__':
    unittest.main()
