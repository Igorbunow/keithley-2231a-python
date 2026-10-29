"""R4.4-B hardware policy verified through the real driver and virtual UART."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_4_display_stage import R44DisplayScope, run_display


class Evidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        record = {'event': event, **fields}
        self.events.append(record)
        return record


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudoterminal')
class DisplayHardwareUartTests(unittest.TestCase):
    def stage(self, *, final_gate=lambda token: None, **peer_kwargs):
        peer = UARTPeer(**peer_kwargs)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.14)
        ev = Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_display(wire, ev, final_gate=final_gate)
        return outcome, peer, ev

    def test_success_exact_query_once_and_clean_wire(self):
        outcome, peer, evidence = self.stage()
        self.assertEqual(outcome, 'PASS')
        self.assertTrue(peer.await_received(67), 'missing final SYST:LOC on virtual UART')
        self.assertEqual(len(peer.rx), 67)
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx.count('DISP?'), 1)
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertEqual(peer.rx_bytes,
                         [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])
        self.assertFalse(any(c.startswith(('OUTP ', 'VOLT ', 'CURR ', 'MEAS:',
                                           'FETC:', 'DISP ', '*CLS', '*RST'))
                             for c in peer.rx))
        results = [e['values'] for e in evidence.events if e['event'] == 'results']
        self.assertEqual(results, [{'display_enabled': True, 'display_query_count': 1}])
        self.assertTrue(evidence.events[-1]['physical_off_confirmed'])

    def test_display_off_is_valid_observation(self):
        outcome, peer, evidence = self.stage(response_overrides={'DISP?': 'OFF'})
        self.assertEqual(outcome, 'PASS')
        self.assertFalse(next(e['values']['display_enabled'] for e in evidence.events
                              if e['event'] == 'results'))

    def test_no_display_query_when_output_on(self):
        for ch in (1, 2, 3):
            with self.subTest(ch=ch):
                outcome, peer, evidence = self.stage(off_by_channel={ch: 1})
                self.assertEqual(outcome, 'ABORTED')
                self.assertNotIn('DISP?', peer.rx)
                self.assertFalse(evidence.events[-1]['physical_off_confirmed'])

    def test_invalid_response_blocks_error_poll_and_all_followup(self):
        for raw in ('2', '-1', '1.0', 'garbage', '0,0'):
            with self.subTest(raw=raw):
                outcome, peer, evidence = self.stage(response_overrides={'DISP?': raw})
                self.assertEqual(outcome, 'ABORTED')
                self.assertEqual(peer.rx[-1], 'DISP?')
                self.assertFalse(evidence.events[-1]['link_known'])
                self.assertNotIn('SYST:LOC', peer.rx)

    def test_timeout_blocks_retry_and_shutdown(self):
        outcome, peer, evidence = self.stage(drop='DISP?')
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(peer.rx[-1], 'DISP?')
        self.assertEqual(peer.rx.count('DISP?'), 1)
        self.assertFalse(evidence.events[-1]['link_known'])
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_scpi_error_after_valid_query_is_abort(self):
        outcome, peer, evidence = self.stage(fault_after='DISP?')
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(peer.rx[-2:], ['DISP?', 'SYST:ERR?'])
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_final_operator_refusal_is_abort(self):
        def decline(_): raise EOFError('operator refused')
        outcome, peer, evidence = self.stage(final_gate=decline)
        self.assertEqual(outcome, 'ABORTED')
        self.assertFalse(evidence.events[-1]['physical_off_confirmed'])

    def test_scope_never_sends_unapproved_commands(self):
        peer = UARTPeer()
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        scope = R44DisplayScope(wire, Evidence())
        for command in ('*PSC?', '*ESR?', 'MEAS:POW?', 'FETC:VOLT?', 'VOLT?',
                        'STAT:OPER:EVEN?'):
            with self.subTest(query=command), self.assertRaises((SafetyError, ValueError)):
                scope.query(command)
        for command in ('OUTP 0', 'OUTP 1', 'DISP 0', 'VOLT 0', '*RST', '*CLS'):
            with self.subTest(write=command), self.assertRaises((SafetyError, ValueError)):
                scope.write(command)
        self.assertEqual(peer.rx, [])

    def test_cli_dry_run_and_provenance(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src') + ':' + str(ROOT / 'tests/hardware'))
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tools/r4_4_display_qualify.py'),
             '--config', str(ROOT / 'config/bench.example.toml')],
            env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DRY RUN:', result.stdout)
        from tools.r4_4_display_qualify import provenance
        info = provenance()
        self.assertEqual(info['r44_display_stage_sha256'],
                         hashlib.sha256((ROOT / 'tests/hardware/r4_4_display_stage.py').read_bytes()).hexdigest())
        self.assertEqual(info['r44_display_runner_sha256'],
                         hashlib.sha256((ROOT / 'tools/r4_4_display_qualify.py').read_bytes()).hexdigest())

    def test_example_config_rejected_before_hardware_even_with_execute(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src') + ':' + str(ROOT / 'tests/hardware'))
        result = subprocess.run(
            [sys.executable, str(ROOT / 'tools/r4_4_display_qualify.py'), '--execute',
             '--config', str(ROOT / 'config/bench.example.toml')],
            env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Example bench cannot be used', result.stderr)


if __name__ == '__main__':
    unittest.main()
