"""H10 must never certify a CV/CC point with ambiguous source mode evidence."""
from types import SimpleNamespace
import unittest

import h10_powered_stage as h10
from keithley2231a import SafetyError


class H10RegulationGuardTests(unittest.TestCase):
    @staticmethod
    def point(regulation, *, mode='CV3A'):
        class Source:
            def set_channel(self, *args, **kwargs):
                pass

            def combined_outputs(self, enabled, **kwargs):
                pass

            def read_channel(self, channel):
                if channel == 3:
                    return SimpleNamespace(voltage_v=0., current_a=0., enabled=True,
                                           label='CH3', regulation='UNKNOWN')
                return SimpleNamespace(voltage_v=2.976, current_a=2.944,
                                       enabled=True, label='CH1+CH2', regulation=regulation)

        class Load:
            def configure_cr(self, resistance):
                return resistance

            def set_input(self, value):
                pass

            def measurements(self):
                return {'kel103_voltage_v': 2.944, 'kel103_current_a': 2.944,
                        'kel103_power_w': 2.944 * 2.944}

        class Meter:
            def measure(self):
                return {'voltage_v': 2.973}

        class Evidence:
            def __init__(self):
                self.samples = []

            def add(self, name, **kwargs):
                self.samples.append((name, kwargs))

        evidence = Evidence()
        result = h10._point(Source(), Load(), Meter(), evidence, mode, 1.0, 4.0,
                            max_power_w=10.0, current_tolerance_a=.1,
                            expected_current_a=(2.70, 3.20),
                            expected_voltage_v=(2.70, 3.10),
                            regulation_mode='CV')
        return result, evidence

    def test_unknown_regulation_cannot_qualify_numeric_cv(self):
        with self.assertRaisesRegex(SafetyError, 'regulation.*UNKNOWN'):
            self.point('UNKNOWN')

    def test_explicit_cv_still_qualifies(self):
        result, evidence = self.point('CV')
        self.assertEqual(result['psu_regulation'], 'CV')
        self.assertEqual(len(evidence.samples), 4)

    def test_wrong_explicit_regulation_still_rejected(self):
        with self.assertRaisesRegex(SafetyError, 'regulation'):
            self.point('CC')


if __name__ == '__main__':
    unittest.main()
