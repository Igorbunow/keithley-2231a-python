"""Bounded monitoring qualification using the public acquisition API.

@file h07_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Verify CSV statistics, timestamp integration, resets and a real sampling gap.
@details All PSU traffic uses the common driver dispatch; only CH1 may be enabled.
"""
from __future__ import annotations
from pathlib import Path
import sys

# Hardware qualification reuses example helpers, not driver orchestration.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from examples.acquisition import Acquisition



from bench_config import current_bench

import csv
from dataclasses import asdict
import hashlib
import math
import re
import statistics
import time

from common import (ShutdownEvidence, StageAdapter, channel_snapshot, operator_gate,
                    release_psu_and_load, require_outputs)
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError

DURATION_S = 180.0
INTERVAL_S = 2.0
MAX_GAP_S = 10.0
GAP_PAUSE_S = 12.0
CHANNELS = {1: (4.00, .300, 4.00, .150),
            2: (4.05, .400, 1.50, .200),
            3: (4.10, .500, 2.00, .300)}
FINAL_TOKEN = 'CONFIRM H07 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'


def approval_token(load_serial_port=None):
    """Authorize one three-minute pass with a physically disconnected load at entry."""
    if load_serial_port is None:
        load_serial_port = current_bench().load.port
    return (f'CONFIRM H07 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
            f'USB={load_serial_port} BAUD={current_bench().load.baudrate} CH1=V4.00_I0.150 '
            'CH2=V1.50_I0.200 CH3=V2.00_I0.300 CR=40OHM DURATION=180S RECALL=OFF')


class H07Scope(StageAdapter):
    """Bound setup and metering commands, forbidding output ON outside CH1."""
    MAX_COMMANDS = 16000
    MAX_SECONDS = 300
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|4\.00|1\.50|2\.00)|CURR (?:0|0\.150|0\.200|0\.300)|'
        r'VOLT:LIM (?:4\.00|4\.05|4\.10)|VOLT:LIM:STAT 1)$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if command == 'CHAN:OUTP 1' and (
                self.selected != 1 or self.authorized_channel != 1 or
                any(self.outputs[ch] for ch in (2, 3))):
            raise SafetyError('H07 may enable only the explicitly gated CH1')


def _check(condition, detail):
    """Keep qualification checks active when Python runs with optimization enabled."""
    if not condition:
        raise SafetyError(f'H07 verification failed: {detail}')


def verify_csv(path, expected, *, max_gap_s):
    """Recompute using csv/statistics and explicit trapezoids, independently of monitor.py.

    Absolute integration tolerances include CSV timestamp rounding to microseconds.
    These are arithmetic tolerances, not instrument accuracy specifications.
    """
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        _check(reader.fieldnames == Acquisition.HEADER, 'CSV schema differs')
        rows = list(reader)
    result = {}
    for ch in (1, 2, 3):
        samples = [r for r in rows if int(r['channel']) == ch]
        _check(len(samples) >= 30, f'CH{ch}: insufficient samples')
        numbers = {key: [float(r[key]) for r in samples]
                   for key in ('monotonic_s', 'voltage_v', 'current_a', 'power_w')}
        _check(all(math.isfinite(v) for values in numbers.values() for v in values),
               f'CH{ch}: nonfinite CSV value')
        actual = {}
        for key in ('voltage_v', 'current_a', 'power_w'):
            values = numbers[key]
            actual[key] = dict(count=len(values), min=min(values), max=max(values),
                               mean=statistics.fmean(values), stddev=statistics.pstdev(values))
            for name, value in actual[key].items():
                _check(math.isclose(value, expected[str(ch)][key][name],
                                    abs_tol=1e-10, rel_tol=1e-10), (ch, key, name))
        ah, wh, active, gaps = 0.0, 0.0, 0.0, 0
        intervals = []
        for prev, row in zip(samples, samples[1:]):
            dt = float(row['monotonic_s']) - float(prev['monotonic_s'])
            _check(dt > 0, 'CSV timestamps must increase per channel')
            intervals.append(dt)
            if dt > max_gap_s:
                gaps += 1
            elif prev['enabled'] == row['enabled'] == '1':
                active += dt
                ah += (float(prev['current_a']) + float(row['current_a'])) * dt / 7200.0
                wh += (float(prev['power_w']) + float(row['power_w'])) * dt / 7200.0
        actual.update(Ah=ah, Wh=wh, active_duration_s=active, skipped_gaps=gaps)
        for key, tolerance in (('Ah', 2e-9), ('Wh', 1e-8), ('active_duration_s', 2e-6),
                               ('skipped_gaps', 0)):
            _check(math.isclose(actual[key], expected[str(ch)][key], abs_tol=tolerance,
                                rel_tol=0), (ch, key, actual[key], expected[str(ch)][key]))
        _check(gaps == 1, f'CH{ch}: expected exactly one intentional acquisition gap')
        _check(all(r['enabled'] == ('1' if ch == 1 else '0') for r in samples),
               f'CH{ch}: unexpected output state in CSV')
        _check((ah > 0 and wh > 0) if ch == 1 else ah == wh == active == 0,
               f'CH{ch}: incorrect active energy/duration')
        actual['interval_s'] = dict(min=min(intervals), max=max(intervals),
                                    mean=statistics.fmean(intervals))
        result[str(ch)] = actual
    _check(len(rows) == sum(result[str(ch)]['voltage_v']['count'] for ch in (1, 2, 3)),
           'CSV contains an unexpected channel')
    return result


def _numeric(value, name, *, nonnegative=True):
    """Reject malformed/nonfinite telemetry before comparisons (including NaN)."""
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        raise SafetyError(f'H07 invalid {name}: expected finite numeric reading')
    return value


def _check_live_measurements(readings, values):
    """Compare U, I, P of the load and source at each monitor scan.

    Source P is computed from separately sampled U and I, not an independent
    Keithley MEAS:POW? observation. The KEL103 power is a separate readback.
    """
    _check(len(readings) == 3, 'H07 must read exactly CH1, CH2 and CH3')
    for channel, sample in enumerate(readings, start=1):
        for key in ('voltage_v', 'current_a', 'power_w'):
            _numeric(getattr(sample, key), f'CH{channel} {key}', nonnegative=channel == 1)
    source = readings[0]
    if not source.enabled or source.regulation != 'CV':
        raise SafetyError('H07 CH1 must remain ON in CV')
    for ch, sample in enumerate(readings[1:], start=2):
        if (sample.enabled or abs(sample.voltage_v) > 0.10 or
                abs(sample.current_a) > 0.01 or abs(sample.power_w) > 0.01):
            raise SafetyError(f'H07 CH{ch} must remain OFF')
    v = _numeric(values['kel103_voltage_v'], 'KEL103 voltage')
    i = _numeric(values['kel103_current_a'], 'KEL103 current')
    p = _numeric(values['kel103_power_w'], 'KEL103 power')
    # A 25 mW power tolerance is conservative against observed H04P/H06
    # differences (few mW), while rejecting the former 310 mW false PASS.
    if (abs(v - 4.00) > 0.06 or abs(i - 0.100) > 0.03 or
            abs(v - source.voltage_v) > 0.06 or
            abs(i - source.current_a) > 0.03 or
            abs(p - v * i) > 0.025 or
            abs(source.power_w - source.voltage_v * source.current_a) > 0.025 or
            abs(p - source.power_w) > 0.025):
        raise SafetyError('H07 Keithley/KEL103 U/I/P measurement mismatch')


def h07(transport, evidence, load):
    """Acquire three minutes, shut down, verify persisted data and test both resets."""
    original_evidence = evidence
    evidence = ShutdownEvidence(original_evidence)
    # The physical KEL103 adapter logs before sending USB commands, too. Its
    # logging must not be permitted to block an emergency INPUT OFF.
    if getattr(load, 'evidence', None) is original_evidence:
        load.evidence = evidence
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H07Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H07'
    try:
        dev.identity()
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('KEL103 identity/input preflight failed')
        baseline = dev.diagnostic_query('SYST:ERR?')
        code, message = baseline.split(',', 1)
        evidence.add('instrument_error_queue_baseline', response=baseline)
        if int(code.strip()) != 0 or not message.strip():
            raise SafetyError(f'pre-existing Keithley error: {baseline}')
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('H07 requires independent outputs')
        evidence.add('initial_settings', channels={str(ch): channel_snapshot(dev, ch)
                                                   for ch in CHANNELS})
        profile = Profile('H07 monitoring qualification', tuple(
            ChannelProfile(*CHANNELS[ch]) for ch in (1, 2, 3)),
            wiring='independent', lock_front_panel=False, verify_every_s=5.0)
        dev.apply_profile(profile, confirmed_wiring='independent')
        dev.verify_profile()
        evidence.add('profile', **asdict(profile))
        token = 'CONFIRM H07 CONNECT CH1 ALL_OFF LOAD=OFF'
        operator_gate(token, 'Все выходы Keithley и INPUT KEL103 OFF. Подключите '
                      'CH1+ к KEL103+, CH1− к KEL103− короткими проводами. '
                      'CH2/CH3 свободны; осциллограф отключён, перемычек нет. '
                      'Runner задаст CR 40 Ω; CH1 4.00 V / 0.150 A, около '
                      '0.100 A / 0.400 W; CH2/CH3 останутся OFF.', io)
        evidence.add('operator_gate', token=token)
        load.configure_cr(40.0)
        load.set_input(True)
        io.authorized_channel = 1
        dev.output(1, True)
        require_outputs(dev, {1: True, 2: False, 3: False})
        csv_path = evidence.path.parent / 'telemetry.csv'
        acquisition = Acquisition(dev, interval_s=INTERVAL_S, max_gap_s=MAX_GAP_S,
                                  csv_path=csv_path)
        scans = 0

        def observe(readings):
            nonlocal scans
            scans += 1
            values = load.measurements()
            _check_live_measurements(readings, values)
            evidence.add('monitor_sample', scan=scans,
                         readings=[asdict(r) for r in readings], **values)
            if scans == 10:
                evidence.add('intentional_sampling_gap', pause_s=GAP_PAUSE_S,
                             max_gap_s=MAX_GAP_S, link='connected; no I/O fault injected')
                time.sleep(GAP_PAUSE_S)

        acquisition.run(duration_s=DURATION_S, on_sample=observe)
        # Both links must reach independently confirmed OFF before file/stats work.
        dev.output(1, False)
        require_outputs(dev)
        load.set_input(False)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT OFF readback mismatch')
        dev.verify_profile()
        expected = {str(ch): stats.as_dict() for ch, stats in acquisition.stats.items()}
        recomputed = verify_csv(csv_path, expected, max_gap_s=MAX_GAP_S)
        evidence.add('csv_verification', channels=recomputed, library_summary=expected,
                     csv_file=csv_path.name, csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(),
                     max_gap_s=MAX_GAP_S, requested_interval_s=INTERVAL_S,
                     requested_duration_s=DURATION_S,
                     tolerance=dict(statistics=1e-10, Ah=2e-9, Wh=1e-8, duration_s=2e-6))
        acquisition.export_summary(evidence.path.parent / 'summary.json')
        acquisition.reset_statistics()
        for ch, stats in acquisition.stats.items():
            _check(stats.voltage.count == stats.current.count == stats.power.count == 0,
                   f'CH{ch}: statistics reset')
            _check(stats.ampere_hours == expected[str(ch)]['Ah'] and
                   stats.watt_hours == expected[str(ch)]['Wh'] and
                   stats.duration_s == expected[str(ch)]['active_duration_s'] and
                   stats.skipped_gaps == expected[str(ch)]['skipped_gaps'],
                   f'CH{ch}: energy changed during statistics-only reset')
        acquisition.reset_statistics(reset_energy=True)
        for stats in acquisition.stats.values():
            _check(stats.ampere_hours == stats.watt_hours == stats.duration_s == 0 and
                   stats.skipped_gaps == 0 and stats._previous is None, 'energy reset')
        evidence.add('statistics_reset', statistics_only='PASS', reset_energy='PASS')
        token = 'CONFIRM H07 DISCONNECT CH1 ALL_OFF LOAD=OFF'
        operator_gate(token, 'Все выходы и INPUT нагрузки выключены по readback. '
                      'Проверьте отсутствие напряжения и отсоедините нагрузку от CH1.', io)
        evidence.add('operator_gate', token=token)
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('final KEL103 INPUT is not OFF')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'CSV, statistics, timestamp integration, gap and resets verified'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or not getattr(load, 'live', True) or
                   isinstance(exc, (TimeoutError, KeyboardInterrupt, InterruptedError))
                   else 'FAIL')
        reason = f'H07 stopped: {type(exc).__name__}: {exc}; verify physical OFF'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__,
                         message=str(exc), live_link=io.live)
        except BaseException:
            # A full disk or a broken evidence sink must never prevent OFF.
            outcome = 'ABORTED'
            reason += '; evidence write failed'
        evidence.cleanup = True
        failures = release_psu_and_load(dev, io, load, evidence)
        if failures:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
        if io.live and not failures:
            try:
                dev.diagnostic_write('SYST:LOC')
            except BaseException:
                outcome, reason = 'ABORTED', 'SYST:LOC unconfirmed; manual OFF required'
    finally:
        for device in (transport, load):
            try:
                device.close()
            except BaseException:
                outcome, reason = 'ABORTED', 'transport close failed; manual OFF required'
        if evidence.lost:
            outcome = 'ABORTED'
            reason += '; evidence sink failed; manual review required'
        try:
            evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                         verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
        except BaseException:
            outcome = 'ABORTED'  # Persisted evidence could not confirm a PASS.
    return outcome
