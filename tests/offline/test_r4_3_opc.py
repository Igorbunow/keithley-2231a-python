"""R4.3-C: explicit completion query and simulated hardware safety gate."""
from __future__ import annotations
import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import Keithley2231A, InstrumentError, SafetyError
from support.qualification_transport import FakeTransport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_3_opc_stage import R43OpcScope, run_opc


class OpcFake(FakeTransport):
    def __init__(self, reply='1'):
        super().__init__()
        self.reply = reply
        self.break_on = None
        self.error_reply = '0,No error'
        self.enable = {'*ESE?': '17', '*SRE?': '32'}

    def query(self, cmd):
        if cmd == self.break_on:
            self.cmds.append(cmd)
            raise OSError('UART read timeout')
        if cmd == '*OPC?':
            self.cmds.append(cmd)
            assert self.remote
            return self.reply
        if cmd in self.enable:
            self.cmds.append(cmd)
            return self.enable[cmd]
        if cmd == 'SYST:ERR?':
            self.cmds.append(cmd)
            return self.error_reply
        return super().query(cmd)


class MemoryEvidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        r = dict(event=event, **fields)
        self.events.append(r)
        return r


class OpcTests(unittest.TestCase):
    def stage(self, wire=None, *, final_gate=lambda _: None):
        wire = wire if wire is not None else OpcFake()
        evidence = MemoryEvidence()
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_opc(wire, evidence, final_gate=final_gate)
        return status, wire, evidence

    def test_scope_rejects_unsupported_reads_and_all_mutating_writes(self):
        for command in ('*PSC?', '*ESR?', '*OPC', '*WAI', 'STAT:QUES:EVEN?',
                        'VOLT?', 'SYST:MODU?'):
            with self.subTest(command=command):
                wire, ev = OpcFake(), MemoryEvidence()
                scope = R43OpcScope(wire, ev)
                with self.assertRaises(SafetyError): scope.query(command)
                self.assertEqual(wire.cmds, [])
        for command in ('*OPC', '*WAI', 'OUTP 0', 'OUTP 1', 'VOLT 0',
                        'CURR 0', '*PSC 0', '*CLS', '*RST', '*TRG', '*RCL 0'):
            with self.subTest(command=command):
                wire, ev = OpcFake(), MemoryEvidence()
                scope = R43OpcScope(wire, ev)
                with self.assertRaises(SafetyError): scope.write(command)
                self.assertEqual(wire.cmds, [])

    def test_hardware_success_one_opc_and_enable_masks_unchanged(self):
        outcome, wire, ev = self.stage()
        self.assertEqual(outcome, 'PASS')
        self.assertEqual(wire.cmds.count('*OPC?'), 1)
        self.assertEqual(wire.cmds.count('*ESE?'), 2)
        self.assertEqual(wire.cmds.count('*SRE?'), 2)
        self.assertTrue(wire.closed)
        self.assertFalse(wire.remote)
        self.assertNotIn('*WAI', wire.cmds)
        self.assertFalse(any(x.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ')) for x in wire.cmds))
        self.assertEqual(next(e for e in ev.events if e['event']=='results')['values']['complete'], True)
        self.assertEqual(ev.events[-1]['outcome'], 'PASS')
        self.assertEqual(len([e for e in ev.events if e['event']=='read' and e.get('command')=='*OPC?']), 1)
        self.assertEqual(len([e for e in ev.events if e['event']=='read' and e.get('command')=='SYST:ERR?']),
                         wire.cmds.count('SYST:ERR?'))

    def test_hardware_bad_reply_no_error_query_after_opc(self):
        wire = OpcFake('0')
        result, wire, ev = self.stage(wire)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(wire.cmds[-1], '*OPC?')
        self.assertFalse(any(e['event'] == 'results' for e in ev.events))
        self.assertFalse(ev.events[-1]['link_known'])

    def test_hardware_control_character_reply_stops_before_error_poll(self):
        wire = OpcFake('1\n')
        result, wire, ev = self.stage(wire)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(wire.cmds[-1], '*OPC?')
        self.assertFalse(ev.events[-1]['link_known'])

    def test_hardware_lost_reply_no_next_command_and_no_shutdown(self):
        wire = OpcFake()
        wire.break_on = '*OPC?'
        result, wire, ev = self.stage(wire)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(wire.cmds[-1], '*OPC?')
        self.assertFalse(any(c.startswith('OUTP ') for c in wire.cmds))
        self.assertFalse(ev.events[-1]['link_known'])

    def test_hardware_error_after_opc_is_not_pass(self):
        wire = OpcFake()
        wire.error_reply = '120,"Execution error"'
        result, wire, ev = self.stage(wire)
        self.assertEqual(result, 'ABORTED')
        self.assertNotIn('*OPC?', wire.cmds)

    def test_hardware_output_on_rejected_before_opc(self):
        for ch in (1,2,3):
            with self.subTest(ch=ch):
                wire = OpcFake()
                wire.values[ch]['CHAN:OUTP'] = 1
                result, wire, ev = self.stage(wire)
                self.assertEqual(result, 'ABORTED')
                self.assertNotIn('*OPC?', wire.cmds)
                self.assertFalse(any(c.startswith('OUTP ') for c in wire.cmds))

    def test_hardware_enable_drift_rejected(self):
        wire = OpcFake()
        orig = wire.query
        counts = {'*ESE?': 0}
        def changed(cmd):
            if cmd == '*ESE?':
                counts[cmd] += 1
                if counts[cmd] == 2:
                    wire.cmds.append(cmd)
                    return '18'
            return orig(cmd)
        wire.query = changed
        result, wire, ev = self.stage(wire)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(wire.cmds.count('*OPC?'), 1)
        self.assertFalse(any(c.startswith('OUTP ') for c in wire.cmds))

    def test_hardware_final_gate_refusal_aborts(self):
        def decline(_): raise EOFError('operator refused')
        result, wire, ev = self.stage(final_gate=decline)
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(ev.events[-1]['outcome'], 'ABORTED')

    def test_cli_dry_run_uses_no_hardware_and_provenance_hashes_match(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        run = subprocess.run([sys.executable, str(ROOT/'tools/r4_3_opc_qualify.py'),
                              '--config', str(ROOT/'config/bench.example.toml')],
                             env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('DRY RUN:',run.stdout)
        from tools.r4_3_opc_qualify import provenance
        info = provenance()
        self.assertEqual(info['r43_opc_runner_sha256'],
                         hashlib.sha256((ROOT/'tools/r4_3_opc_qualify.py').read_bytes()).hexdigest())
        self.assertEqual(info['r43_opc_stage_sha256'],
                         hashlib.sha256((ROOT/'tests/hardware/r4_3_opc_stage.py').read_bytes()).hexdigest())


if __name__ == '__main__': unittest.main()
