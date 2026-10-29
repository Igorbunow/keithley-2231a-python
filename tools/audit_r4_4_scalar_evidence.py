#!/usr/bin/env python3
"""Offline, fail-closed review of a single R4.4-C CH1 scalar qualification.

Reads local JSONL only. No equipment is opened; the input is never modified.
An accepted record proves one stable low-power observation, NOT FETC freshness.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys


class AuditError(ValueError):
    pass


def require(condition, explanation):
    if not condition:
        raise AuditError(explanation)


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, f'duplicate JSON key {key!r}')
        value[key] = item
    return value


def load_events(path):
    require(path.is_file(), 'evidence path is not a file')
    require(path.stat().st_size <= 20_000_000, 'evidence exceeds 20 MB limit')
    events = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        require(line_number <= 20000, 'too many evidence events')
        try:
            entry = json.loads(line, object_pairs_hook=unique_keys,
                               parse_constant=lambda token: (_ for _ in ()).throw(
                                   AuditError(f'non-finite JSON {token}')))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise AuditError(f'invalid JSONL line {line_number}: {exc}') from exc
        require(isinstance(entry, dict) and 'event' in entry, f'invalid event {line_number}')
        events.append(entry)
    require(events, 'empty evidence')
    return events


def by_type(events, event):
    return [(index, row) for index, row in enumerate(events) if row['event'] == event]


def single(events, event):
    matched = by_type(events, event)
    require(len(matched) == 1, f'expect one {event}, found {len(matched)}')
    return matched[0]


def numeric(value, name, maximum):
    require(type(value) in (float, int) and math.isfinite(value) and 0 <= value <= maximum,
            f'out-of-bounds {name}')
    return float(value)


def audit(events):
    require(all(e.get('test_id') == 'R44SCALAR' for e in events), 'wrong test_id')
    prev_mono = prev_time = None
    for i, event in enumerate(events):
        ts = datetime.fromisoformat(event['utc'])
        mono = event['monotonic_s']
        require(ts.tzinfo is not None and isinstance(mono, (float, int)) and math.isfinite(mono),
                f'bad clock at event {i}')
        if prev_mono is not None:
            require(mono > prev_mono and ts > prev_time, f'non-increasing event clock at {i}')
        prev_time, prev_mono = ts, mono

    start_idx, start = single(events, 'start')
    end_idx, end = single(events, 'outcome')
    require(start_idx == 0 and end_idx == len(events)-1, 'start/outcome must bound evidence')
    require(start.get('variant') == 'LOW_POWER_SCALAR_CH1_ONLY', 'incorrect variant')
    require(start.get('fixed_point') == {'v': 3.3, 'i_limit': .15, 'cr': 40., 'p_max': .6},
            'wrong configuration fixed point')
    require(end.get('outcome') == 'PASS' and end.get('phase') == 'LOW_POWER_SCALAR'
            and end.get('status') == 'HW NOT_REVIEWED', 'stage did not finish normal PASS')
    require(not any(e['event'] in ('stage_error', 'io_error', 'invalid_scpi_reply')
                    for e in events), 'record contains failure events')
    _, initial = single(events, 'pre_open_approvals')
    require(initial.get('gates') == ['BENCH', 'INITIAL']
            and initial.get('config_sha256') == start.get('config_sha256'),
            'missing initial gates/config provenance')
    require([e['gate'] for _, e in by_type(events, 'operator_confirmation')]
            == ['ENABLE', 'FINAL'], 'missing or reordered operator confirmation')

    # The initial OUTP:SER? logical query can enclose an automatic SYST:REM.
    # Match low-level traces by command and occurrence, not global high-level
    # ordering: otherwise this legitimate nested transition looks duplicated.
    actions = [(i, e) for i, e in enumerate(events) if e['event'] in ('query', 'write')]
    reads = by_type(events, 'read')
    traces = by_type(events, 'driver_trace')
    wire = [(index, record['trace']) for index, record in traces
            if record['trace']['event'] in ('query','write')]
    expected_trace = sum(2 if e['event'] == 'query' else 1 for _, e in actions)
    require(len(traces) == expected_trace, 'driver trace operation count mismatch')
    require(len(wire) == len(actions), 'on-wire request count mismatch')
    require(len(reads) == sum(e['event']=='query' for _, e in actions),
            'PSU reply count mismatch')

    from collections import defaultdict
    actions_by_key = defaultdict(list)
    trace_by_key = defaultdict(list)
    replies_by_key = defaultdict(list)
    for item in actions:
        actions_by_key[(item[1]['event'],item[1]['command'])].append(item)
    for item in traces:
        kind = item[1]['trace']['event']
        key = (kind,item[1]['trace']['command'])
        (replies_by_key if kind == 'reply' else trace_by_key)[key].append(item)
    for key, action_rows in actions_by_key.items():
        wire_rows = trace_by_key.get(key, [])
        require(len(action_rows) == len(wire_rows), 'unmatched PSU trace: '+str(key))
        for (action_index,_),(trace_index,_) in zip(action_rows,wire_rows):
            require(action_index < trace_index, 'PSU trace before logical command: '+str(key))
    for key, wire_rows in trace_by_key.items():
        require(key in actions_by_key, 'stray on-wire operation: '+str(key))
    for key, row_list in replies_by_key.items():
        require(key[0] == 'reply', 'unexpected trace kind')
        query_key = ('query',key[1])
        reqs = trace_by_key.get(query_key,[])
        corresponding_reads = [(i,e) for i,e in reads if e['command']==key[1]]
        require(len(reqs) == len(row_list) == len(corresponding_reads),
                'read/reply count mismatch: '+key[1])
        for (qi,_),(ri, reply),(ei, measured) in zip(reqs,row_list,corresponding_reads):
            require(qi < ri < ei and reply['trace']['response'] == measured['response'],
                    'read/trace value or order mismatch: '+key[1])
    require(sum(len(v) for v in replies_by_key.values()) == len(reads),
            'stray reply or high-level read')

    # Only examine the actual on-wire sequence for per-command error polling.
    # Its first OUTP:SER? may differ in order from the logical query call.
    polls = [(i,e) for i,e in wire if e['command']=='SYST:ERR?']
    for pos,(index,e) in enumerate(wire):
        cmd=e['command']
        if cmd == 'SYST:ERR?': continue
        require((cmd == 'SYST:LOC' and pos == len(wire)-1) or
                (pos+1 < len(wire) and wire[pos+1][1]['command']=='SYST:ERR?'),
                f'missing SCPI error poll after {cmd}')
    require(len(polls)==sum(e['command']!='SYST:ERR?' for _,e in wire)-1,
            'incorrect number of SCPI error polls')
    read_errors = [e['response'].replace(' ','').lower()
                   for _,e in reads if e['command']=='SYST:ERR?']
    require(len(read_errors)==len(polls) and all(x == '0,"noerror"'
            for x in read_errors), 'nonzero or missing PSU error response')

    read_for_action = {}
    for key, action_rows in actions_by_key.items():
        if key[0] == 'query':
            matching_reads = [e for _,e in reads if e['command']==key[1]]
            require(len(action_rows)==len(matching_reads), 'unpaired read for '+key[1])
            for (action_index,_),reply in zip(action_rows,matching_reads):
                read_for_action[action_index] = reply['response']

    # Reconstruct selected-channel output readbacks from explicit wire operations.
    selected = 1
    output_on = False
    witnessed_on = set()
    witnessed_off_after = Counter()
    first_on_pos = shutdown_pos = None
    for index, event in actions:
        cmd = event['command']
        if event['event'] == 'write':
            require(cmd in {
                'SYST:REM', 'SYST:LOC', 'OUTP 0',
                'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
                'CHAN:OUTP 0', 'CHAN:OUTP 1',
                'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
                'OUTP:TIM:STAT 0', 'VOLT 0', 'CURR 0',
                'VOLT:LIM 3.50', 'VOLT:LIM 1.50', 'VOLT:LIM:STAT 1',
                'VOLT 3.30', 'VOLT 1.00', 'CURR 0.150', 'CURR 0.100',
            }, f'forbidden PSU write {cmd}')
            if cmd.startswith('INST:NSEL '): selected = int(cmd[-1])
            if cmd == 'CHAN:OUTP 1':
                require(first_on_pos is None and selected == 1, 'repeated/incorrect output ON')
                first_on_pos = index
                output_on = True
            if cmd == 'OUTP 0' and output_on:
                shutdown_pos = index
                output_on = False
        else:
            require(cmd in {
                '*IDN?', 'SYST:ERR?', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
                'INST:NSEL?', 'CHAN:OUTP?', 'VOLT?', 'CURR?',
                'VOLT:LIM?', 'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?',
                'MEAS:VOLT?', 'MEAS:CURR?', 'MEAS:POW?',
                'FETC:VOLT?', 'FETC:CURR?', 'FETC:POW?',
            } | {f'STAT:{kind}:INST:ISUM{ch}:COND?' for kind in ('OPER','QUES')
                  for ch in (1,2,3)}, f'forbidden PSU query {cmd}')
            # Each read is uniquely linked by the trace audit above.
            raw = read_for_action[index]
            if cmd == 'INST:NSEL?':
                require(int(raw) == selected, 'channel selection mismatch')
            if cmd == 'CHAN:OUTP?':
                expected = 1 if output_on and selected == 1 else 0
                require(int(raw) == expected, f'CH{selected} output state mismatch')
                if output_on: witnessed_on.add(selected)
                if shutdown_pos is not None: witnessed_off_after[selected] += 1
            if cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
                require(float(raw) == 0., 'combined topology active')
    require(first_on_pos is not None and shutdown_pos is not None,
            'missing ON or subsequent OFF command')
    require(first_on_pos < shutdown_pos and witnessed_on == {1,2,3},
            'active phase did not verify all channel outputs')
    require(all(witnessed_off_after[ch] >= 2 for ch in (1,2,3)),
            'fewer than two OFF readbacks for a channel after shutdown')
    require(actions[-1][1]['command'] == 'SYST:LOC', 'missing final SYST:LOC')

    def pairs_for(prefix):
        q = by_type(events, prefix+'_query')
        reads_ = by_type(events, prefix+'_read')
        require(len(q) == len(reads_), prefix+' query/reply count mismatch')
        for (qi,query),(ri,reply) in zip(q,reads_):
            require(qi < ri and query['command'] == reply['command'],
                    prefix+' query/reply mismatch')
            require(not any(qi < other < ri for other, _ in q),
                    prefix+' response interleaving')
        return q, reads_

    _, kel_reads = pairs_for('kel103')
    _, dmm_reads = pairs_for('dmm7510')
    kel_writes = by_type(events, 'kel103_write')
    dmm_writes = by_type(events, 'dmm7510_write')
    require([x['command'] for _, x in kel_writes] ==
            [':FUNC CR', ':RES 40OHM', ':INP ON', ':INP OFF'],
            'unexpected KEL write sequence')
    require([x['command'] for _, x in dmm_writes] ==
            ['*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
             ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
             ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF'],
            'unexpected DMM write sequence')
    kel_input_reads = [(i,e['response']) for i,e in kel_reads if e['command']==':INP?']
    require(len(kel_input_reads) >= 4 and all(value == 'OFF' for _, value in kel_input_reads[-2:]),
            'KEL final OFF not confirmed twice')
    require(kel_writes[2][0] < first_on_pos and kel_writes[3][0] > shutdown_pos,
            'KEL power sequence not aligned with PSU')
    require(next(i for i,x in by_type(events,'operator_confirmation')
                 if x['gate']=='ENABLE') < first_on_pos,
            'missing prior power gate')
    require(next(i for i,x in by_type(events,'operator_confirmation')
                 if x['gate']=='FINAL') > kel_writes[3][0],
            'final gate before load was switched off')
    require(any(x['command']==':ROUT:TERM?' and x['response']=='FRON'
                for _,x in dmm_reads), 'DMM FRONT terminals not observed')
    dmm_samples = [x['response'] for _,x in dmm_reads if x['command']==':READ?']
    require(len(dmm_samples) == 1, 'expected one DMM sample')
    require(all(x['response'].startswith('0,') for _,x in dmm_reads
                if x['command']==':SYST:ERR?'), 'DMM reported SCPI error')

    measure_index, measurement = single(events, 'scalar_measurements')
    require(first_on_pos < measure_index < shutdown_pos, 'measurements outside powered interval')
    require(measurement['fetch_is_fresh']=='UNVERIFIED' and
            measurement['fetch_agrees_with_live'] is True,
            'FETC freshness incorrectly claimed or agreement false')
    v = numeric(measurement['psu_voltage_v'], 'PSU V', 3.55)
    i = numeric(measurement['psu_current_a'], 'PSU A', .165)
    ku = numeric(measurement['kel_voltage_v'], 'KEL V', 3.55)
    ki = numeric(measurement['kel_current_a'], 'KEL A', .165)
    du = numeric(measurement['dmm_voltage_v'], 'DMM V', 3.55)
    require(abs(float(dmm_samples[0])-du)<1e-7, 'DMM sample differs from recorded measurement')
    kp = numeric(measurement['kel_power_w'], 'KEL W', .6)
    p = numeric(measurement['meas_power_w'], 'MEAS W', .6)
    fp = numeric(measurement['fetch_power_w'], 'FETC W', .6)
    fv = numeric(measurement['fetch_voltage_v'], 'FETC V', 3.55)
    fi = numeric(measurement['fetch_current_a'], 'FETC A', .165)
    derived = numeric(measurement['psu_derived_power_w'], 'PSU power', .6)
    require(abs(v*i-derived) < 1e-9, 'PSU derived power inconsistent with U*I')
    require(all(abs(x-3.3) <= .20 for x in (v,ku,du)), 'voltage outside approved point')
    require(abs(v-ku) <= .08 and abs(v-du) <= .08 and abs(ku-du) <= .08,
            'independent voltage mismatch')
    require(abs(i-ki) <= .03 and abs(ku*ki-kp) <= .045,
            'independent load current/power mismatch')
    require(abs(derived-p) <= .08 and abs(p-kp) <= .08,
            'MEAS power mismatch')
    require(abs(fv-v) <= .08 and abs(fi-i) <= .03 and abs(fp-p) <= .08,
            'FETC disagreement with stable point')
    expected = {'MEAS:VOLT?':v,'MEAS:CURR?':i,'MEAS:POW?':p,
                'FETC:VOLT?':fv,'FETC:CURR?':fi,'FETC:POW?':fp}
    for cmd, value in expected.items():
        response = [e['response'] for _,e in reads if e['command']==cmd]
        require(len(response)==1 and abs(float(response[0])-value) < 1e-7,
                'scalar mismatch/missing: '+cmd)
    kexpected = {':MEAS:VOLT?':ku,':MEAS:CURR?':ki,':MEAS:POW?':kp}
    for cmd, value in kexpected.items():
        response = [e['response'] for _,e in kel_reads if e['command']==cmd]
        require(len(response)==1 and abs(float(response[0].rstrip('VAW'))-value) < 1e-7,
                'KEL sample missing/mismatch: '+cmd)
    times = measurement['timestamps_monotonic_s']
    require(list(times)==['psu_read_channel_s','kel103_s','dmm7510_s',
                          'meas_power_s','fetch_voltage_s','fetch_current_s','fetch_power_s'],
            'missing per-device timing metadata')
    require(all(start['monotonic_s'] < a < b < events[measure_index]['monotonic_s']
                for a,b in zip(times.values(),list(times.values())[1:])),
            'measurement timestamps out of order')
    return {
        'status':'REVIEWABLE_PASS', 'scope':'one independent CH1 point; FETC freshness UNVERIFIED',
        'records':len(events), 'psu_queries':len([1 for _,e in actions if e['event']=='query']),
        'psu_writes':len([1 for _,e in actions if e['event']=='write']),
        'scpi_error_polls':len(polls), 'dmm_queries':len(dmm_reads),
        'kel_queries':len(kel_reads), 'output_checks_after_off':dict(witnessed_off_after),
        'psu_voltage_v':v, 'kel_voltage_v':ku, 'dmm_voltage_v':du,
        'psu_current_a':i, 'kel_current_a':ki, 'meas_power_w':p,
        'psu_derived_power_w':derived, 'kel_power_w':kp,
        'fetch_voltage_v':fv,'fetch_current_a':fi,'fetch_power_w':fp,
        'firmware':measurement['firmware'],
        'source_commit':start['software']['sha'],
        'source_dirty':start['software']['dirty'],
        'config_sha256':start['config_sha256'],
        'identity_privacy_note':'legacy evidence psu_identity may contain raw serial; do not redistribute JSONL'
    }


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl', type=Path)
    parser.add_argument('--expected-sha256', default=None)
    args=parser.parse_args(argv)
    try:
        raw=args.jsonl.read_bytes()
        actual=hashlib.sha256(raw).hexdigest()
        if args.expected_sha256 is not None:
            require(actual==args.expected_sha256.lower(), 'evidence SHA-256 mismatch')
        result=audit(load_events(args.jsonl))
        result['evidence_sha256']=actual
    except (AuditError, OSError, KeyError, TypeError, ValueError, OverflowError) as exc:
        print(f'R4.4-C EVIDENCE AUDIT REJECTED: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 2
    print('R4.4-C EVIDENCE AUDIT REVIEWABLE PASS (not independent physical proof)')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
