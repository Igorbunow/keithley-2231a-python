"""Independent R4.5-E-B powered STEP evidence auditor (one CV or CC case).

Inspect the *original* JSONL from the CH1/KEL103/DMM7510 runner. This confirms
one steady-state analog response, NOT transient response or analog calibration.
No instrument I/O, no file writes, no dependency on private bench settings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


class AuditError(ValueError):
    pass


def need(ok, message):
    if not ok:
        raise AuditError(message)


def number(value):
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError):
        raise AuditError(f'Invalid numeric observation: {value!r}') from None
    need(math.isfinite(x), 'Non-finite observation')
    return x


def one(items, title):
    need(len(items) == 1, f'Expected exactly one {title}, found {len(items)}')
    return items[0]


def matching_pairs(events, query_kind, read_kind):
    """Sequential peers have no implicit nested driver startup queries."""
    outstanding = None
    found = []
    for i, ev in enumerate(events):
        if ev.get('event') == query_kind:
            need(outstanding is None, f'Overlapping {query_kind}')
            outstanding = ev.get('command')
        elif ev.get('event') == read_kind:
            need(outstanding == ev.get('command'), f'Missing or swapped {read_kind}')
            need(isinstance(ev.get('response'), str), 'Missing instrument response')
            found.append((i, ev))
            outstanding = None
    need(outstanding is None and found, f'Unpaired {query_kind}')
    return found


def check_trace(events):
    """Physical PSU command/response ordering, not misleading stage-start order."""
    traces = [ev['trace'] for ev in events if ev.get('event') == 'driver_trace']
    stage_reads = [ev for ev in events if ev.get('event') == 'read']
    stage_queries = [ev for ev in events if ev.get('event') == 'query']
    stage_writes = [ev for ev in events if ev.get('event') == 'write']
    need(traces and stage_reads and stage_writes, 'Missing original physical SCPI trace')
    pending = None
    wire_replies = []
    wire_queries = []
    wire_writes = []
    for tr in traces:
        typ, cmd = tr.get('event'), tr.get('command')
        need(isinstance(cmd, str) and cmd, 'Invalid SCPI command')
        if typ == 'query':
            need(pending is None, 'Overlapping PSU physical queries')
            pending = cmd
            wire_queries.append(cmd)
        elif typ == 'reply':
            need(pending == cmd, 'Wrong or unsolicited PSU physical reply')
            need(isinstance(tr.get('response'), str), 'Missing PSU physical response')
            pending = None
            wire_replies.append(tr)
        elif typ == 'write':
            need(pending is None, 'PSU write interleaved with pending reply')
            wire_writes.append(cmd)
        else:
            raise AuditError('Unexpected driver trace event')
    need(pending is None, 'PSU query without physical reply')
    need(Counter(wire_queries) == Counter(x['command'] for x in stage_queries),
         'PSU physical queries differ from stage commands')
    need(Counter(wire_writes) == Counter(x['command'] for x in stage_writes),
         'PSU physical writes differ from stage commands')
    need(len(wire_replies) == len(stage_reads), 'PSU reply count mismatch')
    for physical, stage in zip(wire_replies, stage_reads):
        need(physical['command'] == stage['command'], 'PSU physical reply order drift')
        need(physical['response'] == stage['response'], 'PSU physical reply value drift')
    # Exactly one scoped ON and one STEP; reconstruct selected CH on the wire.
    selected = 1
    output = {1: False, 2: False, 3: False}
    saw_on = False
    on_checks = Counter()
    off_after_shutdown = Counter()
    step = []
    last_outp0_idx = -1
    safe_writes = {
        'SYST:REM', 'SYST:LOC', 'OUTP 0', 'OUTP:SER 0',
        'OUTP:PARALLEL 0', 'OUTP:TRAC 0', 'OUTP:TIM:STAT 0',
        'VOLT 0', 'CURR 0', 'VOLT:LIM 3.50', 'VOLT:LIM 1.50',
        'VOLT:LIM:STAT 1', 'VOLT 1.00', 'VOLT 3.30',
        'CURR 0.100', 'CURR 0.150', 'CHAN:OUTP 1', 'CHAN:OUTP 0',
        'VOLT:STEP 0.010', 'VOLT:UP', 'CURR:STEP 0.005', 'CURR:UP',
        *(f'INST:NSEL {ch}' for ch in (1,2,3)),
    }
    for index, tr in enumerate(traces):
        cmd, typ = tr['command'], tr['event']
        if typ == 'write':
            need(cmd in safe_writes, 'Unapproved physical PSU write')
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd[-1]) if cmd[-1] in '123' else 0
                need(selected in (1, 2, 3), 'Unexpected channel selection')
            elif cmd == 'CHAN:OUTP 1':
                need(selected == 1 and not saw_on, 'Wrong/repeated CH ON')
                saw_on = True
                output[1] = True
            elif cmd == 'CHAN:OUTP 0':
                output[selected] = False
            elif cmd == 'OUTP 0':
                output = {1: False, 2: False, 3: False}
                last_outp0_idx = index
            elif cmd in ('VOLT:UP', 'CURR:UP', 'VOLT:DOWN', 'CURR:DOWN') or ':STEP ' in cmd:
                step.append((index, selected, cmd, saw_on, output[1]))
        elif typ == 'reply' and cmd == 'CHAN:OUTP?':
            need(tr['response'] == ('1' if output[selected] else '0'),
                 f'CH{selected} output readback conflicts with physical output commands')
            if output[selected]: on_checks[selected] += 1
            if index > last_outp0_idx >= 0 and saw_on and not output[selected]:
                off_after_shutdown[selected] += 1
        elif typ == 'reply' and cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            need(tr['response'] == '0', 'Unexpected combined topology')
    need(saw_on and on_checks[1] >= 2 and not on_checks[2] and not on_checks[3],
         'Powered CH1 was not independently observed')
    need(all(off_after_shutdown[ch] >= 1 for ch in (1, 2, 3)) and not any(output.values()),
         'Final independent OFF readbacks missing')
    need(wire_writes[-1] == 'SYST:LOC' and wire_writes.count('SYST:LOC') == 1,
         'PSU must return to Local only after confirmed OFF')
    return {'steps': step, 'writes': len(wire_writes), 'queries': len(wire_queries),
            'off_checks': sum(x['command'] == 'CHAN:OUTP?' and x['response'] == '0'
                              for x in stage_reads),
            'scpi_errors': sum(x['command'] == 'SYST:ERR?' for x in stage_reads),
            'on_checks': on_checks[1]}


def audit(records):
    need(records and all(isinstance(x, dict) for x in records), 'Empty/invalid evidence')
    need(all(x.get('test_id') == 'R45EPOWERED' for x in records), 'Mixed evidence test IDs')
    ts = [number(x.get('monotonic_s')) for x in records]
    need(all(a <= b for a, b in zip(ts, ts[1:])), 'Nonmonotonic evidence timestamps')
    start = one([x for x in records if x.get('event') == 'start'], 'start')
    mode = start.get('mode')
    need(mode in ('cv', 'cc') and start.get('variant') == 'CH1_ONE_POWERED_STEP',
         'Unknown powered scenario')
    plan = {'cv': ('voltage', 'CV', .010, 40., .150),
            'cc': ('current', 'CC', .005, 11., .100)}[mode]
    kind, regulation, step_amount, ohms, current_limit = plan
    point = start.get('point', {})
    need(point.get('kind') == kind and point.get('mode') == regulation and
         all(abs(number(point.get(k)) - v) < 1e-9 for k, v in
             [('step', step_amount), ('expected_delta', step_amount), ('cr', ohms),
              ('voltage', 3.30), ('limit', current_limit)]), 'Wrong powered point')
    software = start.get('software', {})
    need(all(isinstance(software.get(k), str) and len(software[k]) == 64 for k in
             ('r45e_runner_sha256', 'r45e_stage_sha256', 'source_sha256'))
         and isinstance(software.get('sha'), str) and len(software['sha']) == 40,
         'Missing runner/stage/source provenance')
    need(isinstance(start.get('config_sha256'), str) and len(start['config_sha256']) == 64,
         'Bench SHA missing')
    pre = one([x for x in records if x.get('event') == 'pre_open_approvals'], 'initial gates')
    need(pre.get('gates') == ['BENCH', 'INITIAL'] and
         pre.get('config_sha256') == start['config_sha256'], 'Initial approvals not linked to bench')
    gates = [(i, e.get('gate')) for i, e in enumerate(records)
             if e.get('event') == 'operator_confirmation']
    need([x[1] for x in gates] == ['POWER', 'FINAL'], 'Powered/final gates missing')
    outcome = one([x for x in records if x.get('event') == 'outcome'], 'outcome')
    need(records[-1] is outcome and outcome.get('outcome') == 'PASS'
         and outcome.get('mode') == mode and outcome.get('physical_off_confirmed') is True
         and outcome.get('status') == 'HW NOT_REVIEWED', 'Wrong/missing final outcome')
    need(not [x for x in records if x.get('event') in ('stage_error', 'io_error',
                  'invalid_scpi_reply', 'shutdown_error_queue')], 'Stage error or link uncertainty')
    idn = [x.get('response', '') for x in records if x.get('event') == 'read'
           and x.get('command') == '*IDN?']
    need(len(idn) == 2 and all('Keithley' in x and '2231A-30-3' in x and
         x.rsplit(',', 1)[-1].strip() in ('1.04-0.01', '1.04-1.04') for x in idn),
         'Invalid PSU model or unsupported identity observation')
    wire = check_trace(records)
    need(wire['scpi_errors'] >= 50 and all(
         x.get('response', '').split(',', 1)[0].strip() == '0'
         for x in records if x.get('event') == 'read' and x.get('command') == 'SYST:ERR?'),
         'Keithley error queue nonzero')
    step_write = f'{"VOLT" if mode == "cv" else "CURR"}:STEP {step_amount:.3f}'
    step_up = 'VOLT:UP' if mode == 'cv' else 'CURR:UP'
    need([v[2] for v in wire['steps']] == [step_write, step_up]
         and wire['steps'][0][1] == wire['steps'][1][1] == 1
         and not wire['steps'][0][4] and wire['steps'][1][4],
         'Incorrect powered STEP selection, direction, or phase')
    step_i = one([i for i,x in enumerate(records) if x.get('event') == 'write'
                  and x.get('command') == step_up], 'STEP UP index')
    enable_i = one([i for i,x in enumerate(records) if x.get('event') == 'write'
                    and x.get('command') == 'CHAN:OUTP 1'], 'CH1 ON index')
    off_i = [i for i,x in enumerate(records) if x.get('event') == 'write'
             and x.get('command') == 'OUTP 0' and i > enable_i]
    local_i = one([i for i,x in enumerate(records) if x.get('event') == 'write'
                   and x.get('command') == 'SYST:LOC'], 'Local command')
    need(len(off_i) >= 1 and gates[0][0] < enable_i < step_i < off_i[0] < local_i < gates[1][0],
         'Power, STEP and OFF not in safety order')
    extra = [x for x in records if x.get('event') == 'powered_step_digital']
    d = one(extra, 'digital step')
    need(d.get('mode') == mode and d.get('kind') == kind and d.get('direction') == 'up'
         and abs(number(d.get('step')) - step_amount) < 1e-9 and
         abs(number(d.get('readback')) - (3.3 + step_amount if mode == 'cv'
                     else current_limit + step_amount)) < .00049,
         'Digital STEP metadata disagrees with expected readback')
    need(step_i < records.index(d) < off_i[0], 'Digital step event outside powered interval')
    k = matching_pairs(records, 'kel103_query', 'kel103_read')
    m = matching_pairs(records, 'dmm7510_query', 'dmm7510_read')
    kel_w = [(i,x['command']) for i,x in enumerate(records)
             if x.get('event') == 'kel103_write']
    need([cmd for _,cmd in kel_w] == [':FUNC CR', f':RES {ohms:g}OHM', ':INP ON', ':INP OFF']
         and gates[0][0] < kel_w[2][0] < enable_i and
         off_i[0] < kel_w[3][0] < local_i,
         'KEL103 configuration, enabling or shutdown order wrong')
    kel_input = [x['response'] for _,x in k if x['command'] == ':INP?']
    need(kel_input and kel_input[0] == 'OFF' and kel_input[-2:] == ['OFF', 'OFF']
         and 'ON' in kel_input, 'KEL103 input state unconfirmed')
    need(all(x['response'] == 'CR' for _,x in k if x['command'] == ':FUNC?')
         and all(abs(number(x['response'].removesuffix('OHM'))-ohms)<.001
                 for _,x in k if x['command'] == ':RES?'), 'KEL103 CR mismatch')
    dc = one([x for x in records if x.get('event') == 'dmm7510_configuration'],
             'DMM configuration')
    need(dc.get('outcome') == 'PASS' and dc.get('function') == 'DC voltage'
         and dc.get('terminals') == 'front' and dc.get('range_v') == 10
         and dc.get('nplc') == 1 and
         one([x for x in records if x.get('event') == 'dmm7510_command_set'],
             'DMM language').get('language') == 'SCPI', 'DMM setup mismatch')
    dmm_w = [(i,x['command']) for i,x in enumerate(records)
             if x.get('event') == 'dmm7510_write']
    need([cmd for _,cmd in dmm_w] == [
          '*RST', ':SENS:FUNC "VOLT:DC"', ':SENS:VOLT:RANG 10',
          ':SENS:VOLT:INP AUTO', ':SENS:VOLT:NPLC 1',
          ':SENS:VOLT:AZER ON', ':SENS:VOLT:AVER OFF']
         and all(i < gates[0][0] for i,cmd in dmm_w),
         'DMM configuration mutated during powered measurement')
    need(any('MODEL DMM7510' in x['response'] for _,x in m if x['command'] == '*IDN?'),
         'Missing DMM7510 identity')
    need(sum(x['command'] == ':READ?' for _,x in m) == 2,
         'DMM must provide exactly two independently sampled voltages')
    need(all(x['response'].split(',',1)[0] == '0' for _,x in m
             if x['command'] == ':SYST:ERR?'), 'Nonzero DMM error queue')
    snaps = [(i,x) for i,x in enumerate(records) if x.get('event') == 'powered_step_snapshot']
    need(len(snaps) == 2 and [x['label'] for _,x in snaps] == ['before','after']
         and enable_i < snaps[0][0] < step_i < snaps[1][0] < off_i[0],
         'Missing or wrongly ordered analog snapshots')
    fields = {'psu_v':'MEAS:VOLT?', 'psu_a':'MEAS:CURR?', 'psu_meas_w':'MEAS:POW?',
              'kel_v':':MEAS:VOLT?', 'kel_a':':MEAS:CURR?', 'kel_w':':MEAS:POW?',
              'dmm_v':':READ?'}
    for ind,(loc,s) in enumerate(snaps):
        need(s.get('mode') == regulation, 'Snapshot mode mismatch')
        floor = enable_i if ind == 0 else step_i
        for field, cmd in fields.items():
            provider = ('read' if field.startswith('psu_') else
                        'kel103_read' if field.startswith('kel_') else 'dmm7510_read')
            candidates = [(i,e) for i,e in enumerate(records[floor:loc], start=floor)
                          if e.get('event') == provider and e.get('command') == cmd]
            need(len(candidates) >= 1, f'Missing independent {field} measurement')
            unit = {'kel_v': 'V', 'kel_a': 'A', 'kel_w': 'W'}.get(field, '')
            actual = number(candidates[-1][1]['response'].removesuffix(unit))
            need(abs(number(s.get(field))-actual) < 1e-8,
                 f'Snapshot {field} does not match raw instrument observation')
        pu,pi,pw,ku,ki,kp,du = [number(s[x]) for x in fields]
        need(0 < pu <= 3.55 and 0 < ku <= 3.55 and 0 < du <= 3.55
             and 0 < pi <= .165 and 0 < ki <= .165 and
             0 < pw <= .60 and 0 < kp <= .60,
             'Analog snapshot exceeds approved bounds')
        need(abs(number(s.get('psu_derived_w'))-pu*pi)<1e-8
             and abs(pw-pu*pi)<.08 and abs(kp-ku*ki)<.045
             and abs(pw-kp)<.08 and abs(ki-pi)<.030
             and abs(du-pu)<.08 and abs(du-ku)<.08, 'Inconsistent instrument measurements')
        times = s.get('timestamps_monotonic_s', {})
        need(all(isinstance(times.get(k), (int,float)) and math.isfinite(times[k])
                 for k in ('psu_s','kel_s','dmm_s','power_s'))
             and times['psu_s'] < times['kel_s'] < times['dmm_s'] < times['power_s']
             and floor < loc and ts[floor] < times['psu_s'] < times['power_s'] < s['monotonic_s']+.01,
             'Snapshot samples misordered or timestamps fabricated')
        if mode == 'cv':
            need(abs(du-(3.3+ind*step_amount)) <= .040, 'CV wrong absolute voltage')
        else:
            exp = current_limit + ind*step_amount
            need(abs(ki-exp)<=.012 and abs(du-ohms*exp)<=.200,
                 'CC wrong absolute current/load voltage')
    before, after = snaps[0][1],snaps[1][1]
    dv = number(after['dmm_v'])-number(before['dmm_v'])
    di = number(after['kel_a'])-number(before['kel_a'])
    analog = (number(after['psu_v'])-number(before['psu_v']) if mode=='cv' else
              number(after['psu_a'])-number(before['psu_a']))
    if mode == 'cv': need(.003 <= dv <= .030, 'No physically measured CV step')
    else: need(.002 <= di <= .020 and .010 <= dv <= .150,
               'No independently measured CC step')
    delta = one([x for x in records if x.get('event') == 'powered_step_delta'], 'delta')
    need(delta.get('mode') == regulation and
         abs(number(delta.get('dmm_voltage_delta_v'))-dv)<1e-8 and
         abs(number(delta.get('kel_current_delta_a'))-di)<1e-8 and
         abs(number(delta.get('psu_analog_delta'))-analog)<1e-8,
         'Analog delta differs from raw before/after measurements')
    need(snaps[1][0] < records.index(delta) < off_i[0], 'Delta logged after shutdown')
    return dict(status='REVIEWABLE_PASS',scope=f'CH1 {regulation} one powered STEP; '
                'steady-state analog point, not calibration or transient proof',
                mode=mode, dmm_delta_v=dv, kel_current_delta_a=di,
                psu_analog_delta=analog, scpi_queries=wire['queries'],
                scpi_writes=wire['writes'],scpi_error_polls=wire['scpi_errors'],
                output_off_readbacks=wire['off_checks'],
                source_commit=software['sha'],source_dirty=software.get('dirty'),
                config_sha256=start['config_sha256'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        data = args.events.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        need(digest.lower() == args.expected_sha256.lower(), 'Original evidence SHA-256 mismatch')
        result = audit([json.loads(line) for line in data.decode('utf-8').splitlines()])
        result['evidence_sha256'] = digest
    except (AuditError, ValueError, KeyError, OSError, UnicodeError, TypeError, AttributeError) as exc:
        print(f'R4.5-E-B REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-E-B HARDWARE EVIDENCE REVIEWABLE PASS (single steady-state point)')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
