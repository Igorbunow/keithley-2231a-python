"""R5-E8: reviewed neutral event evidence stays pinned and scoped."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keithley2231a import Keithley2231A
from tools.audit_r5_e7_neutral_trigger import AuditError
from tools.audit_r5_e8_neutral_hw_evidence import audit_review, INDEX


class NeutralHardwareReviewContract(unittest.TestCase):
    def setUp(self):
        self.source = json.loads(INDEX.read_text(encoding='utf-8'))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.evidence = self.folder / 'events.jsonl'
        self.record = self.folder / 'review.json'
        self.synthetic = [
            {'event':'start','software':{
                'sha':self.source['git_head_at_run'],
                'source_sha256':self.source['source_sha256'],
                'r5e7_runner_sha256':self.source['runner_sha256'],
                'r5e7_stage_sha256':self.source['stage_sha256'],
                'dirty':self.source['working_tree_dirty_at_run']},
             'config_sha256':self.source['config_sha256']},
            {'event':'write','command':'*TRG'},
            {'event':'read','command':'SYST:ERR?','response':'0, "No error"'},
            {'event':'read','command':'CHAN:OUTP?','response':'0'},
        ]
        self.evidence.write_text(''.join(json.dumps(e)+'\n' for e in self.synthetic))
        self.source.update(original_sha256=hashlib.sha256(self.evidence.read_bytes()).hexdigest(),
                           wire_write_count=1,scpi_error_query_count=1,off_readback_count=1)
        self.record.write_text(json.dumps(self.source))

    def fake_audit(self, path):
        self.assertEqual(path,self.evidence)
        return {'status':self.source['status'],
                'source_sha256':self.source['source_sha256'],
                'evidence_sha256':self.source['original_sha256'],
                'trigger_count':self.source['trigger_count'],
                'records':self.source['record_count'],
                'queries':self.source['wire_query_count'],
                'original':self.source['original_presets'],
                'neutral':self.source['neutral_presets'],
                'channels':[1,2,3]}

    def test_hardware_status_is_only_neutral_off(self):
        features = Keithley2231A.capabilities()
        neutral = features.feature('neutral_trigger_bus_off_candidate')
        self.assertEqual(neutral.evidence,'bounded_hw')
        self.assertEqual(neutral.hardware_evidence[0].channels,(1,2,3))
        self.assertEqual(neutral.hardware_evidence[0].output_state,'OFF')
        self.assertEqual(neutral.hardware_evidence[0].regulation,None)
        self.assertEqual(features.feature('hardware_triggered_setpoint').evidence,'documented_only')
        self.assertEqual(features.feature('trigger_channel_coupling').evidence,'documented_only')

    def test_index_has_original_provenance_and_scope(self):
        self.assertEqual(self.source['git_head_at_run'][:7],'2a11929')
        self.assertEqual(self.source['trigger_count'],1)
        self.assertEqual(self.source['status'],'REVIEWABLE_PASS_NEUTRAL_OFF_ONLY_NOT_ACTUATION')
        self.assertEqual(self.source['coupling'],'UNKNOWN_NOT_QUALIFIED')
        self.assertEqual(self.source['analog'],'NOT_MEASURED')
        self.assertEqual(self.source['wire_query_count'],3263)
        self.assertEqual(self.source['record_count'],14352)
        self.assertEqual(self.source['duplicated_adjacent_error_query_pairs_in_call_sequence'],50)

    def test_review_wrapper_pins_metadata(self):
        with patch('tools.audit_r5_e8_neutral_hw_evidence.audit_path',side_effect=self.fake_audit):
            report=audit_review(self.evidence,index=self.record)
        self.assertEqual(report['trigger_count'],1)
        self.assertEqual(report['scope'],'NEUTRAL_OFF_ONLY_NOT_ACTUATION')

    def test_original_mutation_rejected_even_if_underlying_auditor_would_pass(self):
        self.evidence.write_text(self.evidence.read_text()+'{}\n')
        with self.assertRaisesRegex(AuditError,'SHA-256'):
            audit_review(self.evidence,index=self.record)

    def test_wrong_runner_fingerprint_rejected(self):
        self.synthetic[0]['software']['r5e7_runner_sha256']='f'*64
        self.evidence.write_text(''.join(json.dumps(e)+'\n' for e in self.synthetic))
        self.source['original_sha256']=hashlib.sha256(self.evidence.read_bytes()).hexdigest()
        self.record.write_text(json.dumps(self.source))
        with patch('tools.audit_r5_e8_neutral_hw_evidence.audit_path',side_effect=self.fake_audit):
            with self.assertRaisesRegex(AuditError,'runner'):
                audit_review(self.evidence,index=self.record)


if __name__=='__main__':
    unittest.main()
