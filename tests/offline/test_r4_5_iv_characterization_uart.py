"""R4.5-F-A: POSIX virtual UART production-driver pilot plus independent KEL/DMM doubles."""
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
from tests.offline.test_r4_5_powered_series_uart import PowerOverrides
from tests.offline.test_r4_5_powered_step_hardware_uart import Evidence, Load, Meter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_iv_characterization_stage import (
    R45FIVScope, EXPECTED_V, INITIAL_TOKEN, POWER_TOKEN, FINAL_TOKEN,
    validate_iv, run_iv, profile)


class FrozenLoad(Load):
    def _values(self):
        return 3.00, .075


class DriftLoad(Load):
    def measurements(self):
        original = super().measurements()
        original['kel103_current_a'] += .015
        return original


@unittest.skipUnless(os.name == 'posix', 'PTY-backed production driver tests need POSIX')
class IVUART(unittest.TestCase):
    def case(self, *, fault_after=None, drop_after_first=None,
             wrong_dmm=False, frozen=False, wrong_load=False,
             refused=None, loss_at=None):
        peer = UARTPeer(profile_registers=True, fault_after=fault_after)
        self.addCleanup(peer.close)
        peer.overrides = PowerOverrides(peer, 'cv')
        transport = open_test_transport(peer, timeout=.16)
        evidence = Evidence()
        cls = FrozenLoad if frozen else DriftLoad if wrong_load else Load
        load = cls(peer, 'cv')
        meter = Meter(load, wrong=wrong_dmm, drop_after_first=drop_after_first)
        tokens=[]
        def confirm(token, prompt, io=None):
            tokens.append(token)
            if refused == token: raise EOFError('operator veto')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_iv(transport, load, meter, evidence, gate=confirm)
        return outcome,peer,evidence,load,meter,tokens

    def test_complete_five_analog_points_bidirectional(self):
        outcome,peer,evidence,load,meter,gates=self.case()
        self.assertEqual(outcome, 'PASS', evidence.events[-1])
        self.assertEqual([x for x in peer.rx if x in ('VOLT:UP','VOLT:DOWN')],
                         ['VOLT:UP','VOLT:UP','VOLT:DOWN','VOLT:DOWN'])
        self.assertEqual(peer.rx.count('VOLT:STEP 0.100'),1)
        self.assertEqual(peer.rx.count('VOLT:STEP 0.010'),0)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(peer.rx.count('OUTP 1'),0)
        points=[e for e in evidence.events if e['event']=='iv_point']
        self.assertEqual([round(p['digital_v'],2) for p in points],list(EXPECTED_V))
        self.assertEqual([round(p['dmm_v'],2) for p in points],list(EXPECTED_V))
        self.assertEqual(len([e for e in evidence.events if e['event']=='iv_digital_step']),4)
        self.assertEqual(len([e for e in evidence.events if e['event']=='iv_characterization']),1)
        self.assertLess(abs(points[0]['kel_a']-points[-1]['kel_a']),1e-8)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(load.input,'OFF')
        self.assertEqual(gates[-1],FINAL_TOKEN)
        self.assertFalse(peer.unsupported,peer.unsupported)

    def test_invalid_dmm_fails_before_first_step(self):
        outcome,peer,ev,load,_,_=self.case(wrong_dmm=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertNotIn('VOLT:UP',peer.rx)
        self.assertEqual(load.input,'OFF')

    def test_static_physical_observation_stops_before_second_step(self):
        outcome,peer,ev,load,_,_=self.case(frozen=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertEqual(peer.rx.count('VOLT:DOWN'),0)
        self.assertEqual(load.input,'OFF')

    def test_wrong_cr_current_aborts_early(self):
        outcome,peer,ev,load,_,_=self.case(wrong_load=True)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),0)
        self.assertEqual(load.input,'OFF')

    def test_power_gate_refusal_blocks_enable(self):
        outcome,peer,ev,load,_,_=self.case(refused=POWER_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertNotIn('CHAN:OUTP 1',peer.rx)
        self.assertNotIn('VOLT:STEP 0.100',peer.rx)
        self.assertEqual(load.input,'OFF')

    def test_final_gate_refusal_prevents_pass(self):
        outcome,peer,ev,load,_,_=self.case(refused=FINAL_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_scpi_step_fault_does_not_repeat(self):
        outcome,peer,ev,load,_,_=self.case(fault_after='VOLT:UP')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'),1)
        self.assertEqual(load.input,'OFF')

    def test_uncertain_uart_blocks_further_steps_but_kel_off(self):
        outcome,peer,ev,load,_,_=self.case(drop_after_first='VOLT:STEP?')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx[-1],'VOLT:STEP?')
        self.assertNotIn('VOLT:UP',peer.rx)
        self.assertEqual(load.input,'OFF')
        self.assertIn('manual',ev.events[-1]['reason'].lower())

    def test_scope_rejects_other_modes_and_hidden_commands_before_wire(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        scope=R45FIVScope(open_test_transport(peer,timeout=.16),Evidence())
        self.addCleanup(scope.close)
        for cmd in ('*RST','OUTP 1','CHAN:OUTP 1','CURR:UP',
                    'CURR:STEP 0.005','VOLT:STEP 0.010','VOLT:STEP 0.100',
                    'VOLT:DOWN','VOLT 2.80','VOLT 3.30'):
            with self.subTest(cmd=cmd), self.assertRaises((SafetyError,ValueError)):
                scope.write(cmd)
        self.assertEqual(peer.rx,[])

    def test_scope_forbids_step_after_power_on(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        scope=R45FIVScope(open_test_transport(peer,timeout=.16),Evidence())
        self.addCleanup(scope.close)
        scope.step_window_authorized=True
        scope.selected=1
        scope.outputs={1:True,2:False,3:False}
        with self.assertRaises(SafetyError): scope.write('VOLT:STEP 0.100')
        self.assertEqual(peer.rx,[])

    def test_iv_rejects_nonresistive_slope_and_wrong_return(self):
        p=[dict(dmm_v=v,kel_a=v/40) for v in EXPECTED_V]
        self.assertAlmostEqual(validate_iv(p)['equivalent_ohms'],40.)
        p[2]['kel_a'] += .015
        with self.assertRaises(SafetyError): validate_iv(p)
        p=[dict(dmm_v=v,kel_a=v/40) for v in EXPECTED_V]
        p[4]['dmm_v'] += .025
        with self.assertRaises(SafetyError): validate_iv(p)

    def test_cli_dry_run_no_instrument_io(self):
        command=[sys.executable,str(ROOT/'tools/r4_5_iv_characterize.py'),
                 '--config',str(ROOT/'config/bench.example.toml')]
        proc=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('DRY RUN',proc.stdout)
        self.assertIn('3.00, 3.10, 3.20, 3.10, 3.00',proc.stdout)

    def test_cli_no_execute_rejects_evidence_override(self):
        command=[sys.executable,str(ROOT/'tools/r4_5_iv_characterize.py'),
                 '--config',str(ROOT/'config/bench.example.toml'),
                 '--evidence','/tmp/should-not-create.jsonl']
        proc=subprocess.run(command,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('requires --execute',proc.stderr)


if __name__ == '__main__': unittest.main()
