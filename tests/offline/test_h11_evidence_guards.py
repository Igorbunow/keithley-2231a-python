"""Fail-closed cross-instrument and CSV integrity regression for H11."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from h11_stage import verify_h11_load_sample, verify_h11_csv
from keithley2231a import SafetyError


class H11EvidenceGuards(unittest.TestCase):
    SAMPLES = (
        (.500232, .0108995, .0054522786839999995, .4978, .0125, .0062),
        (.999655, .0233593, .0233512410415, .9976, .0247, .0246),
        (1.49929, .0358629, .053768887341, 1.4974, .0373, .0558),
        (1.64498, .0397196, .065337947608, 1.644, .0409, .0672),
    )

    @staticmethod
    def point(index, data):
        sv, si, sp, kv, ki, kp = data
        return SimpleNamespace(index=index, requested_v=(.5, 1., 1.5, 2.)[index],
            timestamp_utc=f'2026-10-29T01:06:{40+index}.000000+00:00',
            monotonic_s=1000. + index,
            measured_v=sv, measured_a=si, measured_w=sp,
            regulation='CC' if index==3 else 'CV',
            external={'kel_voltage_v':kv,'kel_current_a':ki,'kel_power_w':kp})

    def test_all_four_saved_real_h11_samples_pass_stronger_checks(self):
        for data in self.SAMPLES:
            sv, si, sp, kv, ki, kp = data
            with self.subTest(sv=sv):
                verify_h11_load_sample(SimpleNamespace(voltage_v=sv,current_a=si,power_w=sp), kv,ki,kp)

    def test_wrong_current_cannot_hide_within_old_25ma_tolerance(self):
        sv, si, sp, kv, ki, kp = self.SAMPLES[2]
        with self.assertRaises(SafetyError):
            verify_h11_load_sample(SimpleNamespace(voltage_v=sv,current_a=si,power_w=sp), kv, .015, .0225)

    def test_wrong_kel_power_or_ohms_cannot_hide_within_old_100mw_tolerance(self):
        sv, si, sp, kv, ki, kp = self.SAMPLES[3]
        src = SimpleNamespace(voltage_v=sv,current_a=si,power_w=sp)
        for wrong_v, wrong_i, wrong_p in ((kv,ki,0.0),(kv,.01,kv*.01),(kv,ki,.095)):
            with self.subTest(values=(wrong_v,wrong_i,wrong_p)), self.assertRaises(SafetyError):
                verify_h11_load_sample(src, wrong_v,wrong_i,wrong_p)

    def test_csv_all_columns_and_external_values_must_match_actual_samples(self):
        points=[self.point(i,d) for i,d in enumerate(self.SAMPLES)]
        with TemporaryDirectory() as td:
            path=Path(td)/'iv_sweep.csv'
            cols=('index','requested_v','timestamp_utc','monotonic_s','measured_v','measured_a','measured_w','regulation',
                  'kel_current_a','kel_power_w','kel_voltage_v')
            with path.open('w',newline='',encoding='utf-8') as fd:
                w=csv.writer(fd);w.writerow(cols)
                for p in points:
                    w.writerow((p.index,p.requested_v,p.timestamp_utc,p.monotonic_s,p.measured_v,p.measured_a,p.measured_w,p.regulation,
                                p.external['kel_current_a'],p.external['kel_power_w'],p.external['kel_voltage_v']))
            self.assertEqual(verify_h11_csv(path, points), 4)
            with path.open(newline='',encoding='utf-8') as fd:
                rows=list(csv.DictReader(fd))
            for key,value in (('measured_a','0.002'),('kel_power_w','0.000'),('index','2')):
                with self.subTest(corrupt=key):
                    tampered=[dict(row) for row in rows]
                    tampered[1][key]=value
                    with path.open('w',newline='',encoding='utf-8') as fd:
                        w=csv.DictWriter(fd,fieldnames=cols);w.writeheader();w.writerows(tampered)
                    with self.assertRaises(SafetyError):verify_h11_csv(path,points)
            with path.open('w',newline='',encoding='utf-8') as fd:
                w=csv.writer(fd);w.writerow(('requested_v','regulation'))
                for p in points:w.writerow((p.requested_v,p.regulation))
            with self.assertRaises(SafetyError):verify_h11_csv(path,points)
