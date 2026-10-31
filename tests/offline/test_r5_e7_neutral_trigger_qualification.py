"""R5-E7 guarded neutral OFF event via production driver and original JSONL."""
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
from r5_e7_neutral_trigger_stage import (STAGE,PHASE,NeutralTriggerScope,
                                         run_neutral_trigger)
from tools.audit_r5_e7_neutral_trigger import audit, audit_path, AuditError
from tools.r5_e7_neutral_trigger_qualify import main as runner_main


@unittest.skipUnless(os.name=='posix','POSIX PTY required')
class NeutralTriggerHardwareCandidate(unittest.TestCase):
    def case(self, *, responders=(1,2,3), **opts):
        peer=UARTPeer(profile_registers=True,trigger_registers=True,
                      trigger_bus_channels=responders,**opts)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.16)
        self.addCleanup(wire.close)
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        file=Path(folder.name)/'events.jsonl'
        evidence=Evidence(file,STAGE)
        evidence.add('start',phase=PHASE,config_sha256='a'*64,
            software={'source_sha256':'b'*64,'r5e7_runner_sha256':'c'*64,
                      'r5e7_stage_sha256':'d'*64})
        evidence.add('pre_open_approvals',gates=['BENCH','INITIAL'],
                     approved_before_evidence_open=True,config_sha256='a'*64)
        original={ch:{name:value for name,value in fields.items()}
                  for ch,fields in peer.channel_registers.items()}
        with contextlib.redirect_stdout(StringIO()):
            outcome=run_neutral_trigger(wire,evidence,
                profile_gate=lambda *a:None,
                event_gate=lambda *a:None,
                final_gate=lambda *a:None)
        return outcome,peer,file,original

    def test_three_possible_groups_one_trigger_and_restoration(self):
        for responders in ((1,),(2,),(3,),(1,2,3),(1,3)):
            with self.subTest(responders=responders):
                outcome,peer,path,original=self.case(responders=responders)
                self.assertEqual(outcome,'PASS')
                audited=audit_path(path)
                self.assertEqual(audited['status'],
                                 'REVIEWABLE_PASS_NEUTRAL_OFF_ONLY_NOT_ACTUATION')
                self.assertEqual(audited['channels'],[1,2,3])
                self.assertEqual(peer.rx.count('*TRG'),1)
                self.assertEqual(peer.unsupported,[])
                self.assertFalse(any(peer.off_by_channel.values()))
                self.assertFalse(any(c in ('OUTP 1','CHAN:OUTP 1') or
                    c.startswith('INST:COUP') for c in peer.rx))
                for ch in (1,2,3):
                    for cmd in ('VOLT:TRIG','CURR:TRIG'):
                        self.assertEqual(peer.channel_registers[ch][cmd],
                                         original[ch][cmd])

    def test_scpi_fault_after_single_trigger_no_repeat(self):
        status,peer,path,_=self.case(fault_after='*TRG')
        self.assertEqual(status,'ABORTED')
        self.assertEqual(peer.rx.count('*TRG'),1)
        with self.assertRaises(AuditError):audit_path(path)

    def test_uncertain_uart_after_trigger_no_retry(self):
        status,peer,path,_=self.case(drop_error_after_trigger=True)
        self.assertEqual(status,'ABORTED')
        self.assertEqual(peer.rx.count('*TRG'),1)
        with self.assertRaises(AuditError):audit_path(path)

    def test_invalid_original_register_aborts_before_event(self):
        status,peer,path,_=self.case(response_overrides={'VOLT:TRIG?':'INVALID'})
        self.assertEqual(status,'ABORTED')
        self.assertNotIn('*TRG',peer.rx)
        with self.assertRaises(AuditError):audit_path(path)

    def test_scope_never_permits_ungated_trigger_or_power(self):
        peer=UARTPeer(profile_registers=True,trigger_registers=True,
                      trigger_bus_channels=(1,2,3))
        self.addCleanup(peer.close)
        scope=NeutralTriggerScope(open_test_transport(peer,timeout=.15),
            type('E',(),{'add':lambda *a,**kw:{}})())
        self.addCleanup(scope.close)
        for cmd in ('*TRG','OUTP 1','CHAN:OUTP 1','INST:COUP CH1,CH2',
                    'VOLT:TRIG 1.000','CURR:TRIG 0.050'):
            with self.subTest(cmd=cmd),self.assertRaises(SafetyError):
                scope.write(cmd)
        self.assertFalse(peer.rx)

    def test_audit_rejects_forged_original_stream(self):
        status,peer,path,_=self.case()
        self.assertEqual(status,'PASS')
        baseline=[json.loads(x) for x in path.read_text().splitlines()]
        def find(r,event):return next(x for x in r if x['event']==event)
        mutations={
            'false_pass':lambda r:find(r,'outcome').update(physical_off_confirmed=False),
            'wrong_event_count':lambda r:find(r,'results')['values'].update(trigger_count=2),
            'masked_error':lambda r:next(x for x in r if x['event']=='read' and
                x.get('command')=='SYST:ERR?').update(response='130,"Fault"'),
            'masked_off':lambda r:next(x for x in r if x['event']=='read' and
                x.get('command')=='CHAN:OUTP?').update(response='1'),
            'delete_trigger':lambda r:r.remove(next(x for x in r if
                x['event']=='write' and x.get('command')=='*TRG')),
            'extra_trigger':lambda r:r.insert(-1,dict(next(x for x in r if
                x['event']=='write' and x.get('command')=='*TRG'))),
            'fake_output_on':lambda r:r.insert(-1,dict(r[-2],event='write',command='OUTP 1')),
            'delete_event_gate':lambda r:r.remove(next(x for x in r if
                x['event']=='operator_confirmation' and x.get('gate')=='EVENT')),
            'wrong_stage':lambda r:find(r,'start').update(phase='DIFFERENT'),
            'fake_restoration':lambda r:find(r,'results')['values']['restored']['3'].update(voltage_v=.9),
            'missing_off':lambda r:find(r,'outcome').update(digital_off_confirmed=False),
            'fake_coupling':lambda r:find(r,'neutral_bus_event').update(coupling='CH1'),
            'fake_wire_trace':lambda r:next(x for x in r if x['event']=='driver_trace'
                and x['trace'].get('command')=='*TRG')['trace'].update(command='*RST'),
        }
        for label,mutate in mutations.items():
            with self.subTest(label=label):
                records=copy.deepcopy(baseline)
                mutate(records)
                with self.assertRaises(AuditError):audit(records)

    def test_truncated_jsonl_rejected(self):
        status,peer,path,_=self.case()
        self.assertEqual(status,'PASS')
        path.write_bytes(path.read_bytes().rstrip(b'\n'))
        with self.assertRaises(AuditError):audit_path(path)

    def test_runner_dry_run_does_not_open_devices(self):
        with contextlib.redirect_stdout(StringIO()) as output:
            code=runner_main(['--config','config/bench.example.toml'])
        self.assertEqual(code,0)
        self.assertIn('DRY RUN: no USB enumeration',output.getvalue())
        self.assertIn('COUPLING_UNKNOWN',output.getvalue())

if __name__=='__main__':unittest.main()
