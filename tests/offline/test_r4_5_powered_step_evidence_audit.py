"""Negative and positive independent wire-order tests for R4.5-E reviewer.

Never depends on private hardware logs or connected equipment.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
from audit_r4_5_powered_step_evidence import (AuditError, check_trace,
                                              matching_pairs, number)


def physical_fixture():
    events = []
    def wire(event, command, response=None):
        trace = {'event':event,'command':command}
        if response is not None: trace['response']=response
        events.append({'event':'driver_trace','trace':trace})
        if event in ('write','query'):
            events.append({'event':event,'command':command})
        elif event == 'reply':
            events.append({'event':'read','command':command,'response':response})
    def write(c): wire('write',c)
    def query(c,v): wire('query',c);wire('reply',c,v)
    write('OUTP 0')
    write('INST:NSEL 1')
    write('VOLT:STEP 0.010')
    write('CHAN:OUTP 1')
    query('CHAN:OUTP?', '1')
    write('VOLT:UP')
    query('VOLT?', '3.31')
    query('CHAN:OUTP?', '1')
    write('INST:NSEL 2');query('CHAN:OUTP?', '0')
    write('INST:NSEL 3');query('CHAN:OUTP?', '0')
    write('OUTP 0')
    for ch in (1,2,3):
        write(f'INST:NSEL {ch}');query('CHAN:OUTP?','0')
    write('SYST:LOC')
    return events


class PoweredEvidenceAuditUART(unittest.TestCase):
    def test_positive_wire_fixture(self):
        d = check_trace(physical_fixture())
        self.assertEqual([x[2] for x in d['steps']], ['VOLT:STEP 0.010','VOLT:UP'])
        self.assertEqual(d['on_checks'],2)
        self.assertEqual(d['off_checks'],5)

    def _reject(self, modified):
        with self.assertRaises(AuditError): check_trace(modified)

    def test_missing_final_off_reply_is_rejected(self):
        v=physical_fixture()
        for i in range(len(v)-1,0,-1):
            if v[i]['event']=='read' and v[i]['command']=='CHAN:OUTP?':
                v[i]['response']='1'
                break
        self._reject(v)

    def test_duplicate_power_on_is_rejected(self):
        v=physical_fixture()
        pos=next(i for i,x in enumerate(v) if x.get('event')=='write' and x['command']=='CHAN:OUTP 1')
        v[pos:pos]=[deepcopy(v[pos-1]),deepcopy(v[pos])]
        self._reject(v)

    def test_global_enable_is_rejected(self):
        v=physical_fixture()
        for x in v:
            if x.get('command')=='CHAN:OUTP 1':x['command']='OUTP 1'
            if x.get('event')=='driver_trace' and x['trace']['command']=='CHAN:OUTP 1':
                x['trace']['command']='OUTP 1'
        self._reject(v)

    def test_step_on_other_channel_rejected(self):
        v=physical_fixture()
        pos=next(i for i,x in enumerate(v) if x.get('event')=='write' and x['command']=='VOLT:UP')
        v[pos:pos]=[{'event':'driver_trace','trace':{'event':'write','command':'INST:NSEL 2'}},
                    {'event':'write','command':'INST:NSEL 2'}]
        self._reject(v)

    def test_stage_reply_corruption_rejected(self):
        v=physical_fixture()
        for x in v:
            if x.get('event')=='read' and x['command']=='VOLT?':
                x['response']='3.30';break
        self._reject(v)

    def test_wire_response_corruption_rejected(self):
        v=physical_fixture()
        for x in v:
            if x.get('event')=='driver_trace' and x['trace']['event']=='reply' and x['trace']['command']=='VOLT?':
                x['trace']['response']='3.30';break
        self._reject(v)

    def test_orphan_reply_rejected(self):
        v=physical_fixture()
        for x in v:
            if x.get('event')=='driver_trace' and x['trace']['event']=='query' and x['trace']['command']=='VOLT?':
                x['trace']['command']='CURR?';break
        self._reject(v)

    def test_missing_local_rejected(self):
        v=physical_fixture()
        v=[x for x in v if x.get('command')!='SYST:LOC' and x.get('trace',{}).get('command')!='SYST:LOC']
        self._reject(v)

    def test_external_query_swap_rejected(self):
        v=[{'event':'dmm7510_query','command':':READ?'},
           {'event':'dmm7510_read','command':':SYST:ERR?','response':'0,"No error"'}]
        with self.assertRaises(AuditError):matching_pairs(v,'dmm7510_query','dmm7510_read')

    def test_external_query_unanswered_rejected(self):
        with self.assertRaises(AuditError):matching_pairs(
            [{'event':'kel103_query','command':':INP?'}],
            'kel103_query','kel103_read')

    def test_nonfinite_measurements_rejected(self):
        for s in ('NaN','inf','-Infinity','not_a_number'):
            with self.subTest(s=s),self.assertRaises(AuditError):number(s)


if __name__ == '__main__':unittest.main()
