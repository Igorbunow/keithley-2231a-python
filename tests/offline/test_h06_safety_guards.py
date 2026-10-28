"""Fault injection for H06 independent shutdown and U/I/P checks; no hardware."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from keithley2231a import SafetyError
from h06_stage import _check_load, _release_h06_instruments


class H06GuardTests(unittest.TestCase):
    @staticmethod
    def scenario(*, psu_failure=False, load_failure=False, psu_live=True,
                 load_live=True, evidence_failure=False, timer_failure=False):
        class PSU:
            debug_bypass_profile = False
            def __init__(self): self.calls = []
            def emergency_off(self):
                self.calls.append('emergency_off')
                if psu_failure: raise OSError('PSU OFF failed')
            def diagnostic_write(self, command):
                self.calls.append(command)
            def diagnostic_query(self, command):
                self.calls.append(command)
                return '1' if timer_failure else '0'
            @staticmethod
            def _bool(value): return value == '1'
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
        class Evidence:
            def __init__(self): self.records = []
            def add(self, *args, **kwargs):
                if evidence_failure: raise OSError('disk full')
                self.records.append((args, kwargs))
        psu, load, evidence = PSU(), Load(), Evidence()
        adapter = SimpleNamespace(live=psu_live, cleanup=False)
        with patch('h06_stage.require_outputs', return_value={1: False, 2: False, 3: False}):
            failures = _release_h06_instruments(psu, adapter, load, evidence)
        return failures, psu, adapter, load, evidence

    def test_psu_off_error_does_not_skip_kel_off_or_retry_psu(self):
        failures, psu, _, load, _ = self.scenario(psu_failure=True)
        self.assertEqual(psu.calls, ['emergency_off'])
        self.assertIn(('set_input', False), load.calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(any('Keithley OFF unconfirmed' in error for error in failures))
        self.assertTrue(any('timers not verified' in error for error in failures))

    def test_kel_off_error_does_not_skip_psu_off_and_timer_reset(self):
        failures, psu, _, load, _ = self.scenario(load_failure=True)
        self.assertIn('emergency_off', psu.calls)
        self.assertEqual(psu.calls.count('OUTP:TIM:STAT 0'), 3)
        self.assertIn(('set_input', False), load.calls)
        self.assertTrue(any('KEL103 INPUT OFF unconfirmed' in error for error in failures))

    def test_psu_uncertain_link_is_never_reused_but_kel_off_runs(self):
        failures, psu, _, load, _ = self.scenario(psu_live=False)
        self.assertEqual(psu.calls, [])
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(any('Keithley link uncertain' in e for e in failures))

    def test_load_uncertain_link_is_never_reused_but_psu_off_runs(self):
        failures, psu, _, load, _ = self.scenario(load_live=False)
        self.assertEqual(load.calls, [])
        self.assertIn('emergency_off', psu.calls)
        self.assertTrue(any('KEL103 link uncertain' in e for e in failures))

    def test_evidence_write_failure_does_not_interrupt_off_attempts(self):
        failures, psu, _, load, _ = self.scenario(psu_failure=True, evidence_failure=True)
        self.assertIn('emergency_off', psu.calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(failures)

    def test_timer_readback_failure_is_not_silent(self):
        failures, psu, _, load, _ = self.scenario(timer_failure=True)
        self.assertTrue(any('timers/Local unconfirmed' in e for e in failures))
        self.assertIn('OUTP:TIM:STAT?', psu.calls)
        self.assertEqual(load.state, 'OFF')

    def test_normal_cleanup_confirms_both_off_and_three_timers(self):
        failures, psu, _, load, _ = self.scenario()
        self.assertEqual(failures, [])
        self.assertEqual(psu.calls.count('OUTP:TIM:STAT 0'), 3)
        self.assertEqual(psu.calls.count('OUTP:TIM:STAT?'), 3)
        self.assertIn('SYST:LOC', psu.calls)
        self.assertEqual(load.state, 'OFF')

    def test_live_numeric_checks_compare_both_instruments(self):
        readings = {'kel103_voltage_v': 1.5, 'kel103_current_a': 1.5/22,
                    'kel103_power_w': 1.5*(1.5/22)}
        load = SimpleNamespace(target_channel=None, measurements=lambda: dict(readings))
        _check_load(load, 2, 1.5, True, source_current_a=1.5/22,
                    source_power_w=1.5*(1.5/22))
        for field, wrong in [('kel103_power_w', .2), ('kel103_current_a', .2),
                             ('kel103_voltage_v', 1.1), ('kel103_power_w', float('nan'))]:
            with self.subTest(field=field, wrong=wrong):
                flawed = dict(readings, **{field: wrong})
                load.measurements = lambda flawed=flawed: flawed
                with self.assertRaises(SafetyError):
                    _check_load(load, 2, 1.5, True, source_current_a=1.5/22,
                                source_power_w=1.5*(1.5/22))

    def test_keithley_power_mismatch_fails_even_if_kel_self_consistent(self):
        load = SimpleNamespace(target_channel=None,
                    measurements=lambda: {'kel103_voltage_v': 1.0,
                                          'kel103_current_a': 1/22,
                                          'kel103_power_w': 1/22})
        with self.assertRaises(SafetyError):
            _check_load(load, 1, 1.0, True, source_current_a=1/22,
                        source_power_w=.15)

    def test_unnumeric_keithley_source_rejected(self):
        load = SimpleNamespace(target_channel=None,
                    measurements=lambda: {'kel103_voltage_v': 1.0,
                                          'kel103_current_a': 1/22,
                                          'kel103_power_w': 1/22})
        for value in (float('nan'), float('inf'), -1.0, None, True):
            with self.subTest(value=value), self.assertRaises(SafetyError):
                _check_load(load, 1, 1.0, True, source_current_a=value,
                            source_power_w=1/22)

class H06RecallGuardsTests(unittest.TestCase):
    def test_initial_on_never_starts_recall_profile_or_power_cycle(self):
        from h06_recall import recall
        from qualification import Evidence
        from test_driver import FakeTransport
        source = FakeTransport()
        source.values[2]['CHAN:OUTP'] = 1.0
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Evidence(Path(tmp) / 'events.jsonl', 'H06')
            with patch('builtins.input', side_effect=AssertionError('no gate permitted')), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = recall(source, evidence, lambda: (_ for _ in ()).throw(
                    AssertionError('must never reopen')))
        self.assertEqual(outcome, 'FAIL')
        self.assertNotIn('VOLT 0', source.cmds)
        self.assertNotIn('CHAN:OUTP 1', source.cmds)
        self.assertIn('OUTP 0', source.cmds)
        self.assertTrue(all(not source.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))

    def test_recall_evidence_failure_cannot_skip_emergency_off(self):
        from h06_recall import recall
        from qualification import Evidence
        from test_driver import FakeTransport
        class FaultyEvidence(Evidence):
            def add(self, event, **kw):
                if event in ('recall_boot_observation', 'stage_error'):
                    raise OSError('simulated evidence disk full')
                return super().add(event, **kw)
        initial = FakeTransport()
        reopened = []
        def reopen():
            self.assertTrue(initial.closed)
            boot = FakeTransport()
            boot.values = {ch: dict(values) for ch, values in initial.values.items()}
            for values in boot.values.values():
                values['CHAN:OUTP'] = 0.0
            reopened.append(boot)
            return boot
        answers = ['CONFIRM H06 RECALL CASE=OFF_111 RECALL=OFF '
                   'SAVE_LAST=ON ALL_OFF CYCLE=AUTHORIZED',
                   'CONFIRM H06 RECALL CASE=OFF_111 POWER_CYCLED PANEL=000']
        with tempfile.TemporaryDirectory() as tmp:
            evidence = FaultyEvidence(Path(tmp) / 'events.jsonl', 'H06')
            with patch('builtins.input', side_effect=answers), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = recall(initial, evidence, reopen)
        self.assertEqual(outcome, 'FAIL')
        self.assertEqual(len(reopened), 1)
        self.assertIn('OUTP 0', reopened[0].cmds)
        self.assertTrue(all(not reopened[0].values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
