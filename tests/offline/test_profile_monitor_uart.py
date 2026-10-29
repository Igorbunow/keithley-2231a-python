"""Active-profile monitoring through the real driver and a POSIX UART peer.

Covers the actual apply_profile(), worker query and fault shutdown path;
no direct FakeTransport calls or real power supply are involved.
"""
from __future__ import annotations

import os
import time
import unittest
import threading
from unittest.mock import patch

from keithley2231a import (ChannelProfile, Keithley2231A, ParallelProfile,
                          SafetyError)
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


    def test_interval_validation_and_explicit_first_verification(self):
        original = self.dev.verify_profile
        verified = threading.Event()

        def observed_verify():
            original()
            verified.set()

        self.dev.verify_profile = observed_verify
        for interval in (0, -1, float('nan'), True, .01):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    self.dev.start_profile_monitor(interval_s=interval)
                self.assertFalse(self.dev.profile_monitor_running)
        with self.assertRaises(ValueError):
            self.dev.stop_profile_monitor(timeout_s=-1)
        self.assertFalse(verified.is_set())
        self.dev.start_profile_monitor(interval_s=.3)
        self.assertTrue(verified.is_set())
        self.assertTrue(self.dev.profile_monitor_running)
        self.dev.stop_profile_monitor()
        self.check_wire()

    def test_monitor_join_precedes_profile_replacement(self):
        self.dev.start_profile_monitor(interval_s=.3)
        self.assertTrue(self.dev.profile_monitor_running)
        self.dev.emergency_off()
        # The worker may have terminated promptly after emergency_off. If it
        # has not, profile replacement must refuse until it is joined.
        if self.dev.profile_monitor_running:
            with self.assertRaises(SafetyError):
                self.dev.apply_profile(profile(), confirmed_wiring='independent')
        self.dev.stop_profile_monitor()
        self.dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(self.dev.armed)
        self.check_wire()

    def test_close_stops_worker_and_verifies_off_before_transport_close(self):
        self.dev.start_profile_monitor(interval_s=.3)
        self.dev.close()
        self.assertFalse(self.dev.profile_monitor_running)
        self.assertFalse(self.dev.armed)
        self.assertTrue(self.peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(self.peer.off_by_channel.values()))
        # dev.close() closed the real serial or fallback PTY; inspection of
        # pyserial's non-public handle belongs to the concrete transport test.
        self.check_wire()

    def test_unarmed_or_faulted_monitor_start_emits_no_scpi(self):
        unused = Keithley2231A(self.transport)
        before = list(self.peer.rx)
        with self.assertRaises(SafetyError):
            unused.start_profile_monitor(interval_s=.3)
        self.assertEqual(self.peer.rx, before)
        self.dev.emergency_off()
        before = list(self.peer.rx)
        with self.assertRaises(SafetyError):
            self.dev.start_profile_monitor(interval_s=.3)
        self.assertEqual(self.peer.rx, before)
        self.check_wire()

    def test_parallel_worker_detects_ch3_limit_drift(self):
        scripts = {('parallel', 1, 'VOLT:LIM:STAT?'): '0',
                   ('parallel', 2, 'VOLT:LIM:STAT?'): '0'}
        peer = UARTPeer(profile_registers=True, mode_channel_overrides=scripts)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.18)
        self.addCleanup(wire.close)
        dev = Keithley2231A(wire)
        self.addCleanup(dev.stop_profile_monitor)
        configured = ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                                     ChannelProfile(4.0, .5, 1.0, .1))
        dev.apply_profile(configured, confirmed_wiring='parallel')
        dev.start_profile_monitor(interval_s=.1)
        peer.channel_registers[3]['VOLT:LIM'] = '3.99'
        self.until(lambda: dev.profile_monitor_error is not None)
        dev.stop_profile_monitor()
        self.assertIn('CH3 hardware voltage limit changed', dev.profile_monitor_error)
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertEqual(peer.unsupported, [])

    def test_stop_timeout_does_not_block_on_scpi_lock(self):
        reached = threading.Event()
        release = threading.Event()
        original_query = self.transport.query

        def blocked_query(command):
            if command == 'OUTP:SER?':
                reached.set()
                release.wait(1.5)
            return original_query(command)

        self.dev.start_profile_monitor(interval_s=.1)
        with patch.object(self.transport, 'query', side_effect=blocked_query):
            try:
                self.assertTrue(reached.wait(2.0))
                started = time.monotonic()
                with self.assertRaisesRegex(SafetyError, 'did not stop'):
                    self.dev.stop_profile_monitor(timeout_s=.02)
                self.assertLess(time.monotonic() - started, .5)
                self.assertTrue(self.dev.profile_monitor_running)
            finally:
                release.set()
                self.dev.stop_profile_monitor()
        self.assertTrue(self.dev.armed)
        self.check_wire()


if __name__ == '__main__':
    unittest.main()
