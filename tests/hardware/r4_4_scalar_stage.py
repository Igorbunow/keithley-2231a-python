"""R4.4-C: operator-gated, CH1-only scalar measurements with KEL103+DMM7510.

The stage uses the production Keithley driver. No I/O is performed on import.
FETC freshness is NOT inferred from numerical agreement.
"""
from __future__ import annotations

import math
import time

from common import (StageAdapter, ShutdownEvidence, operator_gate,
                    require_outputs, release_psu_and_load)
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_status_stage import _redacted_trace

STAGE = 'R44SCALAR'
VOLTAGE_V = 3.30
CURRENT_LIMIT_A = 0.150
LOAD_OHM = 40.0
MAX_POWER_W = 0.60
INITIAL_TOKEN = ('CONFIRM R44SCALAR INDEPENDENT ALL_PSU_OFF KEL_INPUT_OFF '
                 'CH1_KEL_CR40 DMM_FRONT_PARALLEL_CH1 CH2_CH3_OPEN '
                 'NO_JUMPERS NO_DUT RECALL_OFF')
ENABLE_TOKEN = ('CONFIRM R44SCALAR ENABLE_CH1_ONLY V3.30 I0.150 '
                'KEL_CR40 DMM_FRONT MAXP0.60')
FINAL_TOKEN = 'CONFIRM R44SCALAR CH1=OFF CH2=OFF CH3=OFF KEL103=OFF'


class R44ScalarScope(StageAdapter):
    """Minimal fixed SCPI envelope; no arbitrary user-settable output values."""
    READS = frozenset({
        '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
        'OUTP:TRAC?', 'INST:NSEL?', 'CHAN:OUTP?', 'VOLT?', 'CURR?',
        'VOLT:LIM?', 'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?',
        'MEAS:VOLT?', 'MEAS:CURR?', 'MEAS:POW?',
        'FETC:VOLT?', 'FETC:CURR?', 'FETC:POW?',
    } | {f'STAT:{kind}:INST:ISUM{ch}:COND?'
         for kind in ('OPER', 'QUES') for ch in (1, 2, 3)})
    WRITES = frozenset({
        'SYST:REM', 'SYST:LOC', 'OUTP 0',
        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
        'CHAN:OUTP 0', 'CHAN:OUTP 1',
        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
        'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
        'VOLT:LIM 3.50', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
        'VOLT 3.30', 'VOLT 1.00', 'CURR 0.150', 'CURR 0.100',
    })
    MAX_COMMANDS = 900
    MAX_SECONDS = 240
    SHUTDOWN_RESERVE = 96

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.enable_authorized = False
        self.power_on_commands = 0

    def _validate(self, command, query):
        if command not in (self.READS if query else self.WRITES):
            raise SafetyError(f'R4.4 scalar forbidden SCPI: {command}')
        if command == 'CHAN:OUTP 1':
            if not self.enable_authorized or self.cleanup or self.selected != 1 or self.power_on_commands:
                raise SafetyError('CH1 power ON requires one current operator authorization')
            self.power_on_commands += 1
        if command == 'SYST:LOC' and (any(self.outputs.values()) or self.cleanup):
            raise SafetyError('LOCAL prohibited before verified normal OFF')


def _profile():
    return Profile('R4.4-C scalar CH1', (
        ChannelProfile(3.50, 0.150, VOLTAGE_V, CURRENT_LIMIT_A),
        ChannelProfile(1.50, 0.100, 1.00, 0.100),
        ChannelProfile(1.50, 0.100, 1.00, 0.100),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def _number(value, name, *, maximum=None):
    try:
        v = float(value)
        if not math.isfinite(v) or v < 0 or (maximum is not None and v > maximum):
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise SafetyError(f'Invalid or overrange {name}: {value!r}') from None
    return v


def validate_measurements(reading, kel, dmm, scalar, fetched):
    """Strict live power envelope, conservative tolerances, no FETC freshness claim."""
    pu = _number(reading.voltage_v, 'PSU U', maximum=3.55)
    pi = _number(reading.current_a, 'PSU I', maximum=0.165)
    pp = _number(reading.power_w, 'PSU U*I', maximum=MAX_POWER_W)
    ku = _number(kel['kel103_voltage_v'], 'KEL U', maximum=3.55)
    ki = _number(kel['kel103_current_a'], 'KEL I', maximum=0.165)
    kp = _number(kel['kel103_power_w'], 'KEL P', maximum=MAX_POWER_W)
    du = _number(dmm['voltage_v'], 'DMM U', maximum=3.55)
    mp = _number(scalar, 'MEAS:POW', maximum=MAX_POWER_W)
    fv, fi, fp = (_number(fetched[k], f'FETC {k}', maximum=limit)
                  for k, limit in (('voltage', 3.55), ('current', .165), ('power', MAX_POWER_W)))
    if (abs(pu - VOLTAGE_V) > .20 or abs(ku - VOLTAGE_V) > .20 or
            abs(pu - ku) > .08 or abs(pu - du) > .08 or abs(ku - du) > .08 or
            abs(pi - ki) > .030 or abs(pp - pu*pi) > .015 or
            abs(kp - ku*ki) > .045 or abs(mp - pu*pi) > .080 or
            abs(mp - kp) > .080):
        raise SafetyError('Independent scalar power/voltage/current mismatch')
    return dict(psu_voltage_v=pu, psu_current_a=pi, psu_derived_power_w=pp,
                kel_voltage_v=ku, kel_current_a=ki, kel_power_w=kp,
                dmm_voltage_v=du, meas_power_w=mp,
                fetch_voltage_v=fv, fetch_current_a=fi, fetch_power_w=fp,
                fetch_is_fresh='UNVERIFIED',
                fetch_agrees_with_live=(abs(fv-pu)<=.08 and abs(fi-pi)<=.030
                                         and abs(fp-mp)<=.080))


def run_scalar(transport, load, meter, evidence, *, gate=operator_gate):
    """One fixed CV point, externally gated; no repeat or connection fallback."""
    original_evidence = evidence
    evidence = ShutdownEvidence(original_evidence)
    # Both auxiliary adapters must share the shutdown-only evidence sink too:
    # failure to append JSONL must not prevent a physically possible OFF.
    if getattr(load, 'evidence', None) is original_evidence:
        load.evidence = evidence
    if getattr(meter, 'evidence', None) is original_evidence:
        meter.evidence = evidence
    dev = io = None
    started_profile = False
    outcome, reason = 'ABORTED', 'R4.4-C incomplete; physical OFF required'
    try:
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: R44ScalarScope(wire, evidence))
        io = dev.debug_transport
        identity = dev.identity()
        evidence.add('psu_identity', response=identity)
        if 'KEL103' not in load.identify().upper():
            raise SafetyError('Unexpected KEL103 identity')
        if load.input_state() != 'OFF':
            raise SafetyError('Load must be OFF at entry')
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError('Independent topology required')
        # DMM setup may *RST the DMM only, never the Keithley source.
        meter.configure_voltage(10)
        load.configure_cr(LOAD_OHM)
        require_outputs(dev)
        started_profile = True
        dev.apply_profile(_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        if load.input_state() != 'OFF':
            raise SafetyError('KEL input changed before authorized ON')
        gate(ENABLE_TOKEN, 'Confirm CH1 polarity, meter in parallel, expected 0.27 W; enable CH1 only', io)
        evidence.add('operator_confirmation', gate='ENABLE')
        load.set_input(True)
        io.enable_authorized = True
        try:
            dev.output(1, True)
        finally:
            io.enable_authorized = False
        require_outputs(dev, {1: True, 2: False, 3: False}, settle_timeout_s=.5)
        reading = dev.read_channel(1)
        measured_at = {'psu_read_channel_s': time.monotonic()}
        kel = load.measurements()
        measured_at['kel103_s'] = time.monotonic()
        dmm = meter.measure()
        measured_at['dmm7510_s'] = time.monotonic()
        # A finite but wrong DMM reading (e.g. 9 V on the 10 V range)
        # must abort before additional scalar/FETC traffic on live outputs.
        # Range-only rejection in DMM7510.measure() is not enough.
        du = _number(dmm['voltage_v'], 'DMM U', maximum=3.55)
        if (abs(du - reading.voltage_v) > .08 or
                abs(du - kel['kel103_voltage_v']) > .08):
            raise SafetyError('DMM7510 voltage disagrees with PSU/KEL: '
                              f'DMM={du:.6g} V, PSU={reading.voltage_v:.6g} V, '
                              f'KEL={kel["kel103_voltage_v"]:.6g} V')
        scalar = dev.measure_power(1)
        measured_at['meas_power_s'] = time.monotonic()
        fetched = {}
        for kind in ('voltage', 'current', 'power'):
            fetched[kind] = dev.fetch_measurement(1, kind)
            measured_at[f'fetch_{kind}_s'] = time.monotonic()
        values = validate_measurements(reading, kel, dmm, scalar, fetched)
        evidence.add('scalar_measurements', **values, timestamps_monotonic_s=measured_at,
                     firmware=identity.split(',')[-1].strip(),
                     note='Sequential observations; FETC age/synchrony UNKNOWN')
        print('R4.4-C:', values)
        outcome, reason = 'PASS', 'one low-power CH1 point; FETC freshness unverified'
    except BaseException as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        reason = f'{type(exc).__name__}: {exc}; manual OFF if link uncertain'
    finally:
        evidence.cleanup = True  # Preserve independent OFF attempts on evidence write failure.
        failures = []
        # If profile modification started, independently stop the PSU and load
        # on links still trustworthy; never retransmit on uncertain transport.
        if dev is not None and started_profile:
            failures = release_psu_and_load(dev, io, load, evidence)
        else:
            failures = ['profile not started; physical OFF confirmation required']
        if failures or evidence.lost:
            outcome = 'ABORTED'
            reason += '; ' + '; '.join(failures)
            if evidence.lost:
                reason += '; evidence journal incomplete'
        elif outcome == 'PASS':
            try:
                io.cleanup = False  # verified OFF; leave cleanup-only mode for LOCAL
                dev.diagnostic_write('SYST:LOC')
            except BaseException as exc:
                outcome = 'ABORTED'
                reason += f'; LOCAL unconfirmed: {type(exc).__name__}'
        for item in (meter, load, transport):
            try:
                if item is not None: item.close()
            except BaseException as exc:
                outcome = 'ABORTED'
                reason += f'; close failed: {type(exc).__name__}'
        try:
            gate(FINAL_TOKEN, 'Physically confirm PSU CH1/CH2/CH3 OFF and KEL103 INPUT OFF')
            evidence.add('operator_confirmation', gate='FINAL')
        except BaseException:
            outcome = 'ABORTED'
            reason += '; physical final OFF not confirmed'
        evidence.add('outcome', outcome=outcome, reason=reason,
                     phase='LOW_POWER_SCALAR', status='HW NOT_REVIEWED')
        if evidence.lost and outcome == 'PASS':
            outcome, reason = 'ABORTED', 'incomplete evidence; hardware review impossible'
    print(f'R4.4-C {outcome}: {reason}')
    return outcome
