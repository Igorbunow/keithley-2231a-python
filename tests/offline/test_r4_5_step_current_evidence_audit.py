"""Current STEP OFF-only audit: real production API through a PTY, not hardware."""
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
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from qualification import Evidence
from r4_5_step_mutation_stage import run_step_mutation
from tools.audit_r4_5_step_mutation_evidence import audit, audit_file, AuditError


@unittest.skipUnless(os.name == 'posix', 'PTY required')
class R45DCurrentStepEvidenceUART(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / 'current.jsonl'
        cls.peer = UARTPeer(profile_registers=True)
        transport = open_test_transport(cls.peer, timeout=.20)
        evidence = Evidence(cls.path, 'R45STEPMUT')
        evidence.add('start', variant='STEP_MUTATION_OFF', channel=1,
                     kind='current', increment=.001, config_sha256='a'*64,
                     software={'sha': '9920aab' + '0'*33, 'dirty': False,
                               'source_sha256': '1'*64,
                               'r45d_runner_sha256': '2'*64,
                               'r45d_stage_sha256': '3'*64})
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'],
                     approved_before_evidence_open=True,
                     config_sha256='a'*64)

        def final_gate(_):
            evidence.add('operator_confirmation', gate='FINAL_PHYSICAL')

        with contextlib.redirect_stdout(io.StringIO()):
            cls.result = run_step_mutation(
                transport, evidence, channel=1, kind='current', increment=.001,
                profile_gate=lambda *args: None,
                mutation_gate=lambda *args: None,
                final_gate=final_gate)
        cls.peer.await_command('SYST:LOC')
        cls.records = [json.loads(line) for line in cls.path.read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.peer.close()
        cls.temp.cleanup()

    def reject(self, update):
        rows = deepcopy(self.records)
        update(rows)
        with self.assertRaises(AuditError):
            audit(rows)

    def test_current_1ma_audit_success(self):
        self.assertEqual(self.result, 'PASS')
        result = audit(self.records)
        self.assertEqual((result['kind'], result['step']), ('current', .001))
        self.assertEqual(result['setpoints'], [.101, .102, .101, .100])
        self.assertEqual(audit_file(self.path)['status'], 'REVIEWABLE_PASS')

    def test_current_wrong_increment_rejected(self):
        self.reject(lambda rows: next(x for x in rows if x['event']=='read' and
                                      x['command']=='CURR:STEP?').update(response='.005'))

    def test_current_1ma_setpoint_drift_rejected(self):
        self.reject(lambda rows: next(x for x in rows if x['event']=='read' and
                                      x['command']=='CURR?' and
                                      x['response']=='0.101').update(response='.103'))

    def test_current_second_up_action_rejected(self):
        def update(rows):
            actions = [x for x in rows if x['event']=='driver_trace' and
                       x['trace']['event']=='write' and
                       x['trace']['command']=='CURR:UP']
            self.assertEqual(len(actions), 2)
            actions[1]['trace']['command']='CURR:DOWN'
        self.reject(update)

    def test_current_missing_middle_off_rejected(self):
        self.reject(lambda rows: next(x for x in rows if x['event']=='driver_trace' and
                                      x['trace']['event']=='reply' and
                                      x['trace']['command']=='CHAN:OUTP?')['trace'].update(response='1'))

    def test_voltage_one_millivolt_not_approved_for_audit(self):
        # Ensure the auditor can't promote the historically rejected 1mV voltage
        # write by changing the metadata of an otherwise successful current run.
        def update(rows):
            rows[0]['kind']='voltage'
        self.reject(update)

    def test_missing_current_error_poll_rejected(self):
        self.reject(lambda rows: rows.remove(next(x for x in rows if
                 x['event']=='driver_trace' and x['trace']['event']=='query'
                 and x['trace']['command']=='SYST:ERR?')))

    def test_current_final_physical_confirmation_required(self):
        self.reject(lambda rows: rows.__setitem__(slice(None), [x for x in rows if not
                (x['event']=='operator_confirmation' and x['gate']=='FINAL_PHYSICAL')]))

if __name__ == '__main__':
    unittest.main()
