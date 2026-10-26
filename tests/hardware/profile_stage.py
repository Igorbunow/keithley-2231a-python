"""Bounded, manually gated H02 profile qualification; no import-time I/O.

@file profile_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Apply the public profile API with an exact OFF-only SCPI plan.
@details The wrapper records real replies and checks errors after configuration
         writes. Plan drift is rejected before transmission. No output ON exists.
"""
from __future__ import annotations

import math
import re
import time

from keithley2231a import ChannelProfile, Keithley2231A, LimitsError, Profile, SafetyError

TOKEN = ('CONFIRM H02 INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
         'V=1.00 I=0.100 VCAP=4.00 ICAP=0.500 RECALL=OFF')
FINAL_TOKEN = 'CONFIRM H02 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
OFF = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
MODES = ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')
SNAPSHOT = ('INST:NSEL?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
            'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?')
METERS = ('MEAS:VOLT?', 'MEAS:CURR?')


def guarded_profile():
    """Same reduced caps on all channels; the actual current setting is 0.100 A."""
    return Profile('H02 guarded no-load', tuple(ChannelProfile(4., .5, 1., .1)
                   for _ in (1, 2, 3)), lock_front_panel=False)


def command_plan():
    """Exact successful trace, including error reads injected after each write.

    Kept explicit so the real public driver cannot silently expand the approved
    hardware action. A focused offline test compares the complete actual trace.
    """
    steps = []
    def add(*commands):
        for command in commands:
            steps.append(command)
            if not command.endswith('?') and command != 'SYST:LOC':
                steps.append('SYST:ERR?')
    def snapshots(measured=False):
        for ch in (1, 2, 3):
            add(f'INST:NSEL {ch}', *SNAPSHOT, *(METERS if measured else ()), 'SYST:ERR?')
    add('SYST:REM', '*IDN?', 'SYST:ERR?', *OFF, *MODES)
    snapshots()
    # Keithley2231A.apply_profile(), independent wiring, panel lock disabled.
    add('SYST:REM', '*IDN?', 'OUTP 0', *OFF,
        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0')
    for ch in (1, 2, 3):
        add(f'INST:NSEL {ch}', 'OUTP:TIM:STAT 0', 'OUTP:TIM:STAT?',
            'VOLT 0', 'CURR 0', 'VOLT:LIM 4.00', 'VOLT:LIM:STAT 1',
            'VOLT:LIM:STAT?', 'VOLT:LIM?', 'CURR 0.100', 'VOLT 1.00',
            f'INST:NSEL {ch}', 'VOLT?', 'CURR?')
    add(*MODES, *OFF)
    # Keithley2231A.verify_profile().
    add(*MODES, *OFF)
    for ch in (1, 2, 3):
        add(f'INST:NSEL {ch}', 'VOLT:LIM:STAT?', 'VOLT:LIM?',
            f'INST:NSEL {ch}', 'VOLT?', 'CURR?')
    snapshots(measured=True)
    # emergency_off() disarms; leave CH1 selected and panel control available.
    add('SYST:REM', 'OUTP 0', *OFF, 'INST:NSEL 1', 'INST:NSEL?', *OFF,
        'SYST:ERR?', 'SYST:LOC')
    return tuple(steps)


class CheckedIO:
    """Record/guard the approved trace; never retry a failed physical transaction."""
    def __init__(self, transport, evidence):
        self.transport, self.evidence = transport, evidence
        self.plan, self.index = command_plan(), 0
        self.attempts = 0
        self.live, self.cleanup = True, False
        self.started = time.monotonic()

    def _transfer(self, command, query):
        # Count rejected calls too: the stage guard must not hide a driver bug
        # in tests which require rejection before any transport operation.
        self.attempts += 1
        if not self.live:
            raise OSError('uncertain link; no further SCPI')
        if self.cleanup:
            if command not in ('SYST:REM', 'OUTP 0', 'SYST:ERR?', 'SYST:LOC', *OFF):
                raise ValueError('only shutdown is permitted after a failure')
        else:
            if time.monotonic() - self.started > 180:
                raise TimeoutError('H02 time budget exceeded')
            if self.index >= len(self.plan) or command != self.plan[self.index]:
                raise ValueError('command differs from approved H02 plan')
            self.index += 1
        self.evidence.add('query' if query else 'write', command=command)
        try:
            answer = self.transport.query(command) if query else self.transport.write(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command, exception_type=type(exc).__name__,
                              cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
            raise
        if query:
            units = {'VOLT?': 'V', 'CURR?': 'A', 'VOLT:LIM?': 'V',
                     'OUTP:TIM:DEL?': 's', 'MEAS:VOLT?': 'V', 'MEAS:CURR?': 'A'}
            record = self.evidence.add('read', command=command, response=answer, units=units.get(command))
            print(f'{command}: {record["response"]}')
        return answer

    def query(self, command):
        return self._transfer(command, True)

    def write(self, command):
        self._transfer(command, False)
        if command != 'SYST:LOC': self.errors_clear()

    def errors_clear(self):
        code, message = self.query('SYST:ERR?').split(',', 1)
        if int(code.strip()) != 0 or not message.strip():
            raise ValueError('instrument error recorded; stop H02')


def _all_off(io):
    for command in OFF:
        value = float(io.query(command))
        if not math.isfinite(value) or value != int(value) or not 0 <= value <= 255 or int(value) & 8:
            raise ValueError('OFF status check failed')


def _snapshot(io, evidence, *, final=False):
    for ch in (1, 2, 3):
        io.write(f'INST:NSEL {ch}')
        values = {command: float(io.query(command)) for command in (*SNAPSHOT, *(METERS if final else ()))}
        evidence.add('channel_snapshot', channel=ch, phase='final' if final else 'initial', values=values)
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError('nonfinite channel snapshot')
        if values['INST:NSEL?'] != ch or values['CHAN:OUTP?'] != 0:
            raise ValueError('channel selection/output mismatch')
        if any(values[key] not in (0, 1) for key in ('VOLT:LIM:STAT?', 'OUTP:TIM:STAT?')):
            raise ValueError('invalid limit/timer state')
        if final and (abs(values['VOLT?'] - 1.) > .0049 or abs(values['CURR?'] - .1) > .00049
                      or abs(values['VOLT:LIM?'] - 4.) > .0049
                      or values['VOLT:LIM:STAT?'] != 1 or values['OUTP:TIM:STAT?'] != 0):
            raise ValueError('protected profile readback mismatch')
        io.errors_clear()


def h02(transport, evidence):
    """One OFF-only application; cleanup does not restore old settings or ON state."""
    io = CheckedIO(transport, evidence)
    dev = Keithley2231A(io)
    outcome, reason = 'ABORTED', 'incomplete H02'
    try:
        io.write('SYST:REM')
        dev.identity()
        instrument_key = dev.instrument_key
        io.errors_clear()
        _all_off(io)
        if any(float(io.query(command)) != 0 for command in MODES):
            raise ValueError('independent mode required before profile application')
        _snapshot(io, evidence)
        for ch in (1, 2, 3):
            before = io.attempts
            try: dev.output(ch, True)
            except SafetyError: pass
            else: raise ValueError('unarmed output request was accepted')
            if io.attempts != before: raise ValueError('unarmed output request reached transport')
            evidence.add('case', channel=ch, check='unarmed ON rejected before SCPI', outcome='PASS')
        evidence.add('phase', name='apply_profile')
        dev.apply_profile(guarded_profile())
        if dev.instrument_key != instrument_key or not dev.armed or dev.faulted:
            raise ValueError('profile identity/armed/fault state mismatch')
        evidence.add('phase', name='verify_profile')
        dev.verify_profile()
        _snapshot(io, evidence, final=True)
        for ch in (1, 2, 3):
            for field, value in (('voltage', -.01), ('voltage', 4.01), ('voltage', float('nan')),
                                 ('current', -.001), ('current', .501), ('current', float('inf'))):
                before = io.attempts
                try: dev.set_channel(ch, **{field: value})
                except LimitsError: pass
                else: raise ValueError('invalid setpoint was accepted')
                if io.attempts != before: raise ValueError('invalid setpoint reached transport')
                evidence.add('case', channel=ch, check=f'{field}={value} rejected before SCPI', outcome='PASS')
        dev.emergency_off()
        io.write('INST:NSEL 1')
        if float(io.query('INST:NSEL?')) != 1: raise ValueError('CH1 selection restore failed')
        _all_off(io)
        io.errors_clear()
        io.write('SYST:LOC')
        if io.index != len(io.plan): raise ValueError('incomplete H02 trace')
        outcome, reason = 'PASS', 'query checks passed; physical OFF confirmation still required'
    except BaseException as exc:
        outcome = 'FAIL' if io.live and isinstance(exc, Exception) and not isinstance(exc, TimeoutError) else 'ABORTED'
        reason = 'H02 stopped; inspect recorded responses; manual OFF check required'
        evidence.add('stage_error', exception_type=type(exc).__name__, live_link=io.live)
        # apply_profile may already attempt OFF. Only shutdown is permitted now.
        if io.live:
            io.cleanup = True
            try:
                io.write('SYST:REM')
                io.write('OUTP 0')
                _all_off(io)
                io.errors_clear()
                io.write('SYST:LOC')
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


class H04CheckedIO:
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
        r'^(?:SYST:ERR\?|INST:NSEL\?|STAT:OPER:INST:ISUM[123]:COND\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|VOLT(?::LIM(?::STAT)?)?\?|CURR\?|'
        r'CHAN:OUTP\?|OUTP:TIM:(?:STAT|DEL)\?)$')

    def __init__(self, transport, evidence):
        self.transport, self.evidence = transport, evidence
        self.live = True
        self.commands = 0
        self.started = time.monotonic()
        self.tamper_written = False
        self.limit_tamper_written = False
        self.cleanup = False

    def operator_input(self, prompt):
        """Pause the hardware deadline while the operator performs a manual gate."""
        pause_started = time.monotonic()
        try:
            return input(prompt)
        finally:
            self.started += time.monotonic() - pause_started

    def _transfer(self, command, query):
        self.commands += 1
        over_budget = self.commands > 1000 or time.monotonic() - self.started > 180
        if over_budget and not self.cleanup:
            raise TimeoutError('H04 command/time budget exceeded')
        if self.cleanup:
            allowed = (self._CLEANUP_QUERY.fullmatch(command) if query else
                       self._CLEANUP_WRITE.fullmatch(command))
            if self.commands > 1100 or not allowed:
                raise TimeoutError('H04 shutdown reserve exhausted or command not allowed')
        if not self.live:
            raise OSError('uncertain link; no further SCPI')
        if query and not self._QUERY.fullmatch(command):
            raise ValueError(f'H04 query not allowed: {command}')
        if not query and (not self._WRITE.fullmatch(command) or
                          command in ('SYST:RWLOCK', 'OUTP 1', 'CHAN:OUTP 1')):
            raise ValueError(f'H04 write not allowed: {command}')
        self.evidence.add('query' if query else 'write', command=command)
        try:
            answer = self.transport.query(command) if query else self.transport.write(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        if query:
            record = self.evidence.add('read', command=command, response=answer,
                                       units={'VOLT?': 'V', 'VOLT:LIM?': 'V',
                                              'CURR?': 'A'}.get(command))
            print(f'{command}: {record["response"]}')
        if not query and command == 'CURR 0.101':
            self.tamper_written = True
        if not query and command == 'VOLT:LIM 5.00':
            self.limit_tamper_written = True
        return answer

    def query(self, command):
        return self._transfer(command, True)

    def write(self, command):
        self._transfer(command, False)
        if command != 'SYST:LOC':
            code, message = self.query('SYST:ERR?').split(',', 1)
            if int(code.strip()) != 0 or not message.strip():
                raise ValueError('instrument error recorded; stop H04')


def _h04_all_off(io):
    states = {}
    for ch in (1, 2, 3):
        mask = float(io.query(f'STAT:OPER:INST:ISUM{ch}:COND?'))
        if not math.isfinite(mask) or mask != int(mask) or int(mask) & 8:
            raise SafetyError(f'CH{ch} is not confirmed OFF')
        states[ch] = int(mask)
    return states


def _h04_snapshot(io, evidence):
    """Capture initial per-channel settings before H04 changes the profile."""
    for channel in (1, 2, 3):
        io.write(f'INST:NSEL {channel}')
        values = {command: float(io.query(command)) for command in
                  ('INST:NSEL?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                   'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?')}
        if not all(math.isfinite(value) for value in values.values()):
            raise SafetyError(f'CH{channel} initial profile snapshot is invalid')
        if (values['INST:NSEL?'] != channel or values['CHAN:OUTP?'] != 0.0
                or values['VOLT:LIM:STAT?'] not in (0.0, 1.0)
                or values['OUTP:TIM:STAT?'] not in (0.0, 1.0)):
            raise SafetyError(f'CH{channel} initial snapshot is inconsistent or ON')
        evidence.add('channel_snapshot', channel=channel, phase='initial', values=values)


def _h04_max_voltage_panel_gate(io, dev, evidence):
    """Require front-panel rejection before checking the public API boundary."""
    dev.verify_profile()
    limits = {1: 2.0, 2: 3.0, 3: 4.0}
    setpoints = {1: 1.0, 2: 2.0, 3: 3.0}
    currents = {1: .1, 2: .2, 3: .3}
    for channel in (1, 2, 3):
        io.write(f'INST:NSEL {channel}')
        value = float(io.query('VOLT:LIM?'))
        enabled = dev._bool(io.query('VOLT:LIM:STAT?'))
        output = dev._bool(io.query('CHAN:OUTP?'))
        voltage = float(io.query('VOLT?'))
        current = float(io.query('CURR?'))
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
    io.write('SYST:LOC')
    print('На панели Protection Settings → Max Volt Set установите On и значения: '
          'CH1=2.00 V, CH2=3.00 V, CH3=4.00 V. На каждом канале попробуйте задать '
          'уставку на 0.01 V выше его предела (CH1 2.01 V, CH2 3.01 V, CH3 4.01 V). '
          'Выходы не включать. Прибор должен ограничить уставки соответственно 2.00/3.00/4.00 V; '
          'это может изменить исходные уставки 1.00/2.00/3.00 V. Если значение выше '
          'предела принято или появилась ошибка, '
          'не вводите PASS: остановите runner через Ctrl-C; он вернёт Remote и выполнит '
          'попытку отключить выходы.')
    while io.operator_input(
            f'После проверки всех каналов введите {H04_MAXV_TOKEN}: ').strip() != H04_MAXV_TOKEN:
        print('Подтверждение не совпало; проверьте все три канала и повторите ввод.')
    evidence.add('approval', phase='front_panel_max_voltage_clamp',
                 token=H04_MAXV_TOKEN)

    io.write('SYST:REM')
    error = io.query('SYST:ERR?')
    if not error.startswith('0,'):
        raise SafetyError(f'front-panel max voltage attempt left instrument error: {error}')
    states = _h04_all_off(io)
    if any(mask & 8 for mask in states.values()):
        raise SafetyError('a channel reports ON after panel max voltage test')
    for channel in (1, 2, 3):
        io.write(f'INST:NSEL {channel}')
        voltage = float(io.query('VOLT?'))
        current = float(io.query('CURR?'))
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
        io.write(f'INST:NSEL {channel}')
        io.write(f'VOLT {setpoints[channel]:.2f}')
        voltage = float(io.query('VOLT?'))
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
    io = H04CheckedIO(transport, evidence)
    dev = Keithley2231A(io)
    outcome, reason = 'ABORTED', 'incomplete H04'
    try:
        io.write('SYST:REM')
        dev.identity()
        instrument_key = dev.instrument_key
        _h04_all_off(io)
        if any(dev._bool(io.query(command)) for command in MODES):
            raise SafetyError('independent wiring required for H04')
        _h04_snapshot(io, evidence)

        rated = Profile.unrestricted(name='H04 rated caps, zero output setpoint',
                                     lock_front_panel=False)
        dev.apply_profile(rated)
        dev.verify_profile()
        if dev.instrument_key != instrument_key or not dev.armed or dev.faulted:
            raise SafetyError('unrestricted profile identity/arm state mismatch')
        for channel, cap in ((1, 30.0), (2, 30.0), (3, 5.0)):
            io.write(f'INST:NSEL {channel}')
            values = {'voltage_limit_v': float(io.query('VOLT:LIM?')),
                      'voltage_set_v': float(io.query('VOLT?')),
                      'current_set_a': float(io.query('CURR?')),
                      'enabled': dev._bool(io.query('CHAN:OUTP?'))}
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
        io.write('INST:NSEL 1')
        io.write('VOLT:LIM 5.00')
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
        states = _h04_all_off(io)
        if any(mask & 8 for mask in states.values()):
            raise SafetyError('output active after voltage-limit tamper detection')
        evidence.add('case', channel=1, check='power_only voltage-limit tamper detected',
                     limit_v=5.00, outcome='PASS')

        # Re-arm the reduced profile while OFF, then test an exact setpoint
        # edit that remains inside both profile caps and hardware ratings.
        dev.apply_profile(fixed)
        dev.verify_profile()
        io.write('INST:NSEL 3')
        io.write('CURR 0.101')
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
        states = _h04_all_off(io)
        if any(mask & 8 for mask in states.values()):
            raise SafetyError('output active after tamper detection')
        io.write('SYST:LOC')
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
                io.write('SYST:REM')
                io.write('OUTP 0')
                _h04_all_off(io)
                for channel in (1, 2, 3):
                    io.write(f'INST:NSEL {channel}')
                    io.write('OUTP:TIM:STAT 0')
                    io.write('VOLT:LIM 4.00')
                    io.write('VOLT:LIM:STAT 1')
                    io.write('CURR 0.100')
                    io.write('VOLT 1.00')
                    if (abs(float(io.query('VOLT?')) - 1.0) > .0049
                            or abs(float(io.query('CURR?')) - .1) > .00049
                            or abs(float(io.query('VOLT:LIM?')) - 4.0) > .0049
                            or not dev._bool(io.query('VOLT:LIM:STAT?'))):
                        raise SafetyError(f'CH{channel}: safe H04 cleanup readback mismatch')
                _h04_all_off(io)
                io.write('SYST:LOC')
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
