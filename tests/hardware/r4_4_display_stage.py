"""R4.4-B: one operator-gated DISP? observation, PSU OFF and disconnected.

No output/state/setpoint writes or automatic OFF. This is deliberately NOT the
powered scalar/measurement qualification (which requires a separately approved
profile/load and external meter). Importing this module has no hardware I/O.
"""
from __future__ import annotations

import hashlib
import json
import time

from common import require_outputs, StageAdapter
from keithley2231a import Keithley2231A, SafetyError
from r4_status_stage import _redacted_trace
from r4_3_psc_stage import R43PscScope

STAGE = 'R44DISPLAY'
INITIAL_TOKEN = ('CONFIRM R44DISPLAY ALL_OUTPUTS_OFF NO_LOAD NO_DUT NO_JUMPERS '
                 'INDEPENDENT RECALL_OFF PSU_ONLY')
FINAL_TOKEN = 'CONFIRM R44DISPLAY CH1=OFF CH2=OFF CH3=OFF'


class R44DisplayScope(R43PscScope):
    """Exact read-only SCPI envelope; strictly validate DISP? before error poll."""
    READS = frozenset({'*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                       'OUTP:TRAC?', 'INST:NSEL?', 'CHAN:OUTP?', 'DISP?'})
    WRITES = frozenset({'SYST:REM', 'SYST:LOC', 'INST:NSEL 1',
                        'INST:NSEL 2', 'INST:NSEL 3'})
    MAX_COMMANDS = 105
    MAX_SECONDS = 55
    SHUTDOWN_RESERVE = 0

    def __init__(self, transport, evidence):
        super().__init__(transport, evidence)
        self.display_reads = 0

    def _transfer(self, command, query):
        if not (query and command == 'DISP?'):
            return StageAdapter._transfer(self, command, query)
        self.commands += 1
        if not self.live:
            raise OSError('Uncertain link; no further SCPI')
        if (self.cleanup or self.commands > self.MAX_COMMANDS or
                time.monotonic() - self.started > self.MAX_SECONDS):
            raise TimeoutError('R4.4 display query budget exceeded')
        self._validate(command, True)
        if self.display_reads:
            raise SafetyError('DISP? must be queried exactly once')
        self.display_reads += 1
        self.evidence.add('query', command=command)
        try:
            reply = self.transport.query(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        if not isinstance(reply, str) or reply.strip().upper() not in ('0', '1', 'ON', 'OFF'):
            self.live = False
            self.evidence.add('invalid_scpi_reply', command=command,
                              classification='UNKNOWN OUTPUT STATE; no following I/O')
            raise SafetyError('Malformed DISP? reply; no retry or error poll')
        self.evidence.add('read', command=command, response=reply)
        # As with every other stage operation, use the common single error poll;
        # it drains the SCPI error queue, not status/event latches.
        error = StageAdapter._transfer(self, 'SYST:ERR?', True)
        code, _, message = error.partition(',')
        if code.strip() != '0' or not message.strip():
            raise SafetyError(f'SCPI error after DISP?: {error}')
        return reply


def run_display(transport, evidence, *, final_gate=None):
    """One bounded OFF-only query through the real driver; caller owns approval."""
    dev = scope = None
    outcome, reason = 'ABORTED', 'incomplete R4.4 display qualification'
    observed = {}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda channel: R44DisplayScope(channel, evidence))
        scope = dev.debug_transport
        identity = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=identity)
        require_outputs(dev)
        for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(command)):
                raise SafetyError(f'Independent topology required: {command} ON')
        selected = dev.diagnostic_query('INST:NSEL?')
        if selected not in ('1', '2', '3'):
            raise SafetyError('Invalid selected channel')
        observed['display_enabled'] = dev.display_enabled()
        observed['display_query_count'] = scope.display_reads
        if dev.diagnostic_query('INST:NSEL?') != selected:
            raise SafetyError('DISP? changed selected channel')
        require_outputs(dev)
        if dev.identity().split(',')[2].strip() != identity.split(',')[2].strip():
            raise SafetyError('Keithley physical identity drift')
        if dev.faulted or not scope.live or scope.display_reads != 1:
            raise SafetyError('Driver or link fault')
        # dev.close() is intentionally NOT called: it would write OUTP 0.
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(observed, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
        evidence.add('results', values=observed, results_sha256=digest)
        if final_gate is None:
            raise SafetyError('Physical post-LOCAL OFF confirmation is required')
        final_gate(FINAL_TOKEN)
        outcome, reason = 'PASS', 'one DISP? observation; evidence awaits hardware review'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            (scope if scope is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='DISPLAY_OFF_QUERY_ONLY',
                         physical_off_confirmed=outcome == 'PASS',
                         link_known=scope.live if scope is not None else False,
                         note='No output writes; UNKNOWN requires independent manual OFF check')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.4 DISPLAY {outcome}: {reason}; physically verify CH1/CH2/CH3 OFF')
    return outcome
