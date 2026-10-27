"""OFF-only H12 negative checks and final qualification cleanup.

@file h12_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Exercise rejected arguments and verify final safe settings without ON.
@details I/O loss and mismatched replies are injected offline, never on hardware.
"""
from __future__ import annotations

import re

from common import StageAdapter, channel_snapshot, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, Profile, ProfileError

TOKEN = ('CONFIRM H12 OFF_DIAGNOSTIC INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
         'DMM=DISCONNECTED SCOPE=DISCONNECTED NO_JUMPERS RECALL=OFF')
FINAL_TOKEN = ('CONFIRM H12 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF '
               'DISCONNECTED NO_JUMPERS')


def safe_profile():
    """Use distinct channel settings to expose channel-selection mistakes."""
    return Profile('H12 final safe profile', (
        ChannelProfile(2.00, .300, 1.00, .100),
        ChannelProfile(3.00, .400, 1.50, .200),
        ChannelProfile(4.00, .500, 2.00, .300)), lock_front_panel=False)


def rejected_calls(dev):
    """Yield bounded negative API cases; none may reach the wire."""
    for channel in (0, 4, True):
        yield f'invalid channel {channel!r}', lambda ch=channel: dev.set_channel(ch, voltage=1.0)
    for field in ('voltage', 'current'):
        for value in (float('nan'), float('inf'), -1.0):
            yield f'invalid {field} {value}', lambda f=field, v=value: dev.set_channel(1, **{f: v})
    yield 'voltage above cap', lambda: dev.set_channel(1, voltage=2.01)
    yield 'current above cap', lambda: dev.set_channel(1, current=.301)
    yield 'invalid profile type', lambda: dev.apply_profile(None)


class H12Scope(StageAdapter):
    """Restrict the physical phase to known OFF-only library sequences."""
    MAX_COMMANDS = 3000
    MAX_SECONDS = 180
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|VOLT\?|CURR\?|'
        r'VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP 0|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|0\.00|1\.00|1\.50|2\.00)|CURR (?:0|0\.100|0\.200|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')


def preflight(port):
    """Describe the physical phase without opening any instrument."""
    print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config). Все выходы OFF.\n'
          'Клеммы CH1/CH2/CH3 свободны: нагрузка, DMM, осциллограф, перемычки '
          'и общая шина отключены. KEL103 INPUT OFF; режим/значение не задавать.\n'
          'Output Recall=Off проверить на панели; сетевой выключатель доступен.\n'
          'ON запрещён. Профиль: CH1 1.00 V/0.100 A, cap 2.00 V/0.300 A; '
          'CH2 1.50 V/0.200 A, cap 3.00 V/0.400 A; '
          'CH3 2.00 V/0.300 A, cap 4.00 V/0.500 A.\n'
          'Неверные каналы, NaN/Inf, отрицательные значения, превышение caps '
          'и неверный профиль должны отклоняться без SCPI. '
          'Ошибки I/O и readback проверяются только офлайн.\n'
          'В финале: OFF всех выходов, таймеров, Series/Parallel/Tracking; '
          'уставки проверены, SYST:LOC возвращает панель. Не извлекать USB. '
          'При неопределённости вручную выключить прибор; автоматического reconnect нет.\n'
          f'Допуск:\n{TOKEN}')


def h12(transport, evidence):
    """Reject invalid calls and leave the instrument in a verified OFF state."""
    dev = Keithley2231A(transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: H12Scope(channel, evidence))
    outcome = 'ABORTED'
    try:
        if '2231A-30-3' not in dev.identity():
            raise ValueError('H12 instrument identity mismatch')
        dev.check_errors()
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise ValueError('H12 requires independent topology')
        for ch in (1, 2, 3):
            evidence.add('initial_channel', channel=ch,
                         settings=channel_snapshot(dev, ch, measured=True))
        dev.apply_profile(safe_profile(), confirmed_wiring='independent')
        for label, operation in rejected_calls(dev):
            before = dev.debug_transport.commands
            try:
                operation()
            except (ProfileError, ValueError) as exc:
                if dev.debug_transport.commands != before:
                    raise AssertionError(f'Invalid argument reached SCPI: {label}') from exc
                evidence.add('expected_api_rejection', case=label,
                             exception_type=type(exc).__name__, wire_commands=0)
            else:
                raise AssertionError(f'Invalid argument accepted: {label}')
        dev.verify_profile()
        dev.emergency_off()
        for ch in (1, 2, 3):
            values = channel_snapshot(dev, ch, measured=True)
            spec = safe_profile().channels[ch - 1]
            if (values['CHAN:OUTP?'] != 0 or values['OUTP:TIM:STAT?'] != 0 or
                    values['VOLT:LIM:STAT?'] != 1 or
                    abs(values['VOLT?'] - spec.initial_voltage) > .011 or
                    abs(values['CURR?'] - spec.initial_current) > .002 or
                    abs(values['VOLT:LIM?'] - spec.max_voltage) > .011):
                raise AssertionError(f'H12 final CH{ch} settings mismatch')
            evidence.add('final_channel', channel=ch, settings=values)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise AssertionError('H12 final topology mismatch')
        evidence.add('final_output_states', outputs=require_outputs(dev))
        dev.check_errors()
        dev.diagnostic_write('SYST:LOC')
        outcome = 'PASS'
    except Exception as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        print(f'H12 остановлен: {type(exc).__name__}: {exc}')
    finally:
        if outcome != 'PASS':
            try:
                dev.emergency_off()
                evidence.add('final_output_states', outputs=require_outputs(dev))
                dev.diagnostic_write('SYST:LOC')
            except Exception as exc:
                evidence.add('shutdown_error', exception_type=type(exc).__name__)
                print('UNKNOWN OUTPUT STATE: проверить OFF вручную; повтор запрещён.')
    return outcome
