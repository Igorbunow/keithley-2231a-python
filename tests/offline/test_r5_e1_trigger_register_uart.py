"""R5-E1 native trigger readback over virtual UART; no hardware I/O."""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError, TriggerPresetReadback
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class TriggerReadbackUART(unittest.TestCase):
    def new_device(self, *, debug=False, **peer_options):
        peer = UARTPeer(profile_registers=True, trigger_registers=True, **peer_options)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.17)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport, debug=debug)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        return dev, peer

    def assert_no_unsafe_trigger(self, peer):
        self.assertFalse(any(c == '*TRG' or c.startswith(('TRIG', 'INST:COUP',
                         'VOLT:TRIG ', 'CURR:TRIG ', 'APPL')) for c in peer.rx))
        self.assertFalse(any(c in ('OUTP 1', 'CHAN:OUTP 1') for c in peer.rx))
        self.assertEqual(peer.unsupported, [])

    def test_all_channels_read_correct_independent_registers(self):
        dev, peer = self.new_device()
        before = {i: dict(peer.channel_registers[i]) for i in (1, 2, 3)}
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                result = dev.read_trigger_presets(ch)
                self.assertEqual(result.channel, ch)
                self.assertAlmostEqual(result.voltage_v, ch * .5)
                self.assertAlmostEqual(result.current_a, ch * .025)
                self.assertIn(peer.selected, (1, 2, 3))
                self.assertEqual(dev._expected_outputs, {1: False, 2: False, 3: False})
        self.assertEqual(peer.rx.count('VOLT:TRIG?'), 3)
        self.assertEqual(peer.rx.count('CURR:TRIG?'), 3)
        self.assertEqual(peer.channel_registers, before)
        self.assert_no_unsafe_trigger(peer)

    def test_reject_invalid_channel_before_any_scpi(self):
        dev, peer = self.new_device()
        before = list(peer.rx)
        for ch in (0, 4, -1, True, 1.0, '1', None):
            with self.subTest(ch=ch), self.assertRaises(ValueError):
                dev.read_trigger_presets(ch)
        self.assertEqual(peer.rx, before)
        self.assert_no_unsafe_trigger(peer)

    def test_output_enabled_rejected_before_trigger_query(self):
        dev, peer = self.new_device()
        dev.output(1, True)
        with self.assertRaises(SafetyError):
            dev.read_trigger_presets(1)
        self.assertNotIn('VOLT:TRIG?', peer.rx)
        self.assertNotIn('CURR:TRIG?', peer.rx)
        dev.emergency_off()
        self.assertNotIn("*TRG", peer.rx)

    def test_wrong_channel_readback_aborts_before_trigger_query(self):
        dev, peer = self.new_device()
        peer.overrides['INST:NSEL?'] = '1'
        with self.assertRaises(SafetyError):
            dev.read_trigger_presets(2)
        self.assertNotIn('VOLT:TRIG?', peer.rx)
        self.assertNotIn("CURR:TRIG?", peer.rx)

    def test_unexpected_or_nonfinite_reply_latches_unknown_and_no_retry(self):
        for invalid in ('INVALID', 'nan', 'inf'):
            with self.subTest(invalid=invalid):
                dev, peer = self.new_device(response_overrides={'VOLT:TRIG?': invalid})
                with self.assertRaises(InstrumentError):
                    dev.read_trigger_presets(1)
                self.assertTrue(dev.faulted)
                self.assertTrue(dev._link_uncertain)
                self.assertEqual(peer.rx.count('VOLT:TRIG?'), 1)
                self.assertNotIn('CURR:TRIG?', peer.rx)

    def test_out_of_range_register_latches_unknown(self):
        dev, peer = self.new_device()
        peer.channel_registers[3]['VOLT:TRIG'] = '10'
        with self.assertRaises(InstrumentError):
            dev.read_trigger_presets(3)
        self.assertTrue(dev._link_uncertain)
        self.assertEqual(peer.rx.count('VOLT:TRIG?'), 1)
        self.assertEqual(peer.rx.count('CURR:TRIG?'), 1)

    def test_query_timeout_no_retry_or_unconfirmed_shutdown(self):
        dev, peer = self.new_device(drop='CURR:TRIG?')
        prior_off = peer.rx.count('OUTP 0')
        with self.assertRaises(Exception):
            dev.read_trigger_presets(2)
        self.assertTrue(dev._link_uncertain)
        self.assertEqual(peer.rx.count('VOLT:TRIG?'), 1)
        self.assertEqual(peer.rx.count('CURR:TRIG?'), 1)
        self.assertEqual(peer.rx.count('OUTP 0'), prior_off)

    def test_undocumented_trigger_coupling_and_writes_stay_blocked(self):
        dev, peer = self.new_device(debug=True)
        before = list(peer.rx)
        for cmd in ('*TRG', 'TRIG', 'INST:COUP CH1.CH2',
                    'VOLT:TRIG 2.0', 'CURR:TRIG 0.1', 'SOUR:VOLT:TRIG 2',
                    'APP:VOLT 1,1,1'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError):
                dev.diagnostic_write(cmd)
        for cmd in ('INST:COUP?', 'INST:COUP:TRIG?', 'VOLT:TRIG?', 'CURR:TRIG?'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError):
                dev.diagnostic_query(cmd)
        self.assertEqual(peer.rx, before)

    def test_scpi_error_after_first_trigger_query_blocks_second(self):
        dev, peer = self.new_device(fault_after='VOLT:TRIG?')
        with self.assertRaises(Exception):
            dev.read_trigger_presets(3)
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:TRIG?'), 1)
        self.assertEqual(peer.rx.count('CURR:TRIG?'), 0)

    def test_excessive_or_negative_current_reply_is_never_accepted(self):
        for value in ('-0.01', '3.01'):
            with self.subTest(value=value):
                dev, peer = self.new_device(response_overrides={'CURR:TRIG?': value})
                with self.assertRaises(InstrumentError):
                    dev.read_trigger_presets(1)
                self.assertTrue(dev._link_uncertain)
                self.assertEqual(peer.rx.count('CURR:TRIG?'), 1)

    def test_output_only_preset_queries_are_sequential_and_not_mutations(self):
        dev, peer = self.new_device()
        expected = dict(dev._expected_setpoints)
        before = len(peer.rx)
        result = dev.read_trigger_presets(3)
        after = peer.rx[before:]
        self.assertLess(after.index('VOLT:TRIG?'), after.index('CURR:TRIG?'))
        self.assertEqual(expected, dev._expected_setpoints)
        self.assertNotIn('INST:COUP?', after)
        self.assertNotIn('*TRG', after)
        self.assertAlmostEqual(result.voltage_v, 1.5)
        self.assert_no_unsafe_trigger(peer)

    def test_read_with_active_step_window_is_rejected(self):
        dev, peer = self.new_device()
        dev._powered_step_window = {'channel': 1}  # test-private guard fixture
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.read_trigger_presets(1)
        self.assertEqual(peer.rx, before)
        dev._powered_step_window = None

    def test_presets_are_immutable_and_capability_not_hw_verified(self):
        dev, peer = self.new_device()
        from dataclasses import FrozenInstanceError
        result = dev.read_trigger_presets(1)
        with self.assertRaises(FrozenInstanceError):
            result.voltage_v = 2.0
        caps = Keithley2231A.capabilities()
        feat = caps.feature('trigger_preset_readback')
        self.assertTrue(feat.driver_exposed)
        self.assertEqual(feat.evidence, 'simulated_only')
        self.assertFalse(feat.hardware_evidence)
        self.assertFalse(caps.feature('hardware_triggered_setpoint').driver_exposed)
        self.assertFalse(caps.feature('trigger_channel_coupling').driver_exposed)
        self.assert_no_unsafe_trigger(peer)


if __name__ == '__main__':
    unittest.main()
