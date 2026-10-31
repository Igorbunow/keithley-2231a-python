"""R5-E6: software-only neutral bus trigger, no physical instrument I/O."""
import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, NeutralTriggerObservation
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'PTY required')
class NeutralBusUART(unittest.TestCase):
    def pair(self, *, responders=(1, 2, 3), **options):
        peer = UARTPeer(profile_registers=True, trigger_registers=True,
                        trigger_bus_channels=responders, **options)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.15)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport, debug=True)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        return dev, peer

    @staticmethod
    def neutralize(dev):
        baseline = dict(dev._expected_setpoints)
        for ch, (v, i) in baseline.items():
            dev.stage_trigger_presets_off(ch, v, i, confirmed_off_only=True)
        return baseline

    def test_all_three_possible_responders_each_software_case(self):
        for responders in ((1,), (2,), (3,), (1, 2, 3), (1, 3)):
            with self.subTest(responders=responders):
                dev, peer = self.pair(responders=responders)
                before = self.neutralize(dev)
                observed = dev.fire_neutral_trigger_off(
                    confirmed_all_outputs_off=True, confirmed_empty_terminals=True)
                self.assertIsInstance(observed, NeutralTriggerObservation)
                self.assertEqual(observed.channels, (1, 2, 3))
                self.assertTrue(observed.outputs_off)
                self.assertEqual(observed.coupling, 'UNKNOWN_NOT_QUALIFIED')
                self.assertEqual(peer.rx.count('*TRG'), 1)
                self.assertEqual(peer.unsupported, [])
                for ch, (v, i) in before.items():
                    self.assertAlmostEqual(float(peer.channel_registers[ch]['VOLT']), v)
                    self.assertAlmostEqual(float(peer.channel_registers[ch]['CURR']), i)
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assertFalse(any(cmd.startswith('INST:COUP') for cmd in peer.rx))

    def test_require_both_physical_assertions_before_io(self):
        dev, peer = self.pair()
        before = list(peer.rx)
        for params in ({}, {'confirmed_all_outputs_off': True},
                       {'confirmed_empty_terminals': True},
                       {'confirmed_all_outputs_off': 1, 'confirmed_empty_terminals': True}):
            with self.subTest(params=params), self.assertRaises(SafetyError):
                dev.fire_neutral_trigger_off(**params)
        self.assertEqual(before, peer.rx)

    def test_non_neutral_preset_on_any_channel_blocks_bus_event(self):
        for wrong in (1, 2, 3):
            dev, peer = self.pair()
            self.neutralize(dev)
            v, i = dev._expected_setpoints[wrong]
            dev.stage_trigger_presets_off(wrong, v - .01, i,
                                          confirmed_off_only=True)
            with self.subTest(channel=wrong), self.assertRaises(SafetyError):
                dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                             confirmed_empty_terminals=True)
            self.assertNotIn('*TRG', peer.rx)

    def test_requires_staged_session_not_merely_valid_default_presets(self):
        dev, peer = self.pair()
        with self.assertRaises(SafetyError):
            dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                         confirmed_empty_terminals=True)
        self.assertNotIn('*TRG', peer.rx)

    def test_output_on_fails_before_bus_event(self):
        dev, peer = self.pair()
        dev.output(1, True)
        with self.assertRaises(SafetyError):
            dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                         confirmed_empty_terminals=True)
        self.assertNotIn('*TRG', peer.rx)

    def test_consumed_no_replay_even_after_success(self):
        dev, peer = self.pair()
        self.neutralize(dev)
        args = dict(confirmed_all_outputs_off=True, confirmed_empty_terminals=True)
        dev.fire_neutral_trigger_off(**args)
        before = len(peer.rx)
        with self.assertRaises(SafetyError):
            dev.fire_neutral_trigger_off(**args)
        self.assertEqual(len(peer.rx), before)
        self.assertEqual(peer.rx.count('*TRG'), 1)

    def test_trigger_scpi_error_stops_and_faults_without_retry(self):
        dev, peer = self.pair()
        self.neutralize(dev)
        peer.fault_after = '*TRG'
        with self.assertRaises(SafetyError):
            dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                         confirmed_empty_terminals=True)
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('*TRG'), 1)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_uncertain_uart_after_trigger_never_resends_event(self):
        dev, peer = self.pair(drop_error_after_trigger=True)
        self.neutralize(dev)
        with self.assertRaises(Exception):
            dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                         confirmed_empty_terminals=True)
        self.assertTrue(dev._link_uncertain)
        self.assertEqual(peer.rx.count('*TRG'), 1)
        with self.assertRaises(SafetyError):
            dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                         confirmed_empty_terminals=True)
        self.assertEqual(peer.rx.count('*TRG'), 1)

    def test_debug_bypass_still_cannot_send_bus_trigger_or_coupling(self):
        dev, peer = self.pair()
        dev.debug_bypass_profile = True
        before = list(peer.rx)
        for command in ('*TRG', 'TRIG', 'INST:COUP CH1.CH2', 'INST:COUP?'):
            with self.subTest(command=command), self.assertRaises(SafetyError):
                (dev.diagnostic_query(command) if command.endswith('?')
                 else dev.diagnostic_write(command))
        self.assertEqual(before, peer.rx)

    def test_capability_status_is_simulation_not_hardware(self):
        entry = Keithley2231A.capabilities().feature('neutral_trigger_bus_off_candidate')
        self.assertEqual(entry.evidence, 'simulated_only')
        self.assertTrue(entry.driver_exposed)
        self.assertFalse(entry.hardware_evidence)

    def test_after_event_output_enable_remains_denied(self):
        dev, peer = self.pair()
        self.neutralize(dev)
        dev.fire_neutral_trigger_off(confirmed_all_outputs_off=True,
                                     confirmed_empty_terminals=True)
        with self.assertRaises(SafetyError):
            dev.output(2, True)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
