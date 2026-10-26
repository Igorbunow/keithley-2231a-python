"""Manually gated H03 CV/CC qualification for all three independent channels.

@file channel_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Exact-plan H03 stage; the electronic load remains operator controlled.
@details Importing this module does not access hardware. See docs/H03_PREFLIGHT.ru.md.
"""
from __future__ import annotations

import math
import ipaddress
import re
import socket
import time

from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError

TOKEN = ('CONFIRM H03 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
         'LAN=UDP:18190 RECALL=OFF VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')
RESUME_CH2_TOKEN = ('CONFIRM H03 RESUME_CH2 INDEPENDENT ALL_OFF '
                    'LOAD=KEL103 INPUT=OFF LAN=UDP:18190 VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')
RESUME_CH3_TOKEN = ('CONFIRM H03 RESUME_CH3 INDEPENDENT ALL_OFF '
                    'LOAD=KEL103 INPUT=OFF LAN=UDP:18190 VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')
FINAL_TOKEN = 'CONFIRM H03 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
MODES = ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')
OFF = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
STATUS_DIAGNOSTIC_QUERIES = (
    'INST:NSEL?', 'CHAN:OUTP?', *OFF,
    *(f'STAT:QUES:INST:ISUM{ch}:COND?' for ch in (1, 2, 3)),
    'STATus:OPERation:INSTrument:ISUMmary3:CONDition?',
    'STATus:QUEStionable:INSTrument:ISUMmary3:CONDition?',
    'STAT:OPER:INST:ISUM3:ENAB?', 'STAT:QUES:INST:ISUM3:ENAB?')


def approval_token(start_channel, load_transport='udp', load_host='192.168.22.106',
                   load_port=18190, load_serial_port='/dev/ttyACM0', diagnose_status=False):
    """Return the exact H03 approval bound to the selected load connection."""
    if diagnose_status and start_channel != 3:
        raise ValueError('status diagnosis is restricted to CH3')
    if load_transport == 'udp':
        connection = f'LAN=UDP:{load_port}'
    elif load_transport == 'serial':
        connection = f'USB={load_serial_port} BAUD=115200'
    else:
        raise ValueError('load transport must be udp or serial')
    if start_channel == 1:
        return ('CONFIRM H03 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
                f'{connection} RECALL=OFF VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')
    if start_channel not in (2, 3):
        raise ValueError('start channel must be 1, 2 or 3')
    action = 'DIAGNOSTIC_CH3' if diagnose_status else f'RESUME_CH{start_channel}'
    return (f'CONFIRM H03 {action} INDEPENDENT ALL_OFF '
            f'LOAD=KEL103 INPUT=OFF {connection} VCAP=4.00 ICAP=0.500 ERRBASE=CHECK')


class StatusWireTrace:
    """Observe serial status bytes without changing framing or logging identity bytes."""
    def __init__(self, serial_port, evidence):
        self.serial_port, self.evidence = serial_port, evidence
        self.status_command = None

    def __getattr__(self, name):
        return getattr(self.serial_port, name)

    def write(self, data):
        command = data.decode('ascii').strip()
        self.status_command = command if command.upper().startswith(('STAT:', 'STATUS:')) else None
        if self.status_command:
            self.evidence.add('status_wire_write', command=command, tx_hex=data.hex(' '))
        return self.serial_port.write(data)

    def readline(self, size):
        data = self.serial_port.readline(size)
        if self.status_command:
            self.evidence.add('status_wire_read', command=self.status_command, rx_hex=data.hex(' '))
        return data


class KEL103UDP:
    """Bounded KEL103 network adapter for H03 and H04-Powered load points.

    @details Uses the documented KEL103 UDP command endpoint (LF-terminated
             commands). Only qualification queries and approved CR/INPUT mutations are permitted.
             Setpoint changes require a remotely confirmed INPUT OFF state.
    """
    READ_COMMANDS = frozenset(('*IDN?', ':INP?', ':FUNC?', ':RES?',
                               ':MEAS:VOLT?', ':MEAS:CURR?', ':MEAS:POW?'))
    WRITE_COMMANDS = frozenset((':FUNC CR', ':INP ON', ':INP OFF',
                                ':RES 5OHM', ':RES 11OHM', ':RES 12OHM', ':RES 22OHM',
                                ':RES 40OHM', ':RES 47OHM', ':RES 56OHM'))
    H04P_RESISTANCES = (5.0, 11.0, 12.0, 22.0, 47.0, 56.0)
    VERIFIED_RESISTANCES = (5.0, 11.0, 12.0, 22.0, 40.0, 47.0, 56.0)

    def __init__(self, host, port=18190, timeout=1.5, sock=None, evidence=None):
        self.host = str(ipaddress.IPv4Address(host))
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid KEL103 UDP port')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('KEL103 timeout must be finite and positive')
        self.port, self.endpoint = port, (self.host, port)
        self.transport = 'UDP'
        self.timeout, self.evidence = timeout, evidence
        if sock is None:
            route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                route.connect(self.endpoint)
                local_ip = route.getsockname()[0]
            finally:
                route.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((local_ip, port))
            except BaseException:
                sock.close()
                raise
        self.sock = sock
        self.sock.settimeout(timeout)
        self.live = True
        self.verified_resistance = None

    def _event(self, event, **values):
        if self.evidence is not None:
            self.evidence.add(event, **values)

    def query(self, command):
        """Send an allow-listed query and require a reply from the target."""
        if not self.live:
            raise OSError('KEL103 link uncertain; no further UDP commands')
        if command not in self.READ_COMMANDS:
            raise ValueError(f'KEL103 query is not permitted in H03: {command}')
        self._event('kel103_query', command=command)
        try:
            self.sock.sendto((command + '\n').encode('ascii'), self.endpoint)
            data, peer = self.sock.recvfrom(4096)
            if peer != self.endpoint:
                raise OSError(f'unexpected KEL103 UDP peer: {peer!r}')
            response = data.decode('ascii').strip()
            if not response:
                raise OSError(f'empty KEL103 response to {command}')
        except BaseException as exc:
            self.live = False
            self._event('kel103_io_error', command=command,
                        exception_type=type(exc).__name__)
            raise
        safe_response = re.sub(r'(?i)(SN\s*:\s*)[^,\s]+', r'\1[REDACTED]', response)
        self._event('kel103_read', command=command, response=safe_response)
        print(f'KEL103 {command}: {safe_response}')
        return response

    def write(self, command):
        """Send one H03-specific mutation; callers must verify it by query."""
        if not self.live:
            raise OSError('KEL103 link uncertain; no further UDP commands')
        if command not in self.WRITE_COMMANDS:
            raise ValueError(f'KEL103 write is not permitted in H03: {command}')
        self._event('kel103_write', command=command)
        try:
            self.sock.sendto((command + '\n').encode('ascii'), self.endpoint)
        except BaseException as exc:
            self.live = False
            self._event('kel103_io_error', command=command,
                        exception_type=type(exc).__name__)
            raise

    def identify(self):
        """Require the expected load family before any remote mutation."""
        identity = self.query('*IDN?')
        if 'KEL103' not in identity.upper():
            raise SafetyError('unexpected electronic-load identity')
        return identity

    def input_state(self):
        state = self.query(':INP?').upper()
        if state not in ('ON', 'OFF'):
            raise SafetyError(f'invalid KEL103 input state: {state}')
        return state

    @staticmethod
    def _quantity(response, unit):
        text = response.strip()
        if text.upper().endswith(unit):
            text = text[:-len(unit)].strip()
        value = float(text)
        if not math.isfinite(value) or value < 0:
            raise SafetyError(f'invalid KEL103 measurement: {response}')
        return value

    def configure_cr(self, resistance_ohm):
        """Select CR 12/40 Ω only while INPUT is confirmed OFF and verify it."""
        if resistance_ohm not in (12.0, 40.0):
            raise ValueError('H03 permits only CR 12.0 Ω and 40.0 Ω')
        if self.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF before changing CR settings')
        self.write(':FUNC CR')
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 did not enter CR mode')
        command = f':RES {resistance_ohm:g}OHM'
        self.write(command)
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 left CR mode while setting resistance')
        actual = self._quantity(self.query(':RES?'), 'OHM')
        if abs(actual - resistance_ohm) > 0.001:
            raise SafetyError(f'KEL103 CR readback mismatch: {actual} Ω')
        self.verified_resistance = resistance_ohm
        return actual

    def configure_h04p_cr(self, resistance_ohm):
        """Select one bounded H04-Powered resistance while INPUT is confirmed OFF."""
        if resistance_ohm not in self.H04P_RESISTANCES:
            raise ValueError('H04-Powered CR value is not allow-listed')
        if self.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF before changing CR settings')
        self.write(':FUNC CR')
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 did not enter CR mode')
        self.write(f':RES {resistance_ohm:g}OHM')
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 left CR mode while setting resistance')
        actual = self._quantity(self.query(':RES?'), 'OHM')
        if abs(actual - resistance_ohm) > 0.001:
            raise SafetyError(f'KEL103 CR readback mismatch: {actual} Ω')
        self.verified_resistance = resistance_ohm
        return actual

    def set_input(self, enabled):
        """Set and verify the load input; ON requires verified H03 CR setup."""
        if type(enabled) is not bool:
            raise TypeError('enabled must be bool')
        if enabled:
            if self.verified_resistance not in self.VERIFIED_RESISTANCES:
                raise SafetyError('KEL103 CR setpoint must be verified before INPUT ON')
            if self.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT is already ON unexpectedly')
        self.write(':INP ON' if enabled else ':INP OFF')
        expected = 'ON' if enabled else 'OFF'
        if self.input_state() != expected:
            raise SafetyError(f'KEL103 INPUT did not verify {expected}')

    def measurements(self):
        """Read the three independent KEL103 live measurement values."""
        return {
            'kel103_voltage_v': self._quantity(self.query(':MEAS:VOLT?'), 'V'),
            'kel103_current_a': self._quantity(self.query(':MEAS:CURR?'), 'A'),
            'kel103_power_w': self._quantity(self.query(':MEAS:POW?'), 'W'),
        }

    def close(self):
        self.sock.close()


class KEL103Serial(KEL103UDP):
    """Bounded H03 adapter for the KEL103 USB CDC serial interface.

    @details USB serial command framing and the 115200 baud line setting are
             community-documented and require physical confirmation on this unit.
             This adapter never performs automatic fallback from UDP.
    """
    def __init__(self, port='/dev/ttyACM0', timeout=1.5, serial_port=None,
                 evidence=None):
        if not isinstance(port, str) or not port:
            raise ValueError('KEL103 serial port must be a non-empty path')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('KEL103 timeout must be finite and positive')
        if serial_port is None:
            import serial
            import os
            from serial.tools import list_ports
            matching = [item for item in list_ports.comports()
                        if item.device == port and item.vid == 0x0416
                        and item.pid == 0x5011]
            if len(matching) != 1:
                raise OSError('KEL103 USB adapter identity mismatch; expected 0416:5011')
            serial_port = serial.Serial(
                port=port, baudrate=115200, bytesize=8, parity='N', stopbits=1,
                timeout=timeout, write_timeout=timeout, xonxoff=False,
                rtscts=False, dsrdtr=False,
                **({'exclusive': True} if os.name == 'posix' else {}))
        self.port, self.timeout, self.evidence = port, timeout, evidence
        self.transport = 'USB serial (community protocol)'
        self.sock = serial_port
        self.live = True
        self.verified_resistance = None

    def query(self, command):
        if not self.live:
            raise OSError('KEL103 link uncertain; no further serial commands')
        if command not in self.READ_COMMANDS:
            raise ValueError(f'KEL103 query is not permitted in H03: {command}')
        self._event('kel103_query', command=command)
        try:
            if self.sock.in_waiting:
                raise OSError('unsolicited/stale KEL103 serial input')
            payload = (command + '\n').encode('ascii')
            if self.sock.write(payload) != len(payload):
                raise OSError('short KEL103 serial write')
            self.sock.flush()
            data = self.sock.readline(4097)
            if not data.endswith(b'\n') or len(data) > 4096:
                raise OSError('incomplete/oversized KEL103 serial response')
            response = data.decode('ascii').strip()
            if not response:
                raise OSError('empty KEL103 serial response')
        except BaseException as exc:
            self.live = False
            self._event('kel103_io_error', command=command,
                        exception_type=type(exc).__name__)
            raise
        safe_response = re.sub(r'(?i)(SN\s*:\s*)[^,\s]+', r'\1[REDACTED]', response)
        self._event('kel103_read', command=command, response=safe_response)
        print(f'KEL103 {command}: {safe_response}')
        return response

    def write(self, command):
        if not self.live:
            raise OSError('KEL103 link uncertain; no further serial commands')
        if command not in self.WRITE_COMMANDS:
            raise ValueError(f'KEL103 write is not permitted in H03: {command}')
        self._event('kel103_write', command=command)
        try:
            payload = (command + '\n').encode('ascii')
            if self.sock.write(payload) != len(payload):
                raise OSError('short KEL103 serial write')
            self.sock.flush()
        except BaseException as exc:
            self.live = False
            self._event('kel103_io_error', command=command,
                        exception_type=type(exc).__name__)
            raise


def profile():
    """Conservative 4 V / 0.5 A caps; outputs remain OFF after application."""
    return Profile('H03 CV CC qualification', tuple(
        ChannelProfile(4.0, 0.5, 4.0, 0.5, expected_regulation='auto')
        for _ in (1, 2, 3)), wiring='independent', lock_front_panel=False,
        verify_every_s=3600.0)


def command_plan(start_channel=1, diagnose_status=False):
    """Return the exact successful SCPI trace for this library version.

    Every write except LOC is followed by an error queue read by StageIO. The
    sequence mirrors apply_profile(), verify_profile(), set_channel(), output(),
    and read_channel(); drift is rejected before transmission.
    """
    if start_channel not in (1, 2, 3):
        raise ValueError('start_channel must be 1, 2 or 3')
    if diagnose_status and start_channel != 3:
        raise ValueError('status diagnosis is restricted to CH3')
    plan = []
    def add(*commands):
        for command in commands:
            plan.append(command)
            if command not in ('SYST:ERR?', 'SYST:LOC'):
                plan.append('SYST:ERR?')
    def select(ch): add(f'INST:NSEL {ch}')
    def status(): add(*OFF)
    def check_setpoints(ch): select(ch); add('VOLT?', 'CURR?')
    def wiring(): add(*MODES)
    def verify():
        wiring(); status()
        for ch in (1, 2, 3):
            select(ch); add('VOLT:LIM:STAT?', 'VOLT:LIM?')
            check_setpoints(ch)
    # Public apply_profile() success trace.
    plan.append('SYST:ERR?')  # Attribute any queued error before the first mutation.
    add('SYST:REM', '*IDN?', 'OUTP 0', *OFF,
        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0')
    for ch in (1, 2, 3):
        select(ch)
        add('OUTP:TIM:STAT 0', 'OUTP:TIM:STAT?', 'VOLT 0', 'CURR 0',
            'VOLT:LIM 4.00', 'VOLT:LIM:STAT 1', 'VOLT:LIM:STAT?', 'VOLT:LIM?',
            'CURR 0.500', 'VOLT 4.00')
        check_setpoints(ch)
    wiring(); status()
    for ch in range(start_channel, 4):
        for mode in ('CV', 'CC'):
            if mode == 'CC':
                verify()
                select(ch); add('CURR 0.250')
                check_setpoints(ch)
                # set_channel() performs one final readback for each changed
                # setpoint; CC changes current only.
                select(ch); add('CURR?')
            verify()
            select(ch); add('CHAN:OUTP 1', 'CHAN:OUTP?')
            status()
            # read_channel(): selected output, U/I, setpoints and status bits.
            select(ch); add('CHAN:OUTP?', 'MEAS:VOLT?', 'MEAS:CURR?', 'VOLT?',
                            'CURR?', f'STAT:OPER:INST:ISUM{ch}:COND?',
                            f'STAT:QUES:INST:ISUM{ch}:COND?', 'MEAS:POW?',
                            f'STAT:OPER:INST:ISUM{ch}:COND?',
                            f'STAT:QUES:INST:ISUM{ch}:COND?')
            if diagnose_status:
                # Early sample, followed by independent display confirmation.
                add('INST:NSEL 3', *STATUS_DIAGNOSTIC_QUERIES)
                # A late sample at >=5 s, then repeat with CH1 selected.
                add('INST:NSEL 3', *STATUS_DIAGNOSTIC_QUERIES)
                add('INST:NSEL 1', *STATUS_DIAGNOSTIC_QUERIES)
                add('INST:NSEL 3', 'INST:NSEL?')
            select(ch); add('CHAN:OUTP 0', 'CHAN:OUTP?')
            status()
    # emergency_off() verifies all channels, then h03() performs its final
    # independent all-off check before returning local control.
    add('SYST:REM', 'OUTP 0', *OFF, *OFF, 'INST:NSEL 1', 'SYST:LOC')
    return tuple(plan)


class StageIO:
    """Exact success-path guard and error-queue check for H03."""
    def __init__(self, transport, evidence, start_channel=1, diagnose_status=False):
        self.transport, self.evidence = transport, evidence
        self.plan, self.index = command_plan(start_channel, diagnose_status), 0
        self.live, self.cleanup = True, False
        self.selected = 1
        self.authorized_channel = None
        self.started = time.monotonic()

    def _transfer(self, command, is_query):
        if not self.live:
            raise OSError('uncertain link; no further SCPI')
        if not is_query:
            if command == 'OUTP 1':
                raise ValueError('global output ON is forbidden in H03')
            if command == 'CHAN:OUTP 1' and self.authorized_channel != self.selected:
                raise ValueError('individual ON requires a matching confirmed operator gate')
        if self.cleanup:
            allowed = ('SYST:REM', 'OUTP 0', 'SYST:ERR?', *OFF, 'SYST:LOC',
                       'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3', 'CHAN:OUTP 0')
            if command not in allowed:
                raise ValueError('only shutdown is permitted after H03 failure')
        else:
            if time.monotonic() - self.started > 600:
                raise TimeoutError('H03 10-minute stage budget exceeded')
            if self.index >= len(self.plan) or command != self.plan[self.index]:
                raise ValueError(f'SCPI differs from approved H03 plan at step {self.index + 1}: {command}')
            self.index += 1
        self.evidence.add('query' if is_query else 'write', command=command)
        try:
            response = self.transport.query(command) if is_query else self.transport.write(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__,
                              cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
            raise
        if is_query:
            units = {'MEAS:VOLT?': 'V', 'MEAS:CURR?': 'A', 'MEAS:POW?': 'W',
                     'VOLT?': 'V', 'VOLT:LIM?': 'V', 'CURR?': 'A'}
            record = self.evidence.add('read', command=command, response=response,
                                       units=units.get(command))
            print(f'{command}: {record["response"]}')
            if command != 'SYST:ERR?':
                self._check_error_queue()
            return response
        if command.startswith('INST:NSEL '):
            self.selected = int(command.rsplit(' ', 1)[1])
        if command != 'SYST:LOC':
            self._check_error_queue(command)
        return response

    def _check_error_queue(self, command='query'):
        error = self._transfer('SYST:ERR?', True)
        code, message = error.split(',', 1)
        if int(code.strip()) != 0 or not message.strip():
            raise ValueError(f'instrument error after {command}: {error}')

    def query(self, command): return self._transfer(command, True)
    def write(self, command): self._transfer(command, False)


def guarded_profile(): return profile()


def _gate(token, prompt):
    print(prompt)
    while True:
        try: answer = input(f'Подтверждение: {token}\n> ').strip()
        except (EOFError, KeyboardInterrupt): raise InterruptedError('operator gate interrupted')
        if answer == token:
            return
        print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')


def _all_off(io):
    for command in OFF:
        raw = io.query(command)
        value = float(raw)
        if not math.isfinite(value) or value != int(value) or not 0 <= value <= 255 or int(value) & 8:
            raise SafetyError(f'CH status not OFF: {command}={raw}')


def _only_target_on(io, target):
    """Confirm operation-register ON bits while the one approved channel runs."""
    for ch, command in enumerate(OFF, 1):
        raw = io.query(command)
        value = float(raw)
        if not math.isfinite(value) or value != int(value) or not 0 <= value <= 255:
            raise SafetyError(f'invalid CH{ch} operation status: {raw}')
        if bool(int(value) & 8) != (ch == target):
            raise SafetyError(f'expected only CH{target} ON; CH{ch} status={raw}')


def _display_gate(evidence, channel, mode, readings):
    """Require independent panel confirmation against the network readbacks."""
    token = f'CONFIRM H03 DISPLAY CH{channel} PSU={mode} KEL=CR INPUT=ON'
    _gate(token, f"Проверьте на Keithley режим {mode}; на KEL103 — CR, INPUT ON. "
          f"Сверьте дисплей KEL103 с сетевыми показаниями "
          f"{readings['kel103_voltage_v']:.4f} V / {readings['kel103_current_a']:.4f} A / "
          f"{readings['kel103_power_w']:.4f} W. При любом расхождении не подтверждайте.")
    evidence.add('operator_display_confirmation', token=token, channel=channel,
                 keithley_panel_mode=mode, kel103_panel_mode='CR', kel103_panel_input='ON',
                 kel103_display_matches_remote=True)


def _status_snapshot(io, evidence, mode, phase, selected_channel, output_started):
    """Record direct status replies; the library regulation decoder is bypassed."""
    io.write(f'INST:NSEL {selected_channel}')
    responses = {command: io.query(command) for command in STATUS_DIAGNOSTIC_QUERIES}
    values = {command: float(answer) for command, answer in responses.items()}
    evidence.add('status_diagnostic', channel=3, mode=mode, phase=phase,
                 selected_channel=selected_channel,
                 elapsed_since_output_on_s=time.monotonic() - output_started,
                 responses=responses)
    if values['INST:NSEL?'] != selected_channel:
        raise SafetyError('status diagnosis channel selection did not verify')
    if values['CHAN:OUTP?'] != int(selected_channel == 3):
        raise SafetyError('selected-channel output disagrees with status diagnosis')
    if any(not math.isfinite(value) or value != int(value) or not 0 <= value <= 255
           for value in values.values()):
        raise SafetyError('invalid status diagnosis register response')
    for channel, command in enumerate(OFF, 1):
        if bool(int(values[command]) & 8) != (channel == 3):
            raise SafetyError('status diagnosis expected only CH3 ON')


def h03(transport, evidence, load, start_channel=1, diagnose_status=False):
    """Run H03 once; operator wires and confirms panels, UDP controls KEL103."""
    if start_channel not in (1, 2, 3):
        raise ValueError('start_channel must be 1, 2 or 3')
    io = StageIO(transport, evidence, start_channel, diagnose_status)
    dev = Keithley2231A(io)
    outcome, reason = 'ABORTED', 'incomplete H03'
    status_unqualified = False
    try:
        load.identify()
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF at H03 start')
        load_transport_name = getattr(load, 'transport', type(load).__name__)
        evidence.add('kel103_preflight', connection=load.port,
                     transport=load_transport_name, input_state='OFF')
        baseline_error = io.query('SYST:ERR?')
        try:
            error_code, error_message = baseline_error.split(',', 1)
            clean_queue = int(error_code.strip()) == 0 and bool(error_message.strip())
        except (ValueError, TypeError):
            clean_queue = False
        evidence.add('instrument_error_queue_baseline', response=baseline_error,
                     clean=clean_queue)
        if not clean_queue:
            raise ValueError(f'pre-existing instrument error before H03: {baseline_error}')
        dev.apply_profile(guarded_profile())
        if not dev.armed or dev.faulted or dev.profile.wiring != 'independent':
            raise SafetyError('H03 profile not armed in independent mode')
        evidence.add('profile', name='H03 CV CC qualification', voltage_cap_v=4.0,
                     current_cap_a=0.5, initial_voltage_v=4.0, initial_current_a=0.5,
                     load_model='KORAD KEL103')
        print(f'Профиль готов, все выходы должны быть OFF. KEL103 CR/INPUT '
              f'управляются через {load_transport_name} только после подтверждения '
              'подключения и PSU OFF.')
        # A CH2 resume starts with the load still on CH1 and needs a move gate.
        # A CH3 resume is used after the prior CH3 point stopped safely, so the
        # load is already on CH3 and only the per-point connection gate applies.
        previous_channel = 1 if start_channel == 2 else None
        for channel in range(start_channel, 4):
            if previous_channel is not None:
                move = f'CONFIRM H03 MOVE CH{previous_channel}->CH{channel} ALL_OFF LOAD=OFF'
                _gate(move, f'Keithley и KEL103 OFF; отключите нагрузку от CH{previous_channel}, '
                      f'проверьте отсутствие напряжения и подключите полярность только CH{channel}.')
                evidence.add('operator_gate', token=move, action='load relocation',
                             from_channel=previous_channel, to_channel=channel)
            for mode, resistance in (('CV', '40.0'), ('CC', '12.0')):
                if mode == 'CC':
                    # Change only the target-channel current setpoint while OFF.
                    dev.set_channel(channel, current=0.250)
                token = f'CONFIRM H03 CH{channel} {mode} CR={resistance} CONNECTED PSU=OFF'
                _gate(token, f'CH{channel}: Keithley OFF; INPUT KEL103 OFF. Подключите '
                      f'KEL103 только к CH{channel} (+ к +, − к −), остальные каналы '
                      f'оставьте неподключёнными. Runner проверит и задаст CR {resistance} Ω, '
                      'затем включит INPUT; источник останется OFF до отдельной команды.')
                evidence.add('operator_gate', token=token, channel=channel, mode=mode,
                             connection=f'KEL103 to CH{channel}; correct polarity',
                             other_channels='OFF', psu_output='OFF')
                load.configure_cr(float(resistance))
                load.set_input(True)
                evidence.add('kel103_setup_verified', channel=channel, mode=mode,
                             resistance_ohm=float(resistance), input_state='ON')
                io.authorized_channel = channel
                dev.output(channel, True)
                output_started = time.monotonic()
                _only_target_on(io, channel)
                sample = dev.read_channel(channel)
                if not sample.enabled:
                    raise SafetyError(f'CH{channel} unexpectedly OFF during {mode} point')
                if abs(sample.power_w - sample.voltage_v * sample.current_a) > 1e-9:
                    raise SafetyError('library power arithmetic mismatch')
                # Explicit power query is in the Keithley manual; retain actual reply.
                scpi_power = float(io.query('MEAS:POW?'))
                if not math.isfinite(scpi_power) or scpi_power < 0:
                    raise SafetyError('invalid MEAS:POW? response')
                raw_status = int(float(io.query(f'STAT:OPER:INST:ISUM{channel}:COND?')))
                questionable_status = int(float(io.query(
                    f'STAT:QUES:INST:ISUM{channel}:COND?')))
                if diagnose_status:
                    _status_snapshot(io, evidence, mode, 'early', 3, output_started)
                observed = load.measurements()
                # Worst published KEL103 measurement range is used because the
                # selected range is not remotely observable in this runner.
                voltage_tolerance_v = 0.06
                current_tolerance_a = 0.03
                power_tolerance_w = 0.31
                evidence.add('measurement', channel=channel, mode=mode,
                             keithley_voltage_v=sample.voltage_v,
                             keithley_current_a=sample.current_a,
                             keithley_power_api_w=sample.power_w,
                             keithley_power_scpi_w=scpi_power,
                             keithley_status_mask=raw_status,
                             keithley_questionable_status_mask=questionable_status,
                             voltage_difference_max_v=voltage_tolerance_v,
                             current_difference_max_a=current_tolerance_a,
                             power_difference_max_w=power_tolerance_w, **observed)
                if abs(sample.voltage_v - observed['kel103_voltage_v']) > voltage_tolerance_v:
                    raise SafetyError('Keithley/KEL103 voltage disagreement exceeds 0.06 V')
                if abs(sample.current_a - observed['kel103_current_a']) > current_tolerance_a:
                    raise SafetyError('Keithley/KEL103 current disagreement exceeds 0.03 A')
                if abs(observed['kel103_power_w'] -
                       observed['kel103_voltage_v'] * observed['kel103_current_a']) > power_tolerance_w:
                    raise SafetyError('KEL103 power differs from V*I by more than its 300 W full-scale tolerance')
                if abs(scpi_power - sample.power_w) > power_tolerance_w:
                    raise SafetyError('MEAS:POW? differs from measured U*I by more than 0.31 W')
                # In a diagnostic status mismatch, preserve independent panel
                # evidence before failing the point and shutting down.
                _display_gate(evidence, channel, mode, observed)
                if diagnose_status:
                    # Directed second sample, not a retry of a failed command.
                    remaining = 5.0 - (time.monotonic() - output_started)
                    if remaining > 0:
                        time.sleep(remaining)
                    _status_snapshot(io, evidence, mode, 'after_display', 3, output_started)
                    _status_snapshot(io, evidence, mode, 'selected_other_channel', 1, output_started)
                    io.write('INST:NSEL 3')
                    if int(io.query('INST:NSEL?')) != 3:
                        raise SafetyError('CH3 selection restore did not verify')
                operation_mode = ('CV' if raw_status & 1 and not raw_status & 2 else
                                  'CC' if raw_status & 2 and not raw_status & 1 else 'UNKNOWN')
                questionable_mode = ('CV' if questionable_status & 1 and not questionable_status & 2 else
                                     'CC' if questionable_status & 2 and not questionable_status & 1 else 'UNKNOWN')
                if questionable_mode != mode:
                    raise SafetyError(
                        f'CH{channel} {mode} questionable status mismatch: '
                        f'operation={raw_status}, questionable={questionable_status}')
                if (not raw_status & 8 or operation_mode != mode or
                        sample.regulation != mode):
                    status_unqualified = True
                    evidence.add('status_unqualified', channel=channel, mode=mode,
                                 library_regulation=sample.regulation,
                                 operation_status=raw_status,
                                 operation_mode=operation_mode,
                                 questionable_status=questionable_status,
                                 questionable_mode=questionable_mode,
                                 reason='operation status did not confirm the panel/load-verified mode')
                dev.output(channel, False)
                io.authorized_channel = None
                _all_off(io)
                load.set_input(False)
                off_token = f'CONFIRM H03 CH{channel} {mode} PSU=OFF LOAD=OFF'
                _gate(off_token, f'Keithley и KEL103 INPUT опрошены и подтверждены как OFF. '
                      'Проверьте оба дисплея и подтвердите OFF перед следующей точкой.')
                evidence.add('operator_gate', token=off_token, channel=channel,
                             mode=mode, psu_output='OFF', load_input='OFF')
            previous_channel = channel
        dev.emergency_off()
        _all_off(io)
        io.write('INST:NSEL 1')
        io.write('SYST:LOC')
        if io.index != len(io.plan):
            raise ValueError(f'incomplete H03 command plan: {io.index}/{len(io.plan)}')
        cases = (4 if start_channel == 2 else 2 if start_channel == 3 else 6)
        if status_unqualified:
            outcome, reason = ('PARTIAL', f'{cases} CV/CC cases completed; operation status '
                               'did not qualify every mode; review evidence')
        else:
            outcome, reason = 'PASS', f'{cases} CV/CC cases and all OFF checks completed; review evidence'
    except BaseException as exc:
        outcome = 'ABORTED' if not io.live or isinstance(exc, (InterruptedError, KeyboardInterrupt, TimeoutError)) else 'FAIL'
        reason = f'H03 stopped: {type(exc).__name__}: {exc}; manual OFF verification required'
        evidence.add('stage_error', exception_type=type(exc).__name__,
                     message=str(exc), live_link=io.live)
        if io.live:
            io.cleanup = True
            try:
                dev.emergency_off()
                if load.live:
                    load.set_input(False)
                io.write('SYST:LOC')
            except BaseException as cleanup_exc:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT STATE'
                evidence.add('cleanup_error', exception_type=type(cleanup_exc).__name__)
        else:
            reason = 'I/O failure; UNKNOWN OUTPUT STATE; no retry or reconnect'
    finally:
        try: transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'transport close failed; manual OFF check required'
        try: load.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'KEL103 transport close failed; manual OFF check required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware,
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
        print(f'{outcome}: {reason}\nПроверьте физически CH1/CH2/CH3 OFF и INPUT KEL103 OFF.')
    return outcome
