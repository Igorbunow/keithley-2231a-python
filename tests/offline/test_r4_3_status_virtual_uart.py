"""R4.3 PSC/OPC hardware stage -> PRODUCTION driver -> POSIX UART wire.

No simulated driver, no live instrument. Exact wire trace and fail-stop are
checked independently of a high-level PASS/outcome or in-memory driver calls.
"""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import unittest

from keithley2231a import SerialTransport
from support.virtual_uart import PTYTransport, UARTPeer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_3_opc_stage import run_opc
from r4_3_psc_stage import run_psc


class Evidence:
    def __init__(self):
        self.events = []

    def add(self, event, **fields):
        record = dict(event=event, **fields)
        self.events.append(record)
        return record


def expected_wire(command):
    """Independent expected transmission sequence from qualified R4.3 runs."""
    operations = ['*IDN?', 'SYST:REM', 'OUTP:SER?', 'INST:NSEL?']
    for ch in (1, 2, 3):
        operations += [f'INST:NSEL {ch}', 'INST:NSEL?', 'CHAN:OUTP?']
    operations += [
        'INST:NSEL 1', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
        'INST:NSEL?', '*ESE?', '*SRE?', command, '*ESE?', '*SRE?',
        'INST:NSEL?', 'OUTP:SER?', 'INST:NSEL?',
    ]
    for ch in (1, 2, 3):
        operations += [f'INST:NSEL {ch}', 'INST:NSEL?', 'CHAN:OUTP?']
    operations += ['INST:NSEL 1', '*IDN?']
    return [part for op in operations for part in (op, 'SYST:ERR?')] + ['SYST:LOC']


@unittest.skipUnless(os.name == 'posix', 'requires Linux/POSIX pseudoterminal')
class StatusHardwareVirtualUartTests(unittest.TestCase):
    def stage(self, operation, **peer_options):
        peer = UARTPeer(**peer_options)
        self.addCleanup(peer.close)
        try:
            import serial  # noqa: F401
        except ImportError:
            wire = PTYTransport(peer.path, timeout=.17)
        else:
            wire = SerialTransport(peer.path, timeout=.17)
        # R4.3 stages own and close the transport; a double close is not needed.
        ev = Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            result = (run_opc if operation == '*OPC?' else run_psc)(
                wire, ev, final_gate=lambda _prompt: None)
        if result == 'PASS':
            self.assertTrue(peer.await_received(75), 'missing final SYST:LOC on virtual UART')
        return result, peer, ev

    def assert_clean_wire(self, peer, command):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx, expected_wire(command))
        self.assertEqual(len(peer.rx), 75)
        self.assertEqual(peer.rx.count('SYST:ERR?'), 37)
        self.assertEqual(peer.rx.count(command), 1)
        self.assertEqual(peer.rx.count('CHAN:OUTP?'), 6)
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])
        self.assertFalse(any(cmd.startswith(('OUTP ', 'VOLT ', 'CURR ', 'CAL:'))
                             for cmd in peer.rx))

    def test_opc_full_wire_and_result(self):
        result, peer, ev = self.stage('*OPC?')
        self.assertEqual(result, 'PASS')
        self.assert_clean_wire(peer, '*OPC?')
        self.assertEqual(ev.events[-1]['outcome'], 'PASS')
        self.assertTrue(ev.events[-1]['physical_off_confirmed'])

    def test_psc_full_wire_and_result(self):
        result, peer, ev = self.stage('*PSC?')
        self.assertEqual(result, 'PASS')
        self.assert_clean_wire(peer, '*PSC?')
        self.assertEqual(ev.events[-1]['outcome'], 'PASS')

    def test_opc_bad_reply_aborts_without_error_poll_after_it(self):
        for value in ('0', '01', '2', 'ON', '1.0', '1,0'):
            with self.subTest(value=value):
                result, peer, ev = self.stage('*OPC?', response_overrides={'*OPC?': value})
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(peer.rx[-1], '*OPC?')
                self.assertNotIn('SYST:LOC', peer.rx)
                self.assertFalse(ev.events[-1]['link_known'])
                self.assertEqual(peer.unsupported, [])

    def test_psc_bad_reply_aborts_without_error_poll_after_it(self):
        for value in ('2', '01', '-1', 'ON'):
            with self.subTest(value=value):
                result, peer, ev = self.stage('*PSC?', response_overrides={'*PSC?': value})
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(peer.rx[-1], '*PSC?')
                self.assertNotIn('SYST:LOC', peer.rx)
                self.assertFalse(ev.events[-1]['link_known'])
                self.assertEqual(peer.unsupported, [])

    def test_opc_timeout_no_repeat_or_followup(self):
        result, peer, ev = self.stage('*OPC?', drop='*OPC?')
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(peer.rx[-1], '*OPC?')
        self.assertEqual(peer.rx.count('*OPC?'), 1)
        self.assertFalse(ev.events[-1]['link_known'])

    def test_psc_timeout_no_repeat_or_followup(self):
        result, peer, ev = self.stage('*PSC?', drop='*PSC?')
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(peer.rx[-1], '*PSC?')
        self.assertEqual(peer.rx.count('*PSC?'), 1)
        self.assertFalse(ev.events[-1]['link_known'])

    def test_any_output_on_blocks_query_before_wire(self):
        for command in ('*OPC?', '*PSC?'):
            for ch in (1, 2, 3):
                with self.subTest(command=command, ch=ch):
                    result, peer, ev = self.stage(command, off_by_channel={ch: 1})
                    self.assertEqual(result, 'ABORTED')
                    self.assertNotIn(command, peer.rx)
                    self.assertNotIn('SYST:LOC', peer.rx)
                    self.assertFalse(any(x.startswith('OUTP ') for x in peer.rx))
                    self.assertEqual(peer.unsupported, [])

    def test_status_mask_drift_aborts_after_query(self):
        for command in ('*OPC?', '*PSC?'):
            with self.subTest(command=command):
                result, peer, ev = self.stage(command, response_sequences={
                    '*ESE?': ['0', '1']})
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(peer.rx.count(command), 1)
                self.assertNotIn('SYST:LOC', peer.rx)
                self.assertEqual(peer.unsupported, [])

    def test_error_after_new_query_stops_and_never_restores_on_unknown(self):
        for command in ('*OPC?', '*PSC?'):
            with self.subTest(command=command):
                result, peer, ev = self.stage(command, fault_after=command)
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(peer.rx[-2:], [command, 'SYST:ERR?'])
                self.assertNotIn('SYST:LOC', peer.rx)
                self.assertEqual(peer.unsupported, [])

    def test_peer_denies_unknown_scpi_without_fake_success(self):
        peer = UARTPeer()
        self.addCleanup(peer.close)
        wire = PTYTransport(peer.path, timeout=.17)
        self.addCleanup(wire.close)
        self.assertEqual(wire.query('BOGUS?'), 'INVALID VIRTUAL UART QUERY')
        self.assertEqual(peer.unsupported, ['BOGUS?'])
        wire.write('BOGUS 1')
        self.assertIn('Unsupported virtual UART command', wire.query('SYST:ERR?'))
        self.assertIn('BOGUS 1', peer.unsupported)


if __name__ == '__main__':
    unittest.main()
