"""Offline gate and on-wire scope tests for R4 STATUS hardware qualification."""
from __future__ import annotations

import contextlib
import io
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARDWARE = ROOT / 'tests/hardware'
sys.path.insert(0, str(HARDWARE))
from test_driver import FakeTransport
from keithley2231a import SafetyError
from r4_status_stage import R4StatusScope, run_status, EVENT_TOKEN, FINAL_TOKEN


class FakeStatusPSU(FakeTransport):
    def __init__(self):
        super().__init__()
        self.masks = {'*ESE?': '0', '*SRE?': '0', 'STAT:QUES:ENAB?': '0',
                      'STAT:OPER:ENAB?': '0'}
        self.masks.update({f'STAT:{grp}:INST:ENAB?': '1' for grp in ('QUES', 'OPER')})
        self.masks.update({f'STAT:{grp}:INST:ISUM{ch}:ENAB?': '1'
                           for grp in ('QUES', 'OPER') for ch in (1, 2, 3)})
        self.events = {'*ESR?': '0', 'STAT:QUES:EVEN?': '0', 'STAT:OPER:EVEN?': '0'}
        self.module = '2231,1.04'
        self.bad_command = None
        self.error_response = '0,No error'
    def query(self, cmd):
        if cmd == self.bad_command:
            self.cmds.append(cmd)
            raise OSError('fake broken UART')
        if cmd == 'SYST:ERR?':
            self.cmds.append(cmd)
            return self.error_response
        if cmd == 'SYST:MODU?':
            self.cmds.append(cmd)
            return self.module
        if cmd in self.masks:
            self.cmds.append(cmd)
            return self.masks[cmd]
        if cmd in self.events:
            self.cmds.append(cmd)
            answer = self.events[cmd]
            self.events[cmd] = '0'
            return answer
        return super().query(cmd)


class MemoryEvidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        result = dict(event=event, **fields)
        self.events.append(result)
        return result


class R4StatusHardwareTests(unittest.TestCase):
    def execute_fake(self, *, consume=False, wire=None, event_gate=None, final_gate=None):
        wire = wire or FakeStatusPSU()
        evidence = MemoryEvidence()
        with contextlib.redirect_stdout(io.StringIO()):
            result = run_status(wire, evidence, consume_events=consume,
                                event_gate=event_gate, final_gate=final_gate or (lambda t: None))
        return result, wire, evidence

    def test_read_only_real_driver_scoped_pass_and_no_destructive_wire(self):
        status, wire, evidence = self.execute_fake()
        self.assertEqual(status, 'PASS')
        self.assertTrue(wire.closed)
        self.assertEqual(wire.active_ch, 1)
        self.assertFalse(wire.remote)
        self.assertIn('SYST:MODU?', wire.cmds)
        self.assertEqual(sum(cmd == 'SYST:MODU?' for cmd in wire.cmds), 1)
        self.assertEqual(sum(cmd == 'SYST:ERR?' for cmd in wire.cmds),
                         len(wire.cmds) - sum(cmd == 'SYST:ERR?' for cmd in wire.cmds) - 1)
        self.assertNotIn('*ESR?', wire.cmds)
        self.assertFalse(any(':EVEN?' in x for x in wire.cmds))
        self.assertFalse(any(x.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ', '*CLS'))
                             for x in wire.cmds))
        self.assertEqual(evidence.events[-1]['outcome'], 'PASS')
        for entry in evidence.events:
            if entry['event'] == 'driver_trace':
                self.assertNotIn('TEST', str(entry.get('trace', {})))
        self.assertEqual([e for e in evidence.events if e['event'] == 'results'][0]['values']['enable'],
                         {'standard_event': 0, 'service_request': 0,
                          'questionable': 0, 'operation': 0})

    def test_results_hash_is_canonical_json_not_mislabeled_source(self):
        _, _, evidence = self.execute_fake()
        record = next(e for e in evidence.events if e['event'] == 'results')
        self.assertNotIn('source_sha256', record)
        expected = hashlib.sha256(json.dumps(
            record['values'], sort_keys=True, separators=(',', ':'),
            ensure_ascii=True).encode('utf-8')).hexdigest()
        self.assertEqual(record['results_sha256'], expected)

    def test_r4_runner_has_independent_provenance_and_pre_open_gate_record(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        from r4_status_qualify import r4_software_provenance, record_pre_open_approvals
        meta = r4_software_provenance()
        self.assertEqual(meta['r4_runner_sha256'], hashlib.sha256(
            (ROOT / 'tools/r4_status_qualify.py').read_bytes()).hexdigest())
        self.assertEqual(meta['r4_stage_sha256'], hashlib.sha256(
            (ROOT / 'tests/hardware/r4_status_stage.py').read_bytes()).hexdigest())
        evidence = MemoryEvidence()
        record_pre_open_approvals(evidence, 'deadbeef')
        self.assertEqual(evidence.events, [{
            'event': 'pre_open_approvals', 'approved_before_evidence_open': True,
            'gates': ['BENCH', 'INITIAL'], 'config_sha256': 'deadbeef'}])

    def test_event_phase_requires_one_gate_and_each_event_only_once(self):
        gates = []
        status, wire, evidence = self.execute_fake(
            consume=True, event_gate=lambda token: gates.append(token))
        self.assertEqual(status, 'PASS')
        self.assertEqual(gates, [EVENT_TOKEN])
        for key in ('*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?'):
            self.assertEqual(wire.cmds.count(key), 1)
        self.assertFalse(any(c.startswith(('OUTP ', 'CHAN:OUTP ')) for c in wire.cmds))

    def test_missing_event_gate_aborts_without_clearing_events(self):
        status, wire, evidence = self.execute_fake(consume=True)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(wire.events['*ESR?'], '0')
        self.assertNotIn('*ESR?', wire.cmds)
        self.assertEqual(evidence.events[-1]['outcome'], 'ABORTED')

    def test_any_live_channel_rejects_before_new_queries_without_power_writes(self):
        for channel in (1, 2, 3):
            with self.subTest(channel=channel):
                wire = FakeStatusPSU()
                wire.values[channel]['CHAN:OUTP'] = 1
                status, wire, evidence = self.execute_fake(wire=wire)
                self.assertEqual(status, 'ABORTED')
                self.assertNotIn('SYST:MODU?', wire.cmds)
                self.assertFalse(any(c.startswith('OUTP ') for c in wire.cmds))
                self.assertFalse(any(c.startswith('CHAN:OUTP ') for c in wire.cmds))

    def test_combined_mode_refused_even_all_off(self):
        for flag in ('OUTP:SER', 'OUTP:PARALLEL', 'OUTP:TRAC'):
            wire = FakeStatusPSU()
            wire.flags[flag] = 1
            status, wire, _ = self.execute_fake(wire=wire)
            self.assertEqual(status, 'ABORTED')
            self.assertNotIn('SYST:MODU?', wire.cmds)

    def test_bad_module_reply_does_not_poll_error_and_never_passes(self):
        for response in ('', 'bad\nreply', 'X'*129):
            with self.subTest(response=response):
                wire = FakeStatusPSU()
                wire.module = response
                status, wire, _ = self.execute_fake(wire=wire)
                self.assertEqual(status, 'ABORTED')
                self.assertEqual(wire.cmds[-1], 'SYST:MODU?')

    def test_no_reconnect_or_next_command_after_uart_loss(self):
        wire = FakeStatusPSU()
        wire.bad_command = '*SRE?'
        status, wire, evidence = self.execute_fake(wire=wire)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(wire.cmds[-1], '*SRE?')
        self.assertNotIn('STAT:QUES:ENAB?', wire.cmds)
        self.assertEqual(evidence.events[-1]['link_known'], False)


    def test_event_wire_reply_lost_cannot_retry_or_continue(self):
        wire = FakeStatusPSU()
        wire.bad_command = 'STAT:QUES:EVEN?'
        status, wire, evidence = self.execute_fake(
            consume=True, wire=wire, event_gate=lambda _: None)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(wire.cmds[-1], 'STAT:QUES:EVEN?')
        self.assertNotIn('STAT:OPER:EVEN?', wire.cmds)
        self.assertEqual(wire.cmds.count('STAT:QUES:EVEN?'), 1)
        self.assertEqual(evidence.events[-1]['link_known'], False)

    def test_instrument_error_after_module_not_mislabeled_as_pass(self):
        wire = FakeStatusPSU()
        # Simulate a documented SCPI query reply that carried an error.
        original = wire.query
        def query(cmd):
            if cmd == 'SYST:MODU?':
                wire.error_response = '120,Invalid command'
            return original(cmd)
        wire.query = query
        status, wire, evidence = self.execute_fake(wire=wire)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(wire.cmds[-1], 'SYST:ERR?')
        self.assertNotIn('*ESE?', wire.cmds)
        self.assertEqual(evidence.events[-1]['outcome'], 'ABORTED')

    def test_unsafe_dispatch_writes_rejected_before_on_wire(self):
        for command in ('OUTP 0', 'OUTP 1', 'CHAN:OUTP 1', 'VOLT 1.0',
                        '*CLS', '*RST', 'STAT:PRES', 'STAT:QUES:ENAB 1'):
            with self.subTest(command=command):
                wire, ev = FakeStatusPSU(), MemoryEvidence()
                scope = R4StatusScope(wire, ev)
                with self.assertRaises(SafetyError):
                    scope.write(command)
                self.assertEqual(wire.cmds, [])

    def test_event_query_refused_in_read_only_phase_at_scope(self):
        for cmd in ('*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?'):
            wire, ev = FakeStatusPSU(), MemoryEvidence()
            scope = R4StatusScope(wire, ev)
            with self.assertRaises(SafetyError):
                scope.query(cmd)
            self.assertEqual(wire.cmds, [])

    def test_refused_final_gate_cannot_create_pass(self):
        def refuse(_token): raise KeyboardInterrupt('operator refused')
        status, wire, evidence = self.execute_fake(final_gate=refuse)
        self.assertEqual(status, 'ABORTED')
        self.assertEqual(evidence.events[-1]['outcome'], 'ABORTED')

    def test_cli_is_dry_without_usb_and_execute_requires_tty(self):
        cli = ROOT / 'tools/r4_status_qualify.py'
        for argv in ([], ['--config', str(ROOT/'config/bench.example.toml')]):
            proc = subprocess.run([sys.executable, str(cli), *argv], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('DRY RUN', proc.stdout)
        proc = subprocess.run([sys.executable, str(cli), '--consume-events'],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        proc = subprocess.run([sys.executable, str(cli), '--execute'],
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn('USB adapter identity:', proc.stdout)

    def test_only_exact_status_commands_and_bounded_budget(self):
        scope = R4StatusScope(FakeStatusPSU(), MemoryEvidence(), allow_events=True)
        self.assertLessEqual(scope.MAX_COMMANDS, 250)
        self.assertLessEqual(scope.MAX_SECONDS, 120)
        self.assertFalse(scope.event_authorized)
        self.assertFalse({'*CLS', '*RST', 'OUTP 0', 'OUTP 1'} & set(scope.WRITES))
