"""Bounded tracking qualification with one independently connected voltage meter.

@file h08_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Exercise linked voltages, paired timers and OFF-only zero diagnostics.
@details All PSU commands use the driver dispatch and common StageAdapter.
"""
from __future__ import annotations

from dataclasses import asdict
import math
import re
import time

from common import StageAdapter, channel_snapshot, operator_gate, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, LimitsError, Profile, SafetyError

FINAL_TOKEN = 'CONFIRM H08 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF DMM=DISCONNECTED'


def approval_token(host=None):
    from bench_config import current_bench
    bench = current_bench()
    if host is None: host = bench.meter.host
    return (f'CONFIRM H08 TRACKING ALL_OFF LOAD=DISCONNECTED SCOPE=DISCONNECTED '
            f'NO_JUMPERS DMM7510={host}:{current_bench().meter.network_port} DMM_LEADS=DISCONNECTED FRONT=DCV10 NPLC=1 '
            'RATIOS=1:1,1:2 VCAP=2.00,3.00,4.00 ICAP=0.300,0.400,0.500 RECALL=OFF')


def preflight(port, host=None):
    from bench_config import current_bench
    bench = current_bench()
    if host is None: host = bench.meter.host
    """Display the complete planned wiring and command scope without any I/O."""
    print(f'Keithley {port}: проверенный CP2102 10C4:EA60, USB–TTL 8N1 (скорость из bench config).\n'
          f'DMM7510 {host}:{current_bench().meter.network_port} raw TCP, SCPI (не TSP), FRONT INPUT HI/LO, DCV 10 V, NPLC=1.\n'
          'Начало: все три выхода OFF; KEL103 INPUT OFF, силовые провода и щупы DMM отключены.\n'
          'Осциллограф отключён; общей шины, перемычек и DUT нет; Output Recall=Off.\n'
          'После CONNECT gate: красный FRONT INPUT HI DMM к CH1+, чёрный INPUT LO к CH1−.\n'
          'Разъёмы тока и SENSE не использовать. Межканальные минусы не соединять.\n'
          'После OFF и отдельного MOVE gate переставить оба щупа на CH2 (+ к HI, − к LO).\n'
          'Один DMM измеряет последовательно; одновременно два выхода не измеряются.\n'
          'CH3 всегда физически свободен: глобальный OUTP 1 включает и его.\n'
          'V-cap CH1/2/3=2.00/3.00/4.00 V, software I-cap=0.300/0.400/0.500 A.\n'
          'I-set=0.100/0.200/0.300 A; CH3=0.50 V. Для 1:1: CH1/CH2 1.00→1.20→1.40 V;\n'
          'для 1:2: CH1 1.00→1.20→1.40 V, CH2 2.00→2.40→2.80 V.\n'
          'Изменения через CH1 и CH2; новый профиль для смены отношения только при OFF.\n'
          'Одинаковые V-set CH1/CH2 только в обязательном случае 1:1.\n'
          'Максимумы точек CH1/2/3: 1.40/2.80/0.50 V, 0.100/0.200/0.300 A,\n'
          '0.140/0.560/0.150 W; без нагрузки ожидается почти нулевой ток.\n'
          'Допуск DMM↔уставка/readback: 0.06% U + 20 mV + 5 mV + 0.1 mV; OFF ≤100 mV.\n'
          'Таймеры tracking 3/5 и 5/3 s: ожидается OFF обоих CH1/2 около 3 s;\n'
          'окно host polling 2.4..4.5 s, DMM проверяет напряжение после OFF.\n'
          'При OFF: запреты нуля/NaN/превышений, individual output/timer/sequence;\n'
          'debug VOLT 0 и CURR 0 только при OFF, затем новый профиль без ON нулевой уставки.\n'
          'PSU SCPI: штатные profile/TRAC/VOLT/readbacks/OUTP/timers/REM/LOC и SYST:ERR?;\n'
          'DMM: *IDN?, SYST:ERR?, ROUT:TERM?, *RST, SENS:FUNC/VOLT, READ?; без смены языка.\n'
          'Все ответы/измерения пишутся; PSU serial и DMM serial редактируются в evidence.\n'
          'Бюджет PSU 16000 команд/600 s без операторского ожидания; DMM timeout 5 s.\n'
          'При ошибке STOP; попытка OFF по живому PSU, без reconnect. При потере связи\n'
          'выключить Keithley сетевым выключателем и вручную проверить выходы.\n'
          'Финал: independent, все OFF, таймеры OFF, local; DISCONNECT DMM и физический OFF.\n'
          'НЕ ВКЛЮЧАТЬ ВЫХОДЫ до отдельного разрешения H08. Токен:\n'
          f'{approval_token(host)}')


class H08Scope(StageAdapter):
    """Permit the bounded profile values; ON requires an operator wiring gate."""
    MAX_COMMANDS = 16000
    MAX_SECONDS = 600
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP [01]|OUTP:(?:SER|PARALLEL) 0|OUTP:TRAC [01]|'
        r'INST:NSEL [123]|CHAN:OUTP 0|OUTP:TIM:STAT [01]|OUTP:TIM:DEL [35]\.0|'
        r'VOLT (?:0|0\.50|1\.00|1\.20|1\.40|1\.50|2\.00|2\.40|2\.80)|'
        r'CURR (?:0|0\.100|0\.200|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')
    _CLEANUP_WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:TRAC 0|OUTP:TIM:STAT 0|INST:NSEL [123]|CHAN:OUTP 0)$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if command == 'OUTP 1' and self.authorized_channel not in (1, 2):
            raise SafetyError('H08 ON requires the DMM wiring confirmation')

    def _after_write(self, command):
        super()._after_write(command)
        if command == 'OUTP 1':
            self.outputs = {ch: True for ch in (1, 2, 3)}
            self.last_on_s = time.monotonic()


def tracking_profile(ratio):
    """Distinct caps/currents; equal CH1/2 voltage only for the required 1:1 case."""
    return Profile(f'H08 tracking 1:{ratio}', (
        ChannelProfile(2, .3, 1, .1), ChannelProfile(3, .4, ratio, .2),
        ChannelProfile(4, .5, .5, .3)), wiring='tracking', lock_front_panel=False)


def _arm(dev, ratio):
    if dev.armed: dev.emergency_off()
    dev.apply_profile(tracking_profile(ratio))
    dev.verify_profile()
    require_outputs(dev)


def _meter_voltage(meter):
    value = meter.measure()['voltage_v']
    if not isinstance(value, (float, int)) or not math.isfinite(value):
        raise SafetyError('Invalid independent voltage reading')
    return value


def _voltage_tolerance(voltage):
    """PSU 0.06%+20 mV, half-step 5 mV, 0.1 mV external-meter allowance.

    PSU: 077102700 section 2. DMM7510 10 V range, 2-year specification:
    22 ppm reading + 1.2 ppm range, <0.1 mV for this stage (<=2.8 V).
    Calibration/temperature conditions are not certified by this functional test.
    """
    return .0006 * abs(voltage) + .020 + .005 + .0001


def _require_meter_off(meter):
    """Allow at most 2 s for unloaded output discharge, without masking failure."""
    deadline = time.monotonic() + 2
    while True:
        voltage = _meter_voltage(meter)
        if abs(voltage) <= .10: return voltage
        if time.monotonic() >= deadline:
            raise SafetyError('DMM voltage remained above 0.10 V with outputs OFF')
        time.sleep(.1)


def _capture(dev, meter, evidence, measured_channel, ratio, label):
    require_outputs(dev, {1: True, 2: True, 3: True})
    readings = {ch: dev.read_channel(ch) for ch in (1, 2, 3)}
    voltage = _meter_voltage(meter)
    selected = readings[measured_channel]
    if (abs(voltage - selected.voltage_set_v) > _voltage_tolerance(selected.voltage_set_v)
            or abs(voltage - selected.voltage_v) > _voltage_tolerance(selected.voltage_v)
            or any(abs(r.voltage_v - r.voltage_set_v) > 2 * _voltage_tolerance(r.voltage_set_v)
                   or abs(r.current_a) > .011 for r in readings.values())
            or abs(readings[2].voltage_set_v - ratio * readings[1].voltage_set_v) > .0049):
        raise SafetyError('Tracking setpoints/PSU/DMM readings disagree')
    evidence.add('tracking_measurement', measured_channel=measured_channel,
                 ratio=f'1:{ratio}', label=label, dmm_voltage_v=voltage,
                 readings={str(ch): asdict(r) for ch, r in readings.items()}, outcome='PASS')
    return voltage


def _guard_checks(dev, evidence, ratio):
    """Exercise rejected APIs at OFF; no rejected request may send a command."""
    for label, action in (
            ('zero', lambda: dev.set_tracking_voltage(1, 0)),
            ('nan', lambda: dev.set_tracking_voltage(1, float('nan'))),
            ('wrong_channel', lambda: dev.set_tracking_voltage(3, 1)),
            ('linked_cap', lambda: dev.set_tracking_voltage(1, 1.6 if ratio == 2 else 2.01)),
            ('resolution', lambda: dev.set_tracking_voltage(2, .01 if ratio == 2 else .001)),
            ('independent_set', lambda: dev.set_channel(1, voltage=1.2)),
            ('individual_output', lambda: dev.output(1, True)),
            ('individual_timer', lambda: dev.timer(1, 3)),
            ('sequence', lambda: dev.sequence([(0, 1, True)]))):
        count = dev.debug_transport.commands
        try: action()
        except (LimitsError, SafetyError): pass
        else: raise SafetyError(f'H08 expected refusal: {label}')
        if dev.debug_transport.commands != count:
            raise SafetyError(f'H08 rejected request sent SCPI: {label}')
        evidence.add('tracking_api_refusal', case=label, wire_commands=0, outcome='PASS')


def _timer_check(dev, meter, evidence, measured_channel, pair):
    _arm(dev, 2)
    dev.tracking_timers(*pair)
    dev.combined_outputs(True, confirm_all_channels_safe=True)
    started = dev.debug_transport.last_on_s
    live_voltage = _meter_voltage(meter)
    expected = float(measured_channel)  # initial CH1=1 V, CH2=2 V for the 1:2 profile
    if abs(live_voltage - expected) > _voltage_tolerance(expected):
        raise SafetyError('DMM live voltage before timer expiry disagrees')
    while True:
        states = dev.output_states()
        elapsed = time.monotonic() - started
        if not states[3]: raise SafetyError('CH3 unexpectedly changed during tracking timer test')
        if not states[1] and not states[2]:
            if not 2.4 <= elapsed <= 4.5:
                raise SafetyError(f'Tracking timer expiry outside 3 s window: {elapsed:.3f}')
            break
        if elapsed > 4.5: raise SafetyError('Tracking timers did not use the shorter 3 s duration')
        time.sleep(.1)
    time.sleep(.3)
    off_voltage = _require_meter_off(meter)
    evidence.add('tracking_timer_expiry', measured_channel=measured_channel,
                 configured_s=pair, shorter_s=3, observed_off_s=elapsed,
                 dmm_live_voltage_v=live_voltage, dmm_off_voltage_v=off_voltage, outcome='PASS',
                 timing_basis='host polling after OUTP write; not physical edge timing')
    dev.combined_outputs(False)
    require_outputs(dev)
    dev.tracking_timers(None, None)


def h08(transport, evidence, meter):
    """Two meter positions, each with both voltage ratios and both timer orders."""
    dev = Keithley2231A(transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: H08Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H08'
    independent = {}
    try:
        dev.identity()
        baseline = dev.diagnostic_query('SYST:ERR?')
        if int(baseline.split(',', 1)[0]) != 0:
            raise SafetyError(f'Pre-existing PSU error: {baseline}')
        require_outputs(dev)
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(mode)):
                raise SafetyError('H08 starts with independent mode and no jumpers')
        evidence.add('initial_settings', channels={
            str(ch): channel_snapshot(dev, ch) for ch in (1, 2, 3)})
        meter.configure_voltage()
        for channel in (1, 2):
            require_outputs(dev)
            if channel == 1:
                token = 'CONFIRM H08 CONNECT DMM->CH1 ALL_OFF FRONT=VOLTAGE NO_JUMPERS'
                prompt = ('Все выходы OFF. Подключите FRONT INPUT HI (красный) DMM7510 '
                          'к CH1+, FRONT INPUT LO (чёрный) к CH1−. Токовый вход и '
                          'SENSE не использовать. Нагрузка/осциллограф отключены; перемычек нет.')
            else:
                token = 'CONFIRM H08 MOVE CH1->CH2 ALL_OFF DMM=VOLTAGE NO_JUMPERS'
                prompt = ('Все выходы OFF. Снимите оба щупа DMM с CH1; подключите '
                          'FRONT INPUT HI к CH2+, FRONT INPUT LO к CH2−. '
                          'CH1/CH3, нагрузка, осциллограф свободны; перемычек нет.')
            operator_gate(token, prompt, io)
            evidence.add('operator_gate', token=token, measured_channel=channel)
            io.authorized_channel = channel
            # Finite residual check before any authorized ON.
            _require_meter_off(meter)
            for ratio in (1, 2):
                _arm(dev, ratio)
                _guard_checks(dev, evidence, ratio)
                dev.combined_outputs(True, confirm_all_channels_safe=True)
                time.sleep(.5)
                independent[ratio, 'initial', channel] = _capture(
                    dev, meter, evidence, channel, ratio, 'initial')
                dev.set_tracking_voltage(1, 1.2)
                time.sleep(.3)
                independent[ratio, 'change_via_CH1', channel] = _capture(
                    dev, meter, evidence, channel, ratio, 'change_via_CH1')
                dev.set_tracking_voltage(2, 1.4 * ratio)
                time.sleep(.3)
                independent[ratio, 'change_via_CH2', channel] = _capture(
                    dev, meter, evidence, channel, ratio, 'change_via_CH2')
                dev.combined_outputs(False)
                require_outputs(dev)
                _require_meter_off(meter)
            for pair in ((3, 5), (5, 3)):
                _timer_check(dev, meter, evidence, channel, pair)
            # Zero diagnostics are deliberately OFF-only and never reused as
            # normal state. Reapply a fresh profile after every observation.
            for quantity in ('VOLT', 'CURR'):
                _arm(dev, 2)
                dev.diagnostic_write('INST:NSEL 1')
                dev.debug_bypass_profile = True
                try: dev.diagnostic_write(f'{quantity} 0')
                finally: dev.debug_bypass_profile = False
                require_outputs(dev)
                evidence.add('tracking_zero_observation', quantity=quantity,
                             measured_channel=channel, outputs='OFF',
                             channels={str(ch): channel_snapshot(dev, ch) for ch in (1, 2, 3)},
                             scope='OFF-only diagnostic; no claim of powered zero tracking')
                dev.emergency_off()
        for ratio in (1, 2):
            for label in ('initial', 'change_via_CH1', 'change_via_CH2'):
                v1, v2 = (independent[ratio, label, ch] for ch in (1, 2))
                error_v = abs(v2 - ratio * v1)
                tolerance_v = _voltage_tolerance(v2) + ratio * _voltage_tolerance(v1)
                if v1 <= 0 or error_v > tolerance_v:
                    raise SafetyError('Independent sequential DMM voltage ratio disagrees')
                evidence.add('tracking_independent_ratio', expected_ratio=ratio,
                             measured_ratio=v2 / v1, ch1_voltage_v=v1, ch2_voltage_v=v2,
                             label=label, error_v=error_v, tolerance_v=tolerance_v,
                             simultaneous=False, outcome='PASS')
        # Restore through the ordinary profile path, keeping every output OFF.
        dev.apply_profile(Profile('H08 safe independent exit', (
            ChannelProfile(2, .3, 1, .1), ChannelProfile(3, .4, 1.5, .2),
            ChannelProfile(4, .5, .5, .3)), lock_front_panel=False))
        require_outputs(dev)
        _require_meter_off(meter)
        token = 'CONFIRM H08 DISCONNECT CH2 ALL_OFF DMM=DISCONNECTED'
        operator_gate(token, 'Все выходы OFF; отсоедините оба щупа DMM7510 от CH2.', io)
        evidence.add('operator_gate', token=token, action='disconnect')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'two ratios, both meter positions, paired timers and OFF guards passed'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or isinstance(exc, (
            OSError, TimeoutError, KeyboardInterrupt, InterruptedError)) else 'FAIL')
        reason = f'H08 stopped: {type(exc).__name__}: {exc}; verify all outputs OFF'
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc), live_link=io.live)
        if io.live:
            try:
                io.cleanup = True
                dev.emergency_off()
                require_outputs(dev)
                dev.debug_bypass_profile = True
                try:
                    dev.diagnostic_write('OUTP:TRAC 0')
                    for channel in (1, 2, 3):
                        dev.diagnostic_write(f'INST:NSEL {channel}')
                        dev.diagnostic_write('OUTP:TIM:STAT 0')
                finally: dev.debug_bypass_profile = False
                dev.diagnostic_write('SYST:LOC')
            except BaseException:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT STATE'
    finally:
        for instrument in (transport, meter):
            try: instrument.close()
            except BaseException:
                outcome, reason = 'ABORTED', 'instrument close failed; manual OFF required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware,
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
    return outcome
