"""Active-profile monitoring through the real driver and a POSIX UART peer.

Covers the actual apply_profile(), worker query and fault shutdown path;
no direct FakeTransport calls or real power supply are involved.
"""
from __future__ import annotations

import os
import time
import unittest

from keithley2231a import Keithley2231A, SafetyError
from test_driver import profile
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class ProfileMonitorUARTTests(unittest.TestCase):
    def setUp(self):
        self.peer = UARTPeer(profile_registers=True)
        self.addCleanup(self.peer.close)
        self.transport = open_test_transport(self.peer, timeout=.14)
        self.addCleanup(self.transport.close)
        self.dev = Keithley2231A(self.transport)
        self.addCleanup(self.dev.stop_profile_monitor)
        self.dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(self.dev.armed)
        self.assertEqual(self.peer.unsupported, [])
        self.assertTrue(self.peer.await_received(76), "apply_profile final write missing")
        self.baseline = list(self.peer.rx)

    def until(self, predicate, timeout=2.):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            if predicate():
                return
            time.sleep(.005)
        self.fail('profile monitor failed to reach expected state')

    def check_wire(self):
        self.assertEqual(self.peer.unsupported, [])
        self.assertEqual(self.peer.rx_bytes,
                         [(command+'\r\n').encode('ascii') for command in self.peer.rx])
        self.assertNotIn('OUTP 1', self.peer.rx)

    def test_verify_every_s_does_not_start_implicit_worker(self):
        self.assertFalse(self.dev.profile_monitor_running)
        time.sleep(.04)
        self.assertEqual(self.peer.rx, self.baseline)
        self.assertIsNone(self.dev.profile_monitor_error)
        self.check_wire()

    def test_monitor_start_and_stop_query_via_driver(self):
        self.dev.start_profile_monitor(interval_s=.3)
        self.assertTrue(self.dev.profile_monitor_running)
        self.assertIsNone(self.dev.profile_monitor_error)
        self.assertGreater(len(self.peer.rx), len(self.baseline))
        wire = list(self.peer.rx)
        with self.assertRaises(SafetyError):
            self.dev.start_profile_monitor(interval_s=.3)
        self.assertEqual(self.peer.rx, wire)
        self.dev.stop_profile_monitor()
        self.assertFalse(self.dev.profile_monitor_running)
        self.assertTrue(self.dev.armed)
        self.assertEqual(self.peer.rx, wire)
        self.check_wire()

    def detect_drift(self, mutation):
        self.dev.start_profile_monitor(interval_s=.1)
        mutation(self.peer)
        self.until(lambda: self.dev.profile_monitor_error is not None)
        self.dev.stop_profile_monitor()
        self.assertFalse(self.dev.armed)
        self.assertTrue(self.dev.faulted)
        self.assertIn('Profile integrity check failed', self.dev.profile_monitor_error)
        self.assertIn('output OFF confirmed', self.dev.profile_monitor_error)
        self.assertEqual(self.peer.rx.count('OUTP 0'), 2)
        self.check_wire()

    def test_worker_voltage_drift_confirms_output_off(self):
        self.detect_drift(lambda p: p.channel_registers[1].__setitem__('VOLT', '1.11'))

    def test_worker_current_drift_confirms_output_off(self):
        self.detect_drift(lambda p: p.channel_registers[1].__setitem__('CURR', '0.151'))

    def test_worker_hardware_limit_drift_confirms_output_off(self):
        self.detect_drift(lambda p: p.channel_registers[1].__setitem__('VOLT:LIM:STAT', '0'))

    def test_worker_topology_drift_confirms_output_off(self):
        self.detect_drift(lambda p: p.overrides.__setitem__('OUTP:TRAC?', '1'))

    def test_lost_monitor_reply_never_attempts_shutdown_on_uncertain_link(self):
        self.dev.output(1, True)
        self.assertEqual(self.peer.off_by_channel[1], 1)
        self.dev.start_profile_monitor(interval_s=.1)
        before_off = self.peer.rx.count('OUTP 0')
        self.peer.drop = 'VOLT:LIM?'
        self.until(lambda: self.dev.profile_monitor_error is not None)
        self.dev.stop_profile_monitor()
        self.assertIn('UNKNOWN OUTPUT STATE', self.dev.profile_monitor_error)
        self.assertTrue(self.dev.faulted)
        self.assertFalse(self.dev.armed)
        self.assertEqual(self.peer.off_by_channel[1], 1)
        self.assertEqual(self.peer.rx.count('OUTP 0'), before_off)
        last = list(self.peer.rx)
        with self.assertRaises(SafetyError):
            self.dev.verify_profile()
        self.assertEqual(self.peer.rx, last)
        self.check_wire()

    def test_physical_off_state_only_in_peer_fixture(self):
        # No independent source of physical certainty is claimed by PTY tests.
        self.assertFalse(any(self.peer.off_by_channel.values()))
        self.assertEqual(self.peer.unsupported, [])
        self.check_wire()


if __name__ == '__main__':
    unittest.main()
