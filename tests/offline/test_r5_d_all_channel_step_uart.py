"""Channel-neutral powered STEP qualification over a virtual UART, never real hardware."""
from __future__ import annotations
import os
import unittest

from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'Virtual serial test requires POSIX')
class AllChannelStepUART(unittest.TestCase):
    def setup_channel(self, ch: int, mode: str):
        # Deliberately small and independent profiles for every channel.
        overrides = ({f'STAT:OPER:INST:ISUM{ch}:COND?': '2'}
                     if mode == 'CC' else {})
        peer = UARTPeer(profile_registers=True, response_overrides=overrides)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.75)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        dev.apply_profile(Profile('STEP all-channel virtual profile', (
            ChannelProfile(1.50, .120, 1.00, .050),
            ChannelProfile(1.50, .120, 1.00, .050),
            ChannelProfile(1.50, .120, 1.00, .050),
        )), confirmed_wiring='independent')
        return dev, peer

    def test_per_channel_cv_and_cc_native_up_down(self):
        for ch in (1, 2, 3):
            for mode, kind, step in (('CV', 'voltage', .010), ('CC', 'current', .005)):
                with self.subTest(ch=ch, mode=mode):
                    dev, peer = self.setup_channel(ch, mode)
                    with dev.powered_step_qualification(ch, kind, step, 'up',
                            expected_mode=mode, external_load_confirmed=True,
                            sequence=('up', 'up', 'down', 'down')):
                        dev.output(ch, True)
                        previous = 1.00 if kind == 'voltage' else .050
                        for direction in ('up', 'up', 'down', 'down'):
                            previous += step * (1 if direction == 'up' else -1)
                            self.assertAlmostEqual(dev.step_source(ch, kind, direction), previous)
                        self.assertEqual(dev._output_states(),
                                         {i: i == ch for i in (1, 2, 3)})
                        with self.assertRaises(SafetyError):
                            dev.output(1 if ch != 1 else 2, True)
                    self.assertEqual(dev._output_states(), {1: False, 2: False, 3: False})
                    self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
                    self.assertEqual(peer.rx.count('VOLT:UP' if kind == 'voltage' else 'CURR:UP'), 2)
                    self.assertEqual(peer.rx.count('VOLT:DOWN' if kind == 'voltage' else 'CURR:DOWN'), 2)
                    self.assertEqual(peer.unsupported, [])

    def test_wrong_channel_mutation_rejected_without_wire(self):
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                dev, peer = self.setup_channel(ch, 'CV')
                with dev.powered_step_qualification(ch, 'voltage', .010, 'up',
                        expected_mode='CV', external_load_confirmed=True):
                    dev.output(ch, True)
                    prev = len(peer.rx)
                    other = 1 if ch != 1 else 2
                    with self.assertRaises(SafetyError):
                        dev.step_source(other, 'voltage', 'up')
                    self.assertEqual(prev, len(peer.rx))
                self.assertEqual(dev._output_states(), {1: False, 2: False, 3: False})

    def test_channel_specific_power_envelope_rejected_before_mutation(self):
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                dev, peer = self.setup_channel(ch, 'CV')
                prev = list(peer.rx)
                with self.assertRaises(SafetyError):
                    with dev.powered_step_qualification(ch, 'voltage', .010, 'up',
                            expected_mode='CV', external_load_confirmed=False):
                        pass
                self.assertEqual(prev, peer.rx)
                self.assertNotIn('VOLT:UP', peer.rx)


    def test_wrong_channel_energized_blocks_powered_move(self):
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                dev, peer = self.setup_channel(ch, 'CV')
                with self.assertRaises(SafetyError):
                    with dev.powered_step_qualification(ch, 'voltage', .010, 'up',
                            expected_mode='CV', external_load_confirmed=True):
                        dev.output(ch, True)
                        other = 1 if ch != 1 else 2
                        peer.off_by_channel[other] = 1
                        dev.step_source(ch, 'voltage', 'up')
                self.assertNotIn('VOLT:UP', peer.rx)
                self.assertFalse(any(peer.off_by_channel.values()))

    def test_unknown_link_does_not_retry_powered_step(self):
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                dev, peer = self.setup_channel(ch, 'CV')
                with self.assertRaises(Exception):
                    with dev.powered_step_qualification(ch, 'voltage', .010, 'up',
                            expected_mode='CV', external_load_confirmed=True):
                        dev.output(ch, True)
                        peer.drop = 'VOLT:STEP?'
                        dev.step_source(ch, 'voltage', 'up')
                self.assertTrue(dev.faulted)
                self.assertTrue(dev._link_uncertain)
                self.assertEqual(peer.rx.count('VOLT:UP'), 0)
                self.assertEqual(peer.rx.count('VOLT:STEP?'), 2)


if __name__ == '__main__':
    unittest.main()
