"""R3.5.59: tracking voltage safety and SCPI checks on the production driver.

The peer returns explicitly scripted channel readbacks. It deliberately does NOT
implement voltage coupling, which is real-instrument firmware behavior qualified
separately by H08. Physical output and wiring properties are not tested here.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import ChannelProfile, Keithley2231A, LimitsError, Profile, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class TrackingVoltageUARTTests(unittest.TestCase):
    def device(self, ratio):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.18)
        self.addCleanup(transport.close)
        device = Keithley2231A(transport)
        configured = Profile('tracking ratio', (
            ChannelProfile(2, .3, 1, .1),
            ChannelProfile(3, .4, ratio, .2),
            ChannelProfile(4, .5, .5, .3)), wiring='tracking')
        device.apply_profile(configured, confirmed_wiring='independent')
        self.assertEqual(peer.mode_registers['OUTP:TRAC'], 1)
        self.assertTrue(device.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(peer.unsupported, [])
        return device, peer

    def assert_uart(self, peer):
        self.assertFalse(peer.unsupported)
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_tracking_rejects_invalid_linked_setpoints_before_io(self):
        for ratio in (1, 2):
            with self.subTest(ratio=ratio):
                device, peer = self.device(ratio)
                for channel, voltage in (
                        (1, 1.6 if ratio == 2 else 2.01),
                        (1, 0), (1, float('nan')), (1, .001),
                        (3, 1), (2, .01 if ratio == 2 else 3.01)):
                    with self.subTest(channel=channel, voltage=voltage):
                        before = list(peer.rx)
                        with self.assertRaises((LimitsError, SafetyError)):
                            device.set_tracking_voltage(channel, voltage)
                        self.assertEqual(peer.rx, before)
                        self.assertTrue(device.armed)
                self.assert_uart(peer)

    def test_tracking_setpoints_check_both_scripted_readbacks(self):
        for ratio in (1, 2):
            with self.subTest(ratio=ratio):
                device, peer = self.device(ratio)
                # The first reply is pre-write verify_profile; the next is
                # the fixture's *recorded expectation*, not derived coupling.
                peer.channel_response_sequences[2, 'VOLT?'] = [f'{ratio:.2f}', f'{1.2 * ratio:.2f}']
                before = len(peer.rx)
                device.set_tracking_voltage(1, 1.2)
                self.assertTrue(device.armed)
                self.assertIn('VOLT 1.20', peer.rx[before:])
                self.assertEqual(peer.channel_registers[1]['VOLT'], '1.20')
                self.assertEqual(peer.channel_registers[2]['VOLT'], f'{ratio:.2f}')
                # Reverse direction: CH2 written, CH1 pre/post response is
                # explicitly supplied to test that the driver checks both.
                peer.channel_response_sequences[1, 'VOLT?'] = ['1.20', '1.00']
                peer.channel_response_sequences[2, 'VOLT?'] = [f'{1.2 * ratio:.2f}', f'{ratio:.2f}']
                before = len(peer.rx)
                device.set_tracking_voltage(2, ratio)
                self.assertTrue(device.armed)
                self.assertIn(f'VOLT {ratio:.2f}', peer.rx[before:])
                device.verify_profile()
                self.assertTrue(device.armed)
                self.assertFalse(device.faulted)
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assert_uart(peer)

    def test_tracking_mismatched_linked_readback_trips_and_confirms_off(self):
        for ratio in (1, 2):
            with self.subTest(ratio=ratio):
                device, peer = self.device(ratio)
                peer.channel_response_sequences[2, 'VOLT?'] = [f'{ratio:.2f}', f'{1.2 * ratio + .01:.2f}']
                before = peer.rx.count('OUTP 0')
                with self.assertRaisesRegex(SafetyError, 'Tracking voltage operation failed'):
                    device.set_tracking_voltage(1, 1.2)
                self.assertTrue(peer.await_command('OUTP 0', occurrences=before + 1))
                self.assertFalse(device.armed)
                self.assertTrue(device.faulted)
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assertNotIn('OUTP 1', peer.rx)
                self.assert_uart(peer)


if __name__ == '__main__':
    unittest.main()
