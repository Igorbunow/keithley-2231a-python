"""H04P fault injection: both instruments must have independent shutdown paths.

All tests are simulated; no equipment handles or serial ports are opened.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
import powered_profile_stage as h04p
from qualification import Evidence


class PointBench:
    def __init__(self):
        self.events = []
        self.io = SimpleNamespace(outputs={1: False, 2: False, 3: False},
                                  live=True, authorized_channel=None)
        self.dev = MagicMock()
        self.dev.armed = True
        self.dev.faulted = False
        self.dev.diagnostic_query.return_value = '0.33'
        self.dev.read_channel.return_value = SimpleNamespace(
            enabled=True, regulation='CV', voltage_v=3.3,
            current_a=.1, power_w=.33, operation_status_mask=9,
            questionable_status_mask=1)
        self.dev.set_channel.side_effect = lambda *a, **k: self.events.append('psu.config')
        self.dev.output.side_effect = self.output
        self.dev.emergency_off.side_effect = self.emergency_off
        self.load = MagicMock()
        self.load.live = True
        self.load.input_state.return_value = 'OFF'
        self.load.configure_cr.return_value = 47.0
        self.load.measurements.return_value = {'kel103_voltage_v': 3.3,
                                               'kel103_current_a': .1,
                                               'kel103_power_w': .33}
        self.load.set_input.side_effect = self.load_input
        self.input_state = 'OFF'
        self.with_temp = tempfile.TemporaryDirectory()
        self.evidence = Evidence(Path(self.with_temp.name) / 'point.jsonl', 'H04P')

    def output(self, channel, enabled):
        self.events.append('psu.on' if enabled else 'psu.off')
        self.io.outputs[channel] = enabled

    def emergency_off(self):
        self.events.append('psu.emergency_off')
        self.io.outputs = {1: False, 2: False, 3: False}

    def load_input(self, enabled):
        self.events.append('kel.on' if enabled else 'kel.off')
        self.input_state = 'ON' if enabled else 'OFF'

    def release(self):
        self.with_temp.cleanup()

    def measure(self):
        with patch.object(h04p, 'require_outputs', side_effect=self.assert_outputs):
            h04p._measure_point(self.dev, self.io, self.evidence, self.load, 1,
                                phase='control', point='common_3.30V', voltage=3.3,
                                current=.1, resistance=47.0, expected_mode='CV')

    def assert_outputs(self, dev, expected=None, **kw):
        expected = expected or {1: False, 2: False, 3: False}
        if self.io.outputs != expected:
            raise AssertionError(f'expected {expected}, got {self.io.outputs}')


class H04PShutdownTests(unittest.TestCase):
    def setUp(self):
        self.b = PointBench()
        self.addCleanup(self.b.release)

    def test_normal_point_shutdowns_both_and_checks_source_off(self):
        self.b.measure()
        self.assertEqual(self.b.events[-2:], ['psu.off', 'kel.off'])
        self.assertEqual(self.b.input_state, 'OFF')
        self.assertFalse(any(self.b.io.outputs.values()))

    def test_psu_on_failure_after_wire_still_switches_load_off(self):
        def fails_on(channel, enabled):
            self.b.output(channel, enabled)
            if enabled:
                self.b.io.live = False  # response lost, session untrustworthy
                raise OSError('ack lost after physical ON')
        self.b.dev.output.side_effect = fails_on
        with self.assertRaisesRegex(Exception, 'shutdown unconfirmed'):
            self.b.measure()
        self.assertEqual(self.b.events.count('psu.on'), 1)
        self.assertEqual(self.b.events[-1], 'kel.off')
        self.assertNotIn('psu.off', self.b.events)
        self.assertNotIn('psu.emergency_off', self.b.events)
        self.assertEqual(self.b.input_state, 'OFF')

    def test_psu_off_failure_does_not_suppress_load_off_or_repeat_psu(self):
        def fails_off(channel, enabled):
            self.b.output(channel, enabled)
            if not enabled:
                self.b.io.live = False
                raise OSError('PSU OFF ack lost')
        self.b.dev.output.side_effect = fails_off
        with self.assertRaisesRegex(Exception, 'shutdown unconfirmed'):
            self.b.measure()
        self.assertEqual(self.b.events.count('psu.off'), 1)
        self.assertEqual(self.b.events.count('kel.off'), 1)
        self.assertEqual(self.b.input_state, 'OFF')

    def test_load_on_failure_after_physical_on_cannot_leave_confirmed_pass(self):
        def fails_load_on(enabled):
            self.b.load_input(enabled)
            if enabled:
                raise OSError('load ON ack lost')
        self.b.load.set_input.side_effect = fails_load_on
        with self.assertRaisesRegex(OSError, 'ack lost'):
            self.b.measure()
        self.assertNotIn('psu.on', self.b.events)
        self.assertEqual(self.b.events.count('kel.off'), 1)
        self.assertEqual(self.b.input_state, 'OFF')

    def test_load_off_failure_does_not_suppress_psu_off(self):
        def fails_load_off(enabled):
            self.b.load_input(enabled)
            if not enabled:
                raise OSError('load OFF unconfirmed')
        self.b.load.set_input.side_effect = fails_load_off
        with self.assertRaisesRegex(Exception, 'shutdown unconfirmed'):
            self.b.measure()
        self.assertIn('psu.off', self.b.events)
        self.assertEqual(self.b.events.count('kel.off'), 1)

    def test_both_shutdowns_attempted_even_when_psu_shutdown_raises(self):
        self.b.dev.emergency_off.side_effect = OSError('PSU emergency OFF failed')
        with patch.object(h04p, 'require_outputs', side_effect=AssertionError('no readback')):
            result = h04p._release_h04p_instruments(
                self.b.dev, self.b.io, self.b.load, self.b.evidence)
        self.assertEqual(len(result), 1)
        self.assertIn('kel.off', self.b.events)

    def test_no_io_on_uncertain_load_link_but_psu_still_shutdown(self):
        self.b.load.live = False
        with patch.object(h04p, 'require_outputs', side_effect=self.b.assert_outputs):
            result = h04p._release_h04p_instruments(
                self.b.dev, self.b.io, self.b.load, self.b.evidence)
        self.assertIn('psu.emergency_off', self.b.events)
        self.assertNotIn('kel.off', self.b.events)
        self.assertIn('KEL103 link uncertain', result[0])

    def test_stage_level_cleanup_handles_load_even_if_psu_off_fails(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Evidence(Path(td) / 'out.jsonl', 'H04P')
            io = SimpleNamespace(live=True, commands=0, outputs={1: False, 2: False, 3: False})
            dev = MagicMock()
            dev.debug_transport = io
            dev.emergency_off.side_effect = OSError('PSU fault')
            load = MagicMock()
            load.live = True
            load.identify.side_effect = RuntimeError('preflight fault')
            transport = MagicMock()
            with patch.object(h04p, 'Keithley2231A', return_value=dev):
                outcome = h04p.h04_powered(transport, evidence, load)
            self.assertEqual(outcome, 'ABORTED')
            dev.emergency_off.assert_called_once()
            load.set_input.assert_called_once_with(False)
            transport.close.assert_called_once()
            load.close.assert_called_once()
            records = [json.loads(x) for x in evidence.path.read_text().splitlines()]
            self.assertEqual(records[-1]['outcome'], 'ABORTED')
            self.assertTrue(any(r.get('instrument') == 'Keithley'
                                for r in records if r['event'] == 'cleanup_error'))

    def test_stage_level_cleanup_handles_psu_if_load_off_fails(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Evidence(Path(td) / 'out.jsonl', 'H04P')
            io = SimpleNamespace(live=True, commands=0, outputs={1: False, 2: False, 3: False})
            dev = MagicMock()
            dev.debug_transport = io
            load = MagicMock()
            load.live = True
            load.identify.side_effect = RuntimeError('preflight fault')
            load.set_input.side_effect = OSError('load fault')
            transport = MagicMock()
            with patch.object(h04p, 'Keithley2231A', return_value=dev), \
                    patch.object(h04p, 'require_outputs'):
                outcome = h04p.h04_powered(transport, evidence, load)
            self.assertEqual(outcome, 'ABORTED')
            dev.emergency_off.assert_called_once()
            load.set_input.assert_called_once_with(False)


    def test_no_psu_commands_on_uncertain_link_but_korad_off_attempted(self):
        self.b.io.live = False
        result = h04p._release_h04p_instruments(
            self.b.dev, self.b.io, self.b.load, self.b.evidence)
        self.assertEqual(self.b.events, ['kel.off'])
        self.assertIn('Keithley link uncertain', result[0])

    def test_cleanup_evidence_failure_cannot_prevent_other_off_attempt(self):
        self.b.dev.emergency_off.side_effect = OSError('PSU fault')
        self.b.evidence.add = MagicMock(side_effect=OSError('disk full'))
        result = h04p._release_h04p_instruments(
            self.b.dev, self.b.io, self.b.load, self.b.evidence)
        self.assertIn('kel.off', self.b.events)
        self.assertTrue(any('evidence write failed' in item for item in result))


    def test_stage_error_evidence_failure_still_shutdowns_both_links(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Evidence(Path(td) / 'out.jsonl', 'H04P')
            original_add = evidence.add
            def fails_stage_error(event, **values):
                if event == 'stage_error':
                    raise OSError('disk full while recording fault')
                return original_add(event, **values)
            evidence.add = fails_stage_error
            io = SimpleNamespace(live=True, commands=0)
            dev = MagicMock()
            dev.debug_transport = io
            load = MagicMock()
            load.live = True
            load.identify.side_effect = RuntimeError('preflight fault')
            transport = MagicMock()
            with patch.object(h04p, 'Keithley2231A', return_value=dev), \
                    patch.object(h04p, 'require_outputs'):
                outcome = h04p.h04_powered(transport, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            dev.emergency_off.assert_called_once()
            load.set_input.assert_called_once_with(False)
            transport.close.assert_called_once()
            load.close.assert_called_once()

    def test_stage_level_does_not_repeat_point_shutdown_after_failure(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Evidence(Path(td) / 'out.jsonl', 'H04P')
            io = SimpleNamespace(live=True, commands=0,
                                 h04p_point_shutdown_failures=['PSU OFF unconfirmed'])
            dev = MagicMock()
            dev.debug_transport = io
            load = MagicMock()
            load.live = True
            load.identify.side_effect = RuntimeError('point failed')
            transport = MagicMock()
            with patch.object(h04p, 'Keithley2231A', return_value=dev):
                outcome = h04p.h04_powered(transport, evidence, load)
            self.assertEqual(outcome, 'ABORTED')
            dev.emergency_off.assert_not_called()
            load.set_input.assert_not_called()


if __name__ == '__main__':
    unittest.main()
