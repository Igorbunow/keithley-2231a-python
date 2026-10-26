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
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
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

    def operator_input(self, prompt):
        """Exclude operator response time from the instrument execution deadline."""
        started = time.monotonic()
        try: return input(prompt)
        finally: self.started += time.monotonic() - started

    def _validate(self, command, query):
        pattern = getattr(self, '_QUERY' if query else '_WRITE', None)
        allowed = pattern.fullmatch(command) if pattern is not None else command in (
            self.READS if query else self.WRITES)
        if not allowed: raise ValueError(f'Command outside approved stage scope: {command}')

    def _after_write(self, command):
        if command.startswith('INST:NSEL '): self.selected = int(command[-1])
        elif command == 'OUTP 0': self.outputs = {ch: False for ch in (1, 2, 3)}
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
            record = self.evidence.add('read', command=command, response=answer, units=UNITS.get(command))
            print(f'{command}: {record["response"]}')
        else:
            self._after_write(command)
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


def channel_snapshot(dev, channel, *, measured=False):
    """Read and validate one selected channel through driver diagnostics."""
    dev.diagnostic_write(f'INST:NSEL {channel}')
    commands = ('INST:NSEL?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?')
    if measured: commands += ('MEAS:VOLT?', 'MEAS:CURR?')
    values = {command: float(dev.diagnostic_query(command)) for command in commands}
    if (not all(math.isfinite(v) for v in values.values()) or values['INST:NSEL?'] != channel or
            any(values[key] not in (0, 1) for key in ('VOLT:LIM:STAT?', 'CHAN:OUTP?', 'OUTP:TIM:STAT?'))):
        raise SafetyError(f'Invalid CH{channel} settings snapshot: {values}')
    return values


def operator_gate(token, prompt, adapter=None):
    """Wait for a valid operator action without recording mistyped confirmations."""
    print(prompt)
    while True:
        try:
            read = adapter.operator_input if adapter is not None else input
            answer = read(f'Подтверждение:\n{token}\n> ').strip()
        except (EOFError, KeyboardInterrupt):
            raise InterruptedError('operator gate interrupted')
        if answer == token: return
        print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
