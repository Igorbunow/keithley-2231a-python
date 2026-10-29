"""Fail-closed offline R4.5-B INST and STEP JSONL audit (never opens instruments).

An accepted file is evidence of one recorded OFF-only observation, not an
independent physical measurement. STEP runs change OFF presets and are separately verified here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
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


def audit_inst(records):
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



STEP_READS = READS | frozenset(('VOLT?', 'CURR?', 'VOLT:LIM?',
 'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?', 'VOLT:STEP?', 'CURR:STEP?'))
STEP_WRITES = WRITES | frozenset(('OUTP 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
 'OUTP:TRAC 0', 'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
 'VOLT:LIM 3.50', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
 'CURR 0.150', 'CURR 0.100', 'VOLT 3.30', 'VOLT 1.00'))
STEP_Q = Counter({'SYST:ERR?':232,'INST:NSEL?':46,'CHAN:OUTP?':33,
 'VOLT:LIM:STAT?':9,'VOLT:LIM?':9,'VOLT?':9,'CURR?':9,
 'OUTP:SER?':7,'OUTP:PARALLEL?':5,'OUTP:TRAC?':4,
 'OUTP:TIM:STAT?':3,'*IDN?':2,'INST?':1,
 'VOLT:STEP?':1,'CURR:STEP?':1})
STEP_W = Counter({'INST:NSEL 1':27,'INST:NSEL 3':20,'INST:NSEL 2':17,
 'SYST:REM':3,'OUTP:TIM:STAT 0':3,'VOLT 0':3,'CURR 0':3,
 'VOLT:LIM:STAT 1':3,'OUTP 0':2,'VOLT:LIM 1.50':2,
 'CURR 0.100':2,'VOLT 1.00':2,'OUTP:SER 0':1,
 'OUTP:PARALLEL 0':1,'OUTP:TRAC 0':1,'VOLT:LIM 3.50':1,
 'CURR 0.150':1,'VOLT 3.30':1,'SYST:LOC':1})


def audit_step(records):
    check(len(records)>1000 and all(type(r) is dict and
          r.get('test_id')==STAGE and r.get('event') in EVENTS for r in records),
          'short, mixed or error STEP evidence')
    times=[r.get('monotonic_s') for r in records]
    check(all(type(t) in (int,float) and math.isfinite(t) for t in times)
          and all(a<=b for a,b in zip(times,times[1:])),
          'invalid monotonic chronology')
    try:
        utc=[datetime.fromisoformat(r['utc']) for r in records]
        check(all(t.tzinfo is not None for t in utc) and
              all(a<=b for a,b in zip(utc,utc[1:])), 'UTC chronology')
    except (KeyError,TypeError,ValueError) as exc:
        raise AuditError('invalid UTC timestamps') from exc
    def one(event):
        items=[r for r in records if r['event']==event]
        check(len(items)==1,f'exactly one {event} required')
        return items[0]
    start,pre,identity,result,outcome=[one(e) for e in
        ('start','pre_open_approvals','psu_identity','results','outcome')]
    gates=[r for r in records if r['event']=='operator_confirmation']
    check(records[0] is start and records[-1] is outcome and
          start.get('variant')=='STEP_OFF_PROFILE' and
          outcome.get('phase')=='STEP_OFF_PROFILE', 'wrong/mixed phase')
    check([g.get('gate') for g in gates]==['PROFILE','FINAL'] and
          pre.get('gates')==['BENCH','INITIAL'] and
          pre.get('approved_before_evidence_open') is True and
          pre.get('config_sha256')==start.get('config_sha256') and
          len(str(start.get('config_sha256','')))==64,
          'missing or inconsistent operator gates')
    check(outcome.get('outcome')=='PASS' and
          outcome.get('physical_off_confirmed') is True and
          outcome.get('link_known') is True, 'no verified safe outcome')
    check(identity.get('model')=='2231A-30-3' and
          identity.get('firmware')=='1.04-1.04' and not identity.get('response'),
          'wrong/unsafe identity')
    queries=[r for r in records if r['event']=='query']
    reads=[r for r in records if r['event']=='read']
    writes=[r for r in records if r['event']=='write']
    check(Counter(r.get('command') for r in queries)==STEP_Q and
          Counter(r.get('command') for r in reads)==STEP_Q and
          Counter(r.get('command') for r in writes)==STEP_W,
          'unexpected commands or missing SCPI operation')
    check(all(r['command'] in STEP_READS for r in queries+reads) and
          all(r['command'] in STEP_WRITES for r in writes),
          'command violates OFF-only allowlist')
    traces=[r['trace'] for r in records if r['event']=='driver_trace']
    check(len(traces)==len(queries)*2+len(writes), 'trace count mismatch')
    for ev,high in (('query',queries),('reply',reads),('write',writes)):
        low=[t for t in traces if t.get('event')==ev]
        check(Counter(t.get('command') for t in low)==
              Counter(r['command'] for r in high), 'trace/high-level command mismatch')
        if ev=='reply':
            check([(t.get('command'),t.get('response')) for t in low]==
                  [(r['command'],r.get('response')) for r in high],
                  'trace/high-level response mismatch')
    selected=None; waiting=None; ops=[]; off=[]; after_off=[]; last_off=-1
    step=[]; inst=[]
    for i,t in enumerate(traces):
        ev,cmd=t.get('event'),t.get('command')
        check(ev in ('query','reply','write'), 'unrecognized wire event')
        if ev=='query':
            check(waiting is None, 'overlapping SCPI query')
            waiting=cmd; ops.append(cmd)
        elif ev=='write':
            check(waiting is None, 'SCPI write while pending reply')
            ops.append(cmd)
            if cmd.startswith('INST:NSEL '): selected=int(cmd[-1])
            if cmd=='OUTP 0':
                last_off=i
                after_off.clear()
        else:
            check(waiting==cmd, 'orphan/mismatched SCPI reply')
            waiting=None
            reply=str(t.get('response','')).strip()
            if cmd=='SYST:ERR?':
                code,sep,message=reply.partition(',')
                check(bool(sep) and code.strip()=='0' and
                      message.strip().strip('\"')=='No error',
                      'nonzero or malformed error queue')
            elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?',
                         'OUTP:TIM:STAT?'):
                check(reply=='0', 'combined/timed mode unexpectedly active')
            elif cmd=='INST:NSEL?':
                check(reply in ('1','2','3') and
                      (selected is None or int(reply)==selected),
                      'channel selector drift')
            elif cmd=='CHAN:OUTP?':
                check(selected in (1,2,3) and reply=='0', 'output not OFF')
                off.append(selected)
                if i>last_off: after_off.append(selected)
            elif cmd=='VOLT:LIM:STAT?':
                check(reply=='1', 'voltage safety limit not enabled')
            elif cmd in ('VOLT?','CURR?','VOLT:LIM?'):
                check(selected in (1,2,3), 'unknown channel for preset readback')
                expect={1:(3.3,.15,3.5),2:(1.0,.1,1.5),3:(1.0,.1,1.5)}[selected]
                wanted=dict(zip(('VOLT?','CURR?','VOLT:LIM?'),expect))[cmd]
                try: numeric=float(reply)
                except ValueError as exc: raise AuditError('invalid preset number') from exc
                check(math.isfinite(numeric) and
                      math.isclose(numeric,wanted,rel_tol=0,abs_tol=1e-6),
                      f'CH{selected} {cmd} readback disagrees with preset')
            elif cmd=='INST?':
                check(selected==1 and reply=='CH1', 'INST? channel mismatch')
                inst.append(i)
            elif cmd in ('VOLT:STEP?','CURR:STEP?'):
                check(selected==1, 'STEP not on CH1')
                try: numeric=float(reply)
                except ValueError as exc: raise AuditError('invalid STEP number') from exc
                check(math.isfinite(numeric) and 0<numeric<=1,
                      'STEP outside bounded finite range')
                step.append((cmd,numeric,i))
            elif cmd=='*IDN?':
                check('2231A-30-3' in reply and '[REDACTED]' in reply
                      and '1.04-1.04' in reply, 'wrong/unredacted IDN')
    check(waiting is None and len(ops)==465 and ops[-1]=='SYST:LOC',
          'SCPI trace truncated or LOCAL missing')
    check(all(ops[i]!='SYST:ERR?' and ops[i+1]=='SYST:ERR?'
              for i in range(0,len(ops)-1,2)),
          'missing or misordered SYST:ERR? poll')
    check(off==[1,2,3]*11 and after_off==[1,2,3]*2,
          'missing OFF sweeps or post-shutdown verification')
    check(len(inst)==1 and [x[0] for x in step]==['VOLT:STEP?','CURR:STEP?']
          and inst[0]<step[0][2]<step[1][2], 'STEP order/duplication')
    check(gates[0]['monotonic_s'] < next(w['monotonic_s'] for w in writes
          if w['command']=='OUTP 0') and
          next(q['monotonic_s'] for q in queries if q['command']=='INST?')<
          gates[0]['monotonic_s'], 'profile mutation before second gate')
    check(all(w['command'] in WRITES for w in writes
              if w['monotonic_s']<gates[0]['monotonic_s']),
          'preset change before physical profile authorization')
    # Distinguish initial safe profile preparation from final shutdown:
    # both are OUTP OFF, but the second must follow BOTH STEP observations.
    off_write_indices=[i for i,w in enumerate(writes) if w['command']=='OUTP 0']
    check(len(off_write_indices)==2 and
          writes[off_write_indices[0]]['monotonic_s']<
          next(q['monotonic_s'] for q in queries if q['command']=='VOLT:STEP?') and
          writes[off_write_indices[1]]['monotonic_s']>
          next(q['monotonic_s'] for q in queries if q['command']=='CURR:STEP?'),
          'missing/early final OFF or premature STEP')
    # Allow repeated idempotent channel selections, but not reordered
    # profile mutation: zero-before-presets, then CH1/CH2/CH3 setups.
    mutation=[w['command'] for w in writes if w['command'] in {
        'OUTP:SER 0','OUTP:PARALLEL 0','OUTP:TRAC 0',
        'OUTP:TIM:STAT 0','VOLT 0','CURR 0',
        'VOLT:LIM 3.50','VOLT:LIM 1.50','VOLT:LIM:STAT 1',
        'CURR 0.150','CURR 0.100','VOLT 3.30','VOLT 1.00'}]
    expected=['OUTP:SER 0','OUTP:PARALLEL 0','OUTP:TRAC 0']
    for ch in (1,2,3):
        expected+=['OUTP:TIM:STAT 0','VOLT 0','CURR 0',
                   'VOLT:LIM 3.50' if ch==1 else 'VOLT:LIM 1.50',
                   'VOLT:LIM:STAT 1',
                   'CURR 0.150' if ch==1 else 'CURR 0.100',
                   'VOLT 3.30' if ch==1 else 'VOLT 1.00']
    check(mutation==expected, 'profile preset command ordering mismatch')
    check(writes[-1]['command']=='SYST:LOC' and
          writes[-1]['monotonic_s']<result['monotonic_s']<
          gates[1]['monotonic_s']<outcome['monotonic_s'],
          'LOCAL/results/final confirmation order incorrect')
    check(outcome.get('firmware_observations')==['1.04-1.04'],
          'firmware drift')
    values=result.get('values')
    check(type(values) is dict and set(values)=={
          'step_phase','selected_channel','inst_reads','step_reads',
          'step_voltage_v','step_current_a'}, 'missing result fields')
    check(values['step_phase']=='OBSERVED_OFF_ONLY' and
          values['selected_channel']=='CH1' and values['inst_reads']==1 and
          values['step_reads']==['VOLT:STEP?','CURR:STEP?'] and
          math.isclose(values['step_voltage_v'],step[0][1],rel_tol=0,abs_tol=1e-9) and
          math.isclose(values['step_current_a'],step[1][1],rel_tol=0,abs_tol=1e-9),
          'STEP results do not match measured SCPI replies')
    digest=hashlib.sha256(json.dumps(values,sort_keys=True,
           separators=(',',':')).encode('ascii')).hexdigest()
    check(result.get('results_sha256')==digest, 'results digest mismatch')
    return {'status':'REVIEWABLE_PASS','phase':'STEP_OFF_PROFILE',
            'selected_channel':'CH1','step_voltage_v':step[0][1],
            'step_current_a':step[1][1], 'output_off_checks':len(off),
            'post_shutdown_off_checks':len(after_off),'queries':len(queries),
            'writes':len(writes),'error_polls':STEP_Q['SYST:ERR?'],
            'source_commit':start.get('software',{}).get('sha'),
            'source_dirty':start.get('software',{}).get('dirty'),
            'config_sha256':start.get('config_sha256')}


def audit(records):
    check(isinstance(records,list) and bool(records) and type(records[0]) is dict,
          'invalid evidence input')
    if records[0].get('variant')=='INST_OFF_QUERY_ONLY':
        return audit_inst(records)
    if records[0].get('variant')=='STEP_OFF_PROFILE':
        return audit_step(records)
    raise AuditError('unsupported R4.5-B phase')

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
        print(f'R4.5-B SOURCE EVIDENCE AUDIT FAIL: {exc}')
        return 2
    print('R4.5-B SOURCE EVIDENCE REVIEWABLE PASS (not independent physical proof)')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
