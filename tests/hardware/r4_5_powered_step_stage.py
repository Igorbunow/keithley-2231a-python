"""R4.5-E-B: one low-power CH1 powered STEP with KEL103 + independent DMM.

Two mutually exclusive, operator-authorized hardware cases: CV/10 mV or
CC/5 mA. NEVER chain cases, reconnect, auto-retry, or infer analog evidence
from digital register readback. No instrument I/O on import.
"""
from __future__ import annotations

import math
import time

from common import (ShutdownEvidence, operator_gate, require_outputs,
                    release_psu_and_load)
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_4_scalar_stage import R44ScalarScope
from r4_status_stage import _redacted_trace

STAGE = 'R45EPOWERED'
POINTS = {
    'cv': dict(kind='voltage', step=.010, mode='CV', cr=40.0,
               voltage=3.30, limit=.150, expected_delta=.010),
    'cc': dict(kind='current', step=.005, mode='CC', cr=11.0,
               voltage=3.30, limit=.100, expected_delta=.005),
}
MAX_POWER_W = .60
INITIAL_TOKEN = ('CONFIRM R45EPOWERED INDEPENDENT CH1_KEL103_DMM_FRONT_PARALLEL '
                 'CH2_CH3_EMPTY ALL_PSU_OFF KEL_INPUT_OFF NO_JUMPERS NO_DUT RECALL_OFF')
FINAL_TOKEN = 'CONFIRM R45EPOWERED CH1=OFF CH2=OFF CH3=OFF KEL103=OFF'


def power_token(mode):
    p = POINTS[mode]
    return (f'CONFIRM R45EPOWERED MODE={mode.upper()} CH1_ONLY V3.30 '
            f'I{p["limit"]:.3f} KEL_CR{p["cr"]:g} DMM_FRONT '
            'MAX_P0.60 KEL_INPUT_ON_CH1_ON ONE_STEP_UP')


class R45EPoweredScope(R44ScalarScope):
    """R4.4-C approved operations + one tightly scoped source mutation."""
    READS = R44ScalarScope.READS | {'VOLT:STEP?', 'CURR:STEP?'}
    WRITES = R44ScalarScope.WRITES | {
        'VOLT:STEP 0.010', 'VOLT:UP', 'CURR:STEP 0.005', 'CURR:UP'}
    MAX_COMMANDS = 1800
    MAX_SECONDS = 300

    def __init__(self, transport, evidence, *, mode):
        if mode not in POINTS: raise ValueError('Unsupported R4.5-E mode')
        super().__init__(transport, evidence)
        self.mode = mode
        self.step_window_authorized = False
        self.step_writes = 0
        self.moves = 0

    def _validate(self, command, query):
        if not query and command in ('VOLT:STEP 0.010', 'VOLT:UP',
                                    'CURR:STEP 0.005', 'CURR:UP'):
            p = POINTS[self.mode]
            step_cmd = ('VOLT:STEP 0.010' if self.mode == 'cv'
                        else 'CURR:STEP 0.005')
            move_cmd = 'VOLT:UP' if self.mode == 'cv' else 'CURR:UP'
            if (not self.step_window_authorized or self.cleanup or self.selected != 1
                    or command not in (step_cmd, move_cmd)):
                raise SafetyError('Powered STEP outside mode-specific approval')
            if command == step_cmd:
                if self.step_writes or self.moves:
                    raise SafetyError('Powered STEP setting may be written only once')
                self.step_writes += 1
            else:
                if not self.step_writes or self.moves or not self.power_on_commands:
                    raise SafetyError('Powered STEP UP requires configured step and CH1 ON')
                self.moves += 1
            return
        if query and command in ('VOLT:STEP?', 'CURR:STEP?'):
            if command != ('VOLT:STEP?' if self.mode == 'cv' else 'CURR:STEP?'):
                raise SafetyError('Wrong STEP readback for powered mode')
            return
        super()._validate(command, query)


def profile_for(mode):
    p = POINTS[mode]
    return Profile('R4.5-E powered one-shot ' + mode, (
        ChannelProfile(3.50, .150, p['voltage'], p['limit']),
        ChannelProfile(1.50, .100, 1.00, .100),
        ChannelProfile(1.50, .100, 1.00, .100),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def finite(value, title, maximum):
    try:
        result = float(value)
        if not math.isfinite(result) or result < 0 or result > maximum:
            raise ValueError('invalid bounded value')
    except (ValueError, TypeError, OverflowError):
        raise SafetyError(f'Invalid/overrange {title}: {value!r}') from None
    return result


def snapshot(dev, load, meter, evidence, *, mode, label):
    """Chronologically record 3 distinct instruments; no synthetic simultaneity."""
    p = POINTS[mode]
    if load.input_state() != 'ON' or load.query(':FUNC?').strip().upper() != 'CR':
        raise SafetyError('KEL103 INPUT/CR changed during powered STEP')
    ohms = finite(load.query(':RES?').strip().upper().removesuffix('OHM'),
                  'KEL CR resistance', 2000.)
    if abs(ohms - p['cr']) > .001:
        raise SafetyError('KEL103 CR setting drift')
    require_outputs(dev, {1: True, 2: False, 3: False})
    reading = dev.read_channel(1)
    times = {'psu_s': time.monotonic()}
    if not reading.enabled or reading.regulation != p['mode']:
        raise SafetyError('PSU regulation/output mode is not expected')
    pu = finite(reading.voltage_v, 'PSU U', 3.55)
    pi = finite(reading.current_a, 'PSU I', .165)
    if pu * pi > MAX_POWER_W:
        raise SafetyError('PSU live power exceeds 0.60W')
    kel = load.measurements()
    times['kel_s'] = time.monotonic()
    ku = finite(kel['kel103_voltage_v'], 'KEL U', 3.55)
    ki = finite(kel['kel103_current_a'], 'KEL I', .165)
    kp = finite(kel['kel103_power_w'], 'KEL P', MAX_POWER_W)
    meter_read = meter.measure()
    times['dmm_s'] = time.monotonic()
    du = finite(meter_read['voltage_v'], 'DMM U', 3.55)
    # Abort on any wrong external observation before additional PSU scalar queries.
    if (abs(pu-du) > .08 or abs(ku-du) > .08 or abs(pu-ku) > .10
            or abs(pi-ki) > .030 or abs(kp-ku*ki) > .045):
        raise SafetyError('PSU/KEL/DMM independent live readings disagree')
    if p['mode'] == 'CV':
        expected_voltage = p['voltage'] + (p['step'] if label == 'after' else 0.)
        if abs(du - expected_voltage) > .040:
            raise SafetyError('CV external voltage disagrees with commanded absolute setpoint')
    else:
        expected_current = p['limit'] + (p['step'] if label == 'after' else 0.)
        if abs(ki - expected_current) > .012:
            raise SafetyError('CC external current disagrees with commanded absolute current limit')
        if abs(du - p['cr'] * expected_current) > .200:
            raise SafetyError('CC external voltage incompatible with verified CR load')
    mp = finite(dev.measure_power(1), 'PSU MEAS:POW', MAX_POWER_W)
    times['power_s'] = time.monotonic()
    if abs(mp-pu*pi) > .08 or abs(mp-kp) > .08:
        raise SafetyError('PSU MEAS:POW disagrees with live external instruments')
    result = dict(label=label, mode=p['mode'], psu_v=pu, psu_a=pi, psu_derived_w=pu*pi,
                  psu_meas_w=mp, kel_v=ku, kel_a=ki, kel_w=kp, dmm_v=du,
                  timestamps_monotonic_s=times,
                  note='Sequential PSU/KEL/DMM observations; not synchronized')
    evidence.add('powered_step_snapshot', **result)
    return result


def validate_delta(before, after, mode):
    """Require externally measured directional effect, not a digital-only pass."""
    p = POINTS[mode]
    dmm_delta = after['dmm_v'] - before['dmm_v']
    kel_i_delta = after['kel_a'] - before['kel_a']
    psu_delta = (after['psu_v']-before['psu_v'] if mode=='cv'
                 else after['psu_a']-before['psu_a'])
    if mode == 'cv':
        if not .003 <= dmm_delta <= .030:
            raise SafetyError(f'DMM failed to confirm +10mV CV STEP: delta={dmm_delta}')
    else:
        if not .002 <= kel_i_delta <= .020:
            raise SafetyError(f'KEL failed to confirm +5mA CC STEP: delta={kel_i_delta}')
        if not .010 <= dmm_delta <= .150:
            raise SafetyError(f'DMM failed to confirm CC load voltage change: delta={dmm_delta}')
    return dict(dmm_voltage_delta_v=dmm_delta, kel_current_delta_a=kel_i_delta,
                psu_analog_delta=psu_delta, mode=p['mode'])


def run_powered_step(transport, load, meter, target_evidence, *, mode, gate=operator_gate):
    """Exactly one CV or CC powered STEP per distinct hardware session."""
    if mode not in POINTS: raise ValueError('Unsupported mode')
    evidence = ShutdownEvidence(target_evidence)
    if getattr(load, 'evidence', None) is target_evidence: load.evidence = evidence
    if getattr(meter, 'evidence', None) is target_evidence: meter.evidence = evidence
    dev = io = None
    outcome = 'ABORTED'
    reason = 'R4.5-E-B incomplete; operator must verify physical OFF'
    physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda event: _redacted_trace(evidence, event),
            debug_adapter=lambda wire: R45EPoweredScope(wire, evidence, mode=mode))
        io = dev.debug_transport
        identity = dev.identity()
        evidence.add('psu_identity', response=identity)
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('KEL identity or initial INPUT OFF mismatch')
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError('Independent topology required')
        meter.configure_voltage(10)
        load.configure_cr(POINTS[mode]['cr'])
        require_outputs(dev)
        dev.apply_profile(profile_for(mode), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL input changed before approval')
        gate(power_token(mode), f'Physically verify CH1 wiring, KEL CR{POINTS[mode]["cr"]:g}, '
             'DMM FRONT parallel; authorize one powered STEP', io)
        evidence.add('operator_confirmation', gate='POWER', mode=mode)
        io.step_window_authorized = True
        try:
            p = POINTS[mode]
            with dev.powered_step_qualification(1, p['kind'], p['step'], 'up',
                expected_mode=p['mode'], max_power_w=MAX_POWER_W,
                external_load_confirmed=True):
                if load.input_state() != 'OFF':
                    raise SafetyError('KEL input was enabled unexpectedly')
                load.set_input(True)
                io.enable_authorized = True
                try:
                    dev.output(1, True)
                finally:
                    io.enable_authorized = False
                require_outputs(dev, {1:True, 2:False, 3:False}, settle_timeout_s=.5)
                before = snapshot(dev, load, meter, evidence, mode=mode, label='before')
                digital = dev.step_source(1, p['kind'], 'up')
                evidence.add('powered_step_digital', mode=mode, kind=p['kind'],
                             direction='up', readback=digital, step=p['step'])
                after = snapshot(dev, load, meter, evidence, mode=mode, label='after')
                evidence.add('powered_step_delta', **validate_delta(before, after, mode))
        finally:
            io.step_window_authorized = False
        outcome, reason = 'PASS', f'{mode.upper()} single low-power analog STEP; pending review'
    except BaseException as exc:
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
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
                    outcome = 'ABORTED'
                    reason += f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically verify PSU three channels OFF and KEL INPUT OFF')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome = 'ABORTED'
            reason += '; final physical OFF unconfirmed'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason, mode=mode,
                         physical_off_confirmed=physical_off,
                         status='HW NOT_REVIEWED')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-E-B {mode.upper()} {outcome}: {reason}')
    return outcome
