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
                     ['preflight', 'H02'], ['run', 'H02'],
                     ['run', 'H09'], ['abort'], ['report']):
            with self.subTest(args=args), patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
                with contextlib.redirect_stdout(io.StringIO()):
                    q.main(args)

    def test_unattended_and_future_stages_reject_before_open(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H03', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', 'yes']):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', q.TOKEN]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)

    def test_h02_profile_trace_and_stop_on_first_error(self):
        """Exercise the exact profile trace using simulated I/O only."""
        q = self.module()
        from profile_stage import CheckedIO, command_plan, h02
        from test_driver import FakeTransport
        for bad in ('none', 'error170', 'timeout', 'cap_mismatch', 'initial_on', 'identity_change'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                class ProfileFake(FakeTransport):
                    pending_error = False
                    reduced_cap_written = False
                    idn_count = 0
                    def write(self, command):
                        super().write(command)
                        if bad == 'error170' and command == 'CURR 0': self.pending_error = True
                        if command == 'VOLT:LIM 4.00': self.reduced_cap_written = True
                    def query(self, command):
                        if bad == 'identity_change' and command == '*IDN?':
                            self.idn_count += 1
                            if self.idn_count == 2:
                                self.cmds.append(command)
                                return 'Keithley instruments, 2231A-30-3, TEST, 1.04-0.01'
                        answer = super().query(command)
                        if command == 'SYST:ERR?' and self.pending_error:
                            self.pending_error = False
                            return '170,"Invalid command"'
                        if command == 'VOLT:LIM?' and self.reduced_cap_written:
                            if bad == 'timeout': raise OSError('simulated uncertain link')
                            if bad == 'cap_mismatch': return '3.99'
                        return answer
                fake = ProfileFake()
                if bad == 'initial_on': fake.values[2]['CHAN:OUTP'] = 1.
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H02')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = h02(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL')
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd in ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK', '*CLS', '*RST') for cmd in fake.cmds))
                if bad == 'none':
                    self.assertEqual(tuple(fake.cmds), command_plan())
                    self.assertEqual(fake.active_ch, 1)
                    for ch in (1, 2, 3):
                        self.assertEqual(fake.values[ch]['VOLT'], 1.)
                        self.assertEqual(fake.values[ch]['CURR'], .1)
                        self.assertEqual(fake.values[ch]['VOLT:LIM'], 4.)
                        self.assertEqual(fake.values[ch]['VOLT:LIM:STAT'], 1.)
                        self.assertEqual(fake.values[ch]['CHAN:OUTP'], 0.)
                    self.assertEqual(evidence.path.read_text().count('"event": "case"'), 21)
                if bad == 'timeout': self.assertEqual(fake.cmds[-1], 'VOLT:LIM?')
                if bad in ('error170', 'initial_on', 'identity_change'):
                    self.assertNotIn('VOLT:LIM 4.00', fake.cmds)
                self.assertNotIn(', TEST,', evidence.path.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            io_guard = CheckedIO(fake, q.Evidence(Path(tmp) / 'events.jsonl', 'H02'))
            with self.assertRaises(ValueError): io_guard.write('OUTP 1')
            self.assertEqual(fake.cmds, [])
            self.assertEqual(io_guard.attempts, 1)
        with tempfile.TemporaryDirectory() as tmp:
            from keithley2231a import SafetyError
            fake = FakeTransport()
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H02')
            def broken_output(dev, channel, enabled):
                try: dev._io.write('OUTP 1')
                except ValueError: raise SafetyError('simulated driver relying on stage guard')
            with patch('profile_stage.Keithley2231A.output', new=broken_output):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(h02(fake, evidence), 'FAIL')
            self.assertNotIn('OUTP 1', fake.cmds)

    def test_h01_validates_off_identity_and_error_without_output_writes(self):
        q = self.module()
        from test_driver import FakeTransport
        class ProbeFake(FakeTransport):
            def query(self, command):
                if command == 'INST:NSEL?': return '1'
                if command == 'OUTP:ENABLE?': return '1'
                return super().query(command)
        for bad in ('none', 'old170', 'new170', 'on', 'model', 'error', 'timeout'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                fake = ProbeFake()
                base = fake.query
                error_reads = 0
                def query(command):
                    nonlocal error_reads
                    if command == 'SYST:ERR?':
                        error_reads += 1
                        if bad == 'old170' and error_reads == 1: return '170,"Invalid command"'
                        if bad == 'new170' and error_reads == 2: return '170,"Invalid command"'
                    if bad == 'on' and command == 'STAT:OPER:INST:ISUM2:COND?': return '8'
                    if bad == 'model' and command == '*IDN?': return 'Other, MODEL, SECRET, fw'
                    if bad == 'error' and command == 'SYST:ERR?': return '-100,"Command error"'
                    if bad == 'timeout' and command == 'SYST:VERS?': raise OSError('link lost')
                    return base(command)
                fake.query = query
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = q.h01(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad in ('none', 'old170') else 'ABORTED' if bad == 'timeout' else 'FAIL')
                content = evidence.path.read_text()
                self.assertNotIn('SECRET', content)
                self.assertNotIn(', TEST,', content)
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ', 'INST:NSEL ')) for cmd in fake.cmds))
                if bad == 'timeout':
                    self.assertEqual(error_reads, 1)  # No follow-up query on the failed link.
                    self.assertIn('io_error', content)
                    self.assertIn('OSError', content)
                if bad == 'old170': self.assertIn('preexisting_error', content)

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
