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
    add('OUTP 0', *OFF, 'INST:NSEL 1', 'INST:NSEL?', *OFF, 'SYST:ERR?', 'SYST:LOC')
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
            if command not in ('OUTP 0', 'SYST:ERR?', 'SYST:LOC', *OFF):
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
        identity = dev.identity()
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
        if dev.instrument_id != identity or not dev.armed or dev.faulted:
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
