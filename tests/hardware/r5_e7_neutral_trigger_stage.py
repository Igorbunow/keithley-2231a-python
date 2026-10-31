"""R5-E7: one neutral *TRG with CH1/CH2/CH3 OFF and empty terminals.

Operator-gated hardware qualification. A neutral event is NOT proof of
coupling, active-setpoint transition, analog response or synchronization.
"""
from __future__ import annotations

import hashlib
import json
import math

from common import ShutdownEvidence, require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_5_source_stage import R45SourceScope
from r4_4_scalar_stage import _profile
from r4_status_stage import _redacted_trace

STAGE = 'R5E7NEUTRALTRIGGER'
PHASE = 'NEUTRAL_BUS_TRIGGER_OFF'
INITIAL_TOKEN = ('CONFIRM R5E7 ALL_CH123_PHYSICAL_OFF ALL_TERMINALS_EMPTY '
                 'NO_LOAD NO_DUT NO_JUMPERS NO_EXTERNAL_POWER PSU_ONLY')
PROFILE_TOKEN = ('CONFIRM R5E7 APPLY_OFF_PROFILE CH1_V3.30_I0.150 '
                 'CH2_CH3_V1.00_I0.100 EMPTY_TERMINALS')
EVENT_TOKEN = ('CONFIRM R5E7 ONE_NEUTRAL_STAR_TRG ALL_CH123_PRESETS_EQUAL_ACTIVE '
               'ALL_OFF EMPTY_TERMINALS COUPLING_UNKNOWN NO_RETRY')
FINAL_TOKEN = 'CONFIRM R5E7 CH1=OFF CH2=OFF CH3=OFF TERMINALS_EMPTY'
ACTIVE = {1: (3.30, .150), 2: (1.00, .100), 3: (1.00, .100)}


def snapshot(p):
    return {'voltage_v': p.voltage_v, 'current_a': p.current_a}


def near(x, y):
    return abs(x - y) < .00049


class NeutralTriggerScope(R45SourceScope):
    """All-channel wire allowlist with a single operator-armed *TRG."""
    MAX_COMMANDS = 8500
    MAX_SECONDS = 1250
    SHUTDOWN_RESERVE = 160

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.phase = 'BEFORE'
        self.trigger_writes = []
        self.trigger_queries = []
        self.trigger_count = 0
        self.event_armed = False
        self.finished = False
        self.restore = {}

    def _validate(self, command, query):
        if command == '*TRG':
            if (query or self.phase != 'EVENT' or not self.event_armed or
                    self.trigger_count or self.cleanup or
                    any(self.outputs.values()) or not self.profile_authorized):
                raise SafetyError('Unapproved, repeated, or unsafe bus trigger')
            self.trigger_count += 1  # consume before UART transfer
            return
        if command in ('VOLT:TRIG?', 'CURR:TRIG?'):
            if (not query or not self.profile_authorized or self.cleanup or
                    self.phase not in ('ORIGINAL','NEUTRALIZE','NEUTRAL_CHECK',
                                       'RESTORE','RESTORE_CHECK','EVENT') or
                    self.selected not in (1, 2, 3)):
                raise SafetyError('Unapproved trigger-register query')
            self.trigger_queries.append((self.phase, self.selected, command))
            return
        if command.startswith(('VOLT:TRIG ', 'CURR:TRIG ')):
            if (query or not self.profile_authorized or self.cleanup or
                    self.phase not in ('NEUTRALIZE', 'RESTORE') or
                    self.selected not in (1, 2, 3)):
                raise SafetyError('Trigger register write outside guarded stage')
            key = 'VOLT:TRIG' if command.startswith('VOLT:TRIG ') else 'CURR:TRIG'
            v = (ACTIVE[self.selected] if self.phase == 'NEUTRALIZE'
                 else self.restore[self.selected])
            expected = f'{key} {v[0 if key == "VOLT:TRIG" else 1]:.3f}'
            if command != expected:
                raise SafetyError('Wrong channel/value in trigger-register write')
            self.trigger_writes.append((self.phase, self.selected, command))
            return
        if command == 'SYST:LOC' and not self.finished:
            raise SafetyError('LOCAL before verified restoration and final OFF')
        return super()._validate(command, query)


def run_neutral_trigger(transport, evidence, *, profile_gate=None, event_gate=None,
                        final_gate=None):
    """Return PASS pending independent audit; never retry *TRG."""
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    profile_started = off_confirmed = physical_off_confirmed = False
    outcome, reason = 'ABORTED', 'neutral OFF event not completed'
    original = neutral = restored = None
    try:
        if not all((profile_gate, event_gate, final_gate)):
            raise SafetyError('All operator gates mandatory')
        dev = Keithley2231A(transport, debug=True,
            debug_trace=lambda entry: _redacted_trace(evidence, entry),
            debug_adapter=lambda wire: NeutralTriggerScope(wire, evidence))
        io = dev.debug_transport
        fields = dev.identity().split(',')
        if len(fields) != 4 or fields[1].strip() != '2231A-30-3':
            raise SafetyError('Incorrect PSU model')
        evidence.add('psu_identity', command='*IDN?', model=fields[1].strip(),
                     firmware=fields[-1].strip())
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError('Independent topology required')
        profile_gate(PROFILE_TOKEN, io)
        evidence.add('operator_confirmation', gate='PROFILE')
        io.profile_authorized = True
        profile_started = True
        dev.apply_profile(_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        dev.verify_profile()

        # Read ALL original registers before changing any of them.
        io.phase = 'ORIGINAL'
        initial = {ch:dev.read_trigger_presets(ch) for ch in (1,2,3)}
        original = {str(ch):snapshot(initial[ch]) for ch in (1,2,3)}
        for ch in (1,2,3):
            old = initial[ch]
            spec = dev._profile.channels[ch-1]
            if (not math.isfinite(old.voltage_v) or not math.isfinite(old.current_a)
                or old.voltage_v < 0 or old.voltage_v > spec.max_voltage
                or old.current_a < 0 or old.current_a > spec.max_current
                or abs(old.voltage_v*100-round(old.voltage_v*100)) > 1e-6
                or abs(old.current_a*1000-round(old.current_a*1000)) > 1e-6):
                raise SafetyError('An original trigger register is not safely restorable')
            io.restore[ch] = (old.voltage_v, old.current_a)
        io.phase = 'NEUTRALIZE'
        for ch,(volts,amps) in ACTIVE.items():
            dev.stage_trigger_presets_off(ch,volts,amps,confirmed_off_only=True)
        io.phase = 'NEUTRAL_CHECK'
        verified = {ch:dev.read_trigger_presets(ch) for ch in (1,2,3)}
        for ch,(v,i) in ACTIVE.items():
            if not near(verified[ch].voltage_v,v) or not near(verified[ch].current_a,i):
                raise SafetyError('Neutral preset readback failed')
        neutral = {str(ch):snapshot(verified[ch]) for ch in (1,2,3)}
        require_outputs(dev)
        dev.verify_profile()
        evidence.add('neutral_presets_verified', channels=[1,2,3],
                     values=neutral, event_sent=False)

        event_gate(EVENT_TOKEN,io)
        evidence.add('operator_confirmation',gate='EVENT',channels=[1,2,3])
        require_outputs(dev)
        io.phase = 'EVENT'
        io.event_armed = True
        event_observation = dev.fire_neutral_trigger_off(
            confirmed_all_outputs_off=True, confirmed_empty_terminals=True)
        io.event_armed = False
        if (io.trigger_count != 1 or event_observation.channels != (1,2,3)
                or not event_observation.outputs_off):
            raise SafetyError('Single neutral event not confirmed')
        evidence.add('neutral_bus_event',command='*TRG',count=io.trigger_count,
                     analog='NOT_MEASURED',coupling='UNKNOWN_NOT_QUALIFIED')
        require_outputs(dev)
        dev.verify_profile()

        io.phase = 'RESTORE'
        for ch in (1,2,3):
            old = initial[ch]
            dev.stage_trigger_presets_off(ch,old.voltage_v,old.current_a,
                                          confirmed_off_only=True)
        io.phase = 'RESTORE_CHECK'
        checked = {ch:dev.read_trigger_presets(ch) for ch in (1,2,3)}
        for ch in (1,2,3):
            if (not near(checked[ch].voltage_v,initial[ch].voltage_v) or
                    not near(checked[ch].current_a,initial[ch].current_a)):
                raise SafetyError('Original trigger register not restored')
        restored = {str(ch):snapshot(checked[ch]) for ch in (1,2,3)}
        require_outputs(dev)
        dev.verify_profile()
        dev.emergency_off()
        require_outputs(dev)
        off_confirmed = True
        io.finished = True
        if evidence.lost: raise SafetyError('Evidence incomplete')
        dev.diagnostic_write('SYST:LOC')
        values = {'original':original,'neutral':neutral,'restored':restored,
                  'active':{str(ch):{'voltage_v':v,'current_a':i}
                            for ch,(v,i) in ACTIVE.items()},
                  'trigger_count':io.trigger_count,'coupling':'UNKNOWN_NOT_QUALIFIED',
                  'analog':'NOT_MEASURED','active_unchanged':True,
                  'all_channels_off':True}
        digest = hashlib.sha256(json.dumps(values,sort_keys=True,
              separators=(',',':')).encode('ascii')).hexdigest()
        evidence.add('results',values=values,results_sha256=digest)
        final_gate(FINAL_TOKEN)
        physical_off_confirmed = True
        evidence.add('operator_confirmation',gate='FINAL')
        if evidence.lost: raise SafetyError('Evidence incomplete after final gate')
        outcome, reason = 'PASS', 'one neutral OFF event; restores verified; audit pending'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try: evidence.add('stage_error',exception_type=type(exc).__name__,detail=str(exc))
        except BaseException: pass
    finally:
        # Faults may leave six trigger registers changed. NEVER retry *TRG or
        # attempt speculative restoration over an unknown UART link.
        if profile_started and io is not None and io.live and not off_confirmed:
            evidence.cleanup = io.cleanup = True
            try:
                dev.emergency_off()
                require_outputs(dev)
                off_confirmed = True
            except BaseException as exc:
                reason += f'; PSU OFF not confirmed: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; LINK UNKNOWN; physically verify OFF, inspect presets'
        if outcome != 'PASS' and final_gate is not None:
            try:
                final_gate(FINAL_TOKEN)
                physical_off_confirmed = True
                evidence.add('operator_confirmation',gate='FINAL_AFTER_ABORT')
            except BaseException as exc:
                reason += f'; manual OFF unconfirmed: {type(exc).__name__}'
        try: (io if io is not None else transport).close()
        except BaseException as exc:
            outcome,reason = 'ABORTED',f'Close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome,reason = 'ABORTED',reason+'; evidence incomplete'
        evidence.cleanup=True
        try:
            evidence.add('outcome',phase=PHASE,outcome=outcome,reason=reason,
                         link_known=io.live if io is not None else False,
                         digital_off_confirmed=off_confirmed,
                         physical_off_confirmed=physical_off_confirmed,
                         hardware_review='PENDING',
                         note='Neutral *TRG only; coupling and analog NOT QUALIFIED')
        except BaseException:
            outcome='ABORTED'
    print(f'R5-E7 {outcome}: {reason}; physically verify CH1/CH2/CH3 OFF')
    return outcome
