"""Three migrations from FakeTransport to the production driver's POSIX UART.

The peer replaces only the physical serial endpoint. All commands and terminal
safety decisions originate in Keithley2231A. No real instruments are opened.
"""
from __future__ import annotations

import os
import unittest

from keithley2231a import (Keithley2231A, InstrumentError, SafetyError,
                          TransportError)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class DriverIdentityOutputNumericUARTTests(unittest.TestCase):
    def device(self, *, applied=False, expected_serial=None, **fixture):
        peer = UARTPeer(profile_registers=True, **fixture)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.13)
        self.addCleanup(transport.close)
        dev = Keithley2231A(transport, expected_serial=expected_serial)
        if applied:
            dev.apply_profile(profile(), confirmed_wiring='independent')
            self.assertTrue(dev.armed)
            self.assertEqual(peer.unsupported, [])
        return dev, peer

    def wire(self, peer, commands):
        self.assertTrue(peer.await_received(len(commands)), 'incomplete UART exchange')
        self.assertEqual(peer.rx, commands)
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in commands])
        self.assertEqual(peer.unsupported, [])

    # Batch A: read-only output inspection and channel selection.
    def test_initial_local_with_ch2_on_is_not_an_implicit_shutdown(self):
        dev, peer = self.device(off_by_channel={2: 1})
        self.assertEqual(dev.output_states(), {1: False, 2: True, 3: False})
        expected = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?',
                    'INST:NSEL 1', 'INST:NSEL?', 'CHAN:OUTP?',
                    'INST:NSEL 2', 'INST:NSEL?', 'CHAN:OUTP?',
                    'INST:NSEL 3', 'INST:NSEL?', 'CHAN:OUTP?', 'INST:NSEL 1']
        self.wire(peer, expected)
        self.assertEqual(peer.off_by_channel[2], 1)
        self.assertFalse(dev.armed)
        self.assertNotIn('OUTP 0', peer.rx)

    def test_armed_state_survives_read_then_lost_reply_disarms_no_retry(self):
        dev, peer = self.device(applied=True)
        prior = list(peer.rx)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        self.assertTrue(dev.armed)
        self.assertNotIn('SYST:REM', peer.rx[len(prior):])
        peer.drop = 'CHAN:OUTP?'
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.output_states()
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(SafetyError):
            dev.output_states()
        self.wire(peer, before)
        self.assertNotIn('OUTP 0', peer.rx[len(prior):])

    def test_output_inspection_restores_live_ch2_selection(self):
        dev, peer = self.device(applied=True)
        count_before = len(peer.rx)
        dev._select(2)
        self.assertTrue(peer.await_received(count_before + 1))
        baseline = list(peer.rx)
        self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})
        expected_tail = ['INST:NSEL?',
                         'INST:NSEL 1', 'INST:NSEL?', 'CHAN:OUTP?',
                         'INST:NSEL 2', 'INST:NSEL?', 'CHAN:OUTP?',
                         'INST:NSEL 3', 'INST:NSEL?', 'CHAN:OUTP?',
                         'INST:NSEL 2']
        self.assertTrue(peer.await_received(len(baseline) + len(expected_tail)))
        self.assertEqual(peer.rx[len(baseline):], expected_tail)
        self.assertEqual(peer.selected, 2)
        self.assertNotIn('OUTP 0', peer.rx[len(baseline):])
        self.assertTrue(dev.armed)
        self.wire(peer, list(peer.rx))

    def test_wrong_serial_blocks_remote_entry(self):
        dev, peer = self.device(expected_serial='OTHER')
        with self.assertRaisesRegex(InstrumentError, 'serial number mismatch'):
            dev.output_states()
        self.wire(peer, ['*IDN?'])
        self.assertTrue(dev.faulted)

    # Batch B: physical identity binding versus observational firmware text.
    def test_identity_swap_before_profile_writes_is_terminal(self):
        first = 'Keithley instruments, 2231A-30-3, LAB-UNIT-001, 1.04-1.04'
        second = 'Keithley instruments, 2231A-30-3, OTHER, 1.04-1.04'
        dev, peer = self.device(response_sequences={'*IDN?': [first, second]})
        self.assertEqual(dev.identity(), first)
        with self.assertRaisesRegex(InstrumentError, 'identity changed'):
            dev.apply_profile(profile(), confirmed_wiring='independent')
        self.wire(peer, ['*IDN?', 'SYST:REM', '*IDN?'])
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.wire(peer, ['*IDN?', 'SYST:REM', '*IDN?'])

    def test_firmware_aux_revision_does_not_change_physical_key(self):
        first = 'Keithley instruments, 2231A-30-3, LAB-UNIT-001, 1.04-1.04'
        second = 'Keithley instruments, 2231A-30-3, LAB-UNIT-001, 1.04-0.01'
        dev, peer = self.device(response_sequences={'*IDN?': [first, second]})
        self.assertEqual(dev.identity(), first)
        self.assertEqual(dev.identity(), second)
        self.assertEqual(dev.instrument_key,
                         ('keithley instruments', '2231A-30-3', 'LAB-UNIT-001'))
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertFalse(dev.faulted)
        self.assertNotEqual(first, dev.instrument_id)
        self.assertEqual(peer.unsupported, [])

    def test_selection_correlated_aux_is_observation_not_device_swap(self):
        replies = ['Keithley instruments, 2231A-30-3, LAB-UNIT-001, '+fw
                   for fw in ('1.04-1.04', '1.04-0.01', '1.04-1.01', '1.04-1.04')]
        dev, peer = self.device(response_sequences={'*IDN?': replies})
        self.assertEqual(dev.identity(), replies[0])
        dev.remote()
        dev._select(3)
        self.assertEqual(dev.identity(), replies[1])
        self.assertEqual(dev.identity(), replies[2])
        dev._select(1)
        self.assertEqual(dev.identity(), replies[3])
        self.assertEqual(dev.instrument_key,
                         ('keithley instruments', '2231A-30-3', 'LAB-UNIT-001'))
        self.assertFalse(dev.faulted)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(dev.armed)
        self.assertEqual(peer.unsupported, [])

    def test_expected_serial_refuses_profile_configuration(self):
        dev, peer = self.device(expected_serial='OTHER-PSU')
        with self.assertRaisesRegex(InstrumentError, 'serial number mismatch'):
            dev.apply_profile(profile(), confirmed_wiring='independent')
        self.wire(peer, ['SYST:REM', '*IDN?'])
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertFalse(any(c.startswith(('VOLT ', 'CURR ', 'VOLT:LIM ')) for c in peer.rx))

    # Batch C: malformed numeric protocol replies from the real read path.
    def test_fractional_operation_register_stops_before_next_query(self):
        dev, peer = self.device(applied=True)
        peer.overrides['STAT:OPER:INST:ISUM1:COND?'] = '1.5'
        baseline = list(peer.rx)
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.read_channel(1)
        self.wire(peer, list(peer.rx))
        self.assertEqual(peer.rx[-1], 'STAT:OPER:INST:ISUM1:COND?')
        self.assertNotIn('OUTP 0', peer.rx[len(baseline):])
        self.assertTrue(dev.faulted)

    def test_status_mask_validation_fails_closed_without_shutdown(self):
        cases = (
            ('STAT:OPER:INST:ISUM3:COND?', '1.0000000000000001'),
            ('STAT:OPER:INST:ISUM3:COND?', '256'),
            ('STAT:QUES:INST:ISUM3:COND?', '2.0000000000000001'),
            ('STAT:QUES:INST:ISUM3:COND?', '999999'),
            ('STAT:OPER:INST:ISUM3:COND?', '-1'),
            ('STAT:OPER:INST:ISUM3:COND?', 'NaN'),
            ('STAT:OPER:INST:ISUM3:COND?', '9'*5000),
        )
        for command, malformed in cases:
            with self.subTest(command=command, value=malformed[:22]):
                dev, peer = self.device(applied=True)
                dev.output(3, True)
                baseline = list(peer.rx)
                peer.overrides[command] = malformed
                with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
                    dev.read_channel(3)
                self.assertTrue(peer.await_received(len(baseline)+1))
                self.assertEqual(peer.rx[-1], command)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertEqual(peer.off_by_channel[3], 1)
                self.assertNotIn('OUTP 0', peer.rx[len(baseline):])
                self.assertEqual(peer.unsupported, [])

    def test_out_of_range_status_byte_is_terminal(self):
        dev, peer = self.device(applied=True)
        baseline = list(peer.rx)
        peer.overrides['*STB?'] = '256'
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.status()
        self.assertEqual(peer.rx[-1], '*STB?')
        self.assertNotIn('OUTP 0', peer.rx[len(baseline):])
        self.assertTrue(dev.faulted)
        self.assertEqual(peer.unsupported, [])

    def test_malformed_measurement_on_live_channel_blocks_unsafe_off(self):
        dev, peer = self.device(applied=True)
        dev.output(1, True)
        baseline = list(peer.rx)
        peer.overrides['MEAS:VOLT?'] = 'not-a-voltage'
        with self.assertRaisesRegex(SafetyError, 'UNKNOWN OUTPUT STATE'):
            dev.read_channel(1)
        self.assertEqual(peer.rx[-1], 'MEAS:VOLT?')
        self.assertEqual(peer.off_by_channel[1], 1)
        self.assertNotIn('OUTP 0', peer.rx[len(baseline):])
        self.assertTrue(dev.faulted)
        before = list(peer.rx)
        with self.assertRaises(TransportError):
            dev.emergency_off()
        self.wire(peer, before)

@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class DebugDispatchUARTTests(unittest.TestCase):
    def peer_transport(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.13)
        self.addCleanup(transport.close)
        return peer, transport

    def test_debug_readonly_query_requires_flag_and_reenters_remote(self):
        peer, transport = self.peer_transport()
        normal = Keithley2231A(transport)
        with self.assertRaises(SafetyError):
            normal.diagnostic_query('VOLT?')
        self.assertEqual(peer.rx, [])
        events = []
        diag = Keithley2231A(transport, debug=True, debug_trace=events.append)
        self.assertEqual(diag.diagnostic_query('VOLT?'), '0.00')
        self.assertEqual(peer.rx, ['*IDN?', 'SYST:REM', 'VOLT?'])
        self.assertEqual([e['command'] for e in events
                          if e['event'] in ('write', 'query')], peer.rx)
        self.assertNotIn('LAB-UNIT-001', repr(events))
        with self.assertRaises(ValueError):
            diag.diagnostic_query('VOLT?;OUTP 1')
        before = list(peer.rx)
        self.assertEqual(peer.rx, before)
        diag.diagnostic_write('SYSTem:LOCal')
        self.assertEqual(diag.diagnostic_query('VOLT?'), '0.00')
        self.assertEqual(peer.rx[-3:], ['SYST:LOC', 'SYST:REM', 'VOLT?'])
        self.assertEqual(peer.unsupported, [])

    def test_normal_and_debug_apply_emit_identical_real_uart_lines(self):
        normal_peer, normal_transport = self.peer_transport()
        trace_peer, trace_transport = self.peer_transport()
        events = []
        normal = Keithley2231A(normal_transport)
        diagnostic = Keithley2231A(trace_transport, debug=True,
                                   debug_trace=events.append)
        normal.apply_profile(profile(), confirmed_wiring='independent')
        diagnostic.apply_profile(profile(), confirmed_wiring='independent')
        self.assertTrue(normal_peer.await_received(76))
        self.assertTrue(trace_peer.await_received(76))
        self.assertEqual(normal_peer.rx, trace_peer.rx)
        self.assertEqual(normal_peer.rx_bytes, trace_peer.rx_bytes)
        self.assertEqual([e['command'] for e in events
                          if e['event'] in ('write', 'query')], normal_peer.rx)
        self.assertEqual(normal_peer.unsupported, [])
        self.assertEqual(trace_peer.unsupported, [])
        self.assertTrue(normal.armed and diagnostic.armed)

    def test_debug_adapter_cannot_send_directly(self):
        peer, transport = self.peer_transport()
        diag = Keithley2231A(transport, debug=True,
                             debug_adapter=lambda controlled: controlled)
        with self.assertRaises(SafetyError):
            diag.debug_transport.query('VOLT?')
        self.assertEqual(peer.rx, [])
        self.assertEqual(diag.diagnostic_query('VOLT?'), '0.00')
        self.assertEqual(peer.rx, ['*IDN?', 'SYST:REM', 'VOLT?'])
        self.assertEqual(peer.unsupported, [])


if __name__ == '__main__':
    unittest.main()
