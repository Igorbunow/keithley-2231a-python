"""R4.5-E-C: bounded five physical points under one powered CH1 session.

Each invocation covers only one CV or CC mode. Sequence UP UP DOWN DOWN is
fixed; no arbitrary sweep, retry, trigger, alternate channel or auto-reconnect.
"""
from __future__ import annotations

import math

from common import ShutdownEvidence, require_outputs, release_psu_and_load
from keithley2231a import Keithley2231A, SafetyError
from r4_status_stage import _redacted_trace
from r4_5_powered_step_stage import (R45EPoweredScope, POINTS, MAX_POWER_W,
    profile_for, snapshot, finite)

DIRECTIONS = ('up', 'up', 'down', 'down')
OFFSETS = (0, 1, 2, 1, 0)
INITIAL_TOKEN = ('CONFIRM R45ESERIES INDEPENDENT CH1_KEL103_DMM_FRONT_PARALLEL '
                 'CH2_CH3_EMPTY ALL_PSU_OFF KEL_INPUT_OFF NO_JUMPERS NO_DUT RECALL_OFF')
FINAL_TOKEN = 'CONFIRM R45ESERIES CH1=OFF CH2=OFF CH3=OFF KEL103=OFF'


def series_token(mode):
    p = POINTS[mode]
    return (f'CONFIRM R45ESERIES MODE={mode.upper()} CH1_ONLY V3.30 '
            f'I{p["limit"]:.3f} KEL_CR{p["cr"]:g} DMM_FRONT MAX_P0.60 '
            'ONE_ENABLE FOUR_MOVES UP_UP_DOWN_DOWN FIVE_MEASUREMENTS')


class R45ESeriesScope(R45EPoweredScope):
    """Reuse proven per-command whitelist, replace only number of authorized moves."""
    MAX_COMMANDS = 5200
    MAX_SECONDS = 720

    def __init__(self, transport, evidence, *, mode):
        super().__init__(transport, evidence, mode=mode)
        self.move_directions = DIRECTIONS


def validate_segment(before, after, *, mode, direction):
    """Fail on any missing physical effect, including reversed sign."""
    sign = 1 if direction == 'up' else -1
    du = sign * (after['dmm_v'] - before['dmm_v'])
    di = sign * (after['kel_a'] - before['kel_a'])
    if mode == 'cv':
        if not .003 <= du <= .030:
            raise SafetyError(f'CV DMM segment did not follow approved STEP: {du}')
    else:
        if not .002 <= di <= .020 or not .010 <= du <= .150:
            raise SafetyError(f'CC KEL/DMM segment did not follow approved STEP: I={di}, U={du}')
    return dict(dmm_signed_delta_v=du, kel_signed_delta_a=di)


def validate_return(points, mode):
    """Require end-of-sequence repeatability, never infer from final digital setpoint."""
    first, last = points[0], points[-1]
    dmm_error = abs(last['dmm_v'] - first['dmm_v'])
    kel_error = abs(last['kel_a'] - first['kel_a'])
    if dmm_error > (.006 if mode == 'cv' else .035):
        raise SafetyError('Final DMM voltage did not return to original physical level')
    if mode == 'cc' and kel_error > .002:
        raise SafetyError('Final KEL current did not return to original physical level')
    return dict(dmm_return_error_v=dmm_error, kel_return_error_a=kel_error,
                note='steady state only, not dynamic/settling time proof')


def run_powered_series(transport, load, meter, target_evidence, *, mode, gate):
    if mode not in POINTS: raise ValueError('Unsupported mode')
    evidence = ShutdownEvidence(target_evidence)
    if getattr(load, 'evidence', None) is target_evidence: load.evidence = evidence
    if getattr(meter, 'evidence', None) is target_evidence: meter.evidence = evidence
    dev = io = None
    outcome = 'ABORTED'
    reason = 'R4.5-E-C unfinished; manual physical OFF required'
    physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda e: _redacted_trace(evidence, e),
            debug_adapter=lambda w: R45ESeriesScope(w, evidence, mode=mode))
        io = dev.debug_transport
        evidence.add('psu_identity', response=dev.identity())
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('KEL identity or initial INPUT OFF mismatch')
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('Independent topology required')
        meter.configure_voltage(10)
        load.configure_cr(POINTS[mode]['cr'])
        require_outputs(dev)
        dev.apply_profile(profile_for(mode), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL input changed before power approval')
        gate(series_token(mode), 'Verify CR resistance, DMM FRONT DCV, and one controlled five-point sequence', io)
        evidence.add('operator_confirmation', gate='POWER', mode=mode, directions=DIRECTIONS)
        io.step_window_authorized = True
        try:
            p = POINTS[mode]
            with dev.powered_step_qualification(1, p['kind'], p['step'], 'up',
                    expected_mode=p['mode'], max_power_w=MAX_POWER_W,
                    external_load_confirmed=True, sequence=DIRECTIONS):
                if load.input_state() != 'OFF':
                    raise SafetyError('KEL input enabled unexpectedly')
                load.set_input(True)
                io.enable_authorized = True
                try:
                    dev.output(1, True)
                finally:
                    io.enable_authorized = False
                require_outputs(dev, {1: True, 2: False, 3: False}, settle_timeout_s=.5)
                observations = [snapshot(dev, load, meter, evidence, mode=mode,
                                         label='point_0', offset_steps=0)]
                for index, direction in enumerate(DIRECTIONS, 1):
                    digital = dev.step_source(1, p['kind'], direction)
                    evidence.add('powered_series_digital', mode=mode, index=index,
                                 kind=p['kind'], direction=direction,
                                 expected_offset_steps=OFFSETS[index],
                                 readback=digital, step=p['step'])
                    point = snapshot(dev, load, meter, evidence, mode=mode,
                                     label=f'point_{index}',
                                     offset_steps=OFFSETS[index])
                    delta = validate_segment(observations[-1], point,
                                             mode=mode, direction=direction)
                    evidence.add('powered_series_segment', index=index,
                                 direction=direction, **delta)
                    observations.append(point)
                evidence.add('powered_series_return', mode=mode,
                             **validate_return(observations, mode))
        finally:
            io.step_window_authorized = False
        outcome, reason = 'PASS', f'{mode.upper()} five powered steady-state points; pending independent review'
    except BaseException as exc:
        try: evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException: pass
        reason = f'{type(exc).__name__}: {exc}; physical OFF required if uncertain'
    finally:
        evidence.cleanup = True
        failures = []
        if dev is not None and io is not None:
            failures = release_psu_and_load(dev, io, load, evidence)
        else:
            failures.append('PSU not opened; physical OFF required')
            if getattr(load, 'live', False):
                try:
                    if load.input_state() != 'OFF': load.set_input(False)
                    if load.input_state() != 'OFF': raise SafetyError('Load shutdown failed')
                except BaseException: failures.append('KEL INPUT OFF unknown')
        if failures or evidence.lost:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
            if evidence.lost: reason += '; evidence incomplete'
        elif outcome == 'PASS':
            try:
                io.cleanup = False
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                outcome, reason = 'ABORTED', reason + f'; LOCAL unconfirmed: {type(exc).__name__}'
        for instrument in (meter, load, transport):
            if instrument is not None:
                try: instrument.close()
                except BaseException as exc:
                    outcome, reason = 'ABORTED', reason + f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically confirm CH1/CH2/CH3 OFF and KEL INPUT OFF')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome, reason = 'ABORTED', reason + '; final physical OFF unconfirmed'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason, mode=mode,
                         physical_off_confirmed=physical_off,
                         status='HW NOT_REVIEWED')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-E-C {mode.upper()} {outcome}: {reason}')
    return outcome
