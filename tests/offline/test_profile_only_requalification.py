"""Offline evidence that R3.5.2 reuses H02 without full panel/debug probes."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualification
from bench_config import EXAMPLE, load_bench
from connection_gate import connection_token
from profile_stage import (H02_HARD_PROBES, PROFILE_ONLY_TOKEN,
                           H02_CHANNELS, h02)
from support.qualification_transport import FakeTransport


class ProfileOnlyTests(unittest.TestCase):
    def test_limited_mode_uses_shared_driver_and_never_runs_panel_or_debug(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            evidence = qualification.Evidence(Path(folder) / 'stage.jsonl', 'H02')
            with patch('builtins.input', side_effect=AssertionError('unexpected internal approval')), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = h02(fake, evidence, profile_only=True)
            self.assertEqual(result, 'PASS')
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertIn('profile_only', [event['variant'] for event in events
                                           if event['event'] == 'qualification_variant'])
            self.assertEqual([event for event in events if event['event'] == 'approval'], [])
            self.assertFalse(any(command in fake.cmds for command in H02_HARD_PROBES.values()))
            self.assertFalse(any(command in fake.cmds for command in
                                 ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK', '*CLS', '*RST')))
            self.assertTrue(fake.closed)
            self.assertTrue(all(fake.values[ch]['CHAN:OUTP'] == 0 for ch in (1, 2, 3)))
            for ch, (vmax, _imax, voltage, current) in H02_CHANNELS.items():
                self.assertAlmostEqual(fake.values[ch]['VOLT'], voltage)
                self.assertAlmostEqual(fake.values[ch]['CURR'], current)
                self.assertAlmostEqual(fake.values[ch]['VOLT:LIM'], vmax)
            wire_trace = [item['trace']['command'] for item in events
                          if item['event'] == 'driver_trace' and item['trace']['event'] in ('write', 'query')]
            self.assertEqual(wire_trace, fake.cmds)

    def test_limited_mode_aborts_if_initial_output_on(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            fake.values[1]['CHAN:OUTP'] = 1.0
            evidence = qualification.Evidence(Path(folder) / 'stage.jsonl', 'H02')
            with contextlib.redirect_stdout(io.StringIO()):
                result = h02(fake, evidence, profile_only=True)
            self.assertNotEqual(result, 'PASS')
            self.assertNotIn('VOLT 1.00', fake.cmds)
            self.assertNotIn('CHAN:OUTP 1', fake.cmds)
            self.assertTrue(fake.closed)

    def test_profile_only_cli_is_dry_and_rejects_other_stage_ids(self):
        cfg = load_bench(EXAMPLE)
        self.assertNotEqual(connection_token(cfg, 'H02'),
                            connection_token(cfg, 'H02', profile_only=True))
        self.assertTrue(connection_token(cfg, 'H02', profile_only=True)
                        .startswith('CONFIRM BENCH H02-PROFILE-ONLY'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(qualification.main(['preflight', 'H02', '--profile-only',
                                                 '--config', str(EXAMPLE)]), 0)
        self.assertIn('OFF-only Independent profile verification', out.getvalue())
        self.assertIn(PROFILE_ONLY_TOKEN, out.getvalue())
        self.assertNotIn('Panel gate:', out.getvalue())
        self.assertNotIn('Debug gate:', out.getvalue())
        self.assertIn('DRY RUN', out.getvalue())
        for command in ('H01', 'H03', 'H09'):
            with self.subTest(command=command), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    qualification.main(['preflight', command, '--profile-only'])
                self.assertEqual(caught.exception.code, 2)

    def test_limited_mode_is_not_full_h02_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeTransport()
            evidence = qualification.Evidence(Path(folder) / 'stage.jsonl', 'H02')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(h02(fake, evidence, profile_only=True), 'PASS')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertFalse(any(r.get('check') == 'debug bypass rejected by instrument voltage limit'
                                 for r in records))
            self.assertFalse(any(r.get('phase') == 'front_panel_max_voltage' for r in records))
            self.assertTrue(any(r.get('event') == 'qualification_variant' and
                                r.get('variant') == 'profile_only' for r in records))


if __name__ == '__main__':
    unittest.main()
