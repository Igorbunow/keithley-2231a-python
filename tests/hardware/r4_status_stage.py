"""R4.2 single-PSU, manually gated hardware status qualification.

Read-only and read-to-clear phases are independent. Importing this module never
opens an instrument. This module is NOT part of the installable PSU package.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from common import StageAdapter, require_outputs
from keithley2231a import Keithley2231A, SafetyError

STAGE = 'R4STATUS'
INITIAL_TOKEN = ('CONFIRM R4STATUS ALL_OUTPUTS_OFF NO_LOAD NO_DUT NO_JUMPERS '
                 'INDEPENDENT RECALL_OFF PSU_ONLY')
EVENT_TOKEN = ('CONFIRM R4STATUS CONSUME THREE EVENT_LATCHES '
               'IRREVERSIBLE_CLEAR ALL_OUTPUTS_OFF')
FINAL_TOKEN = 'CONFIRM R4STATUS CH1=OFF CH2=OFF CH3=OFF'

MASKS = ('standard_event', 'service_request', 'questionable', 'operation')
NESTED = ('questionable', 'operation')
EVENTS = ('standard_event', 'questionable', 'operation')

class R4StatusScope(StageAdapter):
    """Only status reads, selection for OFF verification, REM and final LOC.

    The shared adapter performs one SYST:ERR? after each command except ERR?
    and LOC. These reads consume the SCPI error queue, NOT the event latches.
    """
    MAX_COMMANDS = 210
    MAX_SECONDS = 90
    READS = frozenset({
        '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
        'OUTP:STAT:ALL?', 'INST:NSEL?', 'CHAN:OUTP?',
        'SYST:MODU?', '*ESE?', '*SRE?', 'STAT:QUES:ENAB?', 'STAT:OPER:ENAB?',
        '*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?',
        *[f'STAT:{group}:INST:ENAB?' for group in ('QUES', 'OPER')],
        *[f'STAT:{group}:INST:ISUM{ch}:ENAB?' for group in ('QUES', 'OPER') for ch in (1, 2, 3)],
    })
    WRITES = frozenset({'SYST:REM', 'SYST:LOC',
                        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'})

    def __init__(self, transport, evidence, *, allow_events=False):
        super().__init__(transport, evidence)
        self.allow_events = allow_events
        self.event_authorized = False

    def _validate(self, command, query):
        # Unlike StageAdapter, the existing OUTP:SER? exception must NOT
        # bypass the explicit R4 allowlist if this stage is ever refactored.
        allowed = command in (self.READS if query else self.WRITES)
        if not allowed:
            raise SafetyError(f'R4 status scope forbidden command: {command}')
        if query and command in ('*ESR?', 'STAT:QUES:EVEN?', 'STAT:OPER:EVEN?'):
            if not self.allow_events or not self.event_authorized:
                raise SafetyError('Event latch reads require separately gated event-only run')

    def _transfer(self, command, query):
        # The common StageAdapter correctly parses numeric masks and status,
        # but SYST:MODU? is documented to return text like '2231,1.04'.
        if not (query and command == 'SYST:MODU?'):
            return super()._transfer(command, query)
        self.commands += 1
        if not self.live:
            raise OSError('Uncertain link; no further SCPI')
        if (self.cleanup or self.commands > self.MAX_COMMANDS or
                time.monotonic() - self.started > self.MAX_SECONDS):
            raise TimeoutError('R4 hardware status budget exceeded')
        self._validate(command, True)
        self.evidence.add('query', command=command)
        try:
            answer = self.transport.query(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        if (not isinstance(answer, str) or not answer.isascii() or
                not answer.strip() or len(answer) > 128 or
                any(ord(c) < 32 or ord(c) == 127 for c in answer)):
            self.live = False
            self.evidence.add('invalid_scpi_reply', command=command,
                              classification='UNKNOWN OUTPUT STATE; no follow-up I/O')
            raise SafetyError('Invalid SYST:MODU? response; no following commands')
        record = self.evidence.add('read', command=command, response=answer)
        print(f'{command}: {record["response"]}')
        check = super()._transfer('SYST:ERR?', True)
        if not re.fullmatch(r'0\s*,\s*.+', check):
            raise SafetyError(f'Instrument SCPI error after SYST:MODU?: {check}')
        return answer


def _redacted_trace(evidence, record):
    """Preserve diagnostic SCPI order without disclosing the PSU serial."""
    trace = dict(record)
    if trace.get('command') == '*IDN?' and 'response' in trace:
        parts = trace['response'].split(',') if isinstance(trace['response'], str) else []
        if len(parts) == 4:
            parts[2] = ' [REDACTED]'
            trace['response'] = ','.join(parts)
        else:
            trace['response'] = '[REDACTED MALFORMED IDN]'
    evidence.add('driver_trace', trace=trace)


def run_status(transport, evidence, *, consume_events=False, event_gate=None,
               final_gate=None):
    """Use exactly the public driver APIs; never change outputs or masks.

    Returns PASS/ABORTED, never automatically retries on uncertain I/O.
    Caller owns the transport and supplies independently authorized gates.
    """
    scope = None
    dev = None
    outcome, reason = 'ABORTED', 'incomplete R4 status qualification'
    results = {}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda channel: R4StatusScope(
                channel, evidence, allow_events=consume_events))
        scope = dev.debug_transport
        identity = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=identity)
        require_outputs(dev)
        # The driver may use a special combined OFF path; combined modes are
        # not in the qualification scope, regardless of the OFF readback.
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            response = dev.diagnostic_query(mode)
            if dev._bool(response):
                raise SafetyError(f'R4 requires independent topology: {mode}={response}')
        selected = dev.diagnostic_query('INST:NSEL?')
        if selected not in ('1', '2', '3'):
            raise SafetyError('Invalid selected channel at baseline')
        results['module'] = dev.module_info()
        results['enable'] = {name: dev.status_enable_mask(name) for name in MASKS}
        results['nested'] = {
            name: {'all': dev.status_instrument_enable_mask(name),
                   **{str(ch): dev.status_instrument_enable_mask(name, channel=ch)
                      for ch in (1, 2, 3)}} for name in NESTED}
        if dev.diagnostic_query('INST:NSEL?') != selected:
            raise SafetyError('R4 changed selected channel')
        require_outputs(dev)
        if consume_events:
            if event_gate is None:
                raise SafetyError('R4 destructive event gate missing')
            gate_started = time.monotonic()
            try:
                event_gate(EVENT_TOKEN)
            finally:
                # Operator approval does not consume the SCPI execution budget.
                scope.started += time.monotonic() - gate_started
            scope.event_authorized = True
            try:
                results['events'] = {
                    name: dev.consume_status_event(name, acknowledge_clear=True)
                    for name in EVENTS}
            finally:
                scope.event_authorized = False
            require_outputs(dev)
        if dev.identity().split(',')[2].strip() != identity.split(',')[2].strip():
            raise SafetyError('PSU serial identity changed')
        if dev.faulted or not scope.live:
            raise SafetyError('R4 driver/transport fault')
        # Never use dev.close(): close() intentionally sends OUTP 0, which is
        # forbidden here even if the channels were already OFF.
        dev.diagnostic_write('SYST:LOC')
        canonical = json.dumps(results, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=True).encode('utf-8')
        evidence.add('results', values=results,
                     results_sha256=hashlib.sha256(canonical).hexdigest())
        if final_gate is None:
            raise SafetyError('R4 final physical OFF confirmation missing')
        final_gate(FINAL_TOKEN)
        outcome, reason = 'PASS', 'bounded hardware status qualification complete'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            if scope is not None:
                scope.close()
            else:
                transport.close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='EVENT' if consume_events else 'READ_ONLY',
                         physical_off_confirmed=outcome == 'PASS',
                         link_known=(scope.live if scope is not None else False),
                         note='No output writes; UNKNOWN needs independent manual OFF check')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4 STATUS {outcome}: {reason}; check CH1/CH2/CH3 physical OFF')
    return outcome
