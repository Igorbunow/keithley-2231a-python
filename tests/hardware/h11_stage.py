"""Low-energy H11 I-V qualification using the public voltage_sweep API.

@file h11_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Verify a bounded CR-load sweep, CC stop condition, CSV and shutdown.
@details Keithley SCPI is restricted through the shared StageAdapter dispatcher.
"""
from __future__ import annotations
from pathlib import Path
import sys

# Hardware qualification reuses example helpers, not driver orchestration.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from examples.iv_sweep import voltage_sweep



from bench_config import current_bench

import csv
import math
import re

from common import (ShutdownEvidence, StageAdapter, operator_gate,
                    release_psu_and_load, require_outputs)
from keithley2231a import (ChannelProfile, Keithley2231A, Profile, SafetyError)

VOLTAGES = (0.50, 1.00, 1.50, 2.00)
CURRENT_LIMIT_A = 0.040
FINAL_TOKEN = 'CONFIRM H11 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF DISCONNECTED'


def approval_token(load_serial_port=None):
    from bench_config import current_bench
    bench = current_bench()
    if load_serial_port is None: load_serial_port = bench.load.port
    return (f'CONFIRM H11 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
            f'USB={load_serial_port} BAUD={current_bench().load.baudrate} RECALL=OFF '
            'CH1=V0.50_I0.040_VCAP2.00_ICAP0.100 '
            'CH2=V1.50_I0.200_VCAP3.00_ICAP0.200 '
            'CH3=V2.00_I0.300_VCAP4.00_ICAP0.300 CR=40OHM '
            'SWEEP=0.50,1.00,1.50,2.00 I_LIMIT=0.040A STOP=CC')


def preflight(port, load_serial_port=None, load_transport=None):
    from bench_config import current_bench
    bench = current_bench()
    if load_serial_port is None: load_serial_port = bench.load.port
    if load_transport is None: load_transport = bench.load.transport
    if load_transport != 'serial':
        raise ValueError('H11 requires the reviewed KEL103 USB connection')
    print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config). KEL103: USB {load_serial_port}, '
          '8N1 (скорость из bench config); INPUT OFF. Осциллограф, DMM, перемычки и общая шина отключены.\n'
          'Перед началом все выходы Keithley OFF; Output Recall=Off; сетевой выключатель доступен. '
          'Нагрузка должна быть физически отсоединена. Runner сначала проверит состояние '
          'и задаст уникальный профиль: CH1 0.50 V/0.040 A, cap 2.00 V/0.100 A; '
          'CH2 1.50 V/0.200 A, cap 3.00 V/0.200 A; CH3 2.00 V/0.300 A, '
          'cap 4.00 V/0.300 A. Только CH1 будет включён.\n'
          'Отдельная пауза на подключение: при Keithley OFF и KEL INPUT OFF соединить '
          'CH1+ с KEL103 INPUT+, CH1− с INPUT− короткими проводами. CH2/CH3 оставить '
          'свободными. Runner настроит CR 40 Ω. Пройдёт лестница 0.50, 1.00, 1.50, '
          '2.00 V с лимитом тока 0.040 A и остановкой при CC. Ожидаемая мощность '
          'порядка 0.06–0.07 W, с проверяемым пределом KEL103 0.10 W; '
          'нагрузка должна показать переход CV→CC около последней точки. '
          'Будут сверены монотонность I–V, показания KEL103, CSV и очередь ошибок.\n'
          'После автоматического OFF отключить KEL103 от CH1, оставить все выходы OFF и '
          'подтвердить финальную проверку. При ошибке не переставлять провода и не '
          'повторять запуск до разбора отчёта.\n'
          f'Токен допуска к одному прогону:\n{approval_token(load_serial_port)}\n'
          f'Финальный токен после отключения проводов:\n{FINAL_TOKEN}')


class H11Scope(StageAdapter):
    """Allow only the CH1 sweep setup, profile verification and shutdown path."""
    MAX_COMMANDS = 6000
    MAX_SECONDS = 300
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|VOLT\?|CURR\?|'
        r'VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|0\.00|0\.50|1\.00|1\.50|2\.00)|'
        r'CURR (?:0|0\.040|0\.200|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if not query and command == 'CHAN:OUTP 1' and (
                self.selected != 1 or self.authorized_channel != 1 or
                any(self.outputs[ch] for ch in (2, 3))):
            raise SafetyError('H11 may enable only the explicitly gated CH1')


def _check(condition, detail):
    if not condition:
        raise SafetyError(f'H11 verification failed: {detail}')


def verify_h11_load_sample(source, voltage_v, current_a, power_w):
    """Require CR40 physics and cross-instrument agreement, not mere finite data.

    The old 25 mA current and 100 mW power tolerances could accept grossly
    incorrect readings on this 40 mA/70 mW sweep. These bounded tolerances
    allow the actual 2026-10-29 hardware readings, including the observed
    ~1.6 mA source/load offset at the 0.5 V point.
    """
    values = (source.voltage_v, source.current_a, source.power_w,
              voltage_v, current_a, power_w)
    _check(all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and v >= 0 for v in values),
           'invalid source or KEL103 numeric measurement')
    _check(abs(source.power_w - source.voltage_v * source.current_a) <= .0005,
           'Keithley U/I/P inconsistent')
    _check(abs(voltage_v - source.voltage_v) <= .025,
           'Keithley/KEL103 voltage disagreement')
    _check(abs(current_a - source.current_a) <=
           max(.003, .08 * max(current_a, source.current_a)),
           'Keithley/KEL103 current disagreement')
    _check(abs(current_a - voltage_v / 40.) <=
           max(.002, .08 * (voltage_v / 40.)),
           'KEL103 current inconsistent with CR 40 ohm')
    _check(abs(power_w - voltage_v * current_a) <=
           max(.0005, .025 * voltage_v * current_a),
           'KEL103 reported power inconsistent with U*I')
    _check(abs(power_w - source.power_w) <=
           max(.003, .10 * max(power_w, source.power_w)),
           'Keithley/KEL103 cross-power disagreement')
    _check(power_w <= .10, 'H11 load power exceeds 0.10 W')


def verify_h11_csv(csv_path, points):
    """Reparse every CSV field and require equality with the measured points.

    A four-row CSV with only correct requested voltages and final CC used to
    count as verified even if it contained altered/missing measured values.
    """
    numeric_columns = ('index', 'requested_v', 'monotonic_s', 'measured_v',
                       'measured_a', 'measured_w', 'kel_current_a',
                       'kel_power_w', 'kel_voltage_v')
    fields = ('index', 'requested_v', 'timestamp_utc', 'monotonic_s',
              'measured_v', 'measured_a', 'measured_w', 'regulation',
              'kel_current_a', 'kel_power_w', 'kel_voltage_v')
    with csv_path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        _check(tuple(reader.fieldnames or ()) == fields, 'CSV columns mismatch')
        rows = list(reader)
    _check(len(rows) == len(points), 'CSV row count mismatch')
    for point, row in zip(points, rows):
        _check(None not in row and None not in row.values(), 'CSV missing/extra fields')
        _check(row['timestamp_utc'] == point.timestamp_utc and
               row['regulation'] == point.regulation,
               f'CSV timestamp or regulation mismatch at {point.index}')
        expected = {'index': point.index, 'requested_v': point.requested_v,
                    'monotonic_s': point.monotonic_s,
                    'measured_v': point.measured_v, 'measured_a': point.measured_a,
                    'measured_w': point.measured_w, **point.external}
        for col in numeric_columns:
            try:
                actual = float(row[col])
                target = float(expected[col])
            except (ValueError, TypeError, KeyError) as exc:
                raise SafetyError(f'CSV nonnumeric or absent {col} at {point.index}') from exc
            _check(math.isfinite(actual) and
                   math.isclose(actual, target, rel_tol=1e-12, abs_tol=1e-12),
                   f'CSV {col} disagrees with measurement at {point.index}')
    return len(rows)


def profile_evidence(profile):
    """Serialize profile setpoints using the public ChannelProfile attributes."""
    return [{'max_voltage_v': channel.max_voltage,
             'max_current_a': channel.max_current,
             'initial_voltage_v': channel.initial_voltage,
             'initial_current_a': channel.initial_current}
            for channel in profile.channels]


def h11(transport, evidence, load):
    """Run one CR40 staircase and always shut down through the library path."""
    original_evidence = evidence
    evidence = ShutdownEvidence(original_evidence)
    # The real KEL103 adapter writes evidence before sending its USB command.
    # Those writes must not block emergency INPUT OFF.
    if getattr(load, 'evidence', None) is original_evidence:
        load.evidence = evidence
    dev = Keithley2231A(
        transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: H11Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H11 qualification'
    try:
        identity = dev.identity()
        if '2231A-30-3' not in identity or 'KEL103' not in load.identify().upper():
            raise SafetyError('H11 instrument identity mismatch')
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF at H11 entry')
        baseline = dev.diagnostic_query('SYST:ERR?')
        evidence.add('instrument_error_queue_baseline', response=baseline)
        if not baseline.lstrip().startswith('0,'):
            raise SafetyError(f'Pre-existing Keithley error: {baseline}')
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('H11 requires independent topology')

        profile = Profile('H11 low-energy CR sweep', (
            ChannelProfile(2.00, .100, .50, .040),
            ChannelProfile(3.00, .200, 1.50, .200),
            ChannelProfile(4.00, .300, 2.00, .300)),
            wiring='independent', lock_front_panel=False, verify_every_s=5.0)
        dev.apply_profile(profile, confirmed_wiring='independent')
        dev.verify_profile()
        evidence.add('profile', channels=profile_evidence(profile))
        operator_gate('CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF',
                      'Убедитесь, что PSU и KEL INPUT OFF. Подключите KEL103 INPUT+ '
                      'к CH1+, INPUT− к CH1−; CH2/CH3 оставить свободными. '
                      'Короткие провода; перемычек и осциллографа нет. '
                      'Runner задаст CR 40 Ω и токовый предел 0.040 A.', io)
        evidence.add('operator_gate', token='CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must remain OFF until configured')
        if abs(load.configure_cr(40.0) - 40.0) > .01:
            raise SafetyError('KEL103 CR 40 Ω readback mismatch')
        load.set_input(True)
        io.authorized_channel = 1
        csv_path = evidence.path.parent / 'iv_sweep.csv'

        def observe(source):
            kel = load.measurements()
            v, i, p = (kel[key] for key in ('kel103_voltage_v',
                                             'kel103_current_a',
                                             'kel103_power_w'))
            verify_h11_load_sample(source, v, i, p)
            return {'kel_voltage_v': v, 'kel_current_a': i, 'kel_power_w': p}

        points = voltage_sweep(dev, 1, VOLTAGES, current_limit_a=CURRENT_LIMIT_A,
                               settle_s=.5, observer=observe, stop_on_cc=True,
                               csv_path=csv_path)
        _check(len(points) == len(VOLTAGES), f'expected {len(VOLTAGES)} sweep points')
        _check(all(point.regulation == 'CV' for point in points[:-1]),
               'pre-limit sweep points must remain in CV')
        _check(points[-1].regulation == 'CC', 'sweep did not stop on the CC condition')
        _check(all(point.measured_a <= CURRENT_LIMIT_A + .003 for point in points),
               'measured current exceeded the 40 mA soft limit')
        _check(all(point.measured_w <= .10 for point in points),
               'measured source power exceeded 0.10 W')
        _check(all(a.measured_a <= b.measured_a + .005
                   for a, b in zip(points, points[1:])), 'I-V current is not monotonic')
        _check(all(a.measured_v < b.measured_v
                   for a, b in zip(points[:-1], points[1:])),
               'measured voltage must increase through the CC transition')
        csv_rows = verify_h11_csv(csv_path, points)
        for point in points:
            evidence.add('iv_measurement', index=point.index,
                         requested_v=point.requested_v,
                         sample_timestamp_utc=point.timestamp_utc,
                         sample_monotonic_s=point.monotonic_s,
                         measured_v=point.measured_v, measured_a=point.measured_a,
                         measured_w=point.measured_w, regulation=point.regulation,
                         external=point.external)
        evidence.add('csv_verified', path=str(csv_path), rows=csv_rows,
                     checked_fields=11)
        require_outputs(dev)
        load.set_input(False)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT failed to turn OFF')
        outcome, reason = 'PASS', 'four CV/CC points, KEL comparison and CSV verified'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or not getattr(load, 'live', True) or
                   isinstance(exc, (OSError, TimeoutError, KeyboardInterrupt,
                                    InterruptedError, EOFError)) else 'FAIL')
        reason = f'H11 stopped: {type(exc).__name__}: {exc}; verify physical OFF'
        print(reason)
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__,
                         detail=str(exc), psu_live=io.live,
                         load_live=getattr(load, 'live', True))
        except BaseException:
            outcome = 'ABORTED'
            reason += '; evidence write failed'
    finally:
        # Emergency SCPI and INPUT OFF must work even when evidence is unavailable.
        # A previous library fault can disarm the driver while physical ON is
        # still possible, so never predicate this step on dev.armed.
        evidence.cleanup = True
        failures = release_psu_and_load(dev, io, load, evidence)
        if failures:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
        # qualification.py owns the final physical DISCONNECT token after
        # the stage returns. Do not request the same operator confirmation twice.
        # A PASS here means electrically confirmed OFF, not yet physical removal.
        if io.live and not failures:
            try:
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                outcome = 'ABORTED'
                reason += f'; SYST:LOC unconfirmed: {type(exc).__name__}'
        for instrument in (transport, load):
            try:
                instrument.close()
            except BaseException as exc:
                outcome = 'ABORTED'
                reason += f'; instrument close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome = 'ABORTED'
            reason += '; evidence incomplete; manual review required'
        # Recorded outcome is not authoritative if its own evidence write fails.
        try:
            evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                         verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
        except BaseException:
            outcome = 'ABORTED'
        if evidence.lost:
            outcome = 'ABORTED'
    return outcome
