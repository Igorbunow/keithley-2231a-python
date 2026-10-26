"""Manually gated H04-Powered profile-range qualification.

@file powered_profile_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Qualify low-power profile limits with automatic KEL103 readings.
@details One KEL103 is moved between independent channels only at explicit gates.
         Each output point is enabled individually and shut down before the next.
         The runner never sends a setpoint outside the active software profile.
"""
from __future__ import annotations

import math
import re
import time

from keithley2231a import (ChannelProfile, Keithley2231A, LimitsError, Profile,
                          SafetyError)

MODES = ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')
OFF = tuple(f'STAT:OPER:INST:ISUM{channel}:COND?' for channel in (1, 2, 3))
CV_LOADS = {2.2: 47.0, 3.3: 47.0, 4.4: 47.0, 4.5: 56.0}
CC_LOADS = {0.100: 22.0, 0.250: 12.0, 0.500: 5.0}
CHANNEL_VOLTAGES = {1: 2.2, 2: 3.3, 3: 4.4}
H04P_MAXV_TOKEN = ('CONFIRM H04P MAXV CH1=4.51 CLAMPED=4.50 '
                   'CH2=4.51 CLAMPED=4.50 CH3=4.51 CLAMPED=4.50 '
                   'ALL_OFF INPUT=OFF LOAD=DISCONNECTED')
H04P_FINAL_TOKEN = 'CONFIRM H04P CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
MAX_COMMANDS = 5000
MAX_SECONDS = 900
SHUTDOWN_RESERVE = 128


def approval_token(load_transport='udp', load_port=18190,
                   load_serial_port='/dev/ttyACM0'):
    """Return an exact H04-Powered gate tied to the explicitly selected load link."""
    if load_transport == 'udp':
        connection = f'LAN=UDP:{load_port}'
    elif load_transport == 'serial':
        connection = f'USB={load_serial_port} BAUD=115200'
    else:
        raise ValueError('load transport must be udp or serial')
    return ('CONFIRM H04P INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
            f'{connection} RECALL=OFF VCAP=4.50 ICAP=0.500 '
            'CV=3.30,2.20,3.30,4.40,4.50 CC=0.100,0.250,0.500')


class PoweredProfileIO:
    """Bound SCPI to H04-Powered profiles, readbacks and one output at a time."""
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|'
        r'OUTP:TIM:(?:STAT|DEL)\?|MEAS:(?:VOLT|CURR|POW)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|2\.20|3\.00|3\.30|4\.40|4\.50)|'
        r'CURR (?:0|0\.100|0\.250|0\.500)|'
        r'VOLT:LIM 4\.50|VOLT:LIM:STAT 1)$')

    def __init__(self, transport, evidence):
        self.transport, self.evidence = transport, evidence
        self.live = True
        self.commands = 0
        self.started = time.monotonic()
        self.selected = 1
        self.authorized_channel = None
        self.outputs = {channel: False for channel in (1, 2, 3)}

    def operator_input(self, prompt):
        """Pause the hardware deadline while the operator handles a manual gate."""
        pause_started = time.monotonic()
        try:
            return input(prompt)
        finally:
            self.started += time.monotonic() - pause_started

    @staticmethod
    def _shutdown_command(command, query):
        """Keep OFF, OFF verification and error checks available after a budget fault."""
        if query:
            return command == 'SYST:ERR?' or command in OFF
        return (command in ('SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:TIM:STAT 0') or
                command.startswith('INST:NSEL ') or command == 'CHAN:OUTP 0')

    def _transfer(self, command, query):
        self.commands += 1
        over_budget = (self.commands > MAX_COMMANDS or
                       time.monotonic() - self.started > MAX_SECONDS)
        if over_budget and not self._shutdown_command(command, query):
            raise TimeoutError('H04-Powered command/time budget exceeded')
        if self.commands > MAX_COMMANDS + SHUTDOWN_RESERVE:
            raise TimeoutError('H04-Powered shutdown command reserve exhausted')
        if not self.live:
            raise OSError('uncertain link; no further SCPI')
        if query and not self._QUERY.fullmatch(command):
            raise ValueError(f'H04-Powered query not allowed: {command}')
        if not query:
            if not self._WRITE.fullmatch(command):
                raise ValueError(f'H04-Powered write not allowed: {command}')
            if command == 'CHAN:OUTP 1' and (
                    self.authorized_channel != self.selected or
                    any(state for channel, state in self.outputs.items()
                        if channel != self.selected)):
                raise ValueError('only the operator-approved channel may turn ON')
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
                                              'CURR?': 'A', 'MEAS:VOLT?': 'V',
                                              'MEAS:CURR?': 'A', 'MEAS:POW?': 'W'}.get(command))
            print(f'{command}: {record["response"]}')
            if command != 'SYST:ERR?':
                self._check_error_queue(command)
            return answer
        if command.startswith('INST:NSEL '):
            self.selected = int(command.rsplit(' ', 1)[1])
        elif command == 'OUTP 0':
            self.outputs = {channel: False for channel in (1, 2, 3)}
        elif command.startswith('CHAN:OUTP '):
            self.outputs[self.selected] = command.endswith('1')
        if command != 'SYST:LOC':
            self._check_error_queue(command)
        return answer

    def _check_error_queue(self, source):
        response = self._transfer('SYST:ERR?', True)
        code, message = response.split(',', 1)
        if int(code.strip()) != 0 or not message.strip():
            raise ValueError(f'instrument error after {source}: {response}')

    def query(self, command):
        return self._transfer(command, True)

    def write(self, command):
        self._transfer(command, False)


def _gate(token, prompt, io):
    print(prompt)
    while True:
        try:
            answer = io.operator_input(f'Подтверждение: {token}\n> ').strip()
        except (EOFError, KeyboardInterrupt):
            raise InterruptedError('operator gate interrupted')
        if answer == token:
            return
        print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')


def _all_off(io):
    states = {}
    for channel, command in enumerate(OFF, 1):
        raw = io.query(command)
        value = float(raw)
        if (not math.isfinite(value) or value != int(value) or
                not 0 <= value <= 255 or int(value) & 8):
            raise SafetyError(f'CH{channel} is not confirmed OFF: {raw}')
        states[channel] = int(value)
    return states


def _profile(access='control'):
    """Use the same conservative caps while the load is physically connected."""
    channels = tuple(ChannelProfile(4.5, .5,
                                    CHANNEL_VOLTAGES[channel] if access == 'power_only' else 3.3,
                                    .1)
                     for channel in (1, 2, 3))
    return Profile('H04-Powered ' + access, channels, wiring='independent',
                   lock_front_panel=False, verify_every_s=3600.0, access=access)


def _max_voltage_panel_gate(io, dev, evidence):
    """Verify the 4.50 V front-panel ceiling and library rejection before loading."""
    profile = _profile(access='control')
    dev.apply_profile(profile)
    dev.verify_profile()
    for channel in (1, 2, 3):
        io.write(f'INST:NSEL {channel}')
        maximum = float(io.query('VOLT:LIM?'))
        enabled = dev._bool(io.query('VOLT:LIM:STAT?'))
        voltage = float(io.query('VOLT?'))
        output = dev._bool(io.query('CHAN:OUTP?'))
        if maximum != 4.5 or not enabled or abs(voltage - 3.3) > .0049 or output:
            raise SafetyError(f'CH{channel}: expected 4.50 V ceiling and 3.30 V setpoint, output OFF')
        evidence.add('max_voltage_panel_limit', channel=channel,
                     max_voltage_v=maximum, enabled=enabled,
                     voltage_set_v=voltage, output=output)
    print('Установленный предел напряжения, считанный с прибора: '
          'CH1=4.50 V, CH2=4.50 V, CH3=4.50 V.')
    io.write('SYST:LOC')
    print('До подключения нагрузки откройте на панели Protection Settings → Max Volt Set '
          'и подтвердите для CH1/CH2/CH3 Max V=4.50 V, состояние On. Для каждого канала '
          'попробуйте ввести V-set=4.51 V и подтвердить; выходы не включать. Прибор должен '
          'ограничить уставку значением 4.50 V, не принимая 4.51 V; она может измениться '
          'с 3.30 V на предельные 4.50 V. Если отображается значение выше 4.50 V или появилась '
          'ошибка, остановите runner через Ctrl-C.')
    while io.operator_input(
            f'После проверки всех каналов введите {H04P_MAXV_TOKEN}: ').strip() != H04P_MAXV_TOKEN:
        print('Подтверждение не совпало; проверьте Max Volt Set и все три канала.')
    evidence.add('operator_gate', phase='front_panel_max_voltage_clamp',
                 token=H04P_MAXV_TOKEN)

    io.write('SYST:REM')
    _all_off(io)
    for channel in (1, 2, 3):
        io.write(f'INST:NSEL {channel}')
        voltage = float(io.query('VOLT?'))
        current = float(io.query('CURR?'))
        if abs(voltage - 4.5) > .0049 or abs(current - .1) > .00049:
            raise SafetyError(f'CH{channel}: front-panel request was not clamped to 4.50 V')
        evidence.add('max_voltage_panel_readback', channel=channel,
                     check='max voltage front-panel setpoint clamped',
                     voltage_set_v=voltage, current_set_a=current,
                     expected_clamped_voltage_v=4.5, output_off=True,
                     outcome='PASS')
        io.write(f'INST:NSEL {channel}')
        io.write('VOLT 3.30')
        if abs(float(io.query('VOLT?')) - 3.3) > .0049:
            raise SafetyError(f'CH{channel}: failed to restore 3.30 V after panel check')
        before = io.commands
        rejection = _expect_local_rejection(dev, io, channel, voltage=4.51)
        if io.commands != before or not dev.armed or dev.faulted:
            raise SafetyError(f'CH{channel}: API over-limit rejection changed SCPI or driver state')
        evidence.add('case', check='max voltage boundary rejected by API',
                     configured_max_v=4.5, scpi_sent=False,
                     **rejection)
    dev.emergency_off()


def _expect_local_rejection(dev, io, channel, *, voltage=None, current=None):
    before = io.commands
    try:
        dev.set_channel(channel, voltage=voltage, current=current)
    except LimitsError:
        pass
    else:
        raise SafetyError('out-of-profile setpoint was accepted')
    if io.commands != before:
        raise SafetyError('out-of-profile setpoint reached SCPI')
    evidence_value = {'channel': channel, 'outcome': 'PASS',
                      'transport_commands': 0}
    if voltage is not None:
        evidence_value.update(field='voltage', requested_v=voltage)
    else:
        evidence_value.update(field='current', requested_a=current)
    return evidence_value


def _measure_point(dev, io, evidence, load, channel, *, phase, point,
                   voltage, current, resistance, expected_mode, configure=True):
    if any(io.outputs.values()):
        raise SafetyError(f'CH{channel} must be OFF before configuring a point')
    if load.input_state() != 'OFF':
        raise SafetyError('KEL103 INPUT must be OFF before configuring a point')
    if configure:
        dev.set_channel(channel, voltage=voltage, current=current)
    else:
        io.write(f'INST:NSEL {channel}')
        if (abs(float(io.query('VOLT?')) - voltage) > .0049 or
                abs(float(io.query('CURR?')) - current) > .00049):
            raise SafetyError('fixed power_only preset readback mismatch')
    actual_resistance = load.configure_h04p_cr(resistance)
    if abs(actual_resistance - resistance) > .001:
        raise SafetyError('KEL103 resistance did not verify')
    load.set_input(True)
    io.authorized_channel = channel
    try:
        dev.output(channel, True)
    finally:
        io.authorized_channel = None
    try:
        _all_off_except(io, channel)
        sample = dev.read_channel(channel)
        load_values = load.measurements()
        scpi_power = float(io.query('MEAS:POW?'))
        if not sample.enabled or sample.regulation != expected_mode:
            raise SafetyError(f'CH{channel} expected {expected_mode}, got {sample.regulation}')
        if (abs(sample.voltage_v - load_values['kel103_voltage_v']) > .06 or
                abs(sample.current_a - load_values['kel103_current_a']) > .03):
            raise SafetyError('Keithley/KEL103 V/I disagreement exceeds H03 tolerances')
        if (abs(load_values['kel103_power_w'] -
                load_values['kel103_voltage_v'] * load_values['kel103_current_a']) > .31 or
                abs(scpi_power - sample.power_w) > .31):
            raise SafetyError('power comparison exceeds 0.31 W tolerance')
        evidence.add('measurement', channel=channel, phase=phase, point=point,
                     mode=expected_mode, expected_mode=expected_mode,
                     actual_mode=sample.regulation,
                     set_voltage_v=voltage, set_current_limit_a=current,
                     load_resistance_ohm=actual_resistance,
                     keithley_voltage_v=sample.voltage_v,
                     keithley_current_a=sample.current_a,
                     keithley_power_api_w=sample.power_w,
                     keithley_power_scpi_w=scpi_power,
                     keithley_operation_status=sample.operation_status_mask,
                     keithley_questionable_status=sample.questionable_status_mask,
                     voltage_difference_max_v=.06, current_difference_max_a=.03,
                     power_difference_max_w=.31, **load_values)
        print(f'H04-Powered CH{channel} {point}: PSU {sample.voltage_v:.4f} V / '
              f'{sample.current_a:.4f} A / {sample.power_w:.4f} W {sample.regulation}; '
              f'KEL103 {load_values["kel103_voltage_v"]:.4f} V / '
              f'{load_values["kel103_current_a"]:.4f} A / '
              f'{load_values["kel103_power_w"]:.4f} W')
    finally:
        dev.output(channel, False)
        _all_off(io)
        load.set_input(False)


def _all_off_except(io, target):
    states = {}
    for channel, command in enumerate(OFF, 1):
        raw = io.query(command)
        value = float(raw)
        if not math.isfinite(value) or value != int(value) or not 0 <= value <= 255:
            raise SafetyError(f'invalid CH{channel} status: {raw}')
        enabled = bool(int(value) & 8)
        if enabled != (channel == target):
            raise SafetyError(f'expected only CH{target} ON; CH{channel}={raw}')
        states[channel] = int(value)
    return states


def h04_powered(transport, evidence, load):
    """Run bounded low-power CV/CC points; manually gate only load movement."""
    io = PoweredProfileIO(transport, evidence)
    dev = Keithley2231A(io)
    outcome, reason = 'ABORTED', 'incomplete H04-Powered'
    try:
        load_identity = load.identify()
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF at H04-Powered start')
        baseline = io.query('SYST:ERR?')
        code, message = baseline.split(',', 1)
        evidence.add('instrument_error_queue_baseline', response=baseline,
                     clean=int(code.strip()) == 0 and bool(message.strip()))
        if int(code.strip()) != 0 or not message.strip():
            raise SafetyError(f'pre-existing Keithley error: {baseline}')
        io.write('SYST:REM')
        identity = dev.identity()
        if identity.split(',')[-1].strip() != evidence.firmware:
            raise SafetyError('instrument identity changed during H04-Powered preflight')
        _all_off(io)
        if any(dev._bool(io.query(command)) for command in MODES):
            raise SafetyError('independent wiring required for H04-Powered')
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must remain OFF during maximum voltage check')
        _max_voltage_panel_gate(io, dev, evidence)
        safe_load_identity = re.sub(r'(?i)(SN\s*:?\s*)[^,\s]+',
                                    r'\1[REDACTED]', load_identity)
        load_connection = getattr(load, 'transport', None)
        if not isinstance(load_connection, str):
            load_connection = type(load).__name__
        evidence.add('kel103_preflight', identity=safe_load_identity,
                         connection=load_connection,
                         input_state='OFF')

        for channel in (1, 2, 3):
            if channel == 1:
                token = 'CONFIRM H04P CONNECT CH1 ALL_OFF LOAD=OFF'
                _gate(token, 'Keithley CH1–CH3 OFF, KEL103 INPUT OFF. Подключите KEL103 '
                      'с правильной полярностью только к CH1; остальные каналы свободны.', io)
                evidence.add('operator_gate', token=token, action='connect', channel=1)
            else:
                previous = channel - 1
                token = f'CONFIRM H04P MOVE CH{previous}->CH{channel} ALL_OFF LOAD=OFF'
                _gate(token, f'Keithley CH1–CH3 OFF и KEL103 INPUT OFF. Отключите нагрузку '
                      f'от CH{previous}; проверьте отсутствие напряжения, подключите '
                      f'правильную полярность только к CH{channel}.', io)
                evidence.add('operator_gate', token=token, action='move',
                             from_channel=previous, to_channel=channel)

            control = _profile(access='control')
            dev.apply_profile(control)
            dev.verify_profile()
            evidence.add('profile', name=control.name, channel=channel,
                         voltage_cap_v=4.5, current_cap_a=.5,
                         initial_voltage_v=3.3, initial_current_a=.1, access='control')
            for field_values in (
                    _expect_local_rejection(dev, io, channel, voltage=4.51),
                    _expect_local_rejection(dev, io, channel, current=.501)):
                evidence.add('case', check='out-of-profile setpoint rejected before SCPI',
                             **field_values)

            # Three readings for each requested channel: shared 3.3 V baseline,
            # the user's per-channel value, and the exact 4.5 V profile boundary.
            for point, voltage, resistance in (
                    ('common_3.30V', 3.3, CV_LOADS[3.3]),
                    ('channel_value', CHANNEL_VOLTAGES[channel],
                     CV_LOADS[CHANNEL_VOLTAGES[channel]]),
                    ('profile_voltage_boundary', 4.5, CV_LOADS[4.5])):
                _measure_point(dev, io, evidence, load, channel, phase='control',
                               point=point, voltage=voltage, current=.1,
                               resistance=resistance, expected_mode='CV')

            # Exercise the programmed PSU current limit at three levels while
            # every requested value stays at or below the profile's 0.500 A cap.
            for current, resistance in CC_LOADS.items():
                _measure_point(dev, io, evidence, load, channel, phase='control',
                               point=f'current_limit_{current:.3f}A', voltage=3.0,
                               current=current, resistance=resistance,
                               expected_mode='CC')

            dev.emergency_off()
            fixed = _profile(access='power_only')
            dev.apply_profile(fixed)
            dev.verify_profile()
            before = io.commands
            for action in (lambda: dev.set_channel(channel, voltage=3.3),
                           lambda: dev.timer(channel, 1.0),
                           lambda: dev.remote(lock_panel=False)):
                try:
                    action()
                except SafetyError:
                    pass
                else:
                    raise SafetyError('power_only API modification was accepted')
            if io.commands != before:
                raise SafetyError('power_only API rejection reached SCPI')
            evidence.add('case', channel=channel,
                         check='power_only setpoint/timer/remote frozen before SCPI',
                         outcome='PASS')
            _measure_point(dev, io, evidence, load, channel, phase='power_only',
                           point='fixed_channel_preset',
                           voltage=CHANNEL_VOLTAGES[channel], current=.1,
                           resistance=47.0, expected_mode='CV', configure=False)
            dev.emergency_off()
            _all_off(io)

        token = 'CONFIRM H04P DISCONNECT CH3 ALL_OFF LOAD=OFF'
        _gate(token, 'Убедитесь, что все выходы Keithley OFF и KEL103 INPUT OFF; '
              'отсоедините нагрузку от CH3 и проверьте отсутствие напряжения.', io)
        evidence.add('operator_gate', token=token, action='disconnect', channel=3)
        io.write('SYST:LOC')
        outcome, reason = 'PASS', 'all 21 low-power CV/CC readings and profile guards passed'
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or isinstance(
            exc, (TimeoutError, KeyboardInterrupt, InterruptedError)) else 'FAIL')
        reason = f'H04-Powered stopped: {type(exc).__name__}: {exc}; verify all outputs/load OFF'
        evidence.add('stage_error', exception_type=type(exc).__name__,
                     message=str(exc), live_link=io.live)
        if io.live:
            try:
                dev.emergency_off()
                _all_off(io)
                if getattr(load, 'live', True):
                    load.set_input(False)
                io.write('SYST:LOC')
            except BaseException as cleanup_exc:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT/LOAD STATE'
                evidence.add('cleanup_error', exception_type=type(cleanup_exc).__name__)
    finally:
        try:
            transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'Keithley transport close failed; manual OFF required'
        try:
            load.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'KEL103 transport close failed; manual INPUT OFF required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware,
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED',
                     command_count=io.commands)
        print(f'{outcome}: {reason}\nПроверьте физически CH1/CH2/CH3 OFF и INPUT KEL103 OFF.')
    return outcome
