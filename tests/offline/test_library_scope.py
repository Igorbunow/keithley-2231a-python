"""The distributable driver owns hardware, not host experiment workflows."""
from pathlib import Path
import unittest
import keithley2231a as driver

ROOT = Path(__file__).resolve().parents[2]


class LibraryScopeTests(unittest.TestCase):
    def test_application_workflows_not_exported(self):
        for symbol in ('voltage_sweep', 'coordinated_voltage_sweep',
                       'TimedVoltageObserver', 'Acquisition', 'ChannelStats'):
            self.assertFalse(hasattr(driver, symbol), symbol)
            self.assertNotIn(symbol, driver.__all__)

    def test_application_helpers_live_outside_installed_package(self):
        for filename in ('sweep.py', 'coordination.py', 'monitor.py'):
            self.assertFalse((ROOT / 'src' / 'keithley2231a' / filename).exists())
        for filename in ('iv_sweep.py', 'iv_coordination.py', 'acquisition.py'):
            self.assertTrue((ROOT / 'examples' / filename).is_file())
