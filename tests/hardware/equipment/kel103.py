"""Qualification-only KEL103 driver: shared policy, serial and UDP transports.

One command path handles validation, evidence, failure invalidation and readback.
No I/O on import, reconnect or protocol fallback.
"""
from __future__ import annotations

import ipaddress
import math
import re
import socket

from keithley2231a import SafetyError


class _KEL103Core:
    READ_COMMANDS = frozenset(('*IDN?', ':INP?', ':FUNC?', ':RES?',
                               ':MEAS:VOLT?', ':MEAS:CURR?', ':MEAS:POW?'))
    WRITE_COMMANDS = frozenset((':FUNC CR', ':INP ON', ':INP OFF',
                                ':RES 0.5OHM', ':RES 5OHM', ':RES 11OHM', ':RES 12OHM', ':RES 22OHM',
                                ':RES 30OHM', ':RES 40OHM', ':RES 47OHM', ':RES 56OHM',
                                ':RES 1600OHM', ':RES 2000OHM'))
    VERIFIED_RESISTANCES = (0.5, 5.0, 11.0, 12.0, 22.0, 30.0, 40.0, 47.0, 56.0,
                            1600.0, 2000.0)


    def _init_state(self, evidence):
        self.evidence = evidence
        self.live = True
        self.verified_resistance = None

    def _event(self, event, **values):
        if self.evidence is not None:
            self.evidence.add(event, **values)

    def _transfer(self, command, *, query):
        if not self.live:
            raise OSError('KEL103 link uncertain; no further commands')
        if command not in (self.READ_COMMANDS if query else self.WRITE_COMMANDS):
            raise ValueError(f'KEL103 command outside qualified scope: {command}')
        self._event('kel103_query' if query else 'kel103_write', command=command)
        try:
            if query:
                response = self._request(command)
                if not response:
                    raise OSError(f'empty KEL103 response to {command}')
            else:
                self._send(command)
                return None
        except BaseException as exc:
            self.live = False
            self._event('kel103_io_error', command=command,
                        exception_type=type(exc).__name__)
            raise
        safe = re.sub(r'(?i)(SN\s*:\s*)[^,\s]+', r'\1[REDACTED]', response)
        self._event('kel103_read', command=command, response=safe)
        print(f'KEL103 {command}: {safe}')
        return response

    def query(self, command):
        return self._transfer(command, query=True)

    def write(self, command):
        self._transfer(command, query=False)

    def identify(self):
        """Require the expected load family before any remote mutation."""
        identity = self.query('*IDN?')
        if 'KEL103' not in identity.upper():
            raise SafetyError('unexpected electronic-load identity')
        return identity

    def _invalid_reply(self, command):
        # An unexpected/malformed reply can desynchronize the USB/UDP stream.
        # Never issue another command on that connection, including INPUT OFF;
        # the other instrument must still be shut down independently.
        self.live = False
        self._event('kel103_invalid_reply', command=command,
                    classification='UNKNOWN LOAD INPUT STATE; no follow-up I/O')
        raise SafetyError(f'Invalid KEL103 reply to {command}; '
                          'UNKNOWN LOAD INPUT STATE (no retry; manual OFF required)')

    def input_state(self):
        state = self.query(':INP?').upper()
        if state not in ('ON', 'OFF'):
            self._invalid_reply(':INP?')
        return state

    def _quantity(self, response, unit, command):
        try:
            text = response.strip()
            if text.upper().endswith(unit):
                text = text[:-len(unit)].strip()
            value = float(text)
            if not math.isfinite(value) or value < 0:
                raise ValueError('non-finite or negative numeric reply')
        except (ValueError, AttributeError, TypeError, OverflowError):
            self._invalid_reply(command)
        return value

    def configure_cr(self, resistance_ohm):
        """Verify one previously reviewed resistance; INPUT must remain OFF."""
        if type(resistance_ohm) not in (float, int) or not math.isfinite(resistance_ohm):
            raise ValueError('KEL103 resistance must be finite')
        if resistance_ohm not in self.VERIFIED_RESISTANCES:
            raise ValueError('KEL103 resistance not approved for qualification')
        if self.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF before changing CR settings')
        self.write(':FUNC CR')
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 did not enter CR mode')
        self.write(f':RES {resistance_ohm:g}OHM')
        if self.query(':FUNC?').upper() != 'CR':
            raise SafetyError('KEL103 left CR mode while setting resistance')
        actual = self._quantity(self.query(':RES?'), 'OHM', ':RES?')
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
            'kel103_voltage_v': self._quantity(self.query(':MEAS:VOLT?'), 'V', ':MEAS:VOLT?'),
            'kel103_current_a': self._quantity(self.query(':MEAS:CURR?'), 'A', ':MEAS:CURR?'),
            'kel103_power_w': self._quantity(self.query(':MEAS:POW?'), 'W', ':MEAS:POW?'),
        }


    def close(self):
        self.sock.close()


class KEL103UDP(_KEL103Core):
    """KEL103 bounded UDP protocol adapter, using the configured endpoint only."""
    def __init__(self, host, port, timeout=1.5, sock=None, evidence=None):
        self.host = str(ipaddress.IPv4Address(host))
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid KEL103 UDP port')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('invalid KEL103 timeout')
        self.port, self.endpoint = port, (self.host, port)
        self.transport, self.timeout = 'UDP', timeout
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
        self._init_state(evidence)

    def _send(self, command):
        self.sock.sendto((command + '\n').encode('ascii'), self.endpoint)

    def _request(self, command):
        self._send(command)
        data, peer = self.sock.recvfrom(4096)
        if peer != self.endpoint:
            raise OSError(f'unexpected KEL103 UDP peer: {peer!r}')
        return data.decode('ascii').strip()


class KEL103Serial(_KEL103Core):
    """KEL103 USB serial adapter; VID/PID checked before opening a physical port."""
    def __init__(self, port, timeout=1.5, serial_port=None,
                 evidence=None, *, expected_vid, expected_pid, baudrate):
        if not isinstance(port, str) or not port:
            raise ValueError('KEL103 serial port must be a non-empty path')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('KEL103 timeout must be finite and positive')
        if serial_port is None:
            import serial
            import os
            from serial.tools import list_ports
            matching = [item for item in list_ports.comports()
                        if item.device == port and item.vid == expected_vid
                        and item.pid == expected_pid]
            if len(matching) != 1:
                raise OSError(f'KEL103 USB adapter identity mismatch; expected {expected_vid:04X}:{expected_pid:04X}')
            serial_port = serial.Serial(
                port=port, baudrate=baudrate, bytesize=8, parity='N', stopbits=1,
                timeout=timeout, write_timeout=timeout, xonxoff=False,
                rtscts=False, dsrdtr=False,
                **({'exclusive': True} if os.name == 'posix' else {}))
        self.port, self.timeout = port, timeout
        self.transport = 'USB serial (community protocol)'
        self.sock = serial_port
        self._init_state(evidence)

    def _send(self, command):
        payload = (command + '\n').encode('ascii')
        if self.sock.write(payload) != len(payload):
            raise OSError('short KEL103 serial write')
        self.sock.flush()

    def _request(self, command):
        if self.sock.in_waiting:
            raise OSError('unsolicited/stale KEL103 serial input')
        self._send(command)
        data = self.sock.readline(4097)
        if not data.endswith(b'\n') or len(data) > 4096:
            raise OSError('incomplete/oversized KEL103 serial response')
        return data.decode('ascii').strip()
