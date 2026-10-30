"""R4.5-F-B exports: synthetic data, no equipment, no new hardware claims."""
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import r4_5_iv_export as subject


def synthetic_points():
    # Representative *sanitized fake* values; these are not raw hardware evidence.
    voltages = (2.996482,3.096954,3.197198,3.097015,2.996920)
    kel_i = (.0746,.0775,.0799,.0775,.0746)
    setpoints = (3.0,3.1,3.2,3.1,3.0)
    rows=[dict(event='start',variant='RESISTIVE_IV_CR40',monotonic_s=100.0)]
    for i in range(5):
        p = float(105 + 6*i)
        rows.append(dict(event='iv_point',index=i,
            monotonic_s=p+2.3,
            utc=f'2026-10-30T12:46:{i*6:02d}+00:00',
            sampling=subject.SAMPLE_MODE,commanded_v=setpoints[i],
            digital_v=setpoints[i],psu_v=setpoints[i]-.0001,
            psu_a=kel_i[i]-.001,psu_meas_w=(setpoints[i]-.0001)*(kel_i[i]-.001),
            kel_v=setpoints[i]-.002,kel_a=kel_i[i],
            kel_w=(setpoints[i]-.002)*kel_i[i],dmm_v=voltages[i],
            kel_effective_ohm=(setpoints[i]-.002)/kel_i[i],
            timestamps_monotonic_s=dict(psu=p,kel=p+.1,dmm=p+.2,power=p+2)))
    return rows


def accepted():
    return {'status':'REVIEWABLE_PASS',
            'source_commit':'fake-for-unit-tests',
            'source_dirty':False,
            'scope':'synthetic unit-test fixture, not actual hardware'}


class ExportMathTests(unittest.TestCase):
    def setUp(self):
        self.rows=synthetic_points()

    def analyze(self):
        return subject.prepare_export(self.rows,accepted(),'a'*64)

    def test_all_five_points_and_four_segments(self):
        points,segments,summary=self.analyze()
        self.assertEqual([p['direction'] for p in points],['start','up','up','down','down'])
        self.assertEqual([s['direction'] for s in segments],['up','up','down','down'])
        self.assertEqual([round(p['kel_a']*1000,1) for p in points],
                         [74.6,77.5,79.9,77.5,74.6])
        self.assertAlmostEqual(summary['two_point_forward_incremental_ohm'],37.870943396,7)
        self.assertAlmostEqual(summary['dmm_return_error_mv'],.438,8)

    def test_sequential_sample_timestamps_preserved_as_relative(self):
        points,_,_=self.analyze()
        self.assertAlmostEqual(points[0]['point_elapsed_s'],7.3)
        self.assertAlmostEqual(points[0]['psu_sample_elapsed_s'],5)
        self.assertAlmostEqual(points[0]['dmm_sample_elapsed_s'],5.2)
        self.assertEqual(points[0]['sampling'],subject.SAMPLE_MODE)
        self.assertAlmostEqual(points[0]['sample_span_s'],2)

    def test_no_instrument_identity_or_endpoint_in_exports(self):
        points,segments,summary=self.analyze()
        for thing in (subject.csv_text(points,subject.COLUMNS),
                      subject.csv_text(segments,subject.SEGMENT_COLUMNS),
                      json.dumps(summary)):
            for forbidden in ('/dev/tty', '192.168.', '*IDN?', '12345678',
                              'SERIAL', 'KORAD-', 'Keithley instruments'):
                self.assertNotIn(forbidden,thing)

    def test_absolute_and_incremental_resistance_separate(self):
        pts,segs,summary=self.analyze()
        self.assertNotAlmostEqual(pts[0]['r_kel_ohm'],segs[0]['incremental_ohm'],2)
        self.assertNotAlmostEqual(summary['mean_absolute_kel_resistance_ohm'],
                                  summary['two_point_forward_incremental_ohm'],2)

    def test_formulas_for_power_offsets_and_residuals(self):
        pts,segs,summary=self.analyze()
        self.assertAlmostEqual(pts[0]['kel_minus_psu_current_ma'],1,8)
        self.assertAlmostEqual(pts[0]['r_dmm_kel_ohm'],2.996482/.0746)
        self.assertAlmostEqual(sum(p['r_kel_ohm'] for p in pts)/5,
                               summary['mean_absolute_kel_resistance_ohm'])
        self.assertTrue(0 <= summary['ols_all_points_r_squared'] <= 1)
        self.assertEqual(summary['sampling'],subject.SAMPLE_MODE)

    def test_csv_column_headers_and_readability(self):
        points,segments,_=self.analyze()
        for rows,cols in ((points,subject.COLUMNS),(segments,subject.SEGMENT_COLUMNS)):
            text=subject.csv_text(rows,cols)
            parsed=list(csv.DictReader(io.StringIO(text)))
            self.assertEqual(tuple(parsed[0].keys()),cols)
            self.assertEqual(len(parsed),len(rows))

    def test_wrong_mode_is_rejected(self):
        self.rows[0]['variant']='UNKNOWN_DUT'
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_fabricated_simultaneous_claim_rejected(self):
        self.rows[2]['sampling']='SIMULTANEOUS'
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_invalid_timestamps_rejected(self):
        self.rows[1]['timestamps_monotonic_s']['kel']=105.3
        self.rows[1]['timestamps_monotonic_s']['psu']=105.5
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_nan_is_rejected(self):
        self.rows[1]['kel_a']=float('nan')
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_incorrect_absolute_voltage_rejected(self):
        self.rows[1]['commanded_v']=3.8
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_zero_current_rejected(self):
        self.rows[1]['kel_a']=0.0
        with self.assertRaises(subject.AuditError):self.analyze()

    def test_unreviewed_data_rejected(self):
        with self.assertRaises(subject.AuditError):
            subject.prepare_export(self.rows,{'status':'HW NOT_REVIEWED'},'a'*64)

    def test_wrong_point_count_rejected(self):
        self.rows.pop()
        with self.assertRaises(subject.AuditError):self.analyze()


class ExportFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.events=self.root/'raw-events.jsonl'
        self.events.write_text('{"test_id":"R45FIVA","event":"start"}\n',encoding='utf-8')
        self.sha=hashlib.sha256(self.events.read_bytes()).hexdigest()
        self.target=self.root/'public-analysis'

    def test_bad_sha_rejected_without_output(self):
        rc=subject.main([str(self.events),'--expected-sha256','0'*64,
                         '--output-dir',str(self.target)])
        self.assertEqual(rc,2)
        self.assertFalse(self.target.exists())

    def test_correct_sha_but_no_real_audit_rejected_without_output(self):
        rc=subject.main([str(self.events),'--expected-sha256',self.sha,
                         '--output-dir',str(self.target)])
        self.assertEqual(rc,2)
        self.assertFalse(self.target.exists())

    def test_invalid_hash_format_rejected(self):
        with self.assertRaises(subject.AuditError):
            subject.read_and_audit(self.events,'not-a-hash')

    def test_duplicate_json_keys_rejected(self):
        self.events.write_text('{"event":"start","event":"outcome"}\n')
        sha=hashlib.sha256(self.events.read_bytes()).hexdigest()
        rc=subject.main([str(self.events),'--expected-sha256',sha,
                         '--output-dir',str(self.target)])
        self.assertEqual(rc,2)
        self.assertFalse(self.target.exists())

    def test_nonstandard_nan_json_rejected(self):
        self.events.write_text('{"event":"start","number":NaN}\n')
        sha=hashlib.sha256(self.events.read_bytes()).hexdigest()
        rc=subject.main([str(self.events),'--expected-sha256',sha,
                         '--output-dir',str(self.target)])
        self.assertEqual(rc,2)

    def test_truncated_jsonl_rejected(self):
        self.events.write_bytes(b'{"event":"start"}')
        sha=hashlib.sha256(self.events.read_bytes()).hexdigest()
        self.assertEqual(subject.main([str(self.events),'--expected-sha256',sha,
                                        '--output-dir',str(self.target)]),2)

    def test_output_dir_is_exclusive(self):
        pts,segs,summary=subject.prepare_export(synthetic_points(),accepted(),'a'*64)
        self.target.mkdir()
        (self.target/'user-notes').write_text('preserve')
        with self.assertRaises(subject.AuditError):
            subject.export_directory(self.target,pts,segs,summary)
        self.assertEqual((self.target/'user-notes').read_text(),'preserve')

    def test_three_sanitized_files_created(self):
        pts,segs,summary=subject.prepare_export(synthetic_points(),accepted(),'a'*64)
        subject.export_directory(self.target,pts,segs,summary)
        self.assertEqual({p.name for p in self.target.iterdir()},
                         {'points.csv','segments.csv','summary.json'})
        with (self.target/'points.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))),5)
        with (self.target/'segments.csv').open() as f:
            self.assertEqual(len(list(csv.DictReader(f))),4)
        self.assertEqual(json.loads((self.target/'summary.json').read_text())['count_points'],5)

    def test_dry_run_can_audit_without_writing(self):
        # Mocking the independent audit is TEST-ONLY and does not give the CLI
        # a production bypass; normal main() requires its complete real audit.
        with patch.object(subject,'read_and_audit',return_value=
                          (synthetic_points(),accepted(),'a'*64)) as verify:
            self.assertEqual(subject.main([str(self.events),'--expected-sha256',self.sha,
                                           '--dry-run']),0)
            verify.assert_called_once()
        self.assertFalse(self.target.exists())

    def test_verify_then_write_in_main(self):
        with patch.object(subject,'read_and_audit',return_value=
                          (synthetic_points(),accepted(),'a'*64)):
            self.assertEqual(subject.main([str(self.events),'--expected-sha256',self.sha,
                                           '--output-dir',str(self.target)]),0)
        self.assertEqual(len(list(self.target.iterdir())),3)

    def test_no_hardware_import_or_scpi_execution_code(self):
        src=Path(subject.__file__).read_text()
        for token in ('import serial','import socket','SerialTransport(',
                      'TcpTransport(','diagnostic_write(','.output(1, True)'):
            self.assertNotIn(token,src)

if __name__=='__main__':unittest.main()
