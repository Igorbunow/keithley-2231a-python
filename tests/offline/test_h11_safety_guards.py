"""Fault injection for H11: no false PASS or missing independent shutdown."""
import contextlib
import csv
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support.qualification_transport import FakeTransport
from qualification import Evidence
from h11_stage import h11
from keithley2231a import SafetyError


class Source(FakeTransport):
    def __init__(self, *, fail_off=False, fail_measurement_link=False):
        super().__init__()
        self.fail_off = fail_off
        self.fail_measurement_link = fail_measurement_link
        self.off_calls = 0
        self.uncertain_cmd_index = None

    def write(self, command):
        if command == 'OUTP 0' and any(v['CHAN:OUTP'] for v in self.values.values()):
            self.off_calls += 1
            if self.fail_off:
                raise OSError('PSU OFF transfer lost')
        return super().write(command)

    def query(self, command):
        if (self.fail_measurement_link and command == 'MEAS:VOLT?' and
                self.values[1]['CHAN:OUTP']):
            self.uncertain_cmd_index = len(self.cmds)
            raise OSError('PSU measurement link lost')
        return super().query(command)


class Load:
    live = True

    def __init__(self, *, fail_off=False):
        self.state = 'OFF'
        self.evidence = None
        self.calls = []
        self.fail_off = fail_off
        self.closed = False

    def identify(self):
        return 'KEL103 TEST'

    def input_state(self):
        if self.evidence is not None:
            self.evidence.add('kel103_query', command=':INP?')
        self.calls.append('read')
        return self.state

    def configure_cr(self, resistance):
        self.calls.append(('cr', resistance))
        return resistance

    def set_input(self, enabled):
        if self.evidence is not None:
            self.evidence.add('kel103_write', command=':INP ON' if enabled else ':INP OFF')
        self.calls.append(('input', enabled))
        if not enabled and self.fail_off:
            raise OSError('KEL INPUT OFF lost')
        self.state = 'ON' if enabled else 'OFF'

    def close(self):
        self.closed = True


class FailingEvidence(Evidence):
    def __init__(self, path):
        super().__init__(path, 'H11')
        self.fail = False

    def add(self, event, **kwargs):
        if self.fail:
            raise OSError('simulated full evidence disk')
        return super().add(event, **kwargs)


class TestH11FailClosed(unittest.TestCase):
    @staticmethod
    def run_case(*, failure='sweep', fail_psu_off=False, fail_load_off=False):
        with tempfile.TemporaryDirectory() as folder:
            evidence = FailingEvidence(Path(folder) / 'events.jsonl')
            source = Source(fail_off=fail_psu_off,
                            fail_measurement_link=failure == 'uncertain_uart')
            load = Load(fail_off=fail_load_off)
            load.evidence = evidence

            def sweep(dev, *args, **kwargs):
                # Exercise the PUBLIC output() path, never a second raw SCPI
                # implementation. Failure is injected only after both are ON.
                dev.output(1, True)
                if failure == 'evidence':
                    evidence.fail = True
                if failure == 'uncertain_uart':
                    dev.diagnostic_query('MEAS:VOLT?')
                raise SafetyError('injected acquisition failure')

            with (patch('h11_stage.voltage_sweep', side_effect=sweep),
                  patch('builtins.input', return_value='CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF'),
                  contextlib.redirect_stdout(io.StringIO())):
                result = h11(source, evidence, load)
            return result, source, load, evidence

    def test_successful_sweep_returns_pass_only_after_verified_electrical_off(self):
        with tempfile.TemporaryDirectory() as folder:
            evidence = FailingEvidence(Path(folder) / 'events.jsonl')
            source, load = Source(), Load()
            load.evidence = evidence

            def successful_sweep(dev, *args, csv_path, **kwargs):
                dev.output(1, True)
                points = [SimpleNamespace(index=index, requested_v=requested,
                                          timestamp_utc=f'2026-10-29T01:06:{40+index}.000000+00:00',
                                          monotonic_s=1000. + index,
                                          measured_v=actual, measured_a=current,
                                          measured_w=actual * current,
                                          regulation=state, external={
                                              'kel_current_a':current,
                                              'kel_power_w':actual * current,
                                              'kel_voltage_v':actual})
                          for index, (requested, actual, current, state) in enumerate((
                              (.50, .50, .012, 'CV'),
                              (1.0, 1.0, .024, 'CV'),
                              (1.5, 1.5, .037, 'CV'),
                              (2.0, 1.63, .040, 'CC')), start=1)]
                dev.output(1, False)
                with csv_path.open('w', newline='', encoding='utf-8') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(('index', 'requested_v', 'timestamp_utc',
                        'monotonic_s', 'measured_v', 'measured_a', 'measured_w',
                        'regulation', 'kel_current_a', 'kel_power_w', 'kel_voltage_v'))
                    for point in points:
                        writer.writerow((point.index, point.requested_v,
                            point.timestamp_utc, point.monotonic_s, point.measured_v,
                            point.measured_a, point.measured_w, point.regulation,
                            point.external['kel_current_a'],
                            point.external['kel_power_w'],
                            point.external['kel_voltage_v']))
                return points

            with (patch('h11_stage.voltage_sweep', side_effect=successful_sweep),
                  patch('builtins.input', return_value='CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF'),
                  contextlib.redirect_stdout(io.StringIO())):
                status = h11(source, evidence, load)
            self.assertEqual(status, 'PASS')
            self.assertIn('OUTP 0', source.cmds)
            self.assertFalse(any(c['CHAN:OUTP'] for c in source.values.values()))
            self.assertEqual(load.state, 'OFF')
            self.assertTrue(source.closed and load.closed)
            recorded = evidence.path.read_text(encoding='utf-8')
            self.assertIn('"event": "query_outcome"', recorded)
            self.assertNotIn('CONFIRM H11 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF DISCONNECTED', recorded)
            # qualification.py, not H11, owns the single final operator gate.

    def test_unconfirmed_output_readback_cannot_be_pass(self):
        with patch('common.require_outputs', return_value={1: True, 2: False, 3: False}):
            status, source, load, _ = self.run_case()
        self.assertEqual(status, 'ABORTED')
        self.assertTrue(source.off_calls)
        self.assertEqual(load.state, 'OFF')

    def test_acquisition_failure_shuts_both_off_and_closes(self):
        status, source, load, evidence = self.run_case()
        self.assertEqual(status, 'FAIL')
        self.assertTrue(source.off_calls)
        self.assertFalse(any(c['CHAN:OUTP'] for c in source.values.values()))
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(source.closed and load.closed)
        self.assertFalse(evidence.fail)

    def test_disk_failure_cannot_prevent_psu_or_load_off(self):
        status, source, load, evidence = self.run_case(failure='evidence')
        self.assertEqual(status, 'ABORTED')
        self.assertTrue(source.off_calls)
        self.assertFalse(any(c['CHAN:OUTP'] for c in source.values.values()))
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(source.closed and load.closed)
        self.assertTrue(evidence.fail)

    def test_psu_off_failure_still_shuts_load_off(self):
        status, source, load, _ = self.run_case(fail_psu_off=True)
        self.assertEqual(status, 'ABORTED')
        self.assertTrue(source.off_calls)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(source.closed and load.closed)

    def test_load_off_failure_still_shuts_psu_off(self):
        status, source, load, _ = self.run_case(fail_load_off=True)
        self.assertEqual(status, 'ABORTED')
        self.assertTrue(source.off_calls)
        self.assertFalse(any(c['CHAN:OUTP'] for c in source.values.values()))
        self.assertTrue(source.closed and load.closed)

    def test_uncertain_psu_link_is_not_retried_and_kel_is_shut_down(self):
        status, source, load, _ = self.run_case(failure='uncertain_uart')
        self.assertEqual(status, 'ABORTED')
        self.assertIsNotNone(source.uncertain_cmd_index)
        # No further PSU commands after the transfer that made UART unknown.
        self.assertEqual(len(source.cmds), source.uncertain_cmd_index)
        self.assertEqual(load.state, 'OFF')
        self.assertTrue(source.closed and load.closed)


if __name__ == '__main__':
    unittest.main()
