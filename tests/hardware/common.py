"""Shared qualification policies and checks; no import-time instrument access.

@file common.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Reuse driver diagnostics, evidence, budgets and state checks in all stages.
@details Stage subclasses define allowed commands and wiring-specific policies.
         The transport supplied by the driver rejects sends outside its dispatch.
"""
from __future__ import annotations

import math
import re
import time
import builtins
import select
import sys

from keithley2231a import SafetyError


OFF_QUERIES = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
UNITS = {'VOLT?': 'V', 'VOLT:LIM?': 'V', 'CURR?': 'A',
         'MEAS:VOLT?': 'V', 'MEAS:CURR?': 'A', 'MEAS:POW?': 'W',
         'OUTP:TIM:DEL?': 's'}


class StageAdapter:
    """Common command scope, bounded execution and error checking for all stages."""
    READS = frozenset()
    WRITES = frozenset()
    MAX_COMMANDS = 2000
    SHUTDOWN_RESERVE = 128
    MAX_SECONDS = 300
    _CLEANUP_QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|OUTP:SER\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'STAT:OPER:INST:ISUM[123]:COND\?)$')
    _CLEANUP_WRITE = re.compile(r'^(?:SYST:(?:REM|LOC)|OUTP 0|CHAN:OUTP 0|INST:NSEL [123])$')

    def __init__(self, transport, evidence):
        self.transport, self.evidence = transport, evidence
        self.live, self.cleanup = True, False
        self.commands = 0
        self.started = time.monotonic()
        self.selected = 1
        self.authorized_channel = None
        self.outputs = {ch: False for ch in (1, 2, 3)}
        self.expected_error = None
        self.last_error_response = None  # Most recent auto-checked SCPI error queue reply.

    def operator_input(self, prompt):
        """Exclude operator response time from the instrument execution deadline."""
        started = time.monotonic()
        try: return input(prompt)
        finally: self.started += time.monotonic() - started

    def _validate(self, command, query):
        # The core driver's startup/shutdown logic must distinguish a Series
        # alias before treating CH1/CH2 as independently selectable outputs.
        if query and command == 'OUTP:SER?':
            return
        pattern = getattr(self, '_QUERY' if query else '_WRITE', None)
        allowed = pattern.fullmatch(command) if pattern is not None else command in (
            self.READS if query else self.WRITES)
        if not allowed: raise ValueError(f'Command outside approved stage scope: {command}')

    def _after_write(self, command):
        if command.startswith('INST:NSEL '): self.selected = int(command[-1])
        elif command == 'OUTP 0': self.outputs = {ch: False for ch in (1, 2, 3)}
        elif command == 'OUTP 1': self.outputs = {ch: True for ch in (1, 2, 3)}
        elif command.startswith('CHAN:OUTP '): self.outputs[self.selected] = command.endswith('1')

    def _transfer(self, command, query):
        self.commands += 1
        if not self.live: raise OSError('Uncertain link; no further SCPI')
        over_budget = (self.commands > self.MAX_COMMANDS or
                       time.monotonic() - self.started > self.MAX_SECONDS)
        cleanup_allowed = (self._CLEANUP_QUERY if query else self._CLEANUP_WRITE).fullmatch(command)
        if self.commands > self.MAX_COMMANDS + self.SHUTDOWN_RESERVE:
            raise TimeoutError('Stage shutdown reserve exhausted')
        if (self.cleanup or over_budget) and not cleanup_allowed:
            raise TimeoutError('Stage budget exhausted or only shutdown is allowed')
        self._validate(command, query)
        self.evidence.add('query' if query else 'write', command=command)
        try:
            answer = self.transport.query(command) if query else self.transport.write(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command, exception_type=type(exc).__name__)
            raise
        if query:
            # The stage adapter is also a trust boundary. Diagnostic queries
            # use raw driver replies; never follow an undecodable reply with
            # a different SCPI command (including automatic error polling).
            try:
                if command == 'SYST:ERR?':
                    code, message = answer.split(',', 1)
                    int(code.strip())
                    if not message.strip(): raise ValueError('empty SCPI error message')
                elif command != '*IDN?':
                    if answer.strip().upper() not in ('ON', 'OFF'):
                        number = float(answer)
                        if not math.isfinite(number): raise ValueError('non-finite number')
            except (AttributeError, TypeError, ValueError, OverflowError):
                self.live = False
                self.evidence.add('invalid_scpi_reply', command=command,
                                  classification='UNKNOWN OUTPUT STATE; no follow-up I/O')
                raise SafetyError(f'Invalid SCPI reply to {command}; '
                                  'UNKNOWN OUTPUT STATE (no retry)')
            if command == 'SYST:ERR?':
                self.last_error_response = answer
            record = self.evidence.add('read', command=command, response=answer, units=UNITS.get(command))
            print(f'{command}: {record["response"]}')
        else:
            self._after_write(command)
            # Show the successful write as well as query replies. Previously the
            # terminal hid writes between error checks, giving a false impression
            # that consecutive printed SYST:ERR? lines were adjacent on the wire.
            print(f'SCPI WRITE: {command}')
        if command not in ('SYST:ERR?', 'SYST:LOC'):
            response = self._transfer('SYST:ERR?', True)
            code, message = response.split(',', 1)
            expected = self.expected_error if not query and self.expected_error is not None else 0
            expected_code = expected[0] if isinstance(expected, tuple) else expected
            expected_message = expected[1] if isinstance(expected, tuple) else None
            actual_message = message.strip().strip('"')
            if (int(code.strip()) != expected_code or not actual_message or
                    (expected_message is not None and actual_message != expected_message)):
                if self.cleanup and command in ('SYST:REM', 'OUTP 0', 'CHAN:OUTP 0'):
                    self.evidence.add('shutdown_error_queue', source_command=command, response=response)
                else:
                    raise SafetyError(f'Instrument error after {command}: {response}; expected {expected}')
            if expected_code:
                self.evidence.add('expected_instrument_rejection', command=command,
                                  response=response, outcome='EXPECTED_REJECTION')
        return answer

    def query(self, command): return self._transfer(command, True)
    def write(self, command): self._transfer(command, False)
    def close(self): self.transport.close()


def require_outputs(dev, expected=None, *, settle_timeout_s=0.0):
    """Check validated ON bits, optionally waiting for a known ON-bit update.

    An unexpected live neighbor or a failed query stops immediately. Polling only
    tolerates the previously observed delayed OPER bit after a successful ON.
    """
    expected = {1: False, 2: False, 3: False} if expected is None else expected
    deadline = time.monotonic() + settle_timeout_s
    while True:
        actual = dev.output_states()
        if actual == expected: return actual
        if (any(actual[ch] and not expected[ch] for ch in (1, 2, 3)) or
                time.monotonic() >= deadline or
                dev.diagnostic_query('CHAN:OUTP?').strip() != '1'):
            raise SafetyError(f'Output states {actual}; expected {expected}')
        time.sleep(.1)


def selected_settings(dev, *, measured=False, expected_channel=None):
    """Observe the current selection and its registers without assigning a channel.

    Combined modes may alias a requested channel. When a channel is expected,
    reject a selection mismatch BEFORE querying any of its settings.
    """
    commands = ('INST:NSEL?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?')
    if measured: commands += ('MEAS:VOLT?', 'MEAS:CURR?')
    values = {}
    for command in commands:
        value = float(dev.diagnostic_query(command))
        if command == 'INST:NSEL?' and expected_channel is not None and value != expected_channel:
            raise SafetyError(f'Selected CH{value:g}, expected CH{expected_channel}; no settings queried')
        values[command] = value
    if (not all(math.isfinite(v) for v in values.values()) or values['INST:NSEL?'] not in (1, 2, 3) or
            any(values[key] not in (0, 1) for key in ('VOLT:LIM:STAT?', 'CHAN:OUTP?', 'OUTP:TIM:STAT?'))):
        raise SafetyError(f'Invalid selected settings snapshot: {values}')
    return values


def channel_snapshot(dev, channel, *, measured=False, select=True):
    """Read one channel strictly; always verify INST:NSEL? even if select=False.

    select=False is allowed only when the caller knows the selection was just
    restored by a validated driver readback. The snapshot still checks the actual
    selection and refuses an aliased/wrong context before using its values.
    """
    if type(channel) is not int or channel not in (1, 2, 3) or type(select) is not bool:
        raise ValueError('channel must be 1, 2 or 3; select must be bool')
    if select:
        dev.diagnostic_write(f'INST:NSEL {channel}')
    values = selected_settings(dev, measured=measured, expected_channel=channel)
    if values['INST:NSEL?'] != channel:
        raise SafetyError(f'Invalid CH{channel} settings snapshot: {values}')
    return values


def operator_gate(token, prompt, adapter=None, *, powered_timeout_s=None):
    """Operator gate; when energized, bound the whole confirmation window.

    Only the native interactive POSIX TTY path can use a timed read. Offline
    mocks use their injected input implementation and never touch real ports.
    A timeout raises so the caller can run its existing live-link OFF cleanup.
    """
    print(prompt)
    deadline = None
    if powered_timeout_s is not None:
        if not (isinstance(powered_timeout_s, (int, float)) and
                math.isfinite(powered_timeout_s) and 0 < powered_timeout_s <= 120):
            raise ValueError('Powered operator gate requires a bounded 1..120 s timeout')
        deadline = time.monotonic() + powered_timeout_s
    while True:
        try:
            if deadline is not None and getattr(builtins.input, '__module__', '') == 'builtins':
                if not sys.stdin.isatty():
                    raise InterruptedError('powered gate requires an interactive TTY')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('powered display confirmation timed out')
                sys.stdout.write(f'Подтверждение:\n{token}\n> ')
                sys.stdout.flush()
                ready, _, _ = select.select([sys.stdin], [], [], remaining)
                if not ready:
                    raise TimeoutError('powered display confirmation timed out')
                answer = sys.stdin.readline().strip()
                if not answer:
                    raise InterruptedError('powered display confirmation EOF')
            else:
                # Unit-test input mocks: no TTY read, no timer adjustment.
                read = adapter.operator_input if adapter is not None else input
                answer = read(f'Подтверждение:\n{token}\n> ').strip()
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError('powered display confirmation timed out')
        except (EOFError, KeyboardInterrupt):
            raise InterruptedError('operator gate interrupted')
        if answer == token: return
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('powered display confirmation timed out')
        print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
