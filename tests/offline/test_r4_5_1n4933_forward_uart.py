"""R4.5-F-D: production driver over PTY, never a physical bench instrument."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from tests.offline.test_r4_5_powered_step_hardware_uart import Evidence

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_1n4933_forward_stage import (VOLTAGES, POWER_TOKEN, FINAL_TOKEN,
    R45FDDiodeScope, run_forward, validate_point, validate_segment, validate_return)


class DiodeOverrides(dict):
    def __init__(self,peer):
        self.peer=peer
        super().__init__({'MEAS:VOLT?': '0', 'MEAS:CURR?': '0'})
    def __getitem__(self,key):
        if key in ('MEAS:VOLT?','MEAS:CURR?'):
            v=float(self.peer.channel_registers[1]['VOLT'])
            # An original bounded test response, not a calibrated diode model.
            dv=({.80:.7962426, .90:.85, 1.00:.88}[round(v,2)]
                if getattr(self.peer,'weak_forward',False) else 0.56+0.30*(v-0.80))
            return str(v if key=='MEAS:VOLT?' else (v-dv)/99.86)
        return super().__getitem__(key)


class Meter:
    def __init__(self,peer,*, bad=False, frozen=False, inject_loss=None):
        self.peer=peer;self.bad=bad;self.frozen=frozen;self.inject_loss=inject_loss
        self.count=0;self.configured=False;self.closed=False
    def configure_voltage(self,range_v):
        if range_v!=10:raise AssertionError('Unexpected range')
        self.configured=True
    def measure(self):
        self.count+=1
        if self.inject_loss and self.count==2:
            self.peer.drop=self.inject_loss
        # First measurement is prior to energizing any PSU output.
        if self.count==1: return {'voltage_v':0.0}
        v=float(self.peer.channel_registers[1]['VOLT'])
        dv=({.80:.7962426, .90:.85, 1.00:.88}[round(v,2)]
            if getattr(self.peer,'weak_forward',False) else
            0.56+0.30*((.80 if self.frozen else v)-.80))
        if getattr(self.peer,'open_diode',False):
            dv=v-0.0034174
        if self.bad: dv=-.4
        return {'voltage_v':dv}
    def close(self):self.closed=True


@unittest.skipUnless(os.name=='posix','PTY test requires POSIX')
class ForwardUART(unittest.TestCase):
    def case(self,*, gate_refusal=None,bad_dmm=False,frozen=False,
             fault_after=None,inject_loss=None,weak_forward=False,open_diode=False):
        peer=UARTPeer(profile_registers=True,fault_after=fault_after)
        peer.weak_forward=weak_forward
        peer.open_diode=open_diode
        self.addCleanup(peer.close)
        peer.overrides=DiodeOverrides(peer)
        transport=open_test_transport(peer,timeout=.16)
        evidence=Evidence();meter=Meter(peer,bad=bad_dmm,frozen=frozen,inject_loss=inject_loss)
        approvals=[]
        def gate(token, description, adapter=None):
            approvals.append(token)
            if token==gate_refusal:raise EOFError('Operator declined')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_forward(transport,meter,evidence,actual_ohm=99.86,gate=gate)
        return outcome,peer,evidence,meter,approvals

    def test_complete_five_point_forward_screening(self):
        result,peer,evidence,meter,gates=self.case()
        self.assertEqual(result,'PASS',evidence.events[-1])
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(peer.rx.count('OUTP 1'),0)
        self.assertEqual([e['preset_v'] for e in evidence.events if e['event']=='diode_iv_point'],list(VOLTAGES))
        self.assertEqual([x for x in peer.rx if x in ('VOLT 0.90','VOLT 1.00','VOLT 0.80')][-4:],
                         ['VOLT 0.90','VOLT 1.00','VOLT 0.90','VOLT 0.80'])
        self.assertEqual(len([x for x in evidence.events if x['event']=='diode_iv_segment']),4)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(gates[-1],FINAL_TOKEN)
        self.assertFalse(peer.unsupported,peer.unsupported)

    def test_weak_0p8_endpoint_requires_verified_growth(self):
        result,peer,evidence,_,_=self.case(weak_forward=True)
        self.assertEqual(result,'PASS',evidence.events[-1])
        points=[x for x in evidence.events if x['event']=='diode_iv_point']
        self.assertEqual(len(points),5)
        self.assertTrue(points[0]['weak_current_unquantified'])
        self.assertTrue(points[4]['weak_current_unquantified'])
        self.assertFalse(points[1]['weak_current_unquantified'])
        self.assertEqual(len([x for x in evidence.events if x['event']=='diode_iv_raw_sample']),5)
        self.assertEqual(len([x for x in evidence.events if x['event']=='diode_iv_low_current_caution']),2)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_open_diode_stops_on_second_point_with_raw_evidence(self):
        result,peer,evidence,_,_=self.case(open_diode=True)
        self.assertEqual(result,'ABORTED')
        self.assertEqual(len([x for x in evidence.events if x['event']=='diode_iv_point']),1)
        self.assertEqual(len([x for x in evidence.events if x['event']=='diode_iv_raw_sample']),2)
        self.assertNotIn('VOLT 1.00',peer.rx[peer.rx.index('CHAN:OUTP 1')+1:])
        self.assertEqual(evidence.events[-1]['physical_off_confirmed'],True)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_weak_minimum_is_limited_to_0p8_endpoints(self):
        reading=SimpleNamespace(enabled=True,regulation='CV',voltage_v=.8,current_a=0)
        with self.assertRaises(SafetyError):
            validate_point(reading,.7962426,.8,99.86)
        p=validate_point(reading,.7962426,.8,99.86,low_current_endpoint=True)
        self.assertTrue(p['weak_current_unquantified'])
        self.assertLess(p['screening_current_a'],.00015)

    def test_refused_power_prevents_enable(self):
        result,peer,_,_,_=self.case(gate_refusal=POWER_TOKEN)
        self.assertEqual(result,'ABORTED')
        self.assertNotIn('CHAN:OUTP 1',peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_invalid_dmm_rejected_without_second_point(self):
        result,peer,_,_,_=self.case(bad_dmm=True)
        self.assertEqual(result,'ABORTED')
        self.assertNotIn('VOLT 0.90',peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_frozen_analog_diode_detected(self):
        result,peer,_,_,_=self.case(frozen=True)
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT 0.90'),1)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_final_refusal_rejects_pass(self):
        result,peer,evidence,_,_=self.case(gate_refusal=FINAL_TOKEN)
        self.assertEqual(result,'ABORTED')
        self.assertFalse(evidence.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_uart_timeout_has_no_scpi_retry_or_off_promise(self):
        result,peer,evidence,_,_=self.case(inject_loss='VOLT?')
        self.assertEqual(result,'ABORTED')
        self.assertEqual(peer.rx[-1],'VOLT?')
        self.assertIn('UNKNOWN',evidence.events[-1]['reason'])

    def test_scpi_error_first_voltage_change_is_terminal(self):
        result, peer, evidence, _, _ = self.case(fault_after='VOLT 0.90')
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(peer.rx.count('VOLT 0.90'), 1)
        self.assertNotIn('VOLT 1.00', peer.rx[peer.rx.index('VOLT 0.90')+1:])
        self.assertIn('OUTP 0', peer.rx)

    def test_no_unsafe_raw_command_reaches_uart(self):
        peer=UARTPeer(profile_registers=True);self.addCleanup(peer.close)
        scope=R45FDDiodeScope(open_test_transport(peer,timeout=.16),Evidence())
        self.addCleanup(scope.close)
        for cmd in ('*RST','OUTP 1','CURR 1.000','VOLT 5.00','CHAN:OUTP 1',
                    'VOLT 0.90','VOLT:UP','SYST:RWLOCK'):
            with self.subTest(cmd=cmd),self.assertRaises((SafetyError, ValueError)):
                scope.write(cmd)
        self.assertEqual(peer.rx,[])

    def test_invalid_measurements(self):
        good=SimpleNamespace(enabled=True,regulation='CV',voltage_v=.9,current_a=.0023)
        p=validate_point(good,.67,.90,99.86)
        self.assertGreater(p['screening_current_a'],0)
        for bad_voltage in (-.5,0,.9,1.7):
            with self.subTest(dmm=bad_voltage),self.assertRaises(SafetyError):
                validate_point(good,bad_voltage,.90,99.86)
        with self.assertRaises(SafetyError):
            validate_point(SimpleNamespace(enabled=True,regulation='CC',voltage_v=.9,current_a=.01),.6,.9,99.86)

    def test_invalid_segments_and_return(self):
        p0=dict(diode_voltage_v=.56,screening_current_a=.002)
        p1=dict(diode_voltage_v=.59,screening_current_a=.0027)
        self.assertEqual(validate_segment(p0,p1,'up')['direction'],'up')
        with self.assertRaises(SafetyError):validate_segment(p1,p0,'up')
        with self.assertRaises(SafetyError):validate_return([p0,p1])

    def test_dry_run_refuses_hardware(self):
        command=[sys.executable,str(ROOT/'tools/r4_5_1n4933_forward_qualify.py'),
          '--config',str(ROOT/'config/bench.example.toml'),
          '--plan',str(ROOT/'docs/examples/R45FD_1N4933_forward_99p86.json')]
        proc=subprocess.run(command,capture_output=True,text=True,timeout=8)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('DRY RUN',proc.stdout)

    def test_noninteractive_execute_rejected(self):
        command=[sys.executable,str(ROOT/'tools/r4_5_1n4933_forward_qualify.py'),
          '--config',str(ROOT/'config/bench.example.toml'),
          '--plan',str(ROOT/'docs/examples/R45FD_1N4933_forward_99p86.json'),
          '--execute']
        proc=subprocess.run(command,capture_output=True,text=True,timeout=8)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('Example bench configuration prohibited',proc.stderr)


if __name__=='__main__':unittest.main()
