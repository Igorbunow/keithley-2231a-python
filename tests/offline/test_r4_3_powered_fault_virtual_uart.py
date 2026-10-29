"""Armed profile shutdown/error handling using real driver through virtual UART.

No real hardware, no service-mode access. Wire fixture responds only to the
SCPI registers required by apply_profile, output, and OFF readback.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import Keithley2231A, Profile, ChannelProfile, InstrumentError, SafetyError, TransportError
from support.virtual_uart import UARTPeer, open_test_transport


def limited_profile():
    return Profile('pty-fault-check', (
        ChannelProfile(3.3, .2, 1.0, .1),
        ChannelProfile(5.0, .3, 1.0, .1),
        ChannelProfile(3.3, .1, 1.0, .05)),
        lock_front_panel=False, verify_every_s=20)


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class PoweredFaultUartTests(unittest.TestCase):
    def setUp(self):
        self.peer = UARTPeer(profile_registers=True)
        self.addCleanup(self.peer.close)
        self.transport = open_test_transport(self.peer, timeout=.13)
        self.addCleanup(self.transport.close)
        self.dev = Keithley2231A(self.transport)
        self.dev.apply_profile(limited_profile(), confirmed_wiring='independent')
        self.assertTrue(self.dev.armed)
        self.assertEqual(self.peer.unsupported, [])
        self.assertNotIn('OUTP 1', self.peer.rx)
        self.dev.output(1, True)
        self.assertEqual(self.peer.off_by_channel.get(1), 1)
        self.assertTrue(self.dev.armed)
        self.baseline = list(self.peer.rx)

    def check_bytes(self):
        self.assertTrue(self.peer.await_received(len(self.peer.rx)))
        self.assertEqual(self.peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in self.peer.rx])
        self.assertEqual(self.peer.unsupported, [])
        self.assertNotIn('OUTP 1', self.peer.rx)

    def test_nonzero_error_shuts_down_once_and_confirms_all_three_off(self):
        self.peer.fault_code = '120,"Out of range"'
        with self.assertRaisesRegex(InstrumentError, 'output OFF confirmed by readback'):
            self.dev.check_errors()
        self.assertTrue(self.peer.await_received(len(self.baseline) + 2))
        tail = self.peer.rx[len(self.baseline):]
        self.assertEqual(tail[0:2], ['SYST:ERR?', 'OUTP 0'])
        self.assertEqual(tail.count('SYST:ERR?'), 1)
        self.assertEqual(tail.count('OUTP 0'), 1)
        self.assertEqual(tail.count('CHAN:OUTP?'), 3)
        self.assertFalse(any(self.peer.off_by_channel.values()))
        self.assertTrue(self.dev.faulted)
        self.assertFalse(self.dev.armed)
        before = list(self.peer.rx)
        with self.assertRaises(SafetyError):
            self.dev.output(1, True)
        self.assertEqual(self.peer.rx, before)
        self.check_bytes()

    def test_global_off_ignored_one_channel_fallback_confirms_off(self):
        self.peer.ignore_global_off = True
        self.peer.fault_code = '120,"Out of range"'
        with self.assertRaisesRegex(InstrumentError, 'output OFF confirmed by readback'):
            self.dev.check_errors()
        tail = self.peer.rx[len(self.baseline):]
        self.assertEqual(tail.count('SYST:ERR?'), 1)
        self.assertEqual(tail.count('OUTP 0'), 1)
        self.assertEqual(tail.count('CHAN:OUTP 0'), 1)
        self.assertEqual(tail.count('CHAN:OUTP?'), 6)
        self.assertEqual(self.peer.off_by_channel[1], 0)
        self.assertTrue(self.dev.faulted)
        self.assertFalse(self.dev.armed)
        self.check_bytes()

    def test_off_ignored_then_per_channel_fallback_cannot_claim_confirmed(self):
        self.peer.ignore_shutdown = True
        self.peer.fault_code = '120,"Out of range"'
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            self.dev.check_errors()
        tail = self.peer.rx[len(self.baseline):]
        self.assertEqual(tail.count('SYST:ERR?'), 1)
        self.assertEqual(tail.count('OUTP 0'), 1)
        self.assertEqual(tail.count('CHAN:OUTP 0'), 1)
        self.assertEqual(self.peer.off_by_channel[1], 1)
        self.assertTrue(self.dev.faulted)
        self.assertFalse(self.dev.armed)
        self.check_bytes()

    def test_lost_reply_during_off_readback_forbids_followup(self):
        self.peer.fault_code = '120,"Out of range"'
        self.peer.drop = 'CHAN:OUTP?'
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            self.dev.check_errors()
        tail = self.peer.rx[len(self.baseline):]
        self.assertEqual(tail.count('SYST:ERR?'), 1)
        self.assertEqual(tail.count('OUTP 0'), 1)
        self.assertEqual(tail.count('CHAN:OUTP?'), 1)
        self.assertEqual(tail[-1], 'CHAN:OUTP?')
        before = list(self.peer.rx)
        with self.assertRaises(TransportError):
            self.dev.emergency_off()
        self.assertEqual(self.peer.rx, before)
        self.assertTrue(self.dev.faulted)
        self.assertFalse(self.dev.armed)
        self.check_bytes()

    def test_malformed_error_while_output_on_no_shutdown(self):
        self.peer.overrides['SYST:ERR?'] = 'not-an-error-code'
        with self.assertRaisesRegex(InstrumentError, 'UNKNOWN OUTPUT STATE'):
            self.dev.check_errors()
        self.assertEqual(self.peer.rx[len(self.baseline):], ['SYST:ERR?'])
        self.assertEqual(self.peer.off_by_channel[1], 1)
        with self.assertRaises(TransportError):
            self.dev.emergency_off()
        self.assertEqual(self.peer.rx[len(self.baseline):], ['SYST:ERR?'])
        self.check_bytes()


if __name__ == '__main__':
    unittest.main()
