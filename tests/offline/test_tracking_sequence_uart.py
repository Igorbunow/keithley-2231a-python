"""R3.5.58: host sequencing and tracking/timer SCPI through the real driver.

A POSIX PTY provides only explicitly scoped digital command/readback fixtures.
It cannot prove instrument-side channel coupling, timer expiry, wiring safety,
or any real electrical operation; those require the existing H05/H06/H08 tests.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class TrackingAndSequenceUARTTests(unittest.TestCase):
    def device(self, wiring='independent'):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.14)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        dev.apply_profile(profile(wiring), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertFalse(peer.unsupported)
        return dev, peer

    def check_wire(self, peer):
        self.assertTrue(peer.await_received(len(peer.rx)))
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_independent_sequence_and_timer_on_real_dispatch_path(self):
        dev, peer = self.device()
        baseline = len(peer.rx)
        dev.sequence([(0, 1, True), (0, 2, True), (0, 1, False)])
        self.assertEqual(peer.off_by_channel, {1: 0, 2: 1, 3: 0})
        self.assertEqual([cmd for cmd in peer.rx[baseline:] if cmd.startswith('CHAN:OUTP ')],
                         ['CHAN:OUTP 1', 'CHAN:OUTP 1', 'CHAN:OUTP 0'])
        dev.timer(2, 2.2)
        self.assertEqual(peer.channel_registers[2]['OUTP:TIM:DEL'], '2.2')
        self.assertEqual(peer.channel_registers[2]['OUTP:TIM:STAT'], '1')
        dev.timer(2, None)
        self.assertEqual(peer.channel_registers[2]['OUTP:TIM:STAT'], '0')
        self.assertTrue(dev.armed)
        self.check_wire(peer)

    def test_tracking_output_requires_explicit_global_confirmation(self):
        dev, peer = self.device('tracking')
        self.assertEqual(peer.mode_registers['OUTP:TRAC'], 1)
        initial = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.set_channel(1, voltage=2)
        with self.assertRaisesRegex(SafetyError, 'Confirm ALL connected loads'):
            dev.combined_outputs(True)
        self.assertEqual(peer.rx, initial)  # both refusals must predate I/O
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        self.assertEqual(peer.off_by_channel, {1: 1, 2: 1, 3: 1})
        self.assertEqual(dev.output_states(), {1: True, 2: True, 3: True})
        dev.combined_outputs(False)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        self.assertTrue(dev.armed)
        self.check_wire(peer)

    def test_tracking_timers_validate_pair_and_require_outputs_off(self):
        independent, old_peer = self.device()
        baseline = list(old_peer.rx)
        with self.assertRaises(SafetyError):
            independent.tracking_timers(3, 5)
        self.assertEqual(old_peer.rx, baseline)
        self.check_wire(old_peer)

        dev, peer = self.device('tracking')
        for timers in ((3, float('nan')), (3, .01), (True, 5), (3, 5.01)):
            with self.subTest(timers=timers):
                before = list(peer.rx)
                with self.assertRaises(ValueError):
                    dev.tracking_timers(*timers)
                self.assertEqual(peer.rx, before)
        dev.tracking_timers(3, 5)
        for ch, delay in ((1, '3.0'), (2, '5.0')):
            self.assertEqual(peer.channel_registers[ch]['OUTP:TIM:DEL'], delay)
            self.assertEqual(peer.channel_registers[ch]['OUTP:TIM:STAT'], '1')
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        before = list(peer.rx)
        with self.assertRaisesRegex(SafetyError, 'only with all outputs OFF'):
            dev.tracking_timers(None, None)
        self.assertFalse(any(cmd.startswith('OUTP:TIM:') and not cmd.endswith('?')
                             for cmd in peer.rx[len(before):]))
        dev.combined_outputs(False)
        dev.tracking_timers(None, None)
        self.assertEqual(peer.channel_registers[1]['OUTP:TIM:STAT'], '0')
        self.assertEqual(peer.channel_registers[2]['OUTP:TIM:STAT'], '0')
        self.assertTrue(dev.armed)
        self.check_wire(peer)

    def test_tracking_timer_readback_mismatch_trips_and_confirms_off(self):
        dev, peer = self.device('tracking')
        peer.overrides['OUTP:TIM:DEL?'] = '3.1'  # valid reply, wrong value
        with self.assertRaisesRegex(SafetyError, 'timer delay readback mismatch'):
            dev.tracking_timers(3, 5)
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)

    def test_tracking_timer_lost_uart_reply_does_not_claim_shutdown(self):
        dev, peer = self.device('tracking')
        peer.drop = 'OUTP:TIM:DEL?'
        before_off = peer.rx.count('OUTP 0')
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.tracking_timers(3, 5)
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertEqual(peer.rx.count('OUTP 0'), before_off)
        self.check_wire(peer)

    def test_tracking_off_waits_for_delayed_global_readback(self):
        dev, peer = self.device('tracking')
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        # Two stale high readings, then OFF; only digital readback is scripted.
        peer.response_sequences['OUTP:STAT:ALL?'] = ['1', '1', '0']
        baseline = peer.rx.count('OUTP:STAT:ALL?')
        dev.combined_outputs(False)
        self.assertGreaterEqual(peer.rx.count('OUTP:STAT:ALL?') - baseline, 3)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        self.assertTrue(dev.armed)
        self.assertFalse(dev.faulted)
        self.check_wire(peer)


if __name__ == '__main__':
    unittest.main()
