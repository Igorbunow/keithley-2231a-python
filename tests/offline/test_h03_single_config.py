"""Fail-closed, offline-only preparation for a CH1 CV/CC qualification."""
from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from bench_config import EXAMPLE, load_bench
from qualification import main
from single_cvcc_preflight import describe


POINT = """[qualification.h03_single_cvcc]
channel = 1
initial_voltage_v = [1.00, 1.50, 2.00]
initial_current_a = [0.100, 0.200, 0.300]
voltage_cap_v = [4.00, 4.05, 4.10]
current_cap_a = [0.300, 0.400, 0.500]
cv_voltage_v = 4.00
cv_current_limit_a = 0.300
cv_resistance_ohm = 40.0
cc_voltage_v = 4.00
cc_current_limit_a = 0.250
cc_resistance_ohm = 12.0
max_power_w = 1.20
"""


class SinglePointConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'bench.local.toml'
        self.base = EXAMPLE.read_text(encoding='utf-8')

    def configure(self, block=POINT):
        self.path.write_text(self.base + '\n' + block, encoding='utf-8')
        return load_bench(self.path)

    def run_cli(self, *extra):
        sink = StringIO()
        with mock.patch('qualification.open_transport', side_effect=AssertionError('PSU opened')), \
             mock.patch('qualification.open_load', side_effect=AssertionError('load opened')), \
             mock.patch('qualification.verify_serial_adapter', side_effect=AssertionError('USB enumerated')), \
             mock.patch('builtins.input', side_effect=AssertionError('operator gate started')), \
             redirect_stdout(sink):
            result = main(['run', 'H03', '--single-channel-cvcc', '--config', str(self.path), *extra])
        return result, sink.getvalue()

    def test_no_default_and_explicit_hash_bound_profile(self):
        self.assertIsNone(load_bench(EXAMPLE).h03_single_cvcc)
        config = self.configure()
        point = config.h03_single_cvcc
        self.assertEqual(point.channel, 1)
        self.assertEqual(point.current_cap_a, (.3, .4, .5))
        card = describe(config)
        self.assertIn(config.sha256, card)
        self.assertIn('CR 40', card)
        self.assertIn('CR 12', card)
        self.assertIn('CH1', card)
        before = config.sha256
        other = self.configure(POINT.replace('cv_voltage_v = 4.00', 'cv_voltage_v = 3.80'))
        self.assertNotEqual(before, other.sha256)
        self.assertIn(other.sha256, describe(other))

    def test_invalid_points_fail_closed(self):
        changes = (
            POINT.replace('channel = 1', 'channel = 2'),
            POINT.replace('channel = 1', 'channel = true'),
            POINT.replace('cv_voltage_v = 4.00', 'cv_voltage_v = 4.50'),
            POINT.replace('cc_current_limit_a = 0.250', 'cc_current_limit_a = 0.500'),
            POINT.replace('cv_resistance_ohm = 40.0', 'cv_resistance_ohm = 22.0'),
            POINT.replace('cc_resistance_ohm = 12.0', 'cc_resistance_ohm = 40.0'),
            POINT.replace('max_power_w = 1.20', 'max_power_w = 2.00'),
            POINT.replace('max_power_w = 1.20', 'max_power_w = 0.20'),
            POINT.replace('current_cap_a = [0.300, 0.400, 0.500]', 'current_cap_a = [0.900, 0.400, 0.500]'),
            POINT.replace('initial_voltage_v = [1.00, 1.50, 2.00]', 'initial_voltage_v = [5.0, 1.50, 2.00]'),
            POINT.replace('initial_current_a = [0.100, 0.200, 0.300]', 'initial_current_a = [0.1, 0.2]'),
            POINT.replace('cv_voltage_v = 4.00', 'cv_voltage_v = true'),
            POINT.replace('cv_voltage_v = 4.00\n', ''),
            POINT + 'unexpected = 12\n',
        )
        for block in changes:
            with self.subTest(block=block[-70:]):
                with self.assertRaises((TypeError, ValueError)):
                    self.configure(block)

    def test_preflight_never_opens_hardware_or_imports_legacy_runner(self):
        self.configure()
        with mock.patch('channel_stage.h03', side_effect=AssertionError('legacy H03 ran')):
            status, out = self.run_cli('--dry-run')
        self.assertEqual(status, 0)
        self.assertIn('DRY RUN', out)
        self.assertIn('CONFIRM BENCH H03-SINGLE-CVCC SHA256=', out)
        self.assertIn('KEL103', out)
        self.assertIn('4 V', out)

    def test_execute_is_explicitly_blocked_before_any_io_or_gate(self):
        self.configure()
        result, stdout = self.run_cli('--execute')
        self.assertEqual(result, 2)
        self.assertIn('BLOCKED: explicitly add --allow-single-cvcc-on', stdout)

    def test_plain_h03_remains_separate(self):
        self.configure()
        with mock.patch('qualification.preflight', return_value=None):
            sink = StringIO()
            with redirect_stdout(sink):
                status = main(['preflight', 'H03', '--config', str(self.path)])
        self.assertEqual(status, 0)
        self.assertNotIn('[H03 CH1 single CV/CC / CONFIG ONLY]', sink.getvalue())

    def test_activate_bench_rechecks_mutated_point(self):
        from bench_config import activate_bench
        cfg = self.configure()
        with self.assertRaisesRegex(ValueError, 'CH1-only'):
            activate_bench(replace(cfg, h03_single_cvcc=replace(cfg.h03_single_cvcc, channel=3)))


if __name__ == '__main__':
    unittest.main()
