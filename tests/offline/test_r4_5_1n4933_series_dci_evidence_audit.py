"""R45FG series DCI independent read-only audit and fail-closed mutations."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
import audit_r4_5_1n4933_series_dci_evidence as au
from audit_r4_5_powered_step_evidence import AuditError


def fixture():
    def ev(event,t,**kwargs):
        return dict(event=event,test_id='R45FG',monotonic_s=t,**kwargs)
    raw=ev('dci_series_raw_sample',5.5,psu_voltage_v=.898814,
           psu_current_a=.00203514,regulation='CV',enabled=True,
           dmm_direct_current_a=.003189844,psu_monotonic_s=5.48,
           dmm_monotonic_s=5.50,note='UNVALIDATED_SEQUENTIAL_SAMPLES')
    point=ev('dci_series_point',5.55,preset_v=.9,psu_voltage_v=.898814,
             psu_current_a=.00203514,dmm_direct_current_a=.003189844,
             psu_minus_dmm_current_a=-.001154704,
             current_method='FRONT_10MA_DCI_AMPS_INPUT_LO_IN_SERIES',
             current_accuracy='UNQUALIFIED',burden_voltage='NOT_MEASURED',
             causal_conclusion='PENDING_INDEPENDENT_AUDIT',
             qualification='SINGLE_SERIES_DCI_DIAGNOSTIC_ONLY',
             psu_dmm_gap_ms=20,powered_duration_s=1.55)
    rows=[ev('start',0,variant='1N4933_FRONT_DCI_SERIES_SINGLE_POINT',
             software={'sha':au.CODE_SHA,'runner_sha256':au.RUNNER_SHA,
                       'stage_sha256':au.STAGE_SHA},plan_sha256=au.PLAN_SHA,
             config_sha256=au.BENCH_SHA,ballast_ohm=99.86,
             commanded_preset_v=.9,current_limit_a=.01,
             operator_physical_claim_only=True,current_accuracy='UNQUALIFIED',
             dmm_role='FRONT_10MA_DCI_AMPS_INPUT_LO_IN_SERIES',
             note='KEL103 disconnected; DMM series ammeter; burden not measured'),
          ev('pre_open_approvals',1,gates=['BENCH','INITIAL']),
          *[ev('query',1.001+i*.005,command='SYST:ERR?') for i in range(120)],
          ev('dmm7510_configuration',2,outcome='PASS'),
          ev('off_state_dmm_current',2.5,dmm_current_a=5.709484e-6),
          ev('operator_confirmation',3,gate='POWER'),raw,point,
          ev('shutdown_psu',8,state='VERIFIED_OFF_BY_READBACK'),
          ev('operator_confirmation',9,gate='FINAL'),
          ev('outcome',13,outcome='PASS',physical_off_confirmed=True,
             status='HW NOT_REVIEWED')]
    return rows,dict(enabled_at=4,disabled_at=7,psu_meas_voltage=.898814,
                     psu_meas_current=.00203514,psu_error_polls=194),\
           dict(off=5.709484e-6,powered=.003189844,dmm_error_polls=17)


class SeriesDciEvidenceAuditTests(unittest.TestCase):
    def check(self, fn=None):
        rows,wire,dmm=fixture()
        if fn:fn(rows,wire,dmm)
        with patch.object(au,'check_wire',return_value=wire) as spy,\
             patch.object(au,'audit_dmm',return_value=dmm):
            report=au.audit(rows)
            spy.assert_called_once_with(rows,successful=True,allowed_writes=au.PSU_WRITES)
            return report

    def test_accepted_but_not_calibrated(self):
        report=self.check()
        self.assertEqual(report['status'],'INDEPENDENT_SERIES_DCI_EVIDENCE_REVIEW_PASS')
        self.assertAlmostEqual(report['psu_minus_dmm_ma'],-1.154704)
        self.assertTrue(report['observed_difference_within_nominal_spec'])
        self.assertEqual(report['source_current_accuracy'],'UNQUALIFIED')

    def test_wrong_plan_rejected(self):
        with self.assertRaisesRegex(AuditError,'provenance'):
            self.check(lambda r,w,m:r[0].update(plan_sha256='0'*64))

    def test_bad_current_limit_rejected(self):
        with self.assertRaisesRegex(AuditError,'Current limit'):
            self.check(lambda r,w,m:r[0].update(current_limit_a=.02))

    def test_missing_physical_off_rejected(self):
        with self.assertRaisesRegex(AuditError,'physical OFF'):
            self.check(lambda r,w,m:r[-1].update(physical_off_confirmed=False))

    def test_missing_readback_off_rejected(self):
        with self.assertRaisesRegex(AuditError,'shutdown'):
            self.check(lambda r,w,m:r[-3].update(state='UNKNOWN'))

    def test_mutated_raw_dmm_rejected(self):
        with self.assertRaisesRegex(AuditError,'Raw dmm'):
            self.check(lambda r,w,m:next(x for x in r if x['event']=='dci_series_raw_sample').update(dmm_direct_current_a=.002))

    def test_mutated_point_residual_rejected(self):
        with self.assertRaisesRegex(AuditError,'residual'):
            self.check(lambda r,w,m:next(x for x in r if x['event']=='dci_series_point').update(psu_minus_dmm_current_a=0))

    def test_negative_current_rejected(self):
        with self.assertRaisesRegex(AuditError,'envelope'):
            self.check(lambda r,w,m: (m.update(powered=-.003),
                next(x for x in r if x['event']=='dci_series_raw_sample').update(dmm_direct_current_a=-.003),
                next(x for x in r if x['event']=='dci_series_point').update(dmm_direct_current_a=-.003)))

    def test_fake_calibration_rejected(self):
        with self.assertRaisesRegex(AuditError,'causal claim'):
            self.check(lambda r,w,m:next(x for x in r if x['event']=='dci_series_point').update(current_accuracy='CALIBRATED'))

    def test_wrong_order_rejected(self):
        with self.assertRaisesRegex(AuditError,'sequence'):
            self.check(lambda r,w,m:r[-3].update(monotonic_s=6))

    def test_absent_gate_rejected(self):
        with self.assertRaisesRegex(AuditError,'gate'):
            self.check(lambda r,w,m:r[-2].update(gate='NO_FINAL'))

    def test_duplicate_point_rejected(self):
        with self.assertRaises(AuditError):
            def duplicate(r,w,m):
                idx=next(i for i,x in enumerate(r) if x['event']=='dci_series_point')
                r.insert(idx,copy.deepcopy(r[idx]))
            self.check(duplicate)

    def test_unexpected_event_rejected(self):
        with self.assertRaisesRegex(AuditError,'unsupported'):
            self.check(lambda r,w,m:r[3].update(event='kel103_write'))

    def test_large_sequential_gap_rejected(self):
        with self.assertRaisesRegex(AuditError,'Sequential gap'):
            self.check(lambda r,w,m:next(x for x in r if x['event']=='dci_series_point').update(psu_dmm_gap_ms=950))

    def test_dmm_transport_and_safety_config(self):
        def ev(kind,cmd=None,response=None):
            o={'event':kind}
            if cmd is not None:o['command']=cmd
            if response is not None:o['response']=str(response)
            return o
        rows=[ev('dmm7510_write',c) for c in au.DMM_WRITES]
        for cmd,resp in (('*IDN?','KEITHLEY INSTRUMENTS,MODEL DMM7510,X,1.7.18a'),
                         ('*LANG?','SCPI'),(':ROUT:TERM?','FRON'),
                         (':SENS:FUNC?','CURR:DC'),(':SENS:CURR:RANG?',.01),
                         (':SENS:CURR:NPLC?',1),(':SENS:CURR:REL:STAT?',0),
                         (':SENS:CURR:RANG:AUTO?',0),
                         (':READ?',5.709484e-6),(':READ?',.003189844)):
            rows.extend([ev('dmm7510_query',cmd),ev('dmm7510_read',cmd,resp)])
        for i in range(17):
            rows.extend([ev('dmm7510_query',':SYST:ERR?'),
                         ev('dmm7510_read',':SYST:ERR?','0,"No error"')])
        rows.append(dict(event='dmm7510_configuration',outcome='PASS',
                         function='DC current',terminals='front',range_a=.01,nplc=1,
                         relative=False,autorange=False,
                         physical_amps_jack='OPERATOR_CLAIM_ONLY'))
        self.assertAlmostEqual(au.audit_dmm(rows)['powered'],.003189844)
        rows[-1]['range_a']=.1
        with self.assertRaisesRegex(AuditError,'setup mismatch'):au.audit_dmm(rows)
        rows[-1]['range_a']=.01
        next(x for x in rows if x.get('event')=='dmm7510_read' and x.get('command')==':SYST:ERR?')['response']='-200,"ERROR"'
        with self.assertRaisesRegex(AuditError,'error polling'):au.audit_dmm(rows)

if __name__=='__main__':unittest.main()
