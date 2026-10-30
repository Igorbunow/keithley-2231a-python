"""R4.5-F-G: one operator-gated CH1 point, DMM DCI in series with ballast.

Fixed command envelope and existing driver/connection/evidence paths only.
A software timeout or SCPI current limit is NOT a hardware interlock.
"""
from __future__ import annotations

import time

from common import ShutdownEvidence, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_4_scalar_stage import R44ScalarScope
from r4_5_1n4933_forward_stage import bounded, _shutdown_psu
from r4_status_stage import _redacted_trace

PRESET_V = .90
BALLAST_OHM = 99.86
CURRENT_LIMIT_A = .010
MAX_POWERED_S = 90.0
INITIAL_TOKEN = ('CONFIRM R45FG DCI 1N4933 CH1_PLUS_R99.86_R_END_TO_DMM_FRONT_AMPS '
                 'DMM_FRONT_INPUT_LO_TO_DIODE_ANODE BANDED_CATHODE_TO_CH1_MINUS '
                 'NO_PARALLEL_DMM_WIRES PSU_ALL_OFF KEL_DISCONNECTED CH2_CH3_EMPTY '
                 'NO_EXTERNAL_SUPPLY NO_JUMPERS RESISTOR_GE_0.25W DMM_FUSE_OK')
POWER_TOKEN = ('CONFIRM R45FG ENABLE_CH1_ONLY V0.90 I0.010 VLIM1.05 '
               'DMM_FRONT_10MA_DCI_IN_SERIES SINGLE_POINT ONE_ENABLE')
FINAL_TOKEN = 'CONFIRM R45FG CH1=OFF CH2=OFF CH3=OFF DIODE_NOT_ENERGIZED'


class R45FGCurrentScope(R44ScalarScope):
    """Enforce fixed setpoint and prevent powered configuration writes."""
    WRITES = frozenset({
        'SYST:REM', 'SYST:LOC', 'OUTP 0',
        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
        'CHAN:OUTP 0', 'CHAN:OUTP 1',
        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
        'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
        'VOLT:LIM 1.05', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
        'VOLT 0.90', 'VOLT 1.00', 'CURR 0.010', 'CURR 0.100',
    })
    MAX_COMMANDS = 1450
    MAX_SECONDS = 240

    def _validate(self, command, query):
        if not query:
            channel_rules = {
                'VOLT:LIM 1.05': (1,), 'VOLT:LIM 1.50': (2, 3),
                'CURR 0.010': (1,), 'CURR 0.100': (2, 3),
                'VOLT 0.90': (1,), 'VOLT 1.00': (2, 3),
            }
            if command in channel_rules and self.selected not in channel_rules[command]:
                raise SafetyError('Wrong-channel fixed R45FG profile configuration')
            if self.power_on_commands and command not in (
                    'SYST:REM', 'OUTP 0', 'CHAN:OUTP 0', 'SYST:LOC',
                    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'):
                raise SafetyError('No configuration changes while DUT powered')
        return super()._validate(command, query)


def profile():
    return Profile('R4.5-F-G inline DMM current diagnostic single point', (
        ChannelProfile(1.05, .010, PRESET_V, CURRENT_LIMIT_A),
        ChannelProfile(1.50, .100, 1.00, .100),
        ChannelProfile(1.50, .100, 1.00, .100),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def validate_current_point(reading, dmm_current_a):
    """Independent DCI sample; limit-check only, NOT instrument calibration."""
    if not reading.enabled or reading.regulation != 'CV':
        raise SafetyError('CH1 not enabled in CV')
    psu_v = bounded(reading.voltage_v, 'PSU voltage', 0, 1.06)
    psu_i = bounded(reading.current_a, 'PSU current', 0, .012)
    dmm_i = bounded(dmm_current_a, 'direct DMM DCI', -.0002, .012)
    if abs(psu_v - PRESET_V) > .05:
        raise SafetyError('PSU voltage outside fixed preset tolerance')
    # Reject reversed leads and weak/open/short fixture conditions; preserve
    # the original reading first. Do not force either prior current hypothesis.
    if not .0002 <= dmm_i <= .008:
        raise SafetyError('DMM current outside single-point pilot envelope')
    if abs(psu_i - dmm_i) > .006:
        raise SafetyError('PSU/DMM current disagreement exceeds screening cap')
    if psu_v*psu_i > .012:
        raise SafetyError('PSU power outside fixed pilot cap')
    return dict(preset_v=PRESET_V, psu_voltage_v=psu_v,
                psu_current_a=psu_i, dmm_direct_current_a=dmm_i,
                psu_minus_dmm_current_a=psu_i-dmm_i,
                current_method='FRONT_10MA_DCI_AMPS_INPUT_LO_IN_SERIES',
                current_accuracy='UNQUALIFIED',
                burden_voltage='NOT_MEASURED',
                causal_conclusion='PENDING_INDEPENDENT_AUDIT')


def run_current(transport, meter, target_evidence, *, gate):
    evidence = ShutdownEvidence(target_evidence)
    if getattr(meter, 'evidence', None) is target_evidence:
        meter.evidence = evidence
    dev = io = None
    outcome, reason = 'ABORTED', 'Direct series DCI check incomplete'
    physical_off = False
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: R45FGCurrentScope(wire, evidence))
        io = dev.debug_transport
        evidence.add('psu_identity', response=dev.identity())
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('Independent physical topology required')
        meter.configure_current(0.01)  # FRONT DCI readback in shared adapter.
        require_outputs(dev)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        require_outputs(dev)
        off_i = bounded(meter.measure_current()['current_a'], 'OFF-state DMM DCI', -.0002, .0002)
        evidence.add('off_state_dmm_current', dmm_current_a=off_i)
        gate(POWER_TOKEN,
             'Verify PSU OFF before wiring; DMM FRONT AMPS inline after resistor, INPUT LO to diode anode; authorize CH1', io)
        evidence.add('operator_confirmation', gate='POWER', ballast_ohm=BALLAST_OHM)
        io.enable_authorized = True
        try:
            dev.output(1, True)
        finally:
            io.enable_authorized = False
        started = time.monotonic()
        if time.monotonic() - started >= MAX_POWERED_S:
            raise SafetyError('Host powered checkpoint expired before measurement')
        require_outputs(dev, {1: True, 2: False, 3: False})
        reading = dev.read_channel(1)
        psu_time = time.monotonic()
        if not reading.enabled or reading.regulation != 'CV':
            raise SafetyError('Unexpected CH1 regulation state')
        bounded(reading.voltage_v, 'PSU voltage', 0, 1.06)
        bounded(reading.current_a, 'PSU current', 0, .012)
        dmm = meter.measure_current()
        dmm_time = time.monotonic()
        # Preserve raw measurements prior to any acceptance check.
        evidence.add('dci_series_raw_sample', psu_voltage_v=reading.voltage_v,
                     psu_current_a=reading.current_a, regulation=reading.regulation,
                     enabled=reading.enabled, dmm_direct_current_a=dmm['current_a'],
                     psu_monotonic_s=psu_time, dmm_monotonic_s=dmm_time,
                     note='UNVALIDATED_SEQUENTIAL_SAMPLES')
        point = validate_current_point(reading, dmm['current_a'])
        preset = bounded(float(dev.diagnostic_query('VOLT?')), 'digital preset', 0, 1.06)
        if abs(preset - PRESET_V) > .00049:
            raise SafetyError('CH1 digital preset changed unexpectedly')
        if time.monotonic() - started >= MAX_POWERED_S:
            raise SafetyError('Host powered checkpoint exceeded; not hardware watchdog')
        evidence.add('dci_series_point', **point,
                     psu_dmm_gap_ms=(dmm_time-psu_time)*1000,
                     powered_duration_s=time.monotonic()-started,
                     qualification='SINGLE_SERIES_DCI_DIAGNOSTIC_ONLY')
        outcome, reason = 'PASS', 'one direct DMM series-current sample; independent audit pending'
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
        for item in (meter, transport):
            if item is not None:
                try: item.close()
                except BaseException as exc:
                    outcome, reason = 'ABORTED', reason+f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically verify all PSU outputs OFF; no live rewiring')
            evidence.add('operator_confirmation', gate='FINAL')
            physical_off = True
        except BaseException:
            outcome, reason = 'ABORTED', reason+'; physical OFF not confirmed'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         physical_off_confirmed=physical_off, status='HW NOT_REVIEWED')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-F-G {outcome}: {reason}')
    return outcome
