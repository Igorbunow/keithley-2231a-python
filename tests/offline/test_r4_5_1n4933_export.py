"""Software-only tests for reviewed 1N4933 CSV/JSON/SVG export."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import r4_5_1n4933_export as export


SAMPLES = (
    (.8, .799263, .5489759, .00145245),
    (.9, .899556, .5646302, .00230122),
    (1., .99966, .577764, .00312805),
    (.9, .900352, .5646074, .00227261),
    (.8, .800187, .5488702, .00147343),
)


def fixture():
    start = {
        'event':'start', 'variant':'1N4933_FORWARD_100OHM', 'monotonic_s':100.,
        'software':{'dirty':True}, 'serial':'SECRET_SERIAL', 'host':'SECRET_IP',
    }
    points = []
    reviewed = []
    for i, (preset, psu, diode, current) in enumerate(SAMPLES):
        estimate = (psu-diode)/99.86
        points.append({
            'event':'diode_iv_point','index':i,'preset_v':preset,
            'sampling':export.SAMPLING,'current_method':export.METHOD,
            'independent_current_sensor':False,'weak_current_unquantified':False,
            'digital_v':preset,'psu_voltage_v':psu,'psu_current_a':current,
            'diode_voltage_v':diode,'resistor_drop_v':psu-diode,
            'screening_current_a':estimate,'diode_estimated_power_w':diode*estimate,
            'monotonic_s':110+i*2,
            'utc':f'2026-10-30T15:40:{10+i*2:02d}+00:00',
            'timestamps_monotonic_s':{'psu':109.9+i*2,'dmm':109.93+i*2},
            'address':'SECRET_IP',
        })
        reviewed.append({'preset_v':preset, 'diode_voltage_v':diode,
                         'screening_current_ma':estimate*1000,
                         'psu_current_ma':current*1000})
    review = {
        'status':'INDEPENDENT_EVIDENCE_REVIEW_PASS',
        'qualification':'1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY',
        'current_accuracy':'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER',
        'points':reviewed,'provenance':{'commit':'a'*40,'runner_sha256':'b'*64,
                  'stage_sha256':'c'*64,'plan_sha256':'d'*64,'bench_sha256':'e'*64},
        'powered_duration_s':15.713, 'return_diode_error_mv':.1057,
        'return_current_error_ua':10.3114,
        'mean_psu_minus_resistor_current_ma':-1.0672585,
        'max_abs_psu_resistor_difference_ma':1.096825,
        'scpi':{'psu_zero_error_polls':430,'dmm_zero_error_polls':18},
    }
    return [start, *points], review


class ExportCalculation(unittest.TestCase):
    def setUp(self):
        self.rows, self.review = fixture()

    def compute(self):
        return export.prepare_export(self.rows, self.review, 'f'*64)

    def test_all_five_points_and_signed_segments(self):
        points, segments, summary = self.compute()
        self.assertEqual([p['direction'] for p in points], ['start','up','up','down','down'])
        self.assertEqual(len(segments),4)
        self.assertLess(segments[2]['delta_diode_v'],0)
        self.assertLess(segments[2]['delta_screening_current_ma'],0)
        self.assertGreater(segments[2]['incremental_resistance_ohm'],0)
        self.assertEqual(summary['count_segments'],4)

    def test_exact_derived_arithmetic(self):
        points,segments,summary=self.compute()
        self.assertAlmostEqual(points[0]['screening_current_ma'],2.5063799319046662)
        self.assertAlmostEqual(points[2]['diode_estimated_power_mw'],2.4409805782495497)
        self.assertAlmostEqual(segments[0]['incremental_resistance_ohm'],
                               (SAMPLES[1][2]-SAMPLES[0][2]) /
                               (points[1]['screening_current_ma']-points[0]['screening_current_ma'])*1000)
        self.assertAlmostEqual(summary['maximum_screening_current_ma'],4.224874824754656)
        self.assertEqual(summary['sampling'],export.SAMPLING)
        self.assertEqual(summary['current_accuracy'],'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER')

    def test_sequential_timestamps_not_simultaneous(self):
        points, _, summary=self.compute()
        self.assertAlmostEqual(points[0]['sample_gap_ms'],30.)
        self.assertIn('SEQUENTIAL',summary['current_method'])

    def test_readable_csv_columns(self):
        points,segments,_=self.compute()
        for rows,columns in ((points,export.POINT_COLUMNS),(segments,export.SEGMENT_COLUMNS)):
            contents=export.csv_text(rows,columns)
            parsed=list(csv.DictReader(io.StringIO(contents)))
            self.assertEqual(len(parsed),len(rows))
            self.assertEqual(tuple(parsed[0]),columns)

    def test_svg_well_formed_and_noninterpolated(self):
        points, _, _=self.compute()
        text=export.render_svg(points)
        root=ET.fromstring(text)
        self.assertTrue(root.tag.endswith('svg'))
        self.assertIn('accuracy unqualified',text)
        self.assertEqual(text.count('<circle'),5)
        self.assertNotIn('<script',text)

    def test_bad_review_rejected(self):
        self.review['status']='HW_NOT_REVIEWED'
        with self.assertRaises(export.AuditError):self.compute()

    def test_wrong_scope_rejected(self):
        self.review['current_accuracy']='CALIBRATED'
        with self.assertRaises(export.AuditError):self.compute()

    def test_mismatched_hardware_values_rejected(self):
        self.rows[1]['diode_voltage_v']+=.01
        with self.assertRaises(export.AuditError):self.compute()

    def test_fabricated_independent_current_rejected(self):
        self.rows[1]['independent_current_sensor']=True
        with self.assertRaises(export.AuditError):self.compute()

    def test_fake_synchronized_sampling_rejected(self):
        self.rows[1]['sampling']='SIMULTANEOUS'
        with self.assertRaises(export.AuditError):self.compute()

    def test_nonfinite_numbers_rejected(self):
        self.rows[2]['screening_current_a']=float('nan')
        with self.assertRaises(export.AuditError):self.compute()

    def test_nonchronological_times_rejected(self):
        self.rows[1]['timestamps_monotonic_s']['psu']=109.94
        with self.assertRaises(export.AuditError):self.compute()

    def test_wrong_number_of_points_rejected(self):
        self.rows.pop()
        with self.assertRaises(export.AuditError):self.compute()

    def test_no_raw_identity_leaks(self):
        points,segments,summary=self.compute()
        sanitized='\n'.join((export.csv_text(points,export.POINT_COLUMNS),
                              export.csv_text(segments,export.SEGMENT_COLUMNS),
                              json.dumps(summary),export.render_svg(points)))
        self.assertNotIn('SECRET_',sanitized)
        self.assertNotIn('raw_scpi',sanitized)


class OfflineCLI(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.events=self.root/'evidence.jsonl'
        self.events.write_text('{"event":"start"}\n')
        self.destination=self.root/'sanitized'
        self.rows,self.review=fixture()

    def test_success_generates_four_files(self):
        with patch.object(export,'load_original',return_value=(self.rows,'f'*64)) as loader,\
             patch.object(export,'audit',return_value=self.review) as auditor:
            rc=export.main([str(self.events),'--expected-sha256','f'*64,
                            '--output-dir',str(self.destination)])
        self.assertEqual(rc,0)
        loader.assert_called_once()
        auditor.assert_called_once()
        self.assertEqual({p.name for p in self.destination.iterdir()},set(export.EXPECTED_FILES))
        with (self.destination/'points.csv').open(encoding='utf-8') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))),5)

    def test_rejected_review_writes_nothing(self):
        self.review['status']='REJECTED'
        with patch.object(export,'load_original',return_value=(self.rows,'f'*64)),\
             patch.object(export,'audit',return_value=self.review):
            self.assertEqual(export.main([str(self.events),'--expected-sha256','f'*64,
                                         '--output-dir',str(self.destination)]),2)
        self.assertFalse(self.destination.exists())

    def test_wrong_sha_with_real_loader_rejected(self):
        self.assertEqual(export.main([str(self.events),'--expected-sha256','0'*64,
                                     '--output-dir',str(self.destination)]),2)
        self.assertFalse(self.destination.exists())

    def test_no_directory_overwrite(self):
        self.destination.mkdir()
        (self.destination/'preserve.txt').write_text('keep')
        with patch.object(export,'load_original',return_value=(self.rows,'f'*64)),\
             patch.object(export,'audit',return_value=self.review):
            self.assertEqual(export.main([str(self.events),'--expected-sha256','f'*64,
                                         '--output-dir',str(self.destination)]),2)
        self.assertEqual((self.destination/'preserve.txt').read_text(),'keep')

    def test_dry_run_creates_nothing(self):
        with patch.object(export,'load_original',return_value=(self.rows,'f'*64)),\
             patch.object(export,'audit',return_value=self.review):
            self.assertEqual(export.main([str(self.events),'--expected-sha256','f'*64,
                                         '--dry-run']),0)
        self.assertFalse(self.destination.exists())

    def test_no_device_io_code(self):
        src=Path(export.__file__).read_text()
        for token in ('import serial','import socket','SerialTransport(', 'TcpTransport(',
                      'diagnostic_write(','.output(1, True)'):
            self.assertNotIn(token,src)


if __name__ == '__main__':
    unittest.main()
