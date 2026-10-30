"""Adversarial synthetic wire and provenance tests; no physical bench connection."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from audit_r4_5_iv_evidence import AuditError, audit, wire_audit, main


class IVReviewWireTests(unittest.TestCase):
    def setUp(self):
        self.traces=[]
        self.stage=[]
        def wr(cmd):
            self.traces.append(dict(event='write',command=cmd,profile_bypass=False))
            self.stage.append(dict(event='write',command=cmd))
        def qr(cmd,val):
            self.traces.extend((dict(event='query',command=cmd,profile_bypass=False),
                                dict(event='reply',command=cmd,response=str(val),profile_bypass=False)))
            self.stage.extend((dict(event='query',command=cmd),
                               dict(event='read',command=cmd,response=str(val))))
        self.wr,self.qr=wr,qr
        wr('SYST:REM'); wr('VOLT 3.00'); wr('CURR 0.150')
        wr('VOLT:STEP 0.100'); qr('VOLT:STEP?','0.1')
        wr('CHAN:OUTP 1')
        for j,v in enumerate((3,3.1,3.2,3.1,3)):
            if j: wr('VOLT:UP' if j<3 else 'VOLT:DOWN')
            qr('VOLT?',v); qr('VOLT:STEP?','0.1') if j else None
            qr('STAT:OPER:INST:ISUM1:COND?','9')
            qr('STAT:QUES:INST:ISUM1:COND?','1')
            for ch in (1,2,3):
                wr(f'INST:NSEL {ch}'); qr('CHAN:OUTP?','1' if ch==1 else '0')
            wr('INST:NSEL 1')
        wr('OUTP 0')
        for ch in (1,2,3):
            wr(f'INST:NSEL {ch}'); qr('CHAN:OUTP?','0')
        wr('SYST:LOC')
        for j in range(901): qr('SYST:ERR?','0, "No error"')
        # Keep SYST:LOC last physically and on stage.
        for seq in (self.traces,self.stage):
            last=next(x for x in seq if x['event']=='write' and x['command']=='SYST:LOC')
            seq.remove(last); seq.append(last)

    def rows(self):
        return ([dict(event='driver_trace',trace=x) for x in self.traces] +
                copy.deepcopy(self.stage))

    def test_synthetic_wire_passes(self):
        r=wire_audit(self.rows())
        self.assertEqual(r['move_commands'],['VOLT:UP','VOLT:UP','VOLT:DOWN','VOLT:DOWN'])
        self.assertEqual(r['scpi_error_polls'],901)

    def test_wrong_step_direction(self):
        for group in (self.traces,self.stage):
            next(x for x in group if x['event']=='write' and x['command']=='VOLT:DOWN')['command']='VOLT:UP'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_extra_step(self):
        for group in (self.traces,self.stage):
            group.insert(8,dict(event='write',command='VOLT:UP',**({'profile_bypass':False} if group is self.traces else {})))
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_off_state_lies(self):
        next(x for x in self.traces if x['event']=='reply' and x['command']=='CHAN:OUTP?')['response']='0'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_payload_swap_between_stage_and_wire(self):
        next(x for x in self.stage if x['event']=='read' and x['command']=='VOLT?')['response']='3.3'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_wrong_mode_register(self):
        next(x for x in self.traces if x['event']=='reply' and x['command'].startswith('STAT:OPER'))['response']='10'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_trace_bypass(self):
        self.traces[0]['profile_bypass']=True
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_missing_query_reply(self):
        i=next(i for i,x in enumerate(self.traces) if x['event']=='reply' and x['command']=='VOLT?')
        self.traces.pop(i)
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_wrong_channel_output(self):
        for g in (self.traces,self.stage):
            on=next(x for x in g if x['event']=='write' and x['command']=='CHAN:OUTP 1')
            on['command']='OUTP 1'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_profile_mutation_when_on(self):
        for g in (self.traces,self.stage):
            index=next(i for i,x in enumerate(g) if x['event']=='write' and x['command']=='VOLT:UP')
            g.insert(index,dict(event='write',command='VOLT 3.00',**({'profile_bypass':False} if g is self.traces else {})))
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_missing_final_off(self):
        for g in (self.traces,self.stage):
            g[:]=[x for x in g if not(x['event']=='write' and x['command']=='OUTP 0')]
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_nonzero_scpi_error(self):
        for g in (self.traces,self.stage):
            next(x for x in g if x['event'] in ('reply','read') and x['command']=='SYST:ERR?')['response']='120,"Parameter error"'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_wrong_step_readback(self):
        for g in (self.traces,self.stage):
            next(x for x in g if x['event'] in ('reply','read') and x['command']=='VOLT:STEP?')['response']='0.01'
        with self.assertRaises(AuditError):wire_audit(self.rows())

    def test_duplicate_enable(self):
        for g in (self.traces,self.stage):
            pos=next(i for i,x in enumerate(g) if x['event']=='write' and x['command']=='CHAN:OUTP 1')
            g.insert(pos,dict(event='write',command='CHAN:OUTP 1',**({'profile_bypass':False} if g is self.traces else {})))
        with self.assertRaises(AuditError):wire_audit(self.rows())


class IVReviewEvidenceTests(unittest.TestCase):
    def test_missing_gate_is_rejected(self):
        with self.assertRaises(AuditError):audit([dict(event='start',test_id='R45FIVA',monotonic_s=1,
                                                   variant='RESISTIVE_IV_CR40')])

    def test_wrong_test_id_is_rejected(self):
        with self.assertRaises(AuditError):audit([dict(event='outcome',test_id='OTHER',monotonic_s=1)])

    def test_sha_mismatch_returns_error(self):
        with tempfile.TemporaryDirectory() as path:
            p=Path(path)/'original.jsonl'
            p.write_text('{"not":"valid evidence"}\n',encoding='utf-8')
            self.assertEqual(main([str(p),'--expected-sha256','0'*64]),2)

    def test_hash_correct_but_not_evidence_returns_error(self):
        with tempfile.TemporaryDirectory() as path:
            p=Path(path)/'original.jsonl'
            p.write_text('{"not":"valid evidence"}\n',encoding='utf-8')
            sha=hashlib.sha256(p.read_bytes()).hexdigest()
            self.assertEqual(main([str(p),'--expected-sha256',sha]),2)

if __name__=='__main__':unittest.main()
