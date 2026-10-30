"""Batched OFF STEP qualification: production driver over actual PTY UART bytes."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
# Allow the PTY peer thread to run when the full suite is under CPU load.
# This is a response deadline for the OFF-only virtual fixture, not a
# relaxation of readback, physical-gate, or fail-stop assertions.
BATCH_PTY_TIMEOUT_S = 0.75
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_5_step_batch_stage import (BatchStepScope, cases, matrix_token,
                                   run_step_batch)

class Evidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        record = {'event': event, **fields}
        self.events.append(record)
        return record

@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class BatchStepUART(unittest.TestCase):
    def execute(self, matrix='current', *, profile_gate=None, batch_gate=None,
                final_gate=None, **kwargs):
        peer = UARTPeer(profile_registers=True, **kwargs)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=BATCH_PTY_TIMEOUT_S)
        evidence = Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_step_batch(wire, evidence, matrix=matrix,
                profile_gate=profile_gate if profile_gate is not None else lambda t, io: None,
                batch_gate=batch_gate if batch_gate is not None else lambda t, io: None,
                final_gate=final_gate if final_gate is not None else lambda t: None)
        return status, peer, evidence

    def assert_success(self, matrix):
        state, peer, ev = self.execute(matrix)
        plan = cases(matrix)
        self.assertEqual(state, 'PASS', ev.events[-1])
        self.assertEqual(ev.events[-1]['completed_cases'], len(plan))
        self.assertEqual(sum(e['event'] == 'batch_case_result' for e in ev.events), len(plan))
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.assertEqual(peer.rx.count('SYST:LOC'), 1)
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertFalse({'*RST', '*CLS', '*TRG'} & set(peer.rx))
        self.assertFalse(any(x.startswith('VOLT:TRIG') or x.startswith('CURR:TRIG') for x in peer.rx))
        for channel, kind, increment in plan:
            command = 'CURR' if kind == 'current' else 'VOLT'
            # A register is shared by selected channel; the same STEP command
            # occurs once per channel in this deliberately exhaustive matrix.
            self.assertEqual(peer.rx.count(f'{command}:STEP {increment:.3f}'), 3)
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(cmd + '\r\n').encode('ascii') for cmd in peer.rx])
        for case in (x for x in ev.events if x['event'] == 'batch_case_result'):
            initial = case['initial']
            step = case['increment']
            self.assertEqual([round(v['setpoint'], 3) for v in case['steps']],
                             [round(initial+step,3),round(initial+2*step,3),
                              round(initial+step,3),round(initial,3)])
            self.assertEqual([v['direction'] for v in case['steps']],
                             ['up','up','down','down'])
        return peer, ev

    def test_current_nine_case_matrix_all_channels(self):
        self.assert_success('current')

    def test_voltage_nine_case_matrix_all_channels(self):
        self.assert_success('voltage')

    def test_all_eighteen_cases_one_session(self):
        self.assert_success('all')

    def test_one_error_aborts_remaining_matrix(self):
        status, peer, ev = self.execute('current', fault_after='CURR:STEP 0.005')
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(peer.rx.count('CURR:STEP 0.005'), 1)
        self.assertEqual(peer.rx.count('CURR:STEP 0.010'), 0)
        self.assertNotIn('SYST:LOC', peer.rx)
        self.assertIn('OUTP 0', peer.rx)
        self.assertLess(ev.events[-1]['completed_cases'], 9)
        self.assertFalse(any(e['event']=='outcome' and e['outcome']=='PASS' for e in ev.events))

    def test_ignored_step_write_aborts_without_next_action(self):
        state, peer, ev = self.execute('current', ignore_step_write=True)
        self.assertEqual(state, 'ABORTED')
        self.assertEqual(peer.rx.count('CURR:UP'), 2)  # first 1mA case completed
        self.assertEqual(peer.rx.count('CURR:STEP 0.010'), 0)
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_link_uncertain_stops_without_retry_or_cleanup_scpi(self):
        status, peer, ev = self.execute('current', drop='CURR:STEP?')
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(peer.rx.count('CURR:STEP?'), 1)
        self.assertEqual(peer.rx[-1], 'CURR:STEP?')
        self.assertFalse(ev.events[-1]['link_known'])
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_missing_approval_blocks_all_step_writes(self):
        def refuse(token, adapter): raise PermissionError('No batch authorization')
        state, peer, ev = self.execute('current', batch_gate=refuse)
        self.assertEqual(state, 'ABORTED')
        self.assertFalse(any(x.startswith('CURR:STEP ') for x in peer.rx))
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_physical_final_gate_refusal_cannot_pass(self):
        def refuse(token): raise PermissionError('No final physical confirmation')
        state, peer, ev = self.execute('current', final_gate=refuse)
        self.assertEqual(state, 'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        # The final operator denial is injected only after verified digital OFF.
        # Keep the assertion strict; include the stage error on sporadic failures.
        self.assertEqual(ev.events[-1]['completed_cases'], len(cases('current')),
                         ev.events[-1])
        self.assertTrue(ev.events[-1]['digital_off_confirmed'], ev.events[-1])

    def test_scope_never_accepts_power_or_trigger(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=BATCH_PTY_TIMEOUT_S)
        self.addCleanup(wire.close)
        scope = BatchStepScope(wire, Evidence(), matrix='current')
        scope.profile_authorized = True
        scope.begin_case(1,'current',.001)
        for cmd in ('OUTP 1', 'CHAN:OUTP 1', 'VOLT:STEP 0.010',
                    'CURR:TRIG', 'VOLT:UP', '*RST', '*TRG'):
            with self.subTest(command=cmd), self.assertRaises((SafetyError, ValueError)):
                scope.write(cmd)
        self.assertEqual(peer.rx, [])

    def test_case_transition_requires_completion_and_order(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=BATCH_PTY_TIMEOUT_S)
        self.addCleanup(wire.close)
        scope = BatchStepScope(wire, Evidence(), matrix='current')
        with self.assertRaises(SafetyError): scope.begin_case(1, 'current', .005)
        scope.begin_case(1, 'current', .001)
        with self.assertRaises(SafetyError): scope.begin_case(2, 'current', .001)
        with self.assertRaises(SafetyError): scope.end_case()
        self.assertEqual(peer.rx, [])

    def test_matrix_validation_and_dry_run_no_io(self):
        self.assertEqual(len(cases('current')), 9)
        self.assertEqual(len(cases('voltage')), 9)
        self.assertEqual(len(cases('all')), 18)
        with self.assertRaises(ValueError): cases('not-a-matrix')
        self.assertIn('CASES=9', matrix_token('current'))
        env = dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        process = subprocess.run([sys.executable, str(ROOT/'tools/r4_5_step_batch_qualify.py'),
                                  '--matrix', 'current'], capture_output=True, text=True,
                                  cwd=ROOT, env=env, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('DRY RUN: no serial enumeration', process.stdout)
        self.assertIn('9 CASES', process.stdout)
        # A non-interactive --execute cannot energize/open hardware.
        process = subprocess.run([sys.executable, str(ROOT/'tools/r4_5_step_batch_qualify.py'),
                                  '--matrix', 'current', '--execute'], capture_output=True,
                                  text=True, cwd=ROOT, env=env, timeout=15)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('Example bench is not authorized', process.stderr)

if __name__ == '__main__':
    unittest.main()
