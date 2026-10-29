"""Readings, regulation evidence and output shutdown on the real driver wire.

The UART peer provides narrow, independently scripted SCPI replies; it does
not model a physical supply, meter accuracy or load/feedback behavior.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import replace

from keithley2231a import (Acquisition, ChannelProfile, Keithley2231A, LimitsError, Profile, SafetyError)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudo-terminal')
class OutputAndMeasurementUART(unittest.TestCase):
    def device(self, configured=None):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.18)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        dev.apply_profile(configured or profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        return dev, peer

    def check_wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_apply_off_state_limits_and_setpoint_guard(self):
        dev, peer = self.device()
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(peer.channel_registers[1]['VOLT:LIM'], '3.30')
        dev.verify_profile()
        dev.set_channel(1, voltage=2.5, current=.15)
        self.assertEqual(peer.channel_registers[1]['VOLT'], '2.50')
        before = list(peer.rx)
        for invalid in (3.31,):
            with self.assertRaises(LimitsError): dev.set_channel(1, voltage=invalid)
            self.assertEqual(peer.rx, before)
        self.assertTrue(dev.armed)
        self.check_wire(peer)

    def test_enable_lock_and_emergency_off_remote_order(self):
        dev, peer = self.device()
        dev.output(1, True)
        self.assertEqual(peer.off_by_channel[1], 1)
        with self.assertRaises(SafetyError):
            dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(1, False)
        self.assertEqual(peer.off_by_channel[1], 0)
        dev.output(2, True)
        before = len(peer.rx)
        dev.emergency_off()
        self.assertEqual(peer.rx[before:before + 2], ['SYST:REM', 'OUTP 0'])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertFalse(dev.armed)
        self.check_wire(peer)

    def test_global_off_fallback_is_individual_and_verified(self):
        dev, peer = self.device()
        dev.output(1, True)
        peer.ignore_global_off = True
        dev.emergency_off()
        self.assertEqual(peer.off_by_channel[1], 0)
        self.assertIn('CHAN:OUTP 0', peer.rx)
        self.assertFalse(dev.armed)
        self.check_wire(peer)

    def test_all_off_paths_ignored_cannot_claim_shutdown(self):
        dev, peer = self.device()
        dev.output(2, True)
        peer.ignore_global_off = True
        peer.ignore_shutdown = True
        with self.assertRaises(SafetyError): dev.emergency_off()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(peer.off_by_channel[2])
        self.assertIn('CHAN:OUTP 0', peer.rx)
        self.check_wire(peer)

    def test_external_voltage_limit_drift_is_detected_and_output_off(self):
        dev, peer = self.device()
        peer.channel_registers[2]['VOLT:LIM'] = '5.01'
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)

    def test_measurement_reading_reports_setpoints_and_off_evidence(self):
        dev, peer = self.device()
        sample = dev.read_channel(1)
        self.assertEqual((sample.voltage_v, sample.current_a, sample.power_w),
                         (1., .1, .1))
        self.assertEqual((sample.voltage_set_v, sample.current_limit_set_a), (1., .1))
        self.assertEqual((sample.regulation, sample.regulation_source), ('OFF', None))
        self.assertEqual((sample.operation_status_mask, sample.questionable_status_mask), (0, 0))
        self.check_wire(peer)

    def test_enabled_with_no_status_mode_stays_unknown(self):
        dev, peer = self.device()
        dev.output(1, True)
        sample = dev.read_channel(1)
        self.assertTrue(sample.enabled)
        self.assertEqual((sample.regulation, sample.regulation_source), ('CV', 'operation'))
        self.assertEqual(sample.operation_status_mask, 9)
        self.assertEqual(sample.questionable_status_mask, 0)
        # Conflicting with normal output state is intentional: canned OPER=0.
        peer.overrides['STAT:OPER:INST:ISUM1:COND?'] = '0'
        sample = dev.read_channel(1)
        self.assertEqual((sample.regulation, sample.regulation_source), ('UNKNOWN', None))
        self.assertEqual(sample.operation_status_mask, 0)
        self.assertEqual(sample.questionable_status_mask, 0)
        self.check_wire(peer)

    def test_ch3_regulation_evidence_matrix_exact_two_status_queries(self):
        dev, peer = self.device()
        dev.output(3, True)
        for oper, ques, mode, source in (
                (9, 1, 'CV', 'both'), (10, 2, 'CC', 'both'),
                (9, 0, 'CV', 'operation'), (10, 0, 'CC', 'operation'),
                (8, 1, 'CV', 'questionable'), (8, 2, 'CC', 'questionable'),
                (8, 0, 'UNKNOWN', None), (8, 3, 'UNKNOWN', None),
                (11, 1, 'UNKNOWN', None), (9, 2, 'UNKNOWN', None)):
            with self.subTest(oper=oper, ques=ques):
                peer.overrides['STAT:OPER:INST:ISUM3:COND?'] = str(oper)
                peer.overrides['STAT:QUES:INST:ISUM3:COND?'] = str(ques)
                before = len(peer.rx)
                reading = dev.read_channel(3)
                self.assertEqual((reading.regulation, reading.regulation_source), (mode, source))
                queries = [cmd for cmd in peer.rx[before:] if cmd.startswith('STAT:')]
                self.assertEqual(queries, [
                    'STAT:OPER:INST:ISUM3:COND?',
                    'STAT:QUES:INST:ISUM3:COND?'])
        dev.output(3, False)
        reading = dev.read_channel(3)
        self.assertEqual((reading.regulation, reading.regulation_source), ('OFF', None))
        unchecked = dev.read_channel(3, with_regulation=False)
        self.assertIsNone(unchecked.regulation_source)
        self.assertIsNone(unchecked.operation_status_mask)
        self.assertIsNone(unchecked.questionable_status_mask)
        self.check_wire(peer)

    def test_ch3_questionable_cv_cc_and_missing_evidence_trip(self):
        for required, mask in (('cv', 1), ('cc', 2)):
            with self.subTest(required=required):
                original = profile()
                setting = replace(original, channels=(
                    original.channels[0], original.channels[1],
                    replace(original.channels[2], expected_regulation=required)))
                dev, peer = self.device(setting)
                dev.set_channel(3, voltage=2., current=.08)
                dev.output(3, True)  # input validation + pre-enable verify
                peer.overrides['STAT:OPER:INST:ISUM3:COND?'] = '8'
                peer.overrides['STAT:QUES:INST:ISUM3:COND?'] = str(mask)
                sample = dev.read_channel(3)
                self.assertEqual((sample.regulation, sample.regulation_source),
                                 (required.upper(), 'questionable'))
                peer.overrides['STAT:QUES:INST:ISUM3:COND?'] = '0'
                with self.assertRaises(SafetyError): dev.read_channel(3)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
                self.assertFalse(any(peer.off_by_channel.values()))
                self.check_wire(peer)

    def test_measured_overvoltage_trips_and_confirms_off(self):
        dev, peer = self.device()
        dev.output(1, True)
        peer.overrides['MEAS:VOLT?'] = '5.00'
        with self.assertRaises(SafetyError): dev.read_channel(1)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)


    def test_setpoint_validation_is_before_any_serial_bytes(self):
        dev, peer = self.device()
        for channel, settings in (
                (1, {'voltage': True}), (1, {'current': float('nan')}),
                (2, {'voltage': 5.001}), (3, {'current': .1001}),
                (3, {'voltage': -.01}), (3, {'voltage': float('inf')})):
            with self.subTest(channel=channel, settings=settings):
                before = list(peer.rx)
                with self.assertRaises(LimitsError):
                    dev.set_channel(channel, **settings)
                self.assertEqual(peer.rx, before)
                self.assertTrue(dev.armed)
        before = len(peer.rx)
        dev.set_channel(1, voltage=2.5, current=.15)
        command = peer.rx[before:]
        self.assertLess(command.index('CURR 0.150'), command.index('VOLT 2.50'))
        dev.verify_profile()
        self.check_wire(peer)

    def test_setpoint_readback_disagreement_is_fail_closed(self):
        dev, peer = self.device()
        # First reply is the pre-write integrity check; second is an
        # independent, deliberately wrong reply after the write.
        peer.channel_response_sequences[3, 'VOLT?'] = ['1.00', '2.01']
        with self.assertRaises(SafetyError):
            dev.set_channel(3, voltage=2., current=.08)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)

    def test_profile_requires_front_panel_lock_and_regulation_evidence(self):
        dev, peer = self.device()
        before = list(peer.rx)
        with self.assertRaises(SafetyError): dev.remote(lock_panel=False)
        self.assertEqual(peer.rx, before)
        dev.remote(lock_panel=True)
        self.assertIn('SYST:RWLOCK', peer.rx)
        dev.emergency_off()
        dev.apply_profile(profile(expected='cv'), confirmed_wiring='independent')
        before = list(peer.rx)
        with self.assertRaises(SafetyError): dev.read_channel(1, with_regulation=False)
        self.assertEqual(peer.rx, before)
        self.check_wire(peer)

    def test_acquisition_samples_real_driver_over_uart(self):
        dev, peer = self.device()
        samples = Acquisition(dev, interval_s=.1).sample()
        self.assertEqual(len(samples), 3)
        self.assertEqual([sample.channel for sample in samples], [1, 2, 3])
        self.assertIn('MEAS:VOLT?', peer.rx)
        self.assertIn('MEAS:CURR?', peer.rx)
        self.check_wire(peer)

    def test_record_false_does_not_disable_secondary_guards(self):
        settings = Profile('record selective', (
            ChannelProfile(3.3, .2, 1., .1, record=False),
            ChannelProfile(5., .2, 1., .1),
            ChannelProfile(3.3, .1, 1., .05)))
        dev, peer = self.device(settings)
        dev.output(1, True)
        peer.channel_registers[1]['VOLT'] = '9.00'
        with self.assertRaises(SafetyError): Acquisition(dev).sample()
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertTrue(dev.faulted)
        self.check_wire(peer)

    def test_output_on_readback_disagreement_disarms_and_confirms_off(self):
        dev, peer = self.device()
        # A valid digital reply contradicts the write; the driver must trip.
        peer.overrides['CHAN:OUTP?'] = '0'
        with self.assertRaises(SafetyError): dev.output(1, True)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)

    def test_measured_overcurrent_trips_and_confirms_off(self):
        dev, peer = self.device()
        dev.output(1, True)
        peer.overrides['MEAS:CURR?'] = '0.30'
        with self.assertRaises(SafetyError): dev.read_channel(1)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.check_wire(peer)

    def test_close_requests_verified_off_then_returns_local(self):
        dev, peer = self.device()
        dev.output(1, True)
        dev.close()
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        last_off = max(i for i, cmd in enumerate(peer.rx) if cmd == 'OUTP 0')
        self.assertLess(last_off, peer.rx.index('SYST:LOC'))
        self.check_wire(peer)


if __name__ == '__main__':
    unittest.main()
