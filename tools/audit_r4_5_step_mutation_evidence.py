"""Offline audit of one R4.5-D OFF-only STEP mutation JSONL.

This checks recorded digital command/reply behavior, NOT physical voltage,
independent output OFF, analog precision, or later experimental readiness.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

STAGE = 'R45STEPMUT'
K = {'voltage': 'VOLT', 'current': 'CURR'}
STEPS = {'voltage': {0.001, 0.005, 0.010, 0.050, 0.100},
         'current': {0.001, 0.005, 0.010}}
READS = {'*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?',
         'OUTP:TRAC?', 'INST:NSEL?', 'CHAN:OUTP?', 'VOLT?', 'CURR?',
         'OUTP:TIM:STAT?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
         'VOLT:STEP?', 'CURR:STEP?'}
WRITES = {'SYST:REM','SYST:LOC','INST:NSEL 1','INST:NSEL 2','INST:NSEL 3',
          'OUTP 0','OUTP:SER 0','OUTP:PARALLEL 0','OUTP:TRAC 0',
          'OUTP:TIM:STAT 0','VOLT 0','CURR 0',
          'VOLT:LIM 3.50','VOLT:LIM 1.50','VOLT:LIM:STAT 1',
          'CURR 0.100','CURR 0.050','VOLT 3.30','VOLT 1.00'}
EVENTS = {'start','pre_open_approvals','psu_identity','query','read','write',
          'driver_trace','operator_confirmation','results','outcome'}

class AuditError(ValueError):
    pass

def require(test, detail):
    if not test:
        raise AuditError(detail)

def near(a, b, message, tol=0.00049):
    try:
        require(type(a) in (int,float,str) and
                math.isfinite(float(a)) and abs(float(a)-b) <= tol, message)
    except (ValueError, OverflowError, TypeError) as exc:
        raise AuditError(message) from exc


def audit(records):
    require(isinstance(records, list) and len(records) > 100, 'missing/short evidence')
    require(all(type(r) is dict and r.get('test_id') == STAGE and
                r.get('event') in EVENTS for r in records), 'mixed/unknown events')
    ticks = [r.get('monotonic_s') for r in records]
    require(all(type(v) in (int,float) and math.isfinite(v) for v in ticks) and
            all(a <= b for a,b in zip(ticks,ticks[1:])), 'unordered monotonic timestamps')
    try:
        dates = [datetime.fromisoformat(r['utc']) for r in records]
        require(all(v.tzinfo is not None for v in dates) and
                all(a <= b for a,b in zip(dates,dates[1:])), 'unordered UTC stamps')
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditError('invalid timestamps') from exc
    def one(evt):
        found = [r for r in records if r['event']==evt]
        require(len(found)==1, f'exactly one {evt} required')
        return found[0]
    start, pre, identity, result, outcome = [one(name) for name in
        ('start','pre_open_approvals','psu_identity','results','outcome')]
    require(records[0] is start and records[-1] is outcome and
            start.get('variant')=='STEP_MUTATION_OFF' and
            outcome.get('phase')=='STEP_MUTATION_OFF', 'incorrect run type/bracketing')
    ch, kind, increment = start.get('channel'),start.get('kind'),start.get('increment')
    require(type(ch) is int and ch in (1,2,3) and kind in K and
            type(increment) in (float,int) and increment in STEPS[kind],
            'unapproved STEP case')
    require(all(r.get('channel') == ch and r.get('kind') == kind and
                r.get('increment') == increment for r in (outcome,)),
            'outcome case mismatch')
    digest = start.get('config_sha256')
    require(isinstance(digest,str) and len(digest)==64 and
            all(c in '0123456789abcdef' for c in digest), 'invalid config digest')
    require(pre.get('gates')==['BENCH','INITIAL'] and
            pre.get('approved_before_evidence_open') is True and
            pre.get('config_sha256')==digest, 'initial approvals missing')
    gates=[r for r in records if r['event']=='operator_confirmation']
    require([g.get('gate') for g in gates] ==
            ['PROFILE','MUTATION','FINAL_PHYSICAL','FINAL'], 'missing/order of physical gates')
    require(gates[1].get('channel')==ch and gates[1].get('kind')==kind and
            gates[1].get('increment')==increment, 'mutation operator token mismatch')
    require(outcome.get('outcome')=='PASS' and
            outcome.get('digital_off_confirmed') is True and
            outcome.get('physical_off_confirmed') is True and
            outcome.get('link_known') is True, 'unsafe/incomplete outcome')
    require(identity.get('model')=='2231A-30-3' and
            identity.get('firmware')=='1.04-1.04' and not identity.get('response'),
            'unexpected or unredacted identity')
    require(outcome.get('firmware_observations') == ['1.04-1.04'],
            'unexpected firmware')
    software=start.get('software',{})
    require(type(software) is dict and software.get('sha') and
            all(len(str(software.get(k,'')))==64 for k in
                ('r45d_runner_sha256','r45d_stage_sha256','source_sha256')),
            'missing provenance hashes')

    queries=[r for r in records if r['event']=='query']
    reads=[r for r in records if r['event']=='read']
    writes=[r for r in records if r['event']=='write']
    require(len(queries)==len(reads) and len(queries)>0, 'highlevel query/read mismatch')
    prefix=K[kind]
    mut=[f'{prefix}:STEP {increment:.3f}',f'{prefix}:UP',f'{prefix}:UP',
         f'{prefix}:DOWN',f'{prefix}:DOWN']
    allowed_writes=WRITES | set(mut)
    require(all(r.get('command') in READS for r in queries+reads) and
            all(r.get('command') in allowed_writes for r in writes),
            'unapproved command in evidence')
    # A high-level query can enclose automatic SYST:REM and its SYST:ERR?;
    # high-level query-start order is not necessarily physical wire order.
    require(Counter(r['command'] for r in queries) ==
            Counter(r['command'] for r in reads), 'highlevel query/read count mismatch')
    traces=[r['trace'] for r in records if r['event']=='driver_trace']
    require(all(type(t) is dict and t.get('event') in ('query','reply','write')
                for t in traces), 'invalid driver trace records')
    for event,high in (('query',queries),('reply',reads),('write',writes)):
        lower=[t for t in traces if t.get('event')==event]
        if event=='query':
            require(Counter(t.get('command') for t in lower) ==
                    Counter(r.get('command') for r in high),
                    'query trace/highlevel counts mismatch')
        else:
            require([t.get('command') for t in lower] ==
                    [r.get('command') for r in high],
                    f'high/low {event} order mismatch')
        if event=='reply':
            require([(t.get('command'),t.get('response')) for t in lower] ==
                    [(r.get('command'),r.get('response')) for r in high],
                    'reply order/value mismatch')
    require(all(a.get('monotonic_s',0)<=b.get('monotonic_s',0)
                for a,b in zip(traces,traces[1:])), 'unordered wire trace')

    # Physical command sequence and per-command immediate SYST:ERR? discipline.
    paired=[]; awaiting=None
    for t in traces:
        event,cmd=t['event'],t.get('command')
        if event=='reply':
            require(awaiting==cmd,'orphan/mismatched SCPI reply')
            paired.append(('reply',cmd,t.get('response')))
            awaiting=None
        else:
            require(awaiting is None, 'interleaved/unfinished SCPI query')
            require(cmd in (READS if event=='query' else allowed_writes),
                    'unapproved SCPI trace command')
            paired.append((event,cmd,None))
            if event=='query': awaiting=cmd
    require(awaiting is None, 'unfinished final SCPI query')
    actions=[p for p in paired if p[0] in ('query','write')]
    require(actions and actions[-1]==('write','SYST:LOC',None), 'last command not LOCAL')
    waiting_polls=0
    for idx,(typ,cmd,_) in enumerate(actions):
        if cmd=='SYST:ERR?':
            require(waiting_polls>0, 'stray error poll')
            waiting_polls-=1
        else:
            require(waiting_polls==0, 'missing immediate error poll')
            waiting_polls=(0 if cmd=='SYST:LOC' else (2 if cmd in mut else 1))
    require(waiting_polls==0, 'missing last error poll')
    errors=[v for typ,cmd,v in paired if typ=='reply' and cmd=='SYST:ERR?']
    require(all(isinstance(v,str) and v.replace(' ', '') in
                ('0,\"Noerror\"', '0,Noerror') for v in errors),
            'nonzero SCPI error')
    require(Counter(cmd for typ,cmd,_ in paired if typ=='write' and
                    cmd in ('SYST:REM','SYST:LOC')) ==
            Counter({'SYST:REM':3,'SYST:LOC':1}), 'REMOTE/LOCAL mismatch')
    require(sum(cmd=='OUTP 0' for typ,cmd,_ in actions)==2,
            'missing/extra global OFF')
    mutations=[(i,cmd) for i,(typ,cmd,_) in enumerate(paired) if
               typ=='write' and cmd in mut]
    require([x[1] for x in mutations]==mut, 'STEP write / action order mismatch')
    require(gates[0]['monotonic_s'] <
            next(t['monotonic_s'] for t in traces if t['event']=='write' and t['command']=='OUTP 0') <
            gates[1]['monotonic_s'] <
            next(t['monotonic_s'] for t in traces if t['event']=='write' and t['command']==mut[0]),
            'profile/mutation gate chronology')

    selected=None; off_channels=[]; step_replies=[]; direction_readbacks=[]
    expected_setpoints={1:{'voltage':3.30,'current':.100},
                        2:{'voltage':1.00,'current':.050},
                        3:{'voltage':1.00,'current':.050}}
    last_mutation=None
    readback_after_mutation=False
    finished_actions=[]
    for i,t in enumerate(traces):
        ev,cmd=t['event'],t.get('command')
        if ev=='write':
            if cmd.startswith('INST:NSEL '):selected=int(cmd[-1])
            if cmd in mut:
                require(selected==ch, 'STEP mutation on wrong selected channel')
                if last_mutation is not None:
                    require(readback_after_mutation, 'missing setpoint readback between mutations')
                last_mutation=cmd
                readback_after_mutation=False
                if cmd in (f'{prefix}:UP',f'{prefix}:DOWN'):
                    expected_setpoints[ch][kind]+=increment * (1 if cmd.endswith('UP') else -1)
            if cmd.startswith('VOLT ') and cmd in ('VOLT 0','VOLT 3.30','VOLT 1.00'):
                require(selected in (1,2,3), 'VOLT write unknown channel')
                expected_setpoints[selected]['voltage']=float(cmd.split()[1])
            if cmd.startswith('CURR ') and cmd in ('CURR 0','CURR 0.100','CURR 0.050'):
                require(selected in (1,2,3), 'CURR write unknown channel')
                expected_setpoints[selected]['current']=float(cmd.split()[1])
        if ev!='reply':continue
        ans=str(t.get('response',''))
        if cmd=='INST:NSEL?':
            if selected is None:
                require(ans in ('1','2','3'), 'unknown initial channel')
            else:require(ans==str(selected), 'selected channel drift')
        elif cmd=='CHAN:OUTP?':
            require(selected in (1,2,3) and ans=='0', 'channel ON or unknown')
            off_channels.append(selected)
        elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?','OUTP:TIM:STAT?'):
            require(ans=='0', 'topology/timer enabled')
        elif cmd=='VOLT:LIM:STAT?':require(ans=='1','voltage limit disabled')
        elif cmd=='VOLT:LIM?':near(ans,3.50 if selected==1 else 1.50,'wrong voltage cap',.0001)
        elif cmd in ('VOLT?','CURR?'):
            require(selected in (1,2,3),'unselected setpoint read')
            k='voltage' if cmd=='VOLT?' else 'current'
            near(ans,expected_setpoints[selected][k],'setpoint deviation')
            if cmd==prefix+'?' and last_mutation is not None and not readback_after_mutation:
                readback_after_mutation=True
                if last_mutation in (f'{prefix}:UP',f'{prefix}:DOWN'):
                    direction_readbacks.append(float(ans))
        elif cmd==prefix+':STEP?':
            near(ans,increment,'step readback mismatch')
            step_replies.append(float(ans))
        elif cmd in ('VOLT:STEP?','CURR:STEP?'):
            raise AuditError('unapproved STEP query')
        elif cmd=='*IDN?':
            require('2231A-30-3' in ans and '[REDACTED]' in ans,
                    'unredacted/wrong instrument identity')
    require(len(off_channels)==78 and off_channels==[1,2,3]*26,
            'missing/wrong order OFF sweep')
    require(len(step_replies)==5 and all(abs(v-increment)<=.00049 for v in step_replies),
            'missing STEP confirmation')
    initial=3.30 if ch==1 and kind=='voltage' else (
            1.00 if kind=='voltage' else (.100 if ch==1 else .050))
    expected_steps=[initial+increment,initial+2*increment,initial+increment,initial]
    require(len(direction_readbacks)==4 and
            all(abs(x-y)<=.00049 for x,y in zip(direction_readbacks,expected_steps)),
            'UP/DOWN wrong digital step')
    require(readback_after_mutation and selected in (1,2,3),'final mutation not read back')
    low_writes=[(i,t['command']) for i,t in enumerate(traces) if t['event']=='write']
    off_positions=[i for i,c in low_writes if c=='OUTP 0']
    mutation_positions=[i for i,c in low_writes if c in mut]
    require(off_positions[0]<mutation_positions[0]<mutation_positions[-1]<off_positions[1],
            'OFF / mutation order incorrect')
    final_off=[x for x in enumerate(traces) if x[0]>off_positions[1] and
               x[1]['event']=='reply' and x[1]['command']=='CHAN:OUTP?']
    require(len(final_off)==6,'missing 2 final OFF sweeps')
    require(traces[-1]['event']=='write' and traces[-1]['command']=='SYST:LOC',
            'post-local commands present')
    require(result['monotonic_s'] > traces[-1]['monotonic_s'] and
            gates[2]['monotonic_s']>result['monotonic_s'] and
            gates[3]['monotonic_s']>=gates[2]['monotonic_s'] and
            outcome['monotonic_s']>gates[3]['monotonic_s'],
            'final evidence/gate chronology invalid')
    values=result.get('values',{})
    require(type(values) is dict and values.get('channel')==ch and
            values.get('kind')==kind and values.get('increment')==increment and
            values.get('analog_measurement')=='NOT_RUN' and
            values.get('direction_plan')==['up','up','down','down'],
            'wrong result metadata')
    near(values.get('initial_setpoint'),initial,'wrong initial setpoint')
    near(values.get('step_readback'),increment,'wrong recorded step')
    require(len(values.get('steps',[]))==4 and
            [r.get('direction') for r in values['steps']]==['up','up','down','down'],
            'missing UP/DOWN results')
    for measured,expected in zip(values['steps'],expected_steps):
        near(measured.get('setpoint'),expected,'wrong reported step setpoint')
    near(values.get('final_setpoint'),initial,'not restored to initial')
    require(result.get('results_sha256') == hashlib.sha256(
        json.dumps(values,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest(),
        'result digest corrupted')
    return {'status':'REVIEWABLE_PASS', 'scope':'one digital OFF-only case; analog NOT_RUN',
            'channel':ch,'kind':kind,'step':increment,
            'setpoints':direction_readbacks,'records':len(records),
            'queries':len(queries),'writes':len(writes),'error_polls':len(errors),
            'output_off_checks':len(off_channels),'source_commit':software['sha'],
            'source_dirty':software.get('dirty'),'config_sha256':digest}


def _unique_object_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise AuditError(f'duplicate JSON key: {key}')
        result[key]=value
    return result


def audit_file(path, expected_sha256=None):
    raw=Path(path).read_bytes()
    digest=hashlib.sha256(raw).hexdigest()
    require(not expected_sha256 or digest==expected_sha256,'raw evidence SHA-256 mismatch')
    try:
        records=[json.loads(line,object_pairs_hook=_unique_object_pairs)
                 for line in raw.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise AuditError('invalid/duplicate JSONL record') from exc
    result=audit(records)
    result['evidence_sha256']=digest
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events_jsonl',type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    try:
        report=audit_file(args.events_jsonl,args.expected_sha256)
    except (OSError,AuditError) as exc:
        print(f'R4.5-D EVIDENCE AUDIT REJECTED: {exc}')
        return 2
    print('R4.5-D EVIDENCE REVIEWABLE PASS (not independent analog proof)')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
