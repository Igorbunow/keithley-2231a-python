"""Offline SCPI sequence review: no redundant error checks or unsafe writes.

The tests exercise the exact qualified H01 and H02_PROFILE_ONLY driver/adapter
paths with a fake transport.  No physical equipment is opened or addressed.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qualification import Evidence, h01
from tools.audit_hw_scpi import audit, main as audit_main
from profile_stage import (H02_CHANNELS, H02_HARD_PROBES, H02_PANEL_TOKEN,
                           H02_DEBUG_TOKEN, h02)
from test_driver import FakeTransport


class ScpiSequenceAuditTests(unittest.TestCase):
    def _capture(self, stage, runner):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            evidence = Evidence(Path(folder) / 'events.jsonl', stage)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner(fake, evidence), 'PASS')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            cmds = list(fake.cmds)
        self.assertTrue(fake.closed)
        trace = [record['trace']['command'] for record in records
                 if record['event'] == 'driver_trace' and
                 record['trace']['event'] in ('write', 'query')]
        self.assertEqual(trace, cmds, 'Audit must cover every command on the wire')
        self.assertEqual(sum(c == 'SYST:ERR?' for c in cmds),
                         sum(c not in ('SYST:ERR?', 'SYST:LOC') for c in cmds),
                         'One error read per command (except ERR? and LOC)')
        for index, command in enumerate(cmds):
            if command not in ('SYST:ERR?', 'SYST:LOC'):
                self.assertLess(index + 1, len(cmds))
                self.assertEqual(cmds[index + 1], 'SYST:ERR?',
                                 f'Missing error check after {command} at {index}')
            if command == 'SYST:ERR?' and index:
                self.assertNotEqual(cmds[index - 1], 'SYST:ERR?',
                                    f'Redundant error check at {index}')
        self.assertFalse(any(c in ('OUTP 1', 'CHAN:OUTP 1', '*RST', '*CLS') for c in cmds))
        self.assertFalse(any(record.get('event') == 'stage_error' for record in records))
        self.assertTrue(all(not fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
        return cmds

    def test_h01_passive_sequence_has_no_adjacent_error_polls(self):
        cmds = self._capture('H01', h01)
        self.assertEqual(cmds[:4], ['SYST:REM', 'SYST:ERR?', '*IDN?', 'SYST:ERR?'])
        self.assertEqual(cmds[-1], 'SYST:LOC')
        self.assertFalse(any(c.startswith(('OUTP ', 'CURR ', 'VOLT ')) for c in cmds))
        core = [command for command in cmds if command != 'SYST:ERR?']
        self.assertFalse(any(a == b == 'INST:NSEL 1' for a, b in zip(core, core[1:])),
                         'H01 repeated selection after output_states restored CH1')
        for ch in (1, 2, 3):
            self.assertIn(f'INST:NSEL {ch}', cmds)
            self.assertIn(f'STAT:OPER:INST:ISUM{ch}:COND?', cmds)

    def test_h01_other_initial_selection_still_selects_first_channel(self):
        """Only skip the first CH1 selection after a proved CH1 restoration."""
        source = FakeTransport()
        source.active_ch = 2
        with tempfile.TemporaryDirectory() as folder:
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H01')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h01(source, evidence), 'PASS')
        cmds = source.cmds
        core = [command for command in cmds if command != 'SYST:ERR?']
        # Output-state sweep restores CH2, then the snapshot must select CH1.
        self.assertIn(('INST:NSEL 2', 'INST:NSEL 1'), list(zip(core, core[1:])))
        self.assertEqual(core[-2:], ['INST:NSEL 2', 'SYST:LOC'])

    def test_snapshot_selection_mismatch_never_reads_wrong_channel_settings(self):
        from common import channel_snapshot
        from keithley2231a import SafetyError

        class WrongSelection:
            def __init__(self): self.calls = []
            def diagnostic_query(self, command):
                self.calls.append(command)
                if command != 'INST:NSEL?':
                    raise AssertionError('Read wrong-channel settings after selection mismatch')
                return '2'
            def diagnostic_write(self, command):
                self.calls.append(command)

        source = WrongSelection()
        with self.assertRaises(SafetyError):
            channel_snapshot(source, 1, select=False)
        self.assertEqual(source.calls, ['INST:NSEL?'])
        with self.assertRaises(SafetyError):
            channel_snapshot(source, 1)
        self.assertEqual(source.calls[-2:], ['INST:NSEL 1', 'INST:NSEL?'])

    def test_terminal_prints_successful_writes_between_error_queries(self):
        """Terminal should never conceal a write that separates two error checks."""
        with tempfile.TemporaryDirectory() as folder:
            source = FakeTransport()
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H01')
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(h01(source, evidence), 'PASS')
        displayed = stream.getvalue().splitlines()
        sent_writes = [command for command in source.cmds if not command.endswith('?')]
        rendered_writes = [line.removeprefix('SCPI WRITE: ') for line in displayed
                           if line.startswith('SCPI WRITE: ')]
        self.assertEqual(rendered_writes, sent_writes)
        self.assertTrue(any(line == 'SCPI WRITE: INST:NSEL 2' for line in displayed))
        self.assertEqual(displayed.count('SCPI WRITE: SYST:LOC'), 1)

    def test_h02_profile_only_sequence_guards_each_setpoint_and_final_off(self):
        cmds = self._capture('H02', lambda fake, evidence: h02(fake, evidence, profile_only=True))
        self.assertEqual(cmds[-1], 'SYST:LOC')
        self.assertIn('OUTP 0', cmds)
        self.assertGreater(cmds.index('OUTP 0'), cmds.index('CHAN:OUTP?'))
        for ch, (maximum_v, _, voltage, current) in H02_CHANNELS.items():
            self.assertIn(f'VOLT:LIM {maximum_v:.2f}', cmds)
            self.assertIn(f'VOLT {voltage:.2f}', cmds)
            self.assertIn(f'CURR {current:.3f}', cmds)
        self.assertTrue(any(c == '*IDN?' for c in cmds))
        self.assertGreaterEqual(cmds.count('CHAN:OUTP?'), 3)

    def test_full_h02_panel_and_debug_sequence_has_one_error_poll_per_wire_operation(self):
        """The FULL H02 path must obey the same wire rules as profile-only."""
        class LimitSource(FakeTransport):
            def __init__(self):
                super().__init__()
                self.pending_error = None
                self.idn_reads = 0

            def write(self, command):
                if command in H02_HARD_PROBES.values():
                    self.cmds.append(command)
                    self.pending_error = '120,"Parameter overflowed"'
                    return
                super().write(command)

            def query(self, command):
                if command == 'SYST:ERR?' and self.pending_error is not None:
                    self.cmds.append(command)
                    answer, self.pending_error = self.pending_error, None
                    return answer
                if command == '*IDN?':
                    self.idn_reads += 1
                    answer = super().query(command)
                    if self.idn_reads > 1:
                        return answer.replace('1.04-1.04', '1.04-0.01')
                    return answer
                return super().query(command)

        source = LimitSource()
        approvals = []

        def approve(token, _prompt, _adapter=None):
            approvals.append(token)
            if token == H02_PANEL_TOKEN:
                for ch, setting in ((1, 2.0), (2, 3.0), (3, 4.0)):
                    source.values[ch]['VOLT'] = setting

        with tempfile.TemporaryDirectory() as folder:
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H02')
            with patch('common.operator_gate', side_effect=approve), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h02(source, evidence, profile_only=False)
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            wire_review = audit(evidence.path)

        self.assertEqual(wire_review['status'], 'PASS', wire_review['issues'])
        self.assertEqual(wire_review['observed_error_codes'], [120, 120, 120])
        self.assertEqual(wire_review['aux_drift_events'], 1)
        self.assertEqual(outcome, 'PASS', [e for e in events if e['event'] == 'stage_error'])
        self.assertEqual(approvals, [H02_PANEL_TOKEN, H02_DEBUG_TOKEN])
        self.assertTrue(source.closed)
        cmds = source.cmds
        trace = [e['trace']['command'] for e in events if e['event'] == 'driver_trace'
                 and e['trace']['event'] in ('write', 'query')]
        self.assertEqual(trace, cmds)
        self.assertEqual(cmds[-1], 'SYST:LOC')
        core = [command for command in cmds if command != 'SYST:ERR?']
        self.assertFalse(any(a == b == 'INST:NSEL?' for a, b in zip(core, core[1:])),
                         'Full H02 final selection was queried twice without a state change')
        self.assertEqual(cmds.count('SYST:ERR?'),
                         sum(cmd not in ('SYST:ERR?', 'SYST:LOC') for cmd in cmds))
        for i, cmd in enumerate(cmds):
            if cmd not in ('SYST:ERR?', 'SYST:LOC'):
                self.assertLess(i + 1, len(cmds))
                self.assertEqual(cmds[i + 1], 'SYST:ERR?', (i, cmd))
            if cmd == 'SYST:ERR?' and i:
                self.assertNotEqual(cmds[i - 1], 'SYST:ERR?', i)
        self.assertFalse(any(c in ('OUTP 1', 'CHAN:OUTP 1', '*CLS', '*RST') for c in cmds))
        self.assertEqual([cmds.count(H02_HARD_PROBES[ch]) for ch in (1, 2, 3)], [1, 1, 1])
        for ch in (1, 2, 3):
            index = cmds.index(H02_HARD_PROBES[ch])
            self.assertEqual(cmds[index + 1], 'SYST:ERR?')
        self.assertEqual(len([e for e in events
                              if e['event'] == 'expected_instrument_rejection']), 3)
        self.assertEqual(len([e for e in events
                              if e['event'] == 'firmware_revision_drift']), 1)
        self.assertEqual(events[-1]['firmware_observations'], ['1.04-1.04', '1.04-0.01'])
        self.assertTrue(all(source.values[ch]['CHAN:OUTP'] == 0 for ch in (1, 2, 3)))

    def test_saved_wire_evidence_auditor_distinguishes_hidden_writes_from_duplicate_polls(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fake = FakeTransport()
            evidence = Evidence(root / 'events.jsonl', 'H01')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h01(fake, evidence), 'PASS')
            summary = audit(evidence.path)
            self.assertEqual(summary['status'], 'PASS', summary['issues'])
            self.assertGreater(summary['wire_commands'], summary['error_polls'])
            # A real duplicate is not disguised as a missing terminal write.
            data = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            anchor = next(i for i, row in enumerate(data)
                          if row.get('event') == 'driver_trace'
                          and row.get('trace', {}).get('event') == 'query'
                          and row['trace'].get('command') == 'SYST:ERR?')
            # Insert one extra recorded wire poll and matching stage/read data.
            twin = [dict(event='driver_trace', trace={'event': 'query', 'command': 'SYST:ERR?'},
                         test_id='H01'),
                    dict(event='query', command='SYST:ERR?', test_id='H01'),
                    dict(event='read', command='SYST:ERR?', response='0,No error', test_id='H01')]
            data[anchor:anchor] = twin
            evidence.path.write_text(''.join(json.dumps(row) + '\n' for row in data))
            self.assertEqual(audit(evidence.path)['status'], 'FAIL')

    def test_legacy_h02_duplicate_requires_explicit_review_and_exact_panel_anchor(self):
        """Pre-fix saved H02 must never be reported as a clean modern PASS."""
        def event(event_name, command, *, query=False):
            return [
                {'test_id': 'H02', 'event': 'driver_trace',
                 'trace': {'event': 'query' if query else 'write', 'command': command}},
                {'test_id': 'H02', 'event': 'query' if query else 'write', 'command': command},
            ] + ([{'test_id': 'H02', 'event': 'read', 'command': command,
                   'response': '0,"No error"'}] if query and command == 'SYST:ERR?' else [])

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'historical_h02.jsonl'
            entries = event('write', 'SYST:LOC')
            entries += [{'test_id': 'H02', 'event': 'approval',
                         'phase': 'front_panel_max_voltage'}]
            entries += event('write', 'SYST:REM')
            entries += event('query', 'SYST:ERR?', query=True)
            entries += event('query', 'SYST:ERR?', query=True)
            entries += event('query', 'OUTP:SER?', query=True)
            entries += event('query', 'SYST:ERR?', query=True)
            entries += [{'test_id': 'H02', 'event': 'query_outcome', 'outcome': 'PASS'}]
            def save():
                path.write_text(''.join(json.dumps(e) + '\n' for e in entries))
            save()
            strict = audit(path)
            self.assertEqual(strict['status'], 'FAIL')
            self.assertTrue(any('Duplicate adjacent' in issue for issue in strict['issues']))
            reviewed = audit(path, acknowledge_historical_h02_panel_poll=True)
            self.assertEqual(reviewed['status'], 'HISTORICAL_DEVIATION_REVIEWED')
            self.assertEqual(len(reviewed['historical_deviations']), 1)
            self.assertEqual(reviewed['issues'], [])
            with contextlib.redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(audit_main(['--acknowledge-historical-h02-panel-poll', str(path)]), 0)
            self.assertIn('HISTORICAL_DEVIATION_REVIEWED', capture.getvalue())
            # Explicit flag never relaxes other duplicates or missing approvals.
            entries.pop(2)  # removes the panel approval
            save()
            self.assertEqual(audit(path, acknowledge_historical_h02_panel_poll=True)['status'], 'FAIL')
            entries.insert(2, {'test_id': 'H02', 'event': 'approval',
                               'phase': 'front_panel_max_voltage'})
            entries += event('query', 'SYST:ERR?', query=True)
            save()
            self.assertEqual(audit(path, acknowledge_historical_h02_panel_poll=True)['status'], 'FAIL')

    def test_h01_malformed_status_aborts_without_even_an_error_query(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            original = fake.query
            def invalid(command):
                if command == 'SYST:VERS?':
                    fake.cmds.append(command)
                    return 'not-a-number'
                return original(command)
            fake.query = invalid
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H01')
            with contextlib.redirect_stdout(io.StringIO()):
                result = h01(fake, evidence)
            events = evidence.path.read_text()
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(fake.cmds[-1], 'SYST:VERS?')
        self.assertIn('invalid_scpi_reply', events)
        self.assertTrue(fake.closed)

    def test_h02_malformed_readback_aborts_without_shutdown_on_bad_link(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            original = fake.query
            def invalid(command):
                if command == 'VOLT?':
                    fake.cmds.append(command)
                    return 'garbled'
                return original(command)
            fake.query = invalid
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H02')
            with contextlib.redirect_stdout(io.StringIO()):
                result = h02(fake, evidence, profile_only=True)
            events = evidence.path.read_text()
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(fake.cmds[-1], 'VOLT?')
        self.assertNotIn('VOLT 1.00', fake.cmds)
        self.assertIn('invalid_scpi_reply', events)
        self.assertTrue(fake.closed)

    def test_h02_bad_error_queue_reply_aborts_before_next_scpi(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            original = fake.query
            def invalid(command):
                if command == 'SYST:ERR?':
                    fake.cmds.append(command)
                    return 'broken'
                return original(command)
            fake.query = invalid
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H02')
            with contextlib.redirect_stdout(io.StringIO()):
                result = h02(fake, evidence, profile_only=True)
            events = evidence.path.read_text()
        self.assertEqual(result, 'ABORTED')
        self.assertEqual(fake.cmds, ['SYST:REM', 'SYST:ERR?'])
        self.assertIn('invalid_scpi_reply', events)
        self.assertTrue(fake.closed)

    def test_aux_revision_change_is_not_hidden_and_serial_is_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H02')
            evidence.add('read', command='*IDN?',
                         response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-1.04')
            evidence.add('read', command='*IDN?',
                         response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-0.01')
            evidence.add('query_outcome', outcome='PASS')
            content = evidence.path.read_text()
        events = [json.loads(line) for line in content.splitlines()]
        drifts = [item for item in events if item['event'] == 'firmware_revision_drift']
        self.assertEqual(len(drifts), 1)
        self.assertEqual((drifts[0]['before'], drifts[0]['after']), ('1.04-1.04', '1.04-0.01'))
        self.assertIn('OBSERVED_ONLY', drifts[0]['classification'])
        self.assertEqual(events[-1]['firmware_observations'], ['1.04-1.04', '1.04-0.01'])
        self.assertNotIn('PRIVATE123', content)


if __name__ == '__main__':
    unittest.main()
