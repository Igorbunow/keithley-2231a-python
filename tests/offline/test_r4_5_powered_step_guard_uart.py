"""R4.5-E bounded powered STEP window; virtual UART ONLY, no real equipment."""
from __future__ import annotations
import os
import unittest

from keithley2231a import Keithley2231A, Profile, ChannelProfile, SafetyError, LimitsError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'POSIX virtual UART required')
class PoweredStepWindowUART(unittest.TestCase):
    @staticmethod
    def point():
        return Profile('bounded R4.5-E simulated', (
            ChannelProfile(3.50, .150, 1.00, .100),
            ChannelProfile(1.50, .100, 1.00, .050),
            ChannelProfile(1.50, .100, 1.00, .050)))

    def setup_device(self, **kwargs):
        peer = UARTPeer(profile_registers=True, **kwargs)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.2)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        dev.apply_profile(self.point(), confirmed_wiring='independent')
        return dev, peer

    def test_cv_one_voltage_step_then_auto_off(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                             expected_mode='CV', external_load_confirmed=True):
            dev.output(1, True)
            self.assertEqual(dev.step_source(1, 'voltage', 'up'), 1.01)
            with self.assertRaises(SafetyError):
                dev.step_source(1, 'voltage', 'up')
            self.assertEqual(peer.off_by_channel[1], 1)
        self.assertEqual(peer.off_by_channel[1], 0)
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertEqual(peer.unsupported, [])

    def test_ordinary_step_remains_off_only(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'current', .001)
        with self.assertRaises(SafetyError):
            dev.output(1, True)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertNotIn('CURR:UP', peer.rx)

    def test_no_approval_and_high_envelope_never_touch_wire(self):
        dev, peer = self.setup_device()
        before = list(peer.rx)
        for options in ({}, {'external_load_confirmed': True, 'max_power_w': 1.0},
                        {'external_load_confirmed': True, 'max_power_w': 0.1}):
            with self.assertRaises((SafetyError, LimitsError)):
                with dev.powered_step_qualification(1, 'current', .001, 'up',
                                                    expected_mode='CC', **options):
                    pass
        self.assertEqual(peer.rx, before)

    def test_wrong_channel_or_mode_rejected_before_uart(self):
        dev, peer = self.setup_device()
        before = list(peer.rx)
        for ch, kind, mode in ((2, 'current', 'CC'), (1, 'current', 'CV'),
                               (1, 'voltage', 'CC'), (3, 'voltage', 'CV')):
            with self.assertRaises(SafetyError):
                with dev.powered_step_qualification(ch, kind, .010, 'up',
                     expected_mode=mode, external_load_confirmed=True):
                    pass
        self.assertEqual(before, peer.rx)

    def test_no_second_enable_or_other_mutating_command(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                             expected_mode='CV', external_load_confirmed=True):
            dev.output(1, True)
            with self.assertRaises(SafetyError):
                dev.output(2, True)
            with self.assertRaises(SafetyError):
                dev.set_channel(1, voltage=1.02)
            with self.assertRaises(SafetyError):
                dev.output(1, True)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertNotIn('VOLT 1.02', peer.rx)

    def test_abort_on_mode_mismatch_before_up_and_off_on_exit(self):
        dev, peer = self.setup_device(response_overrides={'STAT:OPER:INST:ISUM1:COND?': '2'})
        with self.assertRaises(SafetyError):
            with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                               expected_mode='CV', external_load_confirmed=True):
                dev.output(1, True)
                dev.step_source(1, 'voltage', 'up')
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertIn('OUTP 0', peer.rx)

    def test_scpi_error_one_mutation_no_repeat_and_off(self):
        dev, peer = self.setup_device()
        with self.assertRaises(SafetyError):
            with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                               expected_mode='CV', external_load_confirmed=True):
                dev.output(1, True)
                peer.fault_after = 'VOLT:UP'
                dev.step_source(1, 'voltage', 'up')
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.assertIn('OUTP 0', peer.rx)

    def test_unknown_link_no_retry(self):
        dev, peer = self.setup_device(response_overrides={'STAT:OPER:INST:ISUM1:COND?': '2'})
        with self.assertRaises(Exception):
            with dev.powered_step_qualification(1, 'current', .001, 'up',
                                               expected_mode='CC', external_load_confirmed=True):
                dev.output(1, True)
                peer.drop = 'CURR:STEP?'
                dev.step_source(1, 'current', 'up')
        self.assertTrue(dev.faulted)
        self.assertTrue(dev._link_uncertain)
        self.assertEqual(peer.rx.count('CURR:UP'), 0)
        self.assertEqual(peer.rx.count('CURR:STEP?'), 2)  # configure and pre-step check

    def test_cc_single_current_down_then_off(self):
        dev, peer = self.setup_device(response_overrides={'STAT:OPER:INST:ISUM1:COND?': '2'})
        with dev.powered_step_qualification(1, 'current', .001, 'down',
                                             expected_mode='CC', external_load_confirmed=True):
            dev.output(1, True)
            self.assertAlmostEqual(dev.step_source(1, 'current', 'down'), .099)
        self.assertEqual(peer.rx.count('CURR:DOWN'), 1)
        self.assertEqual(peer.rx.count('CURR:UP'), 0)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_power_limit_before_up_aborts(self):
        dev, peer = self.setup_device(response_overrides={'MEAS:VOLT?': '3.40',
                                                         'MEAS:CURR?': '.20'})
        with self.assertRaises(SafetyError):
            with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                               expected_mode='CV', external_load_confirmed=True):
                dev.output(1, True)
                dev.step_source(1, 'voltage', 'up')
        self.assertEqual(peer.rx.count('VOLT:UP'), 0)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_rejected_extra_step_configuration_while_window(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                             expected_mode='CV', external_load_confirmed=True):
            original = peer.rx.count('VOLT:STEP 0.050')
            with self.assertRaises(SafetyError):
                dev.configure_source_step(1, 'voltage', .050)
            self.assertEqual(peer.rx.count('VOLT:STEP 0.050'), original)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_context_abort_after_user_exception_turns_off(self):
        dev, peer = self.setup_device()
        with self.assertRaisesRegex(RuntimeError, 'operator canceled'):
            with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                               expected_mode='CV', external_load_confirmed=True):
                dev.output(1, True)
                raise RuntimeError('operator canceled')
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(peer.rx.count('VOLT:UP'), 0)

    def test_no_second_window_without_new_profile(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                             expected_mode='CV', external_load_confirmed=True):
            pass
        with self.assertRaises(SafetyError):
            with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                               expected_mode='CV', external_load_confirmed=True):
                pass
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 0)

    def test_nested_window_rejected_before_new_step_write(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                             expected_mode='CV', external_load_confirmed=True):
            before = peer.rx.count('VOLT:STEP 0.010')
            with self.assertRaises(SafetyError):
                with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                                                   expected_mode='CV', external_load_confirmed=True):
                    pass
            self.assertEqual(peer.rx.count('VOLT:STEP 0.010'), before)

    def test_enter_without_energizing_exits_cleanly(self):
        dev, peer = self.setup_device()
        with dev.powered_step_qualification(1, 'current', .005, 'down',
                                             expected_mode='CC', external_load_confirmed=True):
            self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertIn('OUTP 0', peer.rx)
        self.assertEqual(peer.rx.count('CURR:DOWN'), 0)

if __name__ == '__main__': unittest.main()
