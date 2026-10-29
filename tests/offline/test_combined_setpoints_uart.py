"""R3.5.60: Series/Parallel aggregate setpoints through production UART.

Scripted digital replies represent explicitly expected SCPI text, not the
firmware's electrical coupling or current sharing; H09/H10 remain independent
hardware evidence. No real instruments, no output enables.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import ChannelProfile, Keithley2231A, ParallelProfile, SafetyError, SeriesProfile
from support.virtual_uart import UARTPeer, open_test_transport


def series_profile():
    return SeriesProfile('bounded series', (2., 3.), 4.5, .3, 2., .1,
                         ChannelProfile(4., .5, 2., .3))


def parallel_profile(current=.3):
    return ParallelProfile('bounded parallel', 3., 4.0 if current > 3 else .3,
                           2., current, ChannelProfile(4., .5, 1., .1),
                           lock_front_panel=False)


# These constants are independent test expectations, not a simulated combined
# limit computed from the configured channel values.
SERIES_READBACK = {('series', 1, 'VOLT:LIM?'): '5.00'}
PARALLEL_READBACK = {('parallel', 1, 'VOLT:LIM:STAT?'): '0',
                     ('parallel', 2, 'VOLT:LIM:STAT?'): '0'}


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudo-terminal')
class CombinedSetpointsUARTTests(unittest.TestCase):
    def device(self, readbacks):
        peer = UARTPeer(profile_registers=True, mode_channel_overrides=readbacks)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.18)
        self.addCleanup(transport.close)
        return Keithley2231A(transport), peer

    def assert_wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)

    def test_series_bad_aggregate_limit_readback_fails_closed(self):
        dev, peer = self.device({('series', 1, 'VOLT:LIM?'): '4.99'})
        with self.assertRaisesRegex(SafetyError, 'Series voltage-limit register'):
            dev.apply_profile(series_profile(), confirmed_wiring='series')
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertIn('OUTP:SER 1', peer.rx)
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertIsNone(dev.profile)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assert_wire(peer)

    def test_parallel_aggregate_4a_is_written_only_after_mode_selection(self):
        dev, peer = self.device(PARALLEL_READBACK)
        dev.apply_profile(parallel_profile(4.), confirmed_wiring='parallel')
        self.assertTrue(dev.armed)
        self.assertFalse(dev.faulted)
        self.assertEqual(peer.mode_registers['OUTP:PARALLEL'], 1)
        self.assertFalse(any(peer.off_by_channel.values()))
        mode = peer.rx.index('OUTP:PARALLEL 1')
        aggregate = peer.rx.index('CURR 4.000')
        self.assertLess(mode, aggregate)
        self.assertNotIn('CURR 4.000', peer.rx[:mode])
        self.assertEqual(peer.channel_registers[1]['CURR'], '4.000')
        self.assert_wire(peer)

    def test_ch3_limit_drift_trips_in_both_combined_modes(self):
        for mode, make, replies in (
                ('series', series_profile, SERIES_READBACK),
                ('parallel', parallel_profile, PARALLEL_READBACK)):
            with self.subTest(mode=mode):
                dev, peer = self.device(replies)
                dev.apply_profile(make(), confirmed_wiring=mode)
                self.assertTrue(dev.armed)
                peer.channel_registers[3]['VOLT:LIM'] = '3.99'
                with self.assertRaisesRegex(SafetyError, 'CH3 hardware voltage limit changed'):
                    dev.verify_profile()
                self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
                self.assertFalse(dev.armed)
                self.assertTrue(dev.faulted)
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assert_wire(peer)

    def test_invalid_combined_script_is_rejected_without_transport(self):
        with self.assertRaises(ValueError):
            UARTPeer(profile_registers=True,
                     mode_channel_overrides={('parallel', 1, 'MEAS:CURR?'): '4.0'})


if __name__ == '__main__':
    unittest.main()
