"""Offline negative/positive CC physics checks. Never accesses real equipment."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from audit_r4_5_powered_step_evidence import AuditError
from audit_r4_5_powered_step_cc_review import check_cc_consistency


def fixture():
    snaps = [
      dict(event='powered_step_snapshot', label='before', mode='CC',
           kel_a=.1001, kel_v=1.1048, kel_w=.1105,
           psu_a=.0996704, psu_v=1.10993, psu_meas_w=.110655,
           dmm_v=1.105249, timestamps_monotonic_s={
               'psu_s':100.,'kel_s':100.02,'dmm_s':100.04,'power_s':102.}),
      dict(event='powered_step_snapshot', label='after', mode='CC',
           kel_a=.1052, kel_v=1.1599, kel_w=.1220,
           psu_a=.104679, psu_v=1.16595, psu_meas_w=.122055,
           dmm_v=1.161415, timestamps_monotonic_s={
               'psu_s':110.,'kel_s':110.02,'dmm_s':110.04,'power_s':112.}),
    ]
    return [dict(event='write', command='CURR:UP', monotonic_s=106.), *snaps]


class CCReviewPhysicsTests(unittest.TestCase):
    def _reject(self, mutate):
        recs = fixture()
        mutate(recs)
        with self.assertRaises(AuditError):
            check_cc_consistency(recs)

    def test_consistent_5ma_and_11ohm(self):
        r = check_cc_consistency(fixture())
        self.assertAlmostEqual(r['kel_current_delta_a'], .0051)
        self.assertAlmostEqual(r['dmm_voltage_delta_v'], .056166)

    def test_missing_after(self):
        self._reject(lambda r: r.pop())

    def test_wrong_regulation(self):
        self._reject(lambda r: r[1].update(mode='CV'))

    def test_wrong_absolute_current(self):
        self._reject(lambda r: r[1].update(kel_a=.125))

    def test_wrong_effective_resistance(self):
        self._reject(lambda r: r[2].update(kel_v=1.4))

    def test_wrong_dmm_voltage_level(self):
        self._reject(lambda r: r[2].update(dmm_v=1.2))

    def test_wrong_psu_current(self):
        self._reject(lambda r: r[2].update(psu_a=.120))

    def test_wrong_psu_power(self):
        self._reject(lambda r: r[2].update(psu_meas_w=.15))

    def test_wrong_kel_power(self):
        self._reject(lambda r: r[2].update(kel_w=.14))

    def test_wrong_step_size(self):
        self._reject(lambda r: r[2].update(kel_a=.108))

    def test_wrong_dmm_delta_preserving_absolute_level(self):
        self._reject(lambda r: r[2].update(dmm_v=1.176))

    def test_nonsynchronized_samples(self):
        self._reject(lambda r: r[2]['timestamps_monotonic_s'].update(dmm_s=113.))

    def test_reversed_sample_clock(self):
        self._reject(lambda r: r[2]['timestamps_monotonic_s'].update(kel_s=109.))

    def test_missing_step_command(self):
        self._reject(lambda r: r[0].update(command='CURR:DOWN'))

    def test_missing_numeric(self):
        self._reject(lambda r: r[2].update(kel_a='NaN'))


if __name__ == '__main__':
    unittest.main()
