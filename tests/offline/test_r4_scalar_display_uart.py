"""R4 scalar measurement/display query-only production driver over POSIX UART.

All values are explicitly scripted SCPI replies, not a physical model. The
hardware may return different/stale FETCh values; those claims are not tested.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, InstrumentError, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class ScalarAndDisplayUARTTests(unittest.TestCase):
    def device(self, *, applied=False, **kwargs):
        peer = UARTPeer(profile_registers=True, **kwargs)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.13)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        if applied:
            dev.apply_profile(profile(), confirmed_wiring='independent')
            self.assertTrue(dev.armed)
            self.assertEqual(peer.unsupported, [])
        return dev, peer

    def wire_valid(self, peer):
        self.assertTrue(peer.await_received(len(peer.rx)))
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_display_read_only_without_profile_and_without_output_change(self):
        dev, peer = self.device()
        self.assertTrue(dev.display_enabled())
        self.assertFalse(dev.armed)
        self.assertEqual(peer.rx, ['*IDN?', 'SYST:REM', 'DISP?'])
        self.assertNotIn('OUTP 0', peer.rx)
        self.assertNotIn('SYST:ERR?', peer.rx)
        self.wire_valid(peer)

    def test_display_zero_is_false(self):
        dev, peer = self.device(response_overrides={'DISP?': '0'})
        self.assertFalse(dev.display_enabled())
        self.assertEqual(peer.rx[-1], 'DISP?')
        self.wire_valid(peer)

    def test_display_invalid_reply_poison_link_no_retry(self):
        for raw in ('2', '1.0', '', 'garbage', 'ON OFF'):
            with self.subTest(raw=raw):
                dev, peer = self.device(response_overrides={'DISP?': raw})
                with self.assertRaises(InstrumentError):
                    dev.display_enabled()
                self.assertTrue(dev.faulted)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.display_enabled()
                self.assertEqual(peer.rx, before)
                self.assertEqual(peer.rx.count('DISP?'), 1)
                self.wire_valid(peer)

    def test_display_timeout_is_uncertain_without_shutdown(self):
        dev, peer = self.device(drop='DISP?')
        with self.assertRaises(Exception):
            dev.display_enabled()
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.display_enabled()
        self.assertEqual(peer.rx, before)
        self.assertNotIn('OUTP 0', peer.rx)
        self.wire_valid(peer)

    def test_display_semantic_fault_has_no_uart(self):
        dev, peer = self.device()
        dev._latch_fault()
        with self.assertRaises(SafetyError):
            dev.display_enabled()
        self.assertEqual(peer.rx, [])

    def test_reading_requires_armed_profile_without_uart(self):
        dev, peer = self.device()
        with self.assertRaises(SafetyError):
            dev.measure_power(1)
        with self.assertRaises(SafetyError):
            dev.fetch_measurement(1, 'voltage')
        self.assertEqual(peer.rx, [])

    def test_bad_kind_and_channel_rejected_before_wire(self):
        dev, peer = self.device(applied=True)
        before = list(peer.rx)
        for kind in (None, '', 'VOLT?', 'Voltage', 'power?', 'current;OUTP 1', 1):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                dev.fetch_measurement(1, kind)
        for channel in (0, 4, True, 1.0, '2'):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                dev.fetch_measurement(channel, 'voltage')
        with self.assertRaises(ValueError):
            dev.measure_power(True)
        self.assertEqual(peer.rx, before)
        self.assertTrue(dev.armed)
        self.wire_valid(peer)

    def test_direct_instrument_power_differs_from_sequential_product(self):
        dev, peer = self.device(applied=True, response_overrides={'MEAS:POW?': '1.234'})
        before = list(peer.rx)
        self.assertEqual(dev.measure_power(2), 1.234)
        tail = peer.rx[len(before):]
        self.assertEqual(tail[-2:], ['INST:NSEL 2', 'MEAS:POW?'])
        self.assertNotIn('MEAS:VOLT?', tail)
        self.assertNotIn('MEAS:CURR?', tail)
        self.assertNotIn('OUTP 1', tail)
        self.assertTrue(dev.armed)
        self.wire_valid(peer)

    def test_all_fetch_forms_only_read_stored_values(self):
        canned = {'FETC:VOLT?': '2.25', 'FETC:CURR?': '0.125', 'FETC:POW?': '0.28125'}
        dev, peer = self.device(applied=True, response_overrides=canned)
        for kind, expected in (('voltage', 2.25), ('current', .125), ('power', .28125)):
            with self.subTest(kind=kind):
                start = len(peer.rx)
                self.assertEqual(dev.fetch_measurement(1, kind), expected)
                tail = peer.rx[start:]
                self.assertEqual(tail[-2:], ['INST:NSEL 1',
                                             {'voltage': 'FETC:VOLT?',
                                              'current': 'FETC:CURR?',
                                              'power': 'FETC:POW?'}[kind]])
                self.assertNotIn('MEAS:POW?', tail)
                self.assertTrue(dev.armed)
        self.wire_valid(peer)

    def test_fetch_does_not_send_output_commands(self):
        dev, peer = self.device(applied=True, response_overrides={'FETC:VOLT?': '0.250'})
        baseline = len(peer.rx)
        self.assertEqual(dev.fetch_measurement(3, 'voltage'), .25)
        tail = peer.rx[baseline:]
        self.assertFalse(any(command.startswith(('CHAN:OUTP ', 'OUTP 1', 'VOLT ', 'CURR '))
                             for command in tail))
        self.assertEqual(tail[-1], 'FETC:VOLT?')
        self.wire_valid(peer)

    def test_profile_integrity_refusal_prevents_scalar_query(self):
        dev, peer = self.device(applied=True, response_overrides={'MEAS:POW?': '1.23'})
        peer.channel_registers[2]['VOLT:LIM'] = '29.95'
        with self.assertRaises(SafetyError):
            dev.measure_power(2)
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertNotIn('MEAS:POW?', peer.rx)
        self.wire_valid(peer)

    def test_invalid_scalar_reply_latches_unknown_no_retry(self):
        for query, invoke in (('MEAS:POW?', lambda d: d.measure_power(1)),
                              ('FETC:CURR?', lambda d: d.fetch_measurement(1, 'current'))):
            with self.subTest(query=query):
                dev, peer = self.device(applied=True, response_overrides={query: 'nan'})
                count = peer.rx.count('OUTP 0')
                with self.assertRaises(SafetyError):
                    invoke(dev)
                self.assertTrue(dev.faulted)
                self.assertEqual(peer.rx.count(query), 1)
                self.assertEqual(peer.rx.count('OUTP 0'), count)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    invoke(dev)
                self.assertEqual(peer.rx, before)
                self.wire_valid(peer)

    def test_lost_scalar_reply_must_not_retry_or_turn_outputs_off(self):
        dev, peer = self.device(applied=True, drop='FETC:POW?')
        count = peer.rx.count('OUTP 0')
        with self.assertRaises(SafetyError):
            dev.fetch_measurement(1, 'power')
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('FETC:POW?'), 1)
        self.assertEqual(peer.rx.count('OUTP 0'), count)
        self.wire_valid(peer)

    def test_unlisted_scalar_commands_not_silently_supported(self):
        dev, peer = self.device(applied=True)
        self.assertEqual(peer.unsupported, [])
        self.assertFalse(any(c in peer.rx for c in ('DISP 0', 'MEAS:POW 0', 'FETC:POW 1')))


if __name__ == '__main__':
    unittest.main()
