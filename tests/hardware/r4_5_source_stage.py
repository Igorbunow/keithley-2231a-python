"""R4.5-B: operator-gated OFF-only INST? and optional STEP readback.

The --steps phase changes OFF-channel presets through the existing independent
profile; it never enables outputs. No hardware I/O is performed on import.
"""
from __future__ import annotations

import hashlib
import json
import time

from common import ShutdownEvidence, StageAdapter, require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_4_scalar_stage import R44ScalarScope, _profile
from r4_status_stage import _redacted_trace

STAGE = 'R45SOURCE'
INITIAL_TOKEN = ('CONFIRM R45SOURCE ALL_OUTPUTS_OFF EMPTY_TERMINALS NO_LOAD '
                 'NO_DUT NO_JUMPERS INDEPENDENT RECALL_OFF PSU_ONLY')
PROFILE_TOKEN = ('CONFIRM R45SOURCE APPLY_OFF_PROFILE CH1_V3.30_I0.150 '
                 'CH2_CH3_V1.00_I0.100 ALL_OUTPUTS_OFF NO_WIRES')
FINAL_TOKEN = 'CONFIRM R45SOURCE CH1=OFF CH2=OFF CH3=OFF'


class R45SourceScope(R44ScalarScope):
    """Fixed SCPI envelope: profile configuration is gated, ON is impossible."""
    READS = frozenset({
        '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
        'OUTP:TRAC?', 'INST:NSEL?', 'INST?', 'CHAN:OUTP?',
        'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
        'OUTP:TIM:STAT?', 'VOLT:STEP?', 'CURR:STEP?',
    })
    WRITES = R44ScalarScope.WRITES - {'CHAN:OUTP 1'}
    MAX_COMMANDS = 720
    MAX_SECONDS = 180
    SHUTDOWN_RESERVE = 95

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.profile_authorized = False
        self.inst_reads = 0
        self.step_reads = []

    def _validate(self, command, query):
        if command not in (self.READS if query else self.WRITES):
            raise SafetyError(f'R4.5 prohibited SCPI: {command}')
        if not self.profile_authorized:
            permitted_writes = {'SYST:REM', 'SYST:LOC',
                                'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'}
            if not query and command not in permitted_writes:
                raise SafetyError(f'Profile writes require second physical gate: {command}')
            if query and command in ('VOLT:STEP?', 'CURR:STEP?'):
                raise SafetyError('STEP query requires active independent profile')
        if not query and command == 'SYST:LOC' and (any(self.outputs.values()) or self.cleanup):
            raise SafetyError('LOCAL forbidden before safe OFF verification')
        if query and command in ('VOLT:STEP?', 'CURR:STEP?'):
            if command in self.step_reads:
                raise SafetyError('Duplicate STEP query is not permitted')
            self.step_reads.append(command)

    def _transfer(self, command, query):
        if command != 'INST?' or not query:
            return StageAdapter._transfer(self, command, query)
        # Shared adapter accepts numeric replies only; INST? is a text enum.
        # Validate BEFORE SYST:ERR? to prevent further commands after garbage.
        self.commands += 1
        if not self.live:
            raise OSError('Uncertain UART link; no further SCPI')
        if (self.cleanup or self.commands > self.MAX_COMMANDS or
                time.monotonic() - self.started > self.MAX_SECONDS):
            raise TimeoutError('R4.5 INST? command budget exceeded')
        self._validate(command, True)
        if self.inst_reads:
            raise SafetyError('INST? must be queried exactly once')
        self.inst_reads += 1
        self.evidence.add('query', command=command)
        try:
            reply = self.transport.query(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        if not isinstance(reply, str) or reply not in ('CH1', 'CH2', 'CH3'):
            self.live = False
            self.evidence.add('invalid_scpi_reply', command=command,
                              classification='UNTRUSTED LINK; no further I/O')
            raise SafetyError('Invalid INST? reply; no following error poll or retry')
        record = self.evidence.add('read', command=command, response=reply)
        print(f'{command}: {record["response"]}')
        error = StageAdapter._transfer(self, 'SYST:ERR?', True)
        code, _, message = error.partition(',')
        if code.strip() != '0' or not message.strip().strip('"'):
            raise SafetyError(f'SCPI error after INST?: {error}')
        return reply


def run_source(transport, evidence, *, steps=False, profile_gate=None, final_gate=None):
    """Perform one INST? OFF observation and optionally two CH1 STEP reads.

    The operator explicitly authorizes changes to OFF-channel presets if steps
    are requested. No output-enable command is in the wire allowlist.
    """
    evidence = ShutdownEvidence(evidence)
    dev = io = None
    started_profile = False
    outcome, reason = 'ABORTED', 'R4.5-B incomplete'
    values = {'step_phase': 'NOT_RUN'}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda wire: R45SourceScope(wire, evidence))
        io = dev.debug_transport
        ident = dev.identity()
        # Only record redacted identity to protect serial number.
        fields = ident.split(',')
        evidence.add('psu_identity', command='*IDN?',
                     model=fields[1].strip(), firmware=fields[-1].strip())
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError(f'Independent topology required: {cmd}=1')
        selected = dev.diagnostic_query('INST:NSEL?')
        if selected not in ('1', '2', '3'):
            raise SafetyError('Invalid selected channel')
        name = dev.selected_channel_name()
        if name != 'CH' + selected:
            raise SafetyError('INST? and INST:NSEL? disagree')
        if dev.diagnostic_query('INST:NSEL?') != selected:
            raise SafetyError('INST? unexpectedly changed selected channel')
        values['selected_channel'] = name
        values['inst_reads'] = io.inst_reads
        require_outputs(dev)
        if steps:
            if profile_gate is None:
                raise SafetyError('Separate physical OFF profile gate is required')
            profile_gate(PROFILE_TOKEN, io)
            evidence.add('operator_confirmation', gate='PROFILE')
            # Profile writes are permitted only after the second gate.
            io.profile_authorized = True
            started_profile = True
            dev.apply_profile(_profile(), confirmed_wiring='independent')
            require_outputs(dev)
            values['step_voltage_v'] = dev.source_step(1, 'voltage')
            require_outputs(dev)
            values['step_current_a'] = dev.source_step(1, 'current')
            require_outputs(dev)
            if io.step_reads != ['VOLT:STEP?', 'CURR:STEP?']:
                raise SafetyError('STEP queries not exactly once in expected order')
            values['step_phase'] = 'OBSERVED_OFF_ONLY'
            values['step_reads'] = list(io.step_reads)
        if dev.faulted or not io.live:
            raise SafetyError('Driver or link fault')
        if started_profile:
            # Always return to confirmed OFF after profile manipulation.
            dev.emergency_off()
            require_outputs(dev)
        else:
            require_outputs(dev)
        if not evidence.lost:
            dev.diagnostic_write('SYST:LOC')
        else:
            raise SafetyError('Evidence lost; cannot declare PASS')
        digest = hashlib.sha256(json.dumps(values, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('results', values=values, results_sha256=digest,
                     note='STEP values observational; no write or UP/DOWN tested')
        if final_gate is None:
            raise SafetyError('Physical OFF confirmation required')
        final_gate(FINAL_TOKEN)
        if evidence.lost:
            raise SafetyError('Evidence failed')
        outcome, reason = 'PASS', 'one INST?; STEP CH1 only if separately approved'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
        try:
            evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException:
            pass
    finally:
        if started_profile and io is not None and io.live and outcome != 'PASS':
            # Evidence failure must not inhibit shutdown. Never retry after
            # UART uncertainty, nor imply OFF on an untrusted connection.
            evidence.cleanup = True
            io.cleanup = True
            try:
                dev.emergency_off()
                require_outputs(dev)
            except BaseException as exc:
                reason += f'; PSU OFF unconfirmed: {type(exc).__name__}'
        if io is not None and not io.live:
            reason += '; link uncertain: physically confirm PSU OFF'
        try:
            (io if io is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        if evidence.lost:
            outcome, reason = 'ABORTED', reason + '; incomplete evidence'
        evidence.cleanup = True
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='STEP_OFF_PROFILE' if steps else 'INST_OFF_QUERY_ONLY',
                         physical_off_confirmed=outcome == 'PASS',
                         link_known=io.live if io is not None else False,
                         note='One stable OFF observation, not a powered qualification')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.5 SOURCE {outcome}: {reason}; physically check CH1/CH2/CH3 OFF')
    return outcome
