"""R4.3: one *PSC? query, physically OFF, single PSU, human-gated.

Import is offline; do not call this stage without explicit operator approval.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from common import StageAdapter, require_outputs
from keithley2231a import Keithley2231A, SafetyError
from r4_status_stage import _redacted_trace

STAGE = 'R43PSC'
INITIAL_TOKEN = ('CONFIRM R43PSC ALL_OUTPUTS_OFF NO_LOAD NO_DUT NO_JUMPERS '
                 'INDEPENDENT RECALL_OFF PSU_ONLY')
FINAL_TOKEN = 'CONFIRM R43PSC CH1=OFF CH2=OFF CH3=OFF'


class R43PscScope(StageAdapter):
    """Exact on-wire whitelist. No *PSC SET, resets or event consumption."""
    READS = frozenset({
        '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
        'OUTP:TRAC?', 'INST:NSEL?', 'CHAN:OUTP?',
        '*ESE?', '*SRE?', '*PSC?',
    })
    WRITES = frozenset({'SYST:REM', 'SYST:LOC',
                        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'})
    MAX_COMMANDS = 110
    SHUTDOWN_RESERVE = 0  # This stage never issues automatic OFF/shutdown.
    MAX_SECONDS = 55

    def _validate(self, command, query):
        # StageAdapter normally special-cases OUTP:SER?; no bypass here.
        if command not in (self.READS if query else self.WRITES):
            raise SafetyError(f'R4.3 PSC scope forbidden SCPI: {command}')

    def _transfer(self, command, query):
        # A floating-point/scientific reply can pass StageAdapter's generic
        # numeric check. Validate *PSC? BEFORE its automatic SYST:ERR? poll.
        if not (query and command == '*PSC?'):
            return super()._transfer(command, query)
        self.commands += 1
        if not self.live:
            raise OSError('Uncertain link; no further SCPI')
        if (self.cleanup or self.commands > self.MAX_COMMANDS or
                time.monotonic() - self.started > self.MAX_SECONDS):
            raise TimeoutError('R4.3 PSC wire budget exceeded')
        self._validate(command, True)
        self.evidence.add('query', command=command)
        try:
            reply = self.transport.query(command)
        except BaseException as exc:
            self.live = False
            self.evidence.add('io_error', command=command,
                              exception_type=type(exc).__name__)
            raise
        if not isinstance(reply, str) or re.fullmatch(r'[01]', reply.strip()) is None:
            self.live = False
            self.evidence.add('invalid_scpi_reply', command=command,
                              classification='UNKNOWN OUTPUT STATE; no following I/O')
            raise SafetyError('Malformed *PSC? reply; session uncertain, no retry')
        record = self.evidence.add('read', command=command, response=reply)
        print(f'{command}: {record["response"]}')
        # Shared StageAdapter performs the single SCPI-error poll.
        error = super()._transfer('SYST:ERR?', True)
        code, message = error.split(',', 1)
        if int(code.strip()) != 0 or not message.strip().strip('"'):
            raise SafetyError(f'SCPI error after *PSC?: {error}')
        return reply


def run_psc(transport, evidence, *, final_gate=None):
    """One *PSC? physical observation; never alter outputs or status flags."""
    dev = scope = None
    outcome, reason = 'ABORTED', 'incomplete R4.3 PSC qualification'
    results = {}
    try:
        dev = Keithley2231A(
            transport, debug=True,
            debug_trace=lambda record: _redacted_trace(evidence, record),
            debug_adapter=lambda channel: R43PscScope(channel, evidence))
        scope = dev.debug_transport
        original_id = dev.identity()
        evidence.add('psu_identity', command='*IDN?', response=original_id)
        require_outputs(dev)
        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            response = dev.diagnostic_query(cmd)
            if dev._bool(response):
                raise SafetyError(f'Independent-only R4.3 PSC: {cmd}={response}')
        selected = dev.diagnostic_query('INST:NSEL?')
        if selected not in ('1', '2', '3'):
            raise SafetyError('Invalid original channel selection')
        results['enable_before'] = {
            'ESE': dev.status_enable_mask('standard_event'),
            'SRE': dev.status_enable_mask('service_request')}
        results['psc'] = dev.power_on_status_clear_state()
        results['enable_after'] = {
            'ESE': dev.status_enable_mask('standard_event'),
            'SRE': dev.status_enable_mask('service_request')}
        if results['enable_before'] != results['enable_after']:
            raise SafetyError('*PSC? unexpectedly changed status enable masks')
        if dev.diagnostic_query('INST:NSEL?') != selected:
            raise SafetyError('*PSC? changed selected channel')
        require_outputs(dev)
        if dev.identity().split(',')[2].strip() != original_id.split(',')[2].strip():
            raise SafetyError('PSU physical identity changed')
        if dev.faulted or not scope.live:
            raise SafetyError('R4.3 driver/transport fault')
        # Diagnostic close, not dev.close(): the latter emits OUTP 0.
        dev.diagnostic_write('SYST:LOC')
        digest = hashlib.sha256(json.dumps(results, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()
        evidence.add('results', values=results, results_sha256=digest)
        if final_gate is None:
            raise SafetyError('Missing operator post-LOCAL OFF gate')
        final_gate(FINAL_TOKEN)
        outcome, reason = 'PASS', 'one bounded *PSC? observation; await review'
    except BaseException as exc:
        reason = f'{type(exc).__name__}: {exc}'
    finally:
        try:
            (scope if scope is not None else transport).close()
        except BaseException as exc:
            outcome, reason = 'ABORTED', f'Close failed: {type(exc).__name__}'
        try:
            evidence.add('outcome', outcome=outcome, reason=reason,
                         phase='PSC_QUERY_ONLY',
                         physical_off_confirmed=outcome == 'PASS',
                         link_known=scope.live if scope is not None else False,
                         note='No output writes; UNKNOWN requires independent physical OFF')
        except BaseException:
            outcome = 'ABORTED'
    print(f'R4.3 PSC {outcome}: {reason}; verify outputs physically OFF')
    return outcome
