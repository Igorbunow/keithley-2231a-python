"""O01 is simulator-only. No USB enumeration, LAN or hardware access."""
from examples.iv_coordination import verify_coordinated_csv
from examples.iv_coordination import coordinated_voltage_sweep
from examples.iv_coordination import TimedVoltageObserver
import csv
import math
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from support.qualification_transport import FakeTransport
from test_driver import profile
from keithley2231a import (Keithley2231A, SafetyError)


_DEFAULT = object()


class FakeDMM:
    def __init__(self, fake):
        self.fake = fake
        self.live = True
        self.configured = True
        self.calls = 0
        self.fail = None
        self.fail_at = None
        self.disarm_on_read = False
        self.reply = _DEFAULT

    def measure(self):
        self.calls += 1
        if self.fail and (self.fail_at is None or self.calls == self.fail_at):
            raise self.fail
        if self.disarm_on_read:
            self.live = False
        if self.reply is not _DEFAULT:
            return self.reply
        return {'voltage_v': self.fake.values[1]['VOLT'] - .001}


class O01CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakeTransport()
        self.dev = Keithley2231A(self.raw)
        self.dev.apply_profile(profile(), confirmed_wiring='independent')
        self.meter = FakeDMM(self.raw)

    def run_sweep(self, csv_path, voltages=(.5, 1., 1.5), **kwargs):
        with patch('examples.iv_sweep.time.sleep'):
            return coordinated_voltage_sweep(self.dev, self.meter, 1, voltages,
                current_limit_a=.04, csv_path=csv_path, settle_s=0., **kwargs)

    def assert_off(self):
        self.assertEqual(self.raw.flags['OUTP:STAT:ALL'], 0)
        self.assertFalse(self.dev.armed)
        self.assertTrue(all(self.raw.values[ch]['CHAN:OUTP'] == 0 for ch in (1,2,3)))

    def test_full_synthetic_sweep_real_driver_path_and_all_csv_fields(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'o01.csv'
            points = self.run_sweep(path)
            self.assertEqual(len(points), 3)
            self.assertEqual(self.meter.calls, 3)
            self.assertEqual(verify_coordinated_csv(path, points), 3)
            self.assertEqual([p.requested_v for p in points], [.5,1.,1.5])
            self.assertEqual([p.regulation for p in points], ['CV']*3)
            for p in points:
                self.assertAlmostEqual(p.external['dmm_voltage_v'], p.measured_v-.001, places=6)
                self.assertGreaterEqual(p.external['source_dmm_gap_s'], 0)
                self.assertLessEqual(p.external['source_dmm_window_s'], 5)
                self.assertAlmostEqual(p.external['source_dmm_window_s'],
                    p.external['source_dmm_gap_s']+p.external['dmm_query_duration_s'])
            self.assertEqual(self.raw.cmds.count('CHAN:OUTP 1'), 1)
            self.assert_off()

    def test_rejected_meter_preflight_never_energizes_or_reads_instrument(self):
        self.meter.configured = False
        prior = list(self.raw.cmds)
        with self.assertRaises(SafetyError):
            self.run_sweep(Path(tempfile.gettempdir())/'not-created-o01.csv')
        self.assertEqual(self.raw.cmds, prior)
        self.assertEqual(self.meter.calls, 0)
        self.assertTrue(self.dev.armed)

    def test_bad_bound_rejected_without_hardware(self):
        prior = list(self.raw.cmds)
        for opts in ({'max_window_s':float('nan')}, {'max_start_lag_s':0.},
                     {'max_window_s':1., 'max_start_lag_s':2.},
                     {'max_voltage_difference_v':2.}, {'max_start_lag_s':True}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                self.run_sweep(Path(tempfile.gettempdir())/'not-created-o01.csv', **opts)
            self.assertEqual(self.raw.cmds, prior)
        with self.assertRaises(ValueError):
            coordinated_voltage_sweep(self.dev,self.meter,1,[.5],current_limit_a=.04,csv_path=None)
        self.assertEqual(self.raw.cmds,prior)

    def test_observer_transport_fault_is_not_retried_and_psu_is_off(self):
        self.meter.fail = OSError('DMM LAN failed')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(OSError, 'DMM LAN failed'):
                self.run_sweep(Path(td)/'o01.csv')
        self.assertEqual(self.meter.calls,1)
        self.assert_off()

    def test_second_meter_fault_stops_sweep_without_new_setpoints(self):
        self.meter.fail = TimeoutError('meter read timeout')
        self.meter.fail_at = 2
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(TimeoutError, 'meter read timeout'):
                self.run_sweep(Path(td)/'o01.csv',voltages=(.5,1.,1.5))
        self.assertEqual(self.meter.calls,2)
        self.assertEqual(self.raw.cmds.count('CHAN:OUTP 1'),1)
        self.assertNotIn('VOLT 1.50',self.raw.cmds)
        self.assert_off()

    def test_csv_disk_write_failure_occurs_after_verified_psu_off(self):
        with tempfile.TemporaryDirectory() as td:
            # A directory is not a valid CSV file; failure occurs after sweep OFF.
            with self.assertRaises(IsADirectoryError):
                self.run_sweep(Path(td), voltages=(.5,))
        self.assertEqual(self.meter.calls,1)
        self.assert_off()

    def test_dmm_becomes_unready_during_read_is_fail_closed(self):
        self.meter.disarm_on_read=True
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(SafetyError,'DMM session became unready'):
                self.run_sweep(Path(td)/'o01.csv', voltages=(.5,))
        self.assertEqual(self.meter.calls,1)
        self.assert_off()

    def test_bad_meter_values_fail_closed_without_retries(self):
        for value in (None, {}, {'voltage_v':float('nan')},
                      {'voltage_v':float('inf')}, {'voltage_v':'2.0'},
                      {'voltage_v':False}, {'voltage_v':10},
                      {'voltage_v':.5,'extra':123}):
            with self.subTest(value=value):
                fake=FakeTransport();dev=Keithley2231A(fake)
                dev.apply_profile(profile(),confirmed_wiring='independent')
                meter=FakeDMM(fake); meter.reply=value
                with tempfile.TemporaryDirectory() as td, patch('examples.iv_sweep.time.sleep'):
                    with self.assertRaises(SafetyError):
                        coordinated_voltage_sweep(dev,meter,1,[.5],current_limit_a=.04,
                                                  csv_path=Path(td)/'o01.csv',settle_s=0)
                self.assertEqual(meter.calls,1)
                self.assertFalse(dev.armed)
                self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)

    def test_excess_observer_duration_or_clock_regression_fails_off(self):
        for stamps in ((100.,100.1,107.), (100.,99.,99.9)):
            fake=FakeTransport();dev=Keithley2231A(fake)
            dev.apply_profile(profile(),confirmed_wiring='independent')
            meter=FakeDMM(fake)
            # The fake Reading has real monotonic time. Patch the observer's
            # clock to lag/advance relative to its source time.
            original=dev.read_channel
            def synthetic_read(ch):
                sample=original(ch)
                return replace(sample,monotonic_s=100.)
            dev.read_channel=synthetic_read
            with tempfile.TemporaryDirectory() as td, patch('examples.iv_sweep.time.sleep'), \
                 patch('examples.iv_coordination.time.monotonic',side_effect=stamps[1:]):
                with self.assertRaises(SafetyError):
                    coordinated_voltage_sweep(dev,meter,1,[.5],current_limit_a=.04,
                                              csv_path=Path(td)/'o01.csv',settle_s=0,
                                              max_start_lag_s=2,max_window_s=5)
            self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)
            self.assertFalse(dev.armed)

    def test_csv_tampering_detected_after_power_off(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'o01.csv';pts=self.run_sweep(path)
            with path.open(newline='') as f:
                rows=list(csv.DictReader(f))
            for key, bad in (('dmm_voltage_v','9.0'),('dmm_query_start_monotonic_s','NaN'),
                             ('source_dmm_window_s','0.1'),('measured_a','0.99'),
                             ('timestamp_utc','spoofed'),('regulation','CC'),('index','3')):
                with self.subTest(key=key):
                    changed=[r.copy() for r in rows];changed[0][key]=bad
                    with path.open('w',newline='') as f:
                        w=csv.DictWriter(f,fieldnames=tuple(rows[0]));w.writeheader();w.writerows(changed)
                    with self.assertRaises(SafetyError):verify_coordinated_csv(path,pts)
            self.assert_off()

    def test_no_unapproved_channel_can_be_on_and_meter_is_not_called(self):
        self.dev.output(2,True)
        before=list(self.raw.cmds)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SafetyError):self.run_sweep(Path(td)/'o01.csv')
        self.assertEqual(self.meter.calls,0)
        self.assertTrue(self.raw.values[2]['CHAN:OUTP'])
        self.assertTrue(self.dev.armed)
        self.assertEqual(self.raw.cmds[:len(before)],before)
        self.dev.emergency_off()


if __name__ == '__main__':unittest.main()
