"""Mutation tests against genuine UART-generated R4.5-B INST evidence."""
from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from qualification import Evidence
from r4_5_source_stage import run_source
from tools.audit_r4_5_source_evidence import audit, audit_file, AuditError


@unittest.skipUnless(os.name == 'posix','requires PTY')
class SourceAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'events.jsonl'
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.15)
        ev = Evidence(self.path, 'R45SOURCE')
        ev.add('start',variant='INST_OFF_QUERY_ONLY',
               config_sha256='f'*64, software={'sha':'a1af3d5' + '0'*33,'dirty':True})
        ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
               approved_before_evidence_open=True,config_sha256='f'*64)
        def confirm(_):
            ev.add('operator_confirmation',gate='FINAL')
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_source(wire,ev,steps=False,final_gate=confirm)
        self.assertEqual(status,'PASS')
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.rows = [json.loads(x) for x in self.path.read_text().splitlines()]

    def test_real_stage_fixture_passes(self):
        self.assertEqual(audit(self.rows)['status'],'REVIEWABLE_PASS')
        self.assertEqual(audit_file(self.path)['status'],'REVIEWABLE_PASS')

    def reject(self,mutate):
        data=deepcopy(self.rows)
        mutate(data)
        with self.assertRaises(AuditError):
            audit(data)

    def test_trace_forgery_rejected(self):
        self.reject(lambda d: next(r for r in d if r['event']=='driver_trace' and
                      r['trace']['event']=='reply' and r['trace']['command']=='INST?')['trace']
                      .update(response='CH2'))

    def test_output_on_rejected(self):
        self.reject(lambda d: next(r for r in d if r['event']=='driver_trace' and
                      r['trace']['event']=='reply' and r['trace']['command']=='CHAN:OUTP?')['trace']
                      .update(response='1'))

    def test_nonzero_scpi_error_rejected(self):
        self.reject(lambda d: next(r for r in d if r['event']=='read' and
                      r['command']=='SYST:ERR?').update(response='100,Error'))

    def test_missing_final_gate_rejected(self):
        self.reject(lambda d: d.__setitem__(slice(None), [r for r in d if
                      r['event']!='operator_confirmation']))

    def test_result_tampering_rejected(self):
        self.reject(lambda d: next(r for r in d if r['event']=='results')['values']
                      .update(step_phase='OBSERVED_OFF_ONLY'))

    def test_unreviewed_command_rejected(self):
        self.reject(lambda d: next(r for r in d if r['event']=='write').update(command='OUTP 1'))

    def test_missing_error_poll_rejected(self):
        self.reject(lambda d: d.remove(next(r for r in d if r['event']=='read'
                                            and r['command']=='SYST:ERR?')))

    def test_hash_mismatch_rejected(self):
        with self.assertRaises(AuditError):
            audit_file(self.path,'0'*64)



@unittest.skipUnless(os.name == 'posix','requires PTY')
class SourceStepAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'events.jsonl'
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.18)
        ev=Evidence(self.path,'R45SOURCE')
        ev.add('start',variant='STEP_OFF_PROFILE',config_sha256='f'*64,
               software={'sha':'a29dc25'+'0'*33,'dirty':True})
        ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
               approved_before_evidence_open=True,config_sha256='f'*64)
        def profile_gate(token,adapter):
            self.assertTrue(token.startswith('CONFIRM R45SOURCE APPLY_OFF_PROFILE'))
        def final_gate(token):
            ev.add('operator_confirmation',gate='FINAL')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_source(wire,ev,steps=True,
                profile_gate=profile_gate,final_gate=final_gate),'PASS')
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.rows=[json.loads(line) for line in self.path.read_text().splitlines()]

    def reject(self, mutate):
        rows=deepcopy(self.rows)
        mutate(rows)
        with self.assertRaises(AuditError):
            audit(rows)

    def test_generated_step_phase_passes_and_is_not_fixed_to_real_value(self):
        r=audit(self.rows)
        self.assertEqual(r['status'],'REVIEWABLE_PASS')
        self.assertEqual(r['phase'],'STEP_OFF_PROFILE')
        self.assertEqual(r['step_voltage_v'],.01)
        self.assertEqual(r['step_current_a'],.001)
        self.assertEqual(r['output_off_checks'],33)
        self.assertEqual(audit_file(self.path)['status'],'REVIEWABLE_PASS')

    def test_forbidden_output_on_rejected(self):
        self.reject(lambda x: next(r for r in x if r['event']=='write' and
            r['command']=='OUTP 0').update(command='OUTP 1'))

    def test_nonzero_error_rejected(self):
        self.reject(lambda x: next(r for r in x if r['event']=='driver_trace'
            and r['trace']['event']=='reply' and
            r['trace']['command']=='SYST:ERR?')['trace'].update(response='100,Error'))

    def test_step_reply_changed_rejected(self):
        self.reject(lambda x: next(r for r in x if r['event']=='driver_trace'
            and r['trace']['event']=='reply' and
            r['trace']['command']=='VOLT:STEP?')['trace'].update(response='0.5'))

    def test_step_nan_rejected(self):
        self.reject(lambda x: next(r for r in x if r['event']=='driver_trace'
            and r['trace']['event']=='reply' and
            r['trace']['command']=='CURR:STEP?')['trace'].update(response='nan'))

    def test_missing_profile_gate_rejected(self):
        self.reject(lambda x: x.__setitem__(slice(None),[r for r in x if
            r['event']!='operator_confirmation' or r.get('gate')!='PROFILE']))

    def test_moved_profile_gate_after_preset_write_rejected(self):
        def mutate(rows):
            gate=next(r for r in rows if r['event']=='operator_confirmation'
                      and r.get('gate')=='PROFILE')
            gate['monotonic_s']+=5
        self.reject(mutate)

    def test_missing_shutdown_sweep_rejected(self):
        self.reject(lambda x: next(r for r in reversed(x) if
            r['event']=='driver_trace' and r['trace']['event']=='reply' and
            r['trace']['command']=='CHAN:OUTP?')['trace'].update(response='1'))

    def test_wrong_setpoint_rejected(self):
        self.reject(lambda x: next(r for r in x if r['event']=='driver_trace'
            and r['trace']['event']=='reply' and
            r['trace']['command']=='VOLT?')['trace'].update(response='17.0'))

    def test_missing_post_write_error_poll_rejected(self):
        self.reject(lambda x: x.remove(next(r for r in x if
            r['event']=='driver_trace' and r['trace']['event']=='query' and
            r['trace']['command']=='SYST:ERR?')))

    def test_results_tamper_rejected(self):
        self.reject(lambda x: next(r for r in x if
            r['event']=='results')['values'].update(step_current_a=.03))

    def test_missing_final_gate_rejected(self):
        self.reject(lambda x: x.__setitem__(slice(None),[r for r in x if
            r['event']!='operator_confirmation' or r.get('gate')!='FINAL']))

    def test_evidence_digest_rejected(self):
        with self.assertRaises(AuditError):
            audit_file(self.path, expected_sha256='0'*64)

if __name__=='__main__': unittest.main()
