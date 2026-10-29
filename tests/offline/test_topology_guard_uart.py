"""R3.5.57: physical-wiring gates and shared abort over the production UART.

The peer substitutes only a POSIX serial endpoint. It intentionally does not
model the electrical Series/Parallel topology; the combined-mode writes below
are followed immediately by an injected failure before any combined readback.
Successful Series/Parallel operation remains the province of hardware evidence.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import (ChannelProfile, Keithley2231A, ParallelProfile,
                          Profile, SafetyError, SeriesProfile)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


def combined_profiles():
    return (
        ('series', SeriesProfile('series', (2., 3.), 4.5, .3, 2., .1,
                                 ChannelProfile(4., .5, 2., .3))),
        ('parallel', ParallelProfile('parallel', 3., .3, 2., .1,
                                     ChannelProfile(4., .5, 1., .1))),
    )


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class TopologyGuardUARTTests(unittest.TestCase):
    def device(self, *, registers=False, **fixture):
        peer = UARTPeer(profile_registers=registers, **fixture)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.13)
        self.addCleanup(transport.close)
        return Keithley2231A(transport), peer

    def wire(self, peer):
        self.assertTrue(peer.await_received(len(peer.rx)))
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])
        self.assertEqual(peer.unsupported, [])
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)

    def test_independent_and_tracking_require_no_jumper_ack_before_any_wire(self):
        for wiring in ('independent', 'tracking'):
            for confirmation in (None, 'series', 'parallel', 'tracking', ''):
                with self.subTest(wiring=wiring, confirmation=confirmation):
                    dev, peer = self.device()
                    with self.assertRaisesRegex(SafetyError, 'NO external Series/Parallel jumpers'):
                        dev.apply_profile(profile(wiring), confirmed_wiring=confirmation)
                    self.assertEqual(peer.rx, [])
                    self.assertFalse(dev.faulted)
                    self.assertFalse(dev.armed)
                    self.assertIsNone(dev.profile)
                    self.wire(peer)

    def test_combined_profiles_require_matching_physical_wiring_before_any_wire(self):
        for wiring, setting in combined_profiles():
            for confirmation in (None, 'independent', 'tracking',
                                 'parallel' if wiring == 'series' else 'series', ''):
                with self.subTest(wiring=wiring, confirmation=confirmation):
                    dev, peer = self.device()
                    with self.assertRaisesRegex(SafetyError, 'Confirm physical'):
                        dev.apply_profile(setting, confirmed_wiring=confirmation)
                    self.assertEqual(peer.rx, [])
                    self.assertFalse(dev.armed)
                    self.assertFalse(dev.faulted)
                    self.assertIsNone(dev.profile)
                    self.wire(peer)

    def test_all_three_topologies_use_shared_abort_on_post_configuration_fault(self):
        cases = [('independent', profile(), 'independent')]
        cases.extend((wiring, setting, wiring) for wiring, setting in combined_profiles())
        for wiring, setting, confirmation in cases:
            with self.subTest(wiring=wiring):
                dev, peer = self.device(registers=True)
                # Inject at the SAME production driver integrity hook, after
                # the configuration writes, without emulating combined hardware.
                def fail_wiring():
                    raise SafetyError('injected profile validation failure')

                dev._check_wiring_mode = fail_wiring
                with self.assertRaisesRegex(SafetyError, 'injected profile validation failure'):
                    dev.apply_profile(setting, confirmed_wiring=confirmation)
                # The last OFF write is not a query, so the peer may still
                # be processing it when apply_profile() returns.
                self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
                self.assertEqual(peer.rx[-1], 'OUTP 0')
                self.assertEqual(peer.rx.count('OUTP 0'), 2)  # preflight + abort
                self.assertFalse(dev.armed)
                self.assertTrue(dev.faulted)
                self.assertIsNone(dev.profile)
                self.assertFalse(any(peer.off_by_channel.values()))
                if wiring != 'independent':
                    self.assertIn('OUTP:' + ('SER' if wiring == 'series' else 'PARALLEL') + ' 1', peer.rx)
                self.wire(peer)

    def test_apply_clears_inherited_timers_before_arming(self):
        dev, peer = self.device(registers=True)
        for ch in (1, 2, 3):
            peer.channel_registers[ch]['OUTP:TIM:STAT'] = '1'
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertFalse(dev.faulted)
        self.assertEqual(peer.rx.count('OUTP:TIM:STAT 0'), 3)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertTrue(all(peer.channel_registers[ch]['OUTP:TIM:STAT'] == '0'
                            for ch in (1, 2, 3)))
        self.wire(peer)

    def test_fixed_power_only_setpoints_trip_on_one_step_tampering(self):
        for key, readback in (('VOLT', '1.01'), ('CURR', '0.101')):
            with self.subTest(key=key):
                dev, peer = self.device(registers=True)
                base = profile()
                dev.apply_profile(Profile('fixed', base.channels, access='power_only'),
                                  confirmed_wiring='independent')
                before = list(peer.rx)
                peer.channel_registers[1][key] = readback
                with self.assertRaisesRegex(SafetyError, 'authorized setpoints changed'):
                    dev.verify_profile()
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertEqual(peer.rx.count('OUTP 0'), 2)
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assertGreater(len(peer.rx), len(before))
                self.wire(peer)

    def test_updated_authorized_setpoint_is_checked_through_uart(self):
        dev, peer = self.device(registers=True)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.set_channel(1, voltage=2.5, current=.15)
        dev.verify_profile()
        self.assertEqual(peer.channel_registers[1]['VOLT'], '2.50')
        self.assertEqual(peer.channel_registers[1]['CURR'], '0.150')
        peer.channel_registers[1]['VOLT'] = '2.49'
        with self.assertRaisesRegex(SafetyError, 'authorized setpoints changed'):
            dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.wire(peer)

    def test_external_output_on_is_not_authorized_by_profile(self):
        dev, peer = self.device(registers=True)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        peer.off_by_channel[3] = 1  # external front-panel change, not API ON
        before = list(peer.rx)
        with self.assertRaisesRegex(SafetyError, 'output OFF confirmed by readback'):
            dev.verify_profile()
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertNotIn('CHAN:OUTP 1', peer.rx[len(before):])
        self.wire(peer)

    def test_selection_alias_is_detected_without_any_output_enable(self):
        dev, peer = self.device(response_overrides={'INST:NSEL?': '1'})
        with self.assertRaisesRegex(SafetyError, 'selected channel is 1'):
            dev.output_states()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertEqual(peer.rx[:3], ['*IDN?', 'SYST:REM', 'OUTP:SER?'])
        self.assertIn('INST:NSEL 2', peer.rx)
        self.assertEqual(peer.rx.count('CHAN:OUTP?'), 1)
        self.assertNotIn('OUTP 0', peer.rx)  # read-only inspection, not shutdown
        self.wire(peer)


if __name__ == '__main__':
    unittest.main()
