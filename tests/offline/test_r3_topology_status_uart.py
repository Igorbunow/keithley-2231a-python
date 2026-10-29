"""R3.5.63: inherited topology and CV/CC evidence through production UART.

The peer supplies fixed, explicit digital readbacks; it does not simulate
combined electrical behavior. H09/H10 remain the hardware qualification.
"""
from __future__ import annotations

import os
import unittest
from dataclasses import replace

from keithley2231a import (ChannelProfile, InstrumentError, Keithley2231A,
                          ParallelProfile, SafetyError, SeriesProfile)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


SERIES_REGISTERS = {('series', 1, 'VOLT:LIM?'): '5.00',
                    ('series', 1, 'VOLT:LIM:STAT?'): '0'}
PARALLEL_REGISTERS = {('parallel', 1, 'VOLT:LIM:STAT?'): '0',
                      ('parallel', 2, 'VOLT:LIM:STAT?'): '0'}


def series_profile():
    return SeriesProfile('series', (2., 3.), 4.5, .3, 2., .1,
                         ChannelProfile(4., .5, 1., .1))


def parallel_profile():
    return ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                           ChannelProfile(4., .5, 1., .1))


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class TopologyAndStatusUARTTests(unittest.TestCase):
    def device(self, *, inherited=False, scripts=None, **options):
        peer = UARTPeer(profile_registers=True,
                        mode_channel_overrides=scripts or {}, **options)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.18)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        if inherited:
            # Fixed pre-existing digital state; no SCPI command is fabricated.
            peer.mode_registers['OUTP:PARALLEL'] = 1
            for ch in (1, 2, 3):
                peer.off_by_channel[ch] = 1
        return dev, peer

    def wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_inherited_parallel_is_shut_down_before_any_mode_change(self):
        for target, config, scripts in (
                ('independent', profile(), {}),
                ('series', series_profile(), SERIES_REGISTERS),
                ('parallel', parallel_profile(), PARALLEL_REGISTERS)):
            with self.subTest(target=target):
                dev, peer = self.device(inherited=True, scripts=scripts)
                dev.apply_profile(config, confirmed_wiring=target)
                commands = peer.rx
                inherited_query = commands.index('OUTP:PARALLEL?')
                shutdown = commands.index('OUTP 0', inherited_query)
                readback = commands.index('OUTP:STAT:ALL?', shutdown)
                clear_mode = commands.index('OUTP:PARALLEL 0', readback)
                self.assertLess(shutdown, readback)
                self.assertLess(readback, clear_mode)
                self.assertTrue(dev.armed)
                self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
                self.wire(peer)

    def test_stuck_parallel_output_cannot_change_topology_or_setpoints(self):
        for target, config, scripts in (
                ('independent', profile(), {}),
                ('series', series_profile(), SERIES_REGISTERS),
                ('parallel', parallel_profile(), PARALLEL_REGISTERS)):
            with self.subTest(target=target):
                dev, peer = self.device(inherited=True, scripts=scripts)
                peer.ignore_shutdown = True
                with self.assertRaisesRegex(SafetyError, 'could not be switched OFF'):
                    dev.apply_profile(config, confirmed_wiring=target)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertIsNone(dev.profile)
                self.assertEqual(peer.mode_registers['OUTP:PARALLEL'], 1)
                self.assertNotIn('OUTP:PARALLEL 0', peer.rx)
                self.assertFalse(any(cmd.startswith(('VOLT ', 'CURR ', 'VOLT:LIM '))
                                     for cmd in peer.rx))
                self.wire(peer)

    def test_malformed_off_readback_prevents_topology_mutation(self):
        dev, peer = self.device(inherited=True, scripts=SERIES_REGISTERS,
                                response_overrides={'OUTP:STAT:ALL?': 'not-a-boolean'})
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            dev.apply_profile(series_profile(), confirmed_wiring='series')
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertTrue(peer.await_command('OUTP:STAT:ALL?'))
        self.assertEqual(peer.rx[-1], 'OUTP:STAT:ALL?')
        self.assertNotIn('OUTP:PARALLEL 0', peer.rx)
        self.assertEqual(peer.mode_registers['OUTP:PARALLEL'], 1)
        self.wire(peer)

    def test_calibration_bit_prevents_cv_cc_claim(self):
        dev, peer = self.device()
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(3, True)
        peer.overrides['STAT:OPER:INST:ISUM3:COND?'] = '13'
        peer.overrides['STAT:QUES:INST:ISUM3:COND?'] = '1'
        reading = dev.read_channel(3)
        self.assertTrue(reading.enabled)
        self.assertEqual((reading.regulation, reading.regulation_source), ('UNKNOWN', None))
        self.assertEqual((reading.operation_status_mask, reading.questionable_status_mask),
                         (13, 1))
        self.wire(peer)

    def test_required_regulation_fails_closed_on_calibration_bit(self):
        dev, peer = self.device()
        base = profile()
        settings = replace(base, channels=(
            base.channels[0], base.channels[1],
            replace(base.channels[2], expected_regulation='cv')))
        dev.apply_profile(settings, confirmed_wiring='independent')
        dev.output(3, True)
        peer.overrides['STAT:OPER:INST:ISUM3:COND?'] = '13'
        peer.overrides['STAT:QUES:INST:ISUM3:COND?'] = '1'
        with self.assertRaisesRegex(SafetyError, 'unexpected regulation'):
            dev.read_channel(3)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.wire(peer)

    def test_series_conflicting_mode_evidence_keeps_raw_registers(self):
        dev, peer = self.device(scripts=SERIES_REGISTERS)
        dev.apply_profile(series_profile(), confirmed_wiring='series')
        dev.verify_profile()
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        peer.overrides['STAT:OPER:INST:ISUM1:COND?'] = '9'
        peer.overrides['STAT:QUES:INST:ISUM1:COND?'] = '2'
        reading = dev.read_channel(1)
        self.assertEqual(reading.label, 'CH1+CH2')
        self.assertEqual((reading.regulation, reading.regulation_source), ('UNKNOWN', None))
        self.assertEqual((reading.operation_status_mask, reading.questionable_status_mask),
                         (9, 2))
        self.wire(peer)


if __name__ == '__main__':
    unittest.main()
