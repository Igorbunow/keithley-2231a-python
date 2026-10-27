"""Regression checks for qualification gates and evidence, using simulated I/O."""
import contextlib
import io
import json
import runpy
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'hardware'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

class TestQualification(unittest.TestCase):
    def assert_driver_path(self, evidence, transport):
        """Every simulated wire command must come from the common driver dispatch."""
        records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
        sent = [r['trace']['command'] for r in records if r['event'] == 'driver_trace'
                and r['trace']['event'] in ('write', 'query')]
        self.assertEqual(sent, transport.cmds)

    def module(self):
        import qualification
        return qualification

    def test_combined_off_diagnostic_never_energizes_and_stops_on_error(self):
        from test_driver import FakeTransport
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec('combined_off_stage'), 'Shared combined OFF runner is missing')
        import combined_off_stage
        for test_id, mode in (('H09', 'SER'), ('H10', 'PARALLEL')):
            for fault in (None, 'alias', 'changed_after', 'recovery', 'queue', 'io', 'mode'):
                with self.subTest(test_id=test_id, fault=fault), tempfile.TemporaryDirectory() as tmp:
                    class Source(FakeTransport):
                        error_returned = False
                        mode_off_count = 0
                        def write(self, command):
                            super().write(command)
                            if command == f'OUTP:{mode} 0':
                                self.mode_off_count += 1
                            if fault in ('alias', 'changed_after'):
                                if self.flags[f'OUTP:{mode}'] and command == 'INST:NSEL 2':
                                    self.active_ch = 1
                            if (fault in ('changed_after', 'recovery') and
                                    command == f'OUTP:{mode} 0' and self.mode_off_count >= 2):
                                    self.values[1]['VOLT:LIM:STAT'] = 0.
                            if fault == 'recovery' and self.mode_off_count >= 3:
                                self.values[1]['VOLT:LIM:STAT'] = 0.
                        def query(self, command):
                            if self.flags[f'OUTP:{mode}']:
                                if fault == 'io' and command == 'VOLT?':
                                    self.cmds.append(command)
                                    raise OSError('combined query link lost')
                                if fault == 'queue' and command == 'SYST:ERR?' and not self.error_returned:
                                    self.error_returned = True
                                    self.cmds.append(command)
                                    return '170,"Invalid command"'
                                if fault == 'mode' and command == f'OUTP:{mode}?':
                                    self.cmds.append(command)
                                    return '0'
                                if fault in ('alias', 'changed_after'):
                                    if command == 'VOLT:LIM?':
                                        self.cmds.append(command)
                                        return '5'
                                    if command == 'VOLT:LIM:STAT?':
                                        self.cmds.append(command)
                                        return '0'
                            if fault == 'recovery' and self.mode_off_count >= 3 and command == 'VOLT:LIM:STAT?':
                                self.cmds.append(command)
                                return '0'
                            return super().query(command)
                    source = Source()
                    evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', test_id)
                    with contextlib.redirect_stdout(io.StringIO()):
                        result = combined_off_stage.run_combined_off(source, evidence, test_id)
                    self.assertEqual(result, 'PARTIAL' if fault in (None, 'alias', 'changed_after') else
                                     'ABORTED' if fault == 'io' else 'FAIL', evidence.path.read_text()[-1200:])
                    self.assertNotIn('OUTP 1', source.cmds)
                    self.assertNotIn('CHAN:OUTP 1', source.cmds)
                    self.assertTrue(source.closed)
                    self.assert_driver_path(evidence, source)
                    if fault in (None, 'alias', 'changed_after'):
                        self.assertEqual(source.flags[f'OUTP:{mode}'], 0)
                        records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
                        self.assertEqual(len([r for r in records if r['event'] == f'{mode.lower()}_off_snapshot']), 4)
                        if fault == 'alias':
                            combined = next(r for r in records if r.get('phase') == f'{mode.lower()}_off')
                            self.assertEqual(combined['channels']['2']['INST:NSEL?'], 1.)
                            self.assertEqual(combined['channels']['2']['VOLT:LIM:STAT?'], 0.)
                        restored = next(r for r in records if r.get('phase') == 'independent_after_restoration')
                        self.assertEqual(restored['channels']['1']['VOLT?'], 1.)
                        self.assertEqual(restored['channels']['2']['VOLT?'], 1.5)
                        self.assertEqual(restored['channels']['1']['VOLT:LIM:STAT?'], 1.)
                    scope = combined_off_stage.CombinedOffScope(source, evidence, mode)
                    for command in ('OUTP 1', 'CHAN:OUTP 1', 'VOLT 60', ('OUTP:PARALLEL 1' if mode == 'SER' else 'OUTP:SER 1')):
                        with self.assertRaises(ValueError): scope._validate(command, False)

    def test_h09_powered_preflight_is_explicit_and_global_on_is_gated(self):
        import h09_powered_stage
        from test_driver import FakeTransport
        from keithley2231a import SafetyError
        with contextlib.redirect_stdout(io.StringIO()) as output:
            h09_powered_stage.preflight('/dev/ttyUSB3', load_transport='serial')
        card = output.getvalue()
        for detail in ('CH1− с CH2+', 'INPUT + к CH1+', 'INPUT − к CH2−', 'CH3', '2.00 V', '0.250 A',
                       'INPUT OFF', 'Series', '30.0–40.5 V', 'V/R', '0.015 A',
                       'оператор должен отдельно подтвердить индикатор CC',
                       h09_powered_stage.approval_token()):
            self.assertIn(detail, card)
        self.assertNotIn('37.8–40.0 V', card)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H09')
            scope = h09_powered_stage.H09PoweredScope(FakeTransport(), evidence)
            with self.assertRaises(SafetyError): scope._validate('OUTP 1', False)
            with self.assertRaises(SafetyError): scope._validate('OUTP:SER 1', False)
            scope.powered_gate = True
            scope._validate('OUTP 1', False)
            scope._validate('OUTP:SER 1', False)

    def test_h10_powered_preflight_and_global_on_gate(self):
        import h10_powered_stage
        from test_driver import FakeTransport
        from keithley2231a import SafetyError
        with contextlib.redirect_stdout(io.StringIO()) as output:
            h10_powered_stage.preflight('/dev/ttyUSB3', load_transport='serial')
        card = output.getvalue()
        for detail in ('CH1+ с CH2+', 'CH1− с CH2−', 'INPUT+ к общей плюсовой точке',
                       'FRONT HI', 'CH3 оставить полностью', 'CR 30 Ω', 'CR 5 Ω',
                       '4.00 V', 'не аппаратная OVP', 'Равное распределение тока',
                       h10_powered_stage.approval_token()):
            self.assertIn(detail, card)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H10')
            scope = h10_powered_stage.H10PoweredScope(FakeTransport(), evidence)
            with self.assertRaises(SafetyError): scope._validate('OUTP 1', False)
            with self.assertRaises(SafetyError): scope._validate('OUTP:SER 1', False)
            with self.assertRaises(ValueError): scope._validate('VOLT 5.00', False)
            with self.assertRaises(SafetyError): scope._validate('CURR 4.000', False)
            scope.powered_gate = True
            scope._validate('OUTP 1', False)
            scope._validate('OUTP:PARALLEL 1', False)
            scope.high_current_gate = True
            scope._validate('CURR 4.000', False)

    def test_h10_powered_runner_uses_shared_driver_and_checks_cv_cc(self):
        import h10_powered_stage
        from test_driver import FakeTransport

        class ParallelSource(FakeTransport):
            def __init__(self):
                super().__init__()
                self.load = None

            def write(self, command):
                super().write(command)
                if command == 'OUTP:PARALLEL 1':
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.
                elif command == 'OUTP:PARALLEL 0':
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.

            def actual_pair(self):
                if not self.flags['OUTP:STAT:ALL'] or self.load is None or self.load.state != 'ON':
                    return 0., 0.
                voltage = min(self.values[1]['VOLT'],
                              self.values[1]['CURR'] * self.load.resistance)
                return voltage, voltage / self.load.resistance

            def query(self, command):
                if command == 'MEAS:VOLT?' and self.active_ch == 1:
                    self.cmds.append(command)
                    return str(self.actual_pair()[0])
                if command == 'MEAS:CURR?' and self.active_ch == 1:
                    self.cmds.append(command)
                    return str(self.actual_pair()[1])
                if command == 'MEAS:VOLT?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '0'
                if command == 'MEAS:CURR?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '0'
                if command == 'STAT:OPER:INST:ISUM1:COND?':
                    self.cmds.append(command)
                    voltage, _ = self.actual_pair()
                    return ('10' if voltage + .001 < self.values[1]['VOLT'] else '9') \
                        if self.flags['OUTP:STAT:ALL'] else '0'
                return super().query(command)

        class Load:
            port = '/dev/ttyACM0'
            live = True
            def __init__(self, source):
                self.source, self.state, self.resistance, self.closed = source, 'OFF', None, False
                source.load = self
            def input_state(self): return self.state
            def configure_cr(self, resistance): self.resistance = resistance; return resistance
            def set_input(self, enabled): self.state = 'ON' if enabled else 'OFF'
            def measurements(self):
                voltage, current = self.source.actual_pair()
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        class Meter:
            live = True
            def __init__(self, source): self.source, self.closed, self.range_v = source, False, None
            def configure_voltage(self, range_v=10): self.range_v = range_v
            def measure(self): return {'voltage_v': self.source.actual_pair()[0]}
            def close(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H10')
            source = ParallelSource()
            load, meter = Load(source), Meter(source)
            observed = []
            def gate(token, prompt, adapter=None): observed.append(token)
            with patch.object(h10_powered_stage, 'operator_gate', side_effect=gate), \
                 patch.object(h10_powered_stage.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = h10_powered_stage.h10_powered(source, evidence, load, meter)
            self.assertEqual(outcome, 'PASS', '\n'.join(
                line for line in evidence.path.read_text().splitlines()
                if 'stage_error' in line or 'query_outcome' in line))
            self.assertEqual(observed, ['CONFIRM H10 DISPLAY PSU=CV KEL=CR INPUT=ON',
                                        'CONFIRM H10 DISPLAY PSU=CC KEL=CR INPUT=ON',
                                        h10_powered_stage.FINAL_TOKEN])
            self.assertEqual(source.flags['OUTP:PARALLEL'], 0.)
            self.assertEqual(source.flags['OUTP:STAT:ALL'], 0.)
            self.assertEqual(load.state, 'OFF')
            self.assertTrue(source.closed and load.closed and meter.closed)
            self.assertEqual(len([r for r in map(json.loads, evidence.path.read_text().splitlines())
                                  if r['event'] == 'parallel_measurement_sample']), 8)
            self.assert_driver_path(evidence, source)

    def test_h10_high_current_preflight_is_separately_gated_and_read_only(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('PSU port opened')):
            try:
                with contextlib.redirect_stdout(output):
                    result = q.main(['preflight', 'H10', '--high-current', ])
            except SystemExit as exc:
                self.fail(f'H10 high-current preflight was not accepted: {exc}')
        card = output.getvalue()
        self.assertEqual(result, 0)
        self.assertIn('CR 0.50 Ω', card)
        self.assertIn('4.00 A', card)
        self.assertIn('8 W', card)
        self.assertIn('AWG 10', card)
        self.assertIn('DMM7510', card)
        self.assertIn('DRY RUN: порт не открыт', card)

    def test_h10_high_current_runner_requires_4a_cc_measurement_and_final_off(self):
        import h10_powered_stage
        from test_driver import FakeTransport

        self.assertTrue(hasattr(h10_powered_stage, 'h10_high_current'),
                        'H10 high-current path is missing')

        class ParallelSource(FakeTransport):
            def __init__(self):
                super().__init__()
                self.load = None

            def write(self, command):
                super().write(command)
                if command in ('OUTP:PARALLEL 1', 'OUTP:PARALLEL 0'):
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    self.values[2]['VOLT:LIM:STAT'] = 0.

            def actual_pair(self):
                if not self.flags['OUTP:STAT:ALL'] or self.load is None or self.load.state != 'ON':
                    return 0., 0.
                voltage = min(self.values[1]['VOLT'],
                              self.values[1]['CURR'] * self.load.resistance)
                return voltage, voltage / self.load.resistance

            def query(self, command):
                if command == 'MEAS:VOLT?' and self.active_ch == 1:
                    self.cmds.append(command)
                    return str(self.actual_pair()[0])
                if command == 'MEAS:CURR?' and self.active_ch == 1:
                    self.cmds.append(command)
                    return str(self.actual_pair()[1])
                if command == 'MEAS:VOLT?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '0'
                if command == 'MEAS:CURR?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '0'
                if command == 'STAT:OPER:INST:ISUM1:COND?':
                    self.cmds.append(command)
                    voltage, _ = self.actual_pair()
                    return ('10' if voltage + .001 < self.values[1]['VOLT'] else '9') \
                        if self.flags['OUTP:STAT:ALL'] else '0'
                return super().query(command)

        class Load:
            port = '/dev/ttyACM0'
            live = True
            def __init__(self, source):
                self.source, self.state, self.resistance, self.closed = source, 'OFF', None, False
                source.load = self
            def input_state(self): return self.state
            def configure_cr(self, resistance):
                self.resistance = resistance
                return resistance
            def set_input(self, enabled): self.state = 'ON' if enabled else 'OFF'
            def measurements(self):
                voltage, current = self.source.actual_pair()
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        class Meter:
            live = True
            def __init__(self, source): self.source, self.closed = source, False
            def configure_voltage(self, range_v=10): pass
            def measure(self): return {'voltage_v': self.source.actual_pair()[0]}
            def close(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H10')
            source = ParallelSource()
            load, meter = Load(source), Meter(source)
            observed = []
            def gate(token, prompt, adapter=None): observed.append(token)
            with patch.object(h10_powered_stage, 'operator_gate', side_effect=gate), \
                 patch.object(h10_powered_stage.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = h10_powered_stage.h10_high_current(source, evidence, load, meter)
            self.assertEqual(outcome, 'PASS', '\n'.join(
                line for line in evidence.path.read_text().splitlines()
                if 'stage_error' in line or 'query_outcome' in line))
            self.assertEqual(observed, ['CONFIRM H10 DISPLAY PSU=CC4A KEL=CR INPUT=ON',
                                        h10_powered_stage.FINAL_TOKEN])
            self.assertEqual(source.flags['OUTP:PARALLEL'], 0.)
            self.assertEqual(source.flags['OUTP:STAT:ALL'], 0.)
            self.assertEqual(load.state, 'OFF')
            self.assertTrue(source.closed and load.closed and meter.closed)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            samples = [r for r in records if r['event'] == 'parallel_measurement_sample']
            self.assertEqual(len(samples), 4)
            self.assertTrue(all(r['point'] == 'CC4A' and r['resistance_ohm'] == .5
                                and 3.9 <= r['kel_current_a'] <= 4.1
                                and 7.0 <= r['kel_power_w'] <= 9.0 for r in samples))
            self.assertIn('CURR 4.000', source.cmds)
            self.assert_driver_path(evidence, source)

    def test_h09_powered_runner_reuses_driver_for_cv_cc_and_restores_all_off(self):
        import h09_powered_stage
        from test_driver import FakeTransport

        class SeriesSource(FakeTransport):
            def __init__(self):
                super().__init__()
                # Model a previous stopped H09 run that left Series selected, outputs OFF.
                self.flags['OUTP:SER'] = 1.
                self.cc40_voltage_reads = 0

            def write(self, command):
                if command == 'OUTP:SER 1':
                    super().write(command)
                    self.values[1]['VOLT:LIM'] = (self.values[1]['VOLT:LIM'] +
                                                  self.values[2]['VOLT:LIM'])
                    self.values[1]['VOLT:LIM:STAT'] = 0.
                    return
                if command == 'OUTP:SER 0' and self.flags['OUTP:SER']:
                    super().write(command)
                    self.values[1]['VOLT:LIM:STAT'] = 1.
                    return
                super().write(command)

            def query(self, command):
                if command == 'STAT:OPER:INST:ISUM1:COND?':
                    self.cmds.append(command)
                    cc = ((self.values[1]['VOLT'] >= 4.0 and self.values[1]['CURR'] >= .1)
                          or (self.values[1]['VOLT'] >= 40.0 and
                              self.values[1]['CURR'] <= .024))
                    return ('10' if cc else '9') \
                        if self.flags['OUTP:STAT:ALL'] else '0'
                if command == 'MEAS:CURR?' and self.active_ch == 1:
                    self.cmds.append(command)
                    if self.values[1]['VOLT'] >= 40.0:
                        return '.024' if self.values[1]['CURR'] <= .024 else '.020'
                    return '.25' if self.values[1]['VOLT'] >= 4.0 else '.05'
                if command == 'MEAS:VOLT?' and self.active_ch == 1 and self.values[1]['VOLT'] >= 40.0:
                    self.cmds.append(command)
                    if self.values[1]['CURR'] <= .024:
                        self.cc40_voltage_reads += 1
                        return str((32.4013, 32.6813, 33.1285, 33.1334, 33.1471)[
                            min(self.cc40_voltage_reads - 1, 4)])
                    return '38.4' if self.values[1]['CURR'] <= .024 else '40.0'
                if command == 'MEAS:VOLT?' and self.active_ch == 1 and self.values[1]['VOLT'] >= 4.0:
                    self.cmds.append(command)
                    return '3.0'
                if command == 'MEAS:CURR?' and self.active_ch == 3:
                    self.cmds.append(command)
                    return '0'
                return super().query(command)

        class Load:
            port = '/dev/ttyACM0'
            live = True
            def __init__(self, meter):
                self.meter = meter
                self.state, self.resistance, self.closed = 'OFF', None, False
            def identify(self): return 'KORAD,KEL103,TEST,3.30'
            def input_state(self): return self.state
            def configure_cr(self, resistance): self.resistance = resistance; return resistance
            def set_input(self, enabled):
                self.state = 'ON' if enabled else 'OFF'
                if enabled:
                    self.meter.voltage = {40.0: 2.0, 12.0: 3.0,
                                          2000.0: 40.0, 1600.0: 33.148}[self.resistance]
            def measurements(self):
                voltage = {40.0: 2.0, 12.0: 3.0,
                           2000.0: 40.0, 1600.0: 33.148}[self.resistance]
                current = .0202 if self.resistance == 1600.0 else voltage / self.resistance
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        class Meter:
            live = True
            def __init__(self): self.voltage, self.closed, self.range_v = 0., False, None
            def configure_voltage(self, range_v=10): self.range_v = range_v
            def measure(self): return {'voltage_v': self.voltage}
            def close(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H09')
            source, meter = SeriesSource(), Meter()
            load = Load(meter)
            observed = []
            def gate(token, prompt, adapter=None):
                observed.append(token)
                meter.voltage = {
                    'CONFIRM H09 DISPLAY PSU=CV KEL=CR INPUT=ON': 2.0,
                    'CONFIRM H09 DISPLAY PSU=CC KEL=CR INPUT=ON': 3.0,
                    'CONFIRM H09 DISPLAY PSU=CV40 KEL=CR INPUT=ON': 40.0,
                    'CONFIRM H09 DISPLAY PSU=CC40 KEL=CR INPUT=ON': 33.148,
                }.get(token, meter.voltage)
            with patch.object(h09_powered_stage, 'operator_gate', side_effect=gate), \
                 patch.object(h09_powered_stage.time, 'sleep'):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = h09_powered_stage.h09_powered(source, evidence, load, meter)
            self.assertEqual(result, 'PASS', evidence.path.read_text()[-1600:])
            self.assertEqual(observed, ['CONFIRM H09 DISPLAY PSU=CV KEL=CR INPUT=ON',
                                        'CONFIRM H09 DISPLAY PSU=CC KEL=CR INPUT=ON',
                                        'CONFIRM H09 DISPLAY PSU=CV40 KEL=CR INPUT=ON',
                                        'CONFIRM H09 DISPLAY PSU=CC40 KEL=CR INPUT=ON',
                                        h09_powered_stage.FINAL_TOKEN])
            self.assertEqual(source.flags['OUTP:SER'], 0.)
            self.assertEqual(source.flags['OUTP:STAT:ALL'], 0.)
            self.assertEqual(load.state, 'OFF')
            self.assertTrue(source.closed and load.closed and meter.closed)
            self.assertEqual(len([r for r in map(json.loads, evidence.path.read_text().splitlines())
                                  if r['event'] == 'series_powered_measurement']), 4)
            self.assertEqual(meter.range_v, 100)
            self.assertGreaterEqual(source.cc40_voltage_reads, 4)
            samples = [r for r in map(json.loads, evidence.path.read_text().splitlines())
                       if r['event'] == 'series_measurement_sample' and r['mode'] == 'CC'
                       and r['resistance_ohm'] == 1600.0]
            self.assertFalse(samples[0]['cross_consistent'])
            self.assertTrue(samples[-1]['cross_consistent'])
            self.assertGreaterEqual(samples[-1]['consecutive_stable_pairs'], 2)
            self.assert_driver_path(evidence, source)

    def test_dmm7510_scpi_voltage_and_identity_evidence_are_isolated(self):
        from equipment.dmm7510 import DMM7510

        class MeterIO:
            language = 'SCPI'
            error = '0,"No error"'
            terminal = 'FRON'
            voltage = '1.23456789'
            def __init__(self): self.cmds, self.closed = [], False
            def write(self, command): self.cmds.append(command)
            def query(self, command):
                self.cmds.append(command)
                return {'*IDN?': 'KEITHLEY INSTRUMENTS,MODEL DMM7510,SECRET,1.7.5',
                        '*LANG?': self.language,
                        ':SYST:ERR?': self.error, ':ROUT:TERM?': self.terminal,
                        ':SENS:FUNC?': '"VOLT:DC"', ':SENS:VOLT:RANG?': '10',
                        ':SENS:VOLT:NPLC?': '1', ':READ?': self.voltage}[command]
            def close(self): self.closed = True

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H08')
            evidence.add('read', command='*IDN?', response='Keithley,2231A-30-3,PSU,1.04-0.01')
            io = MeterIO()
            meter = DMM7510(io, evidence)
            meter.configure_voltage()
            self.assertEqual(meter.measure(), {'voltage_v': 1.23456789})
            self.assertEqual(evidence.firmware, '1.04-0.01')
            self.assertNotIn('SECRET', evidence.path.read_text())
            self.assertLess(io.cmds.index('*IDN?'), io.cmds.index('*RST'))
            self.assertEqual(io.cmds[:3], ['*IDN?', '*LANG?', ':SYST:ERR?'])
            io.voltage = '9.9e37'
            with self.assertRaises(ValueError): meter.measure()
            before = list(io.cmds)
            with self.assertRaises(RuntimeError): meter.measure()
            self.assertEqual(io.cmds, before)
            meter.close()
            self.assertTrue(io.closed)
            for fault in ('wrong_identity', 'rear', 'error', 'tsp'):
                io = MeterIO()
                if fault == 'tsp': io.language = 'TSP'
                if fault == 'rear': io.terminal = 'REAR'
                if fault == 'error': io.error = '-113,"Undefined header"'
                if fault == 'wrong_identity':
                    original = io.query
                    io.query = lambda cmd: 'KEITHLEY,OTHER,SECRET,1.0' if cmd == '*IDN?' else original(cmd)
                with self.subTest(fault=fault), self.assertRaises((ValueError, RuntimeError)):
                    DMM7510(io, evidence).configure_voltage()
                if fault != 'rear': self.assertNotIn('*RST', io.cmds)
                if fault == 'tsp': self.assertEqual(io.cmds, ['*IDN?', '*LANG?'])

    def test_dmm7510_uses_100v_range_for_high_voltage_readback(self):
        from equipment.dmm7510 import DMM7510

        class MeterIO:
            def __init__(self): self.commands, self.range_v = [], 10.
            def write(self, command):
                self.commands.append(command)
                if command.startswith(':SENS:VOLT:RANG '):
                    self.range_v = float(command.rsplit(' ', 1)[1])
            def query(self, command):
                self.commands.append(command)
                return {'*IDN?': 'KEITHLEY INSTRUMENTS,MODEL DMM7510,TEST,1.7.5',
                        '*LANG?': 'SCPI', ':SYST:ERR?': '0,"No error"',
                        ':ROUT:TERM?': 'FRON', ':SENS:FUNC?': '"VOLT:DC"',
                        ':SENS:VOLT:RANG?': str(self.range_v),
                        ':SENS:VOLT:NPLC?': '1', ':READ?': '39.2'}[command]
            def close(self): pass

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H09')
            io = MeterIO()
            meter = DMM7510(io, evidence)
            meter.configure_voltage(range_v=100)
            self.assertIn(':SENS:VOLT:RANG 100', io.commands)
            self.assertEqual(meter.measure(), {'voltage_v': 39.2})
            configuration = [json.loads(line) for line in evidence.path.read_text().splitlines()
                             if json.loads(line)['event'] == 'dmm7510_configuration']
            self.assertEqual(configuration[-1]['range_v'], 100)

    def test_h08_tracking_dmm_gates_timer_and_failures(self):
        import h08_stage
        from test_driver import FakeTransport

        class Clock:
            now = 10.0
            def monotonic(self): return self.now
            def sleep(self, seconds): self.now += seconds

        for fault in (None, 'tracking', 'meter', 'timer', 'io', 'gate'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                clock = Clock()
                class Source(FakeTransport):
                    timer_start = None
                    def write(self, command):
                        super().write(command)
                        if command == 'OUTP 1': self.timer_start = clock.now
                    def query(self, command):
                        clock.now += .002
                        timers = [self.values[ch]['OUTP:TIM:DEL'] for ch in (1, 2)
                                  if self.values[ch]['OUTP:TIM:STAT']]
                        if timers and self.timer_start is not None:
                            expiry = max(timers) if fault == 'timer' else min(timers)
                            if clock.now - self.timer_start >= expiry:
                                for ch in (1, 2): self.values[ch]['CHAN:OUTP'] = 0.
                        if command == 'MEAS:CURR?':
                            self.cmds.append(command)
                            return '0'
                        if command == 'MEAS:VOLT?' and not self.values[self.active_ch]['CHAN:OUTP']:
                            self.cmds.append(command)
                            return '0'
                        return super().query(command)

                source = Source()
                source.tracking_fault = fault == 'tracking'
                class Meter:
                    channel = 1
                    configured = False
                    closed = False
                    def configure_voltage(self): self.configured = True
                    def measure(self):
                        if fault == 'io': raise OSError('meter link lost')
                        settings = source.values[self.channel]
                        voltage = settings['VOLT'] if settings['CHAN:OUTP'] else 0
                        return {'voltage_v': voltage + (.2 if fault == 'meter' else 0)}
                    def close(self): self.closed = True

                meter = Meter()
                answers = []
                def operator(prompt):
                    if fault == 'gate': raise KeyboardInterrupt()
                    token = next(line for line in prompt.splitlines() if line.startswith('CONFIRM '))
                    answers.append(token)
                    if 'MOVE CH1->CH2' in token: meter.channel = 2
                    return token
                evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H08')
                with patch('builtins.input', side_effect=operator), \
                        patch('time.monotonic', clock.monotonic), patch('time.sleep', clock.sleep), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = h08_stage.h08(source, evidence, meter)
                self.assertEqual(result, 'PASS' if fault is None else
                                 'ABORTED' if fault in ('io', 'gate') else 'FAIL',
                                 evidence.path.read_text()[-1500:])
                self.assertTrue(all(not values['CHAN:OUTP'] for values in source.values.values()))
                self.assertTrue(source.closed and meter.closed)
                self.assert_driver_path(evidence, source)
                records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
                if fault is None:
                    self.assertEqual(len([r for r in records if r['event'] == 'tracking_measurement']), 12)
                    self.assertEqual(len([r for r in records if r['event'] == 'tracking_timer_expiry']), 4)
                    self.assertEqual(len([r for r in records if r['event'] == 'tracking_zero_observation']), 4)
                    self.assertEqual(len([r for r in records if r['event'] == 'tracking_independent_ratio']), 6)
                    self.assertTrue(any('MOVE CH1->CH2' in a for a in answers))
                    self.assertTrue(any('DISCONNECT CH2' in a for a in answers))

    def test_adapter_confirmation_must_match_selected_serial_port(self):
        q = self.module()

        class Port:
            device = '/dev/ttyUSB3'
            vid = 0x10C4
            pid = 0xEA60
            description = 'CP2102 USB to UART Bridge Controller'

        class WrongPort:
            device = '/dev/ttyUSB1'
            vid = 0x0403
            pid = 0x6010
            description = 'FTDI Dual RS232-HS'

        ports = [Port(), WrongPort()]
        self.assertTrue(q.verify_serial_adapter('/dev/ttyUSB3', 'TTL 10C4:EA60', ports))
        with self.assertRaisesRegex(ValueError, 'different USB VID:PID|does not match'):
            q.verify_serial_adapter('/dev/ttyUSB1', 'TTL 10C4:EA60', ports)
        with self.assertRaisesRegex(ValueError, 'different USB VID:PID|does not match'):
            q.verify_serial_adapter('/dev/ttyUSB3', 'TTL 0403:6010', ports)

    def test_dry_run_never_constructs_transport(self):
        q = self.module()
        for args in (['list'], ['preflight', 'H01'], ['run', 'H01'],
                     ['preflight', 'H02'], ['run', 'H02'],
                     ['preflight', 'H05'], ['run', 'H05'],
                     ['preflight', 'H06'], ['run', 'H06'],
                     ['preflight', 'H06', '--recall'], ['run', 'H06', '--recall'],
                     ['preflight', 'H07'], ['run', 'H07'],
                     ['preflight', 'H08'], ['run', 'H08'],
                     ['preflight', 'H09'], ['run', 'H09'],
                     ['preflight', 'H10'], ['run', 'H10'], ['abort'], ['report']):
            with self.subTest(args=args), patch.object(q, 'open_transport', side_effect=AssertionError('port opened')), \
                    patch.object(q, 'open_meter', side_effect=AssertionError('meter opened')):
                with contextlib.redirect_stdout(io.StringIO()):
                    q.main(args)

    def test_h05_preflight_is_explicit_and_offline(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.preflight('H05', '/dev/ttyUSB3')
            text = output.getvalue()
            self.assertIn('CH1→CH2→CH3', text)
            self.assertIn('22 Ω', text)
            self.assertIn('×10', text)
            self.assertIn('10% при включении и 90% при выключении', text)
            self.assertIn('пересечения 0.5 V', text)
            self.assertIn('НЕ ВКЛЮЧАТЬ ВЫХОДЫ', text)

    def test_h07_csv_run_and_failure_use_common_driver_path(self):
        import h07_stage
        from test_driver import FakeTransport
        from keithley2231a import SafetyError

        q = self.module()
        self.assertTrue(q.stage_passed(q.stages()['H06'][-1]))
        self.assertTrue(q.stage_passed(q.stages()['H09'][-1]))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            q.preflight('H07', '/dev/ttyUSB3', load_transport='serial')
        for detail in ('CR 40 Ω', '180 s', '12 s', 'max_gap=10 s', 'CH2/CH3',
                       'НЕ ВКЛЮЧАТЬ ВЫХОДЫ', h07_stage.approval_token()):
            self.assertIn(detail, output.getvalue())

        class ClockStop:
            now = 10.0
            def monotonic(self): return self.now
            def sleep(self, seconds): self.now += seconds
            def is_set(self): return False
            def wait(self, seconds):
                self.sleep(seconds)
                return False

        class Source(FakeTransport):
            def write(self, command):
                clock.sleep(.02)
                return super().write(command)

            def query(self, command):
                # Include UART/command execution time: the requested interval is
                # not a promise that a three-channel scan can complete that fast.
                clock.sleep(.07)
                if command in ('MEAS:VOLT?', 'MEAS:CURR?'):
                    self.cmds.append(command)
                    values = self.values[self.active_ch]
                    volts = values['VOLT'] if values['CHAN:OUTP'] else 0.0
                    return str(volts if command == 'MEAS:VOLT?' else volts / 40.0)
                return super().query(command)

        class Load:
            live = True
            input = 'OFF'
            closed = False
            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_cr(self, resistance):
                if self.input != 'OFF' or resistance != 40.0:
                    raise AssertionError('incorrect H07 CR setup')
            def set_input(self, enabled): self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                volts = 4.0 if self.input == 'ON' and source.values[1]['CHAN:OUTP'] else 0.0
                if failure and volts:
                    volts = 0.0
                return {'kel103_voltage_v': volts, 'kel103_current_a': volts / 40.0,
                        'kel103_power_w': volts ** 2 / 40.0}
            def close(self): self.closed = True

        for failure in (False, True):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                clock, source, load = ClockStop(), Source(), Load()
                evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H07')
                answers = ['CONFIRM H07 CONNECT CH1 ALL_OFF LOAD=OFF',
                           'CONFIRM H07 DISCONNECT CH1 ALL_OFF LOAD=OFF']
                with patch('h07_stage.time.monotonic', clock.monotonic), \
                        patch('h07_stage.time.sleep', clock.sleep), \
                        patch('keithley2231a.monitor.threading.Event', return_value=clock), \
                        patch('builtins.input', side_effect=answers), \
                        contextlib.redirect_stdout(io.StringIO()):
                    outcome = h07_stage.h07(source, evidence, load)
                events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
                self.assertEqual(outcome, 'FAIL' if failure else 'PASS',
                                 [e for e in events if e['event'] == 'stage_error'])
                self.assert_driver_path(evidence, source)
                self.assertEqual(source.cmds.count('CHAN:OUTP 1'), 1)
                self.assertFalse(any(source.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
                self.assertEqual(load.input, 'OFF')
                self.assertTrue(load.closed)
                if not failure:
                    result = next(e for e in events if e['event'] == 'csv_verification')
                    self.assertGreaterEqual(result['channels']['1']['voltage_v']['count'], 30)
                    self.assertEqual(result['channels']['1']['skipped_gaps'], 1)
                    self.assertGreater(result['channels']['1']['Ah'], 0.0)
                    self.assertEqual(result['channels']['2']['Ah'], 0.0)
                    self.assertTrue(any(e['event'] == 'statistics_reset' for e in events))
                    # A broken CSV or summary must prevent a qualification PASS.
                    path = evidence.path.parent / 'telemetry.csv'
                    path.write_text(path.read_text().replace(',0.1,0.4,', ',0.2,0.8,', 1))
                    with self.assertRaises(SafetyError):
                        h07_stage.verify_csv(path, result['channels'], max_gap_s=10.0)

    def test_h06_preflight_is_explicit_and_offline(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.preflight('H06', '/dev/ttyUSB3', load_transport='serial',
                            load_serial_port='/dev/ttyACM0')
            text = output.getvalue()
            self.assertIn('KEL103: /dev/ttyACM0 @ 115200 baud 8N1', text)
            self.assertIn('CR 22 Ω', text)
            self.assertIn('только к одному каналу за раз', text)
            self.assertIn('Ручной сетевой цикл Output Recall в этом прогоне НЕ выполняется', text)
            self.assertIn('Разрешение на H06 timer pass', text)
            self.assertIn('LOAD=KEL103 DISCONNECTED USB=/dev/ttyACM0 BAUD=115200', text)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.main(['preflight', 'H06', '--recall', ])
            self.assertIn('111→000', output.getvalue())
            self.assertIn('101→101', output.getvalue())
            self.assertIn('010→010', output.getvalue())
            self.assertIn('000→000', output.getvalue())
            self.assertIn('SCOPE=DISCONNECTED', output.getvalue())

    def test_h06_recall_records_boot_before_shutdown_and_stops_on_fault(self):
        from h06_recall import recall
        from test_driver import FakeTransport

        cases = (('OFF', '111', '000'), ('ON', '101', '101'),
                 ('ON', '010', '010'), ('ON', '000', '000'))
        for fault in (None, 'unexpected_boot', 'wrong_panel', 'wrong_serial',
                      'boot_timeout', 'interrupted_cycle', 'interrupted_menu'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                sessions = [FakeTransport()]
                boot_prefixes = []

                class RecordedEvidence(self.module().Evidence):
                    def add(self, event, **fields):
                        if event == 'recall_boot_observation':
                            boot_prefixes.append(list(sessions[-1].cmds))
                        return super().add(event, **fields)

                class BootTransport(FakeTransport):
                    def query(self, command):
                        reply = super().query(command)
                        if fault == 'wrong_serial' and command == '*IDN?':
                            return reply.replace('TEST', 'OTHER')
                        return reply

                def reopen():
                    self.assertTrue(sessions[-1].closed)
                    previous = sessions[-1]
                    boot = BootTransport()
                    boot.values = {ch: dict(values) for ch, values in previous.values.items()}
                    mode = cases[len(sessions) - 1][0]
                    if mode == 'OFF':
                        for values in boot.values.values(): values['CHAN:OUTP'] = 0.0
                    if fault == 'unexpected_boot': boot.values[1]['CHAN:OUTP'] = 1.0
                    if fault == 'boot_timeout': boot.failed_query = 'CHAN:OUTP?'
                    sessions.append(boot)
                    return boot

                answers = []
                for mode, before, expected in cases:
                    case = f'{mode}_{before}'
                    answers.append(f'CONFIRM H06 RECALL CASE={case} RECALL={mode} '
                                   'SAVE_LAST=ON ALL_OFF CYCLE=AUTHORIZED')
                    answers.append(f'CONFIRM H06 RECALL CASE={case} POWER_CYCLED PANEL={expected}')
                answers.append('CONFIRM H06 RECALL RESTORED RECALL=OFF SAVE_LAST=ON ALL_OFF')
                if fault == 'interrupted_cycle': answers[1] = KeyboardInterrupt()
                if fault == 'interrupted_menu': answers[0] = KeyboardInterrupt()
                if fault == 'wrong_panel':
                    answers[1] = 'CONFIRM H06 RECALL CASE=OFF_111 POWER_CYCLED PANEL=100'
                evidence = RecordedEvidence(Path(tmp) / 'events.jsonl', 'H06')
                with patch('builtins.input', side_effect=answers), \
                        contextlib.redirect_stdout(io.StringIO()):
                    result = recall(sessions[0], evidence, reopen)
                events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
                self.assertEqual(result, 'PASS' if fault is None else
                                 'FAIL' if fault in ('unexpected_boot', 'wrong_panel')
                                 else 'ABORTED', events[-2:])
                self.assertEqual(len(sessions), 5 if fault is None else
                                 1 if fault in ('interrupted_cycle', 'interrupted_menu') else 2)
                sent = [event['trace']['command'] for event in events
                        if event['event'] == 'driver_trace' and
                        event['trace']['event'] in ('write', 'query')]
                self.assertEqual(sent, [command for session in sessions for command in session.cmds])
                for prefix in boot_prefixes:
                    self.assertTrue(all(command.endswith('?') or command == 'SYST:REM' or
                                        command.startswith('INST:NSEL ') for command in prefix), prefix)
                if fault is None:
                    self.assertEqual(len(boot_prefixes), 4)
                    self.assertEqual([event['case'] for event in events
                                      if event['event'] == 'recall_case_result'],
                                     ['OFF_111', 'ON_101', 'ON_010', 'ON_000'])
                    self.assertFalse(any(sessions[-1].values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
                elif fault == 'wrong_serial':
                    self.assertTrue(all(command.endswith('?') for command in sessions[-1].cmds))
                elif fault == 'boot_timeout':
                    self.assertEqual(sessions[-1].cmds[-1], 'CHAN:OUTP?')

    def test_h06_recall_cli_skips_load_and_rechecks_explicit_port(self):
        import h06_recall
        from test_driver import FakeTransport
        q = self.module()
        sessions = [FakeTransport(), FakeTransport()]

        def simulated_runner(transport, evidence, reopen):
            self.assertIs(transport, sessions[0])
            transport.close()
            reopened = reopen()
            self.assertIs(reopened, sessions[1])
            reopened.close()
            return 'PASS'

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'docs/hardware_reports').mkdir(parents=True)
            with patch.object(q, 'ROOT', root), \
                    patch.object(q, 'software_metadata', return_value={}), \
                    patch.object(q, 'stage_passed', return_value=True), \
                    patch.object(q, 'verify_serial_adapter', return_value=True) as verify, \
                    patch.object(q, 'open_transport', side_effect=sessions) as opened, \
                    patch.object(q.sys.stdin, 'isatty', return_value=True), \
                    patch.object(h06_recall, 'recall', side_effect=simulated_runner), \
                    patch('equipment.kel103.KEL103Serial', side_effect=AssertionError('load opened')), \
                    patch('equipment.kel103.KEL103UDP', side_effect=AssertionError('load opened')), \
                    patch('builtins.input', side_effect=['CP2102 10C4:EA60', h06_recall.TOKEN,
                                                       'CONFIRM H06 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF']), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = q.main(['run', 'H06', '--recall', '--execute', ])
            self.assertEqual(result, 0)
            self.assertEqual([call.args for call in opened.call_args_list],
                             [('/dev/ttyUSB1',), ('/dev/ttyUSB1',)])
            self.assertEqual([call.args for call in verify.call_args_list],
                             [('/dev/ttyUSB1', 'CP2102 10C4:EA60')] * 2)

    def test_h06_timer_expiry_and_reenable_use_common_driver_path(self):
        from h06_stage import h06
        from test_driver import FakeTransport

        class Clock:
            now = 10.0
            def monotonic(self): return self.now
            def sleep(self, seconds): self.now += seconds

        class TimedTransport(FakeTransport):
            def __init__(self, clock):
                super().__init__()
                self.clock = clock
                self.enabled_at = {}

            def _expire(self):
                for channel, values in self.values.items():
                    if (values['CHAN:OUTP'] and values['OUTP:TIM:STAT'] and
                            self.clock.now - self.enabled_at.get(channel, self.clock.now) >=
                            values['OUTP:TIM:DEL']):
                        values['CHAN:OUTP'] = 0.0

            def write(self, command):
                super().write(command)
                if command.startswith('CHAN:OUTP 1'):
                    self.enabled_at[self.active_ch] = self.clock.now

            def query(self, command):
                self._expire()
                return super().query(command)

        class Load:
            def __init__(self, transport):
                self.transport = transport
                self.input = 'OFF'
                self.resistance = None
                self.live = True
                self.closed = False

            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_cr(self, resistance):
                if self.input != 'OFF' or resistance != 22.0:
                    raise AssertionError('invalid CR setup')
                self.resistance = resistance
            def set_input(self, enabled):
                self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                values = self.transport.values[self.target_channel]
                enabled = bool(values['CHAN:OUTP'])
                voltage = values['VOLT'] if enabled and self.input == 'ON' else 0.0
                current = voltage / self.resistance if self.resistance else 0.0
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        clock = Clock()
        raw = TimedTransport(clock)
        load = Load(raw)
        answers = [
            'CONFIRM H06 CONNECT CH1 ALL_OFF LOAD=OFF',
            'CONFIRM H06 MOVE CH1->CH2 ALL_OFF LOAD=OFF',
            'CONFIRM H06 MOVE CH2->CH3 ALL_OFF LOAD=OFF',
            'CONFIRM H06 DISCONNECT CH3 ALL_OFF LOAD=OFF',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H06')
            with patch('h06_stage.time.monotonic', clock.monotonic), \
                    patch('h06_stage.time.sleep', clock.sleep), \
                    patch('builtins.input', side_effect=answers), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h06(raw, evidence, load)
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertEqual(outcome, 'PARTIAL',
                             [event for event in events
                              if event['event'] in ('stage_error', 'cleanup_error', 'query_outcome')])
            self.assert_driver_path(evidence, raw)
            expiries = [event for event in events if event['event'] == 'timer_expiry']
            reenabled = [event for event in events if event['event'] == 'timer_reenable']
            self.assertEqual([event['channel'] for event in expiries], [1, 2, 3])
            self.assertTrue(all(2.5 <= event['elapsed_s'] <= 4.0 for event in expiries))
            self.assertEqual([event['channel'] for event in reenabled], [1, 2, 3])
            self.assertEqual(raw.cmds.count('CHAN:OUTP 1'), 6)
            self.assertEqual(raw.cmds.count('OUTP:TIM:DEL 3.0'), 3)
            self.assertFalse(any(raw.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertTrue(all(raw.values[ch]['OUTP:TIM:STAT'] == 0 for ch in (1, 2, 3)))
            self.assertEqual(load.input, 'OFF')
            self.assertTrue(load.closed)
            self.assertIn('Output Recall AC power-cycle remains separately gated',
                          evidence.path.read_text())

            failed_clock = Clock()
            failed_raw = TimedTransport(failed_clock)
            failed_load = Load(failed_raw)
            def fail_cr_setup(resistance):
                raise ValueError(f'unsupported CR value: {resistance}')
            failed_load.configure_cr = fail_cr_setup
            with tempfile.TemporaryDirectory() as failed_tmp:
                failed_evidence = self.module().Evidence(
                    Path(failed_tmp) / 'events.jsonl', 'H06')
                with patch('h06_stage.time.monotonic', failed_clock.monotonic), \
                        patch('h06_stage.time.sleep', failed_clock.sleep), \
                        patch('builtins.input', return_value='CONFIRM H06 CONNECT CH1 ALL_OFF LOAD=OFF'), \
                        contextlib.redirect_stdout(io.StringIO()):
                    failed_outcome = h06(failed_raw, failed_evidence, failed_load)
                failed_events = [json.loads(line) for line in
                                 failed_evidence.path.read_text().splitlines()]
                self.assertEqual(failed_outcome, 'FAIL')
                self.assertFalse(any(event['event'] == 'cleanup_error'
                                     for event in failed_events))
                self.assertEqual(failed_raw.cmds.count('OUTP:TIM:STAT 0'), 6)
                self.assertFalse(any(failed_raw.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
                self.assertTrue(all(failed_raw.values[ch]['OUTP:TIM:STAT'] == 0
                                    for ch in (1, 2, 3)))
                self.assertEqual(failed_load.input, 'OFF')
                self.assert_driver_path(failed_evidence, failed_raw)

    def test_h05_scope_edge_analysis_and_transport_allowlist(self):
        from h05_stage import H05Scope, decode_isf, transitions, qualified_transitions

        self.assertEqual(transitions([(0, 0), (1, 1), (2, 1), (3, 0)]),
                         [(0.5, 'RISE'), (2.5, 'FALL')])
        noisy_fall = [(0, 0), (1, 1), (2, 1), (3, .80), (3.1, .50),
                      (3.2, .53), (3.3, .48), (3.4, .51), (3.5, .44),
                      (3.6, .20), (3.7, .05), (3.8, .03)]
        self.assertGreater(len(transitions(noisy_fall)), 2)
        self.assertEqual(qualified_transitions(noisy_fall),
                         [(0.1, 'RISE'), (2.5, 'FALL')])
        header = (b':WFMPRE:BYT_NR 1;BIT_NR 8;ENCDG BIN;BN_FMT RI;BYT_OR MSB;NR_PT 4;'
                  b'PT_FMT Y;XINCR 1;XZERO 0;PT_OFF 0;YMULT 1;YOFF 0;YZERO 0;'
                  b'XUNIT "s";YUNIT "V";WFID "CH1, synthetic";:CURVE #14')
        raw = header + struct.pack('>4b', -1, 0, 1, 0) + b'\n'
        rows = decode_isf(raw, 1)
        self.assertEqual(transitions(rows), [(1.5, 'RISE'), (2.5, 'FALL')])

        class Raw:
            def __init__(self): self.calls = []
            def write(self, command): self.calls.append(('write', command))
            def query(self, command): self.calls.append(('query', command)); return '0, No error'

        raw = Raw()
        with tempfile.TemporaryDirectory() as tmp:
            guarded = H05Scope(raw, self.module().Evidence(
                Path(tmp) / 'events.jsonl', 'H05'))
            with self.assertRaises(ValueError):
                guarded.write('OUTP 1')
            self.assertEqual(raw.calls, [])

    def test_h05_single_sequence_accepts_one_capture_after_counter_reset(self):
        from equipment.tds3064 import TDS3064B

        class Evidence:
            def __init__(self, path): self.path = path
            def add(self, *args, **kwargs): pass

        class Scope(TDS3064B):
            def __init__(self, path):
                super().__init__(path, Evidence(path / 'events.jsonl'), host='example.invalid', port=80)
                self.count_reads = 0
            def number(self, command):
                if command == 'ACQUIRE:STATE?': return 0
                if command == 'ACQUIRE:NUMACQ?':
                    self.count_reads += 1
                    return 13 if self.count_reads == 1 else 1
                if command == '*ESR?': return 0
                raise AssertionError(command)
            def command(self, command):
                if command == 'ALLEV?': return '0,"No events to report - queue empty"'
                if command == 'ACQUIRE:STATE RUN': return ''
                raise AssertionError(command)
            def post(self, endpoint, fields): return b'waveform'

        with tempfile.TemporaryDirectory() as tmp:
            scope = Scope(Path(tmp))
            with patch('equipment.tds3064.decode_isf', return_value=[(0.0, 0.0), (1.0, 1.0)]):
                scope.arm()
                waveforms = scope.collect()
            self.assertEqual(set(waveforms), {1, 2, 3})
            self.assertEqual(scope.count_reads, 2)

    def test_h05_enters_remote_before_channel_queries(self):
        from h05_stage import h05
        from test_driver import FakeTransport

        class Load:
            enabled = False
            def identify(self): return 'KORAD KEL103'
            def input_state(self): return 'ON' if self.enabled else 'OFF'
            def configure_cr(self, resistance): pass
            def set_input(self, enabled): self.enabled = enabled
            def measurements(self): return {'kel103_voltage_v': 1.0, 'kel103_current_a': 0.045}
            def close(self): pass

        class Scope:
            def __init__(self, *args): pass
            def configure(self): return {}
            def arm(self): pass
            def collect(self):
                return {ch: [(t, float(start <= t < stop)) for t in range(10)]
                        for ch, start, stop in ((1, 1, 8), (2, 2, 7), (3, 3, 6))}
            def number(self, command): return 0

        with tempfile.TemporaryDirectory() as tmp:
            raw, load = FakeTransport(), Load()
            evidence = self.module().Evidence(Path(tmp) / 'events.jsonl', 'H05')
            with patch('equipment.factory.open_scope', return_value=Scope()), patch('keithley2231a.device.time.sleep'), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h05(raw, evidence, load)
            self.assertEqual(outcome, 'PASS')
            self.assert_driver_path(evidence, raw)
            self.assertTrue(raw.closed)
            self.assertFalse(load.enabled)
            self.assertFalse(any(v['CHAN:OUTP'] for v in raw.values.values()))

    def test_probe_uses_driver_remote_setup_without_changing_outputs(self):
        from test_driver import FakeTransport

        fake = FakeTransport()
        fake.values[2]['CHAN:OUTP'] = 1.0
        initial = {ch: values.copy() for ch, values in fake.values.items()}
        output = io.StringIO()
        with patch('keithley2231a.SerialTransport', return_value=fake), \
                patch.object(sys, 'argv', ['probe.py', '--port', 'SIMULATED']), \
                contextlib.redirect_stdout(output):
            runpy.run_path(str(Path(__file__).resolve().parents[2] / 'examples/probe.py'),
                           run_name='__main__')
        self.assertNotIn('ERROR', output.getvalue())
        self.assertEqual(fake.values, initial)
        self.assertTrue(fake.closed)
        self.assertNotIn('OUTP 0', fake.cmds)

    def test_h03_scope_forbids_global_on(self):
        from channel_stage import H03Scope
        from test_driver import FakeTransport
        with tempfile.TemporaryDirectory() as tmp:
            raw = FakeTransport()
            guarded = H03Scope(raw, self.module().Evidence(Path(tmp) / 'scope.jsonl', 'H03'))
            with self.assertRaises(ValueError): guarded.write('OUTP 1')
            self.assertEqual(raw.cmds, [])

    def test_h03_individual_on_requires_authorization_before_transmission(self):
        from channel_stage import H03Scope
        from test_driver import FakeTransport

        with tempfile.TemporaryDirectory() as tmp:
            for authorized in (None, 1, 3):
                with self.subTest(authorized=authorized):
                    fake = FakeTransport()
                    # This test isolates the authorized ON step after Remote setup.
                    fake.write('SYST:REM')
                    fake.cmds.clear()
                    fake.active_ch = 3
                    evidence = self.module().Evidence(
                        Path(tmp) / f'gate_{authorized}.jsonl', 'H03')
                    guarded = H03Scope(fake, evidence, start_channel=3)
                    # Isolate the ON step of an otherwise approved plan.
                    guarded.selected = 3
                    guarded.authorized_channel = authorized
                    if authorized == 3:
                        with contextlib.redirect_stdout(io.StringIO()):
                            guarded.write('CHAN:OUTP 1')
                        self.assertEqual(fake.values[3]['CHAN:OUTP'], 1)
                    else:
                        with self.assertRaises(ValueError):
                            guarded.write('CHAN:OUTP 1')
                        self.assertEqual(fake.cmds, [])
                        self.assertEqual(fake.values[3]['CHAN:OUTP'], 0)

    def test_h03_mistyped_gate_waits_for_valid_confirmation(self):
        from common import operator_gate

        with patch('builtins.input', side_effect=('mistyped confirmation', 'CONFIRM TEST')), \
                contextlib.redirect_stdout(io.StringIO()):
            operator_gate('CONFIRM TEST', 'Verify OFF')

    def test_h03_diagnostic_preflight_is_offline_and_restricts_scope(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                q.preflight('H03', '/dev/ttyUSB1', start_channel=3,
                            load_transport='serial', diagnose_status=True)
            self.assertIn('DIAGNOSTIC_CH3', output.getvalue())
            for args in (['run', 'H01', '--diagnose-status', '--execute'],
                         ['run', 'H03', '--diagnose-status', '--execute']):
                with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        q.main(args)

    def test_kel103_udp_is_lf_terminated_and_rejects_unknown_commands(self):
        from equipment.kel103 import KEL103UDP

        class DatagramFake:
            def __init__(self): self.sent, self.closed = [], False
            def settimeout(self, timeout): self.timeout = timeout
            def sendto(self, payload, address): self.sent.append((payload, address))
            def recvfrom(self, size): return b'OFF\n', ('192.168.22.106', 18190)
            def close(self): self.closed = True

        fake = DatagramFake()
        load = KEL103UDP('192.168.22.106', 18190, sock=fake)
        self.assertEqual(load.query(':INP?'), 'OFF')
        self.assertEqual(fake.sent, [(b':INP?\n', ('192.168.22.106', 18190))])
        with self.assertRaises(ValueError): load.write(':SYST:FACTRESET')
        self.assertEqual(len(fake.sent), 1)
        load.close()
        self.assertTrue(fake.closed)

    def test_driver_status_wire_trace_preserves_bytes_and_excludes_identity(self):
        from types import SimpleNamespace
        from keithley2231a import Keithley2231A, SerialTransport

        class SerialFake:
            in_waiting = 0
            selected = 1
            def write(self, data):
                self.command = data.decode().strip()
                if self.command.startswith('INST:NSEL '): self.selected = int(self.command[-1])
                return len(data)
            def flush(self): pass
            def close(self): pass
            def readline(self, size):
                if self.command == '*IDN?':
                    return b'Keithley instruments, 2231A-30-3, PRIVATE-SERIAL, 1.04-0.01\r\n'
                if self.command == 'INST:NSEL?': return f'{self.selected}\r\n'.encode()
                if self.command == 'OUTP:SER?': return b'0\r\n'
                if self.command == 'CHAN:OUTP?': return b'0\r\n'
                return b'8\r\n'

        with tempfile.TemporaryDirectory() as tmp:
            evidence = self.module().Evidence(Path(tmp) / 'wire.jsonl', 'H03')
            with patch.dict(sys.modules, {'serial': SimpleNamespace(Serial=lambda **kw: SerialFake())}):
                wire = SerialTransport('SIMULATED')
            dev = Keithley2231A(wire, debug=True, debug_wire_trace=lambda record: evidence.add(**record))
            dev.diagnostic_query('*IDN?')
            self.assertEqual(dev.diagnostic_query('STAT:OPER:INST:ISUM3:COND?'), '8')
            content = evidence.path.read_text()
            self.assertNotIn('PRIVATE-SERIAL', content)
            records = [json.loads(line) for line in content.splitlines()]
            self.assertEqual([record['event'] for record in records], ['status_wire_write', 'status_wire_read'])
            self.assertEqual(records[0]['tx_hex'], b'STAT:OPER:INST:ISUM3:COND?\r\n'.hex(' '))
            self.assertEqual(records[1]['rx_hex'], '38 0d 0a')
            Keithley2231A(wire).output_states()
            self.assertEqual(evidence.path.read_text(), content)
            wire.close()

    def test_kel103_cr_setup_is_input_off_only_and_verified(self):
        from equipment.kel103 import KEL103UDP

        class DatagramFake:
            def __init__(self, replies): self.replies, self.sent = list(replies), []
            def settimeout(self, timeout): pass
            def sendto(self, payload, address): self.sent.append(payload)
            def recvfrom(self, size): return self.replies.pop(0), ('192.168.22.106', 18190)
            def close(self): pass

        fake = DatagramFake((b'OFF', b'CR', b'CR', b'40.000OHM'))
        load = KEL103UDP('192.168.22.106', 18190, sock=fake)
        load.configure_cr(40.0)
        self.assertEqual(fake.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                     b':RES 40OHM\n', b':FUNC?\n', b':RES?\n'])

        h04p = DatagramFake((b'OFF', b'CR', b'CR', b'11.000OHM', b'OFF', b'ON'))
        load = KEL103UDP('192.168.22.106', 18190, sock=h04p)
        self.assertEqual(load.configure_cr(11.0), 11.0)
        load.set_input(True)
        self.assertEqual(h04p.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                     b':RES 11OHM\n', b':FUNC?\n', b':RES?\n',
                                     b':INP?\n', b':INP ON\n', b':INP?\n'])

        active = DatagramFake((b'ON',))
        load = KEL103UDP('192.168.22.106', 18190, sock=active)
        with self.assertRaises(Exception): load.configure_cr(12.0)
        self.assertEqual(active.sent, [b':INP?\n'])

        unconfigured = DatagramFake(())
        load = KEL103UDP('192.168.22.106', 18190, sock=unconfigured)
        with self.assertRaises(Exception): load.set_input(True)
        self.assertEqual(unconfigured.sent, [])

        high_voltage = DatagramFake((b'OFF', b'CR', b'CR', b'2000.000OHM'))
        load = KEL103UDP('192.168.22.106', 18190, sock=high_voltage)
        self.assertEqual(load.configure_cr(2000.0), 2000.0)
        self.assertEqual(high_voltage.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                             b':RES 2000OHM\n', b':FUNC?\n', b':RES?\n'])
        before = list(high_voltage.sent)
        with self.assertRaises(ValueError): load.configure_cr(100.0)
        self.assertEqual(high_voltage.sent, before)

    def test_kel103_serial_uses_bounded_lf_protocol_and_latches_link_errors(self):
        from equipment.kel103 import KEL103Serial

        class SerialFake:
            def __init__(self): self.sent, self.replies, self.closed = [], [b'OFF\r\n'], False
            @property
            def in_waiting(self): return 0
            def write(self, payload): self.sent.append(payload); return len(payload)
            def flush(self): pass
            def readline(self, size): return self.replies.pop(0)
            def close(self): self.closed = True

        fake = SerialFake()
        load = KEL103Serial('/dev/ttyACM0', expected_vid=0x0416, expected_pid=0x5011, baudrate=115200, serial_port=fake)
        self.assertEqual(load.query(':INP?'), 'OFF')
        load.write(':INP OFF')
        self.assertEqual(fake.sent, [b':INP?\n', b':INP OFF\n'])
        with self.assertRaises(ValueError): load.write(':SYST:FACTRESET')
        load.close()
        self.assertTrue(fake.closed)

        failed = SerialFake()
        failed.replies = [b'']
        load = KEL103Serial('/dev/ttyACM0', expected_vid=0x0416, expected_pid=0x5011, baudrate=115200, serial_port=failed)
        with self.assertRaisesRegex(OSError, 'incomplete/oversized'):
            load.query(':INP?')
        with self.assertRaisesRegex(OSError, 'link uncertain'):
            load.query(':INP?')
        self.assertEqual(failed.sent, [b':INP?\n'])

    def test_kel103_high_current_cr_requires_input_off_and_is_verified_before_enable(self):
        from equipment.kel103 import KEL103Serial
        from keithley2231a import SafetyError

        class SerialFake:
            def __init__(self, replies): self.sent, self.replies = [], list(replies)
            @property
            def in_waiting(self): return 0
            def write(self, payload): self.sent.append(payload); return len(payload)
            def flush(self): pass
            def readline(self, size): return self.replies.pop(0)
            def close(self): pass

        fake = SerialFake((b'OFF\n', b'CR\n', b'CR\n', b'0.500OHM\n',
                           b'OFF\n', b'ON\n'))
        load = KEL103Serial('/dev/ttyACM0', expected_vid=0x0416, expected_pid=0x5011, baudrate=115200, serial_port=fake)
        self.assertTrue(hasattr(load, 'configure_cr'),
                        'KEL103 bounded CR setup is missing')
        self.assertEqual(load.configure_cr(.5), .5)
        load.set_input(True)
        self.assertEqual(fake.sent, [b':INP?\n', b':FUNC CR\n', b':FUNC?\n',
                                     b':RES 0.5OHM\n', b':FUNC?\n', b':RES?\n',
                                     b':INP?\n', b':INP ON\n', b':INP?\n'])
        before = list(fake.sent)
        with self.assertRaises(ValueError): load.configure_cr(.4)
        self.assertEqual(fake.sent, before)

        active = SerialFake((b'ON\n',))
        load = KEL103Serial('/dev/ttyACM0', expected_vid=0x0416, expected_pid=0x5011, baudrate=115200, serial_port=active)
        with self.assertRaises(SafetyError): load.configure_cr(.5)
        self.assertEqual(active.sent, [b':INP?\n'])

    def test_kel103_serial_refuses_wrong_usb_device_before_open(self):
        from equipment.kel103 import KEL103Serial
        try:
            from serial.tools import list_ports
        except ImportError:
            self.skipTest('pyserial is not installed')
        with patch.object(list_ports, 'comports', return_value=[
                type('Port', (), {'device': '/dev/ttyACM0', 'vid': 0x10C4,
                                  'pid': 0xEA60})()]), \
             patch('serial.Serial') as serial_open:
            with self.assertRaisesRegex(OSError, 'identity mismatch'):
                KEL103Serial('/dev/ttyACM0', expected_vid=0x0416, expected_pid=0x5011, baudrate=115200)
            serial_open.assert_not_called()

    def test_h03_usb_load_preflight_requires_explicit_usb_identity(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('PSU port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H03', ]), 0)
        text = output.getvalue()
        self.assertIn('USB /dev/ttyACM0', text)
        self.assertIn('115200 baud 8N1', text)
        self.assertIn('USB=/dev/ttyACM0 BAUD=115200', text)
        self.assertIn('ERRBASE=CHECK', text)

    def test_h03_preflight_is_offline_and_states_manual_load_points(self):
        q = self.module()
        self.assertTrue(q.stage_passed('PASS'))
        self.assertTrue(q.stage_passed('PASS (second attempt; first FAIL)'))
        self.assertTrue(q.stage_passed('**PASS** (reviewed functional scope; caveat)'))
        self.assertFalse(q.stage_passed('PASSING'))
        self.assertFalse(q.stage_passed('NOT_PASS'))
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H03']), 0)
        text = output.getvalue()
        self.assertIn('KEL103 USB', text)
        self.assertIn('CR 40.0 Ω', text)
        self.assertIn('CR 12.0 Ω', text)
        self.assertIn('считывает U/I/P', text)
        self.assertIn('перестановки CH1→CH2 и CH2→CH3', text)
        self.assertIn('отсутствующие CV/CC-биты OPER записываются как оговорка', text)
        self.assertIn('DRY RUN', text)
        with contextlib.redirect_stdout(output := io.StringIO()):
            q.preflight('H03', '/dev/ttyUSB1', start_channel=3)
        self.assertIn('CONFIRM H03 RESUME_CH3 INDEPENDENT ALL_OFF', output.getvalue())
        self.assertIn('questionable', output.getvalue().lower())

        with contextlib.redirect_stdout(output := io.StringIO()):
            q.preflight('H03', '/dev/ttyUSB3', load_transport='serial',
                        load_serial_port='/dev/ttyACM0', load_connected=True)
        text = output.getvalue()
        self.assertIn('CONFIRM H03 INDEPENDENT ALL_OFF LOAD=KEL103 CONNECTED '
                      'USB=/dev/ttyACM0 BAUD=115200 RECALL=OFF CH1=V1.00_I0.100_'
                      'VCAP4.00_ICAP0.300 CH2=V1.50_I0.200_VCAP4.05_ICAP0.400 '
                      'CH3=V2.00_I0.300_VCAP4.10_ICAP0.500 ERRBASE=CHECK', text)
        self.assertIn('перед профилем проверит все выходы OFF', text)

    def test_reviewed_pass_and_requalification_are_distinct(self):
        q = self.module()
        self.assertTrue(q.stage_passed(q.stages()['H02'][-1]))
        self.assertTrue(q.stage_passed(q.stages()['H03'][-1]))
        self.assertTrue(q.stage_passed(q.stages()['H04'][-1]))
        self.assertTrue(q.stage_passed('**PASS (OFF-only; reviewed)**'))
        self.assertTrue(q.stage_passed(q.stages()['H01'][-1]))
        self.assertTrue(q.stage_passed(q.stages()['H11'][-1]))
        self.assertTrue(q.stage_passed(
            '**PASS (HW OBSERVED; reviewed; [evidence](report.md))**'))
        self.assertFalse(q.stage_passed('NOT_RUN (requalification; historical PASS)'))

    def test_h03_resume_requires_reviewed_prior_points_and_off_confirmation(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            ch1_path, ch2_path = Path(tmp) / 'ch1.jsonl', Path(tmp) / 'ch2.jsonl'
            ch1_events = [
                {'test_id': 'H03', 'event': 'measurement_review', 'channel': 1,
                 'mode': mode, 'review_outcome': 'PASS'}
                for mode in ('CV', 'CC')
            ]
            ch1_events.append({'test_id': 'H03', 'event': 'outcome',
                               'physical_off_confirmed': True})
            ch2_events = [
                {'test_id': 'H03', 'event': 'measurement_review', 'channel': 2,
                 'mode': mode, 'review_outcome': 'PASS'}
                for mode in ('CV', 'CC')
            ]
            ch2_events.append({'test_id': 'H03', 'event': 'outcome',
                               'physical_off_confirmed': True})
            for records in (ch1_events, ch2_events):
                records.insert(0, {'test_id': 'H03', 'event': 'metadata',
                                   'source_sha256': q.software_metadata()['source_sha256']})
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            ch2_path.write_text('\n'.join(json.dumps(event) for event in ch2_events), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)
            source = ch1_events[0]['source_sha256']
            ch1_events[0]['source_sha256'] = 'older-driver-source'
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'current source'):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)
            ch1_events[0]['source_sha256'] = source
            ch1_events.pop()
            ch2_events.pop()
            ch1_path.write_text('\n'.join(json.dumps(event) for event in ch1_events), encoding='utf-8')
            ch2_path.write_text('\n'.join(json.dumps(event) for event in ch2_events), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'OFF confirmation'):
                q.validate_h03_checkpoint([ch1_path, ch2_path], 3)

    def test_h03_profile_starts_with_distinct_channel_values_and_caps(self):
        from channel_stage import profile

        channel_values = tuple(
            (channel.initial_voltage, channel.initial_current,
             channel.max_voltage, channel.max_current)
            for channel in profile().channels)
        self.assertEqual(channel_values, (
            (1.00, 0.100, 4.00, 0.300),
            (1.50, 0.200, 4.05, 0.400),
            (2.00, 0.300, 4.10, 0.500)))

    def test_h03_success_trace_and_error_170_stop(self):
        """Exercise the bounded H03 source/load flow using simulated I/O only."""
        q = self.module()
        from channel_stage import h03
        from test_driver import FakeTransport

        def operator_answers(disagree_cc=False):
            answers = []
            for channel in (1, 2, 3):
                if channel > 1:
                    answers.append(f'CONFIRM H03 MOVE CH{channel - 1}->CH{channel} ALL_OFF LOAD=OFF')
                for mode, resistance in (('CV', '40.0'), ('CC', '12.0')):
                    answers.extend((
                        f'CONFIRM H03 CH{channel} {mode} CR={resistance} CONNECTED PSU=OFF',
                        f'CONFIRM H03 DISPLAY CH{channel} PSU={mode} KEL=CR INPUT=ON',
                        f'CONFIRM H03 CH{channel} {mode} PSU=OFF LOAD=OFF'))
            return answers

        class LoadFake:
            def __init__(self):
                self.port = 18190
                self.input = 'OFF'
                self.resistance = None
                self.cc_voltage = 3.0
                self.live = True
                self.closed = False
            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_cr(self, resistance):
                if self.input != 'OFF': raise RuntimeError('load input active')
                self.resistance = resistance
                return resistance
            def set_input(self, enabled): self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                return ({'kel103_voltage_v': 4.0, 'kel103_current_a': .1, 'kel103_power_w': .4}
                        if self.resistance == 40.0 else
                        {'kel103_voltage_v': self.cc_voltage, 'kel103_current_a': .25,
                         'kel103_power_w': self.cc_voltage * .25})
            def close(self): self.closed = True

        class MeterFake(FakeTransport):
            def query(self, command):
                key = command.upper().removesuffix('?')
                for long, short in (('STATUS', 'STAT'), ('OPERATION', 'OPER'),
                                    ('QUESTIONABLE', 'QUES'), ('INSTRUMENT', 'INST'),
                                    ('ISUMMARY', 'ISUM'), ('CONDITION', 'COND')):
                    key = key.replace(long, short)
                if key in ('STAT:OPER:INST:ISUM3:ENAB', 'STAT:QUES:INST:ISUM3:ENAB'):
                    self.cmds.append(command)
                    return '0'
                if key == 'SYST:ERR':
                    self.cmds.append(command)
                    if getattr(self, 'initial_170', False) and not self.initial_170_consumed:
                        self.initial_170_consumed = True
                        return '170,"Invalid command"'
                    if getattr(self, 'inject_170', False):
                        self.inject_170 = False
                        return '170,"Invalid command"'
                    return '0,"No error"'
                if key == 'STAT:OPER:INST:ISUM1:COND':
                    return self._status(command, 1)
                if key == 'STAT:OPER:INST:ISUM2:COND':
                    return self._status(command, 2)
                if key == 'STAT:OPER:INST:ISUM3:COND':
                    return self._status(command, 3)
                if key in ('STAT:QUES:INST:ISUM1:COND',
                           'STAT:QUES:INST:ISUM2:COND',
                           'STAT:QUES:INST:ISUM3:COND'):
                    channel = int(key.split('ISUM')[1].split(':')[0])
                    return self._status(command, channel)
                if key in ('MEAS:VOLT', 'MEAS:CURR', 'MEAS:POW'):
                    self.cmds.append(command)
                    values = self.values[self.active_ch]
                    cc = values['CURR'] == .25
                    volts, amps = (getattr(self, 'cc_voltage', 3.0), .25) if cc else (4.0, .1)
                    if key == 'MEAS:VOLT': return str(volts)
                    if key == 'MEAS:CURR': return str(amps)
                    return str(volts * amps)
                return super().query(command)

            def _status(self, command, channel):
                self.cmds.append(command)
                if not self.values[channel]['CHAN:OUTP']:
                    return '0'
                if getattr(self, 'missing_mode_status', False) and self.values[channel]['CURR'] == .25:
                    return '0'
                if (command.upper().startswith(('STAT:OPER:', 'STATUS:OPERATION:')) and channel == 3
                        and getattr(self, 'channel3_status_override', None) is not None):
                    return str(self.channel3_status_override)
                if (command.upper().startswith(('STAT:QUES:', 'STATUS:QUESTIONABLE:')) and channel == 3
                        and getattr(self, 'channel3_status_override', None) is not None):
                    return '2' if self.values[channel]['CURR'] == .25 else '1'
                return '10' if self.values[channel]['CURR'] == .25 else '9'

            def write(self, command):
                super().write(command)
                if command == 'CHAN:OUTP 1': self.had_output_on = True
                if (command == 'OUTP 0' and getattr(self, 'ignore_global_off', False)
                        and getattr(self, 'had_output_on', False)):
                    self.values[1]['CHAN:OUTP'] = 1.
                if command == 'VOLT:LIM 4.00' and getattr(self, 'fail_next_limit', False):
                    self.inject_170 = True

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'success.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, load_connected=True)
            self.assertEqual(outcome, 'PASS', [line for line in evidence.path.read_text().splitlines()
                                               if 'stage_error' in line])
            self.assert_driver_path(evidence, fake)
            self.assertTrue(fake.closed)
            self.assertTrue(load.closed)
            self.assertEqual(load.input, 'OFF')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            recovery = next(record for record in records
                            if record['event'] == 'connected_load_recovery_preflight')
            self.assertEqual(recovery['output_states'], {'1': False, '2': False, '3': False})
            self.assertLess(fake.cmds.index('CHAN:OUTP?'), fake.cmds.index('VOLT:LIM 4.00'))
            self.assertEqual(sum(command == 'CHAN:OUTP 1' for command in fake.cmds), 6)
            self.assertEqual(sum(command == 'CHAN:OUTP 0' for command in fake.cmds), 6)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(evidence.path.read_text().count('"event": "measurement"'), 6)
            live_load_checks = [record for record in records
                                if record['event'] == 'kel103_input_during_measurement']
            self.assertEqual(len(live_load_checks), 6)
            self.assertTrue(all(record['input_state'] == 'ON' for record in live_load_checks))
            self.assertIn('"outcome": "PASS"', evidence.path.read_text())

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            fake.missing_mode_status = True
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'missing_mode_status.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'PARTIAL')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertFalse(any(record['event'] == 'stage_error' for record in records))
            self.assertEqual(len([record for record in records if record['event'] == 'measurement']), 6)

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            fake.initial_170 = True
            fake.initial_170_consumed = False
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'preexisting170.jsonl', 'H03')
            with contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertEqual(fake.cmds[0], 'SYST:ERR?')
            self.assertNotIn('OUTP:SER 0', fake.cmds)
            self.assertIn('pre-existing instrument error', evidence.path.read_text())

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.fail_next_limit = True
            evidence = q.Evidence(Path(tmp) / 'error170.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertFalse(any(command == 'CHAN:OUTP 1' for command in fake.cmds))
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertTrue(any(event.get('response') == '170,"Invalid command"' for event in events))

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.cc_voltage = 3.08919
            load.cc_voltage = 3.008
            fake.ignore_global_off = True
            evidence = q.Evidence(Path(tmp) / 'measurement_mismatch.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers(disagree_cc=True)), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'FAIL')
            self.assertIn('CHAN:OUTP 0', fake.cmds)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            failed_measurement = next(event for event in events
                                      if event.get('event') == 'measurement'
                                      and event.get('channel') == 1 and event.get('mode') == 'CC')
            self.assertEqual(failed_measurement['kel103_voltage_v'], 3.008)
            self.assertTrue(any(event.get('event') == 'cleanup_error' for event in events) is False)

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            fake.channel3_status_override = 8
            evidence = q.Evidence(Path(tmp) / 'channel3_status_mismatch.jsonl', 'H03')
            with patch('builtins.input', side_effect=operator_answers()), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load)
            self.assertEqual(outcome, 'PASS')
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(load.input, 'OFF')
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            diagnostic = next(event for event in events
                              if event.get('event') == 'measurement'
                              and event.get('channel') == 3 and event.get('mode') == 'CV')
            self.assertEqual(diagnostic['keithley_status_mask'], 8)
            self.assertEqual(diagnostic['keithley_questionable_status_mask'], 1)
            self.assertTrue(any(event.get('event') == 'operator_display_confirmation'
                                and event.get('channel') == 3 and event.get('keithley_panel_mode') == 'CV'
                                for event in events))
            self.assertEqual([event['mode'] for event in events
                              if event.get('event') == 'measurement'
                              and event.get('channel') == 3], ['CV', 'CC'])
            self.assertEqual(sum(event.get('event') == 'status_caveat'
                                 and event.get('channel') == 3 for event in events), 2)
            self.assertFalse(any(event.get('event') == 'stage_error' for event in events))

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'resume_ch3.jsonl', 'H03')
            answers = (
                'CONFIRM H03 CH3 CV CR=40.0 CONNECTED PSU=OFF',
                'CONFIRM H03 DISPLAY CH3 PSU=CV KEL=CR INPUT=ON',
                'CONFIRM H03 CH3 CV PSU=OFF LOAD=OFF',
                'CONFIRM H03 CH3 CC CR=12.0 CONNECTED PSU=OFF',
                'CONFIRM H03 DISPLAY CH3 PSU=CC KEL=CR INPUT=ON',
                'CONFIRM H03 CH3 CC PSU=OFF LOAD=OFF',
            )
            with patch('builtins.input', side_effect=answers), contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3)
            self.assertEqual(outcome, 'PASS')
            events = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertFalse(any(event.get('action') == 'load relocation' for event in events))
            self.assertEqual([event['mode'] for event in events
                              if event.get('event') == 'measurement'], ['CV', 'CC'])

        with tempfile.TemporaryDirectory() as tmp:
            fake = MeterFake()
            fake.channel3_status_override = 8
            load = LoadFake()
            evidence = q.Evidence(Path(tmp) / 'status_diagnosis.jsonl', 'H03')
            answers = operator_answers()[-6:]
            with patch('builtins.input', side_effect=answers), \
                    patch('channel_stage.time.sleep'), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3, diagnose_status=True)
            self.assertEqual(outcome, 'PASS')
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            snapshots = [record for record in records if record['event'] == 'status_diagnostic']
            self.assertEqual([(record['mode'], record['phase'], record['selected_channel'])
                              for record in snapshots],
                             [('CV', 'early', 3), ('CV', 'after_display', 3),
                              ('CV', 'selected_other_channel', 1),
                              ('CC', 'early', 3), ('CC', 'after_display', 3),
                              ('CC', 'selected_other_channel', 1)])
            for record in snapshots:
                self.assertEqual(record['responses']['STAT:OPER:INST:ISUM3:COND?'], '8')
                self.assertEqual(record['responses'][
                    'STATus:OPERation:INSTrument:ISUMmary3:CONDition?'], '8')
            self.assertEqual(sum(record['event'] == 'status_caveat' for record in records), 2)
            self.assertEqual(fake.cmds.count('CHAN:OUTP 1'), 2)
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))
            self.assertEqual(load.input, 'OFF')

        with tempfile.TemporaryDirectory() as tmp:
            class TimedOutLoad(LoadFake):
                def identify(self): raise TimeoutError('simulated UDP timeout')

            fake = MeterFake()
            load = TimedOutLoad()
            evidence = q.Evidence(Path(tmp) / 'load_timeout.jsonl', 'H03')
            with contextlib.redirect_stdout(io.StringIO()):
                outcome = h03(fake, evidence, load, start_channel=3)
            self.assertEqual(outcome, 'ABORTED')
            self.assertEqual(fake.cmds[0], 'SYST:REM')
            self.assertGreater(fake.cmds.index('OUTP 0'), fake.cmds.index('SYST:REM'))
            self.assertFalse(any(fake.values[ch]['CHAN:OUTP'] for ch in (1, 2, 3)))

    def test_unattended_and_future_stages_reject_before_open(self):
        q = self.module()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)
                self.assertNotEqual(q.main(['run', 'H03', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', 'yes', EOFError()]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H01', '--execute']), 0)
            with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['TTL 1234:5678', q.TOKEN, EOFError()]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(q.main(['run', 'H02', '--execute']), 0)

    def test_h02_profile_trace_and_stop_on_first_error(self):
        """Exercise the exact profile trace using simulated I/O only."""
        q = self.module()
        from profile_stage import (H02Scope, H02_DEBUG_TOKEN, H02_HARD_PROBES,
                                   H02_PANEL_TOKEN,
                                   TOKEN as H02_TOKEN, guarded_profile, h02)
        from test_driver import FakeTransport
        settings = guarded_profile().channels
        self.assertEqual([(p.initial_voltage, p.initial_current, p.max_voltage, p.max_current)
                          for p in settings],
                         [(1.0, .1, 2.0, .3), (1.5, .2, 3.0, .4), (2.0, .3, 4.0, .5)])
        self.assertIn('CH1=V1.00_I0.100_VCAP2.00_ICAP0.300', H02_TOKEN)
        self.assertIn('CH2=V1.50_I0.200_VCAP3.00_ICAP0.400', H02_TOKEN)
        self.assertIn('CH3=V2.00_I0.300_VCAP4.00_ICAP0.500', H02_TOKEN)
        self.assertIn('CH1=2.01 CLAMPED=2.00', H02_PANEL_TOKEN)
        self.assertIn('CH2=3.01 CLAMPED=3.00', H02_PANEL_TOKEN)
        self.assertIn('CH3=4.01 CLAMPED=4.00', H02_PANEL_TOKEN)
        for bad in ('none', 'error170', 'timeout', 'cap_mismatch', 'initial_on', 'identity_change'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                class ProfileFake(FakeTransport):
                    pending_error = False
                    reduced_cap_written = False
                    idn_count = 0
                    pending_hard_rejection = False
                    def write(self, command):
                        if command in H02_HARD_PROBES.values():
                            self.cmds.append(command)
                            self.pending_hard_rejection = True
                            return
                        super().write(command)
                        if bad == 'error170' and command == 'CURR 0': self.pending_error = True
                        if command.startswith('VOLT:LIM '): self.reduced_cap_written = True
                    def query(self, command):
                        if bad == 'identity_change' and command == '*IDN?':
                            self.idn_count += 1
                            if self.idn_count == 2:
                                self.cmds.append(command)
                                return 'Keithley instruments, 2231A-30-3, OTHER, 1.04-1.04'
                        answer = super().query(command)
                        if command == 'SYST:ERR?' and self.pending_hard_rejection:
                            self.pending_hard_rejection = False
                            return '120,"Parameter overflowed"'
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
                def confirm(prompt):
                    if H02_PANEL_TOKEN in prompt:
                        for channel, limit in ((1, 2.), (2, 3.), (3, 4.)):
                            fake.values[channel]['VOLT'] = limit
                        return H02_PANEL_TOKEN
                    if H02_DEBUG_TOKEN in prompt: return H02_DEBUG_TOKEN
                    raise AssertionError(prompt)
                output = io.StringIO()
                with patch('builtins.input', side_effect=confirm), \
                        contextlib.redirect_stdout(output):
                    outcome = h02(fake, evidence)
                records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL',
                                 [r for r in records if r['event'] == 'stage_error'])
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd in ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK', '*CLS', '*RST') for cmd in fake.cmds))
                if bad == 'none':
                    self.assertIn('выберите канал кнопкой CH1/CH2/CH3, нажмите V-set', output.getvalue())
                    self.assertIn('введите значение и нажмите Enter', output.getvalue())
                    self.assertIn('Не меняйте Max Volt Set', output.getvalue())
                    self.assert_driver_path(evidence, fake)
                    self.assertEqual(fake.active_ch, 1)
                    for ch, (limit, current_cap, voltage, current) in enumerate(
                            ((2., .3, 1., .1), (3., .4, 1.5, .2), (4., .5, 2., .3)), 1):
                        self.assertEqual(fake.values[ch]['VOLT'], voltage)
                        self.assertEqual(fake.values[ch]['CURR'], current)
                        self.assertEqual(fake.values[ch]['VOLT:LIM'], limit)
                        self.assertEqual(fake.values[ch]['VOLT:LIM:STAT'], 1.)
                        self.assertEqual(fake.values[ch]['CHAN:OUTP'], 0.)
                    self.assertEqual(evidence.path.read_text().count('"event": "case"'), 28)
                if bad == 'timeout': self.assertEqual(fake.cmds[-1], 'VOLT:LIM?')
                if bad in ('error170', 'initial_on', 'identity_change'):
                    self.assertNotIn('VOLT:LIM 4.00', fake.cmds)
                self.assertNotIn(', TEST,', evidence.path.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            io_guard = H02Scope(fake, q.Evidence(Path(tmp) / 'events.jsonl', 'H02'))
            with self.assertRaises(ValueError): io_guard.write('OUTP 1')
            self.assertEqual(fake.cmds, [])
            self.assertEqual(io_guard.commands, 1)
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

    def test_h02_panel_soft_and_debug_hard_voltage_boundaries(self):
        """Require panel, public API, and explicitly armed instrument-limit probes."""
        q = self.module()
        from profile_stage import H02Scope, H02_DEBUG_TOKEN, H02_HARD_PROBES, H02_PANEL_TOKEN, h02
        from test_driver import FakeTransport

        class LimitFake(FakeTransport):
            pending_error = None
            def write(self, command):
                if command in H02_HARD_PROBES.values():
                    self.cmds.append(command)
                    self.pending_error = '120,Parameter overflowed'
                    return
                return super().write(command)
            def query(self, command):
                if command == 'SYST:ERR?' and self.pending_error:
                    self.cmds.append(command)
                    response, self.pending_error = self.pending_error, None
                    return response
                return super().query(command)

        fake = LimitFake()
        answers = iter((H02_PANEL_TOKEN, H02_DEBUG_TOKEN))
        def operator_gate(_prompt):
            token = next(answers)
            if token == H02_PANEL_TOKEN:
                for channel, limit in ((1, 2.), (2, 3.), (3, 4.)):
                    fake.values[channel]['VOLT'] = limit
            return token
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'h02_boundaries.jsonl', 'H02')
            with patch('builtins.input', side_effect=operator_gate), \
                    contextlib.redirect_stdout(io.StringIO()):
                outcome = h02(fake, evidence)

            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertEqual(outcome, 'PASS', [r for r in records if r['event'] == 'stage_error'])
            self.assert_driver_path(evidence, fake)
            self.assertTrue(fake.closed)
            self.assertEqual([fake.cmds.count(H02_HARD_PROBES[ch]) for ch in (1, 2, 3)], [1, 1, 1])
            self.assertEqual([fake.values[ch]['VOLT'] for ch in (1, 2, 3)], [1.0, 1.5, 2.0])
            self.assertTrue(all(fake.values[ch]['CHAN:OUTP'] == 0 for ch in (1, 2, 3)))
            self.assertEqual(fake.cmds.count('CHAN:OUTP 1'), 0)
            self.assertEqual(len([r for r in records if r['event'] == 'expected_instrument_rejection']), 3)
            self.assertEqual(len([r for r in records if r['event'] == 'case' and
                                  r.get('check') == 'voltage above profile cap rejected before SCPI']), 3)
            self.assertEqual(len([r for r in records if r['event'] == 'case' and
                                  r.get('check') == 'debug bypass rejected by instrument voltage limit']), 3)

        from keithley2231a import SafetyError
        with tempfile.TemporaryDirectory() as tmp:
            class WrongErrorMessage(FakeTransport):
                pending = False
                def write(self, command):
                    if command == 'VOLT 2.01':
                        self.cmds.append(command)
                        self.pending = True
                        return
                    super().write(command)
                def query(self, command):
                    if command == 'SYST:ERR?' and self.pending:
                        self.cmds.append(command)
                        self.pending = False
                        return '120,"Different error"'
                    return super().query(command)
            fake = WrongErrorMessage()
            scope = H02Scope(fake, q.Evidence(Path(tmp) / 'wrong_message.jsonl', 'H02'))
            scope.arm_hard_probes()
            with self.assertRaises(ValueError): scope.write('VOLT 3.01')
            self.assertEqual(fake.cmds, [])
            scope.expected_error = (120, 'Parameter overflowed')
            with self.assertRaises(SafetyError): scope.write('VOLT 2.01')
            self.assertEqual(fake.cmds, ['VOLT 2.01', 'SYST:ERR?'])

    def test_h04_offline_runner_stays_off_and_detects_power_only_tampering(self):
        q = self.module()
        from profile_stage import H04Scope, H04_MAXV_TOKEN, h04
        from test_driver import FakeTransport

        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeTransport()
            evidence = q.Evidence(Path(tmp) / 'h04.jsonl', 'H04')
            gate_answers = iter(('wrong token', H04_MAXV_TOKEN))
            def panel_clamps_to_limit(_prompt):
                answer = next(gate_answers)
                if answer == H04_MAXV_TOKEN:
                    for channel, value in ((1, 2.0), (2, 3.0), (3, 4.0)):
                        fake.values[channel]['VOLT'] = value
                return answer
            with patch('builtins.input', side_effect=panel_clamps_to_limit), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                outcome = h04(fake, evidence)
            self.assertEqual(outcome, 'PASS')
            self.assertIn('Max Volt Set', output.getvalue())
            self.assertIn('CH1=2.00 V, CH2=3.00 V, CH3=4.00 V', output.getvalue())
            self.assertIn('нажмите V-set, введите 2.01 и Enter', output.getvalue())
            self.assert_driver_path(evidence, fake)
            self.assertIn('4.01 V', output.getvalue())
            self.assertTrue(fake.closed)
            self.assertEqual(fake.values[3]['CURR'], .101)
            self.assertEqual(fake.values[1]['VOLT:LIM'], 4.0)
            self.assertEqual([fake.values[ch]['VOLT'] for ch in (1, 2, 3)], [1.0] * 3)
            self.assertTrue(all(value['CHAN:OUTP'] == 0 for value in fake.values.values()))
            self.assertFalse(any(command in ('OUTP 1', 'CHAN:OUTP 1', 'SYST:RWLOCK',
                                             '*RST', '*CLS') for command in fake.cmds))
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            self.assertEqual(records[-1]['event'], 'query_outcome')
            self.assertEqual(records[-1]['outcome'], 'PASS')
            self.assertIn('CURR 0.101', fake.cmds)
            self.assertIn('VOLT:LIM 5.00', fake.cmds)
            self.assertNotIn('VOLT 4.01', fake.cmds)
            self.assertIn('max voltage front-panel setpoint clamped', [
                record.get('check') for record in records])
            maxv_cases = [record for record in records
                          if record.get('check') == 'max voltage boundary rejected by API']
            self.assertEqual([record['channel'] for record in maxv_cases], [1, 2, 3])
            self.assertEqual([record['configured_max_v'] for record in maxv_cases],
                             [2.0, 3.0, 4.0])
            self.assertTrue(all(record['scpi_sent'] is False for record in maxv_cases))
            current_cases = [record for record in records
                             if record.get('check') == 'profile current boundary rejected by API']
            self.assertEqual([record['channel'] for record in current_cases], [1, 2, 3])
            self.assertEqual([record['configured_max_a'] for record in current_cases],
                             [.2, .3, .4])
            self.assertTrue(all(record['scpi_sent'] is False for record in current_cases))

        with tempfile.TemporaryDirectory() as tmp:
            io_guard = H04Scope(FakeTransport(), q.Evidence(
                Path(tmp) / 'pause.jsonl', 'H04'))
            io_guard.started = 0.0
            with patch('profile_stage.time.monotonic', side_effect=(10.0, 310.0)), \
                    patch('builtins.input', return_value='operator response'):
                io_guard.operator_input('manual gate: ')
            self.assertEqual(310.0 - io_guard.started, 10.0)

            io_guard.cleanup = True
            fake = io_guard.transport
            with patch('profile_stage.time.monotonic', return_value=500.0):
                io_guard.write('SYST:REM')
                io_guard.write('OUTP 0')
            self.assertIn('OUTP 0', fake.cmds)

        with tempfile.TemporaryDirectory() as tmp:
            io_guard = H04Scope(FakeTransport(), q.Evidence(
                Path(tmp) / 'guard.jsonl', 'H04'))
            for command in ('OUTP 1', 'CHAN:OUTP 1', 'VOLT 30.00',
                            'SYST:RWLOCK', 'STAT:OPER:EVENT?'):
                before = io_guard.transport.cmds[:]
                with self.subTest(command=command), self.assertRaises(ValueError):
                    (io_guard.query(command) if command.endswith('?') else io_guard.write(command))
                self.assertEqual(io_guard.transport.cmds, before)

    def test_h04_preflight_is_manual_gated_and_dry(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H04']), 0)
        card = output.getvalue()
        self.assertIn('CONFIRM H04 INDEPENDENT ALL_OFF', card)
        self.assertIn('CURR 0.101', card)
        self.assertIn('VOLT:LIM 5.00', card)
        self.assertIn('Max Volt Set', card)
        self.assertIn('H04 MAXV', card)
        self.assertIn('V-cap=2.00 V', card)
        self.assertIn('CH2=2.00/0.200/3.00/0.300', card)
        self.assertIn('CH3=3.00/0.300/4.00/0.400', card)
        self.assertIn('выход', card.lower())

    def test_h02_preflight_lists_all_three_voltage_protection_layers(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H02']), 0)
        card = output.getvalue()
        self.assertIn('CONFIRM H02 PANEL MAXV', card)
        self.assertIn('CONFIRM H02 DEBUG_BYPASS', card)
        self.assertIn('120,"Parameter overflowed"', card)
        self.assertIn('CH1=V1.00_I0.100_VCAP2.00_ICAP0.300', card)
        self.assertIn('CH2=V1.50_I0.200_VCAP3.00_ICAP0.400', card)
        self.assertIn('CH3=V2.00_I0.300_VCAP4.00_ICAP0.500', card)
        self.assertIn('CH1=2.01 CLAMPED=2.00', card)
        self.assertIn('CH2=3.01 CLAMPED=3.00', card)
        self.assertIn('CH3=4.01 CLAMPED=4.00', card)
        self.assertIn('VOLT 2.01', card)
        self.assertIn('не является независимой', card)

    def test_h04p_preflight_is_dry_and_explains_load_moves(self):
        q = self.module()
        output = io.StringIO()
        with patch.object(q, 'open_transport', side_effect=AssertionError('port opened')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(q.main(['preflight', 'H04P']), 0)
        card = output.getvalue()
        self.assertIn('CONFIRM H04P', card)
        self.assertIn('KEL103', card)
        self.assertIn('2.20', card)
        self.assertIn('3.30', card)
        self.assertIn('4.40', card)
        self.assertIn('CR 22', card)
        self.assertIn('CR 22/11/5', card)
        self.assertIn('Max Volt Set', card)
        self.assertIn('H04P MAXV', card)
        self.assertIn('DRY RUN', card)

    def test_h04p_offline_runner_auto_measures_and_pauses_only_for_load_moves(self):
        q = self.module()
        from powered_profile_stage import H04P_MAXV_TOKEN, H04PoweredScope, h04_powered
        from test_driver import FakeTransport

        class LoadFake:
            def __init__(self):
                self.input = 'OFF'
                self.resistance = None
                self.live = True
                self.closed = False
            def identify(self): return 'KORAD-KEL103 V3.30 SN:[REDACTED]'
            def input_state(self): return self.input
            def configure_cr(self, resistance):
                if self.input != 'OFF': raise RuntimeError('load input active')
                self.resistance = resistance
                return resistance
            def set_input(self, enabled): self.input = 'ON' if enabled else 'OFF'
            def measurements(self):
                voltage, current, mode = self.transport.metrics(self.transport.active_ch)
                return {'kel103_voltage_v': voltage, 'kel103_current_a': current,
                        'kel103_power_w': voltage * current}
            def close(self): self.closed = True

        class PoweredFake(FakeTransport):
            def __init__(self, load):
                super().__init__()
                self.load = load
                self.load.transport = self
                self.modes = {}
                self.pending_error = None
                self.status_warmup = {channel: 0 for channel in (1, 2, 3)}
            def metrics(self, channel):
                values = self.values[channel]
                if values['CHAN:OUTP'] == 0 or self.load.input != 'ON':
                    return 0.0, 0.0, 'OFF'
                target_v, limit_a = values['VOLT'], values['CURR']
                demand_a = target_v / self.load.resistance
                if demand_a >= limit_a:
                    return limit_a * self.load.resistance, limit_a, 'CC'
                return target_v, demand_a, 'CV'
            def write(self, command):
                if command == 'CHAN:OUTP 1':
                    self.status_warmup[self.active_ch] = 0
                if command == 'VOLT 4.51':
                    self.cmds.append(command)
                    if (self.values[self.active_ch]['VOLT:LIM:STAT'] != 1 or
                            self.values[self.active_ch]['CHAN:OUTP'] != 0):
                        raise AssertionError('unsafe remote max-voltage probe')
                    self.pending_error = '120,Parameter overflowed'
                    return
                return super().write(command)
            def query(self, command):
                if command == 'SYST:ERR?' and self.pending_error:
                    self.cmds.append(command)
                    response = self.pending_error
                    self.pending_error = None
                    return response
                key = command.upper().removesuffix('?')
                if key in ('MEAS:VOLT', 'MEAS:CURR', 'MEAS:POW'):
                    self.cmds.append(command)
                    voltage, current, mode = self.metrics(self.active_ch)
                    return str({'MEAS:VOLT': voltage, 'MEAS:CURR': current,
                                'MEAS:POW': voltage * current}[key])
                if key.startswith(('STAT:OPER:INST:ISUM', 'STAT:QUES:INST:ISUM')) and key.endswith(':COND'):
                    self.cmds.append(command)
                    channel = int(key.split('ISUM')[1].split(':')[0])
                    enabled = bool(self.values[channel]['CHAN:OUTP'])
                    voltage, current, mode = self.metrics(channel)
                    self.modes[channel] = mode
                    if enabled and self.status_warmup[channel]:
                        self.status_warmup[channel] -= 1
                        return '0'
                    return str((8 if enabled else 0) |
                               (1 if enabled and mode == 'CV' else
                                2 if enabled and mode == 'CC' else 0))
                return super().query(command)

        load = LoadFake()
        fake = PoweredFake(load)
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'h04p.jsonl', 'H04P')
            gate_answers = [
                H04P_MAXV_TOKEN,
                'CONFIRM H04P CONNECT CH1 ALL_OFF LOAD=OFF',
                'CONFIRM H04P MOVE CH1->CH2 ALL_OFF LOAD=OFF',
                'CONFIRM H04P MOVE CH2->CH3 ALL_OFF LOAD=OFF',
                'CONFIRM H04P DISCONNECT CH3 ALL_OFF LOAD=OFF',
            ]
            answers = iter(gate_answers)
            def panel_clamps_to_limit(_prompt):
                answer = next(answers)
                if answer == H04P_MAXV_TOKEN:
                    for channel in (1, 2, 3):
                        fake.values[channel]['VOLT'] = 4.5
                return answer
            with patch('builtins.input', side_effect=panel_clamps_to_limit), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = h04_powered(fake, evidence, load)
            self.assertEqual(result, 'PASS', evidence.path.read_text())
            self.assert_driver_path(evidence, fake)
            self.assertTrue(fake.closed)
            self.assertTrue(load.closed)
            self.assertEqual(load.input, 'OFF')
            self.assertTrue(all(values['CHAN:OUTP'] == 0
                                for values in fake.values.values()))
            self.assertFalse(any(command in ('OUTP 1', 'SYST:RWLOCK', '*RST', '*CLS')
                                 for command in fake.cmds))
            self.assertEqual(fake.cmds.count('VOLT 4.51'), 3)
            records = [json.loads(line) for line in evidence.path.read_text().splitlines()]
            points = [record for record in records if record['event'] == 'measurement']
            self.assertEqual(len(points), 21)
            maxv_cases = [record for record in records
                          if record.get('check') == 'max voltage boundary rejected by API']
            self.assertEqual([record['channel'] for record in maxv_cases], [1, 2, 3])
            self.assertTrue(all(record['scpi_sent'] is False for record in maxv_cases))
            remote_rejections = [record for record in records
                                 if record.get('check') == 'remote SCPI max-voltage rejection']
            self.assertEqual([record['channel'] for record in remote_rejections], [1, 2, 3])
            self.assertEqual([record['actual_voltage_v'] for record in remote_rejections],
                             [3.3, 3.3, 3.3])
            self.assertTrue(all(record['error_code'] == 120 for record in remote_rejections))
            panel_gate_index = next(index for index, record in enumerate(records)
                                    if record.get('phase') == 'front_panel_max_voltage_clamp')
            remote_probe_index = next(index for index, record in enumerate(records)
                                      if record.get('check') == 'remote SCPI max-voltage rejection')
            self.assertLess(remote_probe_index, panel_gate_index)
            self.assertEqual({point['channel'] for point in points}, {1, 2, 3})
            self.assertEqual({point['mode'] for point in points}, {'CV', 'CC'})
            self.assertEqual(sum(record['event'] == 'operator_gate' for record in records), 5)
            self.assertTrue(any(record.get('phase') == 'front_panel_max_voltage_clamp'
                                for record in records))
            panel_clamps = [record for record in records
                            if record.get('check') == 'max voltage front-panel setpoint clamped']
            self.assertEqual([record['voltage_set_v'] for record in panel_clamps],
                             [4.5, 4.5, 4.5])

            timed_io = H04PoweredScope(FakeTransport(), q.Evidence(
                Path(tmp) / 'pause.jsonl', 'H04P'))
            timed_io.started = 0.0
            with patch('powered_profile_stage.time.monotonic', side_effect=(10., 910.)), \
                    patch('builtins.input', return_value='manual gate'):
                timed_io.operator_input('manual gate: ')
            self.assertEqual(910. - timed_io.started, 10.)

    def test_h01_validates_off_identity_and_error_without_output_writes(self):
        q = self.module()
        from test_driver import FakeTransport
        for bad in ('none', 'old170', 'new170', 'on', 'model', 'error', 'timeout'):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as tmp:
                fake = FakeTransport()
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
                    if bad == 'timeout' and command == 'SYST:VERS?':
                        fake.cmds.append(command)
                        raise OSError('link lost')
                    return base(command)
                fake.query = query
                evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
                with contextlib.redirect_stdout(io.StringIO()):
                    outcome = q.h01(fake, evidence)
                self.assertEqual(outcome, 'PASS' if bad == 'none' else 'ABORTED' if bad == 'timeout' else 'FAIL')
                if bad == 'none': self.assert_driver_path(evidence, fake)
                content = evidence.path.read_text()
                self.assertNotIn('SECRET', content)
                self.assertNotIn(', TEST,', content)
                self.assertTrue(fake.closed)
                self.assertFalse(any(cmd.startswith(('OUTP ', 'CHAN:OUTP ', 'VOLT ', 'CURR ')) for cmd in fake.cmds))
                if bad == 'none':
                    self.assertEqual(fake.active_ch, 1)
                    snapshots = [json.loads(line) for line in content.splitlines()
                                 if json.loads(line)['event'] == 'channel_snapshot']
                    self.assertEqual([r['channel'] for r in snapshots], [1, 2, 3])
                if bad == 'timeout':
                    self.assertEqual(fake.cmds[-1], 'SYST:VERS?')  # No follow-up query.
                    self.assertIn('io_error', content)
                    self.assertIn('OSError', content)

    def test_evidence_redacts_before_serializing(self):
        q = self.module()
        with tempfile.TemporaryDirectory() as tmp:
            evidence = q.Evidence(Path(tmp) / 'events.jsonl', 'H01')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-1.04')
            evidence.add('read', command='*IDN?', response='Keithley instruments, 2231A-30-3, PRIVATE123, 1.04-0.01')
            evidence.add('outcome', outcome='PASS')
            self.assertNotIn('PRIVATE123', evidence.path.read_text())
            self.assertIn('1.04-1.04', evidence.path.read_text())
            self.assertIn('1.04-0.01', evidence.path.read_text())
            self.assertIn('"firmware_observations": ["1.04-1.04", "1.04-0.01"]',
                          evidence.path.read_text())
            rendered = io.StringIO()
            with contextlib.redirect_stdout(rendered): q.report(evidence.path)
            self.assertNotIn('PRIVATE123', rendered.getvalue())
            self.assertIn('1.04-1.04', rendered.getvalue())

            numeric_serial = q.Evidence(Path(tmp) / 'numeric-serial.jsonl', 'H05')
            numeric_serial.add('read', command='*IDN?',
                               response='Keithley instruments, 2231A-30-3, 0, 1.04-0.01')
            numeric_serial.add('read', command='MEAS:CURR?', response='0.099')
            record = numeric_serial.path.read_text()
            self.assertNotIn('"test_id": "H[REDACTED]5"', record)
            self.assertIn('"test_id": "H05"', record)
            self.assertIn('"response": "0.099"', record)
            self.assertIn('"response": "Keithley instruments, 2231A-30-3, [REDACTED], 1.04-0.01"',
                          record)
