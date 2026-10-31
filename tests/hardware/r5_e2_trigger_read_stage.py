"""R5-E2: one-channel OFF-only native trigger register readback qualification.

The production driver is the only SCPI owner. A bounded independent profile is
applied to OFF outputs; no output-ON, trigger execute, coupling or trigger write.
"""
from __future__ import annotations

import hashlib
import json

from common import ShutdownEvidence, require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_5_source_stage import R45SourceScope
from r4_4_scalar_stage import _profile
from r4_status_stage import _redacted_trace

STAGE = 'R5E2TRIGGERREAD'
INITIAL_TOKEN = ('CONFIRM R5E2 INDEPENDENT ALL_OUTPUTS_OFF EMPTY_TERMINALS '
                 'NO_LOAD NO_DUT NO_JUMPERS RECALL_OFF PSU_ONLY')
PROFILE_TOKEN = ('CONFIRM R5E2 APPLY_OFF_PROFILE CH1_V3.30_I0.150 '
                 'CH2_CH3_V1.00_I0.100 EMPTY_TERMINALS')
FINAL_TOKEN = 'CONFIRM R5E2 CH1=OFF CH2=OFF CH3=OFF EMPTY_TERMINALS'
TRIGGER_QUERIES = ('VOLT:TRIG?', 'CURR:TRIG?')


def query_token(channel):
    if type(channel) is not int or channel not in (1, 2, 3):
        raise ValueError('channel must be CH1, CH2 or CH3')
    return (f'CONFIRM R5E2 CH{channel} READ_VOLT_TRIG_AND_CURR_TRIG '
            'NO_TRIGGER_EVENT ALL_OUTPUTS_OFF EMPTY_TERMINALS')


class TriggerReadScope(R45SourceScope):
    """Extend a known OFF-only safety scope by exactly two targeted queries."""
    MAX_COMMANDS = 2100
    MAX_SECONDS = 540
    SHUTDOWN_RESERVE = 128

    def __init__(self, transport, evidence, channel):
        super().__init__(transport, evidence)
        query_token(channel)
        self.channel = channel
        self.query_authorized = False
        self.query_complete = False
        self.trigger_queries = []

    def _validate(self, command, query):
        if command in TRIGGER_QUERIES:
            if (not query or not self.query_authorized or self.cleanup or
                    self.selected != self.channel or
                    len(self.trigger_queries) >= len(TRIGGER_QUERIES) or
                    command != TRIGGER_QUERIES[len(self.trigger_queries)]):
                raise SafetyError('Unapproved trigger readback / selection / sequence')
            self.trigger_queries.append(command)
            return
        if command == 'SYST:LOC' and not self.query_complete:
            raise SafetyError('LOCAL only after complete trigger reads and verified OFF')
        return super()._validate(command, query)


def run_trigger_readback(transport, evidence, *, channel, profile_gate=None,
                         query_gate=None, final_gate=None):
    """One requested channel, OFF-only. Result is NOT independent HW review."""
    query_token(channel)
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    profile_started = off_confirmed = physical_off_confirmed = False
    outcome, reason = 'ABORTED', 'trigger readback incomplete'
    values = None
    try:
        if profile_gate is None or query_gate is None or final_gate is None:
            raise SafetyError('All independent operator gates are mandatory')
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda event: _redacted_trace(evidence, event),
            debug_adapter=lambda wire: TriggerReadScope(wire, evidence, channel))
        io = dev.debug_transport
        identity = dev.identity()
        parts = identity.split(',')
        if len(parts) != 4 or parts[1].strip() != '2231A-30-3':
            raise SafetyError('Unrecognized instrument identity')
        evidence.add('psu_identity', command='*IDN?',
                     model=parts[1].strip(), firmware=parts[-1].strip())
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError(f'Non-independent topology: {cmd}')
        profile_gate(PROFILE_TOKEN, io)
        evidence.add('operator_confirmation', gate='PROFILE')
        io.profile_authorized = True
        profile_started = True
        dev.apply_profile(_profile(), confirmed_wiring='independent')
        require_outputs(dev)
        query_gate(query_token(channel), io)
        evidence.add('operator_confirmation', gate='QUERY', channel=channel)
        io.query_authorized = True
        readback = dev.read_trigger_presets(channel)
        io.query_authorized = False
        if io.trigger_queries != list(TRIGGER_QUERIES):
            raise SafetyError('Incomplete native trigger readback')
        require_outputs(dev)
        dev.verify_profile()
        values = {'channel': channel,
                  'voltage_v': readback.voltage_v,
                  'current_a': readback.current_a,
                  'queries': list(io.trigger_queries),
                  'active_setpoints_not_triggered': True,
                  'analog_measurement': 'NOT_RUN'}
        dev.emergency_off()
        require_outputs(dev)
        off_confirmed = True
        io.query_complete = True
        if evidence.lost:
            raise SafetyError('Evidence incomplete')
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(values, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('results', values=values, results_sha256=digest)
        final_gate(FINAL_TOKEN)
        physical_off_confirmed = True
        evidence.add('operator_confirmation', gate='FINAL')
        if evidence.lost:
            raise SafetyError('Evidence incomplete after final gate')
        outcome, reason = 'PASS', 'OFF-only preset readback; independent audit pending'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException:
            pass
    finally:
        if profile_started and io is not None and io.live and not off_confirmed:
            evidence.cleanup = io.cleanup = True
            try:
                dev.emergency_off()
                require_outputs(dev)
                off_confirmed = True
            except BaseException as exc:
                reason += f'; OFF not verified: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; UART UNKNOWN: operator must physically confirm OFF'
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
            evidence.add('outcome', phase='TRIGGER_PRESET_OFF_READ', channel=channel,
                         outcome=outcome, reason=reason,
                         link_known=io.live if io is not None else False,
                         digital_off_confirmed=off_confirmed,
                         physical_off_confirmed=physical_off_confirmed,
                         hardware_review='PENDING',
                         note='OFF-only readback, not trigger actuation')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R5-E2 CH{channel} {outcome}: {reason}; physically verify all outputs OFF')
    return outcome
