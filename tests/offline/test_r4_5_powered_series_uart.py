"""R4.5-E-C: real production CH1 driver over virtual UART; KEL/DMM isolated peers."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import Keithley2231A, SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_powered_step_stage import POINTS, profile_for
from r4_5_powered_series_stage import (DIRECTIONS, OFFSETS, INITIAL_TOKEN,
    FINAL_TOKEN, series_token, R45ESeriesScope, run_powered_series)
from tests.offline.test_r4_5_powered_step_hardware_uart import Evidence, Load, Meter


class PowerOverrides(dict):
    """Only test doubles: reflect STEP register dynamics, no independent PSU driver."""
    def __init__(self, peer, mode):
        self.peer, self.mode = peer, mode
        super().__init__({x: '0' for x in ('MEAS:VOLT?', 'MEAS:CURR?', 'MEAS:POW?')})
        if mode == 'cc': self['STAT:OPER:INST:ISUM1:COND?'] = '2'
    def __getitem__(self, key):
        if key in ('MEAS:VOLT?', 'MEAS:CURR?', 'MEAS:POW?'):
            registers = self.peer.channel_registers[1]
            v = float(registers['VOLT']) if self.mode == 'cv' else float(registers['CURR'])*11
            i = v/40 if self.mode == 'cv' else float(registers['CURR'])
            return str({'MEAS:VOLT?': v, 'MEAS:CURR?': i, 'MEAS:POW?': v*i}[key])
        return super().__getitem__(key)


@unittest.skipUnless(os.name == 'posix', 'PTY UART fixture requires POSIX')
class PoweredSeriesUART(unittest.TestCase):
    def case(self, mode='cv', *, fault_after=None, unchanged=False,
             wrong_dmm=False, final_reject=False, power_reject=False,
             drop_after_first=None):
        peer = UARTPeer(profile_registers=True, fault_after=fault_after)
        self.addCleanup(peer.close)
        peer.overrides = PowerOverrides(peer, mode)
        transport = open_test_transport(peer, timeout=.16)
        evidence = Evidence()
        load = Load(peer, mode, unchanged=unchanged)
        meter = Meter(load, wrong=wrong_dmm, drop_after_first=drop_after_first)
        gates=[]
        def confirm(token, prompt, io=None):
            gates.append(token)
            if final_reject and token==FINAL_TOKEN: raise EOFError('final refused')
            if power_reject and token==series_token(mode): raise EOFError('power refused')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_powered_series(transport,load,meter,evidence,mode=mode,gate=confirm)
        return outcome,peer,evidence,load,meter,gates

    def test_cv_complete_exact_order_and_return(self):
        outcome,peer,ev,load,_,gates=self.case('cv')
        self.assertEqual(outcome,'PASS',ev.events[-1])
        moves=[x for x in peer.rx if x in ('VOLT:UP','VOLT:DOWN')]
        self.assertEqual(moves,['VOLT:UP','VOLT:UP','VOLT:DOWN','VOLT:DOWN'])
        self.assertEqual(peer.rx.count('VOLT:STEP 0.010'),1)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(peer.rx.count('OUTP 1'),0)
        self.assertEqual(peer.rx.count('*RST'),0)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')
        self.assertEqual(gates[-1],FINAL_TOKEN)
        points=[e for e in ev.events if e['event']=='powered_step_snapshot']
        self.assertEqual([e['offset_steps'] for e in points], list(OFFSETS))
        self.assertEqual([e['label'] for e in points], [f'point_{i}' for i in range(5)])
        self.assertEqual(len([e for e in ev.events if e['event']=='powered_series_segment']),4)
        self.assertLess(abs(points[0]['dmm_v']-points[-1]['dmm_v']),1e-6)
        self.assertFalse(peer.unsupported, peer.unsupported)

    def test_cc_complete_exact_order_and_return(self):
        outcome,peer,ev,load,_,_=self.case('cc')
        self.assertEqual(outcome,'PASS',ev.events[-1])
        self.assertEqual([x for x in peer.rx if x in ('CURR:UP','CURR:DOWN')],
                         ['CURR:UP','CURR:UP','CURR:DOWN','CURR:DOWN'])
        self.assertEqual(peer.rx.count('CURR:STEP 0.005'),1)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')
        points=[e for e in ev.events if e['event']=='powered_step_snapshot']
        self.assertEqual([round(e['kel_a'],3) for e in points],
                         [.100,.105,.110,.105,.100])
        self.assertFalse(peer.unsupported, peer.unsupported)

    def test_cv_wrong_dmm_aborts_before_first_mutation(self):
        outcome,peer,ev,load,_,_=self.case(wrong_dmm=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')

    def test_cv_no_analog_step_aborts_before_second_mutation(self):
        outcome,peer,ev,load,_,_=self.case(unchanged=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertEqual(peer.rx.count('VOLT:DOWN'),0)
        self.assertEqual(load.input,'OFF')

    def test_cc_no_analog_step_aborts_before_second_mutation(self):
        outcome,peer,ev,load,_,_=self.case('cc',unchanged=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('CURR:UP'),1)
        self.assertEqual(load.input,'OFF')

    def test_power_gate_rejected_no_energizing(self):
        outcome,peer,ev,load,_,_=self.case(power_reject=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),0)
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')

    def test_final_gate_rejected_not_pass(self):
        outcome,peer,ev,load,_,_=self.case(final_reject=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')

    def test_rejected_first_down_no_second_down(self):
        outcome,peer,ev,load,_,_=self.case(fault_after='VOLT:DOWN')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),2)
        self.assertEqual(peer.rx.count('VOLT:DOWN'),1)
        self.assertEqual(load.input,'OFF')

    def test_lost_uart_no_post_uncertainty_commands_but_kel_off(self):
        outcome,peer,ev,load,_,_=self.case(drop_after_first='VOLT:STEP?')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx[-1],'VOLT:STEP?')
        self.assertEqual(load.input,'OFF')

    def test_strict_limit_rejects_other_sequence(self):
        peer=UARTPeer(profile_registers=True); self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.16)
        dev=Keithley2231A(wire)
        self.addCleanup(dev.close)
        dev.apply_profile(profile_for('cv'),confirmed_wiring='independent')
        before=len(peer.rx)
        for bad in (('up','down'), ('up',)*5, ['up','up','down','down'],
                    ('down','up','up','down')):
            with self.subTest(bad=bad), self.assertRaises(SafetyError):
                with dev.powered_step_qualification(1,'voltage',.010,'up',
                        expected_mode='CV',external_load_confirmed=True,sequence=bad): pass
        self.assertEqual(len(peer.rx),before)

    def test_series_scope_rejects_other_write_before_wire(self):
        peer=UARTPeer(profile_registers=True); self.addCleanup(peer.close)
        scope=R45ESeriesScope(open_test_transport(peer,timeout=.16),Evidence(),mode='cv')
        self.addCleanup(scope.close)
        for cmd in ('*RST','OUTP 1','CHAN:OUTP 1','VOLT:DOWN','VOLT:UP',
                    'CURR:UP','CURR:STEP 0.005'):
            with self.subTest(cmd=cmd), self.assertRaises((SafetyError,ValueError)):
                scope.write(cmd)
        self.assertEqual(peer.rx,[])

    def test_dry_run_cv_cc_no_hardware(self):
        for mode in ('cv','cc'):
            with self.subTest(mode=mode):
                process=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_powered_series_qualify.py'),
                         '--config',str(ROOT/'config/bench.example.toml'), '--mode',mode],
                         capture_output=True,text=True,timeout=10)
                self.assertEqual(process.returncode,0,process.stderr)
                self.assertIn('DRY RUN',process.stdout)
                self.assertIn('UP UP DOWN DOWN',process.stdout)

if __name__=='__main__': unittest.main()

@unittest.skipUnless(os.name == 'posix', 'PTY UART fixture requires POSIX')
class PoweredSeriesDirectGuard(unittest.TestCase):
    def test_wrong_direction_then_final_extra_move_no_uart(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        peer.overrides = PowerOverrides(peer, 'cv')
        dev = Keithley2231A(open_test_transport(peer, timeout=.16))
        self.addCleanup(dev.close)
        dev.apply_profile(profile_for('cv'), confirmed_wiring='independent')
        with dev.powered_step_qualification(1, 'voltage', .010, 'up',
                expected_mode='CV', external_load_confirmed=True,
                sequence=DIRECTIONS):
            dev.output(1, True)
            before = len(peer.rx)
            with self.assertRaises(SafetyError):
                dev.step_source(1, 'voltage', 'down')
            self.assertEqual(len(peer.rx), before)
            for direction in DIRECTIONS:
                dev.step_source(1, 'voltage', direction)
            before = len(peer.rx)
            with self.assertRaises(SafetyError):
                dev.step_source(1, 'voltage', 'up')
            self.assertEqual(len(peer.rx), before)
            with self.assertRaises(SafetyError):
                dev.output(1, True)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertEqual(peer.rx.count('VOLT:UP'), 2)
        self.assertEqual(peer.rx.count('VOLT:DOWN'), 2)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_wrong_channel_and_kind_no_uart(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        peer.overrides = PowerOverrides(peer, 'cc')
        dev = Keithley2231A(open_test_transport(peer, timeout=.16))
        self.addCleanup(dev.close)
        dev.apply_profile(profile_for('cc'), confirmed_wiring='independent')
        with dev.powered_step_qualification(1, 'current', .005, 'up',
                expected_mode='CC', external_load_confirmed=True,
                sequence=DIRECTIONS):
            dev.output(1, True)
            before = len(peer.rx)
            for ch,kind in ((2,'current'),(3,'current'),(1,'voltage')):
                with self.subTest(ch=ch,kind=kind), self.assertRaises(SafetyError):
                    dev.step_source(ch,kind,'up')
            self.assertEqual(len(peer.rx), before)
        self.assertEqual(peer.rx.count('CURR:UP'), 0)
        self.assertFalse(any(peer.off_by_channel.values()))
