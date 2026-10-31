"""R3.5.62: remaining driver protocol guards on production Keithley2231A over PTY.

The peer returns scripted SCPI bytes, not electrical Series/Parallel behavior.
Direct transport-boundary injection is limited to responses impossible on a
serial byte stream (non-string return) and BaseException interruption.
"""
from __future__ import annotations
from examples.acquisition import Acquisition

import os
import unittest
from unittest.mock import patch

from keithley2231a import (InstrumentError, Keithley2231A, LimitsError, SafetyError, TransportError)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudo-terminal')
class RemainingDriverGuardsUART(unittest.TestCase):
    def device(self, *, applied=True, debug=False, **peer_options):
        peer = UARTPeer(profile_registers=True, **peer_options)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.15)
        self.addCleanup(wire.close)
        device = Keithley2231A(wire, debug=debug)
        if applied:
            device.apply_profile(profile(), confirmed_wiring='independent')
            self.assertTrue(device.armed)
        return device, peer, wire

    def assert_wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(command + '\r\n').encode('ascii')
                                         for command in peer.rx])

    def test_debug_bypass_does_not_relax_public_profile_limits(self):
        dev, peer, _ = self.device(debug=True, fault_after='VOLT 3.31')
        before = list(peer.rx)
        with self.assertRaises(LimitsError):
            dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(peer.rx, before)
        dev.debug_bypass_profile = True
        with self.assertRaises(LimitsError):
            dev.set_channel(3, voltage=3.31)
        self.assertEqual(peer.rx, before)
        # Bypass is restricted to explicit diagnostic dispatch, not public API.
        dev.diagnostic_write('INST:NSEL 3')
        dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(dev.diagnostic_query('SYST:ERR?'),
                         '120,"Injected SCPI error"')
        self.assertIn('VOLT 3.31', peer.rx)
        dev.debug_bypass_profile = False
        before = list(peer.rx)
        with self.assertRaises(LimitsError):
            dev.diagnostic_write('VOLT 3.31')
        self.assertEqual(peer.rx, before)
        self.assert_wire(peer)

    def test_debug_is_explicit_and_serial_mismatch_stops_write(self):
        normal, peer, _ = self.device(applied=False)
        with self.assertRaises(SafetyError):
            normal.debug_bypass_profile = True
        self.assertEqual(peer.rx, [])

        _, peer2, wire2 = self.device(applied=False)
        wrong = Keithley2231A(wire2, debug=True, debug_bypass_profile=True,
                              expected_serial='OTHER')
        wrong.diagnostic_write('SYST:REM')
        with self.assertRaises(InstrumentError):
            wrong.diagnostic_write('VOLT 1.00')
        self.assertNotIn('VOLT 1.00', peer2.rx)
        self.assertTrue(wrong.faulted)
        self.assert_wire(peer2)

    def test_non_string_transport_reply_latches_uncertainty_without_off(self):
        dev, peer, wire = self.device()
        dev.output(1, True)
        original = wire.query
        def invalid_after_wire(command):
            response = original(command)
            return None if command == 'SYST:VERS?' else response
        before_off = peer.rx.count('OUTP 0')
        with patch.object(wire, 'query', side_effect=invalid_after_wire):
            with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
                dev.status()
        self.assertTrue(peer.await_command('SYST:VERS?'))
        self.assertEqual(peer.rx[-1], 'SYST:VERS?')
        self.assertEqual(peer.rx.count('OUTP 0'), before_off)
        self.assertTrue(peer.off_by_channel[1])
        self.assertTrue(dev.faulted)
        self.assert_wire(peer)

    def test_keyboard_interrupt_during_query_latches_uncertain_link(self):
        for debug in (False, True):
            with self.subTest(debug=debug):
                dev, peer, wire = self.device(applied=False, debug=debug)
                original = wire.query
                def interrupt_after_wire(command):
                    response = original(command)
                    if command == 'CHAN:OUTP?':
                        raise KeyboardInterrupt()
                    return response
                with patch.object(wire, 'query', side_effect=interrupt_after_wire):
                    with self.assertRaises(KeyboardInterrupt):
                        dev.output_states()
                self.assertTrue(peer.await_command('CHAN:OUTP?'))
                before = list(peer.rx)
                with self.assertRaises(TransportError):
                    dev.emergency_off()
                self.assertEqual(peer.rx, before)
                self.assertTrue(dev.faulted)
                self.assert_wire(peer)

    def test_confirmed_profile_drift_requests_verified_off(self):
        dev, peer, _ = self.device()
        peer.channel_registers[1]['VOLT:LIM'] = '0.00'
        with self.assertRaises(SafetyError):
            dev.verify_profile()
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assert_wire(peer)

    def test_lost_limit_reply_never_attempts_unverified_shutdown(self):
        dev, peer, _ = self.device()
        dev.output(1, True)
        off_count = peer.rx.count('OUTP 0')
        peer.drop = 'VOLT:LIM?'
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.verify_profile()
        self.assertEqual(peer.rx.count('OUTP 0'), off_count)
        self.assertTrue(peer.off_by_channel[1])
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        before = list(peer.rx)
        with self.assertRaises((SafetyError, TransportError)):
            dev.emergency_off()
        self.assertEqual(peer.rx, before)
        self.assert_wire(peer)

    def test_status_reply_loss_disarms_and_denies_new_output(self):
        dev, peer, _ = self.device()
        peer.drop = 'SYST:VERS?'
        with self.assertRaises(SafetyError):
            dev.status()
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.output(1, True)
        self.assertEqual(peer.rx, before)
        self.assert_wire(peer)

    def test_output_bool_rejected_without_io(self):
        dev, peer, _ = self.device()
        before = list(peer.rx)
        with self.assertRaises(ValueError):
            dev.output(1, 'OFF')
        self.assertEqual(peer.rx, before)
        self.assert_wire(peer)

    def test_identity_model_mismatch_and_channel_type_guard(self):
        dev, peer, _ = self.device()
        for channel in (1.0, True, '1'):
            with self.subTest(channel=channel):
                before = list(peer.rx)
                with self.assertRaises(ValueError):
                    dev.output(channel, True)
                self.assertEqual(peer.rx, before)
        peer.overrides['*IDN?'] = 'Keithley instruments, 2231A-30-30, LAB-UNIT-001, 1.04-1.04'
        with self.assertRaises(InstrumentError):
            dev.identity()
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assert_wire(peer)

    def test_close_verifies_off_then_closes_connection(self):
        dev, peer, wire = self.device()
        dev.output(1, True)
        local_before = peer.rx.count('SYST:LOC')
        dev.close()
        # close() sends final non-query UART writes. Wait until the peer
        # receives them before taking the no-more-I/O snapshot: otherwise
        # its reader thread can append already-sent bytes after the snapshot.
        self.assertTrue(peer.await_command('SYST:LOC', occurrences=local_before + 1,
                                           timeout=.75))
        # Transport's public contract is close(), NOT a .closed attribute.
        # Verify closure by refusing another wire query for both the
        # production pyserial transport and the POSIX PTY fallback.
        before = list(peer.rx)
        with self.assertRaises((OSError, TransportError)):
            wire.query('SYST:VERS?')
        self.assertEqual(peer.rx, before)
        self.assertFalse(peer.off_by_channel[1])
        self.assertFalse(dev.armed)
        self.assertIn('OUTP 0', peer.rx)
        self.assert_wire(peer)

    def test_acquisition_clock_does_not_sample_after_duration(self):
        class ClockStop:
            now = 0.
            def is_set(self): return False
            def wait(self, seconds):
                self.now += seconds
                return False

        dev, peer, _ = self.device()
        stop = ClockStop()
        acquisition = Acquisition(dev, interval_s=2.)
        with patch('examples.acquisition.time.monotonic', lambda: stop.now):
            acquisition.run(duration_s=1., stop_event=stop)
        self.assertEqual(acquisition.stats[1].voltage.count, 1)
        self.assertEqual(stop.now, 1.)
        self.assert_wire(peer)
