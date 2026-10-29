"""R4.5-A documented query-only INST? and source STEP? via production UART.

This fixture supplies canned digital replies, not stepping/output physics. Do
not infer hardware validation or permission to send UP/DOWN/STEP writes.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, InstrumentError, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class SourceObservationsUART(unittest.TestCase):
    def device(self, *, applied=False, **peer_kwargs):
        peer = UARTPeer(profile_registers=True, **peer_kwargs)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.14)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport)
        if applied:
            dev.apply_profile(profile(), confirmed_wiring='independent')
            self.assertTrue(dev.armed)
        return dev, peer

    def check_wire(self, peer):
        self.assertTrue(peer.await_received(len(peer.rx)))
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])

    def test_selected_name_without_profile_or_change_to_output(self):
        dev, peer = self.device()
        self.assertEqual(dev.selected_channel_name(), 'CH1')
        self.assertEqual(peer.rx, ['*IDN?', 'SYST:REM', 'INST?'])
        self.assertNotIn('INST:NSEL 1', peer.rx)
        self.assertNotIn('OUTP 0', peer.rx)
        self.check_wire(peer)

    def test_symbolic_name_tracks_selected_channel_without_selection_write(self):
        dev, peer = self.device(applied=True)
        for ch in (3, 2, 1):
            count = peer.rx.count(f'INST:NSEL {ch}') + 1
            dev._select(ch)
            self.assertTrue(peer.await_command(f'INST:NSEL {ch}', occurrences=count))
            before = len(peer.rx)
            self.assertEqual(dev.selected_channel_name(), f'CH{ch}')
            self.assertEqual(peer.rx[before:], ['INST?'])
        self.check_wire(peer)

    def test_selected_name_accepts_only_documented_exact_forms(self):
        for raw in ('', 'CH4', '1', 'ch1', 'CH1,CH2', 'UNKNOWN'):
            with self.subTest(raw=raw):
                dev, peer = self.device(response_overrides={'INST?': raw})
                with self.assertRaises(InstrumentError):
                    dev.selected_channel_name()
                self.assertTrue(dev.faulted)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.selected_channel_name()
                self.assertEqual(peer.rx, before)
                self.assertEqual(peer.rx.count('INST?'), 1)
                self.check_wire(peer)

    def test_selected_name_timeout_is_terminal_and_cannot_retry(self):
        dev, peer = self.device(drop='INST?')
        with self.assertRaises(Exception):
            dev.selected_channel_name()
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.selected_channel_name()
        self.assertEqual(peer.rx, before)
        self.assertNotIn('OUTP 0', peer.rx)
        self.check_wire(peer)

    def test_source_steps_are_preset_queries_not_up_or_down(self):
        dev, peer = self.device(applied=True)
        for ch in (1, 2, 3):
            for kind, command, expected in (('voltage', 'VOLT:STEP?', .01),
                                            ('current', 'CURR:STEP?', .001)):
                before = len(peer.rx)
                self.assertEqual(dev.source_step(ch, kind), expected)
                commands = peer.rx[before:]
                self.assertEqual(commands[-2:], [f'INST:NSEL {ch}', command])
                self.assertFalse(any(c.endswith((':UP', ':DOWN')) for c in commands))
                self.assertNotIn('OUTP 1', commands)
                self.assertNotIn('VOLT:STEP 0.01', commands)
        self.check_wire(peer)

    def test_step_zero_preset_is_allowed_observation_not_an_action(self):
        dev, peer = self.device(applied=True,
                                response_overrides={'VOLT:STEP?': '0', 'CURR:STEP?': '0.0'})
        self.assertEqual(dev.source_step(1, 'voltage'), 0.0)
        self.assertEqual(dev.source_step(2, 'current'), 0.0)
        self.assertTrue(dev.armed)
        self.check_wire(peer)

    def test_step_kind_and_channel_refused_before_uart(self):
        dev, peer = self.device(applied=True)
        before = list(peer.rx)
        for bad_ch in (0, 4, True, 1.0, '1', None):
            with self.subTest(ch=bad_ch), self.assertRaises(ValueError):
                dev.source_step(bad_ch, 'voltage')
        for bad_kind in (None, '', 'VOLT:STEP?', 'Voltage', 'current;OUTP 1', True):
            with self.subTest(kind=bad_kind), self.assertRaises(ValueError):
                dev.source_step(1, bad_kind)
        self.assertEqual(peer.rx, before)
        self.check_wire(peer)

    def test_step_requires_armed_profile_no_scpi_emitted(self):
        dev, peer = self.device()
        with self.assertRaises(SafetyError):
            dev.source_step(1, 'voltage')
        self.assertEqual(peer.rx, [])

    def test_step_refuses_latched_session_without_wire(self):
        dev, peer = self.device(applied=True)
        dev._latch_fault(link_uncertain=True)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.source_step(2, 'voltage')
        self.assertEqual(peer.rx, before)
        self.check_wire(peer)

    def test_profile_integrity_drift_prevents_source_step_query(self):
        dev, peer = self.device(applied=True)
        peer.channel_registers[3]['VOLT:LIM'] = '4.99'
        with self.assertRaises(SafetyError):
            dev.source_step(3, 'current')
        self.assertTrue(dev.faulted)
        self.assertNotIn('CURR:STEP?', peer.rx)
        self.check_wire(peer)

    def test_step_rejects_numeric_nonfinite_and_negative_reply(self):
        for raw in ('nan', 'inf', '-0.001', '-1', 'bad value', ''):
            with self.subTest(raw=raw):
                dev, peer = self.device(applied=True, response_overrides={'CURR:STEP?': raw})
                with self.assertRaises(InstrumentError):
                    dev.source_step(1, 'current')
                self.assertTrue(dev.faulted)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.source_step(1, 'current')
                self.assertEqual(peer.rx, before)
                self.assertEqual(peer.rx.count('CURR:STEP?'), 1)
                self.check_wire(peer)

    def test_step_out_of_rated_bounds_fails_closed(self):
        for kind, ch, query, reply in (('voltage', 3, 'VOLT:STEP?', '5.01'),
                                      ('voltage', 1, 'VOLT:STEP?', '30.01'),
                                      ('current', 2, 'CURR:STEP?', '3.01')):
            with self.subTest(kind=kind, channel=ch):
                dev, peer = self.device(applied=True, response_overrides={query: reply})
                with self.assertRaises(InstrumentError):
                    dev.source_step(ch, kind)
                self.assertTrue(dev.faulted)
                self.assertEqual(peer.rx.count(query), 1)
                self.check_wire(peer)

    def test_step_timeout_never_retries_or_sends_off(self):
        dev, peer = self.device(applied=True, drop='VOLT:STEP?')
        off_before = peer.rx.count('OUTP 0')
        with self.assertRaises(Exception):
            dev.source_step(1, 'voltage')
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.rx.count('VOLT:STEP?'), 1)
        self.assertEqual(peer.rx.count('OUTP 0'), off_before)
        self.check_wire(peer)

    def test_step_writes_and_up_down_remain_service_blocked(self):
        dev, peer = self.device(applied=True)
        before = list(peer.rx)
        for command in ('VOLT:STEP 0.01', 'CURR:STEP 0.01', 'VOLT:UP',
                        'VOLT:DOWN', 'CURR:UP', 'CURR:DOWN',
                        'VOLT:TRIG?', 'CURR:TRIG?'):
            self.assertTrue(dev._service_only_command(command), command)
        self.assertFalse(dev._service_only_command('VOLT:STEP?'))
        self.assertFalse(dev._service_only_command('CURR:STEP?'))
        self.assertEqual(peer.rx, before)
        self.check_wire(peer)

    def test_forbidden_step_and_trigger_wire_even_in_debug_bypass(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.14)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport, debug=True, debug_bypass_profile=True)
        for command in ('VOLT:STEP 0.01', 'CURR:STEP 0.01',
                        'VOLT:UP', 'CURR:DOWN'):
            with self.subTest(command=command), self.assertRaises(SafetyError):
                dev.diagnostic_write(command)
        for command in ('VOLT:TRIG?', 'CURR:TRIG?'):
            with self.subTest(command=command), self.assertRaises(SafetyError):
                dev.diagnostic_query(command)
        self.assertEqual(peer.rx, [])
        self.check_wire(peer)

    def test_symbolic_channel_not_reusable_after_fault(self):
        dev, peer = self.device()
        dev._latch_fault(link_uncertain=True)
        with self.assertRaises(SafetyError):
            dev.selected_channel_name()
        self.assertEqual(peer.rx, [])


if __name__ == '__main__':
    unittest.main()
