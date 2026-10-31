"""Independent original-JSONL audit: ONE neutral *TRG, all outputs OFF.

No instrument I/O. REVIEWABLE is not coupling, transition, analog or timing proof.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

# Keep the wire whitelist in this independent offline auditor. Do not trust
# constants imported from the physical runner or the production driver.
STAGE = 'R5E7NEUTRALTRIGGER'
PHASE = 'NEUTRAL_BUS_TRIGGER_OFF'
ACTIVE = {1:(3.30,.150),2:(1.00,.100),3:(1.00,.100)}
READS = frozenset({
    '*IDN?','SYST:ERR?','OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?',
    'INST:NSEL?','CHAN:OUTP?','VOLT?','CURR?','VOLT:LIM?',
    'VOLT:LIM:STAT?','OUTP:TIM:STAT?','VOLT:TRIG?','CURR:TRIG?',
})
WRITES = frozenset({
    'SYST:REM','SYST:LOC','OUTP 0','OUTP:SER 0','OUTP:PARALLEL 0',
    'OUTP:TRAC 0','OUTP:TIM:STAT 0','VOLT 0','CURR 0',
    'VOLT:LIM 3.50','VOLT:LIM 1.50','VOLT:LIM:STAT 1',
    'VOLT 3.30','VOLT 1.00','CURR 0.150','CURR 0.100',
    'INST:NSEL 1','INST:NSEL 2','INST:NSEL 3',
})

class AuditError(ValueError):
    pass


def need(condition, msg):
    if not condition:
        raise AuditError(msg)


def num(v):
    try:
        n=float(v)
    except (TypeError,ValueError,OverflowError) as exc:
        raise AuditError('Non-numeric SCPI response') from exc
    need(math.isfinite(n),'Nonfinite SCPI number')
    return n


def near(x,y):
    return abs(num(x)-num(y)) < .00049


def audit(records):
    need(type(records) is list and len(records)>500, 'Missing/short evidence')
    need(all(type(x) is dict and x.get('test_id')==STAGE and
             type(x.get('monotonic_s')) in (int,float) and
             math.isfinite(x['monotonic_s']) for x in records),
         'Mixed identities or invalid events')
    need(all(a['monotonic_s']<=b['monotonic_s'] for a,b in
             zip(records,records[1:])), 'Nonmonotonic event ordering')
    def one(event):
        matches=[(i,x) for i,x in enumerate(records) if x.get('event')==event]
        need(len(matches)==1,'Expected exactly one '+event)
        return matches[0]
    start_i,start=one('start'); approval_i,approval=one('pre_open_approvals')
    identity_i,identity=one('psu_identity')
    verified_i,verified=one('neutral_presets_verified')
    event_i,event=one('neutral_bus_event')
    result_i,result=one('results');out_i,outcome=one('outcome')
    need((start_i,approval_i)==(0,1) and
         identity_i<verified_i<event_i<result_i<out_i==len(records)-1,
         'Invalid event bracketing')
    need(start.get('phase')==PHASE and approval.get('gates')==['BENCH','INITIAL'] and
         approval.get('approved_before_evidence_open') is True and
         approval.get('config_sha256')==start.get('config_sha256'),
         'Incorrect stage or pre-open approval')
    source=start.get('software')
    need(type(source) is dict,'Missing source provenance')
    for value in (start.get('config_sha256'),*(source.get(k) for k in (
            'source_sha256','r5e7_runner_sha256','r5e7_stage_sha256'))):
        need(type(value) is str and len(value)==64 and
             set(value)<=set('0123456789abcdef'), 'Invalid source/config digest')
    need(identity.get('model')=='2231A-30-3' and
         identity.get('firmware') in ('1.04-1.04','1.04-0.01'),
         'Unrecognized PSU identity')
    need(not any(x['event'] in ('stage_error','io_error','invalid_scpi_reply',
        'shutdown_error_queue','cleanup_error','firmware_revision_drift') for x in records),
         'Evidence contains a fault')
    need(all(outcome.get(k)==v for k,v in {
        'phase':PHASE,'outcome':'PASS','link_known':True,
        'digital_off_confirmed':True,'physical_off_confirmed':True,
        'hardware_review':'PENDING'}.items()), 'Missing PASS and OFF verification')
    gates=[(i,x) for i,x in enumerate(records) if x['event']=='operator_confirmation']
    need([x.get('gate') for _,x in gates]==['PROFILE','EVENT','FINAL'] and
         gates[0][0]<verified_i<gates[1][0]<event_i<result_i<gates[2][0]<out_i,
         'Missing/out-of-order human approvals')
    need(event.get('command')=='*TRG' and event.get('count')==1 and
         event.get('coupling')=='UNKNOWN_NOT_QUALIFIED' and
         event.get('analog')=='NOT_MEASURED', 'Unqualified event claims')
    data=result.get('values')
    need(type(data) is dict and data.get('trigger_count')==1 and
         data.get('active_unchanged') is True and data.get('all_channels_off') is True and
         data.get('analog')=='NOT_MEASURED' and
         data.get('coupling')=='UNKNOWN_NOT_QUALIFIED', 'Invalid result claims')
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,
           separators=(',',':')).encode('ascii')).hexdigest()
    need(digest==result.get('results_sha256'), 'Results SHA-256 mismatch')
    original,neutral,restored,active=(data.get(k) for k in
                                     ('original','neutral','restored','active'))
    need(all(type(d) is dict and set(d)=={'1','2','3'} for d in
             (original,neutral,restored,active)), 'Missing all-channel inventory')
    for ch in (1,2,3):
        key=str(ch)
        need(all(type(d[key]) is dict and
                 set(d[key])=={'voltage_v','current_a'} for d in
                 (original,neutral,restored,active)), 'Invalid trigger snapshot')
        for idx, field in enumerate(('voltage_v','current_a')):
            val=ACTIVE[ch][idx]
            need(near(neutral[key][field],val) and
                 near(active[key][field],val) and
                 near(original[key][field],restored[key][field]),
                 'Neutral or restored values mismatch')
            need(0<=num(original[key][field]) <= (
                (3.5 if ch==1 else 1.5) if idx==0 else (.15 if ch==1 else .1)),
                 'Original trigger exceeds bounded profile')
        need(verified.get('values',{}).get(key)==neutral[key],
             'Neutral checkpoint inconsistent with readbacks')
    need(verified.get('channels')==[1,2,3] and
         verified.get('event_sent') is False, 'Premature bus event')
    writes=[x for x in records if x['event']=='write']
    queries=[x for x in records if x['event']=='query']
    reads=[x for x in records if x['event']=='read']
    trace=[(i,x['trace']) for i,x in enumerate(records) if x['event']=='driver_trace']
    need(len(queries)>=300 and len(queries)==len(reads) and
         Counter(x.get('command') for x in queries)==Counter(x.get('command') for x in reads),
         'Missing wire query/response evidence')
    allowed_writes=set(WRITES) | {'*TRG'} | {
        f'{command} {val:.3f}' for ch in (1,2,3)
        for command, val in (
            ('VOLT:TRIG',ACTIVE[ch][0]),('CURR:TRIG',ACTIVE[ch][1]),
            ('VOLT:TRIG',num(original[str(ch)]['voltage_v'])),
            ('CURR:TRIG',num(original[str(ch)]['current_a'])))}
    need(all(x.get('command') in allowed_writes for x in writes),
         'Disallowed physical write (includes output-enable or coupling)')
    need(all(x.get('command') in READS for x in queries+reads),
         'Disallowed physical query (includes INST:COUP?)')
    need([tr.get('command') for _,tr in trace if tr.get('event')=='write']==
         [x['command'] for x in writes], 'Wire write trace mismatch')
    need(Counter(tr.get('command') for _,tr in trace if tr.get('event')=='query')==
         Counter(x['command'] for x in queries), 'Wire query trace mismatch')
    need([(tr.get('command'),tr.get('response')) for _,tr in trace if tr.get('event')=='reply']==
         [(x.get('command'),x.get('response')) for x in reads],
         'Wire response trace mismatch')
    pending=None; selected=None; op={ch:[] for ch in (1,2,3)}
    trigger_index=None; off_after=set(); final_off=set(); final_outp_idx=None
    last_global_off_idx=None; active_after={ch:set() for ch in (1,2,3)}
    for record_i,tr in trace:
        kind=tr.get('event');cmd=tr.get('command')
        need(kind in ('query','reply','write') and type(cmd) is str,
             'Invalid wire trace event')
        if kind=='query':
            need(pending is None and cmd in READS,'Overlapping/invalid wire query')
            pending=cmd
        elif kind=='reply':
            need(pending==cmd,'Unmatched wire query response')
            pending=None
            answer=tr.get('response')
            if cmd=='SYST:ERR?':
                parts=str(answer).split(',',1)
                need(len(parts)==2 and parts[0].strip()=='0' and parts[1].strip(),
                     'Unexpected SCPI error')
            elif cmd=='INST:NSEL?':
                need(str(answer).strip() in ('1','2','3') and
                     (selected is None or int(answer)==selected),
                     'Unexpected channel selection')
            elif cmd=='CHAN:OUTP?':
                need(selected in (1,2,3) and str(answer).strip()=='0',
                     'Observed output ON')
                if trigger_index is not None:off_after.add(selected)
                if last_global_off_idx is not None and record_i>last_global_off_idx:
                    final_off.add(selected)
            elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?','OUTP:TIM:STAT?'):
                need(str(answer).strip()=='0','Unexpected coupled topology or timer ON')
            elif cmd=='VOLT:LIM:STAT?':
                need(str(answer).strip()=='1','Voltage limit disabled')
            elif cmd=='VOLT:LIM?' and selected in ACTIVE:
                need(near(answer,3.5 if selected==1 else 1.5),'Wrong voltage limit')
            elif cmd in ('VOLT?','CURR?') and selected in ACTIVE:
                need(near(answer,ACTIVE[selected][0 if cmd=='VOLT?' else 1]),
                     'Active setting changed')
                if trigger_index is not None:active_after[selected].add(cmd)
            elif cmd in ('VOLT:TRIG?','CURR:TRIG?'):
                need(selected in ACTIVE,'Trigger query without channel')
                op[selected].append((record_i,cmd,num(answer)))
        else:
            need(pending is None and cmd in allowed_writes,'Unexpected physical write')
            if cmd.startswith('INST:NSEL '):selected=int(cmd[-1])
            elif cmd=='OUTP 0':last_global_off_idx=record_i
            elif cmd=='*TRG':
                need(trigger_index is None and
                     gates[1][0]<record_i<event_i,
                     'Duplicate, early or late *TRG')
                trigger_index=record_i
            elif cmd.startswith(('VOLT:TRIG ','CURR:TRIG ')):
                need(selected in ACTIVE,'Trigger write without selected channel')
                op[selected].append((record_i,cmd,num(cmd.split()[1])))
    need(pending is None,'Unanswered wire query')
    need(trigger_index is not None and verified_i<trigger_index and
         {1,2,3}==off_after and {1,2,3}==final_off,
         'No single armed trigger or incomplete post-trigger OFF')
    need(last_global_off_idx is not None and last_global_off_idx>trigger_index,
         'Missing final global OFF')
    need(all(x=={'VOLT?','CURR?'} for x in active_after.values()),
         'Post-trigger all-channel active settings unverified')
    need([tr.get('command') for _,tr in trace if tr.get('event')=='write'][-1]=='SYST:LOC',
         'LOCAL not final physical write')
    for ch in (1,2,3):
        orig=original[str(ch)];v,i=ACTIVE[ch]
        ov,oi=num(orig['voltage_v']),num(orig['current_a'])
        pattern=[('VOLT:TRIG?',ov),('CURR:TRIG?',oi),
                 (f'VOLT:TRIG {v:.3f}',v),('VOLT:TRIG?',v),
                 (f'CURR:TRIG {i:.3f}',i),('CURR:TRIG?',i),
                 ('VOLT:TRIG?',v),('CURR:TRIG?',i),
                 ('VOLT:TRIG?',v),('CURR:TRIG?',i),
                 (f'VOLT:TRIG {ov:.3f}',ov),('VOLT:TRIG?',ov),
                 (f'CURR:TRIG {oi:.3f}',oi),('CURR:TRIG?',oi),
                 ('VOLT:TRIG?',ov),('CURR:TRIG?',oi)]
        actual=op[ch]
        need(len(actual)==len(pattern) and all(
            cmd==exp and near(value,want) for (_,cmd,value),(exp,want) in
            zip(actual,pattern)), f'CH{ch}: wrong write/readback sequence')
        need(all(idx<trigger_index for idx,_,_ in actual[:10]) and
             all(idx>event_i for idx,_,_ in actual[10:]),
             f'CH{ch}: registers not neutral before trigger or not restored after')
    need(sum(x.get('command')=='*TRG' for x in writes)==1,
         'Missing/duplicate physical *TRG')
    return {'status':'REVIEWABLE_PASS_NEUTRAL_OFF_ONLY_NOT_ACTUATION',
            'trigger_count':1,'channels':[1,2,3],
            'original':original,'neutral':neutral,'restored':restored,
            'queries':len(queries),'output_off_readbacks':len(final_off),
            'firmware':identity['firmware'],
            'source_sha256':source['source_sha256'],
            'coupling':'UNKNOWN_NOT_QUALIFIED','analog':'NOT_MEASURED'}


def audit_path(path):
    raw=Path(path).read_bytes()
    need(raw.endswith(b'\n') and len(raw)<24*1024*1024,
         'Truncated or oversized original JSONL')
    try:records=[json.loads(line) for line in raw.splitlines()]
    except (ValueError,UnicodeDecodeError) as exc:
        raise AuditError('Invalid original JSONL') from exc
    result=audit(records)
    result['records']=len(records)
    result['evidence_sha256']=hashlib.sha256(raw).hexdigest()
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    args=parser.parse_args(argv)
    try:r=audit_path(args.events)
    except (AuditError,OSError) as exc:
        print(f'R5-E7 AUDIT REJECTED: {exc}')
        return 2
    print(json.dumps(r,indent=2,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
