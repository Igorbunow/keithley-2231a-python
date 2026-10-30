"""R4.5-C: production STEP control over an actual virtual UART, never physical PSU.

All simulated step actions modify *digital register replies only*. No accuracy,
settling time, output physics or on-wire command support has been qualified.
"""
from __future__ import annotations
import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, LimitsError
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class StepControlsUART(unittest.TestCase):
    def setup_device(self, **kwargs):
        peer = UARTPeer(profile_registers=True, **kwargs)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.14)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertEqual(peer.unsupported, [])
        self.assertTrue(dev.armed)
        return dev, peer

    def wire(self, peer):
        self.assertEqual(peer.unsupported, [], peer.unsupported)
        self.assertEqual(peer.rx_bytes, [(c + '\r\n').encode('ascii') for c in peer.rx])

    def test_voltage_set_step_and_up_down_single_channel(self):
        dev, peer = self.setup_device()
        self.assertAlmostEqual(dev.configure_source_step(1, 'voltage', .001), .001)
        self.assertEqual(peer.rx.count('VOLT:STEP 0.001'), 1)
        self.assertAlmostEqual(dev.step_source(1, 'voltage', 'up'), 1.001)
        self.assertAlmostEqual(dev.step_source(1, 'voltage', 'down'), 1.0)
        self.assertEqual(dev._expected_setpoints[1], (1., .1))
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.assertEqual(peer.rx.count('VOLT:DOWN'), 1)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertEqual(peer.off_by_channel.get(1, 0), 0)
        self.wire(peer)

    def test_current_steps_across_channels_and_no_cross_control(self):
        dev, peer = self.setup_device()
        for channel in (1, 2, 3):
            self.assertAlmostEqual(dev.configure_source_step(channel, 'current', .005), .005)
            before = [dict(registers) for _, registers in sorted(peer.channel_registers.items())]
            self.assertAlmostEqual(dev.step_source(channel, 'current', 'up'),
                                   .055 if channel == 3 else .105)
            for other in (1, 2, 3):
                if other != channel:
                    self.assertEqual(peer.channel_registers[other]['CURR'], before[other-1]['CURR'])
            self.assertAlmostEqual(dev.step_source(channel, 'current', 'down'),
                                   .05 if channel == 3 else .1)
        self.assertEqual(peer.rx.count('CURR:UP'), 3)
        self.assertEqual(peer.rx.count('CURR:DOWN'), 3)
        self.wire(peer)

    def test_steps_with_multiple_precisions_and_irregular_directions(self):
        for increment in (.001, .010, .050, .100):
            with self.subTest(increment=increment):
                dev, peer = self.setup_device()
                self.assertAlmostEqual(dev.configure_source_step(2, 'voltage', increment), increment)
                for direction in ('up', 'up', 'down', 'down'):
                    dev.step_source(2, 'voltage', direction)
                self.assertAlmostEqual(dev._expected_setpoints[2][0], 1.0)
                self.assertTrue(dev.armed)
                self.wire(peer)

    def test_invalid_arguments_do_not_touch_wire(self):
        dev, peer = self.setup_device()
        initial = list(peer.rx)
        for invalid in (0, 4, True, 1., None, '2'):
            with self.assertRaises(ValueError): dev.configure_source_step(invalid, 'voltage', .01)
        for kind in ('voltage;OUTP 1', None, 'Voltage', True, 'CURR'):
            with self.assertRaises(ValueError): dev.configure_source_step(1, kind, .01)
        for invalid in (None, False, float('nan'), float('inf'), -.01, 0, .0005, .0015, .501):
            with self.assertRaises((ValueError, TypeError)): dev.configure_source_step(1, 'voltage', invalid)
        for invalid in (True, 0, .0004, .101, .0085):
            with self.assertRaises((ValueError, TypeError)): dev.configure_source_step(1, 'current', invalid)
        for bad in ('UP', 'down;OUTP 1', True, 1, None):
            with self.assertRaises(ValueError): dev.step_source(1, 'voltage', bad)
        self.assertEqual(initial, peer.rx)
        self.wire(peer)

    def test_initial_profile_and_one_unconfigured_step_rejected(self):
        dev, peer = self.setup_device()
        before = list(peer.rx)
        with self.assertRaises(SafetyError): dev.step_source(1, 'current', 'up')
        self.assertEqual(peer.rx.count('CURR:UP'), 0)
        self.assertEqual(dev._expected_setpoints[1], (1.0, .1))
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertGreaterEqual(len(peer.rx), len(before))
        self.wire(peer)

    def test_profile_bound_prevents_next_step_without_step_write(self):
        dev, peer = self.setup_device()
        dev.set_channel(1, voltage=3.3)
        dev.configure_source_step(1, 'voltage', .010)
        with self.assertRaises(LimitsError): dev.step_source(1, 'voltage', 'up')
        self.assertNotIn('VOLT:UP', peer.rx)
        dev.step_source(1, 'voltage', 'down')
        self.assertAlmostEqual(dev._expected_setpoints[1][0], 3.29)
        self.wire(peer)

    def test_lower_boundary_prevents_down_no_mutation(self):
        dev, peer = self.setup_device()
        dev.set_channel(2, current=0)
        dev.configure_source_step(2, 'current', .001)
        with self.assertRaises(LimitsError): dev.step_source(2, 'current', 'down')
        self.assertNotIn('CURR:DOWN', peer.rx)
        self.wire(peer)

    def test_missing_registration_for_other_kind_or_channel(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .010)
        for ch, kind in ((1,'current'), (2,'voltage'), (3,'voltage')):
            with self.assertRaises(SafetyError): dev.step_source(ch, kind, 'up')
        self.assertEqual(peer.rx.count('VOLT:UP'), 0)
        self.wire(peer)

    def test_ignoring_step_registration_readback_aborts_and_no_up(self):
        dev, peer = self.setup_device(ignore_step_write=True)
        with self.assertRaises(SafetyError): dev.configure_source_step(1, 'voltage', .005)
        self.assertTrue(dev.faulted)
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertEqual(peer.rx.count('VOLT:STEP 0.005'), 1)
        self.wire(peer)

    def test_ignoring_up_command_aborts_with_safe_shutdown(self):
        dev, peer = self.setup_device(ignore_step_action=True)
        dev.configure_source_step(1, 'current', .002)
        with self.assertRaises(SafetyError): dev.step_source(1, 'current', 'up')
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('CURR:UP'), 1)
        self.assertIn('OUTP 0', peer.rx)
        self.assertNotIn('OUTP 1', peer.rx)
        self.wire(peer)

    def test_scpi_error_after_step_write_aborts(self):
        dev, peer = self.setup_device()
        peer.fault_after = 'VOLT:STEP 0.050'
        with self.assertRaises(Exception): dev.configure_source_step(1, 'voltage', .050)
        self.assertTrue(dev.faulted)
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertNotIn('OUTP 1', peer.rx)
        self.wire(peer)

    def test_timeout_on_step_query_latches_unknown_and_never_retries(self):
        dev, peer = self.setup_device()
        peer.drop = 'VOLT:STEP?'
        before = peer.rx.count('OUTP 0')
        with self.assertRaises(Exception): dev.configure_source_step(1, 'voltage', .01)
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:STEP?'), 1)
        self.assertEqual(before, peer.rx.count('OUTP 0'))
        self.wire(peer)

    def test_external_step_drift_blocks_action_before_up(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .010)
        peer.channel_registers[1]['VOLT:STEP'] = '0.020'
        with self.assertRaises(SafetyError): dev.step_source(1, 'voltage', 'up')
        self.assertNotIn('VOLT:UP', peer.rx)
        self.wire(peer)

    def test_hidden_1mv_setpoint_drift_detected_by_verify_profile(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .001)
        peer.channel_registers[1]['VOLT'] = '1.001'
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.wire(peer)

    def test_output_activation_remains_blocked_after_step_config(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .01)
        with self.assertRaises(SafetyError): dev.output(1, True)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertNotIn('OUTP 1', peer.rx)
        self.wire(peer)

    def test_external_output_on_blocks_next_mutation(self):
        dev, peer = self.setup_device(off_by_channel={2: 0})
        dev.configure_source_step(1, 'voltage', .01)
        peer.off_by_channel[2] = 1
        with self.assertRaises(SafetyError): dev.step_source(1, 'voltage', 'up')
        self.assertNotIn('VOLT:UP', peer.rx)
        self.wire(peer)

    def test_debug_bypass_does_not_release_service_only_step_commands(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        t = open_test_transport(peer, timeout=.15)
        self.addCleanup(t.close)
        dev = Keithley2231A(t, debug=True, debug_bypass_profile=True)
        for command in ('VOLT:STEP 0.001', 'CURR:STEP 0.005', 'VOLT:UP', 'CURR:DOWN'):
            with self.assertRaises(SafetyError): dev.diagnostic_write(command)
        self.assertEqual(peer.rx, [])
        self.wire(peer)

    def test_step_up_scpi_error_does_not_report_success(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .01)
        peer.fault_after = 'VOLT:UP'
        with self.assertRaises(Exception): dev.step_source(1, 'voltage', 'up')
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.assertNotIn('OUTP 1', peer.rx)
        self.wire(peer)

    def test_voltage_config_register_change_other_channel_isolated(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .005)
        self.assertEqual(peer.channel_registers[1]['VOLT:STEP'], '0.005')
        self.assertEqual(peer.channel_registers[2]['VOLT:STEP'], '0.010')
        self.assertEqual(peer.channel_registers[3]['VOLT:STEP'], '0.010')
        self.wire(peer)

    def test_bad_selected_channel_after_preflight_prevents_mutation(self):
        dev, peer = self.setup_device()
        peer.overrides['INST:NSEL?'] = '1'
        with self.assertRaises(Exception): dev.configure_source_step(2, 'current', .005)
        self.assertNotIn('CURR:STEP 0.005', peer.rx)
        self.assertNotIn('CURR:UP', peer.rx)
        self.wire(peer)

    def test_step_change_is_not_general_service_unlock(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .010)
        self.assertIsNone(dev._source_mutation_command)
        before = len(peer.rx)
        # The real public API never exposes raw diagnostic dispatch without debug.
        with self.assertRaises(SafetyError): dev.diagnostic_write('VOLT:UP')
        self.assertEqual(before, len(peer.rx))
        self.assertTrue(dev._service_only_command('VOLT:UP'))
        self.wire(peer)

    def test_malformed_post_write_setpoint_is_terminal_no_retry(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .010)
        # Three profile VOLT? reads, one prior-value check, then malformed post-UP.
        peer.response_sequences['VOLT?'] = ['1.000'] * 4 + ['UNKNOWN']
        with self.assertRaises(Exception): dev.step_source(1, 'voltage', 'up')
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.wire(peer)

    def test_while_output_on_no_step_registration_even_if_expected_on(self):
        dev, peer = self.setup_device()
        dev.output(2, True)
        self.assertEqual(peer.off_by_channel[2], 1)
        with self.assertRaises(SafetyError): dev.configure_source_step(1, 'voltage', .01)
        self.assertNotIn('VOLT:STEP 0.010', peer.rx)
        dev.output(2, False)
        self.wire(peer)

    def test_profile_reapply_invalidates_authorized_steps(self):
        dev, peer = self.setup_device()
        dev.configure_source_step(1, 'voltage', .005)
        dev.emergency_off()
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertEqual(dev._configured_source_steps, {})
        with self.assertRaises(SafetyError): dev.step_source(1, 'voltage', 'up')
        self.assertNotIn('VOLT:UP', peer.rx)
        self.wire(peer)

if __name__ == '__main__': unittest.main()
