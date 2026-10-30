"""R4.5-D: ONE operator-approved OFF-only STEP mutation case per run.

NO output enable command; isolated empty-terminal topology is mandatory. The
production driver executes all mutating operations; this adapter independently
bounds the wire envelope and forbids every ON command. Import is I/O-free.
"""
from __future__ import annotations

import hashlib
import json

from common import ShutdownEvidence, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError
from r4_5_source_stage import R45SourceScope
from r4_status_stage import _redacted_trace

STAGE = 'R45STEPMUT'
INITIAL_TOKEN = ('CONFIRM R45STEPMUT ALL_OUTPUTS_OFF EMPTY_TERMINALS '
                 'NO_LOAD NO_DUT NO_JUMPERS INDEPENDENT RECALL_OFF PSU_ONLY')
PROFILE_TOKEN = ('CONFIRM R45STEPMUT APPLY_OFF_PROFILE CH1_V3.30_I0.100 '
                 'CH2_CH3_V1.00_I0.050 ALL_OUTPUTS_OFF NO_WIRES')
FINAL_TOKEN = 'CONFIRM R45STEPMUT CH1=OFF CH2=OFF CH3=OFF'
DIRECTIONS = ('up', 'up', 'down', 'down')
VOLTAGE_STEPS = (.010, .050, .100)
CURRENT_STEPS = (.001, .005, .010)


def case_token(channel, kind, increment):
    validate_case(channel, kind, increment)
    code = 'VOLT' if kind == 'voltage' else 'CURR'
    return (f'CONFIRM R45STEPMUT CH{channel} {code}_STEP={increment:.3f} '
            'UP_UP_DOWN_DOWN ALL_OUTPUTS_OFF EMPTY_TERMINALS')


def validate_case(channel, kind, increment):
    if type(channel) is not int or channel not in (1, 2, 3):
        raise ValueError('channel must be 1, 2 or 3')
    if type(kind) is not str or kind not in ('voltage', 'current'):
        raise ValueError('kind must be voltage or current')
    allowed = VOLTAGE_STEPS if kind == 'voltage' else CURRENT_STEPS
    if type(increment) not in (float, int) or increment not in allowed:
        raise ValueError(f'increment must be an approved {kind} value: {allowed}')


def profile():
    # Below each hardware profile cap: supports two UPs and returning DOWNs.
    return Profile('R4.5-D bounded OFF STEP test', (
        ChannelProfile(3.50, .150, 3.30, .100),
        ChannelProfile(1.50, .100, 1.00, .050),
        ChannelProfile(1.50, .100, 1.00, .050),
    ), wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


class StepMutationScope(R45SourceScope):
    """Exact one-case wire allowlist, with independent phase and channel gates."""
    WRITES = R45SourceScope.WRITES | {'CURR 0.050'}
    MAX_COMMANDS = 3000
    MAX_SECONDS = 720
    SHUTDOWN_RESERVE = 110

    def __init__(self, transport, evidence, *, channel, kind, increment):
        super().__init__(transport, evidence)
        validate_case(channel, kind, increment)
        self.channel, self.kind, self.increment = channel, kind, increment
        self.step_authorized = False
        self.phase_complete = False
        self.step_write_count = 0
        self.direction_writes = []
        self.step_query_count = 0
        self._prefix = 'VOLT' if kind == 'voltage' else 'CURR'

    def _validate(self, command, query):
        if command == 'SYST:LOC' and not self.phase_complete:
            raise SafetyError('LOCAL requires completed STEP and verified OFF')
        if command in ('VOLT:STEP?', 'CURR:STEP?'):
            if (not query or not self.step_authorized or self.cleanup or
                    self.selected != self.channel or command != self._prefix + ':STEP?'):
                raise SafetyError('Unapproved STEP query / channel / phase')
            self.step_query_count += 1
            if self.step_query_count > 10:
                raise SafetyError('STEP query count exceeded case budget')
            return
        fixed_write = f'{self._prefix}:STEP {self.increment:.3f}'
        if not query and command in (fixed_write, f'{self._prefix}:UP', f'{self._prefix}:DOWN'):
            if (not self.step_authorized or self.cleanup or
                    self.selected != self.channel):
                raise SafetyError('STEP mutation requires target channel and physical gate')
            if command == fixed_write:
                if self.step_write_count or self.direction_writes:
                    raise SafetyError('Exactly one STEP register write allowed')
                self.step_write_count += 1
            else:
                if not self.step_write_count or len(self.direction_writes) >= len(DIRECTIONS):
                    raise SafetyError('STEP action order/budget violated')
                wanted = f'{self._prefix}:{DIRECTIONS[len(self.direction_writes)].upper()}'
                if command != wanted:
                    raise SafetyError(f'STEP action order violation: expected {wanted}')
                self.direction_writes.append(command)
            return
        # All other commands remain bounded by the earlier, field-tested
        # Independent-profile safety scope. Unlisted STEP variants never pass.
        return super()._validate(command, query)


def run_step_mutation(transport, evidence, *, channel, kind, increment,
                      profile_gate=None, mutation_gate=None, final_gate=None):
    """Perform exactly one OFF case. PASS requires post-OFF and physical gate.

    Link uncertainty prohibits further SCPI. On a known link a bounded
    emergency_off is attempted even if evidence logging fails.
    """
    validate_case(channel, kind, increment)
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    started_profile = False
    off_confirmed = False
    outcome, reason = 'ABORTED', 'case incomplete'
    physical_off_confirmed = False
    values = {'channel': channel, 'kind': kind, 'increment': increment,
              'direction_plan': list(DIRECTIONS), 'analog_measurement': 'NOT_RUN'}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: StepMutationScope(
                wire, evidence, channel=channel, kind=kind, increment=increment))
        io = dev.debug_transport
        ident = dev.identity()
        fields = ident.split(',')
        evidence.add('psu_identity', model=fields[1].strip(), firmware=fields[-1].strip())
        require_outputs(dev)
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(mode)):
                raise SafetyError(f'Non-independent mode: {mode}')
        if profile_gate is None or mutation_gate is None or final_gate is None:
            raise SafetyError('Three physical operator gates required')
        profile_gate(PROFILE_TOKEN, io)
        evidence.add('operator_confirmation', gate='PROFILE')
        started_profile = True
        io.profile_authorized = True
        dev.apply_profile(profile(), confirmed_wiring='independent')
        require_outputs(dev)
        mutation_gate(case_token(channel, kind, increment), io)
        evidence.add('operator_confirmation', gate='MUTATION', channel=channel,
                     kind=kind, increment=increment)
        io.step_authorized = True
        spec = dev._profile.channels[channel - 1]
        initial = spec.initial_voltage if kind == 'voltage' else spec.initial_current
        values['initial_setpoint'] = initial
        actual_step = dev.configure_source_step(channel, kind, increment)
        values['step_readback'] = actual_step
        values['steps'] = []
        for direction in DIRECTIONS:
            actual = dev.step_source(channel, kind, direction)
            values['steps'].append({'direction': direction, 'setpoint': actual})
            require_outputs(dev)
        values['final_setpoint'] = values['steps'][-1]['setpoint']
        if abs(values['final_setpoint'] - initial) > .00049:
            raise SafetyError('STEP sequence did not restore initial setpoint')
        if io.step_write_count != 1 or io.direction_writes != [
                f'{io._prefix}:{d.upper()}' for d in DIRECTIONS]:
            raise SafetyError('STEP action/write budget did not complete')
        io.step_authorized = False
        dev.verify_profile()
        dev.emergency_off()
        require_outputs(dev)
        off_confirmed = True
        io.phase_complete = True
        if evidence.lost:
            raise SafetyError('Evidence lost, cannot finish successfully')
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(values, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('results', values=values, results_sha256=digest)
        final_gate(FINAL_TOKEN)
        physical_off_confirmed = True
        evidence.add('operator_confirmation', gate='FINAL')
        if evidence.lost:
            raise SafetyError('Incomplete evidence')
        outcome, reason = 'PASS', 'one digital OFF-only STEP case; analog unverified'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException:
            pass
    finally:
        if started_profile and io is not None and io.live and not off_confirmed:
            evidence.cleanup = True
            io.cleanup = True
            try:
                dev.emergency_off()
                require_outputs(dev)
                off_confirmed = True
            except BaseException as exc:
                reason += f'; OFF unconfirmed: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; link uncertain: manual physical OFF required'
        if outcome != 'PASS' and final_gate is not None:
            try:
                final_gate(FINAL_TOKEN)
                physical_off_confirmed = True
                evidence.add('operator_confirmation', gate='FINAL_AFTER_ABORT')
            except BaseException as exc:
                reason += f'; physical OFF not confirmed: {type(exc).__name__}'
        try:
            (io if io is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome, reason = 'ABORTED', reason + '; incomplete evidence'
        evidence.cleanup = True
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='STEP_MUTATION_OFF', channel=channel, kind=kind,
                         increment=increment, link_known=io.live if io else False,
                         digital_off_confirmed=off_confirmed,
                         physical_off_confirmed=physical_off_confirmed,
                         note='OFF-only register behavior; not powered/analog')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-D {outcome}: {reason}; manually check CH1/CH2/CH3 OFF')
    return outcome
