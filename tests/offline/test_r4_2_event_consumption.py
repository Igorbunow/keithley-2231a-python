"""R4.2-B destructive-event reads: production Keithley2231A over a POSIX UART.

The virtual peer supplies read-to-clear latches and records the raw wire bytes.
Only one event query is allowed per explicit call. This is NOT firmware proof.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from support.virtual_uart import UARTPeer, open_test_transport


EVENTS = {'standard_event': ('*ESR?', 7),
          'questionable': ('STAT:QUES:EVEN?', 129),
          'operation': ('STAT:OPER:EVEN?', 255)}


def off_preflight(selected=1):
    """Independently expected SCPI preflight, including selection restoration."""
    result = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?']
    for ch in (1, 2, 3):
        result.extend((f'INST:NSEL {ch}', 'INST:NSEL?', 'CHAN:OUTP?'))
    return result + [f'INST:NSEL {selected}']


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class EventConsumptionTests(unittest.TestCase):
    def device(self, **opts):
        peer = UARTPeer(**opts)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        return Keithley2231A(wire), peer

    def assert_wire(self, peer, expected):
        self.assertTrue(peer.await_received(len(expected)), 'virtual UART did not receive expected commands')
        self.assertEqual(peer.rx, expected)
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in expected])
        self.assertEqual(peer.unsupported, [])
        self.assertNotIn('SYST:ERR?', expected)  # never drain the SCPI error queue
        for forbidden in ('*CLS', 'STAT:PRES', 'OUTP 0', 'OUTP 1', '*RST'):
            self.assertNotIn(forbidden, expected)
        self.assertFalse(any(cmd.startswith('CHAN:OUTP ') for cmd in expected))

    def test_one_explicit_query_each_event_and_only_selected_latch_cleared(self):
        dev, peer = self.device()
        expected = []
        remaining = dict(peer.event_latches)
        for i, (register, (cmd, value)) in enumerate(EVENTS.items()):
            expected += (off_preflight() if i == 0 else off_preflight()[2:])
            expected.append(cmd)
            self.assertEqual(dev.consume_status_event(register, acknowledge_clear=True), value)
            self.assert_wire(peer, expected)
            remaining[cmd] = '0'
            self.assertEqual(peer.event_latches, remaining)
        self.assertEqual(peer.selected, 1)
        self.assertFalse(dev.armed or dev.faulted)

    def test_event_latch_is_read_to_clear_only_for_explicit_second_call(self):
        dev, peer = self.device()
        self.assertEqual(dev.consume_status_event('standard_event', acknowledge_clear=True), 7)
        first = list(peer.rx)
        self.assertEqual(peer.rx.count('*ESR?'), 1)
        self.assertEqual(peer.event_latches['*ESR?'], '0')
        self.assertEqual(dev.consume_status_event('standard_event', acknowledge_clear=True), 0)
        self.assertEqual(peer.rx.count('*ESR?'), 2)
        self.assertEqual(peer.rx[:len(first)], first)
        self.assertEqual(peer.rx[-1], '*ESR?')
        self.assert_wire(peer, first + off_preflight()[2:] + ['*ESR?'])

    def test_ack_and_register_whitelist_rejected_before_uart(self):
        dev, peer = self.device()
        for name in ('*ESR?', 'operation:EVEN', 'STAT:OPER:EVEN?',
                     'questionable;*CLS', 'all', '', None, 0, True, ('operation',)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                dev.consume_status_event(name, acknowledge_clear=True)
        for ack in (None, False, 1, 'True', (), 0):
            with self.subTest(ack=ack), self.assertRaises(ValueError):
                dev.consume_status_event('standard_event', acknowledge_clear=ack)
        with self.assertRaises(TypeError):
            dev.consume_status_event('standard_event')
        self.assert_wire(peer, [])

    def test_each_output_on_refuses_without_consuming_any_event(self):
        for ch in (1, 2, 3):
            with self.subTest(channel=ch):
                dev, peer = self.device(off_by_channel={ch: 1})
                with self.assertRaisesRegex(SafetyError, 'OFF'):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assert_wire(peer, off_preflight())
                self.assertEqual(peer.event_latches['*ESR?'], '7')
                self.assertEqual(peer.off_by_channel[ch], 1)
                self.assertFalse(dev.faulted)

    def test_profile_armed_and_faulted_refuse_without_event_wire(self):
        dev, peer = self.device()
        dev.identity()
        dev.remote(lock_panel=False)
        dev._armed = True
        dev._profile = object()  # inject guard state, no fake transport
        with self.assertRaisesRegex(SafetyError, 'profile-free'):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM'])
        self.assertEqual(peer.event_latches['STAT:QUES:EVEN?'], '129')
        dev._latch_fault()
        with self.assertRaises(SafetyError):
            dev.consume_status_event('operation', acknowledge_clear=True)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM'])

    def test_invalid_off_readback_stops_before_event_without_restore(self):
        dev, peer = self.device(response_overrides={'CHAN:OUTP?': 'unknown'})
        with self.assertRaises(InstrumentError):
            dev.consume_status_event('standard_event', acknowledge_clear=True)
        before = list(peer.rx)
        self.assert_wire(peer, off_preflight()[:7])
        self.assertEqual(peer.event_latches['*ESR?'], '7')
        self.assertTrue(dev.faulted)
        with self.assertRaises(SafetyError):
            dev.consume_status_event('standard_event', acknowledge_clear=True)
        self.assert_wire(peer, before)

    def test_lost_off_readback_is_terminal_and_preserves_event(self):
        dev, peer = self.device(drop='CHAN:OUTP?')
        with self.assertRaises(OSError):
            dev.consume_status_event('operation', acknowledge_clear=True)
        before = list(peer.rx)
        self.assert_wire(peer, off_preflight()[:7])
        self.assertEqual(peer.event_latches['STAT:OPER:EVEN?'], '255')
        self.assertTrue(dev.faulted)
        with self.assertRaises(SafetyError):
            dev.consume_status_event('operation', acknowledge_clear=True)
        self.assert_wire(peer, before)

    def test_malformed_consumed_event_is_terminal_no_retry(self):
        for reply in ('NaN', '-1', '256', '1.1', '1e2', '', 'True', '99999'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'*ESR?': reply})
                with self.assertRaises(InstrumentError):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assert_wire(peer, off_preflight() + ['*ESR?'])
                self.assertEqual(peer.event_latches['*ESR?'], '0')
                with self.assertRaises(SafetyError):
                    dev.consume_status_event('standard_event', acknowledge_clear=True)
                self.assert_wire(peer, off_preflight() + ['*ESR?'])
                self.assertTrue(dev.faulted)

    def test_lost_consumed_event_reply_does_not_repeat_query(self):
        dev, peer = self.device(drop='STAT:QUES:EVEN?')
        with self.assertRaises(OSError):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assert_wire(peer, off_preflight() + ['STAT:QUES:EVEN?'])
        self.assertEqual(peer.event_latches['STAT:QUES:EVEN?'], '0')
        self.assertTrue(dev.faulted)
        with self.assertRaises(SafetyError):
            dev.consume_status_event('questionable', acknowledge_clear=True)
        self.assert_wire(peer, off_preflight() + ['STAT:QUES:EVEN?'])

    def test_regular_mask_does_not_consume_event(self):
        dev, peer = self.device(response_overrides={'*ESE?': '3'})
        self.assertEqual(dev.status_enable_mask('standard_event'), 3)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', '*ESE?'])
        self.assertEqual(peer.event_latches['*ESR?'], '7')
        self.assertEqual(peer.rx.count('*ESR?'), 0)

    def test_off_preflight_restores_nondefault_selected_channel(self):
        dev, peer = self.device()
        peer.selected = 2
        self.assertEqual(dev.consume_status_event('questionable', acknowledge_clear=True), 129)
        self.assert_wire(peer, off_preflight(selected=2) + ['STAT:QUES:EVEN?'])
        self.assertEqual(peer.selected, 2)

    def test_no_error_poll_or_implicit_latch_clear(self):
        dev, peer = self.device()
        self.assertEqual(dev.consume_status_event('questionable', acknowledge_clear=True), 129)
        self.assert_wire(peer, off_preflight() + ['STAT:QUES:EVEN?'])
        self.assertEqual(peer.event_latches['STAT:OPER:EVEN?'], '255')
        self.assertEqual(peer.event_latches['*ESR?'], '7')


if __name__ == '__main__':
    unittest.main()
