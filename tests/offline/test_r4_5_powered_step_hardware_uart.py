"""R4.5-E-B independent analog peers + real production PSU driver over PTY."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_powered_step_stage import (R45EPoweredScope, run_powered_step,
                                      POINTS, FINAL_TOKEN, power_token)


class Evidence:
    def __init__(self): self.events=[]
    def add(self, event, **values):
        item = {'event':event, **values}; self.events.append(item); return item


class Load:
    live = True
    def __init__(self, peer, mode, *, unchanged=False, high_power=False):
        self.peer=peer; self.mode=mode; self.cr=None; self.input='OFF'; self.closed=False
        self.unchanged=unchanged; self.high_power=high_power
    def identify(self): return 'KEL103, simulated'
    def input_state(self): return self.input
    def configure_cr(self, resistance):
        if self.input!='OFF' or resistance!=POINTS[self.mode]['cr']:
            raise SafetyError('unapproved CR point')
        self.cr=resistance
    def set_input(self, enabled):
        if enabled and self.cr is None: raise SafetyError('unconfigured load')
        self.input='ON' if enabled else 'OFF'
    def query(self, command):
        if command==':FUNC?': return 'CR'
        if command==':RES?': return f'{self.cr:.3f}OHM'
        raise ValueError(command)
    def _values(self):
        if self.mode=='cv':
            v=float(self.peer.channel_registers[1]['VOLT'])
            if self.unchanged: v=3.30
            i=v/self.cr
        else:
            i=float(self.peer.channel_registers[1]['CURR'])
            if self.unchanged: i=.100
            v=self.cr*i
        return v, i
    def measurements(self):
        v, i=self._values()
        if self.high_power: return dict(kel103_voltage_v=4., kel103_current_a=.2,
                                        kel103_power_w=.8)
        return dict(kel103_voltage_v=v, kel103_current_a=i, kel103_power_w=v*i)
    def close(self): self.closed=True


class Meter:
    live=True
    def __init__(self, load, *, wrong=False, drop_after_first=None):
        self.load=load; self.configured=False; self.wrong=wrong; self.closed=False
        self.drop_after_first=drop_after_first; self.count=0
    def configure_voltage(self, value):
        if value!=10: raise ValueError('DMM range')
        self.configured=True
    def measure(self):
        if not self.configured: raise ValueError('DMM not configured')
        v = 3.8 if self.wrong else self.load._values()[0]
        self.count += 1
        if self.count == 1 and self.drop_after_first is not None:
            self.load.peer.drop = self.drop_after_first
        return {'voltage_v': v}
    def close(self): self.closed=True


@unittest.skipUnless(os.name=='posix', 'POSIX UART peer required')
class PoweredStepRunnerUART(unittest.TestCase):
    def case(self, mode='cv', *, fault_after=None, drop=None, gate=None,
             unchanged=False, wrong_dmm=False, high_power=False, drop_after_first=None):
        overrides=({'MEAS:VOLT?':'3.300', 'MEAS:CURR?':'.0825',
                    'MEAS:POW?':'.27225'} if mode=='cv' else
                   {'MEAS:VOLT?':'1.100', 'MEAS:CURR?':'.100',
                    'MEAS:POW?':'.110', 'STAT:OPER:INST:ISUM1:COND?':'2'})
        peer=UARTPeer(profile_registers=True, response_overrides=overrides,
                      fault_after=fault_after, drop=drop)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.16)
        ev=Evidence(); load=Load(peer,mode,unchanged=unchanged,high_power=high_power)
        meter=Meter(load,wrong=wrong_dmm,drop_after_first=drop_after_first); approvals=[]
        def confirm(token, prompt, io=None):
            approvals.append(token)
            if gate is not None: gate(token)
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_powered_step(wire,load,meter,ev,mode=mode,gate=confirm)
        return outcome,peer,ev,load,meter,approvals

    def test_cv_10mv_dmm_measures_real_increment(self):
        outcome,peer,ev,load,meter,gates=self.case()
        self.assertEqual(outcome,'PASS',ev.events[-1])
        self.assertEqual(peer.rx.count('VOLT:STEP 0.010'),1)
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(peer.rx.count('OUTP 1'),0)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')
        self.assertEqual(gates[-1],FINAL_TOKEN)
        self.assertEqual([e['label'] for e in ev.events if e['event']=='powered_step_snapshot'],
                         ['before','after'])
        delta=next(e for e in ev.events if e['event']=='powered_step_delta')
        self.assertAlmostEqual(delta['dmm_voltage_delta_v'],.010)
        self.assertFalse(peer.unsupported,peer.unsupported)

    def test_cc_5ma_kel_and_dmm_measure_real_increment(self):
        outcome,peer,ev,load,meter,gates=self.case('cc')
        self.assertEqual(outcome,'PASS',ev.events[-1])
        self.assertEqual(peer.rx.count('CURR:STEP 0.005'),1)
        self.assertEqual(peer.rx.count('CURR:UP'),1)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(load.input,'OFF')
        delta=next(e for e in ev.events if e['event']=='powered_step_delta')
        self.assertAlmostEqual(delta['kel_current_delta_a'],.005)
        self.assertAlmostEqual(delta['dmm_voltage_delta_v'],.055)
        self.assertFalse(peer.unsupported,peer.unsupported)

    def test_static_analog_readings_do_not_make_pass(self):
        outcome,peer,ev,load,_,_=self.case(unchanged=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertIn('DMM failed to confirm',ev.events[-1]['reason'])
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')

    def test_wrong_dmm_blocks_step(self):
        outcome,peer,ev,load,_,_=self.case(wrong_dmm=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')
        self.assertIn('OUTP 0',peer.rx)

    def test_wrong_load_power_blocks_step(self):
        outcome,peer,ev,load,_,_=self.case(high_power=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')

    def test_declined_power_gate_prevents_on(self):
        def reject(token):
            if token==power_token('cv'): raise EOFError('operator veto')
        outcome,peer,ev,load,_,_=self.case(gate=reject)
        self.assertEqual(outcome,'ABORTED')
        self.assertNotIn('CHAN:OUTP 1',peer.rx)
        self.assertEqual(load.input,'OFF')
        self.assertEqual(peer.rx.count('VOLT:STEP 0.010'),0)

    def test_rejected_step_command_no_retry(self):
        outcome,peer,ev,load,_,_=self.case(fault_after='VOLT:UP')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertEqual(load.input,'OFF')
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_lost_uart_during_pre_step_probe_no_retry_keluaran_off(self):
        outcome,peer,ev,load,_,_=self.case(drop_after_first='VOLT:STEP?')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(peer.rx[-1],'VOLT:STEP?')
        self.assertEqual(load.input,'OFF')
        self.assertIn('manual OFF',ev.events[-1]['reason'])

    def test_cc_no_physical_change_rejected_even_if_digital_up_passes(self):
        outcome,peer,ev,load,_,_=self.case('cc',unchanged=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('CURR:UP'),1)
        self.assertIn('KEL failed to confirm',ev.events[-1]['reason'])
        self.assertEqual(load.input,'OFF')

    def test_rejected_final_operator_gate_prevents_pass(self):
        def reject(token):
            if token==FINAL_TOKEN: raise EOFError('final gate refused')
        outcome,peer,ev,load,_,_=self.case(gate=reject)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')

    def test_unexpected_cc_regulation_blocks_mutation(self):
        # CV UART state is not acceptable for an authorized CC mutation.
        outcome,peer,ev,load,_,_=self.case('cc')
        self.assertEqual(outcome,'PASS')  # baseline proves explicit CC firmware fixture
        self.assertNotIn('VOLT:UP',peer.rx)

    def test_scope_rejects_out_of_mode_step_and_unapproved_enable(self):
        peer=UARTPeer(profile_registers=True); self.addCleanup(peer.close)
        scope=R45EPoweredScope(open_test_transport(peer,timeout=.12),Evidence(),mode='cv')
        self.addCleanup(scope.close)
        for command in ('*RST','OUTP 1','CHAN:OUTP 1','VOLT:UP',
                        'CURR:STEP 0.005','CURR:UP','VOLT:DOWN'):
            with self.subTest(command=command), self.assertRaises((SafetyError,ValueError)):
                scope.write(command)
        self.assertEqual(peer.rx,[])

    def test_cli_no_execute_does_not_open_hardware(self):
        process=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_powered_step_qualify.py'),
                                 '--config',str(ROOT/'config/bench.example.toml'),
                                 '--mode','cv'], capture_output=True,text=True,timeout=12)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertIn('DRY RUN',process.stdout)
        self.assertNotIn('START GATE',process.stdout)

if __name__=='__main__': unittest.main()
