"""R5-D2: one operator-approved CH1/CH2/CH3 CV or CC powered STEP case.

Exactly UP, UP, DOWN, DOWN. No chaining, retries or cross-channel power.
The stage uses the production driver and independent KEL103/DMM7510 observations.
"""
from __future__ import annotations

import math
import time

from common import ShutdownEvidence, operator_gate, require_outputs, release_psu_and_load
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_5_powered_step_stage import POINTS, MAX_POWER_W, finite
from r4_4_scalar_stage import R44ScalarScope
from r4_status_stage import _redacted_trace

STAGE = 'R5D2POWEREDSTEP'
DIRECTIONS = ('up', 'up', 'down', 'down')
FINAL_TOKEN = 'CONFIRM R5D2 CH1=OFF CH2=OFF CH3=OFF KEL103=OFF LOAD_NOT_ENERGIZED'


def validate_case(channel, mode):
    if type(channel) is not int or channel not in (1, 2, 3):
        raise ValueError('Select one channel CH1/CH2/CH3')
    if mode not in POINTS:
        raise ValueError('Select one mode CV or CC')


def initial_token(channel, mode):
    validate_case(channel, mode)
    return (f'CONFIRM R5D2 CH{channel} MODE={mode.upper()} INDEPENDENT '
            'ALL_PSU_OFF KEL_INPUT_OFF DMM_FRONT_DCV_PARALLEL_SELECTED '
            'OTHERS_EMPTY NO_JUMPERS NO_DUT NO_EXTERNAL_POWER RATED_CABLES')


def enable_token(channel, mode):
    validate_case(channel, mode)
    p = POINTS[mode]
    return (f'CONFIRM R5D2 CH{channel} MODE={mode.upper()} ONLY_SELECTED_ON '
            f'V3.30 I{p["limit"]:.3f} KEL_CR{p["cr"]:g} DMM_FRONT '
            'MAX_P0.60 UP_UP_DOWN_DOWN ONE_CASE')


def profile_for(channel, mode):
    validate_case(channel, mode)
    p = POINTS[mode]
    channels = tuple(ChannelProfile(3.50, .150, 3.30, p['limit'])
                     if i == channel else ChannelProfile(1.50, .100, 1.00, .100)
                     for i in (1, 2, 3))
    return Profile(f'R5-D2 CH{channel} {mode} STEP', channels,
                   wiring='independent', lock_front_panel=False,
                   verify_every_s=3600.0)


class R5D2Scope(R44ScalarScope):
    """Explicit selected-channel allowlist; even a driver error cannot energize another channel."""
    READS = R44ScalarScope.READS | {'VOLT:STEP?', 'CURR:STEP?'}
    WRITES = R44ScalarScope.WRITES | {
        'VOLT:STEP 0.010', 'CURR:STEP 0.005',
        'VOLT:UP', 'VOLT:DOWN', 'CURR:UP', 'CURR:DOWN'}
    MAX_COMMANDS = 2400
    MAX_SECONDS = 360
    SHUTDOWN_RESERVE = 120

    def __init__(self, transport, evidence, *, channel, mode):
        validate_case(channel, mode)
        super().__init__(transport, evidence)
        self.channel, self.mode = channel, mode
        self.step_window_authorized = False
        self.step_writes = 0
        self.moves = []

    def _validate(self, command, query):
        if command not in (self.READS if query else self.WRITES):
            raise SafetyError(f'R5-D2 out-of-scope SCPI: {command}')
        p = POINTS[self.mode]
        prefix = 'VOLT' if self.mode == 'cv' else 'CURR'
        reg = f'{prefix}:STEP {p["step"]:.3f}'
        if command in ('VOLT:STEP?', 'CURR:STEP?'):
            if (not query or not self.step_window_authorized or self.cleanup or
                    self.selected != self.channel or command != f'{prefix}:STEP?'):
                raise SafetyError('Unapproved powered STEP readback')
            return
        if (not query and (command.startswith(('VOLT:STEP ', 'CURR:STEP ')) or
                           command in ('VOLT:UP', 'VOLT:DOWN', 'CURR:UP', 'CURR:DOWN'))):
            if (not self.step_window_authorized or self.cleanup or
                    self.selected != self.channel or command not in (
                        reg, f'{prefix}:UP', f'{prefix}:DOWN')):
                raise SafetyError('STEP operation on unapproved channel/mode')
            if command == reg:
                if self.step_writes or self.moves:
                    raise SafetyError('STEP register may be written only once')
                self.step_writes += 1
            else:
                if (self.step_writes != 1 or self.power_on_commands != 1 or
                        len(self.moves) >= len(DIRECTIONS) or
                        command != f'{prefix}:{DIRECTIONS[len(self.moves)].upper()}'):
                    raise SafetyError('Powered STEP order/budget violation')
                self.moves.append(command)
            return
        if command == 'CHAN:OUTP 1':
            if (query or not self.enable_authorized or self.cleanup or
                    self.selected != self.channel or self.power_on_commands or
                    any(self.outputs.values())):
                raise SafetyError('Only selected CH may turn ON once after physical gate')
            self.power_on_commands += 1
        if command == 'SYST:LOC':
            if query or any(self.outputs.values()) or self.cleanup or self.step_window_authorized:
                raise SafetyError('LOCAL requires completed step and verified OFF')


def snapshot(dev, load, meter, evidence, *, channel, mode, index):
    """Read PSU, load, DMM sequentially; require analog consistency each time."""
    p = POINTS[mode]
    if load.input_state() != 'ON' or load.query(':FUNC?').strip().upper() != 'CR':
        raise SafetyError('KEL103 INPUT/CR state changed')
    ohms = finite(load.query(':RES?').strip().upper().removesuffix('OHM'), 'KEL CR', 2000.)
    if abs(ohms - p['cr']) > .001:
        raise SafetyError('KEL103 CR changed')
    require_outputs(dev, {ch: ch == channel for ch in (1, 2, 3)})
    reading = dev.read_channel(channel)
    stamp = {'psu': time.monotonic()}
    if not reading.enabled or reading.regulation != p['mode']:
        raise SafetyError('Selected channel not ON in expected regulation mode')
    pu = finite(reading.voltage_v, 'PSU voltage', 3.55)
    pi = finite(reading.current_a, 'PSU current', .165)
    if pu*pi > MAX_POWER_W:
        raise SafetyError('PSU live power exceeds limit')
    kel = load.measurements()
    stamp['kel'] = time.monotonic()
    ku = finite(kel['kel103_voltage_v'], 'KEL voltage', 3.55)
    ki = finite(kel['kel103_current_a'], 'KEL current', .165)
    kp = finite(kel['kel103_power_w'], 'KEL power', MAX_POWER_W)
    du = finite(meter.measure()['voltage_v'], 'DMM voltage', 3.55)
    stamp['dmm'] = time.monotonic()
    if (abs(pu-du)>.08 or abs(ku-du)>.08 or abs(pu-ku)>.10 or
            abs(pi-ki)>.030 or abs(kp-ku*ki)>.045):
        raise SafetyError('Independent PSU/KEL/DMM measurements disagree')
    offsets = (0, 1, 2, 1, 0)
    if mode == 'cv':
        if abs(du - (p['voltage'] + offsets[index]*p['step'])) > .040:
            raise SafetyError('DMM absolute CV voltage outside approved envelope')
    else:
        if (abs(ki-(p['limit']+offsets[index]*p['step'])) > .012 or
                abs(du-p['cr']*(p['limit']+offsets[index]*p['step'])) > .200):
            raise SafetyError('CC current/voltage outside approved envelope')
    power = finite(dev.measure_power(channel), 'PSU MEAS:POW', MAX_POWER_W)
    stamp['power'] = time.monotonic()
    if abs(power - pu*pi) > .08 or abs(power-kp) > .08:
        raise SafetyError('External and PSU power measurements disagree')
    values = dict(channel=channel, mode=p['mode'], index=index, offset_steps=offsets[index],
                  psu_v=pu, psu_a=pi, psu_w=pu*pi, psu_meas_w=power,
                  kel_v=ku, kel_a=ki, kel_w=kp, dmm_v=du,
                  sampled_monotonic_s=stamp, synchronized=False)
    evidence.add('powered_step_snapshot', **values)
    return values


def check_transition(previous, current, mode, direction):
    sign = 1 if direction == 'up' else -1
    dv = (current['dmm_v'] - previous['dmm_v'])*sign
    di = (current['kel_a'] - previous['kel_a'])*sign
    if mode == 'cv':
        if not .003 <= dv <= .030:
            raise SafetyError('Independent DMM failed to confirm STEP voltage direction')
    elif not (.002 <= di <= .020 and .010 <= dv <= .150):
        raise SafetyError('Independent KEL/DMM failed to confirm STEP current direction')
    return dict(direction=direction, dmm_delta_v=current['dmm_v']-previous['dmm_v'],
                kel_delta_a=current['kel_a']-previous['kel_a'])


def run_case(transport, load, meter, target_evidence, *, channel, mode,
             gate=operator_gate):
    """Exactly one case; leave PASS unreviewed until independent JSONL audit."""
    validate_case(channel, mode)
    evidence = ShutdownEvidence(target_evidence)
    for instrument in (load, meter):
        if getattr(instrument, 'evidence', None) is target_evidence:
            instrument.evidence = evidence
    dev = io = None
    outcome, reason = 'ABORTED', 'R5-D2 incomplete'
    digital_off = physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda event: _redacted_trace(evidence, event),
            debug_adapter=lambda wire: R5D2Scope(wire, evidence, channel=channel, mode=mode))
        io = dev.debug_transport
        identity = dev.identity()
        evidence.add('psu_identity', model=identity.split(',')[1].strip(),
                     firmware=identity.split(',')[-1].strip())
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('KEL identity or initial OFF failed')
        require_outputs(dev)
        for name in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(name)):
                raise SafetyError('Only independent topology permitted')
        meter.configure_voltage(10)
        load.configure_cr(POINTS[mode]['cr'])
        require_outputs(dev)
        dev.apply_profile(profile_for(channel, mode), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('Unexpected KEL INPUT ON before power gate')
        gate(enable_token(channel, mode), 'Verify selected channel/load/DMM and authorize one STEP sequence', io)
        evidence.add('operator_confirmation', gate='ENABLE', channel=channel, mode=mode)
        io.step_window_authorized = True
        try:
            p = POINTS[mode]
            with dev.powered_step_qualification(channel, p['kind'], p['step'], 'up',
                expected_mode=p['mode'], max_power_w=MAX_POWER_W,
                external_load_confirmed=True, sequence=DIRECTIONS):
                load.set_input(True)
                io.enable_authorized = True
                try:
                    dev.output(channel, True)
                finally:
                    io.enable_authorized = False
                require_outputs(dev, {i: i == channel for i in (1,2,3)}, settle_timeout_s=.5)
                prior = snapshot(dev, load, meter, evidence, channel=channel, mode=mode, index=0)
                for index, direction in enumerate(DIRECTIONS, 1):
                    actual = dev.step_source(channel, p['kind'], direction)
                    evidence.add('powered_step_digital', channel=channel, mode=mode,
                                 index=index, direction=direction, setpoint=actual)
                    current = snapshot(dev, load, meter, evidence,
                                       channel=channel, mode=mode, index=index)
                    evidence.add('powered_step_delta', channel=channel, mode=mode,
                                 index=index, **check_transition(prior, current, mode, direction))
                    prior = current
        finally:
            io.step_window_authorized = False
        if io.step_writes != 1 or io.moves != [
                f'{"VOLT" if mode=="cv" else "CURR"}:{d.upper()}' for d in DIRECTIONS]:
            raise SafetyError('Wrong STEP mutation sequence')
        outcome, reason = 'PASS', 'four native powered STEP mutations; HW NOT_REVIEWED'
    except BaseException as exc:
        try: evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException: pass
        reason = f'{type(exc).__name__}: {exc}; manual OFF if communication uncertain'
    finally:
        evidence.cleanup = True
        failures = []
        if dev is not None and io is not None:
            failures = release_psu_and_load(dev, io, load, evidence)
            digital_off = not failures
        else:
            failures.append('PSU not opened; physical OFF required')
            if getattr(load, 'live', False):
                try:
                    if load.input_state() != 'OFF': load.set_input(False)
                    if load.input_state() != 'OFF': raise SafetyError('KEL INPUT OFF failed')
                except BaseException: failures.append('KEL INPUT OFF unknown')
        if failures or evidence.lost:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
            if evidence.lost: reason += '; incomplete evidence'
        elif outcome == 'PASS':
            try:
                io.cleanup = False
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                outcome, reason = 'ABORTED', reason + f'; LOCAL unconfirmed: {type(exc).__name__}'
        for item in (meter, load, transport):
            if item is not None:
                try: item.close()
                except BaseException as exc:
                    outcome, reason = 'ABORTED', reason + f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically verify all outputs OFF, KEL INPUT OFF and no energized load')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome, reason = 'ABORTED', reason + '; final physical OFF unconfirmed'
        try:
            evidence.add('outcome', outcome=outcome, channel=channel, mode=mode,
                         phase='POWERED_STEP_FOUR', digital_off_confirmed=digital_off,
                         physical_off_confirmed=physical_off, reason=reason,
                         hardware_review='PENDING')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R5-D2 CH{channel} {mode.upper()} {outcome}: {reason}')
    return outcome
