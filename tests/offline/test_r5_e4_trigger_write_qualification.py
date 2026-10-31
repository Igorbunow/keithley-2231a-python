"""R5-E4 guarded native trigger-write qualification and offline evidence audit."""
from __future__ import annotations

import contextlib
import copy
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from qualification import Evidence
from r5_e4_trigger_write_stage import (STAGE, PHASE, PRESETS, TriggerWriteScope,
                                        run_trigger_write, stage_token)
from tools.audit_r5_e4_trigger_write import AuditError, audit, audit_path
from tools.r5_e4_trigger_write_qualify import main as runner_main


@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class TriggerWriteQualification(unittest.TestCase):
    def case(self, ch=1, **opts):
        peer = UARTPeer(profile_registers=True, trigger_registers=True, **opts)
        self.addCleanup(peer.close)
        transport = open_test_transport(peer, timeout=.18)
        self.addCleanup(transport.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        file=Path(folder.name)/'evidence.jsonl'
        ev=Evidence(file,STAGE)
        ev.add('start',phase=PHASE,channel=ch,config_sha256='a'*64,
               software={'source_sha256':'b'*64,'r5e4_runner_sha256':'c'*64,
                         'r5e4_stage_sha256':'d'*64})
        ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
               approved_before_evidence_open=True,config_sha256='a'*64)
        before={k:dict(v) for k,v in peer.channel_registers.items()}
        with contextlib.redirect_stdout(StringIO()):
            outcome=run_trigger_write(transport,ev,channel=ch,
                profile_gate=lambda *a:None,stage_gate=lambda *a:None,
                final_gate=lambda *a:None)
        return outcome,peer,file,before

    def test_all_three_channels_real_driver_virtual_uart_and_original_jsonl(self):
        for ch in (1,2,3):
            with self.subTest(channel=ch):
                result,peer,file,original=self.case(ch)
                self.assertEqual(result,'PASS')
                a=audit_path(file)
                self.assertEqual(a['channel'],ch)
                self.assertEqual(a['status'],'REVIEWABLE_PASS_OFF_ONLY_NOT_TRIGGER_ACTUATION')
                self.assertAlmostEqual(a['staged']['voltage_v'],PRESETS[ch][0])
                self.assertEqual({c:{k:v for k,v in d.items() if ':TRIG' in k}
                                  for c,d in peer.channel_registers.items()},
                                 {c:{k:v for k,v in d.items() if ':TRIG' in k}
                                  for c,d in original.items()})
                self.assertEqual(peer.rx.count('VOLT:TRIG?'),5)
                self.assertEqual(peer.rx.count('CURR:TRIG?'),5)
                self.assertEqual(len([c for c in peer.rx if c.startswith('VOLT:TRIG ')]),2)
                self.assertEqual(len([c for c in peer.rx if c.startswith('CURR:TRIG ')]),2)
                self.assertFalse(any(x in ('*TRG','TRIG','OUTP 1','CHAN:OUTP 1') or
                                     x.startswith('INST:COUP') for x in peer.rx))
                self.assertEqual(peer.unsupported,[])

    def test_scpi_error_after_first_stage_write_aborts_no_second_write(self):
        result,peer,file,_=self.case(1,fault_after='VOLT:TRIG 2.200')
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:TRIG 2.200'),1)
        self.assertNotIn('CURR:TRIG 0.120',peer.rx)
        with self.assertRaises(AuditError):audit_path(file)

    def test_invalid_first_read_aborts_without_staging(self):
        result,peer,file,_=self.case(2,response_overrides={'VOLT:TRIG?':'BROKEN'})
        self.assertEqual(result,'ABORTED')
        self.assertFalse(any(c.startswith(('VOLT:TRIG ','CURR:TRIG ')) for c in peer.rx))
        with self.assertRaises(AuditError):audit_path(file)

    def test_lost_uart_no_trigger_retry_and_audit_rejected(self):
        result,peer,file,_=self.case(3,drop='CURR:TRIG?')
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx.count('CURR:TRIG?'),1)
        with self.assertRaises(AuditError):audit_path(file)

    def test_scope_denies_power_trigger_event_and_cross_channel(self):
        peer=UARTPeer(profile_registers=True,trigger_registers=True)
        self.addCleanup(peer.close)
        scope=TriggerWriteScope(open_test_transport(peer,timeout=.12),
                                type('E',(),{'add':lambda *a,**k:{}})(),2)
        self.addCleanup(scope.close)
        for cmd in ('*TRG','TRIG','OUTP 1','CHAN:OUTP 1','INST:COUP CH1,CH2',
                    'VOLT:TRIG 0.800','CURR:TRIG 0.080'):
            with self.subTest(cmd=cmd),self.assertRaises(SafetyError):scope.write(cmd)
        with self.assertRaises(SafetyError):scope.query('VOLT:TRIG?')
        self.assertFalse(peer.rx)

    def test_auditor_rejects_fabricated_evidence(self):
        result,peer,file,_=self.case(2)
        self.assertEqual(result,'PASS')
        base=[json.loads(line) for line in file.read_text().splitlines()]
        def event(r,name):return next(x for x in r if x['event']==name)
        mutators={
            'different_channel':lambda r:event(r,'start').update(channel=1),
            'false_restore':lambda r:event(r,'results')['values']['restored'].update(voltage_v=.9),
            'fake_off':lambda r:next(x for x in r if x['event']=='read' and
                 x.get('command')=='CHAN:OUTP?').update(response='1'),
            'remove_stage_gate':lambda r:r.__setitem__(slice(None),[x for x in r if not
                (x['event']=='operator_confirmation' and x.get('gate')=='STAGE')]),
            'missing_final':lambda r:event(r,'outcome').update(physical_off_confirmed=False),
            'masked_error':lambda r:next(x for x in r if x['event']=='read' and
                 x.get('command')=='SYST:ERR?').update(response='130,"SCPI Fault"'),
            'forged_power_write':lambda r:r.insert(-1,dict(r[-2],event='write',command='OUTP 1')),
            'fake_trace':lambda r:next(x for x in r if x['event']=='driver_trace' and
                x['trace']['event']=='write' and x['trace']['command'].startswith('VOLT:TRIG '))
                ['trace'].update(command='VOLT:TRIG 1.200'),
            'fake_firmware':lambda r:event(r,'psu_identity').update(firmware='9.99'),
            'missing_results':lambda r:r.remove(event(r,'results')),
        }
        for name,mutation in mutators.items():
            with self.subTest(name=name):
                records=copy.deepcopy(base)
                mutation(records)
                with self.assertRaises(AuditError):audit(records)

    def test_truncation_rejected(self):
        status,peer,file,_=self.case(1)
        self.assertEqual(status,'PASS')
        file.write_bytes(file.read_bytes().rstrip(b'\n'))
        with self.assertRaises(AuditError):audit_path(file)

    def test_no_hardware_on_dry_run(self):
        with contextlib.redirect_stdout(StringIO()) as buf:
            result=runner_main(['--config','config/bench.example.toml','--channel','3'])
        self.assertEqual(result,0)
        self.assertIn('DRY RUN: no USB enumeration',buf.getvalue())
        self.assertIn('THEN_RESTORE_ORIGINAL',buf.getvalue())

    def test_bad_channel_rejected(self):
        for invalid in (0,4,'1',None,True,1.0):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                stage_token(invalid)


if __name__=='__main__':unittest.main()
