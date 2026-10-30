"""R4.5-F-E: guarded powered diagnostic exercised only through simulated PTY UART."""
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from r4_5_1n4933_ballast_stage import (BALLAST_OHM, INITIAL_TOKEN, POWER_TOKEN, FINAL_TOKEN,
    R45FEBallastScope, run_ballast, validate_ballast_point)


class BallastOverrides(dict):
    def __init__(self, peer):
        self.peer = peer
        super().__init__({'MEAS:VOLT?':'0', 'MEAS:CURR?':'0'})
    def __getitem__(self, key):
        if key in ('MEAS:VOLT?', 'MEAS:CURR?'):
            v = float(self.peer.channel_registers[1]['VOLT'])
            return str(v if key == 'MEAS:VOLT?' else .00230122)
        return super().__getitem__(key)


class Meter:
    def __init__(self, peer, voltage=.230, fail_at=None, loss_at=None):
        self.peer, self.voltage = peer, voltage
        self.fail_at, self.loss_at = fail_at, loss_at
        self.count = 0
        self.closed = self.configured = False
    def configure_voltage(self, value):
        if value != 10: raise AssertionError('Expected voltage range 10 V')
        self.configured = True
    def measure(self):
        self.count += 1
        if self.count == self.fail_at: raise OSError('Synthetic DMM error')
        if self.count == self.loss_at: self.peer.drop = 'MEAS:CURR?'
        return {'voltage_v': .0 if self.count == 1 else self.voltage}
    def close(self): self.closed = True


@unittest.skipUnless(os.name == 'posix', 'PTY simulation requires POSIX')
class BallastUART(unittest.TestCase):
    def case(self, *, voltage=.230, refuse=None, fail_at=None, fault_after=None,
             loss_at=None):
        peer = UARTPeer(profile_registers=True, fault_after=fault_after)
        self.addCleanup(peer.close)
        peer.overrides = BallastOverrides(peer)
        transport = open_test_transport(peer, timeout=.20)
        evidence = Evidence()
        meter = Meter(peer, voltage=voltage, fail_at=fail_at, loss_at=loss_at)
        approvals=[]
        def gate(token, description, adapter=None):
            approvals.append(token)
            if token == refuse: raise EOFError('Operator refuses')
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_ballast(transport, meter, evidence, gate=gate)
        return outcome, peer, evidence, meter, approvals

    def test_expected_psu_current_drop_completes(self):
        outcome, peer, ev, meter, approvals = self.case(voltage=.230)
        self.assertEqual(outcome, 'PASS',ev.events[-1])
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertEqual(peer.rx.count('OUTP 1'),0)
        self.assertEqual([e['event'] for e in ev.events].count('ballast_direct_point'),1)
        self.assertEqual([e['event'] for e in ev.events].count('ballast_direct_raw_sample'),1)
        self.assertTrue(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(approvals,[POWER_TOKEN,FINAL_TOKEN])
        self.assertFalse(peer.unsupported,peer.unsupported)
        self.assertTrue(meter.closed)

    def test_old_subtraction_drop_also_allowed_not_fabricated_as_truth(self):
        outcome, _, ev, _, _ = self.case(voltage=.335)
        self.assertEqual(outcome,'PASS',ev.events[-1])
        point = [e for e in ev.events if e['event']=='ballast_direct_point'][0]
        self.assertAlmostEqual(point['dmm_direct_ballast_current_a'],.335/BALLAST_OHM)
        self.assertEqual(point['current_accuracy'],'UNQUALIFIED')

    def test_operator_refusal_prevents_output_enable(self):
        outcome, peer, ev, _, _ = self.case(refuse=POWER_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertNotIn('CHAN:OUTP 1',peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_off_confirm_refusal_never_passes(self):
        outcome, peer, ev, _, _ = self.case(refuse=FINAL_TOKEN)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_low_drop_rejected_with_raw_record(self):
        outcome, peer, ev, _, _ = self.case(voltage=.01)
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(len([e for e in ev.events if e['event']=='ballast_direct_raw_sample']),1)
        self.assertFalse(any(e['event']=='ballast_direct_point' for e in ev.events))
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_high_drop_rejected_as_possible_short(self):
        outcome, peer, ev, _, _ = self.case(voltage=.85)
        self.assertEqual(outcome,'ABORTED')
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_negative_and_nonfinite_rejected(self):
        for drop in (-.2,float('nan'),float('inf')):
            with self.subTest(drop=drop):
                result,peer,_,_,_=self.case(voltage=drop)
                self.assertEqual(result,'ABORTED')
                self.assertFalse(any(peer.off_by_channel.values()))

    def test_dmm_error_preserves_psu_off(self):
        outcome,peer,ev,_,_=self.case(fail_at=2)
        self.assertEqual(outcome,'ABORTED')
        self.assertIn('OUTP 0',peer.rx)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_scpi_error_when_enable_is_terminal(self):
        outcome,peer,_,_,_=self.case(fault_after='CHAN:OUTP 1')
        self.assertEqual(outcome,'ABORTED')
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_scpi_loss_after_enable_has_no_retry_and_no_off_promise(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        peer.overrides = BallastOverrides(peer)
        transport = open_test_transport(peer, timeout=.20)
        evidence = Evidence()
        meter = Meter(peer)
        def gate(token, description, adapter=None):
            if token == POWER_TOKEN:
                # MEAS:VOLT? will be the first analog read after enable.
                peer.drop = 'MEAS:VOLT?'
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_ballast(transport, meter, evidence, gate=gate)
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(peer.rx[-1], 'MEAS:VOLT?')
        self.assertEqual(peer.rx.count('MEAS:VOLT?'), 1)
        self.assertTrue(peer.off_by_channel.get(1), 'uncertain means no verified OFF')
        self.assertIn('UNKNOWN', evidence.events[-1]['reason'])
        self.assertFalse(any(e['event']=='shutdown_psu' for e in evidence.events))

    def test_strict_write_envelope_and_wrong_channel(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        scope=R45FEBallastScope(open_test_transport(peer,timeout=.20),Evidence())
        self.addCleanup(scope.close)
        for command in ('*RST','OUTP 1','VOLT 3.00','CURR 2.000',
                        'VOLT 0.80','VOLT 0.90','CHAN:OUTP 1'):
            with self.subTest(command=command),self.assertRaises((SafetyError,ValueError)):
                if command=='VOLT 0.90': scope.selected=2
                else: scope.selected=1
                scope.write(command)
        self.assertEqual(peer.rx,[])

    def test_validator_current_is_screening_only(self):
        read=SimpleNamespace(enabled=True,regulation='CV',voltage_v=.9,current_a=.00230122)
        p=validate_ballast_point(read,.230)
        self.assertAlmostEqual(p['dmm_direct_ballast_current_a'],.230/99.86)
        for invalid in (-.1,.05,.7,1.0,float('nan')):
            with self.subTest(value=invalid),self.assertRaises(SafetyError):
                validate_ballast_point(read,invalid)
        with self.assertRaises(SafetyError):
            validate_ballast_point(SimpleNamespace(enabled=True,regulation='CC',voltage_v=.9,
                                                  current_a=.002),.23)

    def test_dry_run_no_instrument_ports(self):
        process=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_1n4933_ballast_qualify.py'),
             '--config',str(ROOT/'config/bench.example.toml')],
             capture_output=True,text=True,timeout=8)
        self.assertEqual(process.returncode,0,process.stderr)
        self.assertIn('DRY RUN: no ports opened',process.stdout)

    def test_example_bench_refused_in_execute(self):
        process=subprocess.run([sys.executable,str(ROOT/'tools/r4_5_1n4933_ballast_qualify.py'),
             '--config',str(ROOT/'config/bench.example.toml'),'--execute'],
             capture_output=True,text=True,timeout=8)
        self.assertNotEqual(process.returncode,0)
        self.assertIn('Example bench configuration prohibited',process.stderr)

    def test_plan_mutation_rejected(self):
        from tools.r4_5_1n4933_ballast_qualify import check_plan, PLAN
        import json
        import tempfile
        for mutate in ('hardware_authorized','ballast_ohm','dmm_role'):
            with self.subTest(field=mutate),tempfile.TemporaryDirectory() as tmp:
                data=json.loads(PLAN.read_text(encoding="utf-8"))
                data[mutate] = True if mutate=='hardware_authorized' else 'BAD'
                path=Path(tmp)/'plan.json'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError): check_plan(path)


if __name__ == '__main__': unittest.main()
