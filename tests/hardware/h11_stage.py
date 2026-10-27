"""Low-energy H11 I-V qualification using the public voltage_sweep API.

@file h11_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Verify a bounded CR-load sweep, CC stop condition, CSV and shutdown.
@details Keithley SCPI is restricted through the shared StageAdapter dispatcher.
"""
from __future__ import annotations

from bench_config import current_bench

import csv
import math
import re

from common import StageAdapter, operator_gate, require_outputs
from keithley2231a import (ChannelProfile, Keithley2231A, Profile, SafetyError,
                           voltage_sweep)

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
          'не выше 0.064 W; нагрузка должна показать переход CV→CC около последней точки. '
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


def profile_evidence(profile):
    """Serialize profile setpoints using the public ChannelProfile attributes."""
    return [{'max_voltage_v': channel.max_voltage,
             'max_current_a': channel.max_current,
             'initial_voltage_v': channel.initial_voltage,
             'initial_current_a': channel.initial_current}
            for channel in profile.channels]


def h11(transport, evidence, load):
    """Run one CR40 staircase and always shut down through the library path."""
    dev = Keithley2231A(
        transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: H11Scope(channel, evidence))
    io = dev.debug_transport
    outcome = 'ABORTED'
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
        dev.apply_profile(profile)
        dev.verify_profile()
        evidence.add('profile', channels=profile_evidence(profile))
        operator_gate('CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF',
                      'Убедитесь, что PSU и KEL INPUT OFF. Подключите KEL103 INPUT+ '
                      'к CH1+, INPUT− к CH1−; CH2/CH3 оставить свободными. '
                      'Короткие провода; перемычек и осциллографа нет. '
                      'Runner задаст CR 40 Ω и токовый предел 0.040 A.', io)
        evidence.add('operator_gate', token='CONFIRM H11 CONNECT CH1 ALL_OFF LOAD=OFF')
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
            if not all(math.isfinite(x) and x >= 0 for x in (v, i, p)):
                raise SafetyError('Nonfinite KEL103 measurement')
            if (abs(v - source.voltage_v) > .08 or abs(i - source.current_a) > .025 or
                    abs(p - v * i) > .10 or p > .10):
                raise SafetyError('H11 source/KEL103 readings or power bound disagree')
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
        with csv_path.open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        _check(len(rows) == len(points), 'CSV row count mismatch')
        _check(rows[-1]['regulation'] == 'CC', 'CSV does not include the CC stop point')
        _check(all(abs(float(row['requested_v']) - point.requested_v) < 1e-9
                   for row, point in zip(rows, points)), 'CSV requested-voltage mismatch')
        for point in points:
            evidence.add('iv_measurement', index=point.index,
                         requested_v=point.requested_v,
                         measured_v=point.measured_v, measured_a=point.measured_a,
                         measured_w=point.measured_w, regulation=point.regulation,
                         external=point.external)
        evidence.add('csv_verified', path=str(csv_path), rows=len(rows))
        require_outputs(dev)
        load.set_input(False)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT failed to turn OFF')
        outcome = 'PASS'
    except Exception as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        print(f'H11 остановлен: {type(exc).__name__}: {exc}')
        outcome = 'ABORTED'
    finally:
        if dev.armed:
            try:
                dev.emergency_off()
            except Exception as exc:
                evidence.add('shutdown_error', exception_type=type(exc).__name__)
                outcome = 'ABORTED'
        try:
            load.set_input(False)
        except Exception as exc:
            evidence.add('load_shutdown_error', exception_type=type(exc).__name__)
            outcome = 'ABORTED'
        try:
            states = require_outputs(dev)
            evidence.add('final_output_states', outputs=states)
        except Exception as exc:
            evidence.add('final_output_check_error', exception_type=type(exc).__name__)
            outcome = 'ABORTED'
    return outcome
