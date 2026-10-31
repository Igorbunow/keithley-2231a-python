"""Independent OFF-only R5-E4 native trigger write/read/restore evidence audit.

Processes original JSONL only; no instrument I/O. A PASS is NOT *TRG or
powered trigger qualification. Never infer success from terminal PASS alone.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from tools.audit_r5_e2_trigger_read import READS as E2_READS, WRITES as E2_WRITES

STAGE = 'R5E4TRIGGERWRITE'
PHASE = 'TRIGGER_PRESET_OFF_WRITE_RESTORE'
PRESETS = {1:(2.20,.120), 2:(.80,.080), 3:(.80,.080)}
ACTIVE = {1:(3.30,.150), 2:(1.00,.100), 3:(1.00,.100)}
TRIGGER = ('VOLT:TRIG?', 'CURR:TRIG?')


class AuditError(ValueError):
    pass


def need(condition, explanation):
    if not condition: raise AuditError(explanation)


def number(v):
    try: n = float(v)
    except (ValueError, TypeError, OverflowError) as exc:
        raise AuditError('Non-numeric SCPI response') from exc
    need(math.isfinite(n), 'Nonfinite SCPI number')
    return n


def near(a,b,tol=.00049):
    return abs(number(a)-number(b)) < tol


def audit(records):
    need(isinstance(records,list) and len(records)>=350, 'Missing/short evidence')
    need(all(type(r) is dict and r.get('test_id') == STAGE and
             type(r.get('monotonic_s')) in (float,int) and
             math.isfinite(r['monotonic_s']) for r in records), 'Mixed/invalid events')
    times=[r['monotonic_s'] for r in records]
    need(all(a<=b for a,b in zip(times,times[1:])), 'Nonmonotonic events')

    def one(event):
        matches=[(i,r) for i,r in enumerate(records) if r.get('event')==event]
        need(len(matches)==1,'Expected exactly one '+event)
        return matches[0]

    start_i,start=one('start'); pre_i,pre=one('pre_open_approvals')
    _,identity=one('psu_identity'); staged_i,staged=one('staged_observation')
    result_i,result=one('results'); out_i,out=one('outcome')
    ch=start.get('channel')
    need(start_i==0 and pre_i==1 and staged_i<result_i<out_i==len(records)-1,
         'Incorrect event bracketing')
    need(type(ch) is int and ch in (1,2,3) and start.get('phase')==PHASE,
         'Wrong test identity/channel')
    need(start.get('config_sha256')==pre.get('config_sha256') and
         pre.get('gates')==['BENCH','INITIAL'] and
         pre.get('approved_before_evidence_open') is True, 'Missing pre-open gates')
    digests=[start.get('config_sha256')]
    software=start.get('software',{})
    need(type(software) is dict, 'Missing software provenance')
    digests.extend(software.get(k) for k in
        ('source_sha256','r5e4_runner_sha256','r5e4_stage_sha256'))
    need(all(type(s) is str and len(s)==64 and
             all(c in '0123456789abcdef' for c in s) for s in digests),
         'Invalid software/config SHA-256')
    need(identity.get('model')=='2231A-30-3' and
         identity.get('firmware') in ('1.04-1.04','1.04-0.01'),
         'Wrong firmware/model')
    need(all(out.get(k)==v for k,v in {
        'phase':PHASE,'channel':ch,'outcome':'PASS', 'link_known':True,
        'digital_off_confirmed':True,'physical_off_confirmed':True,
        'hardware_review':'PENDING'}.items()), 'Non-PASS or missing OFF')
    need(not any(r['event'] in ('stage_error','io_error','invalid_scpi_reply',
        'shutdown_error_queue','cleanup_error','firmware_revision_drift')
        for r in records), 'Fault event in evidence')
    gates=[(j,r) for j,r in enumerate(records) if r['event']=='operator_confirmation']
    need([g.get('gate') for _,g in gates] == ['PROFILE','STAGE','FINAL'] and
         gates[1][1].get('channel')==ch and
         gates[0][0]<gates[1][0]<staged_i<result_i<gates[2][0]<out_i,
         'Missing/out-of-order operator gates')
    value=result.get('values')
    need(type(value) is dict and value.get('channel')==ch and
         value.get('active_setpoints_unchanged') is True and
         value.get('analog_measurement') == 'NOT_RUN' and
         value.get('trigger_executed') is False, 'Unqualified result fields')
    digest=hashlib.sha256(json.dumps(value,sort_keys=True,
        separators=(',',':')).encode('ascii')).hexdigest()
    need(digest==result.get('results_sha256'), 'Result digest mismatch')
    initial=value.get('original'); staged_value=value.get('staged')
    checked=value.get('checked'); restored=value.get('restored')
    need(all(type(x) is dict and set(x)=={'voltage_v','current_a'} for x in
             (initial,staged_value,checked,restored)), 'Bad register snapshots')
    for entry in (initial,staged_value,checked,restored):
        need(0<=number(entry['voltage_v'])<= (5 if ch==3 else 30) and
             0<=number(entry['current_a'])<=3, 'Trigger value out of range')
    sv,si=PRESETS[ch]
    need(near(staged_value['voltage_v'],sv) and near(staged_value['current_a'],si)
         and near(checked['voltage_v'],sv) and near(checked['current_a'],si)
         and near(restored['voltage_v'],initial['voltage_v']) and
         near(restored['current_a'],initial['current_a']), 'Incorrect stage/restore')
    need(staged.get('channel')==ch and
         near(staged.get('voltage_v'),sv) and near(staged.get('current_a'),si)
         and staged.get('restored') is False and staged.get('trigger_executed') is False,
         'Staged milestone missing/inconsistent')

    reads=[r for r in records if r['event']=='read']
    queries=[r for r in records if r['event']=='query']
    writes=[r for r in records if r['event']=='write']
    traces=[r['trace'] for r in records if r['event']=='driver_trace']
    need(len(queries)>=175 and len(queries)==len(reads) and
         Counter(r.get('command') for r in queries)==Counter(r.get('command') for r in reads),
         'Missing query/reply records')
    need(all(r.get('command') in E2_READS for r in reads+queries),
         'Unapproved query')
    allowed_writes=set(E2_WRITES)|set(value.get('trigger_writes',[]))
    need(all(r.get('command') in allowed_writes for r in writes), 'Unapproved physical write')
    need([t.get('command') for t in traces if t.get('event')=='write']==
         [r['command'] for r in writes], 'Wire writes mismatch')
    need(Counter(t.get('command') for t in traces if t.get('event')=='query')==
         Counter(r['command'] for r in queries) and
         [(t.get('command'),t.get('response')) for t in traces if t.get('event')=='reply']==
         [(r.get('command'),r.get('response')) for r in reads],
         'Wire queries/responses mismatch')
    selected=None; pending=None; trigger_actions=[]; trigger_times=[]
    off_readbacks=[]; setpoints={c:{'VOLT?':[],'CURR?':[]} for c in (1,2,3)}
    last_global_off=None
    for tr in traces:
        kind=tr.get('event'); cmd=tr.get('command'); stamp=tr.get('monotonic_s')
        need(kind in ('query','reply','write') and type(cmd) is str and
             type(stamp) in (int,float) and math.isfinite(stamp), 'Bad wire trace')
        if kind=='query':
            need(cmd in E2_READS and pending is None, 'Query overlap/disallowed')
            pending=cmd
        elif kind=='reply':
            need(pending==cmd, 'Wire reply without matching query')
            pending=None
            answer=tr.get('response')
            if cmd=='SYST:ERR?':
                split=str(answer).split(',',1)
                need(len(split)==2 and split[0].strip()=='0' and split[1].strip(),
                     'SCPI error or malformed queue')
            elif cmd=='INST:NSEL?':
                if selected is not None: need(str(answer).strip()==str(selected),
                                              'Unexpected channel selection')
                else: need(str(answer).strip() in ('1','2','3'),'Invalid initial channel')
            elif cmd=='CHAN:OUTP?':
                need(selected in (1,2,3) and str(answer).strip()=='0', 'Output ON')
                off_readbacks.append((stamp,selected))
            elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?','OUTP:TIM:STAT?'):
                need(str(answer).strip()=='0', 'Coupled topology or timer ON')
            elif cmd=='VOLT:LIM:STAT?':
                need(str(answer).strip()=='1', 'Voltage limit disabled')
            elif cmd=='VOLT:LIM?':
                if selected: need(near(answer,3.5 if selected==1 else 1.5), 'Wrong V limit')
            elif cmd in ('VOLT?','CURR?'):
                if selected:
                    need(near(answer,ACTIVE[selected][0 if cmd=='VOLT?' else 1]),
                         'Active setpoints changed')
                    setpoints[selected][cmd].append(stamp)
            elif cmd in TRIGGER:
                need(selected==ch,'Trigger query on wrong channel')
                trigger_actions.append((cmd,number(answer)))
                trigger_times.append(stamp)
        else:
            need(pending is None and cmd in allowed_writes,'Interleaved/disallowed wire write')
            if cmd.startswith('INST:NSEL '): selected=int(cmd[-1])
            if cmd=='OUTP 0': last_global_off=stamp
            if cmd.startswith(('VOLT:TRIG ','CURR:TRIG ')):
                need(selected==ch,'Trigger write on wrong channel')
                trigger_actions.append((cmd,number(cmd.split()[1])))
                trigger_times.append(stamp)
    need(pending is None,'Unanswered physical query')
    old_v,old_i = number(initial['voltage_v']),number(initial['current_a'])
    pattern=[('VOLT:TRIG?',old_v),('CURR:TRIG?',old_i),
             (f'VOLT:TRIG {sv:.3f}',sv),('VOLT:TRIG?',sv),
             (f'CURR:TRIG {si:.3f}',si),('CURR:TRIG?',si),
             ('VOLT:TRIG?',sv),('CURR:TRIG?',si),
             (f'VOLT:TRIG {old_v:.3f}',old_v),('VOLT:TRIG?',old_v),
             (f'CURR:TRIG {old_i:.3f}',old_i),('CURR:TRIG?',old_i),
             ('VOLT:TRIG?',old_v),('CURR:TRIG?',old_i)]
    need(len(pattern)==len(trigger_actions)==len(trigger_times) and
         all(c==expected and near(val,want) for (c,val),(expected,want)
             in zip(trigger_actions,pattern)), 'Wrong trigger wire command/response order')
    need(value['trigger_queries']==list(TRIGGER)*5 and
         value['trigger_writes']==[x[0] for x in pattern if x[0].startswith(
             ('VOLT:TRIG ','CURR:TRIG '))], 'Incorrect trigger result sequence')
    need(gates[1][1]['monotonic_s']<min(trigger_times) and
         trigger_times[7]<staged['monotonic_s']<trigger_times[8] and
         max(trigger_times)<gates[2][1]['monotonic_s'],
         'Writes outside operator-approved window')
    need(last_global_off is not None and last_global_off>max(trigger_times) and
         {chx for stamp,chx in off_readbacks if stamp>last_global_off}=={1,2,3}
         and all(any(stamp>max(trigger_times) and c==chx for stamp,c in off_readbacks)
                 for chx in (1,2,3)), 'Post-write/shutdown OFF not read for each channel')
    need(len(off_readbacks)>=15 and all(setpoints[c]['VOLT?'] and
         setpoints[c]['CURR?'] and
         max(setpoints[c]['VOLT?'])>max(trigger_times) and
         max(setpoints[c]['CURR?'])>max(trigger_times) for c in (1,2,3)),
         'Missing post-write active setpoint verification')
    need(any(tr.get('event')=='write' and tr.get('command')=='SYST:LOC'
             for tr in traces) and
         [t.get('command') for t in traces if t.get('event')=='write'][-1]=='SYST:LOC',
         'LOCAL not final physical write')
    return dict(status='REVIEWABLE_PASS_OFF_ONLY_NOT_TRIGGER_ACTUATION',channel=ch,
                staged={'voltage_v':sv,'current_a':si},original=initial,
                restored=restored, scpi_queries=len(queries),
                off_readbacks=len(off_readbacks),
                firmware=identity['firmware'],source_sha256=software['source_sha256'],
                digital_off_confirmed=True,physical_off_confirmed=True)


def audit_path(path):
    raw=Path(path).read_bytes()
    need(raw.endswith(b'\n') and len(raw)<12*1024*1024, 'Truncated/oversized JSONL')
    try: records=[json.loads(line) for line in raw.splitlines()]
    except (ValueError,UnicodeDecodeError) as exc:
        raise AuditError('Invalid JSONL') from exc
    result=audit(records)
    result['original_sha256']=hashlib.sha256(raw).hexdigest()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    args=parser.parse_args(argv)
    try: result=audit_path(args.events)
    except (AuditError,OSError) as exc:
        print(f'R5-E4 AUDIT REJECTED: {exc}')
        return 2
    print(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
