"""OFF-only AUX channel correlation: fake transports, no physical hardware."""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

from qualification import Evidence, main
from aux_revision_stage import SELECTION_PLAN, run_sequence
from test_driver import FakeTransport
from tools.audit_hw_scpi import audit_aux_revision_records, audit


class AuxChannelSource(FakeTransport):
    def __init__(self):
        super().__init__()
        self.revisions = {1: '1.04-1.04', 2: '1.04-1.04', 3: '1.04-0.01'}

    def query(self, command):
        if command == '*IDN?':
            self.cmds.append(command)
            return f'Keithley instruments, 2231A-30-3, TEST, {self.revisions[self.active_ch]}'
        return super().query(command)


class AuxRevisionTests(unittest.TestCase):
    def capture(self, source=None):
        source = source or AuxChannelSource()
        with tempfile.TemporaryDirectory() as folder:
            evidence = Evidence(Path(folder) / 'events.jsonl', 'H01_AUX_REVISION')
            with redirect_stdout(StringIO()):
                outcome = run_sequence(source, evidence)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            return outcome, source, records

    def test_observations_track_selected_channel_and_restore_local(self):
        outcome, source, records = self.capture()
        self.assertEqual(outcome, 'PASS')
        samples = [r for r in records if r['event'] == 'aux_channel_observation']
        self.assertEqual([r['selected_channel'] for r in samples], list(SELECTION_PLAN))
        self.assertEqual([r['revision'] for r in samples],
                         [source.revisions[ch] for ch in SELECTION_PLAN])
        self.assertTrue(all(r['output_off_verified'] for r in samples))
        self.assertEqual(source.active_ch, 1)
        self.assertTrue(source.closed)
        self.assertEqual(source.cmds[-1], 'SYST:LOC')
        self.assertEqual([r['event'] for r in records].count('query_outcome'), 1)
        self.assertTrue(any(r['event'] == 'firmware_revision_drift' for r in records))
        # In the bare runner no operator/gate/overall outcome has yet happened;
        # test audit's full integration in the CLI test below.
        self.assertTrue(all(r['response'].endswith('[REDACTED], 1.04-0.01')
                            or '[REDACTED]' in r['response']
                            for r in records if r['event'] == 'read' and r['command'] == '*IDN?'))
        forbidden = ('OUTP 0', 'OUTP 1', 'CHAN:OUTP 0', 'CHAN:OUTP 1', '*CLS', '*RST')
        self.assertFalse(set(source.cmds) & set(forbidden))
        self.assertFalse(any(a == b == 'SYST:ERR?' for a, b in zip(source.cmds, source.cmds[1:])))
        for i, command in enumerate(source.cmds[:-1]):
            if command not in ('SYST:ERR?', 'SYST:LOC'):
                self.assertEqual(source.cmds[i+1], 'SYST:ERR?', (i, command))

    def test_initial_on_never_starts_sampling_or_mutates_outputs(self):
        source = AuxChannelSource()
        source.values[2]['CHAN:OUTP'] = 1
        outcome, source, records = self.capture(source)
        self.assertEqual(outcome, 'FAIL')
        self.assertFalse(any(r['event'] == 'aux_channel_observation' for r in records))
        self.assertFalse(any(c in ('OUTP 0', 'OUTP 1', 'CHAN:OUTP 0', 'CHAN:OUTP 1') for c in source.cmds))
        self.assertTrue(source.closed)

    def test_broken_link_has_no_further_commands_or_restore(self):
        class Broken(AuxChannelSource):
            def query(self, command):
                if command == '*IDN?' and self.active_ch == 3:
                    self.cmds.append(command)
                    raise TimeoutError('simulated link failure')
                return super().query(command)
        outcome, source, records = self.capture(Broken())
        self.assertEqual(outcome, 'ABORTED')
        self.assertEqual(source.cmds[-1], '*IDN?')
        self.assertTrue(source.closed)
        self.assertFalse(any(c == 'SYST:LOC' for c in source.cmds))
        self.assertFalse(any(r['event'] == 'aux_channel_observation' and r['selected_channel'] == 3
                             for r in records))

    def test_selected_channel_mismatch_blocks_identity_sampling(self):
        class Misselect(AuxChannelSource):
            def __init__(self):
                super().__init__()
                self.bad = False
            def write(self, command):
                super().write(command)
                if command == 'INST:NSEL 3':
                    self.bad = True
            def query(self, command):
                if command == 'INST:NSEL?' and self.bad:
                    self.cmds.append(command)
                    return '2'
                return super().query(command)
        outcome, source, records = self.capture(Misselect())
        self.assertEqual(outcome, 'ABORTED')
        self.assertNotEqual(source.cmds[-1], 'SYST:LOC')
        self.assertFalse(any(r['event'] == 'aux_channel_observation' and r['selected_channel'] == 3
                             for r in records))

    def test_unexpected_remote_on_bit_aborts_without_next_identity_read(self):
        class UnexpectedOn(AuxChannelSource):
            def query(self, command):
                if command == 'STAT:OPER:INST:ISUM2:COND?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '8'
                return super().query(command)
        outcome, source, records = self.capture(UnexpectedOn())
        self.assertEqual(outcome, 'FAIL')
        self.assertFalse(any(r['event']=='aux_channel_observation' and r['selected_channel']==3
                             for r in records))
        self.assertFalse(any(c.startswith('CHAN:OUTP ') or c.startswith('OUTP ') for c in source.cmds))

    def test_main_cli_live_simulated_gates_and_evidence(self):
        import aux_revision_stage
        from qualification import EXAMPLE
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            local = root / 'bench.local.toml'
            local.write_bytes(EXAMPLE.read_bytes())
            source = AuxChannelSource()
            tokens = []
            def operator(prompt):
                found = [line for line in prompt.splitlines() if line.startswith('CONFIRM ')]
                if len(found) != 1:
                    raise AssertionError('Ambiguous confirmation prompt')
                tokens.append(found[0])
                return found[0]
            with mock.patch.object(aux_revision_stage.sys.stdin, 'isatty', return_value=True), \
                 mock.patch.object(aux_revision_stage, 'verify_serial_adapter', return_value=True) as usb, \
                 mock.patch.object(aux_revision_stage, 'open_psu', return_value=source) as opened, \
                 mock.patch('qualification.ROOT', root), \
                 mock.patch('qualification.software_metadata', return_value={'sha':'fake','dirty':True}), \
                 mock.patch('builtins.input', side_effect=operator), redirect_stdout(StringIO()):
                outcome = main(['run','H01','--aux-revision-diagnostic',
                                '--config',str(local),'--execute'])
            self.assertEqual(outcome, 0)
            usb.assert_called_once()
            opened.assert_called_once()
            self.assertEqual(len(tokens), 3)
            self.assertIn('H01-AUX-REVISION', tokens[0])
            evidence_paths = list((root/'reports/hardware').glob('*/events.jsonl'))
            self.assertEqual(len(evidence_paths), 1)
            records = [json.loads(line) for line in evidence_paths[0].read_text().splitlines()]
            self.assertEqual(records[-1]['event'], 'outcome')
            self.assertEqual(records[-1]['outcome'], 'PASS')
            self.assertTrue(records[-1]['physical_off_confirmed'])
            self.assertEqual(audit_aux_revision_records(records)['status'], 'PASS')
            self.assertEqual(audit(evidence_paths[0])['status'], 'PASS')
            tampered = [dict(record) for record in records]
            tampered[-1] = dict(tampered[-1], physical_off_confirmed=False)
            self.assertEqual(audit_aux_revision_records(tampered)['status'], 'FAIL')
            self.assertEqual(len([r for r in records if r['event']=='aux_channel_observation']),
                             len(SELECTION_PLAN))
            self.assertEqual(len(list((root/'docs/hardware_reports').glob('*.md'))), 1)

    def test_cli_dry_run_and_wrong_variants_are_offline(self):
        with mock.patch('aux_revision_stage.open_psu', side_effect=AssertionError('Opened hardware')):
            with redirect_stdout(StringIO()) as out:
                self.assertEqual(main(['preflight','H01','--aux-revision-diagnostic']), 0)
                self.assertEqual(main(['run','H01','--aux-revision-diagnostic']), 0)
            self.assertIn('DRY RUN', out.getvalue())
            self.assertIn('H01-AUX-REVISION', out.getvalue())
            with self.assertRaises(SystemExit):
                main(['run','H03','--aux-revision-diagnostic'])
            with self.assertRaises(SystemExit):
                main(['run','H01','--aux-revision-diagnostic','--profile-only'])

    def test_execute_noninteractive_never_opens_or_lists_usb(self):
        with mock.patch('aux_revision_stage.open_psu', side_effect=AssertionError('open')) as opened, \
             mock.patch('aux_revision_stage.verify_serial_adapter', side_effect=AssertionError('usb')) as usb, \
             mock.patch('aux_revision_stage.sys.stdin.isatty', return_value=False), \
             redirect_stdout(StringIO()):
            self.assertEqual(main(['run','H01','--aux-revision-diagnostic',
                                   '--config','config/bench.example.toml','--execute']), 2)
        opened.assert_not_called()
        usb.assert_not_called()


if __name__ == '__main__':
    unittest.main()
