"""Offline adversarial tests for independent 5-point powered series reviewer.

No dependency on user hardware evidence or access to real instruments.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from audit_r4_5_powered_series_evidence import (
    AuditError, review_measurements, verify_wire, OFFSETS)


class FivePointPhysicsAuditTests(unittest.TestCase):
    def setUp(self):
        self.records=[]
        self.snaps=[]
        self.moves=[]
        self.snapshot_data=[]
        for i,offset in enumerate(OFFSETS):
            v=3.3+0.01*offset
            row=dict(event='powered_step_snapshot',label=f'point_{i}',mode='CV',
                offset_steps=offset,monotonic_s=100+i*15+4,
                psu_v=v,psu_a=v/40,psu_derived_w=v*v/40,psu_meas_w=v*v/40,
                kel_v=v,kel_a=v/40,kel_w=v*v/40,dmm_v=v,
                timestamps_monotonic_s={'psu_s':100+i*15+2,
                    'kel_s':100+i*15+2.03,'dmm_s':100+i*15+2.06,
                    'power_s':100+i*15+3.5})
            for key,cmd,unit in [('psu_v','MEAS:VOLT?',''),
                                  ('psu_a','MEAS:CURR?',''),
                                  ('psu_meas_w','MEAS:POW?',''),
                                  ('kel_v',':MEAS:VOLT?','V'),
                                  ('kel_a',':MEAS:CURR?','A'),
                                  ('kel_w',':MEAS:POW?','W'),
                                  ('dmm_v',':READ?','')]:
                kind=('dmm7510_read' if key=='dmm_v' else
                      'kel103_read' if key.startswith('kel_') else 'read')
                self.records.append(dict(event=kind,command=cmd,
                                         response=str(row[key])+unit,
                                         monotonic_s=100+i*15+1))
            self.records.append(row)
            self.snaps.append((len(self.records)-1,row))
            if i<4:
                self.records.append(dict(event='write',command='VOLT:UP' if i<2 else 'VOLT:DOWN',
                                         monotonic_s=100+i*15+5))
                self.moves.append(len(self.records)-1)

    def verify(self, rows=None, snaps=None):
        return review_measurements(rows or self.records,'cv',snaps or self.snaps,
                                   ('up','up','down','down'),self.moves)

    def test_good_five_point_data(self):
        result=self.verify()
        self.assertAlmostEqual(result['dmm_return_error_v'],0)
        self.assertEqual(len(result['signed_dmm_segments_v']),4)

    def test_missing_raw_dmm_read_rejected(self):
        p=[x for x in self.records if not(x.get('event')=='dmm7510_read' and
                                          x.get('response')=='3.32')]
        # missing read at point2 causes a raw/sample value mismatch
        with self.assertRaises(AuditError): self.verify(rows=p)

    def test_wrong_dmm_snapshot_rejected(self):
        self.snaps[2][1]['dmm_v']+=.003
        with self.assertRaises(AuditError): self.verify()

    def test_wrong_current_rejected(self):
        self.snaps[3][1]['psu_a']+=.005
        with self.assertRaises(AuditError): self.verify()

    def test_bad_power_rejected(self):
        self.snaps[2][1]['psu_meas_w']+=.01
        with self.assertRaises(AuditError): self.verify()

    def test_nonfinite_rejected(self):
        self.snaps[4][1]['kel_v']=float('nan')
        with self.assertRaises(AuditError): self.verify()

    def test_wrong_offset_rejected(self):
        self.snaps[2][1]['offset_steps']=1
        with self.assertRaises(AuditError): self.verify()

    def test_bad_timing_rejected(self):
        self.snaps[2][1]['timestamps_monotonic_s']['power_s']+=8
        with self.assertRaises(AuditError): self.verify()

    def test_missing_snapshot_rejected(self):
        points=self.snaps[:-1]
        # final missing is independently invalid in caller; here check readback return explicitly
        self.assertEqual(len(points),4)
        with self.assertRaises((AuditError,IndexError)):
            review_measurements(self.records,'cv',points,('up','up','down','down'),self.moves)

    def test_return_failure_rejected_even_when_digital_returns(self):
        # alter raw latest DMM read and final snapshot in a physically consistent way
        self.snaps[4][1]['dmm_v']+=.009
        r=[x for x in self.records if x.get('event')=='dmm7510_read'][-1]
        r['response']=str(self.snaps[4][1]['dmm_v'])
        with self.assertRaises(AuditError): self.verify()

    def test_extra_hysteresis_rejected(self):
        self.snaps[3][1]['dmm_v']+=.004
        [x for x in self.records if x.get('event')=='dmm7510_read'][3]['response']=str(self.snaps[3][1]['dmm_v'])
        with self.assertRaises(AuditError): self.verify()

    def test_reversed_step_rejected(self):
        self.snaps[1][1]['dmm_v']-=.020
        [x for x in self.records if x.get('event')=='dmm7510_read'][1]['response']=str(self.snaps[1][1]['dmm_v'])
        with self.assertRaises(AuditError): self.verify()

    def test_high_load_power_rejected(self):
        self.snaps[1][1]['kel_w'] = .7
        [x for x in self.records if x.get('event')=='kel103_read' and x.get('command')==':MEAS:POW?'][1]['response']='0.7W'
        with self.assertRaises(AuditError): self.verify()


class WireAuditTests(unittest.TestCase):
    def setUp(self):
        self.trace=[]
        self.stage=[]
        def write(cmd):
            self.trace.append({'event':'write','command':cmd})
            self.stage.append({'event':'write','command':cmd})
        def query(cmd,reply):
            self.trace.extend(({'event':'query','command':cmd},
                               {'event':'reply','command':cmd,'response':reply}))
            self.stage.extend(({'event':'query','command':cmd},
                               {'event':'read','command':cmd,'response':reply}))
        self.write,self.query=write,query
        write('SYST:REM')
        write('VOLT:STEP 0.010')
        write('INST:NSEL 1'); write('CHAN:OUTP 1')
        for ch in (1,2,3):
            write(f'INST:NSEL {ch}')
            query('CHAN:OUTP?','1' if ch==1 else '0')
        for action in ('VOLT:UP','VOLT:UP','VOLT:DOWN','VOLT:DOWN'):
            write('INST:NSEL 1'); write(action)
            query('CHAN:OUTP?','1')
        write('OUTP 0')
        for ch in (1,2,3):
            write(f'INST:NSEL {ch}'); query('CHAN:OUTP?','0')
        write('SYST:LOC')

    def records(self):
        return ([{'event':'driver_trace','trace':x} for x in self.trace]+self.stage)

    def test_valid_order(self):
        self.assertEqual(verify_wire(self.records(),'cv')['scpi_writes'],len([x for x in self.stage if x['event']=='write']))

    def test_extra_up_rejected(self):
        self.trace.insert(-1,{'event':'write','command':'VOLT:UP'})
        self.stage.append({'event':'write','command':'VOLT:UP'})
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_wrong_direction_rejected(self):
        for group in (self.trace,self.stage):
            next(x for x in group if x['event']=='write' and x['command']=='VOLT:DOWN')['command']='VOLT:UP'
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_reply_mutation_rejected(self):
        next(x for x in self.stage if x['event']=='read' and x['command']=='CHAN:OUTP?')['response']='0'
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_other_channel_on_rejected(self):
        for group in (self.trace,self.stage):
            next(x for x in group if x['event']=='write' and x['command']=='CHAN:OUTP 1')['command']='OUTP 1'
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_missing_off_rejected(self):
        for group in (self.trace,self.stage):
            group[:]=[x for x in group if not(x['event']=='write' and x['command']=='OUTP 0')]
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_unpaired_query_rejected(self):
        self.trace.append({'event':'query','command':'VOLT?'})
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

    def test_corrupt_step_channel_rejected(self):
        for group in (self.trace,self.stage):
            ix=next(i for i,x in enumerate(group) if x['event']=='write' and x['command']=='VOLT:UP')
            group.insert(ix,{'event':'write','command':'INST:NSEL 2'})
        with self.assertRaises(AuditError): verify_wire(self.records(),'cv')

if __name__=='__main__': unittest.main()
