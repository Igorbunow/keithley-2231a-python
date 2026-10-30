"""R4.5-D bounded batched OFF-only source-step qualification.

All cases share ONE independently gated profile and ONE PSU session.  No powered
operations, load or DUT.  A failed case aborts the remaining matrix, never skips.
All SCPI, channel selection, profile checking and shutdown use existing code.
"""
from __future__ import annotations

import hashlib
import json

from common import ShutdownEvidence, require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_5_step_mutation_stage import (CURRENT_STEPS, VOLTAGE_STEPS,
    StepMutationScope, DIRECTIONS, PROFILE_TOKEN, FINAL_TOKEN, profile)
from r4_status_stage import _redacted_trace

STAGE = 'R45STEPBATCH'
INITIAL_TOKEN = ('CONFIRM R45STEPBATCH ALL_OUTPUTS_OFF EMPTY_TERMINALS '
                 'NO_LOAD NO_DUT NO_JUMPERS INDEPENDENT RECALL_OFF PSU_ONLY')
FINAL_BATCH_TOKEN = 'CONFIRM R45STEPBATCH CH1=OFF CH2=OFF CH3=OFF EMPTY_TERMINALS'


def cases(matrix):
    if matrix not in ('current', 'voltage', 'all'):
        raise ValueError('matrix must be current, voltage or all')
    kinds = ('current',) if matrix == 'current' else (('voltage',) if matrix == 'voltage'
                                                      else ('current', 'voltage'))
    return tuple((ch, kind, step) for kind in kinds for ch in (1, 2, 3)
                 for step in (CURRENT_STEPS if kind == 'current' else VOLTAGE_STEPS))


def matrix_token(matrix):
    plan = cases(matrix)
    return (f'CONFIRM R45STEPBATCH MATRIX={matrix.upper()} CASES={len(plan)} '
            'UP_UP_DOWN_DOWN ALL_OUTPUTS_OFF EMPTY_TERMINALS NO_LOAD')


class BatchStepScope(StepMutationScope):
    """Reuse the audited one-case SCPI allowlist with controlled case transitions."""
    MAX_COMMANDS = 25000
    MAX_SECONDS = 2700
    SHUTDOWN_RESERVE = 260

    def __init__(self, transport, evidence, *, matrix):
        cases(matrix)
        super().__init__(transport, evidence, channel=1, kind='current', increment=.001)
        self.matrix = matrix
        self.completed_cases = 0
        self.planned_cases = cases(matrix)

    def begin_case(self, channel, kind, step):
        if (self.step_authorized or self.cleanup or not self.live or
                self.completed_cases >= len(self.planned_cases) or
                self.planned_cases[self.completed_cases] != (channel, kind, step)):
            raise SafetyError('Batch case order / authorization violated')
        self.channel, self.kind, self.increment = channel, kind, step
        self._prefix = 'CURR' if kind == 'current' else 'VOLT'
        self.step_write_count = 0
        self.direction_writes = []
        self.step_query_count = 0
        self.step_authorized = True

    def end_case(self):
        if not self.step_authorized or self.step_write_count != 1 or self.direction_writes != [
                f'{self._prefix}:{direction.upper()}' for direction in DIRECTIONS]:
            raise SafetyError('Batch case did not complete exactly four mutations')
        if self.step_query_count < 5:
            raise SafetyError('Missing STEP readback in batch case')
        self.step_authorized = False
        self.completed_cases += 1


def run_step_batch(transport, evidence, *, matrix, profile_gate=None,
                   batch_gate=None, final_gate=None):
    """One atomic approved OFF batch; fail-stop with known-link emergency OFF."""
    plan = cases(matrix)
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    profile_started = False
    off_confirmed = False
    physical_off_confirmed = False
    outcome, reason = 'ABORTED', 'batch incomplete'
    completed = []
    try:
        dev = Keithley2231A(transport, debug=True,
             debug_trace=lambda record: _redacted_trace(evidence, record),
             debug_adapter=lambda wire: BatchStepScope(wire, evidence, matrix=matrix))
        io = dev.debug_transport
        identity = dev.identity()
        fields = identity.split(',')
        evidence.add('psu_identity', model=fields[1].strip(), firmware=fields[-1].strip())
        require_outputs(dev)
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(mode)):
                raise SafetyError(f'Unexpected topology: {mode}')
        if profile_gate is None or batch_gate is None or final_gate is None:
            raise SafetyError('Three independent operator gates are mandatory')
        profile_gate(PROFILE_TOKEN, io)
        evidence.add('operator_confirmation', gate='PROFILE')
        profile_started = True
        io.profile_authorized = True
        dev.apply_profile(profile(), confirmed_wiring='independent')
        require_outputs(dev)
        batch_gate(matrix_token(matrix), io)
        evidence.add('operator_confirmation', gate='BATCH', matrix=matrix, cases=len(plan))
        for index, (channel, kind, increment) in enumerate(plan):
            require_outputs(dev)
            dev.verify_profile()
            io.begin_case(channel, kind, increment)
            spec = dev._profile.channels[channel - 1]
            initial = spec.initial_voltage if kind == 'voltage' else spec.initial_current
            record = {'index': index + 1, 'channel': channel, 'kind': kind,
                      'increment': increment, 'initial': initial, 'steps': []}
            record['readback_step'] = dev.configure_source_step(channel, kind, increment)
            for direction in DIRECTIONS:
                actual = dev.step_source(channel, kind, direction)
                record['steps'].append({'direction': direction, 'setpoint': actual})
                require_outputs(dev)
            if abs(record['steps'][-1]['setpoint'] - initial) >= .0005:
                raise SafetyError('Batch case failed to restore original setpoint')
            io.end_case()
            dev.verify_profile()
            require_outputs(dev)
            record['outcome'] = 'PASS'
            completed.append(record)
            evidence.add('batch_case_result', **record)
            print(f'BATCH CASE {index+1}/{len(plan)} PASS: CH{channel} {kind} '
                  f'{increment:.3f}, restored {initial:.3f}, all outputs OFF')
        if io.completed_cases != len(plan):
            raise SafetyError('Batch coverage incomplete')
        dev.emergency_off()
        require_outputs(dev)
        off_confirmed = True
        io.phase_complete = True
        if evidence.lost:
            raise SafetyError('Evidence logging lost')
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(completed, sort_keys=True,
                    separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('batch_results', matrix=matrix, cases=completed,
                     case_count=len(completed), results_sha256=digest,
                     analog_measurement='NOT_RUN')
        final_gate(FINAL_BATCH_TOKEN)
        physical_off_confirmed = True
        evidence.add('operator_confirmation', gate='FINAL')
        if evidence.lost:
            raise SafetyError('Evidence lost before outcome')
        outcome, reason = 'PASS', 'bounded OFF-only matrix; analog NOT_RUN'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc),
                         completed_cases=len(completed))
        except BaseException:
            pass
    finally:
        if profile_started and io is not None and io.live and not off_confirmed:
            evidence.cleanup, io.cleanup = True, True
            try:
                dev.emergency_off()
                require_outputs(dev)
                off_confirmed = True
            except BaseException as exc:
                reason += f'; OFF UNCONFIRMED: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; link uncertain: independent physical OFF required'
        if outcome != 'PASS' and final_gate is not None:
            try:
                final_gate(FINAL_BATCH_TOKEN)
                physical_off_confirmed = True
                evidence.add('operator_confirmation', gate='FINAL_AFTER_ABORT')
            except BaseException as exc:
                reason += f'; physical OFF unconfirmed: {type(exc).__name__}'
        try:
            (io if io is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome, reason = 'ABORTED', reason + '; evidence incomplete'
        evidence.cleanup = True
        try:
            evidence.add('outcome', outcome=outcome, reason=reason, matrix=matrix,
                         planned_cases=len(plan), completed_cases=len(completed),
                         link_known=io.live if io else False,
                         digital_off_confirmed=off_confirmed,
                         physical_off_confirmed=physical_off_confirmed,
                         phase='STEP_BATCH_OFF', analog_measurement='NOT_RUN')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5-D BATCH {outcome}: {len(completed)}/{len(plan)} cases; {reason}')
    return outcome
