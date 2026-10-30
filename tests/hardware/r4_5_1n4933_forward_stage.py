"""R4.5-F-D: one guarded CH1 1N4933 forward screening run.

No load adapter, generic sweep, manual SCPI override, or background interlock.
The physical 100-ohm ballast is the current-limiting element; host checks are
checkpoints, not protection against a lost serial link or miswired fixture.
"""
from __future__ import annotations

import math
import time

from common import ShutdownEvidence, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_4_scalar_stage import R44ScalarScope
from r4_status_stage import _redacted_trace

VOLTAGES = (.80, .90, 1.00, .90, .80)
CURRENT_LIMIT_A = .020
MAX_POWERED_S = 90.0
INITIAL_TOKEN = ('CONFIRM R45FD 1N4933 CH1_PLUS_RESISTOR_ANODE CATHODE_BANDED_CH1_MINUS '
                 'DMM_FRONT_ACROSS_DIODE KEL_DISCONNECTED CH2_CH3_EMPTY ALL_OUTPUTS_OFF '
                 'NO_JUMPERS NO_EXTERNAL_POWER RESISTOR_GE_0.25W R99.86_MEASURED_OFF')
POWER_TOKEN = ('CONFIRM R45FD POWER_CH1_ONLY V0.80_TO_1.00 I0.020 '
               'R99.86 DMM_FRONT FORWARD_ONLY FIVE_POINTS ONE_ENABLE')
FINAL_TOKEN = 'CONFIRM R45FD CH1=OFF CH2=OFF CH3=OFF DIODE_NOT_ENERGIZED'


class R45FDDiodeScope(R44ScalarScope):
    """Fixed command envelope, including ordering of all powered setpoints."""
    WRITES = frozenset({
        'SYST:REM', 'SYST:LOC', 'OUTP 0',
        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
        'CHAN:OUTP 0', 'CHAN:OUTP 1',
        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
        'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
        'VOLT:LIM 1.05', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
        'VOLT 0.80', 'VOLT 0.90', 'VOLT 1.00',
        'CURR 0.020', 'CURR 0.100',
    })
    MAX_COMMANDS = 3200
    MAX_SECONDS = 240

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.power_gate_approved = False
        self.moves = 0

    def _validate(self, command, query):
        if not query:
            channel_rules = {
                'VOLT:LIM 1.05': (1,), 'VOLT:LIM 1.50': (2, 3),
                'CURR 0.020': (1,), 'CURR 0.100': (2, 3),
            }
            if command in channel_rules and self.selected not in channel_rules[command]:
                raise SafetyError('Wrong-channel fixed profile configuration')
            if command == 'CHAN:OUTP 1':
                if not self.power_gate_approved or self.selected != 1:
                    raise SafetyError('DUT power gate or CH1 selection missing')
            if command in ('VOLT 0.80', 'VOLT 0.90', 'VOLT 1.00'):
                if self.selected != 1:
                    # CH2/CH3 initial 1.00V is required by the fixed profile.
                    if command != 'VOLT 1.00' or self.power_on_commands:
                        raise SafetyError('Wrong-channel DUT voltage change')
                elif self.power_on_commands:
                    if (not self.power_gate_approved or self.cleanup or
                            self.moves >= 4 or
                            command != f'VOLT {VOLTAGES[self.moves+1]:.2f}'):
                        raise SafetyError('DUT powered voltage sequence violation')
                    self.moves += 1
                elif command != 'VOLT 0.80':
                    raise SafetyError('CH1 initial voltage must be 0.80V')
            elif self.power_on_commands and command not in (
                    'SYST:REM', 'OUTP 0', 'CHAN:OUTP 0', 'SYST:LOC',
                    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'):
                raise SafetyError('Powered configuration changes forbidden')
        return super()._validate(command, query)


def profile():
    return Profile('R4.5-F-D 1N4933 limited forward screening', (
        ChannelProfile(1.05, .020, .80, CURRENT_LIMIT_A),
        ChannelProfile(1.50, .100, 1.00, .100),
        ChannelProfile(1.50, .100, 1.00, .100),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def bounded(value, label, minimum, maximum):
    if type(value) not in (float, int) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise SafetyError(f'Invalid {label}: outside {minimum:g}..{maximum:g}')
    return float(value)


def validate_point(reading, diode_voltage, preset, actual_ohm, *, low_current_endpoint=False):
    if not reading.enabled or reading.regulation != 'CV':
        raise SafetyError('CH1 OFF or not in CV')
    psu_v = bounded(reading.voltage_v, 'PSU voltage', 0, 1.06)
    psu_a = bounded(reading.current_a, 'PSU current', 0, CURRENT_LIMIT_A + .002)
    diode_v = bounded(diode_voltage, 'DMM diode voltage', -.02, 1.06)
    if abs(psu_v - preset) > .050:
        raise SafetyError('PSU analog voltage disagrees with preset')
    if not .25 <= diode_v <= psu_v + .015:
        raise SafetyError('DMM voltage inconsistent with forward-biased diode')
    resistor_drop_v = psu_v - diode_v
    screening_current_a = resistor_drop_v / actual_ohm
    # The 0.80 V endpoints are exploratory: a diode can have negligible
    # conduction here. A positive estimate below 150 uA is NOT a calibrated
    # current measurement (PSU and DMM voltages are sequential and independent).
    # Safety is provided by the physical ballast and upper bounds, not by
    # requiring a minimum DUT current at the first voltage level.
    minimum_a = 0.0 if low_current_endpoint else .00015
    if not minimum_a <= screening_current_a <= .0106:
        raise SafetyError('DUT current outside reviewed resistor screening range')
    if abs(psu_a - screening_current_a) > .006:
        raise SafetyError('PSU current and resistor-estimated current disagree')
    if psu_v * psu_a > .022:
        raise SafetyError('PSU measured power exceeds limit')
    return dict(preset_v=preset, psu_voltage_v=psu_v, psu_current_a=psu_a,
                diode_voltage_v=diode_v, resistor_drop_v=resistor_drop_v,
                screening_current_a=screening_current_a,
                diode_estimated_power_w=diode_v*screening_current_a,
                current_method='SEQUENTIAL_PSU_V_MINUS_DMM_DIODE_V_OVER_MEASURED_R',
                independent_current_sensor=False,
                weak_current_unquantified=(screening_current_a < .00015))


def validate_segment(previous, current, direction):
    sign = 1 if direction == 'up' else -1
    di = sign*(current['screening_current_a']-previous['screening_current_a'])
    dv = sign*(current['diode_voltage_v']-previous['diode_voltage_v'])
    if not .0001 <= di <= .002 or not .002 <= dv <= .12:
        raise SafetyError('DUT segment did not follow forward source voltage')
    return dict(direction=direction, signed_current_delta_a=di, signed_diode_delta_v=dv)


def validate_return(points):
    if len(points) != 5:
        raise SafetyError('Incomplete DUT curve')
    for a, b in ((0, 4), (1, 3)):
        if (abs(points[a]['diode_voltage_v']-points[b]['diode_voltage_v']) > .040 or
                abs(points[a]['screening_current_a']-points[b]['screening_current_a']) > .001):
            raise SafetyError('Forward/reverse-run return differs beyond screening margin')
    return dict(return_diode_error_v=abs(points[0]['diode_voltage_v']-points[4]['diode_voltage_v']),
                return_screening_current_error_a=abs(points[0]['screening_current_a']-
                                                     points[4]['screening_current_a']))


def _read_point(dev, meter, evidence, *, index, actual_ohm):
    require_outputs(dev, {1: True, 2: False, 3: False})
    reading = dev.read_channel(1)
    psu_t = time.monotonic()
    # Check PSU immediately before asking the meter; reject faults promptly.
    if not reading.enabled or reading.regulation != 'CV':
        raise SafetyError('PSU not in CV with CH1 enabled')
    bounded(reading.voltage_v, 'PSU voltage', 0, 1.06)
    bounded(reading.current_a, 'PSU current', 0, .022)
    if abs(reading.voltage_v - VOLTAGES[index]) > .050:
        raise SafetyError('PSU voltage outside reviewed setpoint tolerance')
    dmm = meter.measure()
    dmm_t = time.monotonic()
    # Preserve the evidence for the exact sample which caused a safety abort.
    # This record is not an accepted point and must not be used as a HW PASS.
    evidence.add('diode_iv_raw_sample', index=index, preset_v=VOLTAGES[index],
                 psu_voltage_v=reading.voltage_v, psu_current_a=reading.current_a,
                 regulation=reading.regulation, enabled=reading.enabled,
                 dmm_diode_voltage_v=dmm['voltage_v'],
                 resistor_measured_ohm=actual_ohm,
                 psu_sample_monotonic_s=psu_t,
                 dmm_sample_monotonic_s=dmm_t,
                 interpretation='UNVALIDATED_SEQUENTIAL_VOLTAGE_SAMPLE')
    result = validate_point(reading, dmm['voltage_v'], VOLTAGES[index], actual_ohm,
                            low_current_endpoint=(index in (0, 4)))
    digital = bounded(float(dev.diagnostic_query('VOLT?')), 'digital preset', 0, 1.06)
    if abs(digital - VOLTAGES[index]) > .00049:
        raise SafetyError('Digital voltage preset mismatch')
    result.update(index=index, digital_v=digital,
                  timestamps_monotonic_s=dict(psu=psu_t, dmm=dmm_t),
                  sampling='SEQUENTIAL_NOT_SYNCHRONIZED')
    evidence.add('diode_iv_point', **result)
    if result['weak_current_unquantified']:
        evidence.add('diode_iv_low_current_caution', index=index,
                     screening_current_a=result['screening_current_a'],
                     meaning='BELOW_150UA_NOT_QUANTITATIVE; require segment growth')
    return result


def _shutdown_psu(dev, io, evidence):
    if dev is None or io is None or not io.live:
        evidence.add('shutdown_unknown', reason='PSU not opened or serial link uncertain')
        return ['PSU output state UNKNOWN; physical OFF required']
    try:
        io.cleanup = True
        dev.emergency_off()
        require_outputs(dev)
        evidence.add('shutdown_psu', state='VERIFIED_OFF_BY_READBACK')
        return []
    except BaseException as exc:
        try: evidence.add('shutdown_unknown', reason=type(exc).__name__)
        except BaseException: pass
        return [f'PSU OFF unconfirmed ({type(exc).__name__}); physical OFF required']


def run_forward(transport, meter, target_evidence, *, actual_ohm, gate):
    """One operator-gated session; no KEL connection, reconnect or retry."""
    evidence = ShutdownEvidence(target_evidence)
    if getattr(meter, 'evidence', None) is target_evidence:
        meter.evidence = evidence
    dev = io = None
    outcome, reason = 'ABORTED', 'DUT forward screening incomplete'
    physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: R45FDDiodeScope(wire, evidence))
        io = dev.debug_transport
        evidence.add('psu_identity', response=dev.identity())
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('Independent topology required')
        meter.configure_voltage(10)
        require_outputs(dev)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        require_outputs(dev)
        baseline = bounded(meter.measure()['voltage_v'], 'OFF-state DMM voltage', -.05, .05)
        evidence.add('off_state_diode_voltage', dmm_v=baseline)
        gate(POWER_TOKEN, 'Recheck diode polarity, series resistor >=0.25W and meter FRONT; authorize CH1', io)
        evidence.add('operator_confirmation', gate='POWER', measured_resistor_ohm=actual_ohm)
        io.power_gate_approved = True
        io.enable_authorized = True
        try:
            dev.output(1, True)
        finally:
            io.enable_authorized = False
        started = time.monotonic()
        observations = []
        for index, preset in enumerate(VOLTAGES):
            if time.monotonic() - started >= MAX_POWERED_S:
                raise SafetyError('Host-clock powered checkpoint exceeded 90 seconds')
            if index:
                # Same guarded set_channel route used by normal public API.
                dev.set_channel(1, voltage=preset)
            point = _read_point(dev, meter, evidence, index=index, actual_ohm=actual_ohm)
            if index:
                direction = 'up' if index <= 2 else 'down'
                evidence.add('diode_iv_segment', index=index,
                             **validate_segment(observations[-1], point, direction))
            observations.append(point)
            if time.monotonic() - started >= MAX_POWERED_S:
                raise SafetyError('Host-clock powered checkpoint exceeded 90 seconds')
        evidence.add('diode_iv_summary', **validate_return(observations),
                     powered_duration_s=time.monotonic()-started,
                     qualification='FORWARD_STEADY_STATE_SCREENING_ONLY')
        outcome, reason = 'PASS', 'five diode forward points; independent hardware audit pending'
    except BaseException as exc:
        try: evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException: pass
        reason = f'{type(exc).__name__}: {exc}; physical OFF required if uncertain'
    finally:
        evidence.cleanup = True
        failures = _shutdown_psu(dev, io, evidence)
        if failures or evidence.lost:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
            if evidence.lost: reason += '; evidence incomplete'
        elif outcome == 'PASS':
            try:
                io.cleanup = False
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                outcome, reason = 'ABORTED', reason+f'; LOCAL unconfirmed: {type(exc).__name__}'
        for instrument in (meter, transport):
            if instrument is not None:
                try: instrument.close()
                except BaseException as exc:
                    outcome, reason = 'ABORTED', reason+f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically verify CH1/CH2/CH3 OFF; if link uncertain use front panel')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome, reason = 'ABORTED', reason+'; final physical OFF not confirmed'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         physical_off_confirmed=physical_off, status='HW NOT_REVIEWED')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-F-D {outcome}: {reason}')
    return outcome
