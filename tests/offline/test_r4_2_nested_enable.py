"""R4.2-C nested enable-mask queries through production Keithley driver and PTY."""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class NestedEnableTests(unittest.TestCase):
    def device(self, **peer_options):
        peer = UARTPeer(**peer_options)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        return Keithley2231A(wire), peer

    def assert_wire(self, peer, expected):
        self.assertEqual(peer.rx, expected)
        self.assertEqual(peer.rx_bytes, [(x + '\r\n').encode('ascii') for x in expected])
        self.assertEqual(peer.unsupported, [])
        self.assertFalse(any(':EVEN?' in c or c in ('*ESR?', '*CLS', 'STAT:PRES')
                             for c in peer.rx))

    def test_all_eight_masks_exact_wire_and_selected_channel_unchanged(self):
        dev, peer = self.device()
        peer.selected = 2
        expected = []
        for group, base, offset in [('questionable', 'STAT:QUES:INST', 0),
                                    ('operation', 'STAT:OPER:INST', 100)]:
            for ch in (None, 1, 2, 3):
                cmd = f'{base}:ENAB?' if ch is None else f'{base}:ISUM{ch}:ENAB?'
                expected.append(cmd)
                value = offset + 11 + (0 if ch is None else ch)
                self.assertEqual(dev.status_instrument_enable_mask(group, channel=ch), value)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', *expected])
        self.assertEqual(peer.selected, 2)
        self.assertFalse(dev.faulted)

    def test_invalid_register_and_channel_never_open_wire(self):
        dev, peer = self.device()
        for group in ('', None, '*ESR?', 'STAT:QUES:INST:ENAB?', 'Questionable',
                      'operation;*CLS', 0, True, ('operation',)):
            with self.subTest(group=group), self.assertRaises(ValueError):
                dev.status_instrument_enable_mask(group)
        for ch in (0, 4, -1, 1., True, False, '1', '', [], (1,), '1;OUTP 1'):
            with self.subTest(channel=ch), self.assertRaises(ValueError):
                dev.status_instrument_enable_mask('operation', channel=ch)
        self.assert_wire(peer, [])

    def test_output_on_does_not_trigger_output_reads_or_writes(self):
        dev, peer = self.device(off_by_channel={3: 1})
        self.assertEqual(dev.status_instrument_enable_mask('questionable', channel=3), 14)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'STAT:QUES:INST:ISUM3:ENAB?'])

    def test_armed_flag_and_selection_not_mutated_by_mask(self):
        dev, peer = self.device()
        # Only the pre-existing driver state is injected: all SCPI still uses PTY.
        dev.identity()
        dev.remote(lock_panel=False)
        dev._armed = True
        dev._profile = object()
        peer.selected = 3
        self.assertEqual(dev.status_instrument_enable_mask('operation', channel=1), 112)
        self.assertTrue(dev.armed)
        self.assertIsNotNone(dev._profile)
        self.assertEqual(peer.selected, 3)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'STAT:OPER:INST:ISUM1:ENAB?'])

    def test_bad_mask_terminal_without_followup(self):
        for reply in ('NaN', '-1', '256', '1e2', '1.0', '', 'true', '0x10', '99999'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'STAT:OPER:INST:ISUM2:ENAB?': reply})
                with self.assertRaises(InstrumentError):
                    dev.status_instrument_enable_mask('operation', channel=2)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.status_instrument_enable_mask('questionable')
                self.assertTrue(dev.faulted)
                self.assertEqual(before[-1], 'STAT:OPER:INST:ISUM2:ENAB?')
                self.assert_wire(peer, before)

    def test_reply_loss_terminal_no_retry(self):
        dev, peer = self.device(drop='STAT:QUES:INST:ENAB?')
        with self.assertRaises(OSError):
            dev.status_instrument_enable_mask('questionable')
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.status_instrument_enable_mask('operation', channel=3)
        self.assertTrue(dev.faulted)
        self.assertEqual(before[-1], 'STAT:QUES:INST:ENAB?')
        self.assert_wire(peer, before)

    def test_faulted_session_rejects_without_wire(self):
        dev, peer = self.device()
        dev._latch_fault(link_uncertain=True)
        with self.assertRaises(SafetyError):
            dev.status_instrument_enable_mask('questionable', channel=1)
        self.assert_wire(peer, [])

    def test_nested_masks_do_not_consume_events_or_change_outputs(self):
        dev, peer = self.device()
        for group in ('questionable', 'operation'):
            for ch in (None, 1, 2, 3):
                dev.status_instrument_enable_mask(group, channel=ch)
        expected = ['*IDN?', 'SYST:REM'] + [
            f'STAT:{base}:INST' + (':ENAB?' if ch is None else f':ISUM{ch}:ENAB?')
            for base in ('QUES', 'OPER') for ch in (None, 1, 2, 3)]
        self.assert_wire(peer, expected)
        self.assertFalse(any(c.startswith(('OUTP ', 'CHAN:OUTP ', 'INST:NSEL ')) for c in peer.rx))


if __name__ == '__main__':
    unittest.main()
