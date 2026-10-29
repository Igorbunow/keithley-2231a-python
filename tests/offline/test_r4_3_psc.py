"""R4.3 *PSC? single-PSU read-only API and hardware-gate simulation."""
from __future__ import annotations
import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import Keithley2231A, SafetyError, InstrumentError
from support.qualification_transport import FakeTransport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_3_psc_stage import R43PscScope, run_psc


class PscFake(FakeTransport):
    def __init__(self, psc='1'):
        super().__init__()
        self.psc = psc
        self.error_reply = '0,No error'
        self.break_on = None
        self.enable = {'*ESE?': '17', '*SRE?': '32'}

    def query(self, cmd):
        if cmd == self.break_on:
            self.cmds.append(cmd)
            raise OSError('UART read lost')
        if cmd == '*PSC?':
            self.cmds.append(cmd)
            assert self.remote
            return self.psc
        if cmd in self.enable:
            self.cmds.append(cmd)
            assert self.remote
            return self.enable[cmd]
        if cmd == 'SYST:ERR?':
            self.cmds.append(cmd)
            return self.error_reply
        return super().query(cmd)


class MemoryEvidence:
    def __init__(self): self.events = []
    def add(self, event, **kw):
        r = dict(event=event, **kw)
        self.events.append(r)
        return r


class PscTests(unittest.TestCase):
    def fake_hardware(self, wire=None, *, final_gate=lambda _: None):
        wire = wire if wire is not None else PscFake()
        evidence = MemoryEvidence()
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_psc(wire, evidence, final_gate=final_gate)
        return result, wire, evidence

    def test_hardware_scope_allows_only_exact_whitelist(self):
        for query in ('*ESR?', '*OPC?', '*WAI', 'STAT:QUES:EVEN?',
                      '*PSC', 'STAT:PRES', 'OUTP:STAT:ALL?', 'VOLT?', 'SYST:MODU?'):
            with self.subTest(query=query):
                wire, ev = PscFake(), MemoryEvidence()
                scope = R43PscScope(wire, ev)
                with self.assertRaises(SafetyError): scope.query(query)
                self.assertEqual(wire.cmds, [])
        for write in ('OUTP 0', 'OUTP 1', 'VOLT 0', 'CURR 0', '*PSC 1',
                      '*CLS', '*RST', '*RCL 0', 'STAT:PRES', '*OPC', 'SYST:BEEP'):
            with self.subTest(write=write):
                wire, ev = PscFake(), MemoryEvidence()
                scope = R43PscScope(wire, ev)
                with self.assertRaises(SafetyError): scope.write(write)
                self.assertEqual(wire.cmds, [])

    def test_hardware_psc_before_and_after_equal_and_no_destructive(self):
        result, wire, ev = self.fake_hardware()
        self.assertEqual(result, 'PASS')
        self.assertTrue(wire.closed)
        self.assertFalse(wire.remote)
        self.assertEqual(wire.cmds.count('*PSC?'), 1)
        self.assertEqual(wire.cmds.count('*ESE?'), 2)
        self.assertEqual(wire.cmds.count('*SRE?'), 2)
        self.assertFalse(any(c.startswith(('OUTP ', 'VOLT ', 'CHAN:OUTP ', 'CURR ')) for c in wire.cmds))
        self.assertNotIn('*ESR?', wire.cmds)
        self.assertNotIn('*CLS', wire.cmds)
        self.assertEqual(wire.active_ch, 1)
        values = next(e['values'] for e in ev.events if e['event'] == 'results')
        self.assertEqual(values['psc'], 1)
        self.assertEqual(values['enable_before'], values['enable_after'])
        self.assertEqual(ev.events[-1]['outcome'], 'PASS')
        for row in ev.events:
            if row['event'] == 'driver_trace':
                self.assertNotIn('TEST', str(row['trace']))
        self.assertEqual(wire.cmds.count('SYST:ERR?'),
                         len(wire.cmds) - wire.cmds.count('SYST:ERR?') - 1)

    def test_hardware_rejects_on_every_channel_before_psc(self):
        for ch in (1, 2, 3):
            wire = PscFake()
            wire.values[ch]['CHAN:OUTP'] = 1
            outcome, wire, ev = self.fake_hardware(wire)
            self.assertEqual(outcome, 'ABORTED')
            self.assertNotIn('*PSC?', wire.cmds)
            self.assertFalse(any(cmd.startswith('OUTP ') for cmd in wire.cmds))
            self.assertEqual(ev.events[-1]['outcome'], 'ABORTED')

    def test_hardware_rejects_any_combined_mode(self):
        for flag in ('OUTP:SER', 'OUTP:PARALLEL', 'OUTP:TRAC'):
            wire = PscFake()
            wire.flags[flag] = 1
            outcome, wire, _ = self.fake_hardware(wire)
            self.assertEqual(outcome, 'ABORTED')
            self.assertNotIn('*PSC?', wire.cmds)

    def test_hardware_psc_bad_reply_no_next_error_query(self):
        for reply in ('1.0', '2', '', 'nan', 'garbage', '1e0'):
            wire = PscFake(reply)
            outcome, wire, ev = self.fake_hardware(wire)
            self.assertEqual(outcome, 'ABORTED')
            self.assertEqual(wire.cmds[-1], '*PSC?')
            self.assertFalse(ev.events[-1]['link_known'])

    def test_hardware_lost_psc_reply_cannot_reuse_link(self):
        wire = PscFake()
        wire.break_on = '*PSC?'
        outcome, wire, ev = self.fake_hardware(wire)
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(wire.cmds[-1], '*PSC?')
        self.assertEqual(wire.cmds.count('*PSC?'), 1)
        self.assertFalse(ev.events[-1]['link_known'])

    def test_hardware_mutated_enable_masks_never_pass(self):
        wire = PscFake()
        original = wire.query
        def q(cmd):
            answer = original(cmd)
            if cmd == '*PSC?': wire.enable['*ESE?'] = '33'
            return answer
        wire.query = q
        outcome, wire, ev = self.fake_hardware(wire)
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(wire.cmds.count('*PSC?'), 1)

    def test_hardware_instrument_error_aborts(self):
        wire = PscFake()
        original = wire.query
        def q(cmd):
            if cmd == '*PSC?': wire.error_reply = '120,Invalid command'
            return original(cmd)
        wire.query = q
        outcome, wire, ev = self.fake_hardware(wire)
        self.assertEqual(outcome, 'ABORTED')
        self.assertNotIn('*SRE?', wire.cmds[wire.cmds.index('*PSC?')+1:])

    def test_hardware_final_gate_refusal_is_abort(self):
        outcome, wire, ev = self.fake_hardware(
            final_gate=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(ev.events[-1]['outcome'], 'ABORTED')

    def test_cli_dry_run_no_usb_or_live_ports(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT / 'src') + ':' + str(ROOT / 'tests/hardware')
        proc = subprocess.run(
            [sys.executable, str(ROOT / 'tools/r4_3_psc_qualify.py'),
             '--config', str(ROOT / 'config/bench.example.toml')],
            cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('DRY RUN', proc.stdout)
        self.assertIn('*PSC?', proc.stdout)
        self.assertNotIn('USB adapter path and VID:PID verified', proc.stdout)
        no_tty = subprocess.run(
            [sys.executable, str(ROOT / 'tools/r4_3_psc_qualify.py'),
             '--config', str(ROOT / 'config/bench.example.toml'), '--execute'],
            cwd=ROOT, env=env, text=True, capture_output=True)
        self.assertNotEqual(no_tty.returncode, 0)

    def test_runner_provenance_includes_both_new_files(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        from r4_3_psc_qualify import provenance
        fields = provenance()
        for label, path in (
                ('r43_runner_sha256', ROOT / 'tools/r4_3_psc_qualify.py'),
                ('r43_stage_sha256', ROOT / 'tests/hardware/r4_3_psc_stage.py')):
            self.assertEqual(fields[label], hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__': unittest.main()
