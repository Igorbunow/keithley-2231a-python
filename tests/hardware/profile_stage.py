"""Bounded, manually gated H02 profile qualification; no import-time I/O.

@file profile_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Apply public profile APIs through shared OFF-only diagnostic scopes.
@details The wrapper records real replies and checks errors after configuration
         writes and queries. Stage scopes reject unapproved commands; no output ON exists.
"""
from __future__ import annotations

import re
import time

from common import StageAdapter, channel_snapshot, require_outputs

from keithley2231a import ChannelProfile, Keithley2231A, LimitsError, Profile, SafetyError

TOKEN = ('CONFIRM H02 INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
         'CH1=V1.00_I0.100_VCAP2.00_ICAP0.300 '
         'CH2=V1.50_I0.200_VCAP3.00_ICAP0.400 '
         'CH3=V2.00_I0.300_VCAP4.00_ICAP0.500 RECALL=OFF')
FINAL_TOKEN = 'CONFIRM H02 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
H02_PANEL_TOKEN = ('CONFIRM H02 PANEL MAXV CH1=2.01 CLAMPED=2.00 '
                   'CH2=3.01 CLAMPED=3.00 CH3=4.01 CLAMPED=4.00 ALL_OFF')
H02_DEBUG_TOKEN = ('CONFIRM H02 DEBUG_BYPASS CH1=2.01 CH2=3.01 CH3=4.01 '
                   'PROFILE_MAX=2.00,3.00,4.00 HW_LIMIT=2.00,3.00,4.00 '
                   'EXPECT=120 ALL_OFF')
H02_CHANNELS = {
    1: (2.00, .300, 1.00, .100),
    2: (3.00, .400, 1.50, .200),
    3: (4.00, .500, 2.00, .300),
}
H02_HARD_PROBES = {1: 'VOLT 2.01', 2: 'VOLT 3.01', 3: 'VOLT 4.01'}
OFF = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
MODES = ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')


def guarded_profile():
    """Use distinct safe setpoints and profile ceilings on every channel."""
    return Profile('H02 varied guarded no-load', tuple(
        ChannelProfile(max_v, max_i, initial_v, initial_i)
        for max_v, max_i, initial_v, initial_i in H02_CHANNELS.values()),
        lock_front_panel=False)


class H02Scope(StageAdapter):
    """Approved commands; driver methods own their execution sequences."""
    READS = frozenset(
        ('*IDN?', 'CHAN:OUTP?', 'CURR?', 'INST:NSEL?', 'MEAS:CURR?', 'MEAS:VOLT?',
         'OUTP:PARALLEL?', 'OUTP:SER?', 'OUTP:TIM:DEL?', 'OUTP:TIM:STAT?', 'OUTP:TRAC?',
         'STAT:OPER:INST:ISUM1:COND?', 'STAT:OPER:INST:ISUM2:COND?',
         'STAT:OPER:INST:ISUM3:COND?', 'SYST:ERR?', 'VOLT:LIM:STAT?', 'VOLT:LIM?', 'VOLT?')
    )
    WRITES = frozenset(
        ('CURR 0', 'CURR 0.100', 'CURR 0.200', 'CURR 0.300',
         'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3', 'OUTP 0',
         'OUTP:PARALLEL 0', 'OUTP:SER 0', 'OUTP:TIM:STAT 0', 'OUTP:TRAC 0', 'SYST:LOC',
         'SYST:REM', 'VOLT 0', 'VOLT 1.00', 'VOLT 1.50', 'VOLT 2.00',
         *H02_HARD_PROBES.values(), 'VOLT:LIM 2.00', 'VOLT:LIM 3.00',
         'VOLT:LIM 4.00', 'VOLT:LIM:STAT 1')
    )
    MAX_SECONDS = 180

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.hard_probe_armed = False
        self.hard_probe_channels = []

    def _validate(self, command, query):
        if not query and command in H02_HARD_PROBES.values():
            if (not self.hard_probe_armed or not self.hard_probe_channels or
                    command != H02_HARD_PROBES[self.hard_probe_channels[0]] or
                    self.selected != self.hard_probe_channels[0]):
                raise ValueError('H02 instrument-limit probe lacks its explicit stage gate')
            return
        super()._validate(command, query)

    def arm_hard_probes(self):
        if self.hard_probe_armed or self.hard_probe_channels:
            raise SafetyError('H02 instrument-limit probes already armed')
        self.hard_probe_armed = True
        self.hard_probe_channels = [1, 2, 3]

    def _after_write(self, command):
        super()._after_write(command)
        if self.hard_probe_channels and command == H02_HARD_PROBES[self.hard_probe_channels[0]]:
            self.hard_probe_channels.pop(0)
            if not self.hard_probe_channels:
                self.hard_probe_armed = False


def _snapshot(dev, io, evidence, *, final=False):
    for ch in (1, 2, 3):
        values = channel_snapshot(dev, ch, measured=final)
        evidence.add('channel_snapshot', channel=ch, phase='final' if final else 'initial', values=values)
        if values['CHAN:OUTP?'] != 0:
            raise ValueError('channel selection/output mismatch')
        expected_limit, _current_cap, expected_voltage, expected_current = H02_CHANNELS[ch]
        if final and (abs(values['VOLT?'] - expected_voltage) > .0049
                      or abs(values['CURR?'] - expected_current) > .00049
                      or abs(values['VOLT:LIM?'] - expected_limit) > .0049
                      or values['VOLT:LIM:STAT?'] != 1 or values['OUTP:TIM:STAT?'] != 0):
            raise ValueError('protected profile readback mismatch')
        dev.check_errors()


def h02(transport, evidence):
    """One OFF-only application; cleanup does not restore old settings or ON state."""
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H02Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H02'
    try:
        dev.diagnostic_write('SYST:REM')
        dev.identity()
        instrument_key = dev.instrument_key
        dev.check_errors()
        require_outputs(dev)
        if any(float(dev.diagnostic_query(command)) != 0 for command in MODES):
            raise ValueError('independent mode required before profile application')
        _snapshot(dev, io, evidence)
        for ch in (1, 2, 3):
            before = io.commands
            try: dev.output(ch, True)
            except SafetyError: pass
            else: raise ValueError('unarmed output request was accepted')
            if io.commands != before: raise ValueError('unarmed output request reached transport')
            evidence.add('case', channel=ch, check='unarmed ON rejected before SCPI', outcome='PASS')
        evidence.add('phase', name='apply_profile')
        dev.apply_profile(guarded_profile())
        if dev.instrument_key != instrument_key or not dev.armed or dev.faulted:
            raise ValueError('profile identity/armed/fault state mismatch')
        evidence.add('phase', name='verify_profile')
        dev.verify_profile()
        _snapshot(dev, io, evidence, final=True)
        for ch in (1, 2, 3):
            max_voltage, max_current, _, _ = H02_CHANNELS[ch]
            for field, value in (('voltage', -.01), ('voltage', max_voltage + .01),
                                 ('voltage', float('nan')), ('current', -.001),
                                 ('current', max_current + .001), ('current', float('inf'))):
                before = io.commands
                try: dev.set_channel(ch, **{field: value})
                except LimitsError: pass
                else: raise ValueError('invalid setpoint was accepted')
                if io.commands != before: raise ValueError('invalid setpoint reached transport')
                evidence.add('case', channel=ch, check=f'{field}={value} rejected before SCPI', outcome='PASS')

        print('Проверка панели: Max Volt Set (V Protect) уже задан CH1=2.00 V, '
              'CH2=3.00 V, CH3=4.00 V. Не меняйте Max Volt Set. Все выходы OFF, '
              'нагрузка физически отключена. Для каждого канала выберите канал кнопкой '
              'CH1/CH2/CH3, нажмите V-set, введите значение и нажмите Enter: '
              'CH1=2.01 V, CH2=3.01 V, CH3=4.01 V. V-set должен стать '
              '2.00/3.00/4.00 V соответственно. Выходы не включать.')
        dev.diagnostic_write('SYST:LOC')
        from common import operator_gate
        operator_gate(H02_PANEL_TOKEN, 'После панели введите точное подтверждение.', io)
        evidence.add('approval', phase='front_panel_max_voltage', token=H02_PANEL_TOKEN)
        dev.diagnostic_write('SYST:REM')
        panel_error = dev.diagnostic_query('SYST:ERR?')
        if not panel_error.startswith('0,'):
            raise SafetyError(f'panel voltage-limit attempt left error: {panel_error}')
        require_outputs(dev)
        for ch in (1, 2, 3):
            max_voltage = H02_CHANNELS[ch][0]
            dev.diagnostic_write(f'INST:NSEL {ch}')
            actual = float(dev.diagnostic_query('VOLT?'))
            requested_voltage = max_voltage + .01
            if abs(actual - max_voltage) > .0049:
                raise SafetyError(f'CH{ch}: front panel did not clamp V-set to {max_voltage:.2f} V: {actual}')
            evidence.add('max_voltage_panel_readback', channel=ch, requested_voltage_v=requested_voltage,
                         max_voltage_v=max_voltage, actual_voltage_v=actual, output_off=True,
                         error_queue=panel_error, outcome='PASS')

        # A panel edit is external tampering by design. Disarm and reapply the
        # profile through its normal sequence before testing software limits.
        dev.emergency_off()
        dev.apply_profile(guarded_profile())
        dev.verify_profile()
        _snapshot(dev, io, evidence, final=True)
        evidence.add('case', check='panel test followed by profile reapplication',
                     setpoints_restored_v={ch: H02_CHANNELS[ch][2] for ch in (1, 2, 3)},
                     outputs_off=True, outcome='PASS')

        for ch in (1, 2, 3):
            max_voltage = H02_CHANNELS[ch][0]
            before = io.commands
            try:
                dev.set_channel(ch, voltage=max_voltage + .01)
            except LimitsError:
                pass
            else:
                raise SafetyError(f'CH{ch}: library accepted voltage above profile cap')
            if io.commands != before or not dev.armed or dev.faulted:
                raise SafetyError(f'CH{ch}: soft limit rejection changed I/O or driver state')
            evidence.add('case', channel=ch,
                         check='voltage above profile cap rejected before SCPI',
                         requested_voltage_v=max_voltage + .01, profile_max_v=max_voltage,
                         scpi_sent=False, output_off=True, outcome='PASS')

        print('Debug аппаратный предел: soft cap временно обойдётся только после отдельного '
              'допуска. Через общий драйвер выполняются CH1 VOLT 2.01, CH2 VOLT 3.01, '
              'CH3 VOLT 4.01. Ожидается 120,"Parameter overflowed", исходные уставки '
              '1.00/1.50/2.00 V и все выходы OFF.')
        operator_gate(H02_DEBUG_TOKEN,
                      'Для разрешения трёх debug bypass probes введите точный токен.', io)
        evidence.add('approval', phase='debug_bypass_instrument_limit', token=H02_DEBUG_TOKEN)
        require_outputs(dev)
        dev.verify_profile()
        io.arm_hard_probes()
        dev.debug_bypass_profile = True
        try:
            for ch in (1, 2, 3):
                requested_voltage = H02_HARD_PROBES[ch].split()[1]
                initial_voltage = H02_CHANNELS[ch][2]
                max_voltage = H02_CHANNELS[ch][0]
                dev.diagnostic_write(f'INST:NSEL {ch}')
                io.expected_error = (120, 'Parameter overflowed')
                try:
                    dev.diagnostic_write(H02_HARD_PROBES[ch])
                finally:
                    io.expected_error = None
                actual = float(dev.diagnostic_query('VOLT?'))
                if abs(actual - initial_voltage) > .0049:
                    raise SafetyError(f'CH{ch}: instrument did not preserve {initial_voltage:.2f} V after rejection')
                evidence.add('case', channel=ch,
                             check='debug bypass rejected by instrument voltage limit',
                             requested_voltage_v=float(requested_voltage), profile_max_v=max_voltage,
                             hardware_limit_v=max_voltage, actual_voltage_v=actual,
                             expected_error_code=120, output_off=True, outcome='PASS')
        finally:
            io.expected_error = None
            dev.debug_bypass_profile = False
        if io.hard_probe_channels or io.hard_probe_armed:
            raise SafetyError('H02 debug probe allowance was not fully consumed')
        require_outputs(dev)
        dev.check_errors()
        dev.emergency_off()
        dev.diagnostic_write('INST:NSEL 1')
        if float(dev.diagnostic_query('INST:NSEL?')) != 1: raise ValueError('CH1 selection restore failed')
        require_outputs(dev)
        dev.check_errors()
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'query checks passed; physical OFF confirmation still required'
    except BaseException as exc:
        outcome = 'FAIL' if io.live and isinstance(exc, Exception) and not isinstance(exc, TimeoutError) else 'ABORTED'
        reason = 'H02 stopped; inspect recorded responses; manual OFF check required'
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc),
                     live_link=io.live)
        # apply_profile may already attempt OFF. Only shutdown is permitted now.
        if io.live:
            io.cleanup = True
            try:
                dev.diagnostic_write('SYST:REM')
                dev.diagnostic_write('OUTP 0')
                require_outputs(dev)
                dev.check_errors()
                dev.diagnostic_write('SYST:LOC')
            except BaseException as cleanup_exc:
                outcome, reason = 'ABORTED', 'shutdown not confirmed; UNKNOWN OUTPUT STATE'
                evidence.add('cleanup_error', exception_type=type(cleanup_exc).__name__)
        else:
            reason = 'I/O failure; UNKNOWN OUTPUT STATE; no automatic reconnect'
    finally:
        try: transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'transport close failed; manual OFF check required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware, verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
        print(f'{outcome}: {reason}\nПроверьте физически CH1/CH2/CH3 OFF и вход нагрузки OFF.')
    return outcome


H04_TOKEN = ('CONFIRM H04 INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
             'RECALL=OFF UNRESTRICTED=LOW_SETPOINT POWER_ONLY=1.00V_0.100A '
             'TAMPER_LIMIT=5.00V TAMPER_CURRENT=0.101A')
H04_MAXV_TOKEN = ('CONFIRM H04 MAXV CH1=2.01 CLAMPED=2.00 '
                  'CH2=3.01 CLAMPED=3.00 CH3=4.01 CLAMPED=4.00 ALL_OFF')
H04_FINAL_TOKEN = 'CONFIRM H04 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'


class H04Scope(StageAdapter):
    """Bound H04 to safe profile writes, OFF-only status checks and one tamper."""
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|'
        r'STAT:OPER:INST:ISUM[123]:COND\?|OUTP:(?:SER|PARALLEL|TRAC)\?|'
        r'OUTP:TIM:(?:STAT|DEL)\?|VOLT(?::LIM(?::STAT)?)?\?|CURR\?|CHAN:OUTP\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|OUTP:TIM:STAT 0|VOLT 0(?:\.00)?|CURR 0|'
        r'VOLT:LIM (?:30\.00|5\.00|4\.00|3\.00|2\.00)|VOLT:LIM:STAT 1|'
        r'CURR 0\.(?:100|200|300|400|101)|VOLT [123]\.00)$')
    _CLEANUP_WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|INST:NSEL [123]|OUTP:TIM:STAT 0|'
        r'VOLT:LIM 4\.00|VOLT:LIM:STAT 1|CURR 0\.100|VOLT 1\.00)$')
    _CLEANUP_QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|STAT:OPER:INST:ISUM[123]:COND\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|VOLT(?::LIM(?::STAT)?)?\?|CURR\?|'
        r'CHAN:OUTP\?|OUTP:TIM:(?:STAT|DEL)\?)$')

    MAX_COMMANDS = 1600
    SHUTDOWN_RESERVE = 128
    MAX_SECONDS = 180

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.tamper_written = self.limit_tamper_written = False

    def _after_write(self, command):
        super()._after_write(command)
        if command == 'CURR 0.101': self.tamper_written = True
        if command == 'VOLT:LIM 5.00': self.limit_tamper_written = True


def _h04_snapshot(dev, io, evidence):
    """Capture initial per-channel settings before H04 changes the profile."""
    for channel in (1, 2, 3):
        values = channel_snapshot(dev, channel)
        if values['CHAN:OUTP?'] != 0.0:
            raise SafetyError(f'CH{channel} initial snapshot is inconsistent or ON')
        evidence.add('channel_snapshot', channel=channel, phase='initial', values=values)


def _h04_max_voltage_panel_gate(io, dev, evidence):
    """Require front-panel rejection before checking the public API boundary."""
    dev.verify_profile()
    limits = {1: 2.0, 2: 3.0, 3: 4.0}
    setpoints = {1: 1.0, 2: 2.0, 3: 3.0}
    currents = {1: .1, 2: .2, 3: .3}
    for channel in (1, 2, 3):
        dev.diagnostic_write(f'INST:NSEL {channel}')
        value = float(dev.diagnostic_query('VOLT:LIM?'))
        enabled = dev._bool(dev.diagnostic_query('VOLT:LIM:STAT?'))
        output = dev._bool(dev.diagnostic_query('CHAN:OUTP?'))
        voltage = float(dev.diagnostic_query('VOLT?'))
        current = float(dev.diagnostic_query('CURR?'))
        if (value != limits[channel] or not enabled or output or
                abs(voltage - setpoints[channel]) > .0049 or
                abs(current - currents[channel]) > .00049):
            raise SafetyError(f'CH{channel}: per-channel max/setpoint/current readback mismatch')
        evidence.add('max_voltage_panel_limit', channel=channel,
                     max_voltage_v=value, enabled=enabled, output=output,
                     voltage_set_v=voltage, current_set_a=current,
                     software_current_cap_a={1: .2, 2: .3, 3: .4}[channel])

    print('Установленный предел напряжения, считанный с прибора: '
          + ', '.join(f'CH{ch}={value:.2f} V' for ch, value in limits.items()))
    dev.diagnostic_write('SYST:LOC')
    print('Сначала задайте именно защиту на панели: Protection Settings → Max Volt Set = On; '
          'поочерёдно выберите CH1/CH2/CH3 и задайте соответственно 2.00/3.00/4.00 V. '
          'Проверьте, что на экране Max Volt Set значения различаются по каналам: '
          'CH1=2.00 V, CH2=3.00 V, CH3=4.00 V. Затем отдельно задайте уставки: '
          'выберите CH1, нажмите V-set, введите 2.01 и Enter; повторите для CH2 '
          'с 3.01 V и CH3 с 4.01 V. Перед каждым вводом проверьте выбранный канал. '
          'Выходы не включать. Ожидаемые уставки после ограничения: 2.00/3.00/4.00 V; '
          'это может изменить исходные уставки 1.00/2.00/3.00 V. Если Max Volt Set '
          'не отображает 2.00/3.00/4.00 V по каналам, значение выше предела принято '
          'или появилась ошибка, '
          'не вводите PASS: остановите runner через Ctrl-C; он вернёт Remote и выполнит '
          'попытку отключить выходы.')
    while io.operator_input(
            f'После проверки всех каналов введите {H04_MAXV_TOKEN}: ').strip() != H04_MAXV_TOKEN:
        print('Подтверждение не совпало; проверьте все три канала и повторите ввод.')
    evidence.add('approval', phase='front_panel_max_voltage_clamp',
                 token=H04_MAXV_TOKEN)

    dev.diagnostic_write('SYST:REM')
    error = dev.diagnostic_query('SYST:ERR?')
    if not error.startswith('0,'):
        raise SafetyError(f'front-panel max voltage attempt left instrument error: {error}')
    states = require_outputs(dev)
    if any(states.values()):
        raise SafetyError('a channel reports ON after panel max voltage test')
    for channel in (1, 2, 3):
        dev.diagnostic_write(f'INST:NSEL {channel}')
        voltage = float(dev.diagnostic_query('VOLT?'))
        current = float(dev.diagnostic_query('CURR?'))
        if (abs(voltage - limits[channel]) > .0049 or
                abs(current - currents[channel]) > .00049):
            raise SafetyError(f'CH{channel}: front-panel request was not clamped to its channel limit')
        evidence.add('max_voltage_panel_readback', channel=channel,
                     check='max voltage front-panel setpoint clamped',
                     voltage_set_v=voltage, current_set_a=current,
                     requested_voltage_v=limits[channel] + .01,
                     expected_clamped_voltage_v=limits[channel], output_off=True,
                     error_queue=error, outcome='PASS')

    for channel in (1, 2, 3):
        dev.diagnostic_write(f'INST:NSEL {channel}')
        dev.diagnostic_write(f'VOLT {setpoints[channel]:.2f}')
        voltage = float(dev.diagnostic_query('VOLT?'))
        if abs(voltage - setpoints[channel]) > .0049:
            raise SafetyError(f'CH{channel}: failed to restore initial setpoint after panel check')
        evidence.add('max_voltage_setpoint_restored', channel=channel,
                     voltage_set_v=voltage, output_off=True)

    for channel in (1, 2, 3):
        voltage_limit = limits[channel]
        current_limit = {1: .2, 2: .3, 3: .4}[channel]
        before = io.commands
        try:
            dev.set_channel(channel, voltage=voltage_limit + .01)
        except LimitsError:
            pass
        else:
            raise SafetyError(f'CH{channel}: library accepted voltage above its profile cap')
        if io.commands != before or not dev.armed or dev.faulted:
            raise SafetyError(f'CH{channel}: rejected over-limit request changed SCPI or driver state')
        evidence.add('case', channel=channel,
                     check='max voltage boundary rejected by API',
                     requested_voltage_v=voltage_limit + .01,
                     configured_max_v=voltage_limit,
                     scpi_sent=False, outcome='PASS')
        before = io.commands
        try:
            dev.set_channel(channel, current=current_limit + .01)
        except LimitsError:
            pass
        else:
            raise SafetyError(f'CH{channel}: library accepted current above its profile cap')
        if io.commands != before or not dev.armed or dev.faulted:
            raise SafetyError(f'CH{channel}: rejected current request changed SCPI or driver state')
        evidence.add('case', channel=channel,
                     check='profile current boundary rejected by API',
                     requested_current_a=current_limit + .01,
                     configured_max_a=current_limit, scpi_sent=False, outcome='PASS')


def h04(transport, evidence):
    """Test rated caps and power_only tamper detection without energizing outputs."""
    dev = Keithley2231A(transport, debug=True, debug_bypass_profile=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H04Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H04'
    try:
        dev.diagnostic_write('SYST:REM')
        dev.identity()
        instrument_key = dev.instrument_key
        require_outputs(dev)
        if any(dev._bool(dev.diagnostic_query(command)) for command in MODES):
            raise SafetyError('independent wiring required for H04')
        _h04_snapshot(dev, io, evidence)

        rated = Profile.unrestricted(name='H04 rated caps, zero output setpoint',
                                     lock_front_panel=False)
        dev.apply_profile(rated)
        dev.verify_profile()
        if dev.instrument_key != instrument_key or not dev.armed or dev.faulted:
            raise SafetyError('unrestricted profile identity/arm state mismatch')
        for channel, cap in ((1, 30.0), (2, 30.0), (3, 5.0)):
            dev.diagnostic_write(f'INST:NSEL {channel}')
            values = {'voltage_limit_v': float(dev.diagnostic_query('VOLT:LIM?')),
                      'voltage_set_v': float(dev.diagnostic_query('VOLT?')),
                      'current_set_a': float(dev.diagnostic_query('CURR?')),
                      'enabled': dev._bool(dev.diagnostic_query('CHAN:OUTP?'))}
            if (values['voltage_limit_v'] != cap or values['voltage_set_v'] != 0.0
                    or values['current_set_a'] != .1 or values['enabled']):
                raise SafetyError(f'CH{channel} unrestricted low-setpoint readback mismatch')
            evidence.add('rated_profile_low_output', channel=channel,
                         voltage_cap_v=cap, **values)
        before = io.commands
        try:
            dev.apply_profile(guarded_profile())
        except SafetyError:
            pass
        else:
            raise SafetyError('active profile replacement was accepted')
        if io.commands != before:
            raise SafetyError('active profile replacement reached SCPI')
        evidence.add('case', check='active profile immutable before SCPI', outcome='PASS')

        dev.emergency_off()
        limited = Profile('H04 per-channel boundaries', (
            ChannelProfile(2., .2, 1., .1),
            ChannelProfile(3., .3, 2., .2),
            ChannelProfile(4., .4, 3., .3),
        ), lock_front_panel=False, access='control')
        dev.apply_profile(limited)
        _h04_max_voltage_panel_gate(io, dev, evidence)
        dev.emergency_off()

        fixed = Profile('H04 power_only', tuple(ChannelProfile(4., .5, 1., .1)
                       for _ in (1, 2, 3)), lock_front_panel=False, access='power_only')
        dev.apply_profile(fixed)
        dev.verify_profile()
        before = io.commands
        for action in (lambda: dev.set_channel(1, voltage=1.01),
                       lambda: dev.timer(1, 1.0),
                       lambda: dev.remote(lock_panel=False)):
            try:
                action()
            except SafetyError:
                pass
            else:
                raise SafetyError('power_only configuration API was accepted')
        if io.commands != before:
            raise SafetyError('power_only API rejection reached SCPI')
        evidence.add('case', check='power_only API frozen before SCPI', outcome='PASS')

        # A rated-range limit remains physically valid, but exceeds the
        # protected profile and must trip while every output is OFF.
        dev.diagnostic_write('INST:NSEL 1')
        dev.diagnostic_write('VOLT:LIM 5.00')
        try:
            dev.verify_profile()
        except SafetyError:
            pass
        else:
            raise SafetyError('power_only voltage-limit tamper was not detected')
        if not io.limit_tamper_written:
            raise SafetyError('voltage-limit tamper write did not reach the instrument')
        if not dev.faulted or dev.armed:
            raise SafetyError('voltage-limit tamper did not disarm/fault the profile')
        states = require_outputs(dev)
        if any(states.values()):
            raise SafetyError('output active after voltage-limit tamper detection')
        evidence.add('case', channel=1, check='power_only voltage-limit tamper detected',
                     limit_v=5.00, outcome='PASS')

        # Re-arm the reduced profile while OFF, then test an exact setpoint
        # edit that remains inside both profile caps and hardware ratings.
        dev.apply_profile(fixed)
        dev.verify_profile()
        dev.diagnostic_write('INST:NSEL 3')
        dev.diagnostic_write('CURR 0.101')
        try:
            dev.verify_profile()
        except SafetyError:
            pass
        else:
            raise SafetyError('power_only setpoint tamper was not detected')
        if not io.tamper_written:
            raise SafetyError('power_only tamper write did not reach the instrument')
        if not dev.faulted or dev.armed:
            raise SafetyError('tamper did not disarm/fault the profile')
        evidence.add('case', channel=3, check='power_only exact current tamper detected',
                     setpoint_a=.101, tamper_written=io.tamper_written, outcome='PASS')
        states = require_outputs(dev)
        if any(states.values()):
            raise SafetyError('output active after tamper detection')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'all H04 checks passed; outputs stayed OFF'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or isinstance(
            exc, (TimeoutError, KeyboardInterrupt, InterruptedError)) else 'FAIL')
        reason = f'H04 stopped: {type(exc).__name__}: {exc}; verify all outputs OFF manually'
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc),
                     live_link=io.live)
        if io.live:
            try:
                io.cleanup = True
                dev.diagnostic_write('SYST:REM')
                dev.diagnostic_write('OUTP 0')
                require_outputs(dev)
                for channel in (1, 2, 3):
                    dev.diagnostic_write(f'INST:NSEL {channel}')
                    dev.diagnostic_write('OUTP:TIM:STAT 0')
                    dev.diagnostic_write('VOLT:LIM 4.00')
                    dev.diagnostic_write('VOLT:LIM:STAT 1')
                    dev.diagnostic_write('CURR 0.100')
                    dev.diagnostic_write('VOLT 1.00')
                    if (abs(float(dev.diagnostic_query('VOLT?')) - 1.0) > .0049
                            or abs(float(dev.diagnostic_query('CURR?')) - .1) > .00049
                            or abs(float(dev.diagnostic_query('VOLT:LIM?')) - 4.0) > .0049
                            or not dev._bool(dev.diagnostic_query('VOLT:LIM:STAT?'))):
                        raise SafetyError(f'CH{channel}: safe H04 cleanup readback mismatch')
                require_outputs(dev)
                dev.diagnostic_write('SYST:LOC')
            except BaseException as cleanup_exc:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT STATE'
                evidence.add('cleanup_error', exception_type=type(cleanup_exc).__name__)
    finally:
        try:
            transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'transport close failed; manual OFF check required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware, verification='HW OBSERVED' if outcome == 'PASS'
                     else 'HW UNVERIFIED')
        print(f'{outcome}: {reason}\nПроверьте физически CH1/CH2/CH3 OFF; нагрузка отключена.')
    return outcome
