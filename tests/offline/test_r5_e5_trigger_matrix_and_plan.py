"""OFFLINE R5-E5 hardware provenance checks and no-I/O bus trigger risk planner."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest
from keithley2231a import Keithley2231A
from tools.audit_r5_e5_trigger_write_matrix import (
    INDEX, MatrixAuditError, STATUS, validate, audit_three
)
from tools.plan_r5_e5_trigger_event import (
    BOUNDED_EXAMPLE, ChannelBounds, PlanningError, plan_offline
)


def fixture():
    index=json.loads(INDEX.read_text(encoding='utf-8'))
    results=[]
    for entry in index['channels']:
        results.append(dict(
            status='REVIEWABLE_PASS_OFF_ONLY_NOT_TRIGGER_ACTUATION',
            channel=entry['channel'], original_sha256=entry['original_jsonl_sha256'],
            source_sha256=index['source_sha256'], firmware=index['firmware'],
            scpi_queries=entry['queries'], off_readbacks=entry['off_readbacks'],
            digital_off_confirmed=True,physical_off_confirmed=True,
            staged={'voltage_v':entry['preset_voltage_v'],'current_a':entry['preset_current_a']},
            original={'voltage_v':0.,'current_a':0.},
            restored={'voltage_v':0.,'current_a':0.},
        ))
    return index,results


class ReviewedMatrix(unittest.TestCase):
    def test_three_distinct_reviewer_summary_matches_pinned_provenance(self):
        index,results=fixture()
        self.assertEqual(validate(results[::-1],index)['status'], STATUS)
        self.assertEqual(validate(results,index)['channels'], [1,2,3])

    def test_reject_missing_and_repeated_channels(self):
        index,results=fixture()
        for altered in (results[:2],results+[results[0]],
                        [results[0],results[0],results[2]]):
            with self.subTest(size=len(altered)),self.assertRaises(MatrixAuditError):
                validate(altered,index)

    def test_reject_changed_sha_source_and_firmware(self):
        for field,value in (('original_sha256','f'*64),('source_sha256','f'*64),
                            ('firmware','1.02-1.02'),('status','PASS')):
            index,results=fixture();results[0][field]=value
            with self.subTest(field=field),self.assertRaises(MatrixAuditError):
                validate(results,index)

    def test_reject_unconfirmed_off_and_missing_queries(self):
        for field,value in (('physical_off_confirmed',False),('digital_off_confirmed',False),
                            ('scpi_queries',0),('off_readbacks',0)):
            index,results=fixture();results[1][field]=value
            with self.subTest(field=field),self.assertRaises(MatrixAuditError):
                validate(results,index)

    def test_reject_wrong_trigger_preset_and_failed_restore(self):
        for state,key,value in (('staged','voltage_v',5.),('staged','current_a',.111),
                                ('restored','voltage_v',.01),('original','current_a',.01)):
            index,results=fixture(); results[2][state][key]=value
            with self.subTest(state=state,key=key),self.assertRaises(MatrixAuditError):
                validate(results,index)

    def test_overclaim_in_index_is_rejected(self):
        for field,value in (('actuation','PASS'),('analog','MEASURED'),
                            ('trigger_coupling','QUALIFIED')):
            index,results=fixture();index[field]=value
            with self.subTest(field=field),self.assertRaises(MatrixAuditError):
                validate(results,index)

    def test_same_path_three_times_rejected_prior_to_read(self):
        path=Path('/not/a/real/events.jsonl')
        with self.assertRaises(MatrixAuditError): audit_three([path,path,path])

    def test_static_capabilities_are_hardware_bounded_but_actuation_still_unexposed(self):
        caps=Keithley2231A.capabilities()
        staging=caps.feature('trigger_preset_off_staging')
        self.assertEqual(staging.evidence,'bounded_hw')
        self.assertEqual(staging.hardware_evidence[0].channels,(1,2,3))
        self.assertEqual(staging.hardware_evidence[0].output_state,'OFF')
        self.assertTrue((Path(__file__).resolve().parents[2]/staging.hardware_evidence[0].reference).is_file())
        self.assertFalse(caps.feature('hardware_triggered_setpoint').driver_exposed)
        self.assertEqual(caps.feature('trigger_channel_coupling').evidence,'documented_only')


class TriggerRiskPlanner(unittest.TestCase):
    def test_example_checks_all_three_even_if_single_channel_requested_later(self):
        result=plan_offline(BOUNDED_EXAMPLE)
        self.assertEqual(result['potential_responders'],[1,2,3])
        self.assertEqual(result['event'],'NOT_SENT')
        self.assertEqual(result['status'],'DRY_RUN_NO_SCPI_NO_TRIGGER_AUTHORIZATION')
        self.assertEqual(result['coupling'],'UNKNOWN_NOT_QUALIFIED')
        self.assertAlmostEqual(result['aggregate_setpoint_envelope_w'],.825)

    def test_missing_channel_or_duplicate_channel_fails(self):
        for channels in (BOUNDED_EXAMPLE[:2], (BOUNDED_EXAMPLE[0],)*3,
                         BOUNDED_EXAMPLE + BOUNDED_EXAMPLE[:1]):
            with self.assertRaises(PlanningError):plan_offline(channels)

    def test_nonfinite_negative_bool_rejected(self):
        for val in (float('inf'),float('nan'),-1.,True):
            channels=list(BOUNDED_EXAMPLE)
            channels[1]=ChannelBounds(2,1.,.1,val,.08,1.5,.1)
            with self.subTest(value=val),self.assertRaises(PlanningError):
                plan_offline(channels)

    def test_overshoot_on_another_channel_is_not_ignored(self):
        channels=list(BOUNDED_EXAMPLE)
        channels[2]=ChannelBounds(3,1.,.1,1.6,.08,1.5,.1)
        with self.assertRaises(PlanningError):plan_offline(channels)

    def test_ch3_physical_limit_checked(self):
        channels=list(BOUNDED_EXAMPLE)
        channels[2]=ChannelBounds(3,1.,.1,4.,.08,6.,.1)
        with self.assertRaises(PlanningError):plan_offline(channels)

    def test_planning_import_and_call_do_not_access_uart(self):
        # Pure dataclass -> dict transformation. No transport, serial import or I/O.
        result=plan_offline(BOUNDED_EXAMPLE)
        self.assertFalse(any(key in result for key in ('port','serial','execute','trigger_sent')))


if __name__=='__main__':unittest.main()
