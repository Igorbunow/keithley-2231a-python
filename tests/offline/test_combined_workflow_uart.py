"""Combined-mode API workflows on production driver over a strict UART fixture.

These are digital SCPI sequencing and guard regressions, NOT electrical Series/
Parallel emulation or substitutes for bounded physical H09/H10 measurements.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import (ChannelProfile, Keithley2231A, LimitsError,
                          ParallelProfile, Profile, SafetyError, SeriesProfile)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile

SERIES = {('series', 1, 'VOLT:LIM?'): '5.00'}
PARALLEL = {('parallel', 1, 'VOLT:LIM:STAT?'): '0',
            ('parallel', 2, 'VOLT:LIM:STAT?'): '0'}


def series():
    return SeriesProfile('safe series', (2., 3.), 4.5, .3, 2., .1,
                         ChannelProfile(4., .5, 2., .3))


def parallel():
    return ParallelProfile('bounded parallel', 3.0, .300, 2.0, .100,
                           ChannelProfile(4., .5, 1., .1), lock_front_panel=False)


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudo-terminal')
class CombinedWorkflowUART(unittest.TestCase):
    def device(self, topology):
        scripts = SERIES if topology == 'series' else PARALLEL
        peer = UARTPeer(profile_registers=True, mode_channel_overrides=scripts)
        self.addCleanup(peer.close)
        io = open_test_transport(peer, timeout=.18)
        self.addCleanup(io.close)
        return Keithley2231A(io), peer

    def wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(x + '\r\n').encode('ascii') for x in peer.rx])

    def test_series_operator_confirmation_and_aggregate_workflow(self):
        dev, peer = self.device('series')
        config = series()
        with self.assertRaises(SafetyError): dev.apply_profile(config)
        self.assertEqual(peer.rx, [])
        dev.apply_profile(config, confirmed_wiring='series')
        self.assertTrue(dev.armed)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        with self.assertRaises(SafetyError): dev.combined_outputs(True)
        self.assertNotIn('OUTP 1', peer.rx)
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        reading = dev.read_channel(1)
        self.assertEqual((reading.label, reading.voltage_set_v, reading.current_limit_set_a),
                         ('CH1+CH2', 2., .1))
        with self.assertRaises(SafetyError): dev.read_channel(2)
        self.assertEqual(dev.read_channel(3).label, 'CH3')
        before = list(peer.rx)
        with self.assertRaises(LimitsError): dev.set_channel(1, voltage=4.51)
        with self.assertRaises(SafetyError): dev.set_channel(2, voltage=1.)
        self.assertEqual(peer.rx, before)
        dev.set_channel(1, voltage=2.5, current=.2)
        before = list(peer.rx)
        with self.assertRaises(LimitsError): dev.set_channel(3, voltage=4.01)
        self.assertEqual(peer.rx, before)
        dev.set_channel(3, voltage=2.5, current=.4)
        self.assertEqual(peer.channel_registers[3]['VOLT'], '2.50')
        self.assertEqual(dev.read_channel(1).voltage_set_v, 2.5)
        dev.emergency_off()
        self.assertFalse(dev.armed)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.wire(peer)

    def test_series_aggregate_drift_after_rearming_trips(self):
        dev, peer = self.device('series')
        dev.apply_profile(series(), confirmed_wiring='series')
        peer.channel_registers[1]['VOLT'] = '2.01'
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.wire(peer)

    def test_parallel_ch3_and_software_caps_through_shared_driver(self):
        dev, peer = self.device('parallel')
        dev.apply_profile(parallel(), confirmed_wiring='parallel')
        self.assertTrue(dev.armed)
        self.assertEqual(peer.mode_registers['OUTP:PARALLEL'], 1)
        self.assertEqual(dev.read_channel(1, with_regulation=False).label, 'CH1+CH2')
        with self.assertRaises(SafetyError): dev.read_channel(2)
        dev.set_channel(1, voltage=2.5, current=.2)
        before = list(peer.rx)
        with self.assertRaises(LimitsError): dev.set_channel(3, current=.501)
        self.assertEqual(peer.rx, before)
        dev.set_channel(3, voltage=2., current=.2)
        self.assertEqual(peer.channel_registers[3]['VOLT'], '2.00')
        with self.assertRaises(SafetyError): dev.set_channel(2, voltage=2.5)
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        self.assertEqual(dev.output_states(), {1: True, 2: True, 3: True})
        with self.assertRaises(LimitsError): dev.set_channel(1, voltage=3.01)
        dev.combined_outputs(False)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        dev.emergency_off()
        self.assertFalse(dev.armed)
        self.wire(peer)

    def test_parallel_one_step_drift_after_rearm_trips(self):
        dev, peer = self.device('parallel')
        dev.apply_profile(parallel(), confirmed_wiring='parallel')
        peer.channel_registers[1]['CURR'] = '0.101'
        with self.assertRaises(SafetyError): dev.verify_profile()
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.wire(peer)

    def test_parallel_to_independent_requires_explicit_jumper_removal(self):
        dev, peer = self.device('parallel')
        configured = parallel()
        dev.apply_profile(configured, confirmed_wiring='parallel')
        dev.emergency_off()
        before = list(peer.rx)
        for confirmation in (None, 'parallel', 'series'):
            with self.subTest(confirmation=confirmation):
                with self.assertRaisesRegex(SafetyError, 'NO external Series/Parallel jumpers'):
                    dev.apply_profile(profile(), confirmed_wiring=confirmation)
                self.assertEqual(peer.rx, before)
                self.assertIs(dev.profile, configured)
        # User confirmation is a software gate, NOT evidence of jumper removal.
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertEqual(peer.mode_registers['OUTP:PARALLEL'], 0)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        self.wire(peer)


if __name__ == '__main__':
    unittest.main()
