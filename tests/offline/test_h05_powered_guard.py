"""Offline H05 safety regressions; no physical instruments or networking."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from keithley2231a import SafetyError
from h05_stage import h05, validate_rail, verify_load_measurement
from test_driver import FakeTransport
from qualification import Evidence


class Load:
    def __init__(self, *, stuck_on=False, mismatch=False):
        self.enabled = False
        self.stuck_on = stuck_on
        self.mismatch = mismatch
        self.close_called = False

    def identify(self): return 'KORAD KEL103'
    def input_state(self): return 'ON' if self.enabled else 'OFF'
    def configure_cr(self, resistance):
        if resistance != 22.0: raise AssertionError(resistance)
    def set_input(self, enabled):
        if enabled or not self.stuck_on: self.enabled = enabled
    def measurements(self):
        return {'kel103_voltage_v': 1.0, 'kel103_current_a': 0.09 if self.mismatch else 0.045,
                'kel103_power_w': 0.09 if self.mismatch else 0.045}
    def close(self): self.close_called = True


class Scope:
    def __init__(self, *, low_amplitude=False, bad_setup=False):
        self.low_amplitude = low_amplitude
        self.bad_setup = bad_setup
        self.stopped = False
    def configure(self):
        if self.bad_setup: raise OSError('scope setup failed')
        return {}
    def arm(self): pass
    def collect(self):
        amplitude = 0.05 if self.low_amplitude else 1.0
        return {ch: [(float(t), amplitude * float(start <= t < stop))
                     for t in range(10)]
                for ch, start, stop in ((1, 1, 8), (2, 2, 7), (3, 3, 6))}
    def number(self, command): return 0


class H05GuardTests(unittest.TestCase):
    def _run(self, load=None, scope=None, *, factory_failure=False, evidence_failure=False):
        load = load or Load()
        scope = scope or Scope()
        raw = FakeTransport()
        with tempfile.TemporaryDirectory() as temp:
            evidence = Evidence(Path(temp)/'events.jsonl', 'H05')
            if evidence_failure:
                original = evidence.add
                def bad_add(event, **kwargs):
                    if event == 'stage_error': raise OSError('evidence unavailable')
                    return original(event, **kwargs)
                evidence.add = bad_add
            with patch('equipment.factory.open_scope', side_effect=OSError('scope init failed') if factory_failure else None,
                       return_value=scope), patch('keithley2231a.device.time.sleep'), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = h05(raw, evidence, load)
            events = evidence.path.read_text()
        self.assertTrue(raw.closed)
        self.assertTrue(load.close_called)
        self.assertTrue(all(not raw.values[ch]['CHAN:OUTP'] for ch in (1,2,3)))
        return result, events

    def test_real_one_volt_rail_passes(self):
        self.assertEqual(validate_rail([(0,0), (1,1), (2,1), (3,0)], 1),
                         {'low_v': 0, 'high_v': 1})

    def test_small_chatter_and_bad_sample_are_rejected(self):
        for samples in ([ (0,0), (1,.05), (2,0) ],
                        [(0,0), (1,float('nan')), (2,1)],
                        [(0,0), (0,1)],
                        [(0,0), (1,1.25), (2,0)],
                        [(0,0), (1,float('inf')), (2,0)]):
            with self.subTest(samples=samples):
                with self.assertRaises(SafetyError): validate_rail(samples, 2)

    def test_load_comparison_rejects_mismatches_and_nonfinite(self):
        source = {'voltage_v':1.001, 'current_a':0.044, 'power_w':0.044}
        good = {'kel103_voltage_v': 1.0, 'kel103_current_a': .045, 'kel103_power_w': .045}
        self.assertLess(verify_load_measurement(source, good)['delta_power_w'], .025)
        for key, value in [('kel103_current_a', .090), ('kel103_power_w', 0.5),
                           ('kel103_voltage_v', float('nan')), ('kel103_power_w', float('inf'))]:
            with self.subTest(key=key, value=value), self.assertRaises(SafetyError):
                verify_load_measurement(source, dict(good, **{key:value}))

    def test_missing_and_bad_source_numbers_fail_closed(self):
        source = {'voltage_v':1, 'current_a':.045, 'power_w':.045}
        good = {'kel103_voltage_v':1, 'kel103_current_a':.045, 'kel103_power_w':.045}
        with self.assertRaises(SafetyError): verify_load_measurement(source, {'kel103_voltage_v':1})
        with self.assertRaises(SafetyError): verify_load_measurement(dict(source, voltage_v=float('nan')), good)

    def test_normal_h05_completes_with_load_off(self):
        result, events = self._run()
        self.assertEqual(result, 'PASS')
        self.assertIn('"live_measurement_comparison"', events)
        self.assertIn('"scope_voltage_envelopes"', events)

    def test_stuck_kel_input_off_downgrades_pass(self):
        result, _ = self._run(load=Load(stuck_on=True))
        self.assertEqual(result, 'ABORTED')

    def test_invalid_load_measurements_shutdown_source_and_load(self):
        load = Load(mismatch=True)
        result, _ = self._run(load=load)
        self.assertEqual(result, 'ABORTED')
        self.assertFalse(load.enabled)

    def test_waveform_too_small_downgrades_pass(self):
        result, _ = self._run(scope=Scope(low_amplitude=True))
        self.assertEqual(result, 'ABORTED')

    def test_scope_factory_failure_has_final_off(self):
        result, _ = self._run(factory_failure=True)
        self.assertEqual(result, 'ABORTED')

    def test_logging_error_does_not_interrupt_off(self):
        result, _ = self._run(scope=Scope(bad_setup=True), evidence_failure=True)
        self.assertEqual(result, 'ABORTED')
