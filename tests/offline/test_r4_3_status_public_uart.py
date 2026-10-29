"""Public *PSC? / *OPC? tests over the real driver and a strict POSIX UART.

Runner-specific whitelist/evidence tests stay in their own hardware-scope files;
this module replaces the public API tests formerly backed by FakeTransport.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, InstrumentError, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class PublicStatusUARTTests(unittest.TestCase):
    def device(self, **fixture):
        peer = UARTPeer(**fixture)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.12)
        self.addCleanup(transport.close)
        return Keithley2231A(transport), peer

    def wire(self, peer, commands):
        self.assertTrue(peer.await_received(len(commands)), 'incomplete UART exchange')
        self.assertEqual(peer.rx, commands)
        self.assertEqual(peer.rx_bytes, [(cmd+'\r\n').encode('ascii') for cmd in commands])
        self.assertEqual(peer.unsupported, [])
        self.assertFalse(any(c.startswith(('OUTP ', 'CHAN:OUTP ', 'CAL:', '*RST', '*CLS'))
                             for c in commands))

    def test_psc_zero_one_and_whitespace(self):
        for reply, expected in [('0', 0), ('1', 1), (' 1 ', 1)]:
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'*PSC?': reply})
                self.assertEqual(dev.power_on_status_clear_state(), expected)
                self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
                self.assertFalse(dev.faulted)

    def test_psc_invalid_response_terminal(self):
        for reply in ('2', '-1', '1.0', '0.0', 'NaN', '1e0', '', 'ON', 'True', '99999', '00.1'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'*PSC?': reply})
                with self.assertRaises(InstrumentError):
                    dev.power_on_status_clear_state()
                self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
                with self.assertRaises(SafetyError):
                    dev.power_on_status_clear_state()
                self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
                self.assertTrue(dev.faulted)

    def test_psc_read_does_not_change_live_outputs(self):
        dev, peer = self.device(off_by_channel={2: 1})
        self.assertEqual(dev.power_on_status_clear_state(), 0)
        self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
        self.assertEqual(peer.off_by_channel[2], 1)

    def test_psc_faulted_before_first_byte(self):
        dev, peer = self.device()
        dev._latch_fault()
        with self.assertRaises(SafetyError):
            dev.power_on_status_clear_state()
        self.wire(peer, [])

    def test_psc_timeout_is_terminal_without_retry(self):
        dev, peer = self.device(drop='*PSC?')
        with self.assertRaises(OSError):
            dev.power_on_status_clear_state()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
        with self.assertRaises(SafetyError):
            dev.power_on_status_clear_state()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*PSC?'])
        self.assertTrue(dev.faulted)

    def test_opc_exact_one_success(self):
        dev, peer = self.device(response_overrides={'*OPC?': ' 1 '})
        self.assertIs(dev.operation_complete(), True)
        self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
        self.assertFalse(dev.faulted)

    def test_opc_malformed_terminal_without_retry(self):
        # Transport framing failures (embedded CR/LF) have their own tests:
        # those are not valid single-line SCPI replies.
        for reply in ('0','2','-1','1.0','1e0','NaN','ON','', '1,0','01','true',' 1 0 '):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'*OPC?': reply})
                with self.assertRaises(InstrumentError):
                    dev.operation_complete()
                self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
                with self.assertRaises(SafetyError):
                    dev.operation_complete()
                self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
                self.assertTrue(dev.faulted)

    def test_opc_timeout_terminal(self):
        dev, peer = self.device(drop='*OPC?')
        with self.assertRaises(OSError):
            dev.operation_complete()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
        with self.assertRaises(SafetyError):
            dev.operation_complete()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
        self.assertTrue(dev.faulted)

    def test_opc_faulted_before_wire(self):
        dev, peer = self.device()
        dev._latch_fault()
        with self.assertRaises(SafetyError):
            dev.operation_complete()
        self.wire(peer, [])

    def test_opc_rejects_armed_profile_or_sequencer_without_wire(self):
        for attr, value in (('_armed', True), ('_profile', object()), ('_sequencing', True)):
            with self.subTest(attr=attr):
                dev, peer = self.device()
                setattr(dev, attr, value)
                with self.assertRaises(SafetyError):
                    dev.operation_complete()
                self.wire(peer, [])

    def test_opc_garbage_after_valid_digit_is_terminal(self):
        # The read path must not treat a multi-token *OPC? reply as success.
        dev, peer = self.device(response_overrides={'*OPC?': '1,0'})
        with self.assertRaises(InstrumentError):
            dev.operation_complete()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])
        with self.assertRaises(SafetyError):
            dev.operation_complete()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*OPC?'])


if __name__ == '__main__':
    unittest.main()
