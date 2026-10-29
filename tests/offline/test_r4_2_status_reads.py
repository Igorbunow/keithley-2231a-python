"""R4.2-A non-destructive status API through the real driver and virtual UART."""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class StatusReadOnlyTests(unittest.TestCase):
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
        self.assertFalse(any(x in expected for x in ('*ESR?', '*CLS', 'STAT:PRES')))
        self.assertFalse(any(':EVEN?' in x for x in expected))

    def test_module_and_four_enable_masks_exact_wire(self):
        dev, peer = self.device(response_overrides={'*ESE?': '33', '*SRE?': '128'})
        self.assertEqual(dev.module_info(), '2231,1.04')
        for group, expected in [('standard_event', 33), ('service_request', 128),
                                ('questionable', 17), ('operation', 255)]:
            self.assertEqual(dev.status_enable_mask(group), expected)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'SYST:MODU?', '*ESE?', '*SRE?',
                                'STAT:QUES:ENAB?', 'STAT:OPER:ENAB?'])
        self.assertFalse(dev.armed)
        self.assertFalse(dev.faulted)

    def test_invalid_registers_rejected_before_uart(self):
        dev, peer = self.device()
        for group in ('*ESR?', 'operation:EVEN', 'STAT:QUES:ENAB?', 'module', '',
                      'Operation', None, 5, True, ('operation',), 'operation\n*CLS'):
            with self.subTest(group=group), self.assertRaises(ValueError):
                dev.status_enable_mask(group)
        self.assert_wire(peer, [])

    def test_module_aux_drift_observed_without_identity_change(self):
        dev, peer = self.device(serial='LAB-A', response_overrides={'SYST:MODU?': '2231,0.01'})
        self.assertEqual(dev.module_info(), '2231,0.01')
        self.assertEqual(dev.instrument_key[2], 'LAB-A')
        self.assertEqual(peer.rx.count('*IDN?'), 1)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'SYST:MODU?'])

    def test_read_only_module_with_output_on(self):
        dev, peer = self.device(off_by_channel={1: 1})
        self.assertEqual(dev.module_info(), '2231,1.04')
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'SYST:MODU?'])

    def test_malformed_module_faults_no_retry(self):
        for reply in ('', ' \t ', '2231\t1.04', 'X' * 129):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'SYST:MODU?': reply})
                with self.assertRaises(InstrumentError):
                    dev.module_info()
                self.assertTrue(dev.faulted)
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.module_info()
                self.assert_wire(peer, before)
                self.assertEqual(before[-1], 'SYST:MODU?')

    def test_malformed_mask_faults_no_retry(self):
        for reply in ('NaN', '1.0', '-1', '256', '00100.0', '', '1e2', '99999'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'STAT:OPER:ENAB?': reply})
                with self.assertRaises(InstrumentError):
                    dev.status_enable_mask('operation')
                before = list(peer.rx)
                with self.assertRaises(SafetyError):
                    dev.status_enable_mask('standard_event')
                self.assertTrue(dev.faulted)
                self.assertEqual(before[-1], 'STAT:OPER:ENAB?')
                self.assert_wire(peer, before)

    def test_timeout_terminal_no_followup(self):
        dev, peer = self.device(drop='SYST:MODU?')
        with self.assertRaises(OSError):
            dev.module_info()
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.status_enable_mask('operation')
        self.assertEqual(before[-1], 'SYST:MODU?')
        self.assertTrue(dev.faulted)
        self.assert_wire(peer, before)

    def test_fresh_session_one_mask_no_clear(self):
        dev, peer = self.device()
        self.assertEqual(dev.status_enable_mask('questionable'), 17)
        self.assert_wire(peer, ['*IDN?', 'SYST:REM', 'STAT:QUES:ENAB?'])

    def test_faulted_session_does_not_send_scpi(self):
        dev, peer = self.device()
        self.assertEqual(dev.module_info(), '2231,1.04')
        dev._latch_fault()
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.status_enable_mask('operation')
        self.assert_wire(peer, before)


if __name__ == '__main__':
    unittest.main()
