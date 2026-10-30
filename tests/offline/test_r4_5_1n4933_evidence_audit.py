"""Offline adversarial checks for independent 1N4933 original-JSONL review."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
from audit_r4_5_1n4933_evidence import (AuditError, LEVELS, audit, load_original,
                                         points_audit, wire_audit)


class OriginalEvidenceTests(unittest.TestCase):
    def make_file(self, directory, content):
        path = Path(directory)/'events.jsonl'
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def test_requires_exact_original_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = (b'{"test_id":"R45FD","event":"start"}\n')*101
            path, digest = self.make_file(directory, payload)
            rows, observed = load_original(path, digest)
            self.assertEqual(observed, digest)
            self.assertEqual(len(rows), 101)
            with self.assertRaisesRegex(AuditError, 'SHA-256 mismatch'):
                load_original(path, '0'*64)

    def test_rejects_missing_final_newline_even_with_correct_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = (b'{"event":"start"}\n')*100+b'{"event":"start"}'
            path, digest = self.make_file(directory, payload)
            with self.assertRaisesRegex(AuditError, 'terminal newline'):
                load_original(path, digest)

    def test_rejects_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = (b'{"event":"start","event":"outcome"}\n')*101
            path, digest = self.make_file(directory, payload)
            with self.assertRaisesRegex(AuditError, 'Duplicate JSON key'):
                load_original(path, digest)

    def test_rejects_unsupported_events(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = (b'{"event":"fabricated"}\n')*101
            path, digest = self.make_file(directory, payload)
            with self.assertRaisesRegex(AuditError, 'Unexpected record'):
                load_original(path, digest)

    def test_rejects_mixed_ids(self):
        with self.assertRaisesRegex(AuditError, 'Mixed'):
            audit([{'event':'start','test_id':'R45FD','monotonic_s':1},
                   {'event':'outcome','test_id':'R45FA','monotonic_s':2}])

    def test_rejects_missing_physical_trace(self):
        with self.assertRaisesRegex(AuditError, 'Missing physical PSU trace'):
            wire_audit([{'event':'query','command':'*IDN?'}])

    def test_rejects_profile_bypass(self):
        with self.assertRaisesRegex(AuditError, 'bypassed'):
            wire_audit([{'event':'driver_trace', 'trace':{'event':'write',
                        'command':'SYST:REM','profile_bypass':True}}])


class PointNumericTests(unittest.TestCase):
    @staticmethod
    def example():
        # Synthetic numbers, not a real instrument test or copied evidence.
        diode = [.548, .564, .578, .564, .548]
        volt = [.80, .90, 1.00, .90, .80]
        stamps = [(20+i*4, 20.03+i*4) for i in range(5)]
        raw, pts = [], []
        for i, v in enumerate(volt):
            r = 99.86; current=(v-diode[i])/r
            raw.append(dict(event='diode_iv_raw_sample', index=i, preset_v=v,
                            psu_voltage_v=v, psu_current_a=current-.001,
                            regulation='CV',enabled=True,dmm_diode_voltage_v=diode[i],
                            resistor_measured_ohm=r,
                            psu_sample_monotonic_s=stamps[i][0],
                            dmm_sample_monotonic_s=stamps[i][1],
                            interpretation='UNVALIDATED_SEQUENTIAL_VOLTAGE_SAMPLE'))
            pts.append(dict(event='diode_iv_point', index=i, preset_v=v,
                            psu_voltage_v=v, psu_current_a=current-.001,
                            diode_voltage_v=diode[i],resistor_drop_v=v-diode[i],
                            screening_current_a=current,diode_estimated_power_w=diode[i]*current,
                            digital_v=v,weak_current_unquantified=False,
                            current_method='SEQUENTIAL_PSU_V_MINUS_DMM_DIODE_V_OVER_MEASURED_R',
                            independent_current_sensor=False,
                            sampling='SEQUENTIAL_NOT_SYNCHRONIZED',
                            timestamps_monotonic_s={'psu':stamps[i][0], 'dmm':stamps[i][1]}))
        segments=[]
        for i in range(1,5):
            direction = 'up' if i<=2 else 'down'
            sign = 1 if direction=='up' else -1
            segments.append(dict(event='diode_iv_segment',index=i,direction=direction,
                                 signed_current_delta_a=sign*(pts[i]['screening_current_a']-
                                                              pts[i-1]['screening_current_a']),
                                 signed_diode_delta_v=sign*(diode[i]-diode[i-1])))
        summ=dict(event='diode_iv_summary',
                  return_diode_error_v=0.0, return_screening_current_error_a=0.0,
                  powered_duration_s=15.0,qualification='FORWARD_STEADY_STATE_SCREENING_ONLY')
        wire=dict(psu_voltage=volt,psu_current=[r['psu_current_a'] for r in raw],
                  enable_monotonic_s=19, shutdown_monotonic_s=38)
        return raw+pts+segments+[summ], wire, [0.0]+diode

    def test_valid_bounded_synthetic_points(self):
        rows, wire, dmm = self.example()
        result = points_audit(rows,wire,dmm)
        self.assertEqual(len(result['points']),5)
        self.assertEqual(result['return_diode_error_mv'],0)
        self.assertAlmostEqual(result['mean_psu_minus_resistor_current_ma'],-1)

    def test_rejects_substituted_dmm_sample(self):
        rows, wire, dmm = self.example()
        rows[0]['dmm_diode_voltage_v'] += .001
        with self.assertRaisesRegex(AuditError,'Raw DMM diode'):
            points_audit(rows,wire,dmm)

    def test_rejects_wrong_segment_direction(self):
        rows, wire, dmm = self.example()
        next(x for x in rows if x['event']=='diode_iv_segment')['direction']='down'
        with self.assertRaisesRegex(AuditError,'direction'):
            points_audit(rows,wire,dmm)

    def test_rejects_fabricated_independent_ammeter(self):
        rows, wire, dmm = self.example()
        next(x for x in rows if x['event']=='diode_iv_point')['independent_current_sensor']=True
        with self.assertRaisesRegex(AuditError,'independent'):
            points_audit(rows,wire,dmm)

    def test_rejects_unbounded_sample(self):
        rows, wire, dmm = self.example()
        rows[2]['psu_current_a']=0.1
        wire['psu_current'][2]=0.1
        rows[7]['psu_current_a']=0.1
        with self.assertRaisesRegex(AuditError,'current/power'):
            points_audit(rows,wire,dmm)

    def test_rejects_powered_checkpoint_over_90s(self):
        rows, wire, dmm = self.example()
        rows[-1]['powered_duration_s']=91
        with self.assertRaisesRegex(AuditError,'scope/duration'):
            points_audit(rows,wire,dmm)

    def test_rejects_current_arithmetic_mutation(self):
        rows, wire, dmm = self.example()
        rows[5]['screening_current_a']+=.00001
        with self.assertRaisesRegex(AuditError,'Calculated resistor current'):
            points_audit(rows,wire,dmm)


if __name__=='__main__':
    unittest.main()
