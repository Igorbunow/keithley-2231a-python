"""Bounded powered Series qualification for H09.

@file h09_powered_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Verify external CH1+CH2 series operation in CV and CC.
@details One operator gate binds the approved wiring and powered run. Every PSU
         command uses the library dispatcher and shared command/error adapter.
"""
from __future__ import annotations

import math
import re
import time

from common import (ShutdownEvidence, StageAdapter, finish_combined_powered,
                    operator_gate, require_outputs)
from bench_config import current_bench
from keithley2231a import (ChannelProfile, Keithley2231A, Profile, SafetyError,
                           SeriesProfile)

FINAL_TOKEN = 'CONFIRM H09 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF DMM=DISCONNECTED NO_JUMPERS'
HIGH_VOLTAGE_TOKEN = 'CONFIRM H09 SERIES40 LOW_CV_CC=PASS ALL_OFF LOAD=OFF DMM=DCV100 CH3=OPEN RECALL=OFF'
MEASUREMENT_ROUNDS = 6
STABLE_PAIRS_REQUIRED = 2


def approval_token(meter_host=None, load_serial_port=None):
    from bench_config import current_bench
    bench = current_bench()
    if meter_host is None: meter_host = bench.meter.host
    if load_serial_port is None: load_serial_port = bench.load.port
    return (f'CONFIRM H09 POWERED SERIES40 ALL_OFF JUMPER=CH1-->CH2+ '
            f'LOAD=KEL103_USB={load_serial_port} CR=40,12,2000,1600 '
            f'DMM7510={meter_host}:{current_bench().meter.network_port} FRONT=DCV100 CH3=OPEN_ZERO_VOLTAGE '
            'RECALL=OFF CAP=40V_25MA_POWER=1W')


def preflight(port, args_meter_host=None, load_transport=None,
              load_serial_port=None):
    from bench_config import current_bench
    bench = current_bench()
    if args_meter_host is None: args_meter_host = bench.meter.host
    if load_serial_port is None: load_serial_port = bench.load.port
    if load_transport is None: load_transport = bench.load.transport
    if load_transport != 'serial':
        raise ValueError('H09 powered qualification currently requires the reviewed KEL103 USB link')
    print(f'Keithley {port}: USB–TTL 8N1 (скорость из bench config), оператором подтверждённый CP2102 10C4:EA60.\n'
          f'KEL103: USB {load_serial_port}, 8N1 (скорость из bench config), INPUT OFF. '
          f'DMM7510: {args_meter_host}:{current_bench().meter.network_port} raw TCP, SCPI, FRONT DCV 100 V/NPLC 1.\n'
          'Все выходы OFF; Output Recall=Off; сетевой выключатель Keithley доступен.\n'
          'Подключение выполняется только при PSU и KEL INPUT OFF: короткой изолированной '
          'перемычкой соединить Keithley CH1− с CH2+ (внешняя последовательная схема).\n'
          'KEL103: INPUT + к CH1+, INPUT − к CH2−; режим CR настроит runner. '
          'CH3 оставить полностью свободным. Не соединять отрицательные клеммы других каналов.\n'
          'DMM7510: FRONT INPUT HI (красный) к CH1+, FRONT INPUT LO (чёрный) к CH2−; '
          'токовые входы/SENSE и задние клеммы не использовать. Осциллограф, общую шину '
          'и остальные перемычки отключить.\n'
          'Runner проверит исходные OFF, настроит сначала SeriesProfile с CH1/CH2 V-limits '
          '2.00/3.00 V и total soft-cap=4.00 V, затем с CH1/CH2 V-limits 20.00/20.00 V '
          'и total soft-cap=40.00 V; максимальный ток high-voltage профиля 0.025 A. '
          'CH3=0.00 V /0.100 A. В Series прошивка отключает VOLT:LIM:STAT для '
          'совмещённого выхода: оба total cap — программные пределы библиотеки, не '
          'независимая аппаратная OVP. Не использовать debug bypass.\n'
          'CV: CH1+CH2 total 2.00 V, I-limit 0.100 A, KEL103 CR 40 Ω; ожидается около '
          '2.00 V / 0.050 A / 0.10 W. Затем все PSU OFF и KEL INPUT OFF.\n'
          'CC: total voltage 4.00 V, current 0.250 A, KEL103 CR 12 Ω; ожидается около '
          '3.00 V / 0.250 A / 0.75 W. Затем high-voltage CV: total 40.00 V, current '
          'cap 0.025 A, KEL103 CR 2000 Ω; ожидается около 40.00 V / 0.020 A / 0.80 W. '
          'High-voltage CC: total voltage setpoint/cap 40.00 V, current 0.024 A, '
          'KEL103 CR 1600 Ω; ранее измерено 33.148 V / 0.0202 A / 0.6695 W. '
          'Runner проверяет 30.0–40.5 V, согласие KEL103 current по V/R '
          'в пределах 5% (или 1 mA) и мощность не выше 1.00 W. На дисплее Keithley '
          'оператор должен отдельно подтвердить индикатор CC; напряжения недостаточно.\n'
          'Все четыре powered измерения: библиотечный read_channel(1) как CH1+CH2, KEL103 '
          'U/I/P, DMM7510 независимое суммарное напряжение; CH3 отдельно читается и '
          'должен оставаться около 0 V, без нагрузки. Для 40 V точек расхождение '
          'тока PSU/KEL103 допускается до 0.015 A по спецификации Series; напряжения '
          'PSU/KEL/DMM должны совпадать в пределах 0.10 V. Runner делает паузу для подтверждения '
          'индикации CV/CC на обоих приборах после каждой точки.\n'
          'После CC: все OFF и INPUT OFF; снять KEL103, DMM и перемычку, затем подтвердить '
          'NO_JUMPERS. Runner восстановит безопасный независимый профиль; физически '
          'не менять соединения при включённом выходе. При любой ошибке: STOP, не '
          'переставлять провода; runner попробует PSU/KEL OFF только по живой связи.\n'
          f'После низковольтных CV/CC и подтверждённого OFF runner отдельно запросит\n'
          f'{HIGH_VOLTAGE_TOKEN}\n'
          f'Точный допуск на подключение и один powered проход:\n{approval_token(args_meter_host, load_serial_port)}\n'
          f'Финальная проверка после отключения проводов: {FINAL_TOKEN}')


class H09PoweredScope(StageAdapter):
    """Whitelist the exact bounded Series profiles and four low-power points."""
    MAX_COMMANDS = 5000
    MAX_SECONDS = 300
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:STAT\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[13]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP 1|OUTP:(?:SER|PARALLEL|TRAC) [01]|'
        r'INST:NSEL [123]|CHAN:OUTP 0|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|0\.00|1\.00|1\.50|2\.00|4\.00|40\.00)|'
        r'CURR (?:0|0\.024|0\.025|0\.100|0\.200|0\.250|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00|20\.00)|VOLT:LIM:STAT 1)$')
    _CLEANUP_QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|OUTP:SER\?|OUTP:STAT:ALL\?|INST:NSEL\?|CHAN:OUTP\?)$')
    _CLEANUP_WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:SER 0|CHAN:OUTP 0|INST:NSEL [123])$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if not query:
            if command in ('OUTP:PARALLEL 1', 'OUTP:TRAC 1'):
                raise SafetyError('H09 permits Series only')
            if command in ('OUTP 1', 'OUTP:SER 1') and not getattr(self, 'powered_gate', False):
                raise SafetyError('H09 powered command requires the complete setup gate')


def _series_profile():
    return SeriesProfile('H09 external Series', (2.00, 3.00), 4.00, .300,
                         2.00, .100,
                         ChannelProfile(4.00, .500, 0.00, .100),
                         lock_front_panel=False, verify_every_s=3600.0)


def _series_40v_profile():
    return SeriesProfile('H09 external Series 40 V', (20.00, 20.00), 40.00, .025,
                         40.00, .025,
                         ChannelProfile(4.00, .500, 0.00, .100),
                         lock_front_panel=False, verify_every_s=3600.0)


def _safe_independent_profile():
    return Profile('H09 safe independent exit', (
        ChannelProfile(2.00, .300, 1.00, .100),
        ChannelProfile(3.00, .400, 1.50, .200),
        ChannelProfile(4.00, .500, 2.00, .300)), lock_front_panel=False)


def _point(dev, load, meter, evidence, mode, resistance, voltage, current):
    dev.set_channel(1, voltage=voltage, current=current)
    actual_r = load.configure_cr(float(resistance))
    if abs(actual_r - resistance) > .001:
        raise SafetyError('KEL103 resistance readback mismatch')
    load.set_input(True)
    dev.combined_outputs(True, confirm_all_channels_safe=True)
    time.sleep(.5)
    # The manufacturer specifies substantially poorer absolute current accuracy
    # for the CH1+CH2 Series combination; independently check the load's V/R.
    tolerance = .015 if resistance >= 1000 else .03
    stable_pairs = 0
    previous = None
    accepted = None
    for sample_number in range(1, MEASUREMENT_ROUNDS + 1):
        source = dev.read_channel(1)
        ch3 = dev.read_channel(3)
        kel = load.measurements()
        dmm = meter.measure()
        values = (source.voltage_v, source.current_a, ch3.voltage_v, ch3.current_a,
                  kel['kel103_voltage_v'], kel['kel103_current_a'],
                  kel['kel103_power_w'], dmm['voltage_v'])
        if any(not math.isfinite(value) for value in values):
            raise SafetyError('Nonfinite Series measurement')
        if not source.enabled or source.label != 'CH1+CH2':
            raise SafetyError('Series combined output readback is not ON')
        if source.regulation not in (mode, 'UNKNOWN'):
            raise SafetyError(f'Series regulation mismatch: expected {mode}, got {source.regulation}')
        if not ch3.enabled or abs(ch3.voltage_v) > .05 or abs(ch3.current_a) > .03:
            raise SafetyError(f'Unloaded CH3 is not at the safe zero setpoint: {ch3}')
        if kel['kel103_power_w'] > 1.00:
            raise SafetyError('H09 load power exceeded the 1 W measurement ceiling')
        if abs(kel['kel103_power_w'] - kel['kel103_voltage_v'] *
               kel['kel103_current_a']) > .05:
            raise SafetyError('KEL103 power is inconsistent with its voltage/current readings')

        cross_consistent = (
            abs(source.voltage_v - dmm['voltage_v']) <= .10 and
            abs(kel['kel103_voltage_v'] - dmm['voltage_v']) <= .10 and
            abs(source.current_a - kel['kel103_current_a']) <= tolerance)
        stable = False
        if previous is not None and cross_consistent:
            stable = (
                abs(source.voltage_v - previous['source_voltage_v']) <= .03 and
                abs(source.current_a - previous['source_current_a']) <= .001 and
                abs(kel['kel103_voltage_v'] - previous['kel_voltage_v']) <= .03 and
                abs(kel['kel103_current_a'] - previous['kel_current_a']) <= .001 and
                abs(dmm['voltage_v'] - previous['dmm_voltage_v']) <= .03)
        stable_pairs = stable_pairs + 1 if stable else 0
        sample = {'sample': sample_number, 'source_voltage_v': source.voltage_v,
                  'source_current_a': source.current_a,
                  'kel_voltage_v': kel['kel103_voltage_v'],
                  'kel_current_a': kel['kel103_current_a'],
                  'kel_power_w': kel['kel103_power_w'],
                  'dmm_voltage_v': dmm['voltage_v'],
                  'cross_consistent': cross_consistent, 'stable_vs_previous': stable,
                  'consecutive_stable_pairs': stable_pairs}
        evidence.add('series_measurement_sample', mode=mode,
                     resistance_ohm=resistance, **sample)
        if stable_pairs >= STABLE_PAIRS_REQUIRED:
            accepted = (source, ch3, kel, dmm, sample_number)
            break
        previous = sample
        if sample_number < MEASUREMENT_ROUNDS:
            time.sleep(.3)
    if accepted is None:
        raise SafetyError(f'Series source/load/meter readings did not converge within '
                          f'{MEASUREMENT_ROUNDS} samples')
    source, ch3, kel, dmm, sample_number = accepted
    if voltage < 10 and mode == 'CV' and not (1.80 <= dmm['voltage_v'] <= 2.20 and
                                               .015 <= kel['kel103_current_a'] <= .080):
        raise SafetyError('CV operating point outside the expected 2 V / CR40 window')
    if voltage < 10 and mode == 'CC' and not (2.70 <= dmm['voltage_v'] <= 3.25 and
                                               .220 <= kel['kel103_current_a'] <= .280):
        raise SafetyError('CC operating point outside the expected 3 V / 0.250 A window')
    if voltage >= 40 and mode == 'CV' and not (39.5 <= dmm['voltage_v'] <= 40.5 and
                                               .017 <= kel['kel103_current_a'] <= .023):
        raise SafetyError('40 V CV point outside expected 40 V / CR2000 window')
    if voltage >= 40 and mode == 'CC':
        load_current_from_resistance = dmm['voltage_v'] / resistance
        if not (30.0 <= dmm['voltage_v'] <= 40.5 and
                abs(kel['kel103_current_a'] - load_current_from_resistance) <=
                max(.001, .05 * load_current_from_resistance)):
            raise SafetyError('40 V setpoint CC point must exceed 30 V and agree with '
                              'the CR1600 V/R measurement')
    result = {'mode': mode, 'resistance_ohm': resistance,
              'stable_measurement_rounds': sample_number,
              'library_regulation_status': source.regulation,
              'source_reading': source.__dict__, 'ch3_reading': ch3.__dict__,
              **kel, **dmm, 'outcome': 'PASS'}
    evidence.add('series_powered_measurement', **result)
    print(f"H09 {mode}: PSU {source.voltage_v:.4f} V, {source.current_a:.4f} A; "
          f"KEL103 {kel['kel103_voltage_v']:.4f} V, {kel['kel103_current_a']:.4f} A, "
          f"{kel['kel103_power_w']:.4f} W; DMM7510 {dmm['voltage_v']:.6f} V")
    return result


def h09_powered(transport, evidence, load, meter):
    """Run four bounded externally loaded Series points, then restore independent OFF."""
    evidence = ShutdownEvidence(evidence)
    dev = Keithley2231A(transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: H09PoweredScope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H09 powered Series qualification'
    try:
        dev.identity()
        baseline = dev.diagnostic_query('SYST:ERR?')
        if int(baseline.split(',', 1)[0]) != 0:
            raise SafetyError(f'Pre-existing PSU error: {baseline}')
        require_outputs(dev)
        modes = tuple(dev._bool(dev.diagnostic_query(mode)) for mode in
                      ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'))
        if modes not in ((False, False, False), (True, False, False)):
            raise SafetyError('Powered H09 requires independent or already-selected Series mode')
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF before H09 setup')
        meter.configure_voltage(range_v=100)
        token = approval_token(load_serial_port=getattr(load, 'port', None))
        evidence.add('powered_stage_authorization', token=token,
                     source='interactive qualification gate before any instrument port opened')
        io.powered_gate = True
        profile = _series_profile()
        dev.apply_profile(profile, confirmed_wiring='series')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT changed before the CV point')
        _point(dev, load, meter, evidence, 'CV', 40.0, 2.00, .100)
        operator_gate('CONFIRM H09 DISPLAY PSU=CV KEL=CR INPUT=ON',
            'Сверьте экран Keithley: Series/CV и примерно 2.00 V. KEL103: CR 40.0 Ω, '
            'INPUT ON. Выше автоматически считаны PSU, KEL103 и DMM7510.', io)
        evidence.add('operator_display_review', point='CV', outcome='confirmed')
        dev.combined_outputs(False)
        require_outputs(dev)
        load.set_input(False)
        dev.set_channel(1, voltage=4.00, current=.250)
        _point(dev, load, meter, evidence, 'CC', 12.0, 4.00, .250)
        operator_gate('CONFIRM H09 DISPLAY PSU=CC KEL=CR INPUT=ON',
            'Сверьте экран Keithley: Series/CC и примерно 3.00 V / 0.250 A. '
            'KEL103: CR 12.0 Ω, INPUT ON. Выше автоматически считаны PSU, KEL103 и DMM7510.', io)
        evidence.add('operator_display_review', point='CC', outcome='confirmed')
        dev.combined_outputs(False)
        require_outputs(dev)
        load.set_input(False)
        dev.emergency_off()
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT OFF required before 40 V authorization')
        operator_gate(HIGH_VOLTAGE_TOKEN,
            'Низковольтные CV/CC завершены. Сверьте результаты и физическую Series-схему. '
            'Keithley ALL OFF, KEL103 INPUT OFF, DMM7510 FRONT DCV100, CH3 свободен. '
            'Следующее включение повысит суммарную уставку до 40 V; у объединённого '
            'канала нет независимой аппаратной OVP. При сомнениях остановите испытание.', io)
        evidence.add('operator_high_voltage_authorization', token=HIGH_VOLTAGE_TOKEN,
                     low_voltage_completed=True, outputs_off_verified=True,
                     load_off_verified=True)
        dev.apply_profile(_series_40v_profile(), confirmed_wiring='series')
        require_outputs(dev)
        _point(dev, load, meter, evidence, 'CV', 2000.0, 40.00, .025)
        operator_gate('CONFIRM H09 DISPLAY PSU=CV40 KEL=CR INPUT=ON',
            'Сверьте экран Keithley: Series/CV и около 40.00 V суммарно. KEL103: '
            'CR 2000 Ω, INPUT ON. Автоматические измерения PSU, KEL103 и DMM7510 записаны.', io)
        evidence.add('operator_display_review', point='CV40', outcome='confirmed')
        dev.combined_outputs(False)
        require_outputs(dev)
        load.set_input(False)
        _point(dev, load, meter, evidence, 'CC', 1600.0, 40.00, .024)
        operator_gate('CONFIRM H09 DISPLAY PSU=CC40 KEL=CR INPUT=ON',
            'Сверьте экран Keithley: Series/CC; ранее измерено около 33.15 V и '
            '0.020 A на KEL103. Обязательно проверьте индикатор CC, не только напряжение. '
            'Уставка напряжения 40 V, ток задан 0.024 A; '
            'ограничения тока. KEL103: '
            'CR 1600 Ω, INPUT ON. 40.00 V — уставка, фактическое напряжение ниже '
            'из-за режима CC. Автоматические измерения записаны.', io)
        evidence.add('operator_display_review', point='CC40', outcome='confirmed')
        dev.combined_outputs(False)
        require_outputs(dev)
        load.set_input(False)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT OFF unconfirmed before physical disconnection')
        io.powered_gate = False
        operator_gate(FINAL_TOKEN,
            'Все PSU OFF, KEL103 INPUT OFF. Снимите KEL103 и DMM7510 с выходных клемм '
            'и удалите перемычку CH1−↔CH2+. Подтвердите, что на клеммах нет проводов.', io)
        evidence.add('operator_gate', token=FINAL_TOKEN, action='disconnect_series_wiring')
        dev.emergency_off()
        dev.apply_profile(_safe_independent_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 did not remain OFF')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'external Series CV/CC passed at 2 V, 4 V, and 40 V profiles'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or not getattr(load, 'live', True) or
                   isinstance(exc, (OSError, TimeoutError, KeyboardInterrupt,
                                    InterruptedError, EOFError)) else 'FAIL')
        reason = f'H09 powered stopped: {type(exc).__name__}: {exc}; verify all outputs OFF'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc),
                         psu_live=io.live, load_live=getattr(load, 'live', True))
        except BaseException:
            outcome = 'ABORTED'
            reason += '; evidence write failed'
    finally:
        outcome = finish_combined_powered(
            dev, io, load, meter, transport, evidence, outcome=outcome,
            reason=reason,
            scope='external CH1+CH2 Series; CV/CC points at 2 V, 4 V, and 40 V profiles')
    return outcome
