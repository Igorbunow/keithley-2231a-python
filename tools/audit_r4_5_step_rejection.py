"""Independently audit the known 2026-10-30 1 mV VOLT:STEP SCPI refusal.

A REVIEWABLE_ABORT is never a STEP hardware PASS. This tool strictly accepts
only the recorded, bounded OFF-only failure shape. It does not prove analog OFF.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path


class AuditError(ValueError):
    pass


def need(flag, text):
    if not flag:
        raise AuditError(text)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def audit(records):
    need(len(records) > 100 and all(isinstance(r, dict) and
         r.get('test_id') == 'R45STEPMUT' for r in records), 'invalid evidence records')
    t = [r.get('monotonic_s') for r in records]
    need(all(type(x) in (int, float) and math.isfinite(x) for x in t) and
         all(x < y for x, y in zip(t, t[1:])), 'monotonic timestamps invalid')
    try:
        utc = [datetime.fromisoformat(r['utc']) for r in records]
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError('invalid UTC timestamp') from exc
    need(all(d.tzinfo is not None for d in utc) and
         all(a <= b for a, b in zip(utc, utc[1:])), 'UTC timestamps invalid')

    def one(event):
        rows = [r for r in records if r.get('event') == event]
        need(len(rows) == 1, f'exactly one {event} required')
        return rows[0]

    start, pre, identity, stage_error, outcome = [one(n) for n in
        ('start', 'pre_open_approvals', 'psu_identity', 'stage_error', 'outcome')]
    need(records[0] is start and records[-1] is outcome, 'bad event framing')
    need((start.get('channel'), start.get('kind'), start.get('increment')) ==
         (1, 'voltage', .001), 'not the known rejection case')
    need(start.get('variant') == 'STEP_MUTATION_OFF' and
         outcome.get('phase') == 'STEP_MUTATION_OFF' and
         (outcome.get('channel'), outcome.get('kind'), outcome.get('increment')) ==
         (1, 'voltage', .001), 'run/outcome mismatch')
    need(pre.get('gates') == ['BENCH','INITIAL'] and
         pre.get('approved_before_evidence_open') is True and
         pre.get('config_sha256') == start.get('config_sha256'), 'initial gate mismatch')
    need([r.get('gate') for r in records if r.get('event') == 'operator_confirmation'] ==
         ['PROFILE', 'MUTATION', 'FINAL_PHYSICAL', 'FINAL_AFTER_ABORT'],
         'missing/order of physical gates')
    need(outcome.get('outcome') == 'ABORTED' and
         outcome.get('link_known') is True and
         outcome.get('digital_off_confirmed') is True and
         # Historical R4.5-D bug: ABORTED always emitted False here even
         # after FINAL_PHYSICAL confirmation. Must NOT claim it was True.
         outcome.get('physical_off_confirmed') is False,
         'unexpected/historically inconsistent abort flags')
    need(identity.get('model') == '2231A-30-3' and
         not identity.get('response') and
         identity.get('firmware') == '1.04-0.01', 'identity differs from evidence')
    need(outcome.get('firmware_observations') == ['1.04-0.01'], 'firmware trace mismatch')
    reason = 'Instrument error after VOLT:STEP 0.001: 120, "Parameter overflowed"'
    need(reason in outcome.get('reason','') and reason in stage_error.get('detail',''),
         'missing specific refusal in outcome')
    need(stage_error.get('exception_type') == 'SafetyError', 'unexpected exception')
    need(not any(r.get('event') == 'results' for r in records), 'unexpected success results')

    high_q = [r for r in records if r.get('event') == 'query']
    high_r = [r for r in records if r.get('event') == 'read']
    high_w = [r for r in records if r.get('event') == 'write']
    trace = [r['trace'] for r in records if r.get('event') == 'driver_trace']
    tq = [x for x in trace if x.get('event') == 'query']
    tr = [x for x in trace if x.get('event') == 'reply']
    tw = [x for x in trace if x.get('event') == 'write']
    need(len(tq) == len(tr) == len(high_q) == len(high_r) == 290 and
         len(tw) == len(high_w) == 80, 'unexpected SCPI count')
    need(Counter(x.get('command') for x in tq) ==
         Counter(x.get('command') for x in high_q), 'query trace/high-level mismatch')
    need([(x.get('command'),x.get('response')) for x in tr] ==
         [(x.get('command'),x.get('response')) for x in high_r],
         'read trace/high-level mismatch')
    need([x.get('command') for x in tw] == [x.get('command') for x in high_w],
         'write trace/high-level mismatch')
    need(all(x.get('event') in ('query','reply','write') for x in trace),
         'unexpected low-level trace event')
    need(all(a['monotonic_s'] <= b['monotonic_s'] for a,b in zip(trace,trace[1:])),
         'SCPI traces unordered')
    pending = None
    actions=[]
    for x in trace:
        e, c = x.get('event'), x.get('command')
        if e == 'reply':
            need(pending == c, 'unpaired/mismatched reply')
            pending = None
        else:
            need(pending is None, 'nested/unfinished query')
            actions.append((e,c))
            if e == 'query': pending = c
    need(pending is None, 'unfinished query')
    need([c for e,c in actions if e == 'write' and 'STEP' in c] ==
         ['VOLT:STEP 0.001'], 'missing/extra STEP mutation')
    need(not any(c in ('VOLT:UP','VOLT:DOWN','CURR:UP','CURR:DOWN',
                       'OUTP 1','CHAN:OUTP 1','*RST','*TRG')
                 for e,c in actions), 'forbidden SCPI action')
    need(not any(c in ('VOLT:STEP?','CURR:STEP?') for e,c in actions),
         'STEP query must not occur after error')
    need(sum(e=='write' and c=='OUTP 0' for e,c in actions) == 3,
         'emergency OFF count mismatch')
    need(sum(e=='query' and c=='CHAN:OUTP?' for e,c in actions) == 27,
         'OFF verification count mismatch')
    need(all(x.get('response') == '0' for x in tr if x.get('command')=='CHAN:OUTP?'),
         'output ON during aborted run')
    need(not any(c=='SYST:LOC' for e,c in actions), 'LOCAL sent after trip')
    need([x.get('response') for x in tr if x.get('command')=='SYST:ERR?' and
              not str(x.get('response','')).lstrip().startswith('0,')] ==
         ['120, "Parameter overflowed"'], 'SCPI error response mismatch')
    need(sum(e=='query' and c=='SYST:ERR?' for e,c in actions) == 185,
         'SCPI error poll count mismatch')
    # Require rejection before cleanup and both post-rejection output sweeps.
    index = next(i for i,(e,c) in enumerate(actions) if e=='write' and c=='VOLT:STEP 0.001')
    need(actions[index+1] == ('query','SYST:ERR?') and
         all(c not in ('VOLT:STEP 0.001','VOLT:UP','VOLT:DOWN')
             for e,c in actions[index+1:]), 'mutation repeated or missing error poll')
    need(all(e=='write' and c in ('OUTP 0', 'INST:NSEL 1', 'INST:NSEL 2',
                                  'INST:NSEL 3', 'SYST:REM') or e=='query'
             for e,c in actions[index+2:]), 'unsafe command after SCPI refusal')
    need(sum(e=='query' and c=='CHAN:OUTP?' for e,c in actions[index+2:]) >= 6,
         'cleanup OFF sweep missing')
    return {'status':'REVIEWABLE_ABORT_NOT_PASS',
            'scope':'CH1 voltage 1mV rejected; confirmed digital OFF; physical gate present',
            'records':len(records),'queries':len(tq),'writes':len(tw),
            'error_polls':185,'off_readbacks':27,'rejection':'120 Parameter overflowed',
            'firmware_observation':identity['firmware'],
            'historical_outcome_physical_flag_bug':True}


def audit_file(path, expected_sha256=None):
    data=Path(path).read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    need(not expected_sha256 or expected_sha256 == digest, 'SHA mismatch')
    try:
        rows=[json.loads(line,object_pairs_hook=unique_keys) for line in data.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise AuditError('invalid JSONL') from exc
    result=audit(rows)
    result['evidence_sha256']=digest
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events_jsonl',type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    try:
        info=audit_file(args.events_jsonl,args.expected_sha256)
    except (AuditError,OSError) as exc:
        print(f'R4.5-D REJECTION AUDIT FAILED: {exc}')
        return 2
    print('R4.5-D REJECTION VERIFIED; TEST ABORTED, NOT HW PASS')
    print(json.dumps(info,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
