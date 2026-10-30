#!/usr/bin/env python3
"""Strict read-only review of R4.5-E-C five-point powered CV/CC evidence.

No instrument I/O. Requires original JSONL SHA-256. Confirms steady-state
analog physics and wire ordering; cannot certify transient behavior/calibration.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path

from audit_r4_5_powered_step_evidence import (AuditError, need, number, one,
    matching_pairs)

DIRECTIONS = ('up', 'up', 'down', 'down')
OFFSETS = (0, 1, 2, 1, 0)
POINTS = {'cv': ('voltage', 'CV', 0.010, 40.0, 0.150),
          'cc': ('current', 'CC', 0.005, 11.0, 0.100)}
DMM_WRITES = ('*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
              ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
              ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF')
KEL_MEAS = {'kel_v': ':MEAS:VOLT?', 'kel_a': ':MEAS:CURR?',
            'kel_w': ':MEAS:POW?'}
PSU_MEAS = {'psu_v': 'MEAS:VOLT?', 'psu_a': 'MEAS:CURR?',
            'psu_meas_w': 'MEAS:POW?'}


def exact_one(records, event):
    return one([x for x in records if x.get('event') == event], event)


def last_read(events, kind, cmd, until):
    matches = [x for x in events[:until] if x.get('event') == kind
               and x.get('command') == cmd]
    need(matches, f'Missing underlying {kind} {cmd}')
    return number(matches[-1]['response'].removesuffix('V').removesuffix('A').removesuffix('W'))


def verify_wire(records, mode):
    """Physical trace drives ordering; log's high-level write may precede REM."""
    traces = [x['trace'] for x in records if x.get('event') == 'driver_trace']
    stage_q = [x for x in records if x.get('event') == 'query']
    stage_r = [x for x in records if x.get('event') == 'read']
    stage_w = [x for x in records if x.get('event') == 'write']
    queries, replies, writes = [], [], []
    pending = None
    selected = 1
    outputs = {1: False, 2: False, 3: False}
    on_seen = False
    off_after = Counter()
    on_reads = Counter()
    moves = []
    state = []
    shutdown_idx = None
    safe = {'SYST:REM','SYST:LOC','OUTP 0','OUTP:SER 0','OUTP:PARALLEL 0',
            'OUTP:TRAC 0','OUTP:TIM:STAT 0','VOLT 0','CURR 0',
            'VOLT:LIM 3.50','VOLT:LIM 1.50','VOLT:LIM:STAT 1',
            'VOLT 1.00','VOLT 3.30','CURR 0.100','CURR 0.150',
            'CHAN:OUTP 1','VOLT:STEP 0.010','CURR:STEP 0.005',
            'VOLT:UP','VOLT:DOWN','CURR:UP','CURR:DOWN',
            'INST:NSEL 1','INST:NSEL 2','INST:NSEL 3'}
    for index, tr in enumerate(traces):
        typ, cmd = tr.get('event'), tr.get('command')
        need(isinstance(cmd, str) and bool(cmd), 'Invalid physical SCPI command')
        if typ == 'query':
            need(pending is None, 'Overlapping PSU query')
            pending = cmd
            queries.append(cmd)
        elif typ == 'reply':
            need(pending == cmd and isinstance(tr.get('response'),str),
                 'Unmatched or malformed PSU reply')
            pending = None
            replies.append(tr)
            if cmd == 'CHAN:OUTP?':
                need(tr['response'] == ('1' if outputs[selected] else '0'),
                     f'CH{selected} ON/OFF readback conflicts with wire actions')
                if outputs[selected]: on_reads[selected] += 1
                if on_seen and shutdown_idx is not None and index > shutdown_idx:
                    if not outputs[selected]: off_after[selected] += 1
            if cmd in ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?'):
                need(tr['response'] == '0', 'Non-independent topology')
        elif typ == 'write':
            need(pending is None and cmd in safe, 'Unapproved/interleaved PSU write')
            writes.append(cmd)
            if cmd.startswith('INST:NSEL '): selected = int(cmd[-1])
            elif cmd == 'CHAN:OUTP 1':
                need(selected == 1 and not on_seen, 'Repeated/wrong output enable')
                outputs[1] = on_seen = True
                state.append(('enable',index))
            elif cmd == 'OUTP 0':
                outputs = {1: False, 2: False, 3: False}
                if on_seen: shutdown_idx = index
                state.append(('off',index))
            if cmd.startswith(('VOLT:STEP ', 'CURR:STEP ')) or cmd in (
                    'VOLT:UP','VOLT:DOWN','CURR:UP','CURR:DOWN'):
                need(selected == 1, 'STEP on a channel other than CH1')
                moves.append((cmd, index, bool(outputs[1])))
        else:
            raise AuditError('Unknown physical PSU trace type')
    need(pending is None and len(queries) == len(replies), 'Unanswered physical query')
    need(Counter(queries) == Counter(x['command'] for x in stage_q),
         'Stage queries differ from physical queries')
    need(Counter(writes) == Counter(x['command'] for x in stage_w),
         'Stage writes differ from physical writes')
    need(len(stage_r) == len(replies) and all(
         s['command'] == t['command'] and s['response'] == t['response']
         for s,t in zip(stage_r,replies)), 'Stage replies differ from physical replies')
    need(on_seen and on_reads[1] >= 5 and not on_reads[2] and not on_reads[3],
         'Only CH1 should have powered ON confirmations')
    need(all(off_after[k] >= 1 for k in (1,2,3)) and not any(outputs.values()),
         'Independent final OFF readbacks missing')
    need(writes.count('CHAN:OUTP 1') == 1 and writes.count('SYST:LOC') == 1
         and writes[-1] == 'SYST:LOC', 'Repeated enable or unsafe Local order')
    prefix = 'VOLT' if mode == 'cv' else 'CURR'
    step = f'{prefix}:STEP {POINTS[mode][2]:.3f}'
    need([m[0] for m in moves] == [step, f'{prefix}:UP', f'{prefix}:UP',
                                      f'{prefix}:DOWN', f'{prefix}:DOWN'],
         'Physical STEP command order or count differs from approved sequence')
    need(not moves[0][2] and all(m[2] for m in moves[1:]),
         'STEP register must be configured OFF, moves must be powered')
    en_wire = one([i for typ,i in state if typ == 'enable'], 'wire output enable')
    need(moves[0][1] < en_wire < moves[1][1] and
         all(m[1] < shutdown_idx for m in moves[1:]),
         'STEP configured after enable or mutation after shutdown')
    return {'scpi_queries':len(queries), 'scpi_writes':len(writes),
            'scpi_error_polls':sum(x['command']=='SYST:ERR?' for x in stage_r),
            'off_readbacks':sum(x['command']=='CHAN:OUTP?' and x['response']=='0'
                                for x in stage_r), 'moves':moves}


def review_measurements(records, mode, snapshots, directions, step_indices):
    p = POINTS[mode]
    amps, volts = [], []
    for n,(index,s) in enumerate(snapshots):
        need(s.get('label') == f'point_{n}' and s.get('mode') == p[1]
             and s.get('offset_steps') == OFFSETS[n], 'Incorrect snapshot labels/offsets')
        vals = {k:number(s.get(k)) for k in ('psu_v','psu_a','psu_derived_w',
            'psu_meas_w','kel_v','kel_a','kel_w','dmm_v')}
        for key,cmd in PSU_MEAS.items():
            need(abs(vals[key]-last_read(records,'read',cmd,index)) < 1e-6,
                 'Snapshot PSU value differs from raw SCPI response')
        for key,cmd in KEL_MEAS.items():
            need(abs(vals[key]-last_read(records,'kel103_read',cmd,index)) < 1e-5,
                 'Snapshot KEL value differs from raw response')
        need(abs(vals['dmm_v']-last_read(records,'dmm7510_read',':READ?',index)) < 1e-7,
             'Snapshot DMM voltage differs from raw response')
        t = s.get('timestamps_monotonic_s') or {}
        ts = [number(t.get(k)) for k in ('psu_s','kel_s','dmm_s','power_s')]
        need(ts == sorted(ts) and ts[-1] - ts[0] < 4.0,
             'Bad sequential three-instrument measurement times')
        need(ts[-1] <= number(s['monotonic_s']) and
             (n == 0 or ts[0] > number(snapshots[n-1][1]['monotonic_s'])),
             'Measurement timestamps overlap or precede prior snapshot')
        if n:
            need(0 < ts[0] - number(records[step_indices[n-1]]['monotonic_s']) < 15,
                 'Unbounded or non-monotonic post-STEP first sample')
        need(abs(vals['psu_derived_w']-vals['psu_v']*vals['psu_a']) < 1e-7,
             'Recorded derived PSU power inconsistent')
        need(abs(vals['psu_meas_w']-vals['psu_derived_w']) < .002,
             'PSU measured power inconsistent with U x I')
        need(abs(vals['kel_w']-vals['kel_v']*vals['kel_a']) < .002,
             'KEL power inconsistent with U x I')
        need(0 < vals['psu_meas_w'] < .6 and 0 < vals['kel_w'] < .6,
             'Power envelope exceeded')
        need(abs(vals['psu_a']-vals['kel_a']) < .003,
             'PSU/KEL current disagreement beyond observed envelope')
        need(abs(vals['psu_v']-vals['dmm_v']) < .012 and
             abs(vals['kel_v']-vals['dmm_v']) < .010,
             'PSU/KEL/DMM voltage disagreement beyond observed envelope')
        if mode == 'cv':
            target = 3.30 + .010*OFFSETS[n]
            need(abs(vals['dmm_v']-target) < .012 and
                 abs(vals['psu_v']-target) < .008 and
                 .07 < vals['kel_a'] < .10, 'CV absolute voltage/current point wrong')
        else:
            target = .100 + .005*OFFSETS[n]
            need(abs(vals['kel_a']-target) < .003 and
                 abs(vals['psu_a']-target) < .003 and
                 abs(vals['dmm_v']-11*target) < .035, 'CC absolute point wrong')
        amps.append(vals['kel_a']); volts.append(vals['dmm_v'])
    signed=[]
    for n in range(1,5):
        sign = 1 if directions[n-1]=='up' else -1
        dv = sign*(volts[n]-volts[n-1])
        di = sign*(amps[n]-amps[n-1])
        signed.append(dv)
        if mode == 'cv':
            need(.006 <= dv <= .014, f'CV DMM STEP {n} differs from 10mV')
            need(-.0006 <= di <= .0012, 'CV load current response implausible')
        else:
            need(.0035 <= di <= .0065 and .035 <= dv <= .075,
                 f'CC real 5mA STEP {n} missing')
            need(abs(dv-11*di) < .010, 'CC DMM voltage / KEL current inconsistency')
    need(abs(volts[0]-volts[4]) <= (.006 if mode=='cv' else .035),
         'Final physical DMM return failed')
    need(abs(amps[0]-amps[4]) <= (.002 if mode=='cc' else .001),
         'Final physical KEL current return failed')
    need(abs(volts[1]-volts[3]) <= (.003 if mode=='cv' else .025),
         'Repeated intermediate point shows excessive hysteresis')
    return {'dmm_v':volts, 'kel_a':amps, 'signed_dmm_segments_v':signed,
            'dmm_return_error_v':abs(volts[0]-volts[4]),
            'kel_return_error_a':abs(amps[0]-amps[4]),
            'dmm_repeated_midpoint_delta_v':abs(volts[1]-volts[3])}


def audit(records):
    need(bool(records) and all(isinstance(x,dict) and x.get('test_id')=='R45ESERIES'
                               for x in records), 'Invalid/mixed JSONL records')
    ts=[number(x.get('monotonic_s')) for x in records]
    need(all(x<=y for x,y in zip(ts,ts[1:])), 'Nonmonotonic JSONL timestamps')
    start=exact_one(records,'start')
    mode=start.get('mode')
    need(mode in POINTS and start.get('variant')=='CH1_FIVE_POWERED_SNAPSHOTS',
         'Wrong stage/mode')
    kind,regulation,step,ohms,limit=POINTS[mode]
    target=start.get('point') or {}
    need(target.get('kind')==kind and target.get('mode')==regulation and
         all(abs(number(target.get(k))-v)<1e-9 for k,v in (
             ('step',step),('expected_delta',step),('cr',ohms),('limit',limit),('voltage',3.3)))
         and start.get('directions') == list(DIRECTIONS), 'Wrong preauthorized series plan')
    sw=start.get('software') or {}
    # A docs-only review commit changes HEAD, but not the qualified runner/stage.
    # Require exact executable hashes; preserve (do not silently bless) HEAD/dirty.
    need(isinstance(sw.get('sha'),str) and bool(re.fullmatch('[0-9a-f]{40}',sw['sha']))
         and sw.get('r45ec_runner_sha256')=='3ca32484a5cd4a753ff1e08073c126650f56477bc4e77c4c676103727f47d87e'
         and sw.get('r45ec_stage_sha256')=='d9e18458a594e3bfb812a5500ce3bf3a7dedd51dcf4eac2f4fec59491aa5aa05'
         and isinstance(sw.get('source_sha256'),str) and len(sw['source_sha256'])==64,
         'Unverified source or changed powered runner/stage provenance')
    need(isinstance(start.get('config_sha256'),str) and len(start['config_sha256'])==64,
         'Missing config SHA')
    pre=exact_one(records,'pre_open_approvals')
    need(pre.get('gates')==['BENCH','INITIAL'] and
         pre.get('config_sha256')==start['config_sha256'], 'Missing bench approval')
    gates=[(i,x['gate']) for i,x in enumerate(records) if x['event']=='operator_confirmation']
    need([g for i,g in gates]==['POWER','FINAL'], 'Missing/misordered gates')
    outcome=exact_one(records,'outcome')
    need(outcome is records[-1] and outcome.get('outcome')=='PASS' and
         outcome.get('mode')==mode and outcome.get('physical_off_confirmed') is True
         and outcome.get('status')=='HW NOT_REVIEWED', 'Failed or incomplete stage')
    need(not any(x['event'] in ('stage_error','io_error','invalid_scpi_reply',
              'shutdown_error_queue') for x in records), 'Link/stage shutdown error')
    ids=[x['response'] for x in records if x['event']=='read' and x.get('command')=='*IDN?']
    need(len(ids)==2 and all('Keithley' in s and '2231A-30-3' in s and
         s.rsplit(',',1)[-1].strip() in ('1.04-0.01','1.04-1.04') for s in ids),
         'Unexpected PSU identity')
    wire=verify_wire(records,mode)
    need(wire['scpi_error_polls']>=300 and all(x.get('response','').split(',',1)[0].strip()=='0'
         for x in records if x['event']=='read' and x.get('command')=='SYST:ERR?'),
         'Nonzero PSU SCPI error')
    reads_kel=matching_pairs(records,'kel103_query','kel103_read')
    reads_dmm=matching_pairs(records,'dmm7510_query','dmm7510_read')
    kel_w=[(i,x['command']) for i,x in enumerate(records) if x['event']=='kel103_write']
    dmm_w=[(i,x['command']) for i,x in enumerate(records) if x['event']=='dmm7510_write']
    need([c for i,c in kel_w]==[':FUNC CR',f':RES {ohms:g}OHM',':INP ON',':INP OFF'],
         'KEL setpoint/input order changed')
    need([c for i,c in dmm_w]==list(DMM_WRITES) and all(i<gates[0][0] for i,c in dmm_w),
         'DMM reset/configuration during powered series')
    dc=exact_one(records,'dmm7510_configuration')
    need(dc.get('outcome')=='PASS' and dc.get('function')=='DC voltage' and
         dc.get('terminals')=='front' and dc.get('range_v')==10 and dc.get('nplc')==1,
         'DMM settings not verified')
    need(exact_one(records,'dmm7510_command_set').get('language')=='SCPI',
         'DMM command set wrong')
    need(any('MODEL DMM7510' in x['response'] for i,x in reads_dmm
             if x['command']=='*IDN?'), 'Wrong DMM model')
    need(all(x['response'].split(',',1)[0]=='0' for i,x in reads_dmm
             if x['command']==':SYST:ERR?'), 'DMM error queue nonzero')
    need(all(x['response']=='CR' for i,x in reads_kel if x['command']==':FUNC?') and
         all(abs(number(x['response'].removesuffix('OHM'))-ohms)<.001
             for i,x in reads_kel if x['command']==':RES?'), 'KEL CR wrong')
    kel_states=[x['response'] for i,x in reads_kel if x['command']==':INP?']
    need(kel_states and kel_states[0]=='OFF' and kel_states[-2:]==['OFF','OFF']
         and 'ON' in kel_states, 'KEL digital input not confirmed OFF')
    need(len([x for i,x in reads_dmm if x['command']==':READ?'])==5,
         'DMM five physical measurements missing')
    commands=[(i,x['command']) for i,x in enumerate(records) if x['event']=='write']
    en=one([i for i,c in commands if c=='CHAN:OUTP 1'],'one CH1 ON')
    off=[i for i,c in commands if c=='OUTP 0' and i>en]
    loc=one([i for i,c in commands if c=='SYST:LOC'],'SYST:LOC')
    need(off and gates[0][0]<kel_w[2][0]<en<off[0]<kel_w[-1][0]<loc<gates[-1][0],
         'Power/independent shutdown gate order incorrect')
    move=f'{"VOLT" if mode=="cv" else "CURR"}'
    write_indices=[i for i,c in commands if c in (move+':UP',move+':DOWN')]
    digital=[(i,x) for i,x in enumerate(records) if x['event']=='powered_series_digital']
    segments=[(i,x) for i,x in enumerate(records) if x['event']=='powered_series_segment']
    snaps=[(i,x) for i,x in enumerate(records) if x['event']=='powered_step_snapshot']
    need(len(write_indices)==len(digital)==len(segments)==4 and len(snaps)==5,
         'Missing or repeated STEP, digital, segment or measurement')
    need(en<snaps[0][0]<write_indices[0] and
         all(write_indices[j]<digital[j][0]<snaps[j+1][0]<segments[j][0]<
             (write_indices[j+1] if j<3 else off[0]) for j in range(4)),
         'Interleaved powered steps, measurements or shutdown')
    need(all(d.get('direction')==DIRECTIONS[j] and d.get('mode')==mode and
        d.get('kind')==kind and d.get('index')==j+1 and
        d.get('expected_offset_steps')==OFFSETS[j+1] and
        abs(number(d.get('readback'))-(3.3+OFFSETS[j+1]*step
            if mode=='cv' else limit+OFFSETS[j+1]*step)) < .00049
        for j,(_,d) in enumerate(digital)), 'Digital per-step metadata incorrect')
    need(all(s.get('index')==j+1 and s.get('direction')==DIRECTIONS[j]
             for j,(_,s) in enumerate(segments)), 'Per-step segment identity mismatch')
    ret=exact_one(records,'powered_series_return')
    need(snaps[-1][0]<records.index(ret)<off[0] and ret.get('mode')==mode,
         'Final real return event missing')
    measured=review_measurements(records,mode,snaps,DIRECTIONS,write_indices)
    need(abs(number(ret['dmm_return_error_v'])-measured['dmm_return_error_v']) < 1e-8 and
         abs(number(ret['kel_return_error_a'])-measured['kel_return_error_a']) < 1e-8,
         'Return metadata differs from physical snapshots')
    for j,(_,s) in enumerate(segments):
        need(abs(number(s['dmm_signed_delta_v'])-measured['signed_dmm_segments_v'][j])<1e-8,
             'Segment delta metadata does not match DMM evidence')
    return {'status':'REVIEWABLE_PASS','scope':f'CH1 {mode.upper()} five powered steady-state points; not transient or calibration',
            'mode':mode,**{k:wire[k] for k in ('scpi_queries','scpi_writes','scpi_error_polls','off_readbacks')},
            'firmware_observations':sorted({s.rsplit(',',1)[-1].strip() for s in ids}),
            'source_commit':sw['sha'],'source_dirty':sw.get('dirty'),
            'config_sha256':start['config_sha256'], 'physics':measured}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    parser.add_argument('--expected-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        data=args.events.read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        need(digest.lower()==args.expected_sha256.lower(),'Original events SHA-256 mismatch')
        records=[json.loads(line) for line in data.decode('utf-8').splitlines()]
        result=audit(records)
        result['evidence_sha256']=digest
    except (AuditError,OSError,ValueError,TypeError,KeyError,UnicodeError,
            AttributeError,OverflowError,ZeroDivisionError) as exc:
        print(f'R4.5-E-C REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-E-C SERIES HARDWARE REVIEWABLE PASS (five steady-state points)')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
