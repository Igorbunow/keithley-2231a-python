"""R3.2 simulated integrity monitor lifecycle; no physical equipment used."""
import threading
import time
import unittest
from keithley2231a import Keithley2231A, SafetyError, ParallelProfile, ChannelProfile
from tests.test_driver import FakeTransport, profile


class ProfileIntegrityMonitorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTransport()
        self.psu = Keithley2231A(self.fake)
        self.psu.apply_profile(profile(), confirmed_wiring='independent')

    def tearDown(self):
        self.psu.stop_profile_monitor()

    def wait_until(self, predicate, timeout_s=2.0):
        until = time.monotonic() + timeout_s
        while time.monotonic() < until:
            if predicate():
                return
            threading.Event().wait(.005)
        self.fail('Timed out waiting for profile monitor state')

    def test_verify_every_s_does_not_start_an_implicit_thread(self):
        before = list(self.fake.cmds)
        self.assertFalse(self.psu.profile_monitor_running)
        threading.Event().wait(.03)
        self.assertEqual(self.fake.cmds, before)
        self.assertIsNone(self.psu.profile_monitor_error)

    def test_explicit_start_checks_immediately_and_stop_joins_without_scpi(self):
        checked = threading.Event()
        original = self.psu.verify_profile

        def observable_check():
            original()
            checked.set()

        self.psu.verify_profile = observable_check
        for interval in (0, -1, float('nan'), True, .01):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    self.psu.start_profile_monitor(interval_s=interval)
                self.assertFalse(self.psu.profile_monitor_running)
        with self.assertRaises(ValueError):
            self.psu.stop_profile_monitor(timeout_s=-1)
        self.assertFalse(checked.is_set())
        self.psu.start_profile_monitor(interval_s=.1)
        self.assertTrue(checked.is_set())  # initial synchronous verification
        self.assertTrue(self.psu.profile_monitor_running)
        self.assertIsNone(self.psu.profile_monitor_error)
        before = list(self.fake.cmds)
        with self.assertRaises(SafetyError):
            self.psu.start_profile_monitor(interval_s=.1)
        self.assertEqual(self.fake.cmds, before)  # no duplicate check
        self.psu.stop_profile_monitor()
        self.assertFalse(self.psu.profile_monitor_running)
        self.assertEqual(self.fake.cmds, before)
        self.assertTrue(self.psu.armed)

    def test_worker_actually_checks_drift_and_uses_shared_fault_shutdown(self):
        for name, mutation in (
                ('voltage', lambda f: f.values[1].__setitem__('VOLT', 1.11)),
                ('current', lambda f: f.values[1].__setitem__('CURR', .151)),
                ('limit', lambda f: f.values[1].__setitem__('VOLT:LIM:STAT', 0.)),
                ('topology', lambda f: f.flags.__setitem__('OUTP:TRAC', 1.))):
            with self.subTest(change=name):
                fake = FakeTransport()
                psu = Keithley2231A(fake)
                psu.apply_profile(profile(), confirmed_wiring='independent')
                try:
                    psu.start_profile_monitor(interval_s=.1)
                    mutation(fake)
                    self.wait_until(lambda: psu.profile_monitor_error is not None)
                    psu.stop_profile_monitor()
                    self.assertFalse(psu.armed)
                    self.assertTrue(psu.faulted)
                    self.assertFalse(psu.profile_monitor_running)
                    self.assertIn('Profile integrity check failed', psu.profile_monitor_error)
                    self.assertIn('output OFF confirmed', psu.profile_monitor_error)
                    self.assertEqual(fake.cmds.count('OUTP 0'), 2)  # apply + one fault shutdown
                finally:
                    psu.stop_profile_monitor()

    def test_uncertain_link_stops_monitor_without_shutdown_attempt_or_retry(self):
        self.psu.output(1, True)
        self.psu.start_profile_monitor(interval_s=.1)
        self.fake.failed_query = 'VOLT:LIM?'
        before = self.fake.cmds.count('OUTP 0')
        self.wait_until(lambda: self.psu.profile_monitor_error is not None)
        self.psu.stop_profile_monitor()
        self.assertTrue(self.psu.faulted)
        self.assertFalse(self.psu.armed)
        self.assertFalse(self.psu.profile_monitor_running)
        self.assertIn('UNKNOWN OUTPUT STATE', self.psu.profile_monitor_error)
        self.assertEqual(self.fake.cmds.count('OUTP 0'), before)
        # A live output may remain on when the transport becomes uncertain.
        self.assertEqual(self.fake.values[1]['CHAN:OUTP'], 1.)
        cmds = list(self.fake.cmds)
        with self.assertRaises(SafetyError):
            self.psu.verify_profile()
        self.assertEqual(self.fake.cmds, cmds)

    def test_emergency_off_stops_monitor_and_profile_replacement_requires_join(self):
        self.psu.start_profile_monitor(interval_s=.1)
        self.psu.emergency_off()
        with self.assertRaises(SafetyError):
            self.psu.apply_profile(profile(), confirmed_wiring='independent')
        self.psu.stop_profile_monitor()
        self.psu.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(self.psu.armed)

    def test_close_joins_monitor_before_closing_transport(self):
        self.psu.start_profile_monitor(interval_s=.1)
        self.psu.close()
        self.assertFalse(self.psu.profile_monitor_running)
        self.assertTrue(self.fake.closed)


    def test_live_output_drift_attempts_and_confirms_shutdown(self):
        self.psu.output(1, True)
        self.psu.start_profile_monitor(interval_s=.1)
        self.fake.values[1]['CURR'] = .151
        self.wait_until(lambda: self.psu.profile_monitor_error is not None)
        self.psu.stop_profile_monitor()
        self.assertTrue(self.psu.faulted)
        self.assertFalse(self.psu.armed)
        self.assertIn('output OFF confirmed', self.psu.profile_monitor_error)
        self.assertEqual(self.fake.values[1]['CHAN:OUTP'], 0.)

    def test_monitor_cannot_start_unarmed_or_faulted_and_emits_no_scpi(self):
        unarmed_source = FakeTransport()
        unarmed = Keithley2231A(unarmed_source)
        with self.assertRaises(SafetyError):
            unarmed.start_profile_monitor(interval_s=.1)
        self.assertEqual(unarmed_source.cmds, [])
        self.psu.emergency_off()
        before = list(self.fake.cmds)
        with self.assertRaises(SafetyError):
            self.psu.start_profile_monitor(interval_s=.1)
        self.assertEqual(self.fake.cmds, before)

    def test_parallel_monitor_detects_ch3_hardware_limit_drift(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake)
        configured = ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                                     ChannelProfile(4.0, .5, 1.0, .1))
        dev.apply_profile(configured, confirmed_wiring='parallel')
        try:
            dev.start_profile_monitor(interval_s=.1)
            fake.values[3]['VOLT:LIM'] -= .01
            self.wait_until(lambda: dev.profile_monitor_error is not None)
            self.assertIn('CH3 hardware voltage limit changed', dev.profile_monitor_error)
            self.assertTrue(dev.faulted)
            self.assertFalse(dev.armed)
        finally:
            dev.stop_profile_monitor()

    def test_stop_timeout_does_not_wait_for_stalled_scpi_lock(self):
        reached = threading.Event()
        release = threading.Event()
        original_query = self.fake.query

        def blocked_query(cmd):
            if cmd == 'OUTP:SER?':
                reached.set()
                release.wait(2.)  # Simulates a slow in-flight SCPI exchange.
            return original_query(cmd)

        self.psu.start_profile_monitor(interval_s=.1)
        self.fake.query = blocked_query
        try:
            self.assertTrue(reached.wait(2.0))
            started = time.monotonic()
            with self.assertRaisesRegex(SafetyError, 'did not stop'):
                self.psu.stop_profile_monitor(timeout_s=.02)
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(self.psu.profile_monitor_running)  # stop timed out
        finally:
            release.set()
            self.psu.stop_profile_monitor()
        self.assertTrue(self.psu.armed)
