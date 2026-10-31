"""R5-E2 runner and offline evidence audit over a real POSIX virtual UART."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from qualification import Evidence
from r5_e2_trigger_read_stage import (STAGE, TriggerReadScope, query_token,
                                       run_trigger_readback)
from tools.audit_r5_e2_trigger_read import AuditError, audit, audit_path
from tools.r5_e2_trigger_read_qualify import main as runner_main


@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class TriggerReadQualification(unittest.TestCase):
    def run_case(self, channel=1, **peer_options):
        peer=UARTPeer(profile_registers=True,trigger_registers=True,**peer_options)
        self.addCleanup(peer.close)
        transport=open_test_transport(peer,timeout=.18)
        self.addCleanup(transport.close)
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path=Path(temporary.name)/'evidence.jsonl'
        ev=Evidence(path,STAGE)
        ev.add('start',phase='TRIGGER_PRESET_OFF_READ',channel=channel,
               config_sha256='a'*64,
               software={'source_sha256':'b'*64,'r5e2_runner_sha256':'c'*64,
                         'r5e2_stage_sha256':'d'*64})
        ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
               approved_before_evidence_open=True,config_sha256='a'*64)
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_trigger_readback(transport,ev,channel=channel,
                profile_gate=lambda *a:None,query_gate=lambda *a:None,
                final_gate=lambda *a:None)
        return outcome,peer,path

    def test_all_channels_end_to_end_virtual_uart_and_auditor(self):
        for channel in (1,2,3):
            with self.subTest(channel=channel):
                status,peer,path=self.run_case(channel)
                self.assertEqual(status,'PASS')
                result=audit_path(path)
                self.assertEqual(result['channel'],channel)
                self.assertEqual(result['status'],'REVIEWABLE_PASS_NOT_TRIGGER_ACTUATION')
                self.assertAlmostEqual(result['voltage_v'],channel*.5)
                self.assertAlmostEqual(result['current_a'],channel*.025)
                self.assertEqual(peer.rx.count('VOLT:TRIG?'),1)
                self.assertEqual(peer.rx.count('CURR:TRIG?'),1)
                self.assertFalse(any(c=='*TRG' or c.startswith(('INST:COUP','VOLT:TRIG ',
                                 'CURR:TRIG ','CHAN:OUTP 1','OUTP 1','APP')) for c in peer.rx))
                self.assertEqual(peer.unsupported,[])

    def test_bad_trigger_reply_requires_abort_not_pass(self):
        status,peer,path=self.run_case(2,response_overrides={'VOLT:TRIG?':'INVALID'})
        self.assertEqual(status,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:TRIG?'),1)
        self.assertNotIn('CURR:TRIG?',peer.rx)
        with self.assertRaises(AuditError):audit_path(path)

    def test_second_trigger_timeout_no_retry_and_audit_rejected(self):
        status,peer,path=self.run_case(1,drop='CURR:TRIG?')
        self.assertEqual(status,'ABORTED')
        self.assertEqual(peer.rx.count('CURR:TRIG?'),1)
        with self.assertRaises(AuditError):audit_path(path)

    def test_scpi_error_after_first_read_blocks_second(self):
        status,peer,path=self.run_case(3,fault_after='VOLT:TRIG?')
        self.assertEqual(status,'ABORTED')
        self.assertNotIn('CURR:TRIG?',peer.rx)
        with self.assertRaises(AuditError):audit_path(path)

    def test_scope_rejects_unauthorized_commands(self):
        peer=UARTPeer(profile_registers=True,trigger_registers=True)
        self.addCleanup(peer.close)
        transport=open_test_transport(peer,timeout=.15)
        self.addCleanup(transport.close)
        class Stub:
            def add(self,*args,**kwargs):return {'response':kwargs.get('response','')}
        scope=TriggerReadScope(transport,Stub(),channel=2)
        for cmd in ('*TRG','TRIG','INST:COUP CH1.CH2',
                    'VOLT:TRIG 1','CURR:TRIG 0.1','OUTP 1','CHAN:OUTP 1'):
            with self.subTest(cmd=cmd),self.assertRaises(SafetyError):
                scope.write(cmd)
        with self.assertRaises(SafetyError):scope.query('VOLT:TRIG?')
        self.assertNotIn('VOLT:TRIG?',peer.rx)
        self.assertFalse(any(c=='*TRG' or c.startswith('INST:COUP') for c in peer.rx))

    def test_query_token_channel_validation(self):
        for bad in (0,4,True,'1',1.0,None):
            with self.subTest(bad=bad),self.assertRaises(ValueError):query_token(bad)

    def test_runner_dry_run_no_usb_or_evidence(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            rc=runner_main(['--config','config/bench.example.toml','--channel','3'])
        self.assertEqual(rc,0)
        self.assertIn('DRY RUN: no USB enumeration',output.getvalue())
        self.assertIn('NO_TRIGGER_EVENT',output.getvalue())

    def test_tamper_scenarios_are_rejected(self):
        status,peer,path=self.run_case(2)
        self.assertEqual(status,'PASS')
        base=[json.loads(line) for line in path.read_text().splitlines()]
        actions={
            'remove_query_gate': lambda r:r.__setitem__(slice(None),[x for x in r
                if not (x['event']=='operator_confirmation' and x.get('gate')=='QUERY')]),
            'wrong_channel': lambda r:next(x for x in r if x['event']=='start').update(channel=3),
            'fake_off': lambda r:next(x for x in r if x['event']=='read' and
                x.get('command')=='CHAN:OUTP?').update(response='1'),
            'missing_final_gate': lambda r:next(x for x in r if x['event']=='outcome').update(
                physical_off_confirmed=False),
            'fabricated_volt': lambda r:next(x for x in r if x['event']=='results')[
                'values'].update(voltage_v=1.999),
            'nonzero_error': lambda r:next(x for x in r if x['event']=='read' and
                x.get('command')=='SYST:ERR?').update(response='120,"Different error"'),
            'forged_trigger_write': lambda r:r.insert(-1,dict(r[-2],event='write',
                command='VOLT:TRIG 2.00')),
            'masked_fw': lambda r:next(x for x in r if x['event']=='psu_identity').update(
                firmware='2.0'),
            'not_reviewed_state': lambda r:next(x for x in r if x['event']=='outcome').update(
                hardware_review='VERIFIED'),
        }
        for name,change in actions.items():
            with self.subTest(case=name):
                records=copy.deepcopy(base)
                change(records)
                with self.assertRaises(AuditError):audit(records)

    def test_jsonl_truncation_is_rejected(self):
        status,peer,path=self.run_case(1)
        self.assertEqual(status,'PASS')
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        with self.assertRaises(AuditError):audit_path(path)


if __name__=='__main__':unittest.main()
