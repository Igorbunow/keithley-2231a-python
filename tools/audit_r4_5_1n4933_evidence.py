#!/usr/bin/env python3
"""Read-only, fail-closed audit of an original R45FD 1N4933 forward pilot JSONL.

Checks the physical PSU debug trace separately from stage records, metrology
arithmetic, gate ordering, OFF readback, and the exact reviewed runner hashes.
This is a bounded steady-state screening, not a current calibration or a
replacement for an independent ammeter. Never contacts an instrument.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, need, number, one, matching_pairs

LEVELS = (0.80, 0.90, 1.00, 0.90, 0.80)
CODE_SHA = '9fc4db4212ab19f65531903acd2679e57d5f832a'
RUNNER_SHA = '63d820e2231f9adce621bba218f6a12b3b0aef703e107e33f53341a6f437594c'
STAGE_SHA = '51e05f9bbea20a0187fed81b51a5b3a6a6db05ccc183f75e75497c7719fc87f1'
PLAN_SHA = '5ae13e49cf89f91b15ba145c7d7e639200f44dc759f42370cfbad8746a921f17'
BENCH_SHA = 'e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720'
DMM_WRITES = ('*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
              ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
              ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF')
PSU_WRITES = {
    'SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0',
    'OUTP:PARALLEL 0', 'OUTP:TRAC 0', 'OUTP:TIM:STAT 0',
    'VOLT 0', 'CURR 0', 'VOLT:LIM 1.05', 'VOLT:LIM 1.50',
    'VOLT:LIM:STAT 1', 'CURR 0.020', 'CURR 0.100',
    'VOLT 0.80', 'VOLT 0.90', 'VOLT 1.00',
    'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
    'CHAN:OUTP 1', 'CHAN:OUTP 0',
}
EVENTS = {
    'start', 'pre_open_approvals', 'query', 'driver_trace', 'read', 'write',
    'psu_identity', 'dmm7510_query', 'dmm7510_read',
    'dmm7510_command_set', 'dmm7510_write', 'dmm7510_configuration',
    'off_state_diode_voltage', 'operator_confirmation',
    'diode_iv_raw_sample', 'diode_iv_point', 'diode_iv_segment',
    'diode_iv_summary', 'shutdown_psu', 'outcome',
}


def close(actual, expected, tolerance, name):
    need(abs(number(actual) - number(expected)) <= tolerance, f'{name} mismatch')


def unique(rows, event):
    return one([r for r in rows if r.get('event') == event], event)


def load_original(path: Path, expected_hash: str, *, allowed_events=None):
    need(len(expected_hash) == 64 and all(c in '0123456789abcdef' for c in expected_hash.lower()),
         'Original SHA-256 must be a full hexadecimal digest')
    size = path.stat().st_size
    need(0 < size <= 4_000_000, 'Original evidence empty or too large')
    payload = path.read_bytes()
    actual_hash = hashlib.sha256(payload).hexdigest()
    need(actual_hash == expected_hash.lower(), 'Original evidence SHA-256 mismatch')
    need(payload.endswith(b'\n'), 'Truncated JSONL: missing terminal newline')
    lines = payload.splitlines()
    need(100 < len(lines) <= 8000, 'Original evidence event count out of bounds')
    rows = []
    def reject_duplicate(pairs):
        d = {}
        for k, v in pairs:
            need(k not in d, f'Duplicate JSON key: {k}')
            d[k] = v
        return d
    for index, raw in enumerate(lines, 1):
        try:
            item = json.loads(raw, object_pairs_hook=reject_duplicate)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AuditError(f'Invalid JSONL at line {index}: {exc}') from None
        need(type(item) is dict and item.get('event') in (EVENTS if allowed_events is None else allowed_events),
             f'Unexpected record type/event at line {index}')
        rows.append(item)
    return rows, actual_hash


def wire_audit(rows):
    traces = [r['trace'] for r in rows if r['event'] == 'driver_trace']
    need(bool(traces), 'Missing physical PSU trace')
    wire_commands = []
    pending = None
    selected = 1
    outputs = {1: False, 2: False, 3: False}
    enabled = False
    enabled_times = []
    shutdown_time = None
    off_readbacks = Counter()
    cv_bits = Counter()
    moves = []
    for tr in traces:
        kind, cmd = tr.get('event'), tr.get('command')
        need(isinstance(cmd, str) and cmd and tr.get('profile_bypass') is False,
             'Missing, invalid or bypassed physical PSU command')
        need(kind in ('query', 'reply', 'write'), 'Unexpected driver trace kind')
        if kind == 'query':
            need(pending is None, 'Overlapping physical PSU queries')
            pending = cmd
        elif kind == 'reply':
            need(pending == cmd and isinstance(tr.get('response'), str),
                 'Wrong/missing physical PSU response')
            pending = None
            value = tr['response']
            if cmd == 'CHAN:OUTP?':
                need(value == ('1' if outputs[selected] else '0'),
                     f'CH{selected} output readback contradicts physical commands')
                if shutdown_time is not None and value == '0':
                    off_readbacks[selected] += 1
            elif cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
                need(value == '0', 'Combined topology unexpectedly active')
            elif cmd in ('STAT:OPER:INST:ISUM1:COND?', 'STAT:QUES:INST:ISUM1:COND?'):
                need(selected == 1 and outputs[1], 'CV status read outside powered CH1')
                need(value == ('9' if 'OPER' in cmd else '1'), 'Unexpected CH1 CV status bits')
                cv_bits[cmd] += 1
            elif cmd == 'SYST:ERR?':
                need(value.split(',', 1)[0].strip() == '0', 'Nonzero PSU SCPI error')
        elif kind == 'write':
            need(pending is None and cmd in PSU_WRITES, f'Unapproved PSU physical write: {cmd}')
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1])
            elif cmd == 'CHAN:OUTP 1':
                need(selected == 1 and not enabled and not any(outputs.values()),
                     'Wrong/repeated/unsafe CH1 enable')
                enabled = True
                enabled_times.append(number(tr['monotonic_s']))
                outputs[1] = True
            elif cmd == 'OUTP 0':
                outputs = {1: False, 2: False, 3: False}
                if enabled:
                    need(shutdown_time is None, 'Second powered shutdown')
                    shutdown_time = number(tr['monotonic_s'])
            elif cmd == 'CHAN:OUTP 0':
                outputs[selected] = False
            elif cmd in ('VOLT 0.80', 'VOLT 0.90', 'VOLT 1.00') and enabled and shutdown_time is None:
                need(selected == 1, 'Voltage changed on inactive channel while powered')
                moves.append(cmd)
            elif enabled and shutdown_time is None and cmd not in (
                    'SYST:REM', 'SYST:LOC', 'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'):
                raise AuditError(f'Unexpected configuration mutation while CH1 powered: {cmd}')
        wire_commands.append((kind, cmd, tr.get('response')))
    need(pending is None, 'Unterminated physical PSU query')
    # Stage hooks are sometimes logged before the nested wire action and may
    # interleave in a different order. Compare each command/reply type in order.
    # Do not compare the three mixed event types as a single sequence.
    for physical_kind, stage_kind in (('query', 'query'), ('reply', 'read'), ('write', 'write')):
        physical = [(cmd, response) for kind, cmd, response in wire_commands
                    if kind == physical_kind]
        stage = [(r['command'], r.get('response')) for r in rows
                 if r['event'] == stage_kind]
        need((Counter(physical) == Counter(stage)) if physical_kind == 'query' else (physical == stage),
             f'Physical PSU {physical_kind} differs from stage records')
    writes = [cmd for kind, cmd, _ in wire_commands if kind == 'write']
    reads = [(cmd, response) for kind, cmd, response in wire_commands if kind == 'reply']
    need(writes.count('CHAN:OUTP 1') == 1 and writes.count('OUTP 0') == 2,
         'Wrong number of source enable/global OFF commands')
    need(moves == ['VOLT 0.90', 'VOLT 1.00', 'VOLT 0.90', 'VOLT 0.80'],
         'Wrong powered voltage sequence')
    need(not any(outputs.values()) and shutdown_time is not None and
         all(off_readbacks[ch] >= 1 for ch in (1, 2, 3)),
         'Missing confirmed final OFF for all three channels')
    need(writes[-1] == 'SYST:LOC' and writes.count('SYST:LOC') == 1,
         'No final local control restoration')
    need(cv_bits['STAT:OPER:INST:ISUM1:COND?'] == 5 and
         cv_bits['STAT:QUES:INST:ISUM1:COND?'] == 5,
         'Missing one or more CH1 CV status reports')
    err = [value for cmd, value in reads if cmd == 'SYST:ERR?']
    need(len(err) > 100 and all(v.split(',',1)[0].strip() == '0' for v in err),
         'Missing/invalid/nonzero PSU SCPI error polls')
    return dict(scpi_reads=len(reads), scpi_writes=len(writes), error_polls=len(err),
                enable_monotonic_s=enabled_times[0], shutdown_monotonic_s=shutdown_time,
                psu_voltage=[number(v) for cmd,v in reads if cmd == 'MEAS:VOLT?'],
                psu_current=[number(v) for cmd,v in reads if cmd == 'MEAS:CURR?'])


def points_audit(rows, wire, dmm_readings):
    raw = [r for r in rows if r['event'] == 'diode_iv_raw_sample']
    pts = [r for r in rows if r['event'] == 'diode_iv_point']
    segments = [r for r in rows if r['event'] == 'diode_iv_segment']
    summary = unique(rows, 'diode_iv_summary')
    need(len(raw) == len(pts) == len(wire['psu_voltage']) == len(wire['psu_current']) == 5,
         'Not exactly five physically sampled I-V points')
    need(len(dmm_readings) == 6 and len(segments) == 4, 'Wrong DMM/segment count')
    require_current = []
    discrepancies = []
    for index, (r, p, preset) in enumerate(zip(raw, pts, LEVELS)):
        need(r.get('index') == p.get('index') == index and
             r.get('preset_v') == p.get('preset_v') == preset and
             r.get('regulation') == 'CV' and r.get('enabled') is True,
             'Wrong point ordering, target or power state')
        need(p.get('sampling') == 'SEQUENTIAL_NOT_SYNCHRONIZED' and
             p.get('independent_current_sensor') is False and
             p.get('current_method') == 'SEQUENTIAL_PSU_V_MINUS_DMM_DIODE_V_OVER_MEASURED_R',
             'Fabricated independent or synchronous current claim')
        need(r.get('interpretation') == 'UNVALIDATED_SEQUENTIAL_VOLTAGE_SAMPLE' and
             p.get('weak_current_unquantified') is False,
             'Weak/uncertain point cannot be accepted as quantitative')
        close(r['psu_voltage_v'], wire['psu_voltage'][index], 1e-9, 'Raw PSU voltage')
        close(r['psu_current_a'], wire['psu_current'][index], 1e-9, 'Raw PSU current')
        close(r['dmm_diode_voltage_v'], dmm_readings[index+1], 1e-9, 'Raw DMM diode')
        close(p['psu_voltage_v'], r['psu_voltage_v'], 1e-9, 'Point PSU voltage')
        close(p['psu_current_a'], r['psu_current_a'], 1e-9, 'Point PSU current')
        close(p['diode_voltage_v'], r['dmm_diode_voltage_v'], 1e-9, 'Point DMM voltage')
        close(p['digital_v'], preset, 0.00049, 'Digital setpoint')
        voltage = number(p['psu_voltage_v']); diode = number(p['diode_voltage_v'])
        current = number(p['psu_current_a'])
        resistor = number(r['resistor_measured_ohm'])
        close(resistor, 99.86, 1e-9, 'Measured series resistor')
        need(0.25 < diode < voltage <= 1.06 and abs(voltage - preset) <= .05,
             'Outside bounded forward voltage envelope')
        need(0 <= current <= .022 and voltage * current <= .022,
             'PSU analog current/power exceeds allowed ceiling')
        calc_current = (voltage - diode) / resistor
        need(.00015 <= calc_current <= .0106 and abs(current-calc_current) <= .006,
             'Resistor current outside reviewed protective envelope')
        close(p['screening_current_a'], calc_current, 1e-10, 'Calculated resistor current')
        close(p['resistor_drop_v'], voltage-diode, 1e-9, 'Resistor drop')
        close(p['diode_estimated_power_w'], diode*calc_current, 1e-10, 'Estimated diode power')
        stamps = p['timestamps_monotonic_s']
        close(stamps['psu'], r['psu_sample_monotonic_s'], 1e-9, 'PSU sample time')
        close(stamps['dmm'], r['dmm_sample_monotonic_s'], 1e-9, 'DMM sample time')
        need(wire['enable_monotonic_s'] < stamps['psu'] < stamps['dmm'] < wire['shutdown_monotonic_s']
             and 0 < stamps['dmm']-stamps['psu'] < 1,
             'Measurement not inside synchronized-enough bounded powered session')
        require_current.append(calc_current)
        discrepancies.append(1000*(calc_current-current))
        if index:
            segment = segments[index-1]
            prior = pts[index-1]
            direction = 'up' if index<=2 else 'down'
            sign = 1 if direction == 'up' else -1
            need(segment.get('index') == index and segment.get('direction') == direction,
                 'Incorrect segment direction/order')
            di = sign*(calc_current-number(prior['screening_current_a']))
            dv = sign*(diode-number(prior['diode_voltage_v']))
            need(.0001 <= di <= .002 and .002 <= dv <= .12,
                 'Forward curve monotonicity/segment out of bounds')
            close(segment['signed_current_delta_a'], di, 1e-10, 'Segment current')
            close(segment['signed_diode_delta_v'], dv, 1e-10, 'Segment diode voltage')
    ret_v = abs(number(pts[0]['diode_voltage_v'])-number(pts[4]['diode_voltage_v']))
    ret_i = abs(require_current[0]-require_current[4])
    need(ret_v <= .04 and ret_i <= .001, 'Forward return loop exceeds tolerance')
    close(summary['return_diode_error_v'], ret_v, 1e-10, 'Return diode voltage')
    close(summary['return_screening_current_error_a'], ret_i, 1e-10, 'Return current')
    need(summary.get('qualification') == 'FORWARD_STEADY_STATE_SCREENING_ONLY' and
         0 < number(summary.get('powered_duration_s')) <= 90,
         'Review scope/duration incorrect')
    return dict(points=[dict(preset_v=p['preset_v'], diode_voltage_v=p['diode_voltage_v'],
                             screening_current_ma=1000*p['screening_current_a'],
                             psu_current_ma=1000*p['psu_current_a']) for p in pts],
                return_diode_error_mv=ret_v*1000,
                return_current_error_ua=ret_i*1_000_000,
                mean_psu_minus_resistor_current_ma=-sum(discrepancies)/5,
                max_abs_psu_resistor_difference_ma=max(abs(x) for x in discrepancies),
                powered_duration_s=number(summary['powered_duration_s']))


def audit(rows):
    need(bool(rows) and all(r.get('test_id') == 'R45FD' and r.get('event') in EVENTS for r in rows),
         'Mixed/unsupported evidence records')
    times = [number(r['monotonic_s']) for r in rows]
    need(all(a <= b for a, b in zip(times, times[1:])), 'Evidence timestamp order violated')
    start = unique(rows,'start')
    need(rows[0] is start and rows[-1] is unique(rows,'outcome'),
         'Unanchored evidence start/outcome')
    need(start.get('variant') == '1N4933_FORWARD_100OHM' and
         start.get('commanded_voltages') == list(LEVELS), 'Unreviewed DUT topology or plan')
    sw = start.get('software') or {}
    need(sw.get('sha') == CODE_SHA and sw.get('runner_sha256') == RUNNER_SHA and
         sw.get('stage_sha256') == STAGE_SHA and start.get('plan_sha256') == PLAN_SHA and
         start.get('config_sha256') == BENCH_SHA, 'Unreviewed software/plan/bench provenance')
    close(start['resistor_measured_ohm'],99.86,1e-9,'Measured resistor')
    need(start.get('operator_physical_claim_only') is True and
         start.get('note') == 'KEL103 physically disconnected; no independent DUT ammeter',
         'Unexpected physical configuration claim')
    need(unique(rows,'pre_open_approvals').get('gates') == ['BENCH','INITIAL'],
         'Missing initial operator approvals')
    gates = [(i,r.get('gate')) for i,r in enumerate(rows) if r['event']=='operator_confirmation']
    need([g for _,g in gates]==['POWER','FINAL'], 'Missing/misordered POWER/FINAL gates')
    outcome=unique(rows,'outcome')
    need(outcome.get('outcome')=='PASS' and outcome.get('status')=='HW NOT_REVIEWED' and
         outcome.get('physical_off_confirmed') is True, 'Runner did not complete all gates')
    need(not any(r['event'].endswith('_error') or r['event'].endswith('_unknown') or
                 r['event']=='diode_iv_low_current_caution' for r in rows),
         'Fault/low-current caution in completed run')
    need(unique(rows,'shutdown_psu').get('state')=='VERIFIED_OFF_BY_READBACK',
         'PSU did not confirm physical OFF')
    wire=wire_audit(rows)
    first_enable=next(i for i,r in enumerate(rows) if r.get('event')=='write' and
                      r.get('command')=='CHAN:OUTP 1')
    last_off=max(i for i,r in enumerate(rows) if r.get('event')=='write' and
                 r.get('command')=='OUTP 0')
    need(gates[0][0] < first_enable < last_off < gates[1][0] and
         next(i for i,r in enumerate(rows) if r['event']=='shutdown_psu')<gates[1][0],
         'Power gate/output ON/OFF/final gate in wrong order')
    dmm = matching_pairs(rows,'dmm7510_query','dmm7510_read')
    dmm_w = [(i,r['command']) for i,r in enumerate(rows) if r['event']=='dmm7510_write']
    need(tuple(x[1] for x in dmm_w)==DMM_WRITES and dmm_w[-1][0]<first_enable,
         'Unexpected DMM configuration or write while powered')
    need(unique(rows,'dmm7510_command_set').get('language')=='SCPI' and
         unique(rows,'dmm7510_configuration').get('outcome')=='PASS' and
         unique(rows,'dmm7510_configuration').get('terminals')=='front' and
         unique(rows,'dmm7510_configuration').get('function')=='DC voltage' and
         unique(rows,'dmm7510_configuration').get('range_v')==10 and
         unique(rows,'dmm7510_configuration').get('nplc')==1,
         'Incorrect voltage measurement setup')
    dmm_errors=[r for _,r in dmm if r['command']==':SYST:ERR?']
    need(len(dmm_errors)>=10 and all(r['response'].split(',',1)[0].strip()=='0' for r in dmm_errors),
         'DMM SCPI error detected/missing')
    dmm_readings=[number(r['response']) for _,r in dmm if r['command']==':READ?']
    need(len(dmm_readings)==6, 'Missing or extra DMM point reads')
    close(unique(rows,'off_state_diode_voltage')['dmm_v'],dmm_readings[0],1e-9,
          'OFF-state DMM voltage')
    need(abs(dmm_readings[0])<=.05,'Residual OFF-state voltage too high')
    need(not any(r['event'].startswith('kel103') for r in rows),
         'KEL103 unexpectedly controlled during diode run')
    results=points_audit(rows,wire,dmm_readings)
    return dict(status='INDEPENDENT_EVIDENCE_REVIEW_PASS',
                qualification='1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY',
                current_accuracy='UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER',
                review_warning='Source readback current differs from resistor-estimated current',
                provenance=dict(commit=CODE_SHA,runner_sha256=RUNNER_SHA,stage_sha256=STAGE_SHA,
                                plan_sha256=PLAN_SHA,bench_sha256=BENCH_SHA),
                scpi=dict(psu_queries=wire['scpi_reads'],psu_writes=wire['scpi_writes'],
                          psu_zero_error_polls=wire['error_polls'],
                          dmm_zero_error_polls=len(dmm_errors)),
                **results)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',required=True,type=Path,help='Original, unmodified events.jsonl')
    parser.add_argument('--sha256',required=True,help='Full SHA256 of exact original JSONL bytes')
    args=parser.parse_args(argv)
    try:
        rows,src_hash=load_original(args.evidence,args.sha256)
        result=audit(rows)
        result['original_jsonl_sha256']=src_hash
    except (AuditError, OSError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f'R4.5-F-D INDEPENDENT REVIEW REJECTED / HW NOT_REVIEWED: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-D INDEPENDENT EVIDENCE REVIEW PASS (OFFLINE; NO INSTRUMENT I/O)')
    print(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
