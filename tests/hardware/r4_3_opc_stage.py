"""R4.3 *OPC? one-shot idle query. Hardware requires explicit operator gates."""
from __future__ import annotations

import hashlib
import json
import re
import time

from common import require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_status_stage import _redacted_trace
from r4_3_psc_stage import R43PscScope

STAGE = 'R43OPC'
INITIAL_TOKEN = ('CONFIRM R43OPC ALL_OUTPUTS_OFF NO_LOAD NO_DUT NO_JUMPERS '
                 'INDEPENDENT RECALL_OFF PSU_IDLE')
FINAL_TOKEN = 'CONFIRM R43OPC CH1=OFF CH2=OFF CH3=OFF'


class R43OpcScope(R43PscScope):
    """Allow only one idle *OPC?; reject *OPC write, *WAI and all output writes."""
    READS = (R43PscScope.READS - {'*PSC?'}) | {'*OPC?'}
    MAX_COMMANDS = 100
    MAX_SECONDS = 50

    def _transfer(self, command, query):
        if not (query and command == '*OPC?'):
            return super()._transfer(command, query)
        self.commands += 1
        if not self.live:
            raise OSError('Uncertain link; no more SCPI')
        if (self.cleanup or self.commands > self.MAX_COMMANDS or
                time.monotonic() - self.started > self.MAX_SECONDS):
            raise TimeoutError('R4.3 OPC wire budget exceeded')
        self._validate(command, True)
        self.evidence.add('query', command=command)
        started = time.monotonic()
        try:
            reply = self.transport.query(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        elapsed = time.monotonic() - started
        # A reply of 0 or a non-integer is NOT an accepted completion.
        # Validate before the automatically injected SYST:ERR? query.
        if not isinstance(reply, str) or re.fullmatch(r' *1 *', reply) is None:
            self.live = False
            self.evidence.add('invalid_scpi_reply', command=command,
                              classification='UNKNOWN OUTPUT STATE; no next I/O')
            raise SafetyError('Unexpected *OPC? reply; no retry on this session')
        record = self.evidence.add('read', command=command, response=reply,
                                   elapsed_s=elapsed)
        print(f'{command}: {record["response"]} (elapsed {elapsed:.3f} s)')
        error = super()._transfer('SYST:ERR?', True)
        code, message = error.split(',', 1)
        if int(code.strip()) != 0 or not message.strip().strip('"'):
            raise SafetyError(f'SCPI error after *OPC?: {error}')
        return reply


def run_opc(transport, evidence, *, final_gate=None):
    """Idle OFF-only single query; no implicit output OFF on any path."""
    dev = scope = None
    outcome, reason = 'ABORTED', 'incomplete R4.3 OPC qualification'
    results = {}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda channel: R43OpcScope(channel, evidence))
        scope = dev.debug_transport
        original_id = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=original_id)
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(cmd)):
                raise SafetyError(f'Independent-only R4.3 OPC: {cmd} ON')
        selected = dev.diagnostic_query('INST:NSEL?')
        if selected not in ('1', '2', '3'):
            raise SafetyError('Invalid selected channel')
        results['enable_before'] = {
            'ESE': dev.status_enable_mask('standard_event'),
            'SRE': dev.status_enable_mask('service_request')}
        results['complete'] = dev.operation_complete()
        results['enable_after'] = {
            'ESE': dev.status_enable_mask('standard_event'),
            'SRE': dev.status_enable_mask('service_request')}
        if results['enable_before'] != results['enable_after']:
            raise SafetyError('*OPC? changed ESE/SRE enable masks')
        if dev.diagnostic_query('INST:NSEL?') != selected:
            raise SafetyError('*OPC? changed selected channel')
        require_outputs(dev)
        if dev.identity().split(',')[2].strip() != original_id.split(',')[2].strip():
            raise SafetyError('Physical Keithley identity drift')
        if dev.faulted or not scope.live:
            raise SafetyError('Driver/transport fault')
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(results, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()
        evidence.add('results', values=results, results_sha256=digest)
        if final_gate is None:
            raise SafetyError('Missing physical post-LOCAL confirmation')
        final_gate(FINAL_TOKEN)
        outcome, reason = 'PASS', 'one idle *OPC?=1; hardware review required'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            (scope if scope is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='OPC_IDLE_QUERY',
                         physical_off_confirmed=outcome == 'PASS',
                         link_known=scope.live if scope is not None else False,
                         note='No power writes; uncertain link requires physical OFF')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.3 OPC {outcome}: {reason}; physically verify all outputs OFF')
    return outcome
