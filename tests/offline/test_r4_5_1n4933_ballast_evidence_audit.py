"""Offline regression for bounded R45FE reversed/positive evidence review."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
import audit_r4_5_1n4933_ballast_evidence as ba
from audit_r4_5_powered_step_evidence import AuditError


def fixture(successful=True):
    voltage = .3315496 if successful else -.3314382
    psu_v = .899596 if successful else .897882
    psu_i = .00226021 if successful else .00226116
    start = {'event':'start','test_id':'R45FE','monotonic_s':0,
        'variant':'1N4933_DIRECT_99P86OHM_BALLAST_SINGLE_POINT',
        'software':{'sha':ba.CODE_SHA,'runner_sha256':ba.RUNNER_SHA,
                    'stage_sha256':ba.STAGE_SHA},
        'config_sha256':ba.BENCH_SHA,'plan_sha256':ba.PLAN_SHA,
        'ballast_ohm':99.86,'commanded_preset_v':.9,'current_limit_a':.02,
        'operator_physical_claim_only':True,
        'dmm_role':'FRONT_DCV_DIRECT_ACROSS_BALLAST','current_accuracy':'UNQUALIFIED',
        'note':'KEL103 disconnected; no independent DUT ammeter'}
    def event(name,t,**kwargs):
        return dict(test_id='R45FE',event=name,monotonic_s=t,**kwargs)
    rows=[start,event('pre_open_approvals',1,gates=['BENCH','INITIAL'])]
    rows += [event('query', 1.01+i*.0001,command='SYST:ERR?') for i in range(101)]
    rows += [event('dmm7510_configuration',2,outcome='PASS')]
    rows += [event('off_state_ballast_voltage',3,dmm_voltage_v=0)]
    rows += [event('operator_confirmation',6,gate='POWER')]
    rows += [event('ballast_direct_raw_sample',8.04,psu_voltage_v=psu_v,
                   psu_current_a=psu_i,regulation='CV',enabled=True,
                   dmm_ballast_voltage_v=voltage,psu_monotonic_s=8,
                   dmm_monotonic_s=8.03,note='UNVALIDATED_SEQUENTIAL_SAMPLES')]
    if successful:
        i=voltage/99.86
        rows += [event('ballast_direct_point',8.05,psu_voltage_v=psu_v,
                       psu_current_a=psu_i,dmm_ballast_voltage_v=voltage,
                       dmm_direct_ballast_current_a=i,
                       psu_minus_dmm_current_a=psu_i-i,
                       inferred_diode_voltage_v=psu_v-voltage,
                       psu_dmm_gap_ms=30,powered_duration_s=1.6,
                       current_accuracy='UNQUALIFIED',
                       qualification='SINGLE_BALLAST_DCV_DIAGNOSTIC_ONLY',
                       causal_conclusion='NONE; one sequential diagnostic sample')]
    else:
        rows += [event('stage_error',8.05,exception_type='SafetyError',
                       detail='Invalid direct DMM ballast voltage: outside -0.02..1.06')]
    rows += [event('shutdown_psu',10,state='VERIFIED_OFF_BY_READBACK'),
             event('operator_confirmation',12,gate='FINAL'),
             event('outcome',13,outcome='PASS' if successful else 'ABORTED',
                   reason='one direct ballast voltage sample' if successful else
                   'SafetyError: Invalid direct DMM ballast voltage: outside -0.02..1.06',
                   physical_off_confirmed=True,status='HW NOT_REVIEWED')]
    wire={'enabled_at':7,'disabled_at':9,'psu_meas_voltage':psu_v,
          'psu_meas_current':psu_i,'psu_error_polls':193,
          'psu_queries':300,'psu_writes':80}
    meter={'off':0,'powered':voltage,'dmm_error_polls':14}
    return rows,wire,meter


class R45FEBallastAuditTests(unittest.TestCase):
    def audit(self, successful=True, change=None):
        rows, wire, meter=fixture(successful)
        if change is not None:
            change(rows, wire, meter)
        with patch.object(ba,'check_wire',return_value=wire),\
             patch.object(ba,'check_dmm',return_value=meter):
            return ba.audit_ballast(rows,successful=successful)

    def test_positive_point(self):
        result=self.audit()
        self.assertEqual(result['status'],'INDEPENDENT_EVIDENCE_REVIEW_PASS')
        self.assertAlmostEqual(result['ballast_estimated_current_ma'],3.320144201882636)
        self.assertAlmostEqual(result['psu_minus_ballast_current_ma'],-1.059934201882636)

    def test_reversed_leads_rejected_but_off(self):
        result=self.audit(False)
        self.assertEqual(result['run'],'REVERSED_LEADS_SAFE_ABORT')
        self.assertTrue(result['physical_off_confirmed'])

    def test_rejects_missing_off_confirmation(self):
        with self.assertRaisesRegex(AuditError,'shutdown'):
            self.audit(change=lambda r,w,m: r[-3].update(state='UNCONFIRMED'))

    def test_rejects_wrong_plan(self):
        with self.assertRaisesRegex(AuditError,'provenance'):
            self.audit(change=lambda r,w,m: r[0].update(plan_sha256='0'*64))

    def test_rejects_changed_point(self):
        with self.assertRaisesRegex(AuditError,'Calculated current'):
            self.audit(change=lambda r,w,m: next(x for x in r if x['event']=='ballast_direct_point').update(dmm_direct_ballast_current_a=0))

    def test_rejects_fabricated_current_calibration(self):
        with self.assertRaisesRegex(AuditError,'accuracy'):
            self.audit(change=lambda r,w,m: next(x for x in r if x['event']=='ballast_direct_point').update(current_accuracy='CALIBRATED'))

    def test_rejects_nonmonotonic_timestamp(self):
        with self.assertRaisesRegex(AuditError,'Nonmonotonic'):
            self.audit(change=lambda r,w,m: r[-2].update(monotonic_s=5))

    def test_rejects_missing_operator_gate(self):
        with self.assertRaisesRegex(AuditError,'approval'):
            self.audit(change=lambda r,w,m: next(x for x in r if x.get('gate')=='POWER').update(gate='UNSIGNED'))

    def test_rejects_early_off_log(self):
        with self.assertRaisesRegex(AuditError,'gate order'):
            self.audit(change=lambda r,w,m: r[-3].update(monotonic_s=8.5))

    def test_rejects_bypass_outcome_for_negative(self):
        with self.assertRaisesRegex(AuditError,'outcome'):
            self.audit(False,change=lambda r,w,m: r[-1].update(outcome='PASS'))

    def test_rejects_wrong_meter_polarity_for_success(self):
        with self.assertRaisesRegex(AuditError,'screening envelope'):
            self.audit(change=lambda r,w,m: (m.update(powered=-.3315496),
                next(x for x in r if x['event']=='ballast_direct_raw_sample').update(dmm_ballast_voltage_v=-.3315496)))

    def test_rejects_unconfirmed_software_wire_outputs(self):
        with self.assertRaisesRegex(AuditError,'Raw PSU'):
            self.audit(change=lambda r,w,m: w.update(psu_meas_current=0))

    def test_requires_raw_point_before_success(self):
        with self.assertRaisesRegex(AuditError,'Missing accepted point'):
            self.audit(change=lambda r,w,m: r.__setitem__(slice(None), [x for x in r if x['event']!='ballast_direct_point']))

    def test_rejects_unexpected_stage_error_after_success(self):
        with self.assertRaisesRegex(AuditError,'unexpected error'):
            def mutate(rows,w,m):
                rows.insert(-3,dict(event='stage_error',test_id='R45FE',monotonic_s=8.06))
            self.audit(change=mutate)

    def test_dmm_query_pair_validation(self):
        rows=[]
        t=0
        def pair(cmd,reply):
            nonlocal t
            t+=1; rows.append(dict(event='dmm7510_query',command=cmd))
            rows.append(dict(event='dmm7510_read',command=cmd,response=reply))
        pair('*IDN?','KEITHLEY INSTRUMENTS,MODEL DMM7510,redacted,1.7.18a')
        pair('*LANG?','SCPI')
        pair(':ROUT:TERM?','FRON')
        for _ in range(14):pair(':SYST:ERR?','0,"No error;0;0 0"')
        pair(':READ?','0.0');pair(':READ?','0.3315496')
        rows += [dict(event='dmm7510_write',command=c) for c in ba.DMM_WRITES]
        rows += [dict(event='dmm7510_configuration',outcome='PASS',function='DC voltage',
                      terminals='front',range_v=10,nplc=1)]
        result=ba.check_dmm(rows)
        self.assertAlmostEqual(result['powered'],.3315496)
        next(x for x in rows if x.get('event')=='dmm7510_read' and x.get('command')==':SYST:ERR?')['response']='-200,"Error"'
        with self.assertRaises(AuditError):ba.check_dmm(rows)


if __name__=='__main__':
    unittest.main()
