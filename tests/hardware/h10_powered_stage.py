"""Bounded powered Parallel qualification for H10.

@file h10_powered_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Verify bounded Parallel CV/CC and a separately gated high-current point.
@details All PSU commands use the library dispatcher and a strict stage adapter.
"""
from __future__ import annotations

import math
import re
import time

from common import (ShutdownEvidence, StageAdapter, finish_combined_powered,
                    operator_gate, require_outputs)
from bench_config import current_bench
from keithley2231a import (ChannelProfile, Keithley2231A, ParallelProfile,
                           Profile, SafetyError)

FINAL_TOKEN = 'CONFIRM H10 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF DMM=DISCONNECTED NO_JUMPERS'


def approval_token(meter_host=None, load_serial_port=None):
    from bench_config import current_bench
    bench = current_bench()
    if meter_host is None: meter_host = bench.meter.host
    if load_serial_port is None: load_serial_port = bench.load.port
    return (f'CONFIRM H10 POWERED PARALLEL ALL_OFF '
            f'JUMPERS=CH1+->CH2+_CH1-->CH2- LOAD=KEL103_USB={load_serial_port} '
            f'CR=30,5 DMM7510={meter_host}:{current_bench().meter.network_port} FRONT=DCV10 CH3=OPEN RECALL=OFF '
            'CAP=4V_0.500A POWER<=1W')


def high_current_approval_token(meter_host=None, load_serial_port=None):
    from bench_config import current_bench
    bench = current_bench()
    if meter_host is None: meter_host = bench.meter.host
    if load_serial_port is None: load_serial_port = bench.load.port
    return (f'CONFIRM H10 HIGH_CURRENT PARALLEL ALL_OFF '
            f'JUMPERS=CH1+->CH2+_CH1-->CH2-_10AWG '
            f'LOAD=KEL103_USB={load_serial_port} CR=1.00,0.50 LOAD_LEADS=10AWG '
            f'DMM7510={meter_host}:{current_bench().meter.network_port} FRONT=DCV10 CH3=OPEN RECALL=OFF '
            'V=3.00 I=4.00 ICAP=4.50 POWER<=10W CV3A_THEN_CC4A')


def preflight(port, meter_host=None, load_serial_port=None,
              load_transport='serial', high_current=False):
    from bench_config import current_bench
    bench = current_bench()
    if meter_host is None: meter_host = bench.meter.host
    if load_serial_port is None: load_serial_port = bench.load.port
    if load_transport is None: load_transport = bench.load.transport
    if load_transport != 'serial':
        print('H10 Powered requires the reviewed KEL103 USB link; no hardware accessed.')
        return
    if high_current:
        token = high_current_approval_token(meter_host, load_serial_port)
        print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config). KEL103 USB: {load_serial_port}, '
              '8N1 (скорость из bench config). DMM7510: '
              f'{meter_host}:{current_bench().meter.network_port} raw TCP, SCPI, front terminals, DCV 10 V, NPLC 1.\n'
              'Высокотоковые точки CV/CC H10, отдельный разрешённый прогон. До подключения: '
              'Keithley CH1/CH2/CH3 OFF, KEL103 INPUT OFF, Output Recall OFF; сетевой '
              'выключатель Keithley доступен. Осциллограф, общая шина и прежние перемычки сняты.\n'
              'При всех выходах OFF соединить короткими изолированными проводами AWG 10 '
              'CH1+↔CH2+ и CH1−↔CH2−. Подключить KEL103 INPUT+/- к общей паре, используя '
              'подтверждённые кабели AWG 10. DMM7510 FRONT HI/LO подключить поперёк пары, '
              'режим DCV 10 V, NPLC 1; задние клеммы не использовать. CH3 оставить открытым.\n'
              'Runner задаст 3.00 V и 4.00 A, программный cap пары 4.00 V / 4.50 A. '
              'Сначала CV: KEL103 CR 1.00 Ω, ожидается примерно 3.00 V / 3.00 A / 9 W. '
              'После раздельных подтверждённых PSU OFF и KEL INPUT OFF — CC: '
              'KEL103 CR 0.50 Ω, ожидается примерно 2.00 V / 4.00 A / 8 W. '
              'Остановочный предел измеренной мощности для ОБЕИХ точек — 10 W. '
              'Критерий 10 W проверяется по измерению, не является аппаратным ограничителем. '
              'При ошибке или неожиданном режиме запуск не продолжается. '
              'Аппаратный voltage-limit пары в наблюдавшейся конфигурации отключён: '
              '4.00 V — только программный предел. Измерения суммарные и не проверяют '
              'распределение тока между ветвями. Не менять проводку при включённом PSU.\n'
              f'Подтверждение схемы и одного прохода:\n{token}\n'
              f'Финальное подтверждение всех OFF и пустых клемм:\n{FINAL_TOKEN}')
        return
    token = approval_token(meter_host, load_serial_port)
    print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config). KEL103 USB: {load_serial_port}, '
          '8N1 (скорость из bench config). DMM7510: '
          f'{meter_host}:{current_bench().meter.network_port} raw TCP, SCPI, front terminals, DCV 10 V, NPLC 1.\n'
          'До начала: CH1/CH2/CH3 OFF; KEL103 INPUT OFF; Output Recall OFF; '
          'сетевой выключатель Keithley доступен. Осциллограф отключён, земли каналов '
          'и все прежние перемычки сняты.\n'
          'Точная схема (только при всех OFF): двумя короткими изолированными проводами '
          'с подходящим сечением соединить CH1+ с CH2+ и CH1− с CH2−. Не соединять '
          'плюс с минусом. KEL103 подключить INPUT+ к общей плюсовой точке CH1+/CH2+, '
          'INPUT− к общей минусовой точке CH1−/CH2−. DMM7510: FRONT HI к общей плюсовой '
          'точке, FRONT LO к общей минусовой; задние клеммы и токовые входы не использовать. '
          'CH3 оставить полностью без проводов и нагрузки.\n'
          'Runner применит ParallelProfile при выключенных выходах: пара 3.00 V, '
          'начальный суммарный лимит 0.100 A, software caps 4.00 V / 0.500 A; '
          'CH3=0.00 V / 0.100 A. Аппаратный voltage-limit пары после Parallel OFF '
          '(по наблюдённой H10 OFF-диагностике), поэтому 4.00 V — программный предел, '
          'не аппаратная OVP. Максимум запланированной нагрузки — 1 W.\n'
          'CV: KEL103 CR 30 Ω, 3.00 V / 0.100 A (примерно 0.30 W). Затем PSU OFF и '
          'KEL INPUT OFF. CC: CR 5 Ω, напряжение 3.00 V, суммарный ток 0.300 A '
          '(примерно 1.5 V / 0.45 W). После каждой точки runner автоматически снимет '
          'PSU, KEL103 и DMM7510 показания и остановится для проверки надписи CV/CC '
          'на дисплее Keithley. Равное распределение тока между выходными клеммами '
          'этим тестом не доказывается. Не менять провода при включённом PSU.\n'
          'После обеих точек runner выключит PSU и KEL INPUT; при необходимости '
          'остановится для отключения KEL103, DMM7510 и обеих параллельных перемычек.\n'
          f'Подтверждение точной схемы и одного прохода:\n{token}\n'
          f'Финальное подтверждение всех OFF и пустых клемм:\n{FINAL_TOKEN}')


class H10PoweredScope(StageAdapter):
    """Whitelist bounded H10 profile writes and the separately gated 4 A setpoint."""
    MAX_COMMANDS = 3000
    MAX_SECONDS = 240
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:STAT\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[13]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP 1|OUTP:(?:SER|PARALLEL|TRAC) [01]|'
        r'INST:NSEL [123]|CHAN:OUTP 0|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|0\.00|1\.00|1\.50|2\.00|3\.00)|CURR (?:0|0\.100|0\.200|0\.300|4\.000)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')
    _CLEANUP_QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|OUTP:(?:SER|PARALLEL)\?|OUTP:STAT:ALL\?|'
        r'INST:NSEL\?|CHAN:OUTP\?)$')
    _CLEANUP_WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:PARALLEL 0|CHAN:OUTP 0|INST:NSEL [123])$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if (not query and command == 'CURR 4.000' and
                not (getattr(self, 'high_current_gate', False) and
                     getattr(self, 'powered_gate', False))):
            raise SafetyError('H10 4 A setpoint requires the separate high-current gate')
        if not query and command == 'OUTP 1' and not getattr(self, 'powered_gate', False):
            raise SafetyError('H10 global output ON requires the powered setup gate')
        if not query and command in ('OUTP:SER 1', 'OUTP:TRAC 1'):
            raise SafetyError('H10 permits Parallel mode only')


def _parallel_profile():
    return ParallelProfile('H10 bounded external Parallel', 4.00, .500, 3.00, .100,
                           ChannelProfile(4.00, .500, 0.00, .100),
                           expected_regulation='auto', lock_front_panel=False,
                           verify_every_s=3600.0)


def _high_current_profile():
    return ParallelProfile('H10 separately gated 4 A Parallel point', 4.00, 4.500,
                           3.00, 4.000, ChannelProfile(4.00, .500, 0.00, .100),
                           expected_regulation='auto', lock_front_panel=False,
                           verify_every_s=3600.0)


def _safe_profile():
    return Profile('H10 safe independent exit', (
        ChannelProfile(2.00, .300, 1.00, .100),
        ChannelProfile(3.00, .400, 1.50, .200),
        ChannelProfile(4.00, .500, 2.00, .300)), lock_front_panel=False)


def _point(dev, load, meter, evidence, mode, resistance, current, *,
           voltage=3.00, max_power_w=1.00, current_tolerance_a=.03,
           expected_current_a=None, expected_voltage_v=None, regulation_mode=None):
    dev.set_channel(1, voltage=voltage, current=current)
    if abs(load.configure_cr(float(resistance)) - resistance) > .001:
        raise SafetyError('KEL103 resistance readback mismatch')
    load.set_input(True)
    dev.combined_outputs(True, confirm_all_channels_safe=True)
    time.sleep(.5)
    values = []
    for number in range(1, 5):
        source = dev.read_channel(1)
        ch3 = dev.read_channel(3)
        kel = load.measurements()
        dmm = meter.measure()
        sample_values = (source.voltage_v, source.current_a, ch3.voltage_v,
                         ch3.current_a, kel['kel103_voltage_v'],
                         kel['kel103_current_a'], kel['kel103_power_w'], dmm['voltage_v'])
        if any(not math.isfinite(value) for value in sample_values):
            raise SafetyError('Nonfinite Parallel measurement')
        if not source.enabled or source.label != 'CH1+CH2':
            raise SafetyError('Parallel combined output readback is not ON')
        if not ch3.enabled or abs(ch3.voltage_v) > .05 or abs(ch3.current_a) > .03:
            raise SafetyError(f'Unloaded CH3 is not at the safe zero setpoint: {ch3}')
        if kel['kel103_power_w'] > max_power_w:
            raise SafetyError(f'H10 load exceeded the {max_power_w:g} W measurement ceiling')
        if (abs(source.voltage_v - dmm['voltage_v']) > .10 or
                abs(kel['kel103_voltage_v'] - dmm['voltage_v']) > .10):
            raise SafetyError('PSU, KEL103 and DMM7510 voltage readings disagree')
        if abs(source.current_a - kel['kel103_current_a']) > current_tolerance_a:
            raise SafetyError('Parallel aggregate current disagrees with KEL103 beyond tolerance')
        if (expected_current_a is not None and
                not expected_current_a[0] <= kel['kel103_current_a'] <= expected_current_a[1]):
            raise SafetyError('KEL103 current is outside the approved high-current window')
        if (expected_voltage_v is not None and
                not expected_voltage_v[0] <= kel['kel103_voltage_v'] <= expected_voltage_v[1]):
            raise SafetyError('KEL103 voltage is outside the approved high-current window')
        if expected_current_a is not None:
            cr_current = kel['kel103_voltage_v'] / resistance
            if abs(kel['kel103_current_a'] - cr_current) > .10:
                raise SafetyError('KEL103 current disagrees with V/R for the CR point')
            if abs(kel['kel103_power_w'] -
                   kel['kel103_voltage_v'] * kel['kel103_current_a']) > .20:
                raise SafetyError('KEL103 power disagrees with measured V×I')
        wanted_mode = (regulation_mode or mode).upper()
        # A numeric V/I match cannot certify CV or CC if status is ambiguous.
        # The previous UNKNOWN allowance could silently promote an uncertain
        # instrument status to hardware PASS after the operator gate.
        if source.regulation != wanted_mode:
            raise SafetyError(f'Parallel regulation mismatch: expected {wanted_mode}, got {source.regulation}')
        row = {'sample': number, 'psu_voltage_v': source.voltage_v,
               'psu_current_a': source.current_a, 'psu_regulation': source.regulation,
               'kel_voltage_v': kel['kel103_voltage_v'],
               'kel_current_a': kel['kel103_current_a'], 'kel_power_w': kel['kel103_power_w'],
               'dmm_voltage_v': dmm['voltage_v'], 'ch3_voltage_v': ch3.voltage_v,
               'ch3_current_a': ch3.current_a}
        values.append(row)
        evidence.add('parallel_measurement_sample', point=mode, resistance_ohm=resistance, **row)
        print(f"{mode}: PSU {row['psu_voltage_v']:.4f} V / {row['psu_current_a']:.4f} A "
              f"({row['psu_regulation']}); KEL {row['kel_voltage_v']:.4f} V / "
              f"{row['kel_current_a']:.4f} A / {row['kel_power_w']:.4f} W; "
              f"DMM {row['dmm_voltage_v']:.4f} V")
    last = values[-1]
    previous = values[-2]
    if (abs(last['psu_voltage_v'] - previous['psu_voltage_v']) > .03 or
            abs(last['psu_current_a'] - previous['psu_current_a']) > current_tolerance_a / 10):
        raise SafetyError('Parallel readings did not stabilize across final samples')
    return last


def h10_powered(transport, evidence, load, meter):
    """Run the reviewed low-power H10 CV/CC points."""
    return _run_parallel_powered(transport, evidence, load, meter, high_current=False)


def h10_high_current(transport, evidence, load, meter):
    """Run separately gated high-current Parallel CV (~3 A) and CC (4 A)."""
    return _run_parallel_powered(transport, evidence, load, meter, high_current=True)


def _run_parallel_powered(transport, evidence, load, meter, *, high_current):
    token_builder = high_current_approval_token if high_current else approval_token
    token = token_builder(load_serial_port=getattr(load, 'port', None))
    evidence = ShutdownEvidence(evidence)
    dev = Keithley2231A(transport, debug=True,
                       debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                       debug_adapter=lambda channel: H10PoweredScope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H10 high-current Parallel qualification' if high_current else \
        'incomplete H10 powered Parallel qualification'
    try:
        dev.identity()
        baseline = dev.diagnostic_query('SYST:ERR?')
        if int(baseline.split(',', 1)[0]) != 0:
            raise SafetyError(f'Pre-existing PSU error: {baseline}')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF before H10 setup')
        meter.configure_voltage(range_v=10)
        evidence.add('powered_stage_authorization', token=token,
                     source='interactive gate before instrument ports opened')
        io.powered_gate = True
        io.high_current_gate = high_current
        profile = _high_current_profile() if high_current else _parallel_profile()
        dev.apply_profile(profile, confirmed_wiring='parallel')
        require_outputs(dev)

        if high_current:
            _point(dev, load, meter, evidence, 'CV3A', 1.00, 4.000,
                   voltage=3.00, max_power_w=10.0, current_tolerance_a=.10,
                   expected_current_a=(2.70, 3.20), expected_voltage_v=(2.70, 3.10),
                   regulation_mode='CV')
            operator_gate('CONFIRM H10 DISPLAY PSU=CV3A KEL=CR INPUT=ON',
                          'Сверьте дисплей Keithley: Parallel/CV, около 3.00 V и 3.00 A. '
                          'KEL103: CR 1.00 Ω, INPUT ON; мощность не выше 10 W. '
                          'Ожидайте отдельной команды OFF перед изменением сопротивления.',
                          io, powered_timeout_s=120)
            evidence.add('operator_display_review', point='CV3A', outcome='confirmed')
            dev.combined_outputs(False)
            require_outputs(dev)
            load.set_input(False)
            if load.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT OFF unconfirmed between H10 high-current points')

            _point(dev, load, meter, evidence, 'CC4A', .50, 4.000,
                   voltage=3.00, max_power_w=10.0, current_tolerance_a=.10,
                   expected_current_a=(3.75, 4.25), expected_voltage_v=(1.70, 2.30),
                   regulation_mode='CC')
            operator_gate('CONFIRM H10 DISPLAY PSU=CC4A KEL=CR INPUT=ON',
                          'Сверьте дисплей Keithley: Parallel/CC, около 2.00 V и 4.00 A. '
                          'KEL103: CR 0.50 Ω, INPUT ON; мощность не выше 10 W. '
                          'Автоматические показания PSU, KEL103 и DMM7510 записаны.', io, powered_timeout_s=120)
            evidence.add('operator_display_review', point='CC4A', outcome='confirmed')
        else:
            _point(dev, load, meter, evidence, 'CV', 30.0, .100)
            operator_gate('CONFIRM H10 DISPLAY PSU=CV KEL=CR INPUT=ON',
                          'Сверьте дисплей Keithley: Parallel/CV, около 3.00 V и 0.100 A. '
                          'KEL103: CR 30.0 Ω, INPUT ON. Автоматические показания PSU, '
                          'KEL103 и DMM7510 записаны.', io, powered_timeout_s=120)
            evidence.add('operator_display_review', point='CV', outcome='confirmed')
            dev.combined_outputs(False)
            require_outputs(dev)
            load.set_input(False)

            _point(dev, load, meter, evidence, 'CC', 5.0, .300)
            operator_gate('CONFIRM H10 DISPLAY PSU=CC KEL=CR INPUT=ON',
                          'Сверьте дисплей Keithley: Parallel/CC, примерно 1.50 V и 0.300 A. '
                          'KEL103: CR 5.0 Ω, INPUT ON. Автоматические показания PSU, '
                          'KEL103 и DMM7510 записаны.', io, powered_timeout_s=120)
            evidence.add('operator_display_review', point='CC', outcome='confirmed')
        dev.combined_outputs(False)
        require_outputs(dev)
        load.set_input(False)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT OFF unconfirmed before physical disconnection')
        io.powered_gate = False
        operator_gate(FINAL_TOKEN,
                      'Все выходы Keithley OFF, KEL103 INPUT OFF. Отключите KEL103 и '
                      'DMM7510 от общей пары, затем снимите обе перемычки CH1+/CH2+ '
                      'и CH1−/CH2−. Подтвердите пустые клеммы и отсутствие проводов.', io)
        evidence.add('operator_gate', token=FINAL_TOKEN, action='disconnect_parallel_wiring')
        dev.emergency_off()
        dev.apply_profile(_safe_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', ('external Parallel high-current CV 3 A and CC 4 A points passed' if high_current else
                                   'bounded external Parallel CV and CC points passed')
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or not getattr(load, 'live', True) or
                   isinstance(exc, (OSError, TimeoutError, KeyboardInterrupt,
                                    InterruptedError, EOFError)) else 'FAIL')
        reason = f'H10 powered stopped: {type(exc).__name__}: {exc}; verify all outputs OFF'
        try:
            print(f'{outcome}: {reason}')
        except OSError:
            # Console failure must never interfere with electrical shutdown.
            pass
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc),
                         psu_live=io.live, load_live=getattr(load, 'live', True))
        except BaseException:
            outcome = 'ABORTED'
            reason += '; evidence write failed'
    finally:
        def recover_independent():
            # Both rails must read OFF before the physical jumper gate.
            operator_gate(FINAL_TOKEN,
                'АВАРИЙНОЕ ЗАВЕРШЕНИЕ H10: PSU ALL OFF и KEL103 INPUT OFF подтверждены. '
                'Отключите нагрузку и DMM, снимите ОБЕ параллельные перемычки '
                'CH1+/CH2+ и CH1−/CH2−. Только затем подтвердите NO_JUMPERS; '
                'восстановление Independent не включает выходы.',
                io, powered_timeout_s=120)
            evidence.add('operator_gate', token=FINAL_TOKEN,
                         action='abnormal_disconnect_parallel_wiring')
            dev.apply_profile(_safe_profile(), confirmed_wiring='independent')
            require_outputs(dev)
            if any(dev._bool(dev.diagnostic_query(query)) for query in
                   ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')):
                raise SafetyError('Independent mode readback mismatch after H10 recovery')
            if load.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT OFF lost during H10 recovery')

        outcome = finish_combined_powered(
            dev, io, load, meter, transport, evidence, outcome=outcome,
            reason=reason, recover_independent=recover_independent,
            scope=('external Parallel; CV approx. 3 A / CR1.00 and CC 4.000 A / CR0.50; <=10 W each'
                   if high_current else
                   'external Parallel; CV 3 V / CR30 and CC 0.300 A / CR5; <=1 W'))
    return outcome
