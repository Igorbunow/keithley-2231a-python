"""Independent offline audit mutation tests on actual virtual UART evidence."""
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

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/hardware'))
from qualification import Evidence
from r4_5_step_mutation_stage import run_step_mutation
from tools.audit_r4_5_step_mutation_evidence import audit, audit_file, AuditError


@unittest.skipUnless(os.name=='posix', 'PTY needed')
class R45DRealUARTAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.path=Path(cls.tmp.name)/'events.jsonl'
        cls.peer=UARTPeer(profile_registers=True)
        wire=open_test_transport(cls.peer,timeout=.20)
        ev=Evidence(cls.path,'R45STEPMUT')
        ev.add('start', variant='STEP_MUTATION_OFF',channel=1,kind='voltage',
               increment=.010,config_sha256='a'*64,
               software={'sha':'69ec1ac'+'0'*33,'dirty':False,
                         'r45d_runner_sha256':'1'*64,
                         'r45d_stage_sha256':'2'*64,
                         'source_sha256':'3'*64})
        ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
               approved_before_evidence_open=True,config_sha256='a'*64)
        def final_gate(_):
            ev.add('operator_confirmation',gate='FINAL_PHYSICAL')
        with contextlib.redirect_stdout(io.StringIO()):
            cls.result=run_step_mutation(wire,ev,channel=1,kind='voltage',
                increment=.010,profile_gate=lambda *args:None,
                mutation_gate=lambda *args:None,final_gate=final_gate)
        cls.peer.await_command('SYST:LOC')
        cls.records=[json.loads(l) for l in cls.path.read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.peer.close()
        cls.tmp.cleanup()

    def reject(self, change):
        records=deepcopy(self.records)
        change(records)
        with self.assertRaises(AuditError):audit(records)

    def test_virtual_uart_pass_and_raw_sha(self):
        self.assertEqual(self.result,'PASS')
        self.assertEqual(audit(self.records)['status'],'REVIEWABLE_PASS')
        self.assertEqual(audit_file(self.path)['status'],'REVIEWABLE_PASS')

    def test_forbidden_output_enable_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='write'
                                  and x['command']=='OUTP 0').update(command='OUTP 1'))

    def test_hidden_output_on_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='driver_trace'
                                  and x['trace']['event']=='reply' and
                                  x['trace']['command']=='CHAN:OUTP?')['trace'].update(response='1'))

    def test_missing_mutation_gate_rejected(self):
        self.reject(lambda r:r.__setitem__(slice(None),[x for x in r if not
                    (x['event']=='operator_confirmation' and x['gate']=='MUTATION')]))

    def test_reordered_mutation_gate_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='operator_confirmation'
                                  and x['gate']=='MUTATION').update(monotonic_s=9999999))

    def test_wrong_second_up_response_rejected(self):
        def alter(rows):
            seq=[x for x in rows if x['event']=='driver_trace' and
                 x['trace']['event']=='reply' and x['trace']['command']=='VOLT?'
                 and abs(float(x['trace'].get('response','0'))-3.32)<.00001]
            self.assertTrue(seq)
            seq[0]['trace']['response']='3.319'
        self.reject(alter)

    def test_out_of_order_direction_rejected(self):
        def alter(rows):
            writes=[x for x in rows if x['event']=='driver_trace' and
                    x['trace']['event']=='write' and x['trace']['command']=='VOLT:DOWN']
            self.assertEqual(len(writes),2)
            writes[0]['trace']['command']='VOLT:UP'
        self.reject(alter)

    def test_missing_error_poll_rejected(self):
        self.reject(lambda r:r.remove(next(x for x in r if x['event']=='driver_trace'
                         and x['trace']['event']=='query' and
                         x['trace']['command']=='SYST:ERR?')))

    def test_changed_setpoint_readback_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='read' and
                                    x['command']=='VOLT?' and
                                    abs(float(x['response'])-3.31)<.00001).update(response='3.30'))

    def test_nonzero_error_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='read' and
                          x['command']=='SYST:ERR?').update(response='100,Error'))

    def test_results_tampering_rejected(self):
        self.reject(lambda r:next(x for x in r if x['event']=='results')['values']
                          .update(final_setpoint=3.1))

    def test_missing_final_gate_rejected(self):
        self.reject(lambda r:r.__setitem__(slice(None),[x for x in r if not
                         (x['event']=='operator_confirmation' and x['gate']=='FINAL_PHYSICAL')]))

    def test_duplicate_json_key_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            q=Path(t)/'dup.jsonl'
            raw=self.path.read_bytes()
            q.write_bytes(raw.replace(b'"event": "start"',
                b'"event": "start", "event": "start"',1))
            with self.assertRaises(AuditError):audit_file(q)

    def test_hash_mismatch_rejected(self):
        with self.assertRaises(AuditError):audit_file(self.path,'0'*64)

if __name__=='__main__':unittest.main()
