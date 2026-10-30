"""R4.5-D: no live hardware, real PTY serial byte stream of one OFF STEP case."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest

from keithley2231a import SafetyError
from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_5_step_mutation_stage import (StepMutationScope, run_step_mutation,
    case_token, profile, validate_case, PROFILE_TOKEN, FINAL_TOKEN)


class Evidence:
    def __init__(self): self.events = []
    def add(self, event, **fields):
        item = {'event': event, **fields}
        self.events.append(item)
        return item


@unittest.skipUnless(os.name == 'posix', 'POSIX PTY required')
class StepMutationUART(unittest.TestCase):
    def execute(self, ch=1, kind='voltage', increment=.010, *, gate=None,
                mutation_gate=None, final_gate=None, evidence=None, **peer_kwargs):
        peer = UARTPeer(profile_registers=True, **peer_kwargs)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.13)
        ev = evidence if evidence is not None else Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            status = run_step_mutation(
                wire, ev, channel=ch, kind=kind, increment=increment,
                profile_gate=gate if gate is not None else lambda token, io: None,
                mutation_gate=mutation_gate if mutation_gate is not None else lambda token, io: None,
                final_gate=final_gate if final_gate is not None else lambda token: None)
        return status, peer, ev

    def test_voltage_ten_millivolt_off_ch1(self):
        state, peer, ev = self.execute(1, 'voltage', .010)
        self.assertEqual(state, 'PASS', ev.events[-1])
        self.assertTrue(peer.await_command('SYST:LOC'))
        self.assertEqual(peer.rx[-1], 'SYST:LOC')
        self.assertEqual(peer.rx.count('VOLT:STEP 0.010'), 1)
        self.assertEqual(peer.rx.count('VOLT:UP'), 2)
        self.assertEqual(peer.rx.count('VOLT:DOWN'), 2)
        self.assertFalse({'OUTP 1', 'CHAN:OUTP 1', '*TRG', '*RST', '*CLS'} & set(peer.rx))
        vals = next(e['values'] for e in ev.events if e['event'] == 'results')
        self.assertEqual([round(s['setpoint'], 3) for s in vals['steps']],
                         [3.310, 3.320, 3.310, 3.300])
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(c+'\r\n').encode('ascii') for c in peer.rx])

    def test_current_all_channels_and_multi_increment(self):
        for channel, increment in ((1,.001), (2,.005), (3,.010)):
            with self.subTest(channel=channel):
                status, peer, ev = self.execute(channel, 'current', increment)
                self.assertEqual(status, 'PASS', ev.events[-1])
                self.assertTrue(peer.await_command('SYST:LOC'))
                self.assertEqual(peer.rx.count(f'CURR:STEP {increment:.3f}'), 1)
                self.assertEqual(peer.rx.count('CURR:UP'), 2)
                self.assertEqual(peer.rx.count('CURR:DOWN'), 2)
                wanted = .1 if channel == 1 else .05
                vals = next(e['values'] for e in ev.events if e['event'] == 'results')
                self.assertAlmostEqual(vals['final_setpoint'], wanted)
                self.assertEqual(peer.unsupported, [])

    def test_voltage_other_channels_increment_fifty_and_hundred_millivolts(self):
        for channel, increment in ((2,.050), (3,.100)):
            with self.subTest(channel=channel):
                status, peer, ev = self.execute(channel, 'voltage', increment)
                self.assertEqual(status, 'PASS', ev.events[-1])
                vals = next(e['values'] for e in ev.events if e['event'] == 'results')
                self.assertAlmostEqual(vals['final_setpoint'], 1.0)
                self.assertTrue(peer.await_command('SYST:LOC'))
                self.assertEqual(peer.unsupported, [])

    def test_second_gate_denied_no_step_mutation(self):
        def deny(token, adapter): raise PermissionError('operator refused mutation')
        state, peer, ev = self.execute(mutation_gate=deny)
        self.assertEqual(state, 'ABORTED')
        self.assertIn('OUTP 0', peer.rx)
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertNotIn('VOLT:STEP 0.010', peer.rx)
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_profile_gate_denied_without_profile_writes(self):
        def deny(token, adapter): raise PermissionError('operator refused profile')
        state, peer, ev = self.execute(gate=deny)
        self.assertEqual(state, 'ABORTED')
        self.assertNotIn('VOLT 0', peer.rx)
        self.assertNotIn('OUTP 0', peer.rx)
        self.assertNotIn('VOLT:STEP 0.010', peer.rx)

    def test_any_initial_output_on_blocks_profile(self):
        for channel in (1,2,3):
            with self.subTest(channel=channel):
                state, peer, ev = self.execute(off_by_channel={channel:1})
                self.assertEqual(state, 'ABORTED')
                self.assertNotIn('VOLT 0', peer.rx)
                self.assertNotIn('VOLT:STEP 0.010', peer.rx)
                self.assertNotIn('SYST:LOC', peer.rx)

    def test_step_register_write_ignored_aborts_and_cleanup(self):
        state, peer, ev = self.execute(increment=.050, ignore_step_write=True)
        self.assertEqual(state, 'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:STEP 0.050'), 1)
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertNotIn('SYST:LOC', peer.rx)
        self.assertIn('OUTP 0', peer.rx)

    def test_up_action_ignored_aborts_not_false_pass(self):
        state, peer, ev = self.execute(ignore_step_action=True)
        self.assertEqual(state, 'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:UP'), 1)
        self.assertNotIn('SYST:LOC', peer.rx)
        self.assertFalse(any(e['event']=='outcome' and e['outcome']=='PASS' for e in ev.events))

    def test_invalid_reply_blocks_any_followup_on_uncertain_uart(self):
        state, peer, ev = self.execute(response_overrides={'VOLT:STEP?': 'garbled'})
        self.assertEqual(state, 'ABORTED')
        self.assertTrue(peer.await_command('VOLT:STEP?'))
        self.assertEqual(peer.rx[-1], 'VOLT:STEP?')
        self.assertNotIn('SYST:LOC', peer.rx)
        self.assertFalse(ev.events[-1]['link_known'])

    def test_uart_timeout_no_retry_or_auto_shutdown_commands(self):
        state, peer, ev = self.execute(drop='VOLT:STEP?')
        self.assertEqual(state, 'ABORTED')
        self.assertEqual(peer.rx.count('VOLT:STEP?'), 1)
        self.assertEqual(peer.rx[-1], 'VOLT:STEP?')
        self.assertFalse(ev.events[-1]['link_known'])

    def test_scpi_queue_error_aborts_without_next_action(self):
        state, peer, ev = self.execute(fault_after='VOLT:STEP 0.010')
        self.assertEqual(state, 'ABORTED')
        self.assertNotIn('VOLT:UP', peer.rx)
        self.assertNotIn('SYST:LOC', peer.rx)

    def test_final_abort_gate_is_recorded_even_when_run_aborts(self):
        status, peer, ev = self.execute(fault_after='VOLT:STEP 0.010')
        self.assertEqual(status, 'ABORTED')
        # Driver may trip before the stage can itself verify digital OFF.
        # The independent physical gate must still be reported faithfully.
        self.assertTrue(ev.events[-1]['physical_off_confirmed'])
        self.assertTrue(peer.await_command('OUTP 0'))
        self.assertNotIn('VOLT:UP', peer.rx)

    def test_physical_final_gate_refusal_no_pass(self):
        def reject(_): raise PermissionError('operator could not confirm OFF')
        state, peer, ev = self.execute(final_gate=reject)
        self.assertEqual(state, 'ABORTED')
        self.assertFalse(ev.events[-1]['physical_off_confirmed'])
        self.assertNotIn('OUTP 1', peer.rx)

    def test_logger_fails_but_known_link_shutdown_still_attempted(self):
        class FailEvidence(Evidence):
            def add(self, event, **kwargs):
                if event == 'results': raise OSError('disk full')
                return super().add(event, **kwargs)
        state, peer, ev = self.execute(evidence=FailEvidence())
        self.assertEqual(state, 'ABORTED')
        self.assertIn('OUTP 0', peer.rx)
        self.assertFalse(any(e['event']=='outcome' and e['outcome']=='PASS' for e in ev.events))

    def test_stage_scope_rejects_every_external_on_or_other_step(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.12)
        self.addCleanup(wire.close)
        scope = StepMutationScope(wire, Evidence(), channel=1, kind='voltage', increment=.010)
        for cmd in ('OUTP 1', 'CHAN:OUTP 1', '*TRG', 'VOLT:STEP 0.100',
                    'CURR:STEP 0.010', 'VOLT:UP', 'CURR:UP'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError): scope.write(cmd)
        for cmd in ('VOLT:STEP?', 'CURR:STEP?', 'MEAS:POW?', '*OPC?'):
            with self.subTest(cmd=cmd), self.assertRaises(SafetyError): scope.query(cmd)
        self.assertEqual(peer.rx, [])

    def test_invalid_case_rejected_before_hardware(self):
        for channel, kind, increment in ((4,'voltage',.010), (True,'voltage',.010),
                                        (1,'power',.010),(1,'voltage',.500),
                                        (1,'current',.050),(1,'voltage',None),(1,'voltage',.001),(1,'voltage',.005)):
            with self.assertRaises(ValueError): validate_case(channel, kind, increment)
        self.assertIn('CH2', case_token(2, 'current', .005))
        self.assertEqual(profile().channels[1].initial_current, .050)

    def test_cli_dry_run_no_open_and_no_execute_on_example(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT/'src')+':'+str(ROOT/'tests/hardware'))
        args = [sys.executable, str(ROOT/'tools/r4_5_step_mutation_qualify.py'),
                '--config', str(ROOT/'config/bench.example.toml')]
        p = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('DRY RUN:', p.stdout)
        self.assertIn('0.010', p.stdout)
        p2 = subprocess.run(args + ['--execute'], env=env, capture_output=True,
                            text=True, timeout=15)
        self.assertNotEqual(p2.returncode, 0)
        self.assertIn('Example bench', p2.stderr)
        p3 = subprocess.run(args + ['--increment','.500'], env=env, capture_output=True,
                            text=True, timeout=15)
        self.assertNotEqual(p3.returncode, 0)


if __name__ == '__main__':
    unittest.main()
