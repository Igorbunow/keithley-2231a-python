"""Independent offline audit of one R5-D2 powered STEP original JSONL.

No instrument I/O. Review is a candidate, not an automatic hardware qualification.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

STAGE = 'R5D2POWEREDSTEP'
DIRECTIONS = ('up', 'up', 'down', 'down')
LIMITS = {'cv': ('VOLT', .010, .150, 40.0),
          'cc': ('CURR', .005, .100, 11.0)}


class AuditError(ValueError):
    pass


def need(test, message):
    if not test:
        raise AuditError(message)


def number(value, name):
    try:
        n = float(value)
        need(type(value) in (int, float) and math.isfinite(n), name)
        return n
    except (TypeError, ValueError, OverflowError) as exc:
        raise AuditError(name) from exc


def audit(records):
    need(isinstance(records, list) and len(records) >= 80, 'Missing/short evidence')
    need(all(type(r) is dict and r.get('test_id') == STAGE and
             type(r.get('monotonic_s')) in (float, int) for r in records),
         'Mixed test ID or unclocked evidence')
    times = [number(r['monotonic_s'], 'Evidence timestamp') for r in records]
    need(all(x <= y for x, y in zip(times, times[1:])), 'Non-monotonic evidence timestamps')

    def one(event):
        items = [(i,r) for i,r in enumerate(records) if r.get('event') == event]
        need(len(items) == 1, f'Expected exactly one {event}')
        return items[0]

    si, start = one('start')
    pi, approvals = one('pre_open_approvals')
    _, identity = one('psu_identity')
    oi, outcome = one('outcome')
    need(si == 0 and pi == 1 and oi == len(records)-1, 'Invalid evidence ordering')
    channel, mode = start.get('channel'), start.get('mode')
    need(type(channel) is int and channel in (1,2,3) and mode in LIMITS,
         'Wrong channel/mode')
    prefix, step, initial_current, cr = LIMITS[mode]
    need(start.get('phase') == 'POWERED_STEP_FOUR' and
         start.get('directions') == list(DIRECTIONS) and
         start.get('point',{}).get('step') == step and
         start.get('point',{}).get('cr') == cr and
         start.get('point',{}).get('limit') == initial_current,
         'Wrong qualification envelope')
    sha = start.get('config_sha256')
    need(isinstance(sha,str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha),
         'Missing config SHA-256')
    need(approvals.get('approved_before_evidence_open') is True and
         approvals.get('gates') == ['BENCH', 'INITIAL'] and
         approvals.get('config_sha256') == sha, 'Missing pre-open operator gates')
    software = start.get('software',{})
    for key in ('r5d2_runner_sha256', 'r5d2_stage_sha256', 'source_sha256'):
        digest = software.get(key)
        need(isinstance(digest,str) and len(digest) == 64 and
             all(c in '0123456789abcdef' for c in digest),
             'Missing runner/stage/source provenance: ' + key)
    need(identity.get('model') == '2231A-30-3' and
         identity.get('firmware') in ('1.04-1.04', '1.04-0.01'),
         'Unreviewed PSU model/firmware; investigate before qualification')
    need(outcome.get('phase') == 'POWERED_STEP_FOUR' and
         outcome.get('channel') == channel and outcome.get('mode') == mode and
         outcome.get('outcome') == 'PASS' and
         outcome.get('digital_off_confirmed') is True and
         outcome.get('physical_off_confirmed') is True and
         outcome.get('hardware_review') == 'PENDING', 'PASS/OFF gates missing')
    need(not [r for r in records if r.get('event') in (
        'stage_error', 'io_error', 'invalid_scpi_reply', 'kel103_io_error',
        'kel103_invalid_reply', 'cleanup_error', 'shutdown_error_queue')],
        'SCPI/load/evidence fault in original log')
    gates = [(i,r.get('gate')) for i,r in enumerate(records)
             if r.get('event') == 'operator_confirmation']
    need([name for _,name in gates] == ['ENABLE','FINAL'] and
         gates[0][0] < gates[1][0], 'Operator power/final gate missing')
    need(gates[0][1] == 'ENABLE' and gates[1][1] == 'FINAL', 'Gate mismatch')
    dmm_config = [r for r in records if r.get('event') == 'dmm7510_configuration']
    need(len(dmm_config)==1 and dmm_config[0].get('function')=='DC voltage' and
         dmm_config[0].get('terminals')=='front' and
         dmm_config[0].get('range_v')==10 and
         dmm_config[0].get('outcome')=='PASS', 'DMM7510 DCV setup unconfirmed')
    need(len([r for r in records if r.get('event') == 'dmm7510_read' and
              r.get('command') == ':READ?']) == 5,
         'Five independent DMM live readings missing')
    for command in (':MEAS:VOLT?', ':MEAS:CURR?', ':MEAS:POW?'):
        need(len([r for r in records if r.get('event')=='kel103_read'
                  and r.get('command')==command]) == 5,
             f'Five KEL103 {command} observations missing')
    need(not [r for r in records if r.get('event')=='kel103_read'
             and r.get('command')==':INP?' and r.get('response') not in ('ON', 'OFF')],
         'Malformed KEL input response')

    q = [r for r in records if r.get('event')=='query']
    reads = [r for r in records if r.get('event')=='read']
    writes = [r for r in records if r.get('event')=='write']
    traces = [r['trace'] for r in records if r.get('event')=='driver_trace']
    # The driver can perform a nested SYST:REM / error check before an outer
    # query is physically sent; submitted-query and read events can differ in
    # order. Physical trace ordering is authoritative, counts must match.
    need(len(q)>100 and Counter(r['command'] for r in q)==
         Counter(r['command'] for r in reads),
         'Original SCPI queries/readback count mismatch')
    idn = [r['response'] for r in reads if r['command']=='*IDN?']
    need(bool(idn) and all('Keithley' in r and '2231A-30-3' in r and
         r.split(',')[-1].strip() in ('1.04-1.04','1.04-0.01')
         for r in idn) and identity['firmware'] in
         {r.split(',')[-1].strip() for r in idn},
         'Identity readback/model/firmware missing or inconsistent')
    trace_ops = [(r.get('event'),r.get('command')) for r in traces]
    need([c for e,c in trace_ops if e=='write']==[r['command'] for r in writes]
         and Counter(c for e,c in trace_ops if e=='query')==
             Counter(r['command'] for r in q)
         and [c for e,c in trace_ops if e=='reply']==[r['command'] for r in reads],
         'Physical SCPI trace diverges from stage evidence')
    pending = None
    for r in traces:
        event, command = r.get('event'),r.get('command')
        need(event in ('query','reply','write'), 'Unknown physical trace event')
        if event=='query':
            need(pending is None, 'Overlapping physical queries')
            pending = command
        elif event=='reply':
            need(pending==command, 'Unpaired physical query response')
            pending = None
        else:
            need(pending is None, 'Physical write before query response')
    need(pending is None, 'Missing last physical SCPI reply')
    replies = [t for t in traces if t['event']=='reply']
    for stage_reply, physical in zip(reads,replies):
        a,b=stage_reply.get('response'),physical.get('response')
        need(a==b or (stage_reply['command']=='*IDN?' and isinstance(a,str)
             and isinstance(b,str) and a.split(',')[0:2]==b.split(',')[0:2]
             and a.split(',')[-1]==b.split(',')[-1] and
             '[REDACTED]' in a and '[REDACTED]' in b),
             'Physical SCPI response differs from stage-recorded response')
    need(all(r.get('response','').split(',',1)[0].strip()=='0' and
             len(r.get('response','').split(',',1))==2 and
             bool(r['response'].split(',',1)[1].strip()) for r in reads
             if r.get('command') == 'SYST:ERR?'), 'Nonzero/malformed PSU SCPI errors')
    need(all(r.get('response','').split(',',1)[0].strip()=='0' for r in records
             if r.get('event')=='dmm7510_read' and r.get('command')==':SYST:ERR?'),
         'Nonzero DMM7510 SCPI errors')
    wire = [c for e,c in trace_ops if e in ('write','query')]
    need(wire and wire[-1]=='SYST:LOC' and wire.count('SYST:LOC')==1,
         'Unverified LOCAL/shutdown end of SCPI log')
    need(not any(c in ('OUTP 1', '*TRG', '*RST', '*RCL', '*SAV', '*CLS') or ':TRIG' in c
                     for c in wire), 'Forbidden command in powered STEP wire')
    enable = [(i,r) for i,r in enumerate(records)
              if r.get('event')=='write' and r.get('command')=='CHAN:OUTP 1']
    off = [(i,r) for i,r in enumerate(records)
           if r.get('event')=='write' and r.get('command')=='OUTP 0']
    need(len(enable)==1 and len(off)>=1 and
         gates[0][0] < enable[0][0] < off[-1][0] < gates[1][0],
         'Invalid single enable/shutdown ordering')
    selected = None
    step_writes = []
    on_writes = []
    for r in records:
        if r.get('event')!='write': continue
        cmd = r.get('command','')
        if cmd.startswith('INST:NSEL '): selected=int(cmd[-1])
        if cmd=='CHAN:OUTP 1': on_writes.append((selected,cmd))
        if cmd.startswith(('VOLT:STEP ', 'CURR:STEP ')) or cmd in (
                'VOLT:UP','VOLT:DOWN','CURR:UP','CURR:DOWN'):
            step_writes.append((selected,cmd))
    need(on_writes == [(channel,'CHAN:OUTP 1')], 'Wrong energized channel')
    need(step_writes == [(channel,f'{prefix}:STEP {step:.3f}')] +
         [(channel,f'{prefix}:{d.upper()}') for d in DIRECTIONS],
         'Wrong selected channel/STEP operation order')
    mutations = [i for i,r in enumerate(records) if r.get('event')=='write'
                 and r.get('command') in [f'{prefix}:{d.upper()}' for d in DIRECTIONS]]
    need(len(mutations)==4 and gates[0][0]<mutations[0]<off[-1][0],
         'Wrong powered mutation ordering')
    register_reads = [r for r in reads if r['command']==f'{prefix}:STEP?']
    need(len(register_reads)>=5 and all(abs(float(r['response'])-step)<.00049
         for r in register_reads), 'Hardware STEP register readback missing/drifted')
    # Shutdown must be based on physical per-channel OFF queries, not simply an OFF write.
    last_off_idx = off[-1][0]
    off_reads = [(i,r) for i,r in enumerate(records) if i>last_off_idx and
                 r.get('event')=='read' and r.get('command')=='CHAN:OUTP?']
    need(len(off_reads)>=3 and all(str(r['response']).strip().upper() in ('0','OFF')
         for _,r in off_reads), 'Three-channel OFF readback missing')
    selected_off = None
    verified_off_channels = set()
    for record in records[last_off_idx+1:]:
        if record.get('event')=='write' and record.get('command','').startswith('INST:NSEL '):
            selected_off = int(record['command'][-1])
        if record.get('event')=='read' and record.get('command')=='CHAN:OUTP?':
            need(selected_off in (1,2,3), 'Unselected channel OFF readback')
            need(str(record['response']).strip().upper() in ('0','OFF'),
                 'Live channel after shutdown')
            verified_off_channels.add(selected_off)
    need(verified_off_channels == {1,2,3},
         'Independent OFF readback missing for at least one channel')
    snapshots = [(i,r) for i,r in enumerate(records) if r.get('event')=='powered_step_snapshot']
    digital = [(i,r) for i,r in enumerate(records) if r.get('event')=='powered_step_digital']
    deltas = [(i,r) for i,r in enumerate(records) if r.get('event')=='powered_step_delta']
    need(len(snapshots)==5 and len(digital)==4 and len(deltas)==4,
         'Missing/duplicate analog evidence')
    need(snapshots[0][0]>enable[0][0] and snapshots[-1][0]<off[-1][0],
         'Analog readings outside powered window')
    offsets = (0,1,2,1,0)
    dmm_observations=[r for r in records if r.get('event')=='dmm7510_read'
                      and r.get('command')==':READ?']
    kel_observations={name:[r for r in records if r.get('event')=='kel103_read'
                             and r.get('command')==cmd]
                      for name,cmd in (('kel_v',':MEAS:VOLT?'),
                                       ('kel_a',':MEAS:CURR?'),('kel_w',':MEAS:POW?'))}
    def equipment_number(reply, unit):
        text=str(reply).strip().upper()
        if text.endswith(unit): text=text[:-len(unit)].strip()
        try: result=float(text)
        except (ValueError,TypeError) as exc: raise AuditError('Invalid external measurement') from exc
        need(math.isfinite(result), 'Non-finite external measurement')
        return result
    for i,(position,snap) in enumerate(snapshots):
        need(snap.get('index')==i and snap.get('channel')==channel and
             snap.get('mode')==mode.upper() and snap.get('offset_steps')==offsets[i] and
             snap.get('synchronized') is False,
             'Corrupt analog snapshot metadata')
        pu,pi,ku,ki,kp,du,pw = [number(snap.get(key), key) for key in
                                  ('psu_v','psu_a','kel_v','kel_a','kel_w','dmm_v','psu_meas_w')]
        need(0 <= pu <=3.55 and 0 <=pi<=.165 and 0<=ku<=3.55 and
             0<=ki<=.165 and 0<=kp<=.60 and 0<=du<=3.55 and 0<=pw<=.60 and
             pu*pi<=.60 and abs(du-pu)<=.08 and abs(du-ku)<=.08 and
             abs(pi-ki)<=.030 and abs(kp-ku*ki)<=.045 and abs(pw-kp)<=.08,
             'Out-of-envelope PSU/KEL/DMM analog values')
        need(abs(equipment_number(dmm_observations[i].get('response'),'V')-du)<1e-6,
             'DMM raw read does not match analog snapshot')
        for name,unit in (('kel_v','V'),('kel_a','A'),('kel_w','W')):
            need(abs(equipment_number(kel_observations[name][i].get('response'),unit)-
                     number(snap[name],name)) < 1e-5,
                 'KEL raw reading does not match analog snapshot: '+name)
        if mode=='cv':
            need(abs(du-(3.30+offsets[i]*step))<=.040,
                 'Incorrect absolute CV STEP voltage')
        else:
            need(abs(ki-(initial_current+offsets[i]*step))<=.012 and
                 abs(du-cr*(initial_current+offsets[i]*step))<=.200,
                 'Incorrect CC STEP current/voltage')
        if i:
            direction = DIRECTIONS[i-1]
            measured_dv = du-number(snapshots[i-1][1]['dmm_v'],'prior DMM')
            measured_di = ki-number(snapshots[i-1][1]['kel_a'],'prior KEL')
            sign = 1 if direction=='up' else -1
            need(.003 <= measured_dv*sign <= .030 if mode=='cv'
                 else (.002<=measured_di*sign<=.020 and
                       .010<=measured_dv*sign<=.150),
                 'External analog STEP effect missing/reversed')
            dr = digital[i-1][1]
            delta = deltas[i-1][1]
            expected_setpoint = (3.30 if mode=='cv' else initial_current) + offsets[i]*step
            need(dr.get('channel')==channel and dr.get('mode')==mode and
                 dr.get('index')==i and dr.get('direction')==direction and
                 abs(number(dr.get('setpoint'),'digital setpoint')-expected_setpoint)<=.00049,
                 'Digital native STEP setpoint mismatch')
            need(delta.get('channel')==channel and delta.get('mode')==mode and
                 delta.get('index')==i and delta.get('direction')==direction and
                 abs(number(delta.get('dmm_delta_v'),'delta DMM')-measured_dv)<1e-8 and
                 abs(number(delta.get('kel_delta_a'),'delta KEL')-measured_di)<1e-8,
                 'Per-step analog delta record inconsistent')
            need(snapshots[i-1][0]<mutations[i-1]<digital[i-1][0]<position<deltas[i-1][0],
                 'Analog/digital per-step ordering corrupted')
            raw_setpoints = [r for r in records[mutations[i-1]+1:digital[i-1][0]]
                             if r.get('event')=='read' and r.get('command')==f'{prefix}?']
            need(raw_setpoints and
                 abs(float(raw_setpoints[-1]['response'])-expected_setpoint)<.00049,
                 'Native STEP physical register readback does not match digital record')
    return {'status':'REVIEWABLE_PASS_NOT_AUTOMATIC_QUALIFICATION',
            'channel':channel,'mode':mode,'snapshots':5,'mutations':4,
            'scpi_queries':len(q),'digital_off_confirmed':True,
            'physical_off_confirmed':True}


def read_and_audit(path):
    path = Path(path)
    raw = path.read_bytes()
    need(0 < len(raw) <= 64*1024*1024 and raw.endswith(b'\n'),
         'Empty/oversized/truncated JSONL')
    try:
        records = [json.loads(line) for line in raw.splitlines()]
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuditError('Invalid original JSONL') from exc
    return {**audit(records), 'original_sha256':hashlib.sha256(raw).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_jsonl', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        report = read_and_audit(args.original_jsonl)
        text = json.dumps(report, indent=2, ensure_ascii=False)+'\n'
        print(text)
        if args.output:
            with args.output.open('x', encoding='utf-8') as f:
                f.write(text)
        return 0
    except (AuditError,OSError) as exc:
        print(f'REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
