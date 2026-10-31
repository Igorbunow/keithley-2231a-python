"""Offline-only R5-D2 step guard, dry-run and independent evidence audit tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from r5_d2_powered_step_stage import (R5D2Scope, DIRECTIONS,
                                     validate_case, profile_for, check_transition)

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT/'tools'
sys.path.insert(0, str(TOOLS))
import audit_r5_d2_step_powered as audit_tool


class PowerScopeTests(unittest.TestCase):
    def mk(self, channel=2, mode='cv'):
        return R5D2Scope(None, None, channel=channel, mode=mode)

    def test_profile_selects_each_channel_without_spurious_ch1_cap(self):
        for ch in (1,2,3):
            for mode in ('cv','cc'):
                with self.subTest(channel=ch, mode=mode):
                    p = profile_for(ch,mode)
                    self.assertEqual(p.channels[ch-1].initial_voltage,3.30)
                    self.assertEqual(p.channels[ch-1].max_current,.150)
                    for other in (1,2,3):
                        if other != ch:
                            self.assertEqual(p.channels[other-1].initial_voltage,1.00)

    def test_reject_wrong_channel_or_ungated_power(self):
        for channel in (1,2,3):
            io = self.mk(channel)
            io.selected = 1 if channel != 1 else 2
            io.enable_authorized = True
            with self.assertRaises(Exception): io._validate('CHAN:OUTP 1', False)
            self.assertEqual(io.power_on_commands,0)
            io.selected = channel
            with self.assertRaises(Exception): io._validate('VOLT:STEP 0.010',False)
            with self.assertRaises(Exception): io._validate('VOLT:UP',False)
            io.outputs[channel] = True
            with self.assertRaises(Exception): io._validate('SYST:LOC',False)

    def test_four_moves_only_selected_channel_cv_cc(self):
        for channel in (1,2,3):
            for mode,prefix,step in (('cv','VOLT','0.010'),('cc','CURR','0.005')):
                with self.subTest(channel=channel,mode=mode):
                    io = self.mk(channel,mode)
                    io.selected = channel
                    io.step_window_authorized = True
                    io._validate(f'{prefix}:STEP {step}',False)
                    io.enable_authorized = True
                    io._validate('CHAN:OUTP 1',False)
                    io.outputs[channel] = True
                    for direction in DIRECTIONS:
                        io._validate(f'{prefix}:{direction.upper()}',False)
                    self.assertEqual(len(io.moves),4)
                    with self.assertRaises(Exception): io._validate(f'{prefix}:UP',False)
                    with self.assertRaises(Exception): io._validate(f'{prefix}:STEP {step}',False)
                    with self.assertRaises(Exception): io._validate('CHAN:OUTP 1',False)
                    io.outputs[channel] = False
                    io.step_window_authorized = False
                    io._validate('SYST:LOC',False)

    def test_wrong_regulation_and_mode_rejected(self):
        with self.assertRaises(ValueError): validate_case(4,'cv')
        with self.assertRaises(ValueError): validate_case(2,'sweep')
        io = self.mk(2,'cv')
        io.selected = 2
        io.step_window_authorized = True
        with self.assertRaises(Exception): io._validate('CURR:STEP 0.005',False)
        with self.assertRaises(Exception): io._validate('OUTP 1',False)

    def test_external_delta_direction_fails_closed(self):
        for mode in ('cv','cc'):
            prior = {'dmm_v':3.30,'kel_a':.10}
            current = {'dmm_v':3.30,'kel_a':.10}
            with self.assertRaises(Exception): check_transition(prior,current,mode,'up')


class RunnerDryRun(unittest.TestCase):
    def test_default_never_opens_instruments(self):
        runner=ROOT/'tools/r5_d2_step_powered_qualify.py'
        for ch,mode in ((1,'cv'),(2,'cv'),(3,'cc')):
            result=subprocess.run([sys.executable,str(runner),'--channel',str(ch),'--mode',mode],
                                  cwd=ROOT, capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('DRY RUN: no USB enumeration',result.stdout)
            self.assertIn(f'CH{ch}',result.stdout)

    def test_example_hardware_execution_rejected_before_open(self):
        runner=ROOT/'tools/r5_d2_step_powered_qualify.py'
        result=subprocess.run([sys.executable,str(runner),'--channel','2','--mode','cv',
                               '--execute'],cwd=ROOT,capture_output=True,text=True,timeout=12)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Example bench may not be used',result.stderr)


class IndependentAudit(unittest.TestCase):
    @staticmethod
    def synthetic(mode='cv',ch=2):
        """Explicitly synthetic event stream, never presented as hardware evidence."""
        events=[]
        def add(event,**values):
            events.append(dict(test_id=audit_tool.STAGE,event=event,
                               monotonic_s=float(len(events)),**values))
        def write(command):
            add('write',command=command)
            add('driver_trace',trace={'event':'write','command':command})
        def query(command,answer):
            add('query',command=command)
            add('driver_trace',trace={'event':'query','command':command})
            add('read',command=command,response=answer)
            add('driver_trace',trace={'event':'reply','command':command,'response':answer})
        p={'step':.010 if mode=='cv' else .005,'cr':40.0 if mode=='cv' else 11.0,
           'limit':.150 if mode=='cv' else .100}
        add('start',phase='POWERED_STEP_FOUR',channel=ch,mode=mode,point=p,
            config_sha256='a'*64,directions=list(DIRECTIONS),software={
                'source_sha256':'b'*64,'r5d2_runner_sha256':'c'*64,
                'r5d2_stage_sha256':'d'*64})
        add('pre_open_approvals',gates=['BENCH','INITIAL'],
            approved_before_evidence_open=True,config_sha256='a'*64)
        add('psu_identity',model='2231A-30-3',firmware='1.04-1.04')
        add('dmm7510_configuration',function='DC voltage',terminals='front',range_v=10,
            outcome='PASS')
        query('*IDN?', 'Keithley,2231A-30-3,[REDACTED],1.04-1.04')
        for j in range(102): query('SYST:ERR?','0,No error')
        write(f'INST:NSEL {ch}')
        prefix='VOLT' if mode=='cv' else 'CURR'
        write(f'{prefix}:STEP {p["step"]:.3f}')
        for k in range(5): query(f'{prefix}:STEP?',f'{p["step"]:.3f}')
        add('operator_confirmation',gate='ENABLE',channel=ch,mode=mode)
        write('CHAN:OUTP 1')
        offsets=[0,1,2,1,0]
        v=[]
        for i,off in enumerate(offsets):
            if i:
                d=DIRECTIONS[i-1]
                write(f'{prefix}:{d.upper()}')
                setpoint=(3.30 if mode=='cv' else p['limit'])+off*p['step']
                query(f'{prefix}?', f'{setpoint:.3f}')
                add('powered_step_digital',index=i,channel=ch,mode=mode,
                    direction=d,setpoint=setpoint)
            if mode=='cv':
                du=3.30+off*.010
                ki=du/40.
            else:
                ki=.100+off*.005
                du=11.*ki
            values={'channel':ch,'mode':mode.upper(),'index':i,'offset_steps':off,
                    'synchronized':False,'psu_v':du,'psu_a':ki,'kel_v':du,
                    'kel_a':ki,'kel_w':du*ki,'dmm_v':du,'psu_meas_w':du*ki}
            for command,value in ((':MEAS:VOLT?',du),(':MEAS:CURR?',ki),(':MEAS:POW?',du*ki)):
                add('kel103_read',command=command,response=str(value))
            add('dmm7510_read',command=':READ?',response=str(du))
            add('powered_step_snapshot',**values)
            if i:
                add('powered_step_delta',index=i,channel=ch,mode=mode,
                    direction=DIRECTIONS[i-1],dmm_delta_v=du-v[-1][0],kel_delta_a=ki-v[-1][1])
            v.append((du,ki))
        write('OUTP 0')
        for other in (1,2,3):
            write(f'INST:NSEL {other}')
            query('CHAN:OUTP?', '0')
        write('SYST:LOC')
        add('operator_confirmation',gate='FINAL')
        add('outcome',phase='POWERED_STEP_FOUR',channel=ch,mode=mode,
            outcome='PASS',digital_off_confirmed=True,
            physical_off_confirmed=True,hardware_review='PENDING')
        return events

    def test_synthetic_fixture_validates_all_channel_mode_variants(self):
        for ch in (1,2,3):
            for mode in ('cv','cc'):
                with self.subTest(ch=ch,mode=mode):
                    self.assertEqual(audit_tool.audit(self.synthetic(mode,ch))['mutations'],4)

    def test_missing_off_wrong_channel_and_forged_external_delta_rejected(self):
        for change in ('off','channel','delta','duplicate','scpi'):
            data=self.synthetic()
            if change=='off': data[-1]['physical_off_confirmed']=False
            elif change=='channel':
                next(r for r in data if r.get('event')=='powered_step_digital')['channel']=3
            elif change=='delta':
                next(r for r in data if r.get('event')=='powered_step_snapshot' and r['index']==2)['dmm_v']+=.06
            elif change=='duplicate':
                data.insert(-1,copy.deepcopy(next(r for r in data if r.get('event')=='powered_step_delta')))
            else:
                next(r for r in data if r.get('event')=='read' and r['command']=='SYST:ERR?')['response']='120,Fault'
            # Re-number timestamps so we validate actual evidence failure, not fixture clocks.
            for i,r in enumerate(data): r['monotonic_s']=float(i)
            with self.subTest(change=change),self.assertRaises(audit_tool.AuditError):
                audit_tool.audit(data)


if __name__=='__main__':
    unittest.main()

# Full production driver -> POSIX pseudo UART -> independent synthetic analog
# responders. These tests exercise real SCPI dispatch; they are NOT physical tests.
import contextlib
import io
import os
from support.virtual_uart import UARTPeer, open_test_transport
from r5_d2_powered_step_stage import run_case, FINAL_TOKEN


class _Events:
    def __init__(self): self.events=[]
    def add(self, event, **values):
        if event=='read' and values.get('command')=='*IDN?':
            parts=values['response'].split(',')
            if len(parts)==4:
                parts[2]=' [REDACTED]'
                values['response']=','.join(parts)
        r = {'event':event, 'test_id':audit_tool.STAGE,
             'monotonic_s':float(len(self.events)), **values}
        self.events.append(r); return r


class _Load:
    live=True
    def __init__(self,peer,ch,mode,*,frozen=False,evidence=None):
        self.peer,self.ch,self.mode,self.frozen=peer,ch,mode,frozen
        self.input='OFF'; self.cr=None; self.closed=False; self.evidence=evidence
    def identify(self): return 'KEL103, simulated'
    def input_state(self): return self.input
    def configure_cr(self,cr): self.cr=cr
    def set_input(self,flag): self.input='ON' if flag else 'OFF'
    def query(self,c):
        if c==':FUNC?': return 'CR'
        if c==':RES?': return f'{self.cr:.3f}OHM'
        raise ValueError(c)
    def values(self):
        if self.mode=='cv':
            v=float(self.peer.channel_registers[self.ch]['VOLT'])
            if self.frozen: v=3.30
            i=v/self.cr
        else:
            i=float(self.peer.channel_registers[self.ch]['CURR'])
            if self.frozen: i=.100
            v=i*self.cr
        return v,i
    def measurements(self):
        v,i=self.values()
        if self.evidence is not None:
            for cmd,val,unit in ((':MEAS:VOLT?',v,'V'),(':MEAS:CURR?',i,'A'),
                                 (':MEAS:POW?',v*i,'W')):
                self.evidence.add('kel103_read',command=cmd,response=f'{val:.9f}{unit}')
        return {'kel103_voltage_v':v,'kel103_current_a':i,'kel103_power_w':v*i}
    def close(self): self.closed=True


class _Meter:
    live=True
    def __init__(self,load,evidence=None):
        self.load=load; self.closed=False; self.evidence=evidence
    def configure_voltage(self,n):
        if n!=10: raise ValueError('DMM range')
        if self.evidence is not None:
            self.evidence.add('dmm7510_configuration',function='DC voltage',range_v=10,
                              terminals='front',outcome='PASS')
    def measure(self):
        v=self.load.values()[0]
        if self.evidence is not None:
            self.evidence.add('dmm7510_read',command=':READ?',response=str(v))
        return {'voltage_v':v}
    def close(self): self.closed=True


@unittest.skipUnless(os.name=='posix','POSIX UART required')
class HardwareRunnerVirtualUART(unittest.TestCase):
    def case(self,ch,mode,*,frozen=False,reject_final=False,drop=None,audit_ready=False):
        overrides=({'MEAS:VOLT?':'3.300','MEAS:CURR?':'.0825','MEAS:POW?':'.27225'}
                   if mode=='cv' else {'MEAS:VOLT?':'1.140','MEAS:CURR?':'.100',
                     'MEAS:POW?':'.125',f'STAT:OPER:INST:ISUM{ch}:COND?':'2'})
        peer=UARTPeer(profile_registers=True,response_overrides=overrides,drop=drop)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.75)
        ev=_Events(); load=_Load(peer,ch,mode,frozen=frozen,
                                evidence=ev if audit_ready else None)
        meter=_Meter(load,evidence=ev if audit_ready else None)
        if audit_ready:
            p={'step':.010 if mode=='cv' else .005,
               'cr':40.0 if mode=='cv' else 11.0,
               'limit':.150 if mode=='cv' else .100}
            ev.add('start',phase='POWERED_STEP_FOUR',channel=ch,mode=mode,point=p,
                   config_sha256='a'*64,directions=list(DIRECTIONS),software={
                       'source_sha256':'b'*64,'r5d2_runner_sha256':'c'*64,
                       'r5d2_stage_sha256':'d'*64})
            ev.add('pre_open_approvals',gates=['BENCH','INITIAL'],
                   approved_before_evidence_open=True,config_sha256='a'*64)
        approvals=[]
        def confirm(token,prompt,adapter=None):
            approvals.append(token)
            if token==FINAL_TOKEN and reject_final:
                raise EOFError('final refusal')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_case(wire,load,meter,ev,channel=ch,mode=mode,gate=confirm)
        return outcome,peer,ev,load,approvals

    def test_all_six_virtual_channel_modes_four_mutations(self):
        for ch in (1,2,3):
            for mode in ('cv','cc'):
                with self.subTest(channel=ch,mode=mode):
                    result,peer,ev,load,gates=self.case(ch,mode)
                    self.assertEqual(result,'PASS',ev.events[-1])
                    prefix='VOLT' if mode=='cv' else 'CURR'
                    self.assertEqual(peer.rx.count(prefix+':UP'),2)
                    self.assertEqual(peer.rx.count(prefix+':DOWN'),2)
                    self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
                    self.assertEqual(len([x for x in ev.events
                                          if x['event']=='powered_step_snapshot']),5)
                    self.assertFalse(any(peer.off_by_channel.values()))
                    self.assertEqual(load.input,'OFF')
                    self.assertFalse(peer.unsupported,peer.unsupported)
                    self.assertEqual(gates[-1],FINAL_TOKEN)

    def test_real_driver_virtual_uart_evidence_is_independently_auditable(self):
        for channel,mode in ((2,'cv'),(3,'cc')):
            with self.subTest(channel=channel,mode=mode):
                result,peer,ev,load,gates=self.case(channel,mode,audit_ready=True)
                self.assertEqual(result,'PASS',ev.events[-1])
                try:
                    review=audit_tool.audit(ev.events)
                except audit_tool.AuditError as exc:
                    self.fail(f'Auditor rejected real virtual-UART trace: {exc}')
                self.assertEqual(review['status'],'REVIEWABLE_PASS_NOT_AUTOMATIC_QUALIFICATION')

    def test_frozen_analog_refuses_second_step_and_shutdown(self):
        result,peer,ev,load,_=self.case(2,'cv',frozen=True)
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')

    def test_final_gate_refusal_never_pass(self):
        result,peer,ev,load,_=self.case(3,'cc',reject_final=True)
        self.assertEqual(result,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')

    def test_uncertain_uart_aborts_without_step_retry(self):
        result,peer,ev,load,_=self.case(2,'cv',drop='VOLT:STEP?')
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')
        self.assertFalse(ev.events[-1]['digital_off_confirmed'])
