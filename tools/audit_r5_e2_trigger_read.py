"""Independent offline audit of one R5-E2 native trigger-register read JSONL.

No instrument access; PASS is a reviewable OFF-only candidate, never trigger
execution qualification. Audit the original immutable file, not terminal text.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

STAGE = 'R5E2TRIGGERREAD'
TRIGGER = ('VOLT:TRIG?', 'CURR:TRIG?')
EXPECTED_PRESETS = {1: (3.30, .150), 2: (1.00, .100), 3: (1.00, .100)}
# Explicit whitelist is narrower than the driver's general diagnostic scope.
READS = frozenset({
    '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
    'INST:NSEL?', 'CHAN:OUTP?', 'VOLT?', 'CURR?', 'VOLT:LIM?',
    'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?', *TRIGGER,
})
WRITES = frozenset({
    'SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
    'OUTP:TRAC 0', 'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
    'VOLT:LIM 3.50', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
    'VOLT 3.30', 'VOLT 1.00', 'CURR 0.150', 'CURR 0.100',
    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
})


class AuditError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise AuditError(message)


def numeric(raw, name):
    need(type(raw) in (str, float, int), f'{name}: numeric reply required')
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuditError(f'{name}: bad numeric reply') from exc
    need(math.isfinite(value), f'{name}: nonfinite reply')
    return value


def same(x, y, name, tol=0.00049):
    need(abs(numeric(x, name) - y) < tol, f'{name}: mismatch')


def audit(records):
    need(type(records) is list and len(records) >= 300, 'Short/absent evidence')
    need(all(type(r) is dict and r.get('test_id') == STAGE and
             type(r.get('monotonic_s')) in (int, float) for r in records),
         'Mixed test or unclocked record')
    times = [numeric(r['monotonic_s'], 'timestamp') for r in records]
    need(all(a <= b for a,b in zip(times, times[1:])), 'Nonmonotonic timestamps')

    def one(name):
        found = [(i,r) for i,r in enumerate(records) if r.get('event') == name]
        need(len(found) == 1, f'Exactly one {name} required')
        return found[0]

    start_i, start = one('start')
    pre_i, pre = one('pre_open_approvals')
    _, identity = one('psu_identity')
    results_i, results = one('results')
    out_i, out = one('outcome')
    ch = start.get('channel')
    need(start_i == 0 and pre_i == 1 and results_i < out_i == len(records)-1,
         'Wrong evidence bracketing')
    need(type(ch) is int and ch in (1, 2, 3) and
         start.get('phase') == 'TRIGGER_PRESET_OFF_READ', 'Wrong channel or case')
    need(start.get('config_sha256') == pre.get('config_sha256') and
         pre.get('gates') == ['BENCH', 'INITIAL'] and
         pre.get('approved_before_evidence_open') is True,
         'Missing pre-open operator confirmation')
    for key in ('config_sha256',):
        digest = start.get(key)
        need(type(digest) is str and len(digest) == 64 and
             all(c in '0123456789abcdef' for c in digest), 'Bad SHA-256: '+key)
    software = start.get('software', {})
    need(type(software) is dict, 'Missing provenance')
    for field in ('r5e2_runner_sha256', 'r5e2_stage_sha256', 'source_sha256'):
        digest = software.get(field)
        need(type(digest) is str and len(digest) == 64 and
             all(c in '0123456789abcdef' for c in digest), 'Missing provenance '+field)
    need(identity.get('model') == '2231A-30-3' and
         identity.get('firmware') in ('1.04-1.04', '1.04-0.01'),
         'Unqualified firmware/model')
    need(out.get('phase') == 'TRIGGER_PRESET_OFF_READ' and
         out.get('channel') == ch and out.get('outcome') == 'PASS' and
         out.get('link_known') is True and
         out.get('digital_off_confirmed') is True and
         out.get('physical_off_confirmed') is True and
         out.get('hardware_review') == 'PENDING', 'Incomplete PASS/OFF status')
    need(not any(r['event'] in ('stage_error', 'io_error', 'invalid_scpi_reply',
             'shutdown_error_queue', 'cleanup_error', 'firmware_revision_drift')
             for r in records), 'Failure or firmware drift in evidence')
    gates = [(i,r) for i,r in enumerate(records) if r['event']=='operator_confirmation']
    need([r.get('gate') for _,r in gates] == ['PROFILE','QUERY','FINAL'],
         'Missing/operator gates out of order')
    need(gates[1][1].get('channel') == ch and
         gates[0][0] < gates[1][0] < results_i < gates[2][0] < out_i,
         'Operator gate/channel chronology invalid')
    v = results.get('values')
    need(type(v) is dict and v.get('channel') == ch and
         v.get('queries') == list(TRIGGER) and
         v.get('active_setpoints_not_triggered') is True and
         v.get('analog_measurement') == 'NOT_RUN', 'Wrong result record')
    sha = hashlib.sha256(json.dumps(v, sort_keys=True,
            separators=(',', ':')).encode('ascii')).hexdigest()
    need(sha == results.get('results_sha256'), 'Result checksum mismatch')
    voltage = numeric(v.get('voltage_v'), 'result voltage')
    current = numeric(v.get('current_a'), 'result current')
    need(0 <= voltage <= (5 if ch == 3 else 30) and 0 <= current <= 3,
         'Out-of-range trigger preset')

    q = [r for r in records if r['event']=='query']
    reads = [r for r in records if r['event']=='read']
    writes = [r for r in records if r['event']=='write']
    trace = [r['trace'] for r in records if r['event']=='driver_trace']
    need(len(q) > 100 and Counter(r['command'] for r in q) ==
         Counter(r['command'] for r in reads), 'Query/reply count mismatch')
    need(all(r.get('command') in READS for r in q+reads) and
         all(r.get('command') in WRITES for r in writes), 'Unapproved command')
    need([t.get('command') for t in trace if t.get('event')=='write'] ==
         [r['command'] for r in writes], 'Physical writes not reflected in evidence')
    need(Counter(t.get('command') for t in trace if t.get('event')=='query') ==
         Counter(r['command'] for r in q), 'Physical query count mismatch')
    need([(t.get('command'),t.get('response')) for t in trace if t.get('event')=='reply'] ==
         [(r.get('command'),r.get('response')) for r in reads],
         'Physical reply mismatch')
    need(all(type(t) is dict and t.get('event') in ('query','reply','write')
             for t in trace), 'Bad driver trace record')
    last_pending = None
    actions = []
    selected = None
    trigger_found = []
    off_channels = []
    timed_off_channels = []
    trigger_trace_at = []
    all_setpoints = {i: {'voltage': None, 'current': None} for i in (1, 2, 3)}
    for t in trace:
        cmd, typ = t.get('command'), t['event']
        need(cmd in (READS if typ in ('query','reply') else WRITES), 'Bad trace SCPI')
        if typ == 'query':
            need(last_pending is None, 'Interleaved SCPI queries')
            last_pending = cmd
            actions.append((typ,cmd))
        elif typ == 'reply':
            need(last_pending == cmd, 'Reply without matching query')
            last_pending = None
            ans = str(t.get('response',''))
            if cmd == 'SYST:ERR?':
                split = ans.split(',',1)
                need(len(split) == 2 and split[0].strip() == '0' and split[1].strip(),
                     'Nonzero/invalid SCPI error')
            elif cmd == 'INST:NSEL?':
                if selected is None:
                    need(ans in ('1','2','3'), 'Bad initial channel selection')
                else:
                    need(ans == str(selected), 'Channel selection drift')
            elif cmd == 'CHAN:OUTP?':
                need(selected in (1,2,3) and ans == '0', 'Output ON or unknown channel')
                off_channels.append(selected)
                timed_off_channels.append((t['monotonic_s'], selected))
            elif cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
                         'OUTP:TIM:STAT?'):
                need(ans == '0', 'Topology/timer active')
            elif cmd == 'VOLT:LIM:STAT?':
                need(ans == '1', 'Voltage limit disabled')
            elif cmd == 'VOLT:LIM?':
                if selected is not None:
                    same(ans, 3.50 if selected == 1 else 1.50, 'Limit readback')
            elif cmd in ('VOLT?', 'CURR?') and selected is not None:
                k = 'voltage' if cmd == 'VOLT?' else 'current'
                expected = all_setpoints[selected][k]
                if expected is not None:
                    same(ans, expected, f'CH{selected} {k} setpoint')
            elif cmd in TRIGGER:
                need(selected == ch, 'Trigger register read from wrong channel')
                trigger_found.append((cmd, numeric(ans,cmd)))
        else:
            need(last_pending is None, 'Write interleaved with query')
            actions.append((typ,cmd))
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1])
            if cmd.startswith('VOLT ') and selected in (1,2,3):
                all_setpoints[selected]['voltage'] = float(cmd.split()[1])
            if cmd.startswith('CURR ') and selected in (1,2,3):
                all_setpoints[selected]['current'] = float(cmd.split()[1])
        if typ == 'query' and cmd in TRIGGER:
            trigger_trace_at.append(t['monotonic_s'])
    need(last_pending is None, 'Unanswered physical query')
    need([cmd for cmd,_ in trigger_found] == list(TRIGGER) and
         len(trigger_trace_at)==2, 'Missing/duplicate/wrong-order native reads')
    same(trigger_found[0][1], voltage, 'VOLT:TRIG? result', tol=1e-8)
    same(trigger_found[1][1], current, 'CURR:TRIG? result', tol=1e-8)
    need(gates[1][1]['monotonic_s'] < trigger_trace_at[0] < trigger_trace_at[1] <
         gates[2][1]['monotonic_s'], 'Read before query gate or after OFF')
    need(actions[-1] == ('write','SYST:LOC') and
         sum(c=='OUTP 0' for t,c in actions if t=='write') >= 2,
         'Final LOCAL or OFF missing')
    last_global_off = max(t['monotonic_s'] for t in trace
                          if t['event']=='write' and t['command']=='OUTP 0')
    need(set(off_channels) == {1,2,3} and len(off_channels) >= 15 and
         {c for stamp,c in timed_off_channels if stamp > last_global_off} == {1,2,3} and
         all(any(stamp > trigger_trace_at[1] and c == target
                 for stamp,c in timed_off_channels) for target in (1,2,3)),
         'Missing post-trigger and post-shutdown channel-correct OFF readbacks')
    need(all(all_setpoints[i][k] is not None and
             abs(all_setpoints[i][k] - EXPECTED_PRESETS[i][0 if k=='voltage' else 1]) < 1e-8
             for i in (1,2,3) for k in ('voltage','current')),
         'OFF profile not applied/retained')
    need(any(r['event']=='read' and r.get('command')=='*IDN?' and
             '2231A-30-3' in r.get('response','') and
             r.get('response','').split(',')[-1].strip()==identity['firmware']
             for r in records), 'Identity evidence missing')
    return {'status':'REVIEWABLE_PASS_NOT_TRIGGER_ACTUATION', 'channel':ch,
            'voltage_v':voltage,'current_a':current,
            'trigger_queries':list(TRIGGER), 'scpi_queries':len(q),
            'off_readbacks':len(off_channels),
            'digital_off_confirmed':True,'physical_off_confirmed':True,
            'firmware':identity['firmware'],
            'source_sha256':software['source_sha256']}


def audit_path(path):
    raw=Path(path).read_bytes()
    need(raw.endswith(b'\n') and len(raw) <= 12*1024*1024,
         'Truncated/oversized JSONL')
    try:
        records=[json.loads(line) for line in raw.splitlines()]
    except (UnicodeDecodeError,ValueError) as exc:
        raise AuditError('Invalid JSONL') from exc
    result=audit(records)
    result['original_sha256']=hashlib.sha256(raw).hexdigest()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    args=parser.parse_args(argv)
    try:
        result=audit_path(args.events)
    except (AuditError,OSError) as exc:
        print(f'R5-E2 AUDIT REJECTED: {exc}')
        return 2
    print(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
