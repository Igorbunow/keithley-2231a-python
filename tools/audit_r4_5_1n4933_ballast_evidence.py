#!/usr/bin/env python3
"""Independent OFFLINE R45FE evidence review: reversed leads and direct ballast DCV.

Original JSONL SHA-256, dual PSU wire/stage evidence, DMM command/read pairs,
operator gates, bounded single-point arithmetic and independently verified OFF.
This is NOT a hardware run, calibration, or claim about Keithley current accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, need, number, one, matching_pairs
from audit_r4_5_1n4933_evidence import (
    EVENTS as FORWARD_EVENTS, load_original, close, audit as forward_audit,
)

EVENTS = FORWARD_EVENTS | {
    'stage_error', 'ballast_direct_raw_sample', 'ballast_direct_point',
    'off_state_ballast_voltage',
}
CODE_SHA = 'd9e053ba3d13adc30575b6f982b5d2dca9e62b73'
RUNNER_SHA = '155ca7230202db44bca298f15aa5c7107ca2dd0e3b851fdd5a43d2932c8ed023'
STAGE_SHA = '8e2a16160b42655ad596b3a31fe2a1a1abdb397ad9cff8323dd371177808bbcb'
PLAN_SHA = 'b795bbd949bf4d4865c9655e8dc59cd99015d7b291bf06932d66fc6e974717f3'
BENCH_SHA = 'e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720'
DMM_WRITES = ('*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
              ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
              ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF')
PSU_ALLOWED_WRITES = {
    'SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0', 'OUTP:PARALLEL 0',
    'OUTP:TRAC 0', 'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
    'VOLT:LIM 1.05', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
    'VOLT 0.90', 'VOLT 1.00', 'CURR 0.020', 'CURR 0.100',
    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3', 'CHAN:OUTP 1',
    'CHAN:OUTP 0',
}


def single(rows, kind):
    return one([r for r in rows if r.get('event') == kind], kind)


def check_wire(rows, *, successful, allowed_writes=None):
    """Validate the physical driver trace independently of runner-written point records."""
    if allowed_writes is None:
        allowed_writes = PSU_ALLOWED_WRITES
    trace = [r['trace'] for r in rows if r['event'] == 'driver_trace']
    need(len(trace) > 600, 'Missing/bounded physical PSU trace')
    commands = {'query': [], 'reply': [], 'write': []}
    pending = None
    selected, on_count, shutdown_t, power_t = 1, 0, None, None
    outputs = {1: False, 2: False, 3: False}
    after_off = Counter()
    for rec in trace:
        typ, cmd = rec.get('event'), rec.get('command')
        need(typ in commands and isinstance(cmd, str) and rec.get('profile_bypass') is False,
             'Invalid or bypassed physical driver trace')
        if typ == 'query':
            need(pending is None, 'Overlapping PSU queries')
            pending = cmd
        elif typ == 'reply':
            need(pending == cmd and isinstance(rec.get('response'), str), 'PSU reply mismatch')
            pending = None
            v = rec['response']
            if cmd == 'CHAN:OUTP?':
                need(v == str(int(outputs[selected])), 'Contradictory channel readback')
                if shutdown_t is not None and v == '0':
                    after_off[selected] += 1
            elif cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
                need(v == '0', 'Unexpected combined topology')
        elif typ == 'write':
            need(pending is None and cmd in allowed_writes, 'Unreviewed PSU write')
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1])
            elif cmd == 'CHAN:OUTP 1':
                need(selected == 1 and not on_count and not any(outputs.values()),
                     'Wrong or repeated CH1 enable')
                on_count += 1
                outputs[1] = True
                power_t = number(rec['monotonic_s'])
            elif cmd == 'OUTP 0':
                outputs = {1: False, 2: False, 3: False}
                if on_count and shutdown_t is None:
                    shutdown_t = number(rec['monotonic_s'])
            elif cmd == 'CHAN:OUTP 0':
                outputs[selected] = False
            elif on_count and shutdown_t is None and cmd not in ('SYST:REM', 'SYST:LOC'):
                need(cmd.startswith('INST:NSEL '), 'Powered configuration mutation')
        commands[typ].append((cmd, rec.get('response')))
    need(pending is None and on_count == 1 and shutdown_t is not None and
         0 < shutdown_t - power_t <= 90 and all(after_off[ch] for ch in (1,2,3)) and
         not any(outputs.values()), 'No single bounded enable or verified final OFF')
    for kind, stage_kind in (('query','query'), ('reply','read'), ('write','write')):
        wire_seq = commands[kind]
        stage_seq = [(r['command'], r.get('response')) for r in rows if r['event']==stage_kind]
        need((Counter(wire_seq)==Counter(stage_seq)) if kind=='query' else (wire_seq==stage_seq),
             f'Physical PSU {kind} differs from stage record')
    writes = [c for c,_ in commands['write']]
    need(writes.count('CHAN:OUTP 1')==1 and writes.count('OUTP 0')==2 and
         ('SYST:LOC' in writes)==successful and
         (not successful or writes[-1]=='SYST:LOC'), 'PSU ON/OFF/LOCAL policy mismatch')
    errors = [value for cmd,value in commands['reply'] if cmd=='SYST:ERR?']
    need(len(errors)>150 and all(v.split(',')[0].strip()=='0' for v in errors),
         'Missing or nonzero PSU SCPI error')
    reads = commands['reply']
    need([number(v) for c,v in reads if c=='STAT:OPER:INST:ISUM1:COND?']==[9] and
         [number(v) for c,v in reads if c=='STAT:QUES:INST:ISUM1:COND?']==[1],
         'CH1 CV status mismatch')
    return {'enabled_at':power_t,'disabled_at':shutdown_t,
            'psu_error_polls':len(errors),'psu_queries':len(commands['query']),
            'psu_writes':len(writes),
            'psu_meas_voltage':one([number(v) for c,v in reads if c=='MEAS:VOLT?'],'PSU measured voltage'),
            'psu_meas_current':one([number(v) for c,v in reads if c=='MEAS:CURR?'],'PSU measured current')}


def check_dmm(rows):
    reads = matching_pairs(rows, 'dmm7510_query', 'dmm7510_read')
    writes = [r['command'] for r in rows if r['event']=='dmm7510_write']
    need(tuple(writes)==DMM_WRITES, 'Unexpected DMM configuration writes')
    errors = [r['response'] for _,r in reads if r['command']==':SYST:ERR?']
    need(len(errors)>=12 and all(v.split(',')[0].strip()=='0' for v in errors),
         'Missing or nonzero DMM errors')
    need(one([r['response'] for _,r in reads if r['command']==':ROUT:TERM?'],
             'DMM terminals')=='FRON','DMM not configured FRONT')
    need(one([r['response'] for _,r in reads if r['command']=='*LANG?'],
             'DMM language')=='SCPI','DMM non-SCPI language')
    need(one([r['response'] for _,r in reads if r['command']=='*IDN?'],
             'DMM identity').startswith('KEITHLEY INSTRUMENTS,MODEL DMM7510,'),
         'Wrong DMM identity')
    config = single(rows,'dmm7510_configuration')
    need(config.get('outcome')=='PASS' and config.get('function')=='DC voltage' and
         config.get('terminals')=='front' and number(config['range_v'])==10 and
         number(config['nplc'])==1,'DMM config/readback mismatch')
    values=[number(r['response']) for _,r in reads if r['command']==':READ?']
    need(len(values)==2, 'Expected OFF and powered DMM measurement')
    return {'off':values[0], 'powered':values[1], 'dmm_error_polls':len(errors)}


def audit_ballast(rows, *, successful):
    need(100 < len(rows)<=8000 and all(type(r)==dict and r.get('test_id')=='R45FE' and
         r.get('event') in EVENTS for r in rows),'Mixed, invalid or missing evidence')
    times=[number(r.get('monotonic_s')) for r in rows]
    need(all(a<=b for a,b in zip(times,times[1:])), 'Nonmonotonic evidence timestamps')
    need(rows[0] is single(rows,'start') and rows[-1] is single(rows,'outcome'),
         'Unanchored start/outcome')
    start=rows[0]
    sw=start.get('software') or {}
    need(start.get('variant')=='1N4933_DIRECT_99P86OHM_BALLAST_SINGLE_POINT' and
         sw.get('sha')==CODE_SHA and sw.get('runner_sha256')==RUNNER_SHA and
         sw.get('stage_sha256')==STAGE_SHA and start.get('plan_sha256')==PLAN_SHA and
         start.get('config_sha256')==BENCH_SHA and
         start.get('dmm_role')=='FRONT_DCV_DIRECT_ACROSS_BALLAST' and
         start.get('operator_physical_claim_only') is True and
         start.get('current_accuracy')=='UNQUALIFIED' and
         start.get('note')=='KEL103 disconnected; no independent DUT ammeter',
         'Hardware plan/code/fixture provenance mismatch')
    close(start['ballast_ohm'],99.86,1e-9,'Physical resistor measurement')
    close(start['commanded_preset_v'],.9,1e-9,'Preset')
    close(start['current_limit_a'],.02,1e-9,'Current limit')
    need(single(rows,'pre_open_approvals').get('gates')==['BENCH','INITIAL'],
         'Missing connection/physical approval')
    gates=[r['gate'] for r in rows if r['event']=='operator_confirmation']
    need(gates==['POWER','FINAL'], 'Missing ordered power/final approval')
    outcome=rows[-1]
    need(outcome.get('physical_off_confirmed') is True and
         outcome.get('status')=='HW NOT_REVIEWED' and
         outcome.get('outcome')==('PASS' if successful else 'ABORTED'),
         'Wrong or unconfirmed final outcome')
    need(single(rows,'shutdown_psu').get('state')=='VERIFIED_OFF_BY_READBACK',
         'PSU shutdown not physically verified by SCPI')
    wire=check_wire(rows,successful=successful)
    dmm=check_dmm(rows)
    power_gate=one([r for r in rows if r['event']=='operator_confirmation' and
                       r.get('gate')=='POWER'], 'POWER gate')
    final_gate=one([r for r in rows if r['event']=='operator_confirmation' and
                       r.get('gate')=='FINAL'], 'FINAL gate')
    need(number(power_gate['monotonic_s']) < wire['enabled_at'] <
         wire['disabled_at'] <= number(single(rows,'shutdown_psu')['monotonic_s']) <
         number(final_gate['monotonic_s']), 'Operator power/shutdown gate order invalid')
    off=single(rows,'off_state_ballast_voltage')
    close(off['dmm_voltage_v'],dmm['off'],1e-10,'OFF-state DMM voltage')
    need(abs(dmm['off']) < .05,'Unsafe OFF-state DMM voltage')
    raw=single(rows,'ballast_direct_raw_sample')
    close(raw['psu_voltage_v'],wire['psu_meas_voltage'],1e-9,'Raw PSU voltage')
    close(raw['psu_current_a'],wire['psu_meas_current'],1e-9,'Raw PSU current')
    close(raw['dmm_ballast_voltage_v'],dmm['powered'],1e-9,'Raw DMM voltage')
    need(raw.get('regulation')=='CV' and raw.get('enabled') is True and
         raw.get('note')=='UNVALIDATED_SEQUENTIAL_SAMPLES',
         'Incorrect raw CV reading declaration')
    need(wire['enabled_at']<number(raw['psu_monotonic_s'])<
         number(raw['dmm_monotonic_s'])<wire['disabled_at'] and
         0 < number(raw['dmm_monotonic_s'])-number(raw['psu_monotonic_s'])<1,
         'Raw readings not during single powered interval')
    need(0.85 < number(raw['psu_voltage_v']) < .95 and
         0<=number(raw['psu_current_a'])<=.02,'PSU reading outside bounded pilot')
    point=[r for r in rows if r['event']=='ballast_direct_point']
    errors=[r for r in rows if r['event']=='stage_error']
    if successful:
        need(len(point)==1 and not errors and 'one direct ballast voltage sample' in outcome.get('reason',''),
             'Missing accepted point or unexpected error')
        p=point[0]
        need(.10<=dmm['powered']<=.65 and dmm['powered']<raw['psu_voltage_v']-.20,
             'Direct resistor voltage outside screening envelope')
        calc=dmm['powered']/99.86
        close(p['dmm_ballast_voltage_v'],dmm['powered'],1e-9,'Accepted DMM voltage')
        close(p['dmm_direct_ballast_current_a'],calc,1e-10,'Calculated current')
        close(p['psu_minus_dmm_current_a'],raw['psu_current_a']-calc,1e-10,'Current disagreement')
        close(p['inferred_diode_voltage_v'],raw['psu_voltage_v']-dmm['powered'],1e-9,'Inferred diode voltage')
        close(p['psu_voltage_v'],raw['psu_voltage_v'],1e-9,'Point PSU voltage')
        close(p['psu_current_a'],raw['psu_current_a'],1e-9,'Point PSU current')
        need(p.get('current_accuracy')=='UNQUALIFIED' and p.get('qualification')==
             'SINGLE_BALLAST_DCV_DIAGNOSTIC_ONLY' and
             p.get('causal_conclusion')=='NONE; one sequential diagnostic sample' and
             0 < number(p['powered_duration_s']) < 90,
             'Unsupported accuracy/qualification claim')
        close(p['psu_dmm_gap_ms'],(raw['dmm_monotonic_s']-raw['psu_monotonic_s'])*1000,
              1e-5,'Sequential meter time gap')
    else:
        need(not point and len(errors)==1 and errors[0].get('exception_type')=='SafetyError' and
             'Invalid direct DMM ballast voltage' in errors[0].get('detail','') and
             'SafetyError: Invalid direct DMM ballast voltage' in outcome.get('reason','') and
             dmm['powered'] < -.02,
             'Reversed DMM polarity not rejected before successful point')
    return {'status':'INDEPENDENT_EVIDENCE_REVIEW_PASS',
            'run':'POSITIVE_LEADS_PASS' if successful else 'REVERSED_LEADS_SAFE_ABORT',
            'current_accuracy':'UNQUALIFIED',
            'ballast_voltage_v':dmm['powered'],
            'psu_voltage_v':wire['psu_meas_voltage'],
            'psu_current_ma':wire['psu_meas_current']*1000,
            'ballast_estimated_current_ma':(dmm['powered']/99.86*1000) if successful else None,
            'psu_minus_ballast_current_ma':((wire['psu_meas_current']-dmm['powered']/99.86)*1000) if successful else None,
            'psu_error_polls':wire['psu_error_polls'],
            'dmm_error_polls':dmm['dmm_error_polls'],
            'powered_duration_s':wire['disabled_at']-wire['enabled_at'],
            'physical_off_confirmed':True}


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--reversed',type=Path,required=True)
    ap.add_argument('--reversed-sha256',required=True)
    ap.add_argument('--positive',type=Path,required=True)
    ap.add_argument('--positive-sha256',required=True)
    ap.add_argument('--forward',type=Path)
    ap.add_argument('--forward-sha256',default='bdb2859180a8aaadf13a0d6e76e46111dbdb0d2b606977b2dbc016e7a87fd56b')
    args=ap.parse_args(argv)
    try:
        negative, neg_hash=load_original(args.reversed,args.reversed_sha256,allowed_events=EVENTS)
        positive, pos_hash=load_original(args.positive,args.positive_sha256,allowed_events=EVENTS)
        fail=audit_ballast(negative,successful=False)
        passed=audit_ballast(positive,successful=True)
        need(negative[0]['software']==positive[0]['software'] and
             negative[0]['plan_sha256']==positive[0]['plan_sha256'] and
             negative[0]['config_sha256']==positive[0]['config_sha256'],
             'Runs use different source/plan/bench')
        need(abs(fail['ballast_voltage_v']+passed['ballast_voltage_v']) < .02 and
             abs(fail['psu_current_ma']-passed['psu_current_ma']) < .5 and
             abs(fail['psu_voltage_v']-passed['psu_voltage_v']) < .05,
             'Opposite DMM polarity is not consistent with same bounded load')
        output={'status':'INDEPENDENT_DUAL_RUN_EVIDENCE_REVIEW_PASS',
                'source_sha256':{'reversed':neg_hash,'positive':pos_hash},
                'reversed':fail,'positive':passed,
                'diagnostic_only':True,
                'limits':['No calibrated current accuracy established',
                          'Reverse-lead rejection is demonstrated only at this fixed point',
                          'This does not identify cause of PSU current discrepancy']}
        if args.forward is not None:
            rows, forward_hash=load_original(args.forward,args.forward_sha256)
            baseline=forward_audit(rows)
            need(baseline['status']=='INDEPENDENT_EVIDENCE_REVIEW_PASS',
                 'Earlier forward evidence not reviewed')
            old=baseline['points'][1]
            need(number(old['preset_v'])==.9, 'Earlier upward 0.90 V point missing')
            old_i=number(old['screening_current_ma'])
            direct_i=passed['ballast_estimated_current_ma']
            output['previous_forward_point_comparison']={
                'old_psu_current_ma':old['psu_current_ma'],
                'old_resistor_estimated_ma':old_i,
                'new_direct_ballast_ma':direct_i,
                'difference_old_minus_new_ma':old_i-direct_i,
                'old_psu_minus_estimate_ma':old['psu_current_ma']-old_i,
                'new_psu_minus_direct_ma':passed['psu_minus_ballast_current_ma'],
                'earlier_source_sha256':forward_hash,
                'note':'Measurements from DIFFERENT runs; not simultaneous calibration',
            }
    except (AuditError, OSError, ValueError, TypeError, OverflowError, KeyError,
            AttributeError, ZeroDivisionError, UnicodeError) as exc:
        print(f'R4.5-F-E INDEPENDENT DUAL EVIDENCE REJECTED: {type(exc).__name__}: {exc}')
        return 2
    print(json.dumps(output,ensure_ascii=False,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
