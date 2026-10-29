"""Offline, fail-closed review of one R4.4-B display hardware evidence file.

No instrument access and no changes to the evidence. This verifies recorded
claims, not physical reality independently of the operator's confirmation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ALLOWED_EVENTS = frozenset({
    'start', 'pre_open_approvals', 'query', 'write', 'read', 'driver_trace',
    'psu_identity', 'results', 'operator_confirmation', 'outcome',
})
READS = frozenset({
    '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
    'OUTP:TRAC?', 'INST:NSEL?', 'CHAN:OUTP?', 'DISP?',
})
WRITES = frozenset({
    'SYST:REM', 'SYST:LOC', 'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
})


class EvidenceAuditError(ValueError):
    pass


def ensure(condition: bool, reason: str) -> None:
    if not condition:
        raise EvidenceAuditError(reason)


def audit(records: list[dict]) -> dict:
    ensure(len(records) >= 10, 'evidence too short')
    ensure(all(type(r) is dict for r in records), 'record is not a dictionary')
    ensure(all(r.get('test_id') == 'R44DISPLAY' for r in records), 'mixed test IDs')
    ensure(all(r.get('event') in ALLOWED_EVENTS for r in records),
           'unexpected or failure event present')
    times = [r.get('monotonic_s') for r in records]
    ensure(all(type(t) in (float, int) for t in times), 'missing timestamps')
    ensure(all(b >= a for a, b in zip(times, times[1:])), 'timestamps out of order')

    def one(event):
        found = [r for r in records if r['event'] == event]
        ensure(len(found) == 1, f'expected exactly one {event}')
        return found[0]

    start = one('start')
    approved = one('pre_open_approvals')
    results = one('results')
    final = one('operator_confirmation')
    outcome = one('outcome')
    ensure(records[0] is start and records[-1] is outcome,
           'start/outcome must bracket all events')
    ensure(start.get('variant') == 'DISPLAY_OFF_QUERY_ONLY', 'incorrect phase')
    ensure(approved.get('approved_before_evidence_open') is True and
           approved.get('gates') == ['BENCH', 'INITIAL'] and
           approved.get('config_sha256') == start.get('config_sha256'),
           'invalid initial approvals/config provenance')
    ensure(final.get('gate') == 'FINAL', 'missing final operator gate')
    ensure(outcome.get('outcome') == 'PASS' and
           outcome.get('phase') == 'DISPLAY_OFF_QUERY_ONLY' and
           outcome.get('physical_off_confirmed') is True and
           outcome.get('link_known') is True,
           'runner did not complete confirmed PASS')

    seq = [(r['event'], r['command']) for r in records if r['event'] in ('query','write','read')]
    ensure(all(c in (READS if e != 'write' else WRITES) for e,c in seq),
           'SCPI command outside allowlist')
    queries = [r for r in records if r['event'] == 'query']
    reads = [r for r in records if r['event'] == 'read']
    writes = [r for r in records if r['event'] == 'write']
    ensure(Counter(r['command'] for r in reads) ==
           Counter(r['command'] for r in queries), 'missing/extra query reply')
    trace = [r['trace'] for r in records if r['event'] == 'driver_trace']
    ensure(all(type(t) is dict and t.get('event') in ('query','write','reply') for t in trace),
           'unexpected driver trace')
    ensure(Counter(t['command'] for t in trace if t['event']=='write') ==
           Counter(r['command'] for r in writes), 'write/trace mismatch')
    ensure(Counter(t['command'] for t in trace if t['event']=='query') ==
           Counter(r['command'] for r in queries), 'query/trace mismatch')
    ensure(Counter(t['command'] for t in trace if t['event']=='reply') ==
           Counter(r['command'] for r in reads), 'response/trace mismatch')
    for cmd in READS:
        ensure([t['response'] for t in trace if t['event']=='reply' and t['command']==cmd] ==
               [r['response'] for r in reads if r['command']==cmd],
               f'conflicting trace/stage replies for {cmd}')

    waiting = None
    selected = None
    output_channels = []
    display_replies = []
    local_index = None
    for i, t in enumerate(trace):
        event, cmd = t['event'], t['command']
        if event == 'query':
            ensure(waiting is None and local_index is None, 'overlapping/after-LOCAL query')
            ensure(cmd in READS, 'unapproved trace query')
            waiting = cmd
        elif event == 'write':
            ensure(waiting is None and local_index is None, 'overlapping/after-LOCAL write')
            ensure(cmd in WRITES, 'unapproved trace write')
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1])
            if cmd == 'SYST:LOC':
                local_index = i
        else:
            ensure(waiting == cmd and local_index is None, 'reply without matching query')
            waiting = None
            answer = t['response'].strip()
            if cmd == 'INST:NSEL?' and selected is not None:
                ensure(answer == str(selected), 'selected channel readback mismatch')
            if cmd == 'CHAN:OUTP?':
                ensure(selected in (1,2,3), 'output checked without explicit channel')
                ensure(answer == '0', 'channel output not OFF')
                output_channels.append(selected)
            if cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
                ensure(answer == '0', f'combined topology enabled: {cmd}')
            if cmd == 'SYST:ERR?':
                code, sep, message = answer.partition(',')
                ensure(sep and code.strip() == '0' and message.strip().strip('"') == 'No error',
                       'non-zero or malformed error queue response')
            if cmd == 'DISP?':
                ensure(answer.upper() in ('0','1','OFF','ON'), 'invalid display readback')
                display_replies.append(answer)
    ensure(waiting is None and local_index == len(trace)-1, 'unfinished query or LOCAL not last')
    ensure([t['command'] for t in trace if t['event'] == 'write'
            and t['command'] in ('SYST:REM', 'SYST:LOC')] ==
           ['SYST:REM', 'SYST:LOC'], 'remote/local session is incomplete')
    ensure(Counter(t['command'] for t in trace if t['event'] == 'query'
                   and t['command'].startswith('OUTP:')) ==
           Counter({'OUTP:SER?': 3, 'OUTP:PARALLEL?': 1, 'OUTP:TRAC?': 1}),
           'missing combined-mode topology check')
    ensure(output_channels == [1,2,3,1,2,3], 'missing/incorrect two OFF sweeps')
    ensure(len(display_replies) == 1, 'expected one DISP? reply')
    display_index = next(i for i,t in enumerate(trace) if t['event']=='reply' and t['command']=='DISP?')
    outputs = [i for i,t in enumerate(trace) if t['event']=='reply' and t['command']=='CHAN:OUTP?']
    ensure(outputs[2] < display_index < outputs[3], 'DISP? outside OFF checks')
    err_count = sum(t['event']=='reply' and t['command']=='SYST:ERR?' for t in trace)
    normal_queries = sum(t['event']=='query' and t['command']!='SYST:ERR?' for t in trace)
    ensure(err_count == normal_queries + len(writes)-1,
           'missing/extra SYST:ERR? polls (except after LOCAL)')
    identities = [r['response'] for r in reads if r['command']=='*IDN?']
    ensure(len(identities) == 2 and identities[0] == identities[1] and
           '2231A-30-3' in identities[0] and '[REDACTED]' in identities[0],
           'identity drift, wrong model or unredacted identity')
    ensure(outcome.get('firmware_observations') == [identities[0].split(',')[-1].strip()],
           'firmware provenance mismatch')
    ensure(len([r for r in reads if r['command']=='DISP?']) == 1,
           'missing stage display reply')
    expected_enabled = display_replies[0].upper() in ('1','ON')
    values = results.get('values')
    ensure(values == {'display_enabled': expected_enabled, 'display_query_count': 1},
           'result values do not match display reply')
    digest = hashlib.sha256(json.dumps(values, sort_keys=True,
             separators=(',', ':')).encode('ascii')).hexdigest()
    ensure(results.get('results_sha256') == digest, 'results hash mismatch')
    loc_time = next(r['monotonic_s'] for r in records if r['event']=='write' and r['command']=='SYST:LOC')
    ensure(loc_time < final['monotonic_s'] < outcome['monotonic_s'],
           'post-LOCAL operator gate missing or out of order')
    ensure(len([r for r in records if r['event']=='psu_identity']) == 1,
           'missing/multiple PSU identity result')
    return {
        'status': 'REVIEWABLE_PASS',
        'display_enabled': expected_enabled,
        'display_reply': display_replies[0],
        'firmware': outcome['firmware_observations'],
        'queries': len(queries), 'writes': len(writes),
        'error_polls': err_count,
        'output_checks': len(output_channels),
        'source_commit': start.get('software', {}).get('sha'),
        'source_dirty': start.get('software', {}).get('dirty'),
        'config_sha256': start.get('config_sha256'),
    }


def audit_file(path: Path, expected_sha256: str | None = None) -> dict:
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None:
        ensure(digest == expected_sha256.lower(), 'evidence SHA256 mismatch')
    try:
        records = [json.loads(line) for line in content.splitlines()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise EvidenceAuditError('invalid JSONL') from exc
    result = audit(records)
    result['evidence_sha256'] = digest
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--expected-sha256')
    args = parser.parse_args(argv)
    try:
        result = audit_file(args.evidence, args.expected_sha256)
    except (OSError, EvidenceAuditError) as exc:
        print(f'R4.4-B EVIDENCE AUDIT FAIL: {exc}')
        return 2
    print('R4.4-B EVIDENCE AUDIT REVIEWABLE PASS (not independent physical proof)')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
