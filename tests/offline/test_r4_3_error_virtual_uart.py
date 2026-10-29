"""SCPI error and malformed-reply guards through the production driver over PTY.

Migration from test_driver.FakeTransport: instrument selection, output state,
identity and error queue. Exact bytes observed at a POSIX UART peer. No
physical instrument or independently implemented driver is involved.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError, TransportError
from support.virtual_uart import UARTPeer, open_test_transport


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class ErrorVirtualUartTests(unittest.TestCase):
    def device(self, **peer_config):
        peer = UARTPeer(**peer_config)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        return Keithley2231A(wire), peer

    def assert_wire(self, peer, expected):
        self.assertTrue(peer.await_received(len(expected)), 'incomplete PTY wire trace')
        self.assertEqual(peer.rx, expected)
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in expected])
        self.assertEqual(peer.unsupported, [])
        self.assertFalse(any(cmd in ('OUTP 0', 'OUTP 1', '*RST', '*CLS') for cmd in peer.rx))

    def test_malformed_boolean_output_state_preserves_unknown_without_restore(self):
        # CH1 reports OFF; CH2 reports invalid boolean while physically ON.
        # The query must abort on CH2, never select CH3 or restore CH1.
        dev, peer = self.device(off_by_channel={2: 1},
                                response_sequences={'CHAN:OUTP?': ['0', 'maybe']})
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE.*link uncertain'):
            dev.output_states()
        expected = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?',
                    'INST:NSEL 1', 'INST:NSEL?', 'CHAN:OUTP?',
                    'INST:NSEL 2', 'INST:NSEL?', 'CHAN:OUTP?']
        self.assert_wire(peer, expected)
        self.assertEqual(peer.selected, 2)
        self.assertEqual(peer.off_by_channel[2], 1)
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.output_states()
        self.assert_wire(peer, before)

    def test_invalid_selected_channel_reply_never_issues_next_command(self):
        for reply in ('2.5', '4', 'nan'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'INST:NSEL?': reply})
                with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
                    dev.output_states()
                expected = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?']
                self.assert_wire(peer, expected)
                self.assertTrue(dev.faulted)
                with self.assertRaises(SafetyError):
                    dev.output_states()
                self.assert_wire(peer, expected)

    def test_unexpected_identity_is_terminal_without_off_or_remote_retry(self):
        dev, peer = self.device(response_overrides={'*IDN?': 'not-a-complete-identity'})
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.output_states()
        self.assert_wire(peer, ['*IDN?'])
        self.assertTrue(dev.faulted)
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.assert_wire(peer, ['*IDN?'])

    def test_scpi_error_without_armed_profile_does_not_change_outputs(self):
        dev, peer = self.device(fault_code='100,Invalid')
        with self.assertRaisesRegex(InstrumentError, 'no active profile'):
            dev.check_errors()
        self.assert_wire(peer, ['SYST:ERR?'])
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)

    def test_malformed_error_queue_reply_blocks_followup_and_no_shutdown(self):
        for reply in ('INVALID', '120', '120,', '', 'NaN,"error"'):
            with self.subTest(reply=reply):
                dev, peer = self.device(response_overrides={'SYST:ERR?': reply})
                with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
                    dev.check_errors()
                self.assert_wire(peer, ['SYST:ERR?'])
                self.assertTrue(dev.faulted)
                with self.assertRaises(TransportError):
                    dev.emergency_off()
                self.assert_wire(peer, ['SYST:ERR?'])

    def test_dropped_error_queue_reply_no_retry_no_shutdown(self):
        dev, peer = self.device(drop='SYST:ERR?')
        with self.assertRaises(OSError):
            dev.check_errors()
        self.assert_wire(peer, ['SYST:ERR?'])
        self.assertTrue(dev.faulted)
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.assert_wire(peer, ['SYST:ERR?'])

    def test_malformed_output_reply_has_no_extra_status_poll(self):
        dev, peer = self.device(response_overrides={'CHAN:OUTP?': 'OFF?'},
                                off_by_channel={1: 1})
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.output_states()
        expected = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?',
                    'INST:NSEL 1', 'INST:NSEL?', 'CHAN:OUTP?']
        self.assert_wire(peer, expected)
        self.assertNotIn('SYST:ERR?', peer.rx)
        self.assertEqual(peer.off_by_channel[1], 1)


if __name__ == '__main__':
    unittest.main()
