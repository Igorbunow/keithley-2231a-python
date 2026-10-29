"""Offline safety contract for R4.4-C: production driver through UART PTY."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tests/hardware'))
from bench_config import load_bench, R44ScalarPoint
from r4_4_scalar_stage import (R44ScalarScope, run_scalar,
                               validate_measurements, INITIAL_TOKEN,
                               ENABLE_TOKEN, FINAL_TOKEN)


class Evidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        item = {'event': event, **fields}
        self.events.append(item)
        return item


class FakeLoad:
    transport = 'USB serial (simulated)'
    live = True
    def __init__(self): self.input = 'OFF'; self.cr = None; self.closed = False
    def identify(self): return 'KEL103,QUALIFICATION-SIM'
    def input_state(self): return self.input
    def configure_cr(self, r):
        if r != 40.0 or self.input != 'OFF': raise SafetyError('CR setup rejected')
        self.cr = r
    def set_input(self, enabled):
        if enabled and self.cr != 40.0: raise SafetyError('unconfigured load')
        self.input = 'ON' if enabled else 'OFF'
    def measurements(self):
        return dict(kel103_voltage_v=3.30, kel103_current_a=.0825,
                    kel103_power_w=.27225)
    def close(self): self.closed = True


class FakeMeter:
    live = True
    def __init__(self): self.range = None; self.closed = False
    def configure_voltage(self, range_v):
        if range_v != 10: raise AssertionError('bad range')
        self.range = range_v
    def measure(self):
        if self.range != 10: raise AssertionError('not configured')
        return {'voltage_v': 3.300}
    def close(self): self.closed = True


@unittest.skipUnless(os.name == 'posix', 'requires PTY')
class ScalarHardwareUART(unittest.TestCase):
    def run_stage(self, *, fault_after=None, drop=None, load=None, meter=None,
                  gate=None, overrides=None):
        scripted = {'MEAS:VOLT?': '3.300', 'MEAS:CURR?': '.0825',
                    'MEAS:POW?': '.27225', 'FETC:VOLT?': '3.300',
                    'FETC:CURR?': '.0825', 'FETC:POW?': '.27225'}
        scripted.update(overrides or {})
        peer = UARTPeer(profile_registers=True, response_overrides=scripted,
                        fault_after=fault_after, drop=drop)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.14)
        ev = Evidence()
        load = load or FakeLoad()
        meter = meter or FakeMeter()
        tokens = []
        def confirm(token, _, adapter=None):
            tokens.append(token)
            if gate is not None: gate(token, adapter)
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_scalar(wire, load, meter, ev, gate=confirm)
        return outcome, peer, ev, load, meter, tokens

    def test_success_point_and_freshness_not_claimed(self):
        outcome, peer, ev, load, meter, gates = self.run_stage()
        self.assertEqual(outcome, 'PASS', ev.events[-1])
        self.assertIn(ENABLE_TOKEN, gates)
        self.assertEqual(gates[-1], FINAL_TOKEN)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertEqual(peer.rx.count('MEAS:POW?'), 1)
        for c in ('FETC:VOLT?', 'FETC:CURR?', 'FETC:POW?'):
            self.assertEqual(peer.rx.count(c), 1)
        self.assertFalse(any(c in peer.rx for c in ('*RST', '*CLS', 'OUTP 1', 'OUTP:SER 1')))
        self.assertEqual(load.input, 'OFF')
        self.assertTrue(load.closed and meter.closed)
        self.assertFalse(peer.unsupported, peer.unsupported)
        measurement = next(e for e in ev.events if e['event']=='scalar_measurements')
        self.assertEqual(measurement['fetch_is_fresh'], 'UNVERIFIED')

    def test_invalid_power_is_failure_with_off(self):
        outcome, peer, ev, load, _, _ = self.run_stage(overrides={'MEAS:POW?':'1.00'})
        self.assertEqual(outcome, 'ABORTED')
        self.assertIn('OUTP 0', peer.rx)
        self.assertEqual(load.input, 'OFF')
        self.assertFalse(any(e['event']=='scalar_measurements' for e in ev.events))

    def test_link_timeout_has_no_retry_and_still_stops_load(self):
        outcome, peer, ev, load, _, _ = self.run_stage(drop='MEAS:POW?')
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(peer.rx.count('MEAS:POW?'), 1)
        self.assertEqual(peer.rx[-1], 'MEAS:POW?')
        self.assertNotIn('SYST:LOC', peer.rx)
        self.assertEqual(load.input, 'OFF')

    def test_refused_power_gate_never_enables(self):
        def reject(token, _):
            if token == ENABLE_TOKEN: raise EOFError('no approval')
        outcome, peer, ev, load, _, _ = self.run_stage(gate=reject)
        self.assertEqual(outcome, 'ABORTED')
        self.assertNotIn('CHAN:OUTP 1', peer.rx)
        self.assertEqual(load.input, 'OFF')

    def test_malformed_fetch_blocks_error_poll(self):
        outcome, peer, ev, load, _, _ = self.run_stage(overrides={'FETC:POW?': 'NaN'})
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(peer.rx[-1], 'FETC:POW?')
        self.assertEqual(load.input, 'OFF')

    def test_stale_fetch_finite_is_observed_not_fresh(self):
        outcome, peer, ev, _, _, _ = self.run_stage(overrides={'FETC:VOLT?':'0.0',
            'FETC:CURR?':'0.0', 'FETC:POW?':'0.0'})
        self.assertEqual(outcome, 'PASS', ev.events[-1])
        item = next(e for e in ev.events if e['event'] == 'scalar_measurements')
        self.assertFalse(item['fetch_agrees_with_live'])
        self.assertEqual(item['fetch_is_fresh'], 'UNVERIFIED')

    def test_scope_rejects_unreviewed_commands_without_io(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        scope = R44ScalarScope(wire, Evidence())
        for c in ('*RST', 'OUTP 1', 'OUTP:PARALLEL 1', 'VOLT 5.00', 'CURR 3.0', 'DISP 0'):
            with self.subTest(c=c), self.assertRaises(SafetyError): scope.write(c)
        with self.assertRaises(SafetyError): scope.write('CHAN:OUTP 1')
        self.assertEqual(peer.rx, [])

    def test_evidence_failure_cannot_block_shutdown(self):
        class BrokenEvidence(Evidence):
            def add(self, event, **fields):
                if event in ('scalar_measurements', 'outcome'):
                    raise OSError('disk full')
                return super().add(event, **fields)
        peer = UARTPeer(profile_registers=True,
            response_overrides={'MEAS:VOLT?':'3.300', 'MEAS:CURR?':'.0825',
                                'MEAS:POW?':'.27225', 'FETC:VOLT?':'3.300',
                                'FETC:CURR?':'.0825', 'FETC:POW?':'.27225'})
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.14)
        load, meter, ev = FakeLoad(), FakeMeter(), BrokenEvidence()
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_scalar(wire, load, meter, ev, gate=lambda *a:None)
        self.assertEqual(outcome, 'ABORTED')
        self.assertIn('OUTP 0', peer.rx)
        self.assertEqual(load.input, 'OFF')

    def test_final_gate_refusal_prevents_pass(self):
        def reject(token, _):
            if token == FINAL_TOKEN: raise EOFError('physical OFF not confirmed')
        outcome, peer, ev, load, _, _ = self.run_stage(gate=reject)
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(load.input, 'OFF')
        self.assertIn('OUTP 0', peer.rx)

    def test_unexpected_ch2_on_never_enables_ch1(self):
        peer = UARTPeer(profile_registers=True, off_by_channel={2:1})
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.14)
        ev = Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            outcome = run_scalar(wire, FakeLoad(), FakeMeter(), ev, gate=lambda *a:None)
        self.assertEqual(outcome, 'ABORTED')
        self.assertNotIn('CHAN:OUTP 1', peer.rx)

    def test_dmm_failure_causes_verified_off(self):
        class BadMeter(FakeMeter):
            def measure(self): raise TimeoutError('DMM link timeout')
        outcome, peer, ev, load, _, _ = self.run_stage(meter=BadMeter())
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(load.input, 'OFF')
        self.assertIn('OUTP 0', peer.rx)

    def test_bad_config_block_fails_before_ports_open(self):
        example = (ROOT/'config/bench.example.toml').read_text()
        base, tail = example.split('# [qualification.r44_scalar]', 1)
        enabled = base + '[qualification.r44_scalar]' + tail
        marker = '[qualification.r44_scalar]'
        prefix, block = enabled.split(marker, 1)
        for name in ('voltage_v','current_limit_a','load_resistance_ohm','max_power_w'):
            block = block.replace('# '+name+' = ', name+' = ')
        enabled = prefix + marker + block
        with tempfile.TemporaryDirectory() as temp:
            good = Path(temp)/'bench.toml'
            good.write_text(enabled)
            self.assertIsNotNone(load_bench(good).r44_scalar)
            bad = Path(temp)/'bad.toml'
            bad.write_text(enabled.replace('voltage_v = 3.30','voltage_v = 4.30'))
            with self.assertRaises(ValueError): load_bench(bad)

    def test_point_config_exact_validation(self):
        R44ScalarPoint(3.30, .15, 40.0, .60).validate()
        for values in ((4.0,.15,40.,.6),(3.3,.2,40.,.6),
                       (3.3,.15,47.,.6),(3.3,.15,40.,1.0),
                       (3.3,.15,40.,float('nan'))):
            with self.subTest(values=values), self.assertRaises(ValueError):
                R44ScalarPoint(*values).validate()

    def test_cli_dry_run_does_not_open(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        result = subprocess.run([sys.executable, str(ROOT/'tools/r4_4_scalar_qualify.py'),
                                 '--config', str(ROOT/'config/bench.example.toml')],
                                env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DRY RUN:', result.stdout)
        r = subprocess.run([sys.executable, str(ROOT/'tools/r4_4_scalar_qualify.py'),
                            '--config', str(ROOT/'config/bench.example.toml'), '--execute'],
                           env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('Example bench cannot be used', r.stderr)

if __name__=='__main__': unittest.main()
