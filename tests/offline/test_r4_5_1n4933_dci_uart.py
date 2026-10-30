"""R4.5-F-G: fixed DC current capability and protected CH1 via virtual UART."""
from __future__ import annotations

import contextlib
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
from types import SimpleNamespace

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport
from tests.offline.test_r4_5_powered_step_hardware_uart import Evidence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_1n4933_dci_stage import (run_current, R45FGCurrentScope,
                                    validate_current_point, POWER_TOKEN, FINAL_TOKEN)
from equipment.dmm7510 import DMM7510


class Overrides(dict):
    def __init__(self, peer):
        self.peer = peer
        super().__init__({'MEAS:VOLT?': '0', 'MEAS:CURR?': '0'})
    def __getitem__(self, key):
        if key in ('MEAS:VOLT?', 'MEAS:CURR?'):
            value = float(self.peer.channel_registers[1]['VOLT'])
            return str(value if key == 'MEAS:VOLT?' else .00226021)
        return super().__getitem__(key)


class Meter:
    def __init__(self, peer, current=.00315, fail_at=None):
        self.peer, self.current, self.fail_at = peer, current, fail_at
        self.count = 0
        self.closed = self.configured = False
    def configure_current(self, value):
        if value != .01: raise AssertionError('Only 10mA reviewed')
        self.configured = True
    def measure_current(self):
        self.count += 1
        if self.count == self.fail_at: raise OSError('Meter transport failure')
        return {'current_a': 0.0 if self.count == 1 else self.current}
    def close(self): self.closed = True


@unittest.skipUnless(os.name == 'posix', 'PTY required')
class SeriesDciUART(unittest.TestCase):
    def case(self, *, current=.00315, refuse=None, fail_at=None, fault_after=None):
        peer = UARTPeer(profile_registers=True, fault_after=fault_after)
        self.addCleanup(peer.close)
        peer.overrides = Overrides(peer)
        wire = open_test_transport(peer, timeout=.20)
        evidence = Evidence()
        meter = Meter(peer, current=current, fail_at=fail_at)
        approvals=[]
        def gate(token, text, adapter=None):
            approvals.append(token)
            if token == refuse: raise EOFError('operator refusal')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_current(wire, meter, evidence, gate=gate)
        return outcome, peer, evidence, meter, approvals

    def test_one_power_enable_and_complete_off(self):
        outcome, peer, evidence, meter, approvals = self.case()
        self.assertEqual(outcome, 'PASS', evidence.events[-1])
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertNotIn('OUTP 1', peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertTrue(evidence.events[-1]['physical_off_confirmed'])
        self.assertEqual(approvals, [POWER_TOKEN, FINAL_TOKEN])
        self.assertEqual([e['event'] for e in evidence.events].count('dci_series_point'), 1)
        self.assertEqual([e['event'] for e in evidence.events].count('dci_series_raw_sample'), 1)
        self.assertFalse(peer.unsupported,peer.unsupported)
        self.assertTrue(meter.closed)

    def test_two_competing_current_explanations_both_allowed(self):
        for value in (.00226, .00332):
            with self.subTest(current=value):
                outcome, _, ev, _, _ = self.case(current=value)
                self.assertEqual(outcome,'PASS', ev.events[-1])
                point = next(e for e in ev.events if e['event']=='dci_series_point')
                self.assertAlmostEqual(point['dmm_direct_current_a'], value)
                self.assertEqual(point['current_accuracy'], 'UNQUALIFIED')

    def test_reverse_leads_rejected_off_and_raw_logged(self):
        outcome,peer,ev,_,_=self.case(current=-.0033)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual([e['event'] for e in ev.events].count('dci_series_raw_sample'),1)
        self.assertEqual([e['event'] for e in ev.events].count('dci_series_point'),0)

    def test_abnormal_current_rejected(self):
        for bad in (0, .0001, .009, .012, -.012, float('inf'), float('nan')):
            with self.subTest(bad=bad):
                outcome,peer,ev,_,_=self.case(current=bad)
                self.assertEqual(outcome,'ABORTED',ev.events[-1])
                self.assertFalse(any(peer.off_by_channel.values()))

    def test_gate_refusal_blocks_power(self):
        outcome,peer,_,_,_=self.case(refuse=POWER_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertNotIn('CHAN:OUTP 1',peer.rx)

    def test_final_gate_refusal_not_a_pass(self):
        outcome,peer,ev,_,_=self.case(refuse=FINAL_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_meter_failure_after_enable_shutdown(self):
        outcome,peer,ev,_,_=self.case(fail_at=2)
        self.assertEqual(outcome,'ABORTED')
        self.assertIn('OUTP 0',peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_scpi_error_on_enable(self):
        outcome,peer,_,_,_=self.case(fault_after='CHAN:OUTP 1')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_link_loss_after_enable_never_retries_or_claims_off(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        peer.overrides=Overrides(peer)
        wire=open_test_transport(peer,timeout=.20)
        ev=Evidence(); meter=Meter(peer)
        def gate(token,text,adapter=None):
            if token==POWER_TOKEN: peer.drop='MEAS:VOLT?'
        with contextlib.redirect_stdout(io.StringIO()):
            outcome=run_current(wire,meter,ev,gate=gate)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx[-1],'MEAS:VOLT?')
        self.assertEqual(peer.rx.count('MEAS:VOLT?'),1)
        self.assertTrue(peer.off_by_channel.get(1))
        self.assertIn('UNKNOWN',ev.events[-1]['reason'])

    def test_scope_forbids_alternative_currents_and_channel(self):
        peer=UARTPeer(profile_registers=True); self.addCleanup(peer.close)
        scope=R45FGCurrentScope(open_test_transport(peer,timeout=.20), Evidence())
        self.addCleanup(scope.close)
        for command in ('*RST','OUTP 1','CURR 0.020','VOLT 1.10', 'CURR 0.010','CHAN:OUTP 1'):
            with self.subTest(command=command), self.assertRaises((SafetyError,ValueError)):
                scope.selected=2 if command=='CURR 0.010' else 1
                scope.write(command)
        self.assertEqual(peer.rx, [])

    def test_validator(self):
        good=SimpleNamespace(enabled=True,regulation='CV',voltage_v=.9,current_a=.00226)
        validated=validate_current_point(good,.00315)
        self.assertAlmostEqual(validated['psu_minus_dmm_current_a'], -.00089)
        for bad in (-.0033,0,.009,float('nan')):
            with self.subTest(bad=bad), self.assertRaises(SafetyError):
                validate_current_point(good,bad)
        with self.assertRaises(SafetyError):
            validate_current_point(SimpleNamespace(enabled=True,regulation='CC',voltage_v=.9,current_a=.002), .003)

    def test_dry_run_no_io(self):
        proc=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_1n4933_dci_qualify.py'),
                             '--config',str(ROOT/'config/bench.example.toml')],
                            capture_output=True,text=True,timeout=9)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('DRY RUN: no ports opened',proc.stdout)

    def test_example_bench_not_executable(self):
        proc=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_1n4933_dci_qualify.py'),
                             '--config',str(ROOT/'config/bench.example.toml'),'--execute'],
                            capture_output=True,text=True,timeout=9)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('Example bench configuration prohibited',proc.stderr)


class FakeDMMWire:
    def __init__(self,*,current=.00315,function='CURR:DC',overrange=False):
        self.writes=[];self.queries=[];self.closed=False
        self.current=current;self.function=function;self.overrange=overrange
    def write(self,c): self.writes.append(c)
    def query(self,c):
        self.queries.append(c)
        return {'*IDN?':'KEITHLEY INSTRUMENTS,MODEL DMM7510,SN,1.0',
                '*LANG?':'SCPI', ':SYST:ERR?':'0,"No error"',
                ':ROUT:TERM?':'FRON',':SENS:FUNC?':self.function,
                ':SENS:CURR:RANG?':'.01', ':SENS:CURR:NPLC?':'1',
                ':SENS:CURR:REL:STAT?':'0', ':SENS:CURR:RANG:AUTO?':'0',
                ':READ?':str(self.current)}[c]
    def close(self): self.closed=True


class SharedMeterAdapterDCI(unittest.TestCase):
    def test_front_10ma_manual_dci_scpi(self):
        wire=FakeDMMWire();ev=Evidence();meter=DMM7510(wire,ev)
        meter.configure_current(.01)
        self.assertEqual(meter.measure_current(), {'current_a':.00315})
        self.assertIn(':SENS:FUNC "CURR"',wire.writes)
        self.assertIn(':SENS:CURR:RANG:AUTO OFF',wire.writes)
        self.assertIn(':SENS:CURR:RANG 0.01',wire.writes)
        self.assertIn(':SENS:CURR:REL:STAT OFF',wire.writes)
        self.assertEqual(next(e for e in ev.events if e['event']=='dmm7510_read' and e['command']==':READ?')['units'],'A')
        meter.close()
        self.assertTrue(wire.closed)

    def test_nonzero_scpi_rejects_and_marks_link_unusable(self):
        class BadWire(FakeDMMWire):
            def query(self,c):
                if c==':SYST:ERR?': return '120,"Bad setting"'
                return super().query(c)
        meter=DMM7510(BadWire(), Evidence())
        with self.assertRaises(RuntimeError): meter.configure_current(.01)
        self.assertFalse(meter.live)

    def test_bad_function_readback(self):
        meter=DMM7510(FakeDMMWire(function='VOLT:DC'),Evidence())
        with self.assertRaises(ValueError): meter.configure_current(.01)
        self.assertFalse(meter.configured)

    def test_meter_dci_overrange_aborts_not_retry(self):
        wire=FakeDMMWire(current=1e37)
        meter=DMM7510(wire,Evidence());meter.configure_current(.01)
        with self.assertRaises(ValueError):meter.measure_current()
        self.assertFalse(meter.live)
        self.assertEqual(wire.queries.count(':READ?'),1)

    def test_disallows_other_range(self):
        wire=FakeDMMWire();meter=DMM7510(wire,Evidence())
        with self.assertRaises(ValueError): meter.configure_current(.1)
        self.assertEqual(wire.queries,[])


if __name__=='__main__': unittest.main()
