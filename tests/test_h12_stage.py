"""Offline H12 scenarios using the existing stateful SCPI emulator.

@file test_h12_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Verify OFF-only runner, fault latching and explicit new-session recovery.
@details No physical serial port or network endpoint is opened.
"""
import contextlib
import io
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parent / 'hardware'))
from h12_stage import H12Scope, h12, safe_profile
from keithley2231a import Keithley2231A, SafetyError, TransportError
from test_driver import FakeTransport


class Evidence:
    """Collect stage assertions without creating hardware evidence files."""
    def __init__(self):
        self.events = []

    def add(self, event, **fields):
        record = dict(event=event, **fields)
        self.events.append(record)
        return record


class TestH12Stage(unittest.TestCase):
    def test_off_only_runner_rejects_arguments_and_verifies_final_state(self):
        fake, evidence = FakeTransport(), Evidence()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(h12(fake, evidence), 'PASS')
        rejections = [e for e in evidence.events if e['event'] == 'expected_api_rejection']
        self.assertEqual(len(rejections), 12)
        self.assertTrue(all(e['wire_commands'] == 0 for e in rejections))
        self.assertFalse(fake.remote)
        self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
        for ch, spec in enumerate(safe_profile().channels, 1):
            self.assertEqual(fake.values[ch]['VOLT'], spec.initial_voltage)
            self.assertEqual(fake.values[ch]['OUTP:TIM:STAT'], 0)
        self.assertFalse(any(cmd in ('OUTP 1', 'CHAN:OUTP 1') for cmd in fake.cmds))
        scope = H12Scope(fake, evidence)
        for command in ('OUTP 1', 'CHAN:OUTP 1', 'OUTP:SER 1', 'SYST:RWLOCK'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                scope._validate(command, False)

    def test_readback_mismatch_trips_and_prevents_automatic_on(self):
        fake = FakeTransport()
        dev = Keithley2231A(fake)
        dev.apply_profile(safe_profile())
        fake.values[2]['VOLT:LIM'] = 3.1
        with self.assertRaises(SafetyError):
            dev.verify_profile()
        before = list(fake.cmds)
        with self.assertRaises(SafetyError):
            dev.output(1, True)
        self.assertEqual(fake.cmds, before)
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))

    def test_io_loss_forbids_old_session_and_new_session_stays_off(self):
        for debug in (False, True):
            with self.subTest(debug=debug):
                fake = FakeTransport()
                dev = Keithley2231A(fake, debug=debug)
                dev.apply_profile(safe_profile())
                dev.output(1, True)
                fake.failed_query = 'CHAN:OUTP?'
                with self.assertRaises(Exception):
                    dev.output_states()
                before = list(fake.cmds)
                fake.failed_query = None
                for operation in (lambda: dev.output(1, True),
                                  dev.emergency_off, lambda: dev.apply_profile(safe_profile())):
                    with self.assertRaises((SafetyError, TransportError)):
                        operation()
                    self.assertEqual(fake.cmds, before)
                # Link failure does not imply physical shutdown.
                self.assertEqual(fake.values[1]['CHAN:OUTP'], 1)
                fresh = FakeTransport()
                fresh.values[1]['CHAN:OUTP'] = 1.0
                recovered = Keithley2231A(fresh, debug=debug)
                with self.assertRaises(SafetyError):
                    recovered.output(1, True)
                recovered.apply_profile(safe_profile())
                self.assertEqual(recovered.output_states(), {1: False, 2: False, 3: False})
                self.assertNotIn('CHAN:OUTP 1', fresh.cmds)

    def test_stage_aborts_on_io_failure_and_never_reports_pass(self):
        fake, evidence = FakeTransport(), Evidence()
        fake.failed_query = 'CHAN:OUTP?'
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(h12(fake, evidence), 'ABORTED')
        self.assertIn('UNKNOWN OUTPUT STATE', output.getvalue())
        self.assertTrue(any(e['event'] == 'io_error' for e in evidence.events))
        self.assertNotIn('CHAN:OUTP 1', fake.cmds)


if __name__ == '__main__':
    unittest.main()
