"""R4.5-F-A: fixed low-power CV I-V pilot on the known 40-ohm KEL103 load.

Five sequential measurements at 3.00, 3.10, 3.20, 3.10, 3.00 V.
No arbitrary sweep, DUT, auto-repeat, hardware triggering or general powered API.
"""
from __future__ import annotations

import time

from common import ShutdownEvidence, require_outputs, release_psu_and_load
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_status_stage import _redacted_trace
from r4_5_powered_step_stage import finite
from r4_5_powered_series_stage import R45ESeriesScope, DIRECTIONS, OFFSETS

START_V = 3.00
STEP_V = .100
LOAD_OHM = 40.0
CURRENT_LIMIT_A = .150
POWER_LIMIT_W = .60
MAX_POWERED_SECONDS = 300.0  # checkpoint budget, NOT an asynchronous interlock
EXPECTED_V = (3.00, 3.10, 3.20, 3.10, 3.00)
INITIAL_TOKEN = ('CONFIRM R45FIVA INDEPENDENT CH1_KEL103_DMM_FRONT_PARALLEL '
                 'CH2_CH3_EMPTY ALL_PSU_OFF KEL_INPUT_OFF NO_JUMPERS NO_DUT RECALL_OFF')
POWER_TOKEN = ('CONFIRM R45FIVA CV CH1_ONLY START_3.00 STEP_0.100 '
               'KEL_CR40 DMM_FRONT MAX_P0.60 UP_UP_DOWN_DOWN FIVE_POINTS')
FINAL_TOKEN = 'CONFIRM R45FIVA CH1=OFF CH2=OFF CH3=OFF KEL103=OFF'


class R45FIVScope(R45ESeriesScope):
    """An additional literal voltage preset and STEP, not a raw SCPI escape."""
    WRITES = R45ESeriesScope.WRITES | {'VOLT 3.00', 'VOLT:STEP 0.100'}
    MAX_COMMANDS = 5200
    MAX_SECONDS = 660

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence, mode='cv')

    def _validate(self, command, query):
        # The reused R4.4 scope allows the earlier 3.30V profile literal.
        # Do not let that old permit authorize new R4.5-F mutations.
        if not query and command == 'VOLT 3.30':
            raise SafetyError('Old fixed 3.30V profile is not an I-V permit')
        if not query and command in ('VOLT 3.00', 'VOLT 1.00', 'CURR 0.150',
                                     'CURR 0.100', 'VOLT:LIM 3.50',
                                     'VOLT:LIM 1.50'):
            allowed_channel = (1 if command in ('VOLT 3.00', 'CURR 0.150',
                                                'VOLT:LIM 3.50') else (2, 3))
            if (self.selected not in ((allowed_channel,) if type(allowed_channel) is int
                                      else allowed_channel) or self.power_on_commands):
                raise SafetyError('Wrong-channel or powered profile rewrite forbidden')
        if not query and self.power_on_commands and command in (
                'VOLT 0', 'CURR 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
                'OUTP:TRAC 0', 'VOLT:LIM:STAT 1', 'OUTP:TIM:STAT 0'):
            raise SafetyError('Configuration writes forbidden while CH1 powered')
        if not query and command == 'VOLT:STEP 0.010':
            raise SafetyError('R4.5-F fixed 100mV STEP; old 10mV route disallowed')
        if not query and command == 'VOLT:STEP 0.100':
            if (not self.step_window_authorized or self.cleanup or self.selected != 1
                    or self.step_writes or self.moves or self.power_on_commands):
                raise SafetyError('100mV STEP is allowed once while all outputs OFF')
            if any(self.outputs.values()):
                raise SafetyError('STEP configuration prohibited while output ON')
            self.step_writes += 1
            return
        return super()._validate(command, query)


def profile():
    return Profile('R4.5-F-A fixed resistive I-V pilot', (
        ChannelProfile(3.50, .150, START_V, CURRENT_LIMIT_A),
        ChannelProfile(1.50, .100, 1.00, .100),
        ChannelProfile(1.50, .100, 1.00, .100),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def measure_iv(dev, load, meter, evidence, *, index):
    """Sequential U/I/P readings from all instruments, with independent units."""
    expected = EXPECTED_V[index]
    if load.input_state() != 'ON' or load.query(':FUNC?').strip().upper() != 'CR':
        raise SafetyError('KEL103 INPUT or CR mode changed')
    resistance = finite(load.query(':RES?').strip().upper().removesuffix('OHM'), 'KEL CR', 2000)
    if abs(resistance - LOAD_OHM) > .001:
        raise SafetyError('KEL CR40 readback changed')
    require_outputs(dev, {1: True, 2: False, 3: False})
    read = dev.read_channel(1)
    psu_s = time.monotonic()
    if not read.enabled or read.regulation != 'CV':
        raise SafetyError('CH1 no longer reports CV and ON')
    psu_v = finite(read.voltage_v, 'Keithley voltage', 3.55)
    psu_a = finite(read.current_a, 'Keithley current', .165)
    if psu_v * psu_a > POWER_LIMIT_W:
        raise SafetyError('Keithley U x I above approved power')
    kel = load.measurements()
    kel_s = time.monotonic()
    kel_v = finite(kel['kel103_voltage_v'], 'KEL voltage', 3.55)
    kel_a = finite(kel['kel103_current_a'], 'KEL current', .165)
    kel_w = finite(kel['kel103_power_w'], 'KEL power', POWER_LIMIT_W)
    dmm = meter.measure()
    dmm_s = time.monotonic()
    dmm_v = finite(dmm['voltage_v'], 'DMM voltage', 3.55)
    if (abs(dmm_v - expected) > .050 or abs(psu_v - dmm_v) > .080
            or abs(kel_v - dmm_v) > .080 or abs(psu_a - kel_a) > .030
            or abs(kel_a - dmm_v / resistance) > .006
            or abs(kel_w - kel_v * kel_a) > .020):
        raise SafetyError('Physical I-V point does not agree with approved CR load')
    power = finite(dev.measure_power(1), 'Keithley MEAS:POW?', POWER_LIMIT_W)
    power_s = time.monotonic()
    if abs(power - psu_v * psu_a) > .045 or abs(power - kel_w) > .045:
        raise SafetyError('Power disagreement between independent instruments')
    actual_preset = finite(dev.diagnostic_query('VOLT?'), 'Keithley digital preset', 3.50)
    if abs(actual_preset - expected) > .00049:
        raise SafetyError('Digital voltage preset disagrees with I-V point')
    result = dict(index=index, commanded_v=expected, digital_v=actual_preset,
                  psu_v=psu_v, psu_a=psu_a, psu_meas_w=power,
                  kel_v=kel_v, kel_a=kel_a, kel_w=kel_w, dmm_v=dmm_v,
                  kel_effective_ohm=kel_v / kel_a if kel_a else None,
                  timestamps_monotonic_s=dict(psu=psu_s, kel=kel_s, dmm=dmm_s, power=power_s),
                  sampling='SEQUENTIAL_NOT_SYNCHRONIZED')
    evidence.add('iv_point', **result)
    return result


def validate_iv(points):
    if len(points) != len(EXPECTED_V):
        raise SafetyError('Incomplete I-V point collection')
    segments = []
    for index, direction in enumerate(DIRECTIONS):
        a, b = points[index], points[index+1]
        sign = 1 if direction == 'up' else -1
        du = sign * (b['dmm_v'] - a['dmm_v'])
        di = sign * (b['kel_a'] - a['kel_a'])
        if not (.070 <= du <= .130 and .0012 <= di <= .0045):
            raise SafetyError(f'Physical V-I segment {index} did not follow STEP')
        segments.append(dict(index=index+1, direction=direction,
                             signed_dmm_delta_v=du, signed_kel_delta_a=di))
    if (abs(points[0]['dmm_v'] - points[-1]['dmm_v']) > .008 or
            abs(points[0]['kel_a'] - points[-1]['kel_a']) > .002 or
            abs(points[1]['dmm_v'] - points[3]['dmm_v']) > .008 or
            abs(points[1]['kel_a'] - points[3]['kel_a']) > .002):
        raise SafetyError('I-V reverse branch does not return to measured levels')
    # A resistive load is the deliberate reference, not a diode/DUT characteristic.
    slope = (points[2]['kel_a'] - points[0]['kel_a']) / (points[2]['dmm_v'] - points[0]['dmm_v'])
    if not .018 <= slope <= .032:
        raise SafetyError('Measured I-V slope incompatible with CR40 reference')
    return dict(segments=segments, measured_conductance_s=slope,
                equivalent_ohms=1. / slope,
                dmm_return_error_v=abs(points[-1]['dmm_v']-points[0]['dmm_v']),
                kel_return_error_a=abs(points[-1]['kel_a']-points[0]['kel_a']))


def run_iv(transport, load, meter, target_evidence, *, gate):
    evidence = ShutdownEvidence(target_evidence)
    if getattr(load, 'evidence', None) is target_evidence: load.evidence = evidence
    if getattr(meter, 'evidence', None) is target_evidence: meter.evidence = evidence
    dev = io = None
    outcome, reason = 'ABORTED', 'R4.5-F I-V incomplete; physical OFF required'
    physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: R45FIVScope(wire, evidence))
        io = dev.debug_transport
        evidence.add('psu_identity', response=dev.identity())
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('Load identity or initial OFF mismatch')
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError('Independent topology required')
        meter.configure_voltage(10)
        load.configure_cr(LOAD_OHM)
        require_outputs(dev)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL became ON before operator gate')
        gate(POWER_TOKEN, 'Inspect CR40 / DMM FRONT DCV, authorize fixed I-V pilot', io)
        evidence.add('operator_confirmation', gate='POWER', experiment='R45FIVA')
        io.step_window_authorized = True
        try:
            with dev.powered_step_qualification(1, 'voltage', STEP_V, 'up',
                    expected_mode='CV', max_power_w=POWER_LIMIT_W,
                    external_load_confirmed=True, sequence=DIRECTIONS):
                if load.input_state() != 'OFF':
                    raise SafetyError('KEL was unexpectedly enabled')
                load.set_input(True)
                io.enable_authorized = True
                try: dev.output(1, True)
                finally: io.enable_authorized = False
                power_start = time.monotonic()
                require_outputs(dev, {1: True, 2: False, 3: False}, settle_timeout_s=.5)
                observations = []
                for index in range(len(EXPECTED_V)):
                    if time.monotonic()-power_start > MAX_POWERED_SECONDS:
                        raise SafetyError('Powered observation exceeded 300s checkpoint budget')
                    if index:
                        actual = dev.step_source(1, 'voltage', DIRECTIONS[index-1])
                        evidence.add('iv_digital_step', index=index,
                                     direction=DIRECTIONS[index-1], readback=actual)
                    point = measure_iv(dev, load, meter, evidence, index=index)
                    observations.append(point)
                    if time.monotonic()-power_start > MAX_POWERED_SECONDS:
                        raise SafetyError('Powered observation exceeded 300s checkpoint budget')
                    if index:
                        # Stop at the FIRST wrong analog segment, not after completing the curve.
                        prior = observations[-2]
                        signed = 1 if DIRECTIONS[index-1] == 'up' else -1
                        du = signed*(point['dmm_v']-prior['dmm_v'])
                        di = signed*(point['kel_a']-prior['kel_a'])
                        if not (.070 <= du <= .130 and .0012 <= di <= .0045):
                            raise SafetyError('I-V physical STEP segment failed')
                evidence.add('iv_characterization', **validate_iv(observations),
                             powered_duration_s=time.monotonic()-power_start,
                             energy_upper_bound_j=(time.monotonic()-power_start)*POWER_LIMIT_W,
                             energy_note='host-clock cap at checkpoints, no hard electrical energy cutoff')
        finally:
            io.step_window_authorized = False
        outcome, reason = 'PASS', 'fixed low-power resistive I-V reference; independent review pending'
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
                    if load.input_state() != 'OFF': raise SafetyError('KEL did not shut off')
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
                outcome, reason = 'ABORTED', reason+f'; LOCAL unconfirmed: {type(exc).__name__}'
        for instrument in (meter, load, transport):
            if instrument is not None:
                try: instrument.close()
                except BaseException as exc:
                    outcome, reason = 'ABORTED', reason+f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically confirm PSU CH1/CH2/CH3 and KEL INPUT OFF')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome, reason = 'ABORTED', reason+'; final physical OFF not confirmed'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         physical_off_confirmed=physical_off, status='HW NOT_REVIEWED')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-F-A {outcome}: {reason}')
    return outcome
