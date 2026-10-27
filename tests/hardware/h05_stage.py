"""H05 power-sequence qualification using the TDS 3064B e*Scope interface.

@file h05_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Execute the fixed H05 power_only sequence and capture all three rails.
@details The operator gate is checked by qualification.py before any connection.
         The scope common-ground wiring is confirmed separately by the operator.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
import re
import time

from common import StageAdapter, require_outputs
from equipment.tds3064 import TDS3064B, decode_isf

from keithley2231a import ChannelProfile, Keithley2231A, PowerStep, Profile, SafetyError

OFF = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
def approval_token():
    from bench_config import current_bench
    load = current_bench().load
    return (f'CONFIRM H05 POWERED INDEPENDENT ALL_OFF LOAD=KEL103_USB={load.port}_{load.baudrate} '
            'CH1=CR22 CH2=OPEN CH3=OPEN SCOPE=COMMON_NEGATIVE_X10 '
            'PSU=1.00V_0.100A DELAY=1S RECALL=OFF')

def transitions(rows, threshold=0.5):
    """Return raw midpoint crossings, including chatter for diagnostic evidence."""
    edges = []
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if (v0 < threshold <= v1) or (v0 > threshold >= v1):
            fraction = (threshold - v0) / (v1 - v0)
            edges.append((t0 + fraction * (t1 - t0), 'RISE' if v1 > v0 else 'FALL'))
    return edges


def qualified_transitions(rows):
    """Find full-rail edges at 10% rise/90% fall, qualified by the opposite rail.

    Open outputs can discharge slowly and chatter around the midpoint. A single
    10% rising or 90% falling crossing identifies the start of the physical edge;
    requiring the opposite rail before accepting it rejects noise without moving
    the timestamp to the end of the transition.
    """
    if len(rows) < 2:
        raise ValueError('TDS waveform is too short for edge analysis')
    voltages = [voltage for _, voltage in rows]
    low = min(voltages)
    high = max(voltages)
    span = high - low
    if not math.isfinite(span) or span <= 0:
        raise ValueError('TDS waveform has no finite voltage span')
    rise_level = low + 0.1 * span
    fall_level = low + 0.9 * span
    if voltages[0] >= rise_level:
        raise SafetyError('H05 scope capture did not start with the output low')

    edges = []
    is_high = False
    index = 0
    while index < len(rows) - 1:
        found = False
        for crossing_index in range(index, len(rows) - 1):
            t0, v0 = rows[crossing_index]
            t1, v1 = rows[crossing_index + 1]
            if not is_high and v0 < rise_level <= v1:
                confirmation = None
                qualification = None
                for j in range(crossing_index + 1, len(rows)):
                    if rows[j][1] >= fall_level:
                        confirmation, qualification = 'HIGH', j
                        break
                    if rows[j][1] <= rise_level:
                        confirmation, qualification = 'LOW', j
                        break
                if confirmation != 'HIGH':
                    continue
                fraction = (rise_level - v0) / (v1 - v0)
                edges.append((t0 + fraction * (t1 - t0), 'RISE'))
                is_high = True
                index = qualification
                found = True
                break
            if is_high and v0 > fall_level >= v1:
                confirmation = None
                qualification = None
                for j in range(crossing_index + 1, len(rows)):
                    if rows[j][1] <= rise_level:
                        confirmation, qualification = 'LOW', j
                        break
                    if rows[j][1] >= fall_level:
                        confirmation, qualification = 'HIGH', j
                        break
                if confirmation != 'LOW':
                    continue
                fraction = (fall_level - v0) / (v1 - v0)
                edges.append((t0 + fraction * (t1 - t0), 'FALL'))
                is_high = False
                index = qualification
                found = True
                break
        if not found:
            break
    return edges


class H05Scope(StageAdapter):
    """Allow-list Keithley SCPI and record every reply/write in the evidence."""
    WRITES = frozenset(('SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
                        'OUTP:TRAC 0', 'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
                        'VOLT:LIM 1.00', 'VOLT:LIM:STAT 1', 'CURR 0.100',
                        'VOLT 1.00', 'CHAN:OUTP 0', 'CHAN:OUTP 1',
                        *[f'INST:NSEL {ch}' for ch in (1, 2, 3)]))
    READS = frozenset(('*IDN?', 'SYST:ERR?', 'INST:NSEL?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                       'OUTP:TRAC?', 'VOLT:LIM?', 'VOLT:LIM:STAT?', 'VOLT?', 'CURR?',
                       'CHAN:OUTP?', 'MEAS:VOLT?', 'MEAS:CURR?', *OFF,
                       'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?'))
    MAX_COMMANDS = 2000
    SHUTDOWN_RESERVE = 64
    MAX_SECONDS = 300



def h05(transport, evidence, load):
    """Run one fixed low-energy H05 sequence and capture all channel transitions."""
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H05Scope(channel, evidence))
    io = dev.debug_transport
    from equipment.factory import open_scope
    from bench_config import current_bench
    scope = open_scope(current_bench(), evidence)
    outcome = 'ABORTED'
    try:
        identity = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=identity)
        initial_error = dev.error()
        code, message = initial_error.split(',', 1)
        evidence.add('initial_error_queue', response=initial_error)
        if int(code.strip()) != 0 or not message.strip():
            raise SafetyError(f'initial PSU error queue is not clear: {initial_error}')
        states = dev.output_states()
        evidence.add('initial_output_states', channels=states)
        if any(states.values()):
            raise SafetyError(f'output initially ON: {states}')
        if load.identify().upper().find('KEL103') < 0:
            raise SafetyError('wrong electronic load identity')
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must start OFF')
        load.configure_cr(22.0)
        profile = Profile('H05 fixed three-channel sequence',
                          tuple(ChannelProfile(1.0, 0.100, 1.0, 0.100)
                                for _ in range(3)),
                          wiring='independent', lock_front_panel=False,
                          verify_every_s=3600.0, access='power_only',
                          power_sequence=(PowerStep(1, 0.0), PowerStep(2, 1.0),
                                          PowerStep(3, 1.0)))
        dev.apply_profile(profile)
        evidence.add('profile', name=profile.name, access='power_only', wiring='independent',
                     per_channel={'max_voltage_v': 1.0, 'max_current_a': 0.1,
                                  'initial_voltage_v': 1.0, 'initial_current_a': 0.1},
                     power_sequence=[{'channel': step.channel, 'delay_s': step.delay_s}
                                     for step in profile.power_sequence])
        for operation in (lambda: dev.set_channel(1, voltage=0.99),
                          lambda: dev.timer(1, 1.0),
                          lambda: dev.sequence([(0.0, 1, True)]),
                          lambda: dev.output(1, True)):
            before = io.commands
            try:
                operation()
            except SafetyError:
                pass
            else:
                raise SafetyError('power_only API accepted a forbidden call')
            if io.commands != before:
                raise SafetyError('power_only API rejection emitted SCPI')
        evidence.add('api_guards', set_channel='REJECTED_NO_SCPI',
                     timer='REJECTED_NO_SCPI', sequence='REJECTED_NO_SCPI',
                     direct_output='REJECTED_NO_SCPI', outcome='PASS')
        scope_configuration = scope.configure()
        evidence.add('scope_configuration', **scope_configuration)
        for channel in (1, 2, 3):
            reading = dev.read_channel(channel, with_regulation=False)
            if reading.enabled or abs(reading.voltage_set_v - 1.0) > .0049 or abs(reading.current_limit_set_a - .1) > .00049:
                raise SafetyError(f'CH{channel} initial readback not OFF at 1.00 V/0.100 A')
            evidence.add('pre_run_readback', channel=channel, enabled=reading.enabled,
                         voltage_set_v=reading.voltage_set_v, current_limit_set_a=reading.current_limit_set_a)
        scope.arm()
        evidence.add('scope_armed')
        load.set_input(True)
        evidence.add('kel103_input', state='ON')
        started = time.monotonic()
        dev.power_on()
        on_finished = time.monotonic()
        psu_readings = {}
        for channel in (1, 2, 3):
            reading = dev.read_channel(channel, with_regulation=False)
            if not reading.enabled:
                raise SafetyError(f'CH{channel} did not read ON after power_on()')
            psu_readings[channel] = {
                'voltage_v': reading.voltage_v, 'current_a': reading.current_a,
                'power_w': reading.power_w, 'enabled': reading.enabled,
                'voltage_set_v': reading.voltage_set_v,
                'current_limit_set_a': reading.current_limit_set_a}
        evidence.add('all_channels_on_readback', channels=psu_readings)
        load_measurement = load.measurements()
        evidence.add('kel103_all_channels_on_measurement', **load_measurement)
        dev.power_off()
        off_finished = time.monotonic()
        evidence.add('sequence_call_timing', power_on_duration_s=on_finished-started,
                     power_off_duration_s=off_finished-on_finished)
        load.set_input(False)
        evidence.add('kel103_input', state='OFF')
        waveforms = scope.collect()
        midpoint_crossings = {channel: transitions(rows) for channel, rows in waveforms.items()}
        per_channel = {channel: qualified_transitions(rows)
                       for channel, rows in waveforms.items()}
        time_axes = [tuple(t for t, _ in waveforms[channel]) for channel in (1, 2, 3)]
        if not time_axes[0] == time_axes[1] == time_axes[2]:
            raise SafetyError('TDS channel waveform time axes do not match')
        all_edges = sorted((time_s, channel, direction)
                           for channel, edges in per_channel.items()
                           for time_s, direction in edges)
        expected = [(1, 'RISE'), (2, 'RISE'), (3, 'RISE'),
                    (3, 'FALL'), (2, 'FALL'), (1, 'FALL')]
        actual = [(channel, direction) for _, channel, direction in all_edges]
        if actual != expected:
            raise SafetyError(f'H05 measured edge order mismatch: {actual}')
        evidence.add('scope_midpoint_crossings', threshold_v=0.5,
                     crossings=[{'time_s': t, 'channel': ch, 'direction': d}
                                for ch, crossings in midpoint_crossings.items()
                                for t, d in crossings],
                     outcome='DIAGNOSTIC_ONLY')
        intervals = [all_edges[index + 1][0] - all_edges[index][0] for index in range(5)]
        # Edge onset uses 10% rising / 90% falling crossings; the opposite rail
        # qualifies each full transition and midpoint chatter remains diagnostic.
        if any(interval < 0.95 for interval in (intervals[0], intervals[1], intervals[3], intervals[4])):
            raise SafetyError(f'H05 minimum transition delay failed: {intervals}')
        evidence.add('scope_edges', edges=[{'time_s': t, 'channel': ch, 'direction': d}
                                          for t, ch, d in all_edges],
                     measurement='10% rising / 90% falling edge onset; qualified by opposite rail',
                     intervals_s=intervals, minimum_delay_s=1.0,
                     measured_tolerance_s=0.05, outcome='PASS')
        states = dev.output_states()
        if any(states.values()):
            raise SafetyError(f'PSU output remains ON after power_off(): {states}')
        error = dev.error()
        if not error.startswith('0,'):
            raise SafetyError(f'PSU error queue not clear: {error}')
        evidence.add('final_status', output_masks=states, error_queue=error,
                     kel103_input=load.input_state())
        outcome = 'PASS'
    except BaseException as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc))
        print(f'H05 stopped: {type(exc).__name__}: {exc}')
        try:
            if load.input_state() != 'OFF': load.set_input(False)
        except BaseException: pass
        try:
            if io.live:
                io.cleanup = True
                dev.emergency_off()
                dev.diagnostic_write('SYST:LOC')
        except BaseException as off_error:
            evidence.add('shutdown_error', exception_type=type(off_error).__name__, message=str(off_error))
        try:
            if scope.number('ACQUIRE:STATE?') != 0:
                scope.command('ACQUIRE:STATE STOP')
        except BaseException as scope_error:
            evidence.add('scope_stop_error', exception_type=type(scope_error).__name__,
                         message=str(scope_error))
        outcome = 'ABORTED'
    finally:
        try:
            if outcome == 'PASS' and io.live: dev.close()
            else: io.close()
        except BaseException: outcome = 'ABORTED'
        try: load.close()
        except BaseException: outcome = 'ABORTED'
    evidence.add('stage_outcome', outcome=outcome)
    print(f'H05 {outcome}; physical OFF confirmation still required.')
    return outcome
