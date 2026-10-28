"""Offline H07 measurement and independent shutdown fault injection; no hardware I/O."""
from __future__ import annotations

import contextlib
import io
import math
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from keithley2231a import SafetyError
from h07_stage import _check_live_measurements, h07
from common import release_psu_and_load
from qualification import Evidence
from test_driver import FakeTransport


def readings():
    return [SimpleNamespace(enabled=(ch == 1), regulation='CV' if ch == 1 else 'OFF',
                            voltage_v=4.0 if ch == 1 else 0.0,
                            current_a=0.100 if ch == 1 else 0.0,
                            power_w=0.400 if ch == 1 else 0.0)
            for ch in (1, 2, 3)]


def measured():
    return {'kel103_voltage_v': 3.999, 'kel103_current_a': 0.1001,
            'kel103_power_w': 0.4003}


class H07GuardTests(unittest.TestCase):
    def test_normal_measurements_are_valid(self):
        _check_live_measurements(readings(), measured())

    def test_power_disagreement_fails_even_with_valid_u_i(self):
        wrong = dict(measured(), kel103_power_w=0.58)
        with self.assertRaisesRegex(SafetyError, 'mismatch'):
            _check_live_measurements(readings(), wrong)
        wrong_source = readings()
        wrong_source[0].power_w = 0.60
        with self.assertRaisesRegex(SafetyError, 'mismatch'):
            _check_live_measurements(wrong_source, measured())

    def test_cross_power_disagreement_fails_even_if_each_local_power_matches(self):
        # Both devices report internally consistent power, but disagree across
        # U/I within the still-permitted individual absolute tolerances.
        wrong = dict(kel103_voltage_v=3.945, kel103_current_a=0.078,
                     kel103_power_w=3.945 * 0.078)
        with self.assertRaisesRegex(SafetyError, 'mismatch'):
            _check_live_measurements(readings(), wrong)

    def test_nan_inf_wrong_type_and_negative_values_fail_closed(self):
        for bad in (float('nan'), float('inf'), -0.001, '0.4', True):
            with self.subTest(bad=bad):
                kel = dict(measured(), kel103_power_w=bad)
                with self.assertRaises(SafetyError):
                    _check_live_measurements(readings(), kel)
                source = readings()
                source[0].voltage_v = bad
                with self.assertRaises(SafetyError):
                    _check_live_measurements(source, measured())
        other = readings()
        other[1].voltage_v = float('nan')
        with self.assertRaises(SafetyError):
            _check_live_measurements(other, measured())

    def test_off_readback_neighbor_or_bad_power_fails(self):
        for ch, field, val in ((1, 'enabled', True), (2, 'current_a', 0.03),
                               (2, 'power_w', 0.15)):
            with self.subTest(channel=ch, field=field):
                source = readings()
                setattr(source[ch], field, val)
                with self.assertRaises(SafetyError):
                    _check_live_measurements(source, measured())

    @staticmethod
    def shutdown(*, psu_failure=False, load_failure=False, psu_live=True,
                 load_live=True, evidence_failure=False, off_readback=False):
        class PSU:
            def __init__(self): self.calls = []
            def emergency_off(self):
                self.calls.append('emergency_off')
                if psu_failure: raise OSError('PSU OFF failed')
        class Load:
            live = load_live
            state = 'ON'
            def __init__(self): self.calls = []
            def input_state(self):
                self.calls.append('input_state')
                return self.state
            def set_input(self, enabled):
                self.calls.append(('set_input', enabled))
                if load_failure: raise OSError('KEL OFF failed')
                self.state = 'ON' if enabled else 'OFF'
        class EvidenceSink:
            def __init__(self): self.events = []
            def add(self, *args, **kwargs):
                if evidence_failure: raise OSError('disk full')
                self.events.append((args, kwargs))
        psu, load, evidence = PSU(), Load(), EvidenceSink()
        io_adapter = SimpleNamespace(live=psu_live, cleanup=False)
        response = {1: bool(off_readback), 2: False, 3: False}
        with patch('common.require_outputs', return_value=response):
            failures = release_psu_and_load(psu, io_adapter, load, evidence)
        return failures, psu, load, evidence

    def test_independent_off_both_success(self):
        failures, psu, load, _ = self.shutdown()
        self.assertEqual(failures, [])
        self.assertEqual(psu.calls, ['emergency_off'])
        self.assertEqual(load.state, 'OFF')

    def test_psu_failure_does_not_skip_load_off(self):
        failures, psu, load, _ = self.shutdown(psu_failure=True)
        self.assertEqual(psu.calls, ['emergency_off'])
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(any('Keithley' in e for e in failures))

    def test_kel_off_failure_does_not_skip_psu(self):
        failures, psu, load, _ = self.shutdown(load_failure=True)
        self.assertIn('emergency_off', psu.calls)
        self.assertTrue(any('KEL103' in e for e in failures))

    def test_psu_uncertain_is_not_reused_but_load_off_is_attempted(self):
        failures, psu, load, _ = self.shutdown(psu_live=False)
        self.assertEqual(psu.calls, [])
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(failures)

    def test_load_uncertain_is_not_reused_but_psu_off_is_attempted(self):
        failures, psu, load, _ = self.shutdown(load_live=False)
        self.assertIn('emergency_off', psu.calls)
        self.assertEqual(load.calls, [])
        self.assertTrue(failures)

    def test_evidence_failure_cannot_suppress_shutdown(self):
        failures, psu, load, _ = self.shutdown(psu_failure=True, evidence_failure=True)
        self.assertIn('emergency_off', psu.calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(failures)

    def test_unexpected_psu_readback_never_counts_as_clean_shutdown(self):
        failures, psu, load, _ = self.shutdown(off_readback=True)
        self.assertIn('emergency_off', psu.calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(failures)


class H07FullRunnerFaults(unittest.TestCase):
    @staticmethod
    def run_fault(*, fail_evidence=False, fail_psu_off=False, fail_load_off=False):
        class PSU(FakeTransport):
            def __init__(self):
                super().__init__()
                self.failing_off = False
            def write(self, command):
                if command == 'CHAN:OUTP 1':
                    self.failing_off = True
                if fail_psu_off and self.failing_off and command == 'OUTP 0':
                    raise OSError('PSU output OFF transfer lost')
                return super().write(command)
        class Load:
            live = True
            state = 'OFF'
            def __init__(self):
                self.calls = []
                self.evidence = None
            def identify(self): return 'KORAD KEL103 SN:[REDACTED]'
            def input_state(self):
                if self.evidence is not None:
                    self.evidence.add('kel103_query', command=':INP?')
                self.calls.append('state')
                return self.state
            def configure_cr(self, ohms):
                assert ohms == 40 and self.state == 'OFF'
            def set_input(self, enabled):
                if self.evidence is not None:
                    self.evidence.add('kel103_write', command=':INP OFF' if not enabled else ':INP ON')
                self.calls.append(('input', enabled))
                if not enabled and fail_load_off:
                    raise OSError('KEL INPUT OFF lost')
                self.state = 'ON' if enabled else 'OFF'
            def close(self): self.calls.append('close')
        class FaultEvidence(Evidence):
            def add(self, event, **kwargs):
                if fail_evidence and event == 'stage_error':
                    self.disk_full = True
                if fail_evidence and getattr(self, 'disk_full', False):
                    raise OSError('disk full during shutdown instrumentation')
                return super().add(event, **kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            source = PSU()
            load = Load()
            ev = FaultEvidence(Path(tmp) / 'events.jsonl', 'H07')
            load.evidence = ev  # Real KEL103 logs before USB writes and reads.
            # Abort after both devices are enabled, before the first CSV write;
            # no actual 180-second recording or physical I/O is performed.
            with patch('h07_stage.Acquisition.run', side_effect=OSError('sampling disk failed')), \
                 patch('builtins.input', return_value='CONFIRM H07 CONNECT CH1 ALL_OFF LOAD=OFF'), \
                 contextlib.redirect_stdout(io.StringIO()):
                outcome = h07(source, ev, load)
            return outcome, source, load, ev

    def test_sampling_failure_still_turns_both_devices_off(self):
        outcome, source, load, ev = self.run_fault()
        self.assertEqual(outcome, 'FAIL')
        self.assertFalse(any(source.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
        self.assertEqual(load.state, 'OFF')

    def test_evidence_failure_during_exception_still_turns_both_off(self):
        outcome, source, load, ev = self.run_fault(fail_evidence=True)
        self.assertEqual(outcome, 'ABORTED')
        self.assertFalse(any(source.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
        self.assertEqual(load.state, 'OFF')

    def test_psu_off_failure_still_attempts_load_input_off(self):
        outcome, source, load, ev = self.run_fault(fail_psu_off=True)
        self.assertEqual(outcome, 'ABORTED')
        self.assertIn(('input', False), load.calls)

    def test_load_off_failure_still_attempts_psu_off(self):
        outcome, source, load, ev = self.run_fault(fail_load_off=True)
        self.assertEqual(outcome, 'ABORTED')
        self.assertFalse(any(source.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
