"""Safety contract for R4.5-B OFF-only INST?/STEP hardware qualification."""
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
from r4_5_source_stage import (R45SourceScope, run_source, INITIAL_TOKEN,
                               PROFILE_TOKEN, FINAL_TOKEN)


class Evidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        item = {'event': event, **fields}
        self.events.append(item)
        return item


@unittest.skipUnless(os.name == 'posix', 'requires virtual UART PTY')
class SourceStageUART(unittest.TestCase):
    def execute(self, *, steps=False, drop=None, overrides=None, off=None,
                gate=None, final_gate=lambda token: None, fault_after=None,
                evidence=None):
        peer = UARTPeer(profile_registers=True, drop=drop,
                       response_overrides=overrides or {},
                       off_by_channel=off, fault_after=fault_after)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.13)
        ev = evidence or Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_source(wire, ev, steps=steps,
                                profile_gate=gate or (lambda token, adapter: None),
                                final_gate=final_gate)
        return status, peer, ev

    def test_inst_only_does_not_modify_presets_or_outputs(self):
        result, peer, ev = self.execute()
        self.assertEqual(result, 'PASS', ev.events[-1])
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.assertEqual(peer.rx.count('INST?'), 1)
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertEqual(peer.unsupported, [])
        self.assertFalse(any(x in peer.rx for x in (
            'OUTP 0', 'OUTP 1', 'CHAN:OUTP 1', 'VOLT 0',
            'VOLT:STEP?', 'CURR:STEP?', '*RST', '*CLS')))
        vals = next(e['values'] for e in ev.events if e['event'] == 'results')
        self.assertEqual(vals['selected_channel'], 'CH1')
        self.assertEqual(vals['step_phase'], 'NOT_RUN')
        self.assertTrue(ev.events[-1]['physical_off_confirmed'])

    def test_inst_reports_initial_selected_channel(self):
        # Default channel is one; the first mode requires no channel changes
        # except those made during output-safety snapshots.
        result, peer, ev = self.execute(overrides={'INST?': 'CH1'})
        self.assertEqual(result, 'PASS', ev.events[-1])

    def test_inst_name_mismatch_aborts_before_any_step_or_profile_write(self):
        status, peer, ev = self.execute(steps=True, overrides={'INST?': 'CH3'})
        self.assertEqual(status, 'ABORTED')
        self.assertNotIn('VOLT:STEP?', peer.rx)
        self.assertNotIn('VOLT 0', peer.rx)
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_malformed_inst_answer_blocks_error_poll_and_followup(self):
        for raw in ('1', 'CH4', '', 'CH1,CH2'):
            with self.subTest(raw=raw):
                result, peer, ev = self.execute(overrides={'INST?': raw})
                self.assertEqual(result, 'ABORTED')
                self.assertEqual(peer.rx[-1], 'INST?')
                self.assertFalse(ev.events[-1]['link_known'])
                self.assertNotIn('SYST:LOC', peer.rx)

    def test_step_profile_is_gated_and_never_energizes(self):
        called=[]
        status, peer, ev = self.execute(steps=True, gate=lambda token, adapter: called.append(token))
        self.assertEqual(status, 'PASS', ev.events[-1])
        self.assertEqual(called, [PROFILE_TOKEN])
        self.assertEqual(peer.rx.count('INST?'), 1)
        self.assertEqual(peer.rx.count('VOLT:STEP?'), 1)
        self.assertEqual(peer.rx.count('CURR:STEP?'), 1)
        self.assertLess(peer.rx.index('VOLT:STEP?'), peer.rx.index('CURR:STEP?'))
        self.assertIn('OUTP 0', peer.rx)
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertFalse(any(c in peer.rx for c in (
            'OUTP 1', 'CHAN:OUTP 1', 'VOLT:STEP 0.1', 'CURR:STEP 0.1',
            'VOLT:UP', 'VOLT:DOWN', '*RST', '*CLS', '*TRG', '*RCL 0')))
        self.assertEqual(peer.unsupported, [])
        result = next(e['values'] for e in ev.events if e['event']=='results')
        self.assertEqual(result['step_phase'], 'OBSERVED_OFF_ONLY')
        self.assertAlmostEqual(result['step_voltage_v'], .01)
        self.assertAlmostEqual(result['step_current_a'], .001)

    def test_profile_gate_refusal_never_configures(self):
        def reject(_, adapter): raise EOFError('not approved')
        status, peer, ev = self.execute(steps=True, gate=reject)
        self.assertEqual(status, 'ABORTED')
        self.assertNotIn('OUTP 0', peer.rx)
        self.assertFalse(any(c.startswith(('VOLT ', 'CURR ')) for c in peer.rx))
        self.assertNotIn('VOLT:STEP?', peer.rx)

    def test_output_on_at_preflight_blocks_source_queries(self):
        for channel in (1,2,3):
            with self.subTest(channel=channel):
                status, peer, ev = self.execute(off={channel: 1})
                self.assertEqual(status, 'ABORTED')
                self.assertNotIn('INST?', peer.rx)
                self.assertNotIn('OUTP 0', peer.rx)

    def test_missing_step_response_aborts_no_retry_or_loc(self):
        status, peer, ev = self.execute(steps=True, drop='VOLT:STEP?')
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:STEP?'), 1)
        self.assertEqual(peer.rx[-1], 'VOLT:STEP?')
        self.assertNotIn('CURR:STEP?', peer.rx)
        self.assertFalse(ev.events[-1]['link_known'])
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_malformed_step_stops_without_followup_scpi(self):
        for bad in ('None', 'Infinity', 'nan', '1;2'):
            with self.subTest(bad=bad):
                status, peer, ev = self.execute(
                    steps=True, overrides={'VOLT:STEP?': bad})
                self.assertEqual(status, 'ABORTED')
                self.assertEqual(peer.rx[-1], 'VOLT:STEP?')
                self.assertNotIn('CURR:STEP?', peer.rx)
                self.assertFalse(ev.events[-1]['link_known'])

    def test_second_step_scpi_error_preserves_verified_off_and_abort(self):
        status, peer, ev = self.execute(steps=True, fault_after='CURR:STEP?')
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(peer.rx.count('CURR:STEP?'), 1)
        self.assertIn('OUTP 0', peer.rx)
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_final_gate_rejection_prevents_pass(self):
        def reject(_): raise EOFError('OFF not confirmed')
        status, peer, ev = self.execute(steps=True, final_gate=reject)
        self.assertEqual(status, 'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertNotIn('CHAN:OUTP 1', peer.rx)

    def test_evidence_loss_still_runs_verified_off(self):
        class FailEvidence(Evidence):
            def add(self, event, **fields):
                if event == 'results': raise OSError('disk full')
                return super().add(event, **fields)
        status, peer, ev = self.execute(steps=True, evidence=FailEvidence())
        self.assertEqual(status, 'ABORTED')
        self.assertIn('OUTP 0', peer.rx)
        self.assertFalse(any(e['event']=='outcome' and e['outcome']=='PASS' for e in ev.events))

    def test_scope_blocks_any_unreviewed_command_without_wire(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        scope=R45SourceScope(wire, Evidence())
        for cmd in ('MEAS:POW?', 'FETC:VOLT?', '*OPC?', '*ESR?', 'OUTP:STAT:ALL?'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError): scope.query(cmd)
        for cmd in ('OUTP 1', 'CHAN:OUTP 1', 'VOLT:STEP 0.01',
                    'CURR:STEP 0.001', '*TRG', 'OUTP:PARALLEL 1', 'DISP 0'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError): scope.write(cmd)
        with self.assertRaises(SafetyError): scope.write('VOLT 0')
        self.assertEqual(peer.rx, [])

    def test_cli_dry_run_and_provenance(self):
        env=dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        cmd=[sys.executable, str(ROOT/'tools/r4_5_source_qualify.py'),
             '--config', str(ROOT/'config/bench.example.toml')]
        p=subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('DRY RUN:', p.stdout)
        self.assertIn('NOT REQUESTED', p.stdout)
        from tools.r4_5_source_qualify import provenance
        meta=provenance()
        for key, path in [('r45_source_runner_sha256', ROOT/'tools/r4_5_source_qualify.py'),
                          ('r45_source_stage_sha256', ROOT/'tests/hardware/r4_5_source_stage.py')]:
            self.assertEqual(meta[key], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_cli_rejects_steps_without_execute(self):
        env=dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        p=subprocess.run([sys.executable, str(ROOT/'tools/r4_5_source_qualify.py'),
                          '--steps', '--config', str(ROOT/'config/bench.example.toml')],
                         env=env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('require --execute', p.stderr)


if __name__ == '__main__': unittest.main()
