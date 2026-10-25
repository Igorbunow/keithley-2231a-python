"""Regression checks for qualification gates and evidence, using simulated I/O."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class TestQualification(unittest.TestCase):
    def module(self):
        import qualification
        return qualification

    def test_dry_run_never_constructs_transport(self):
        q = self.module()
        for args in (['list'], ['preflight', 'H01'], ['run', 'H01'],
                     ['run', 'H09'], ['abort'], ['report']):
            with self.subTest(args=args), patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
                with contextlib.redirect_stdout(io.StringIO()):
                    q.main(args)

    def test_unattended_and_future_stages_reject_before_open(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H03', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', 'yes']):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)

    def test_h01_validates_off_identity_and_error_without_output_writes(self):
        q = self.module()
        from test_driver import FakeTransport
        class ProbeFake(FakeTransport):
            def query(self, command):
                if command == 'INST:NSEL?': return '1'
                if command == 'OUTP:ENABLE?': return '1'
                return super().query(command)
        for bad in ('none', 'on', 'model', 'error', 'timeout'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                fake = ProbeFake()
                base = fake.query
                def query(command):
                    if bad == 'on' and command == 'STAT:OPER:INST:ISUM2:COND?': return '8'
                    if bad == 'model' and command == '*IDN?': return 'Other, MODEL, SECRET, fw'
                    if bad == 'error' and command == 'SYST:ERR?': return '-100,"Command error"'
                    if bad == 'timeout' and command == 'SYST:VERS?': raise OSError('link lost')
                    return base(command)
                fake.query = query
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = q.h01(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL')
                content = evidence.path.read_text()
                self.assertNotIn('SECRET', content)
                self.assertNotIn(', TEST,', content)
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ', 'INST:NSEL ')) for cmd in fake.cmds))
                if bad == 'timeout': self.assertNotIn('SYST:ERR?', fake.cmds)

    def test_evidence_redacts_before_serializing(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-1.04')
            self.assertNotIn('PRIVATE123', evidence.path.read_text())
            self.assertIn('1.04-1.04', evidence.path.read_text())
            rendered = io.StringIO()
            with contextlib.redirect_stdout(rendered): q.report(evidence.path)
            self.assertNotIn('PRIVATE123', rendered.getvalue())
            self.assertIn('1.04-1.04', rendered.getvalue())
