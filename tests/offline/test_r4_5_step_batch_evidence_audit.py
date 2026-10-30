"""Negative tests for independent, full-wire batch evidence review."""
from __future__ import annotations

import contextlib
import copy
import io
from pathlib import Path
import sys
import unittest

from support.virtual_uart import UARTPeer, open_test_transport
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests/hardware'))
from r4_5_step_batch_stage import run_step_batch
from tools.audit_r4_5_step_batch_evidence import AuditError, audit

class Evidence:
    def __init__(self): self.events=[]
    def add(self,event,**fields):
        r={'event':event,**fields}
        self.events.append(r)
        return r

class BatchEvidenceAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        peer=UARTPeer(profile_registers=True)
        try:
            wire=open_test_transport(peer,timeout=.75)
            cls.evidence=Evidence()
            cls.evidence.add('start',phase='STEP_BATCH_OFF',matrix='current',
                planned_cases=9,config_sha256='a'*64,
                software={'r45_batch_runner_sha256':'b'*64,'r45_batch_stage_sha256':'c'*64})
            cls.evidence.add('pre_open_approvals',gates=['BENCH','INITIAL'],
                approved_before_evidence_open=True,config_sha256='a'*64)
            with contextlib.redirect_stdout(io.StringIO()):
                result=run_step_batch(wire,cls.evidence,matrix='current',
                    profile_gate=lambda t,a:None,batch_gate=lambda t,a:None,
                    final_gate=lambda t:cls.evidence.add('operator_confirmation',gate='FINAL_PHYSICAL'))
            if result!='PASS': raise AssertionError('fixture did not pass')
            cls.records=cls.evidence.events
        finally:
            peer.close()

    def test_full_original_simulation_reviewable(self):
        result=audit(self.records)
        self.assertEqual(result['status'],'REVIEWABLE_PASS')
        self.assertEqual(result['cases'],9)

    def test_known_aux_change_is_metadata_not_fault(self):
        records=copy.deepcopy(self.records)
        for r in records:
            if r['event']=='read' and r.get('command')=='*IDN?':
                r['response']=r['response'].replace('1.04-1.04','1.04-0.01')
            if r['event']=='driver_trace' and r['trace'].get('event')=='reply' and r['trace'].get('command')=='*IDN?':
                r['trace']['response']=r['trace']['response'].replace('1.04-1.04','1.04-0.01')
            if r['event']=='psu_identity': r['firmware']='1.04-0.01'
        self.assertEqual(audit(records)['firmware_observations'],['1.04-0.01'])

    def test_recorded_aux_drift_is_nonblocking_metadata(self):
        records=copy.deepcopy(self.records)
        idn_reads=[r for r in records if r['event']=='read' and r.get('command')=='*IDN?']
        self.assertEqual(len(idn_reads),2)
        idn_reads[1]['response']=idn_reads[1]['response'].replace('1.04-1.04','1.04-0.01')
        idn_trace=[r['trace'] for r in records if r['event']=='driver_trace'
                   and r['trace'].get('event')=='reply' and r['trace'].get('command')=='*IDN?']
        idn_trace[1]['response']=idn_reads[1]['response']
        records.append({'event':'firmware_revision_drift','before':'1.04-1.04',
                        'after':'1.04-0.01','classification':'OBSERVED_ONLY'})
        result=audit(records)
        self.assertEqual(result['firmware_observations'],['1.04-0.01','1.04-1.04'])

    def test_selected_channel_mismatch_is_rejected(self):
        records=copy.deepcopy(self.records)
        # Mutate physical trace only; stage transcript still claims original.
        trace=next(r['trace'] for r in records if r['event']=='driver_trace'
                   and r['trace'].get('event')=='write' and r['trace'].get('command')=='CURR:STEP 0.005')
        trace['command']='CURR:STEP 0.010'
        with self.assertRaises(AuditError): audit(records)

    def test_missing_readback_is_rejected(self):
        records=copy.deepcopy(self.records)
        target=next(r for r in records if r['event']=='read' and r.get('command')=='CURR?'
                    and r.get('response')=='0.105')
        target['response']='0.123'
        with self.assertRaises(AuditError): audit(records)

    def test_nonzero_scpi_error_is_rejected(self):
        records=copy.deepcopy(self.records)
        target=next(r for r in records if r['event']=='read' and r.get('command')=='SYST:ERR?')
        target['response']='120,"Parameter overflowed"'
        with self.assertRaises(AuditError): audit(records)

    def test_one_output_on_is_rejected(self):
        records=copy.deepcopy(self.records)
        target=next(r for r in records if r['event']=='read' and r.get('command')=='CHAN:OUTP?')
        target['response']='1'
        with self.assertRaises(AuditError): audit(records)

    def test_missing_case_is_rejected(self):
        records=[r for r in self.records if not (r['event']=='batch_case_result' and r['index']==4)]
        with self.assertRaises(AuditError): audit(records)

    def test_missing_physical_gate_is_rejected(self):
        records=[r for r in self.records if not (r['event']=='operator_confirmation'
                                                  and r.get('gate')=='FINAL_PHYSICAL')]
        with self.assertRaises(AuditError): audit(records)

    def test_missing_low_level_trace_reply_is_rejected(self):
        records=copy.deepcopy(self.records)
        idx=next(i for i,r in enumerate(records) if r['event']=='driver_trace'
                 and r['trace'].get('event')=='reply' and r['trace'].get('command')=='CURR?')
        records.pop(idx)
        with self.assertRaises(AuditError): audit(records)

    def test_wire_error_response_payload_disagreement_is_rejected(self):
        records = copy.deepcopy(self.records)
        item = next(r['trace'] for r in records if r['event'] == 'driver_trace'
                    and r['trace'].get('event') == 'reply'
                    and r['trace'].get('command') == 'SYST:ERR?')
        item['response'] = '120,"Parameter overflowed"'
        with self.assertRaises(AuditError): audit(records)

    def test_wire_off_response_payload_disagreement_is_rejected(self):
        records = copy.deepcopy(self.records)
        item = next(r['trace'] for r in records if r['event'] == 'driver_trace'
                    and r['trace'].get('event') == 'reply'
                    and r['trace'].get('command') == 'CHAN:OUTP?')
        item['response'] = '1'
        with self.assertRaises(AuditError): audit(records)

    def test_nonmonotonic_timestamps_are_rejected(self):
        records = copy.deepcopy(self.records)
        for i, rec in enumerate(records):
            rec['monotonic_s'] = float(i)
        records[40]['monotonic_s'] = -1
        with self.assertRaises(AuditError): audit(records)

    def test_mixed_source_test_id_is_rejected(self):
        records = copy.deepcopy(self.records)
        for rec in records:
            rec['test_id'] = 'R45STEPBATCH'
        records[40]['test_id'] = 'OTHERTEST'
        with self.assertRaises(AuditError): audit(records)

    def test_wrong_batch_digest_is_rejected(self):
        records=copy.deepcopy(self.records)
        next(r for r in records if r['event']=='batch_results')['results_sha256']='0'*64
        with self.assertRaises(AuditError): audit(records)

if __name__=='__main__': unittest.main()
