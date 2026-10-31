"""R5-E4: guarded OFF-only trigger preset write/read/restore, one channel.

No trigger execution, coupling, output-enable, load or DUT. Restoration is
attempted only in the normal healthy-link path; a failed/unknown link ABORTS.
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

STAGE = 'R5E4TRIGGERWRITE'
PHASE = 'TRIGGER_PRESET_OFF_WRITE_RESTORE'
INITIAL_TOKEN = ('CONFIRM R5E4 INDEPENDENT ALL_OUTPUTS_OFF EMPTY_TERMINALS '
                 'NO_LOAD NO_DUT NO_JUMPERS NO_EXTERNAL_POWER PSU_ONLY')
PROFILE_TOKEN = ('CONFIRM R5E4 APPLY_OFF_PROFILE CH1_V3.30_I0.150 '
                 'CH2_CH3_V1.00_I0.100 EMPTY_TERMINALS')
FINAL_TOKEN = 'CONFIRM R5E4 CH1=OFF CH2=OFF CH3=OFF EMPTY_TERMINALS'
TRIGGER_QUERIES = ('VOLT:TRIG?', 'CURR:TRIG?')
PRESETS = {1: (2.20, .120), 2: (.80, .080), 3: (.80, .080)}


def stage_token(ch):
    if type(ch) is not int or ch not in (1, 2, 3):
        raise ValueError('channel must be CH1, CH2 or CH3')
    v, i = PRESETS[ch]
    return (f'CONFIRM R5E4 CH{ch} WRITE_VOLT_TRIG_{v:.2f}_CURR_TRIG_{i:.3f} '
            'THEN_RESTORE_ORIGINAL NO_TRIGGER ALL_OUTPUTS_OFF EMPTY_TERMINALS')


class TriggerWriteScope(R45SourceScope):
    """Exact trigger writes/readbacks on selected channel, OFF-only."""
    MAX_COMMANDS = 2350
    MAX_SECONDS = 660
    SHUTDOWN_RESERVE = 128

    def __init__(self, transport, evidence, channel):
        super().__init__(transport, evidence)
        stage_token(channel)
        self.channel = channel
        self.phase = 'BEFORE'
        self.authorized = False
        self.operation_complete = False
        self.trigger_queries = []
        self.trigger_writes = []
        self.phase_queries = []
        self.phase_writes = []

    def set_phase(self, phase):
        if phase not in ('BEFORE', 'STAGE', 'CHECK_STAGE', 'RESTORE', 'CHECK_RESTORE'):
            raise SafetyError('Unknown staging phase')
        self.phase = phase
        self.phase_queries = []
        self.phase_writes = []

    def _validate(self, command, query):
        if command in TRIGGER_QUERIES:
            if (not query or not self.authorized or self.cleanup or
                    self.selected != self.channel or len(self.phase_queries) >= 2 or
                    command != TRIGGER_QUERIES[len(self.phase_queries)]):
                raise SafetyError('Unapproved, duplicate or wrong-channel trigger read')
            if self.phase in ('STAGE', 'RESTORE') and (
                    len(self.phase_writes) != len(self.phase_queries) + 1):
                raise SafetyError('Trigger readback without matching write')
            self.phase_queries.append(command)
            self.trigger_queries.append(command)
            return
        if command.startswith(('VOLT:TRIG', 'CURR:TRIG')):
            if (query or not self.authorized or self.cleanup or
                    self.phase not in ('STAGE', 'RESTORE') or
                    self.selected != self.channel or len(self.phase_writes) >= 2 or
                    len(self.phase_queries) != len(self.phase_writes)):
                raise SafetyError('Unapproved/wrong-channel trigger write')
            required_prefix = ('VOLT:TRIG ' if not self.phase_writes else 'CURR:TRIG ')
            target = PRESETS[self.channel][len(self.phase_writes)] if self.phase == 'STAGE' else None
            if (not command.startswith(required_prefix) or
                    (target is not None and command != f'{required_prefix}{target:.3f}')):
                raise SafetyError('Unexpected trigger write value or sequence')
            if self.phase == 'RESTORE':
                expected = self.restore_values[len(self.phase_writes)]
                if command != f'{required_prefix}{expected:.3f}':
                    raise SafetyError('Unexpected restoration command')
            self.phase_writes.append(command)
            self.trigger_writes.append(command)
            return
        if command == 'SYST:LOC' and not self.operation_complete:
            raise SafetyError('LOCAL before verified trigger restore and all-channel OFF')
        return super()._validate(command, query)


def run_trigger_write(transport, evidence, *, channel, profile_gate=None,
                      stage_gate=None, final_gate=None):
    """One operator-authorized OFF-only cycle; return PASS pending independent audit."""
    stage_token(channel)
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    profile_started = off_confirmed = physical_off_confirmed = False
    outcome, reason = 'ABORTED', 'trigger preset staging incomplete'
    values = None
    try:
        if profile_gate is None or stage_gate is None or final_gate is None:
            raise SafetyError('All operator gates are mandatory')
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda event: _redacted_trace(evidence, event),
            debug_adapter=lambda wire: TriggerWriteScope(wire, evidence, channel))
        io = dev.debug_transport
        identity = dev.identity()
        fields = identity.split(',')
        if len(fields) != 4 or fields[1].strip() != '2231A-30-3':
            raise SafetyError('Wrong instrument')
        evidence.add('psu_identity', command='*IDN?',
                     model=fields[1].strip(), firmware=fields[-1].strip())
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError('Not independent topology')
        profile_gate(PROFILE_TOKEN, io)
        evidence.add('operator_confirmation', gate='PROFILE')
        io.profile_authorized = True
        profile_started = True
        dev.apply_profile(_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        stage_gate(stage_token(channel), io)
        evidence.add('operator_confirmation', gate='STAGE', channel=channel)
        io.authorized = True
        old = dev.read_trigger_presets(channel)
        # Never risk leaving unrestorable native presets after our first write.
        spec = dev._profile.channels[channel - 1]
        if (old.voltage_v > spec.max_voltage or old.current_a > spec.max_current or
                not math.isfinite(old.voltage_v) or not math.isfinite(old.current_a) or
                abs(old.voltage_v / .01 - round(old.voltage_v / .01)) > 1e-6 or
                abs(old.current_a / .001 - round(old.current_a / .001)) > 1e-6):
            raise SafetyError('Original trigger presets cannot be safely restored under profile')
        io.set_phase('STAGE')
        v, i = PRESETS[channel]
        staged = dev.stage_trigger_presets_off(channel, v, i, confirmed_off_only=True)
        io.set_phase('CHECK_STAGE')
        checked_stage = dev.read_trigger_presets(channel)
        if (abs(checked_stage.voltage_v - v) >= .00049 or
                abs(checked_stage.current_a - i) >= .00049):
            raise SafetyError('Staged register values not persistent')
        require_outputs(dev)
        dev.verify_profile()
        evidence.add('staged_observation', channel=channel,
                     voltage_v=checked_stage.voltage_v,
                     current_a=checked_stage.current_a,
                     restored=False, trigger_executed=False)
        io.restore_values = (old.voltage_v, old.current_a)
        io.set_phase('RESTORE')
        dev.stage_trigger_presets_off(channel, old.voltage_v, old.current_a,
                                      confirmed_off_only=True)
        io.set_phase('CHECK_RESTORE')
        restored = dev.read_trigger_presets(channel)
        if (abs(restored.voltage_v - old.voltage_v) >= .00049 or
                abs(restored.current_a - old.current_a) >= .00049):
            raise SafetyError('Original trigger values NOT restored')
        require_outputs(dev)
        dev.verify_profile()
        if io.trigger_queries != list(TRIGGER_QUERIES) * 5 or len(io.trigger_writes) != 4:
            raise SafetyError('Incomplete native trigger sequence')
        values = dict(channel=channel,
                      original={'voltage_v':old.voltage_v,'current_a':old.current_a},
                      staged={'voltage_v':staged.voltage_v,'current_a':staged.current_a},
                      checked={'voltage_v':checked_stage.voltage_v,'current_a':checked_stage.current_a},
                      restored={'voltage_v':restored.voltage_v,'current_a':restored.current_a},
                      trigger_queries=list(io.trigger_queries),
                      trigger_writes=list(io.trigger_writes),
                      active_setpoints_unchanged=True,
                      analog_measurement='NOT_RUN', trigger_executed=False)
        dev.emergency_off()
        require_outputs(dev)
        off_confirmed = True
        io.operation_complete = True
        if evidence.lost: raise SafetyError('Evidence incomplete')
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(values, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('results', values=values, results_sha256=digest)
        final_gate(FINAL_TOKEN)
        physical_off_confirmed = True
        evidence.add('operator_confirmation', gate='FINAL')
        if evidence.lost: raise SafetyError('Evidence incomplete after final gate')
        outcome, reason = 'PASS', 'OFF-only trigger preset staged, read and restored; audit pending'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try: evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException: pass
    finally:
        if profile_started and io is not None and io.live and not off_confirmed:
            evidence.cleanup = io.cleanup = True
            try:
                dev.emergency_off()
                require_outputs(dev)
                off_confirmed = True
            except BaseException as exc:
                reason += f'; shutdown not confirmed: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; UART UNKNOWN; operator must confirm OFF'
        if outcome != 'PASS' and final_gate is not None:
            try:
                final_gate(FINAL_TOKEN)
                physical_off_confirmed = True
                evidence.add('operator_confirmation', gate='FINAL_AFTER_ABORT')
            except BaseException as exc:
                reason += f'; manual OFF unconfirmed: {type(exc).__name__}'
        try: (io if io is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome, reason = 'ABORTED', reason + '; incomplete evidence'
        evidence.cleanup = True
        try:
            evidence.add('outcome', phase=PHASE, channel=channel,
                         outcome=outcome, reason=reason,
                         link_known=io.live if io is not None else False,
                         digital_off_confirmed=off_confirmed,
                         physical_off_confirmed=physical_off_confirmed,
                         hardware_review='PENDING',
                         note='No trigger execution or powered qualification')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R5-E4 CH{channel} {outcome}: {reason}; physically verify all outputs OFF')
    return outcome
