#!/usr/bin/env python3
"""Read-only independent original-JSONL audit of R4.5-F-A resistive CV I-V pilot.

No instrument I/O, source changes, or calibration claim. The original file's
SHA-256 is mandatory. Hardware PASS is limited to five settled CR40 points.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, need, number, one, matching_pairs

LEVELS=(3.00,3.10,3.20,3.10,3.00)
DIRECTIONS=('VOLT:UP','VOLT:UP','VOLT:DOWN','VOLT:DOWN')
DMM_WRITES=('*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
            ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
            ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF')
SAFE_WRITES={
    'SYST:REM','SYST:LOC','OUTP 0','OUTP:SER 0','OUTP:PARALLEL 0',
    'OUTP:TRAC 0','OUTP:TIM:STAT 0','VOLT 0','CURR 0',
    'VOLT:LIM 3.50','VOLT:LIM 1.50','VOLT:LIM:STAT 1',
    'VOLT 3.00','VOLT 1.00','CURR 0.150','CURR 0.100',
    'VOLT:STEP 0.100','CHAN:OUTP 1','VOLT:UP','VOLT:DOWN',
    'INST:NSEL 1','INST:NSEL 2','INST:NSEL 3',
}


def unique(rows, kind):
    return one([x for x in rows if x.get('event')==kind],kind)


def source_read(rows, event, cmd, start, end):
    found=[x for x in rows[start:end] if x.get('event')==event and x.get('command')==cmd]
    need(bool(found),f'Missing raw {event} {cmd} in measurement window')
    return number(found[-1]['response'].rstrip('VAW').removesuffix('OHM'))


def wire_audit(rows):
    traces=[x['trace'] for x in rows if x.get('event')=='driver_trace']
    need(bool(traces),'No physical driver trace')
    pending=None; q=[]; replies=[]; writes=[]; selected=1
    output={1:False,2:False,3:False}; seen_on=False
    enable_at=None; off_after=None; move=[]; step_at=None
    off_confirmations=Counter(); cv_states=Counter()
    digital=[]
    for i,tr in enumerate(traces):
        typ,cmd=tr.get('event'),tr.get('command')
        need(isinstance(cmd,str) and bool(cmd) and tr.get('profile_bypass') is False,
             'Malformed or bypassed physical command')
        if typ=='query':
            need(pending is None,'Overlapping physical PSU queries')
            pending=cmd; q.append(cmd)
        elif typ=='reply':
            need(pending==cmd and isinstance(tr.get('response'),str),
                 'Missing/swapped physical PSU reply')
            pending=None; replies.append(tr)
            v=tr['response']
            if cmd=='CHAN:OUTP?':
                need(v==('1' if output[selected] else '0'),
                     f'Physical CH{selected} OFF/ON reply inconsistent')
                if off_after is not None and i>off_after and v=='0':
                    off_confirmations[selected]+=1
            elif cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?'):
                need(v=='0','Topology is not independent')
            elif cmd in ('STAT:OPER:INST:ISUM1:COND?', 'STAT:QUES:INST:ISUM1:COND?'):
                need(selected==1 and not (seen_on and not output[1]),
                     'Mode query outside CH1 powered session')
                need(v==('9' if cmd.startswith('STAT:OPER') else '1'),
                     'Unexpected CH1 CV status bits')
                cv_states[cmd]+=1
            elif cmd=='VOLT?' and selected==1:
                digital.append((i,number(v),bool(output[1])))
            elif cmd=='VOLT:STEP?':
                need(selected==1 and abs(number(v)-.100)<1e-9,
                     'Wrong STEP register readback')
        elif typ=='write':
            need(pending is None and cmd in SAFE_WRITES,
                 f'Nonallowlisted PSU write or pending query: {cmd}')
            writes.append(cmd)
            if cmd.startswith('INST:NSEL '): selected=int(cmd[-1])
            elif cmd=='OUTP 0':
                output={1:False,2:False,3:False}
                if seen_on: off_after=i
            elif cmd=='CHAN:OUTP 1':
                need(selected==1 and not seen_on,'Repeated or non-CH1 enable')
                output[1]=True; seen_on=True; enable_at=i
            elif cmd=='VOLT:STEP 0.100':
                need(selected==1 and step_at is None and not any(output.values()),
                     'STEP set more than once or while powered')
                step_at=i
            elif cmd in ('VOLT:UP','VOLT:DOWN'):
                need(selected==1 and output[1] and off_after is None,
                     'STEP moved with wrong output state/channel')
                move.append((i,cmd))
            elif seen_on and off_after is None and cmd not in ('INST:NSEL 1','INST:NSEL 2','INST:NSEL 3','SYST:REM'):
                raise AuditError(f'Configuration rewrite while CH1 ON: {cmd}')
        else:
            raise AuditError('Unexpected physical driver trace event')
    need(pending is None and len(q)==len(replies),'Unfinished PSU query')
    sq=[x['command'] for x in rows if x.get('event')=='query']
    sr=[x for x in rows if x.get('event')=='read']
    sw=[x['command'] for x in rows if x.get('event')=='write']
    need(Counter(q)==Counter(sq) and Counter(writes)==Counter(sw),
         'Physical SCPI commands disagree with stage records')
    need(len(replies)==len(sr) and all(a['command']==b['command'] and a['response']==b['response']
         for a,b in zip(replies,sr)),'Physical reply payload/order differs from stage')
    need([x[1] for x in move]==list(DIRECTIONS),'Physical STEP sequence/count wrong')
    need(step_at is not None and enable_at is not None and off_after is not None
         and step_at<enable_at<move[0][0] and move[-1][0]<off_after,
         'STEP, output ON and verified OFF physical order wrong')
    need(not any(output.values()) and all(off_confirmations[ch]>0 for ch in (1,2,3)),
         'Missing final CH1/2/3 OFF readbacks')
    need(writes.count('CHAN:OUTP 1')==1 and writes.count('VOLT:STEP 0.100')==1
         and writes.count('SYST:LOC')==1 and writes[-1]=='SYST:LOC',
         'Repeated enable/STEP or incorrect Local ending')
    need(cv_states['STAT:OPER:INST:ISUM1:COND?']>=5 and
         cv_states['STAT:QUES:INST:ISUM1:COND?']>=5,
         'Not enough independent CV status checks')
    need(q.count('VOLT:STEP?')==5, 'Wrong count of physical STEP register confirmations')
    zero=[x for x in sr if x['command']=='SYST:ERR?']
    need(len(zero)>900 and all(x['response'].split(',',1)[0].strip()=='0' for x in zero),
         'Keithley SCPI errors nonzero or missing')
    return dict(scpi_queries=len(q),scpi_writes=len(writes),
                scpi_error_polls=len(zero),
                off_readbacks=sum(x['command']=='CHAN:OUTP?' and x['response']=='0' for x in sr),
                move_commands=[x[1] for x in move],physical_trace=traces)


def audit(rows):
    need(bool(rows) and all(x.get('test_id')=='R45FIVA' for x in rows),
         'Mixed or missing evidence test identity')
    times=[number(x.get('monotonic_s')) for x in rows]
    need(all(a<=b for a,b in zip(times,times[1:])),'Non-monotonic event times')
    initial=unique(rows,'start')
    need(initial.get('variant')=='RESISTIVE_IV_CR40' and initial.get('commanded_voltages')==list(LEVELS)
         and initial.get('directions')==['up','up','down','down'] and
         number(initial.get('power_ceiling_w'))==.6,'Wrong approved I-V stage')
    sw=initial.get('software') or {}
    need(sw.get('sha')=='b73f6d13ceb640d5146ee04b6acf3655cca68a07'
         and sw.get('r45f_runner_sha256')=='f9c643bc21fc3995cf25a0e5ebffdda0ad4b6531e967c099139659c263e07b2f'
         and sw.get('r45f_stage_sha256')=='a8204e2be00fa0839a187c3b1c25aaef320975d44207198bdb07931450c5d35f',
         'Unreviewed powered code provenance')
    need(isinstance(initial.get('config_sha256'),str) and len(initial['config_sha256'])==64
         and initial['config_sha256']==unique(rows,'pre_open_approvals').get('config_sha256')
         and unique(rows,'pre_open_approvals').get('gates')==['BENCH','INITIAL'],
         'Missing independent bench approval')
    ids=[x['response'] for x in rows if x.get('event')=='read' and x.get('command')=='*IDN?']
    need(len(ids)==2 and all('Keithley' in s and '2231A-30-3' in s and
         s.rsplit(',',1)[-1].strip() in ('1.04-1.04','1.04-0.01') for s in ids),
         'Unexpected PSU identity/firmware')
    need(unique(rows,'psu_identity')['response'].rsplit(',',1)[-1].strip()==
         ids[0].rsplit(',',1)[-1].strip(),'Identity summary mismatch')
    gates=[(i,x['gate']) for i,x in enumerate(rows) if x['event']=='operator_confirmation']
    need([g for i,g in gates]==['POWER','FINAL'],'Missing/misordered manual gates')
    end=unique(rows,'outcome')
    need(end is rows[-1] and end.get('outcome')=='PASS' and
         end.get('status')=='HW NOT_REVIEWED' and end.get('physical_off_confirmed') is True,
         'Outcome not a fully completed operator-gated run')
    need(not any(x.get('event') in ('stage_error','io_error','invalid_scpi_reply','shutdown_error_queue')
                 for x in rows),'Stage or transport fault in evidence')
    trace=wire_audit(rows)
    kel=matching_pairs(rows,'kel103_query','kel103_read')
    dmm=matching_pairs(rows,'dmm7510_query','dmm7510_read')
    kel_w=[(i,x['command']) for i,x in enumerate(rows) if x['event']=='kel103_write']
    dmm_w=[(i,x['command']) for i,x in enumerate(rows) if x['event']=='dmm7510_write']
    need([cmd for _,cmd in kel_w]==[':FUNC CR',':RES 40OHM',':INP ON',':INP OFF'],
         'Load input/configuration did not follow approved order')
    need([cmd for _,cmd in dmm_w]==list(DMM_WRITES) and dmm_w[-1][0]<gates[0][0],
         'Unexpected DMM setting, or modified during ON')
    conf=unique(rows,'dmm7510_configuration')
    need(conf.get('outcome')=='PASS' and conf.get('terminals')=='front'
         and conf.get('function')=='DC voltage' and conf.get('range_v')==10
         and conf.get('nplc')==1 and unique(rows,'dmm7510_command_set').get('language')=='SCPI',
         'DMM DCV configuration unconfirmed')
    need(any('MODEL DMM7510' in r['response'] for i,r in dmm if r['command']=='*IDN?')
         and all(r['response'].split(',',1)[0].strip()=='0'
                 for i,r in dmm if r['command']==':SYST:ERR?'),
         'DMM identity or nonzero error')
    need(all(r['response']=='CR' for i,r in kel if r['command']==':FUNC?') and
         all(abs(number(r['response'].removesuffix('OHM'))-40)<.001
             for i,r in kel if r['command']==':RES?'),
         'KEL CR40 mode/setting not consistently verified')
    kstates=[r['response'] for i,r in kel if r['command']==':INP?']
    need(kstates and kstates[0]=='OFF' and kstates[-2:]==['OFF','OFF'] and 'ON' in kstates,
         'KEL input not confirmed both before and after')
    points=[(i,x) for i,x in enumerate(rows) if x['event']=='iv_point']
    steps=[(i,x) for i,x in enumerate(rows) if x['event']=='iv_digital_step']
    need(len(points)==5 and len(steps)==4, 'Five physical points or four digital steps missing')
    pwrites=[(i,x['command']) for i,x in enumerate(rows) if x['event']=='write']
    en=one([i for i,c in pwrites if c=='CHAN:OUTP 1'],'CH1 ON')
    off=[i for i,c in pwrites if c=='OUTP 0' and i>en]
    loc=one([i for i,c in pwrites if c=='SYST:LOC'],'LOCAL')
    need(off and gates[0][0]<kel_w[2][0]<en<points[0][0] and
         points[-1][0]<off[0]<kel_w[-1][0]<loc<gates[-1][0],
         'Incorrect powered gate, measurement, independent shutdown or Local order')
    moves=[i for i,c in pwrites if c in DIRECTIONS]
    need(len(moves)==4 and all(points[j][0]<moves[j]<steps[j][0]<points[j+1][0]
                                for j in range(4)),
         'Steps not interleaved with raw physical readings in approved order')
    samples=[]
    for j,(idx,p) in enumerate(points):
        start=points[j-1][0] if j else en
        if j: start=steps[j-1][0]
        need(p.get('index')==j and p.get('sampling')=='SEQUENTIAL_NOT_SYNCHRONIZED',
             'Unexpected I-V point identity or sampling assumption')
        vals={key:number(p.get(key)) for key in ('commanded_v','digital_v','psu_v','psu_a',
             'psu_meas_w','kel_v','kel_a','kel_w','dmm_v','kel_effective_ohm')}
        need(abs(vals['commanded_v']-LEVELS[j])<1e-10 and
             abs(vals['digital_v']-LEVELS[j])<.00049,
             'Wrong absolute preset/digital level')
        for k,cmd in (('psu_v','MEAS:VOLT?'),('psu_a','MEAS:CURR?'),('psu_meas_w','MEAS:POW?')):
            need(abs(vals[k]-source_read(rows,'read',cmd,start,idx))<1e-7,
                 f'PSU {k} not bound to raw response')
        for k,cmd in (('kel_v',':MEAS:VOLT?'),('kel_a',':MEAS:CURR?'),('kel_w',':MEAS:POW?')):
            need(abs(vals[k]-source_read(rows,'kel103_read',cmd,start,idx))<1e-7,
                 f'KEL {k} not bound to raw response')
        need(abs(vals['dmm_v']-source_read(rows,'dmm7510_read',':READ?',start,idx))<1e-8,
             'DMM voltage not bound to raw response')
        need(abs(vals['digital_v']-source_read(rows,'read','VOLT?',start,idx))<.00049,
             'Digital level not bound to physical PSU readback')
        need(abs(vals['kel_effective_ohm']-vals['kel_v']/vals['kel_a'])<1e-9,
             'Recorded instantaneous equivalent resistance invalid')
        need(2.95 <= vals['dmm_v'] <= 3.25 and
             abs(vals['dmm_v']-LEVELS[j])<.012 and
             abs(vals['psu_v']-LEVELS[j])<.010,
             'Analog DMM or PSU absolute voltage point inconsistent')
        need(.067<vals['kel_a']<.087 and abs(vals['kel_a']-vals['dmm_v']/40)<.003,
             'KEL current incompatible with CR40 load')
        need(39.7<vals['kel_effective_ohm']<40.4,
             'KEL effective resistance outside observed CR40 window')
        need(abs(vals['psu_a']-vals['kel_a'])<.0025 and
             abs(vals['psu_v']-vals['dmm_v'])<.006 and
             abs(vals['kel_v']-vals['dmm_v'])<.004,
             'Independent instrument agreement outside review envelope')
        need(abs(vals['psu_meas_w']-vals['psu_v']*vals['psu_a'])<.0005 and
             abs(vals['kel_w']-vals['kel_v']*vals['kel_a'])<.0005 and
             abs(vals['psu_meas_w']-vals['kel_w'])<.007 and
             0<vals['psu_meas_w']<.6 and 0<vals['kel_w']<.6,
             'PSU/KEL power disagreement or cap exceeded')
        ts=p.get('timestamps_monotonic_s') or {}
        times_pt=[number(ts.get(k)) for k in ('psu','kel','dmm','power')]
        need(times_pt==sorted(times_pt) and 0 < times_pt[-1]-times_pt[0]<4.0 and
             times_pt[-1]<=number(p['monotonic_s']) and
             times_pt[0]>number(rows[start]['monotonic_s']),
             'Measurement timestamps not bounded/order incorrect')
        need(abs(source_read(rows,'read','STAT:OPER:INST:ISUM1:COND?',start,idx)-9)<.01
             and abs(source_read(rows,'read','STAT:QUES:INST:ISUM1:COND?',start,idx)-1)<.01,
             'Physical CH1 CV status inconsistent')
        samples.append(vals)
    need(len([r for i,r in kel if r['command']==':MEAS:CURR?'])==5 and
         len([r for i,r in dmm if r['command']==':READ?'])==5,
         'Extra or missing external meter samples')
    for j,(i,record) in enumerate(steps):
        need(record.get('index')==j+1 and record.get('direction')==DIRECTIONS[j].split(':')[1].lower()
             and abs(number(record.get('readback'))-LEVELS[j+1])<.00049,
             'Misattributed or incorrect per-step digital event')
    segments=[]
    for j in range(4):
        sign=1 if j<2 else -1
        du=sign*(samples[j+1]['dmm_v']-samples[j]['dmm_v'])
        di=sign*(samples[j+1]['kel_a']-samples[j]['kel_a'])
        need(.090<=du<=.110 and .0015<=di<=.0035,
             'Incorrect physical voltage/current segment')
        segments.append((du,di))
    characterization=unique(rows,'iv_characterization')
    need(points[-1][0]<rows.index(characterization)<off[0] and
         len(characterization.get('segments',[]))==4,'Incomplete I-V characterization')
    for j,r in enumerate(characterization['segments']):
        need(r.get('index')==j+1 and r.get('direction')==('up' if j<2 else 'down')
             and abs(number(r.get('signed_dmm_delta_v'))-segments[j][0])<1e-8
             and abs(number(r.get('signed_kel_delta_a'))-segments[j][1])<1e-8,
             'Saved I-V segment does not match raw physical observations')
    s=(samples[2]['kel_a']-samples[0]['kel_a'])/(samples[2]['dmm_v']-samples[0]['dmm_v'])
    need(.018<s<.032 and abs(number(characterization['measured_conductance_s'])-s)<1e-9
         and abs(number(characterization['equivalent_ohms'])-1/s)<1e-7,
         'Calculated characteristic slope does not match instrument samples')
    return_v=abs(samples[-1]['dmm_v']-samples[0]['dmm_v'])
    return_i=abs(samples[-1]['kel_a']-samples[0]['kel_a'])
    need(return_v<.002 and return_i<.0003 and
         abs(samples[1]['dmm_v']-samples[3]['dmm_v'])<.002 and
         abs(number(characterization['dmm_return_error_v'])-return_v)<1e-9 and
         abs(number(characterization['kel_return_error_a'])-return_i)<1e-9,
         'Physical return/repeatability wrong')
    duration=number(characterization.get('powered_duration_s'))
    need(0<duration<300 and abs(number(characterization.get('energy_upper_bound_j'))-duration*.6)<1e-6,
         'Powered elapsed time/energy upper bound metadata wrong')
    return {'status':'REVIEWABLE_PASS',
            'scope':'R4.5-F-A CH1 CV five-point CR40 steady-state I-V reference (not calibration/transients)',
            **{k:v for k,v in trace.items() if k!='physical_trace'},
            'source_commit':sw['sha'],'source_dirty':sw.get('dirty'),
            'config_sha256':initial['config_sha256'],
            'dmm_v':[x['dmm_v'] for x in samples],
            'kel_a':[x['kel_a'] for x in samples],
            'segment_dmm_mv':[round(x[0]*1000,3) for x in segments],
            'segment_kel_ma':[round(x[1]*1000,3) for x in segments],
            'kel_effective_ohms':[round(x['kel_effective_ohm'],4) for x in samples],
            'two_point_incremental_ohms':1/s,
            'dmm_return_mv':return_v*1000,
            'kel_return_ma':return_i*1000,
            'max_kel_power_w':max(x['kel_w'] for x in samples),
            'powered_duration_s':duration}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('events',type=Path)
    ap.add_argument('--expected-sha256',required=True)
    args=ap.parse_args(argv)
    try:
        data=args.events.read_bytes()
        sha=hashlib.sha256(data).hexdigest()
        need(sha.lower()==args.expected_sha256.lower(),'Original JSONL SHA-256 mismatch')
        rows=[json.loads(line) for line in data.decode('utf-8').splitlines()]
        result=audit(rows)
        result['evidence_sha256']=sha
    except (AuditError,OSError,ValueError,TypeError,KeyError,UnicodeError,
            AttributeError,OverflowError,ZeroDivisionError) as exc:
        print(f'R4.5-F-A REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-A RESISTIVE I-V HARDWARE REVIEWABLE PASS (settled five-point CR40)')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
