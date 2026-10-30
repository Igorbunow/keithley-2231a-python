"""Isolated CC-specific audit checks; no hardware and no private JSONL fixture."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from audit_r4_5_powered_series_cc_review import (
    verify_cc_wire, verify_cc_physics, AuditError)


LEVELS = (.100, .105, .110, .105, .100)
MOVES = ('CURR:UP', 'CURR:UP', 'CURR:DOWN', 'CURR:DOWN')


def fixture_wire():
    rows = []
    def tx(event, command, response=None):
        trace = {'event':event, 'command':command}
        if response is not None:
            trace['response'] = response
        rows.append({'event':'driver_trace','trace':trace})
    tx('write','SYST:REM')
    tx('write','INST:NSEL 1')
    tx('write','CURR:STEP 0.005')
    tx('reply','CURR:STEP?','0.005')
    tx('reply','CURR?',str(LEVELS[0]))
    tx('write','CHAN:OUTP 1')
    for n, action in enumerate(MOVES):
        tx('write','INST:NSEL 1')
        tx('reply','CURR:STEP?','0.005')
        tx('reply','CURR?',str(LEVELS[n]))
        tx('write',action)
        tx('reply','CURR?',str(LEVELS[n+1]))
        tx('write','INST:NSEL 2')
        tx('write','INST:NSEL 3')
        tx('write','INST:NSEL 1')
        tx('reply','CURR?',str(LEVELS[n+1]))
    tx('write','SYST:REM')
    tx('write','OUTP 0')
    return rows


def fixture_physics():
    current = (.1006,.1056,.1107,.1056,.1008)
    records = []
    for n, i in enumerate(current):
        u = 11*i
        v_dmm = u-0.0007
        records.append({'event':'powered_step_snapshot',
                        'kel_v':u,'kel_a':i,'psu_a':i-0.0009,
                        'dmm_v':v_dmm,'psu_v':v_dmm+0.004,
                        'kel_w':u*i, 'psu_meas_w':(v_dmm+0.004)*(i-0.0009)})
    for n in range(4):
        sign = 1 if n < 2 else -1
        records.append({'event':'powered_series_segment',
                        'kel_signed_delta_a':sign*(current[n+1]-current[n]),
                        'dmm_signed_delta_v':sign*11*(current[n+1]-current[n])})
    records.append({'event':'powered_series_return',
                    'kel_return_error_a':abs(current[4]-current[0])})
    return records


class CCWireAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows = fixture_wire()
    def mutate(self, command, replacement, event='write', occurrence=0):
        hits=[x for x in self.rows if x['trace']['event']==event and
              x['trace']['command']==command]
        hits[occurrence]['trace']['command']=replacement
    def test_valid_current_trace(self):
        result=verify_cc_wire(self.rows)
        self.assertEqual(result['physical_digital_levels_a'],list(LEVELS))
        self.assertEqual(result['wire_step_readbacks'],5)
    def test_wrong_first_step_readback(self):
        seen=[x for x in self.rows if x['trace']['event']=='reply' and
              x['trace']['command']=='CURR?']
        seen[2]['trace']['response']='0.104'
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_unexpected_config_during_output_on(self):
        self.rows.insert(-2,{'event':'driver_trace','trace':
                               {'event':'write','command':'VOLT 3.30'}})
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_repeat_or_missing_move(self):
        self.mutate('CURR:DOWN','CURR:UP',occurrence=1)
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_missing_step_confirmation(self):
        self.rows=[x for x in self.rows if not(x['trace']['event']=='reply'
                   and x['trace']['command']=='CURR:STEP?')]
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_changed_step_confirmation(self):
        hits=[x for x in self.rows if x['trace']['event']=='reply' and
              x['trace']['command']=='CURR:STEP?']
        hits[2]['trace']['response']='0.010'
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_move_on_wrong_channel(self):
        idx=next(i for i,x in enumerate(self.rows) if
                 x['trace']['command']=='CURR:UP')
        self.rows.insert(idx,{'event':'driver_trace','trace':
                               {'event':'write','command':'INST:NSEL 2'}})
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)
    def test_intermediate_digital_drift(self):
        hits=[x for x in self.rows if x['trace']['event']=='reply' and
              x['trace']['command']=='CURR?']
        hits[3]['trace']['response']='0.106'
        with self.assertRaises(AuditError):verify_cc_wire(self.rows)


class CCPhysicsAuditTests(unittest.TestCase):
    def setUp(self):
        self.rows=fixture_physics()
    def test_valid_five_point_cc_physics(self):
        result=verify_cc_physics(self.rows)
        self.assertAlmostEqual(result['kel_return_error_ma'],0.2)
        self.assertEqual(len(result['signed_kel_segments_ma']),4)
    def test_kel_segment_metadata_mismatch(self):
        segments=[x for x in self.rows if x['event']=='powered_series_segment']
        segments[3]['kel_signed_delta_a']+=0.001
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_dmm_segment_metadata_mismatch(self):
        segments=[x for x in self.rows if x['event']=='powered_series_segment']
        segments[1]['dmm_signed_delta_v']+=0.005
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_missing_snap(self):
        self.rows.pop(2)
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_wrong_cr_ohm_ratio(self):
        self.rows[1]['kel_v']*=1.15
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_missing_return_record(self):
        self.rows.pop()
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_inconsistent_return_metadata(self):
        self.rows[-1]['kel_return_error_a']=0
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_missing_physical_current_step(self):
        self.rows[2]['kel_a']=self.rows[1]['kel_a']
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)
    def test_dmm_step_does_not_follow_current(self):
        self.rows[2]['dmm_v']+=0.020
        with self.assertRaises(AuditError):verify_cc_physics(self.rows)


if __name__=='__main__':
    unittest.main()
