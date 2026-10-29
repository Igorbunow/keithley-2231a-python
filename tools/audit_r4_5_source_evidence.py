"""Fail-closed offline R4.5-B INST-only JSONL audit (never opens instruments).

An accepted file is evidence of one recorded OFF-only observation, not an
independent physical measurement. STEP runs require a separate review path.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

STAGE = 'R45SOURCE'
READS = frozenset({'*IDN?', 'SYST:ERR?', 'OUTP:SER?',
                   'OUTP:PARALLEL?', 'OUTP:TRAC?', 'INST:NSEL?',
                   'INST?', 'CHAN:OUTP?'})
WRITES = frozenset({'SYST:REM', 'SYST:LOC',
                    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'})
EVENTS = frozenset({'start', 'pre_open_approvals', 'query', 'write', 'read',
                    'driver_trace', 'psu_identity', 'results',
                    'operator_confirmation', 'outcome'})


class AuditError(ValueError):
    pass


def check(predicate, message):
    if not predicate:
        raise AuditError(message)


def audit(records):
    check(isinstance(records, list) and len(records) > 100, 'missing/short evidence')
    check(all(type(r) is dict and r.get('test_id') == STAGE for r in records),
          'incorrect/mixed test identifier')
    check(all(r.get('event') in EVENTS for r in records),
          'unapproved, incomplete, or error event present')
    times = [r.get('monotonic_s') for r in records]
    check(all(type(t) in (int, float) for t in times), 'missing timestamps')
    check(all(a <= b for a,b in zip(times, times[1:])), 'unordered timestamps')
    try:
        stamps = [datetime.fromisoformat(r['utc']) for r in records]
        check(all(t.tzinfo is not None for t in stamps), 'timezone missing')
        check(all(a <= b for a,b in zip(stamps, stamps[1:])), 'UTC timestamps unordered')
    except (ValueError, KeyError, TypeError) as exc:
        raise AuditError('invalid UTC chronology') from exc

    def one(event):
        matches = [r for r in records if r['event'] == event]
        check(len(matches) == 1, f'exactly one {event} required')
        return matches[0]

    start, pre, result, confirmation, outcome, identity = [one(name) for name in
        ('start', 'pre_open_approvals', 'results', 'operator_confirmation', 'outcome', 'psu_identity')]
    check(records[0] is start and records[-1] is outcome, 'evidence not bracketed')
    check(start.get('variant') == 'INST_OFF_QUERY_ONLY', 'unexpected or STEP phase')
    check(isinstance(start.get('config_sha256'), str) and
          len(start['config_sha256']) == 64, 'missing config digest')
    check(pre.get('gates') == ['BENCH', 'INITIAL'] and
          pre.get('approved_before_evidence_open') is True and
          pre.get('config_sha256') == start['config_sha256'], 'missing initial gates')
    check(confirmation.get('gate') == 'FINAL', 'missing final gate')
    check(outcome.get('outcome') == 'PASS' and
          outcome.get('phase') == 'INST_OFF_QUERY_ONLY' and
          outcome.get('physical_off_confirmed') is True and
          outcome.get('link_known') is True, 'incomplete or unsafe outcome')
    check(identity.get('model') == '2231A-30-3' and
          not identity.get('response'), 'incorrect or unredacted identity metadata')

    queries = [r for r in records if r['event'] == 'query']
    reads = [r for r in records if r['event'] == 'read']
    writes = [r for r in records if r['event'] == 'write']
    check(len(queries) == len(reads) == 75 and len(writes) == 14,
          'operation count differs from terminal-verified INST run')
    check(all(r.get('command') in READS for r in queries + reads) and
          all(r.get('command') in WRITES for r in writes), 'SCPI command outside allowlist')
    check([(r['command'],r['response']) for r in reads] and
          Counter(r['command'] for r in queries) == Counter(r['command'] for r in reads),
          'query/read counts mismatch')
    traces = [r['trace'] for r in records if r['event'] == 'driver_trace']
    check(all(type(t) is dict and t.get('event') in ('query','reply','write') for t in traces),
          'invalid driver trace')
    for event, highlevel in [('query',queries), ('reply',reads), ('write',writes)]:
        low = [t for t in traces if t.get('event') == event]
        check(Counter(t.get('command') for t in low) == Counter(r['command'] for r in highlevel),
              f'trace/highlevel {event} operation mismatch')
        if event == 'reply':
            check([t.get('response') for t in low] == [r['response'] for r in highlevel],
                  'trace/highlevel response mismatch')

    waiting = None
    selected = None
    output_channels = []
    inst_position = None
    inst_replies = []
    err_polls = 0
    local_index = None
    for index,t in enumerate(traces):
        event, cmd = t['event'], t.get('command')
        if event == 'query':
            check(waiting is None and local_index is None and cmd in READS,
                  'unexpected/overlapping SCPI query')
            waiting = cmd
        elif event == 'write':
            check(waiting is None and local_index is None and cmd in WRITES,
                  'unexpected/overlapping SCPI write')
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1])
            if cmd == 'SYST:LOC':
                local_index = index
        else:
            check(local_index is None and waiting == cmd, 'mismatched SCPI reply')
            waiting = None
            answer = str(t.get('response','')).strip()
            if cmd == 'SYST:ERR?':
                err_polls += 1
                code,sep,message = answer.partition(',')
                check(sep and code.strip() == '0' and message.strip().strip('"') == 'No error',
                      'nonzero or malformed SCPI error')
            elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?'):
                check(answer == '0', 'combined topology active')
            elif cmd == 'INST:NSEL?':
                if selected is not None:
                    check(answer == str(selected), 'channel selection drift')
                else:
                    check(answer in ('1','2','3'), 'invalid initial channel')
            elif cmd == 'CHAN:OUTP?':
                check(selected in (1,2,3) and answer == '0', 'output ON/UNKNOWN')
                output_channels.append(selected)
            elif cmd == 'INST?':
                check(selected in (1,2,3) and answer == 'CH' + str(selected),
                      'symbolic channel mismatch')
                inst_replies.append(answer)
                inst_position = index
            elif cmd == '*IDN?':
                check('2231A-30-3' in answer and '[REDACTED]' in answer,
                      'wrong model or unredacted identity')
    check(waiting is None and local_index == len(traces)-1,
          'unfinished transaction or missing final LOCAL')
    check([t['command'] for t in traces if t['event']=='write' and
           t['command'] in ('SYST:REM','SYST:LOC')] == ['SYST:REM','SYST:LOC'],
          'Remote/Local ordering mismatch')
    check(output_channels == [1,2,3] * 3, 'not exactly three complete OFF sweeps')
    check(len(inst_replies) == 1 and inst_replies[0] == 'CH1',
          'expected one confirmed INST?=CH1')
    off_reply_positions = [i for i,t in enumerate(traces) if
                           t['event']=='reply' and t['command']=='CHAN:OUTP?']
    check(off_reply_positions[2] < inst_position < off_reply_positions[3],
          'INST? outside verified OFF checks')
    check(err_polls == 44 and
          err_polls == len(queries) - err_polls + len(writes) - 1,
          'missing or extra SCPI error polls')
    check(sum(t['event']=='query' and t['command']=='*IDN?' for t in traces)==1,
          'unexpected identity polling')
    check(result.get('values') == {'selected_channel':'CH1', 'inst_reads':1,
                                    'step_phase':'NOT_RUN'}, 'incorrect result payload')
    values = result['values']
    digest = hashlib.sha256(json.dumps(values,sort_keys=True,
                     separators=(',', ':')).encode('ascii')).hexdigest()
    check(result.get('results_sha256') == digest, 'result digest mismatch')
    loc_record = next(r for r in writes if r['command'] == 'SYST:LOC')
    check(loc_record['monotonic_s'] < result['monotonic_s'] <
          confirmation['monotonic_s'] < outcome['monotonic_s'],
          'final confirmation/outcome chronology mismatch')
    check(outcome.get('firmware_observations') and
          all(x == '1.04-1.04' for x in outcome['firmware_observations']),
          'unexpected firmware revision')
    return {'status':'REVIEWABLE_PASS', 'phase':'INST_OFF_QUERY_ONLY',
            'selected_channel': inst_replies[0], 'output_off_checks':len(output_channels),
            'queries':len(queries), 'writes':len(writes), 'error_polls':err_polls,
            'source_commit':start.get('software',{}).get('sha'),
            'source_dirty':start.get('software',{}).get('dirty'),
            'config_sha256':start['config_sha256']}


def _unique_pairs(pairs):
    data = {}
    for key,value in pairs:
        if key in data:
            raise AuditError('duplicate JSON object key')
        data[key] = value
    return data


def audit_file(path, expected_sha256=None):
    content = Path(path).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if expected_sha256 is not None:
        check(digest == expected_sha256.lower(), 'evidence SHA256 mismatch')
    try:
        rows = [json.loads(line, object_pairs_hook=_unique_pairs)
                for line in content.splitlines()]
    except (UnicodeDecodeError,ValueError) as exc:
        raise AuditError('invalid JSONL') from exc
    result = audit(rows)
    result['evidence_sha256'] = digest
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    try:
        result = audit_file(args.evidence, args.expected_sha256)
    except (OSError,AuditError) as exc:
        print(f'R4.5-B INST EVIDENCE AUDIT FAIL: {exc}')
        return 2
    print('R4.5-B INST EVIDENCE REVIEWABLE PASS (not independent physical proof)')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
