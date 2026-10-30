"""Independent OFF-only matrix evidence review (does not prove analog behavior).

Checks original raw JSONL, physical SCPI wire trace, per-action query replies,
ordered case coverage, error queue, output OFF, provenance and operator gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from r4_5_step_batch_stage import cases


class AuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def _numeric(value):
    try: number = float(value)
    except (TypeError, ValueError, OverflowError): raise AuditError('Non-numeric readback')
    require(math.isfinite(number), 'Nonfinite readback')
    return number


def audit(records):
    require(records, 'Empty evidence')
    ids = [x.get('test_id') for x in records]
    require(len(set(ids)) == 1 and ids[0] in (None, 'R45STEPBATCH'),
            'Unexpected mixed test IDs')
    # Real evidence has monotonic timestamps; synthetic UART fixtures may
    # deliberately omit them. Never allow partial timestamps or reversal.
    has_mono = ['monotonic_s' in x for x in records]
    if any(has_mono):
        require(all(has_mono) and all(type(x['monotonic_s']) in (int, float)
                    and math.isfinite(x['monotonic_s']) for x in records)
                and all(a['monotonic_s'] <= b['monotonic_s']
                        for a, b in zip(records, records[1:])),
                'Missing or non-monotonic evidence event timestamps')
    start = [x for x in records if x.get('event')=='start']
    require(len(start)==1 and start[0].get('phase')=='STEP_BATCH_OFF', 'Wrong/missing batch start')
    matrix = start[0].get('matrix')
    plan = cases(matrix)
    require(start[0].get('planned_cases')==len(plan), 'Start count mismatch')
    approvals = [x for x in records if x.get('event')=='pre_open_approvals']
    require(len(approvals)==1 and approvals[0].get('approved_before_evidence_open') is True
            and approvals[0].get('gates')==['BENCH','INITIAL'], 'Missing pre-open approval')
    require(approvals[0].get('config_sha256')==start[0].get('config_sha256'),
            'Bench hash differs between gates/start')
    require(isinstance(start[0].get('config_sha256'),str)
            and len(start[0]['config_sha256'])==64, 'Missing bench SHA')
    for key in ('r45_batch_runner_sha256','r45_batch_stage_sha256'):
        require(len(start[0].get('software',{}).get(key,''))==64, f'Missing {key} provenance')
    require(len([x for x in records if x.get('event')=='psu_identity'
                 and x.get('model')=='2231A-30-3'])==1, 'Unexpected PSU model')
    gates = [x.get('gate') for x in records if x.get('event')=='operator_confirmation']
    require(gates==['PROFILE','BATCH','FINAL_PHYSICAL','FINAL'], 'Wrong operator gate order')
    first_index = lambda event, pred=lambda x: True: next(
        (i for i,x in enumerate(records) if x.get('event')==event and pred(x)), None)
    profile_gate_idx = first_index('operator_confirmation',lambda x:x.get('gate')=='PROFILE')
    batch_gate_idx = first_index('operator_confirmation',lambda x:x.get('gate')=='BATCH')
    first_profile_write = first_index('write',lambda x:x.get('command')=='OUTP 0')
    first_step_write = first_index('write',lambda x:':STEP ' in x.get('command',''))
    final_gate_idx = first_index('operator_confirmation',lambda x:x.get('gate')=='FINAL')
    last_local_idx = first_index('write',lambda x:x.get('command')=='SYST:LOC')
    require(all(v is not None for v in (profile_gate_idx,batch_gate_idx,first_profile_write,
                first_step_write,final_gate_idx,last_local_idx)) and
            profile_gate_idx<first_profile_write<batch_gate_idx<first_step_write
            <last_local_idx<final_gate_idx, 'Physical gate/SCPI event ordering violated')
    outcome = [x for x in records if x.get('event')=='outcome']
    require(len(outcome)==1 and outcome[0].get('outcome')=='PASS'
            and outcome[0].get('phase')=='STEP_BATCH_OFF'
            and outcome[0].get('matrix')==matrix
            and outcome[0].get('planned_cases')==len(plan)
            and outcome[0].get('completed_cases')==len(plan)
            and outcome[0].get('link_known') is True
            and outcome[0].get('digital_off_confirmed') is True
            and outcome[0].get('physical_off_confirmed') is True,
            'Incomplete or unconfirmed batch outcome')
    require(not [x for x in records if x.get('event') in (
        'stage_error','io_error','invalid_scpi_reply')],
        'Unexpected stage error or link anomaly')
    # Source firmware version may be either previously observed Aux value.
    # It never replaces instrument ID/model checks or SCPI state assertions.
    raw_idn = [x['response'] for x in records if x.get('event')=='read'
               and x.get('command')=='*IDN?']
    versions = [v.split(',')[-1].strip() for v in raw_idn]
    require(len(raw_idn)==2 and all('2231A-30-3' in x and 'Keithley' in x
            for x in raw_idn) and all(v in ('1.04-0.01','1.04-1.04') for v in versions) and
            any(x.get('firmware')==versions[0] for x in records if x.get('event')=='psu_identity'),
            'Missing/unrecognized PSU model or firmware observation')
    drifts = [x for x in records if x.get('event')=='firmware_revision_drift']
    require(all(x.get('before') in versions and x.get('after') in versions and
                x.get('before')!=x.get('after') for x in drifts),
            'Unrecognized firmware metadata drift')

    queries = [x for x in records if x.get('event')=='query']
    reads = [x for x in records if x.get('event')=='read']
    require(len(queries)==len(reads) and len(queries)>len(plan)*20,
            'SCPI queries/read replies mismatch')
    require(Counter(x['command'] for x in queries)==Counter(x['command'] for x in reads),
            'SCPI query/reply coverage corrupted')
    errors = [x for x in reads if x['command']=='SYST:ERR?']
    require(len(errors)>len(plan)*10 and all(
        x.get('response','').split(',',1)[0].strip()=='0'
        and len(x.get('response','').split(',',1))==2
        and bool(x['response'].split(',',1)[1].strip().strip('"')) for x in errors),
        'Non-zero/malformed SCPI error queue')
    output_reads = [x for x in reads if x['command']=='CHAN:OUTP?']
    require(len(output_reads)>=len(plan)*12 and all(x.get('response')=='0'
            for x in output_reads), 'Missing OFF readback / output ON')
    require(all(x.get('response')=='0' for x in reads if x['command'] in
                ('OUTP:SER?','OUTP:PARALLEL?','OUTP:TRAC?')),
            'Combined topology was enabled')
    traces = [x['trace'] for x in records if x.get('event')=='driver_trace']
    wire = [x['command'] for x in traces if x.get('event') in ('query','write')]
    replies = [x for x in traces if x.get('event')=='reply']
    require(len(replies)==len(reads), 'Physical trace has missing replies')
    require(Counter(x['command'] for x in records if x.get('event')=='write')==
            Counter(x['command'] for x in traces if x.get('event')=='write'),
            'Physical writes differ from stage write evidence')
    require(Counter(x['command'] for x in records if x.get('event')=='query')==
            Counter(x['command'] for x in traces if x.get('event')=='query'),
            'Physical queries differ from stage query evidence')
    require([x['command'] for x in replies]==[x['command'] for x in reads],
            'Physical reply sequence differs from stage reads')
    def same_reply(physical, stage):
        if physical.get('response') == stage.get('response'):
            return True
        if physical.get('command') != '*IDN?' or stage.get('command') != '*IDN?':
            return False
        # The virtual UART fixture exposes a synthetic serial to the stage,
        # while driver_trace redacts it. Never weaken model or firmware checks.
        a = [v.strip() for v in physical.get('response', '').split(',')]
        b = [v.strip() for v in stage.get('response', '').split(',')]
        return (len(a) == len(b) == 4 and
                a[0] == b[0] and a[1] == b[1] and a[3] == b[3] and
                '[REDACTED]' in (a[2], b[2]))
    require(all(same_reply(x, y) for x, y in zip(replies, reads)),
            'Physical wire reply payload differs from stage readback')
    # A SCPI reply must belong to the immediately preceding physical query.
    # Matching only command multiset or ordered read labels would miss a
    # duplicated, delayed or silently rewritten response on the wire.
    outstanding = None
    for trace in traces:
        if trace.get('event') == 'query':
            require(outstanding is None, 'Next physical query sent before previous reply')
            outstanding = trace.get('command')
        elif trace.get('event') == 'reply':
            require(outstanding == trace.get('command'),
                    'Physical reply is unsolicited or paired with wrong query')
            outstanding = None
        elif trace.get('event') == 'write':
            require(outstanding is None, 'Physical write interleaved with pending query')
        else:
            raise AuditError('Unrecognized driver trace event')
    require(outstanding is None, 'Physical query without matching reply')
    require(wire and wire[-1]=='SYST:LOC' and wire.count('SYST:LOC')==1,
            'SYST:LOC is not the final physical SCPI command')
    forbidden = ('OUTP 1','CHAN:OUTP 1','*RST','*CLS','*TRG','*SAV','*RCL')
    require(not any(c in forbidden or ':TRIG' in c for c in wire),
            'Dangerous/unapproved physical SCPI command')
    # Independently reconstruct target selection from actual wire writes.
    selected = None
    physically_mutated = []
    for command in wire:
        if command.startswith('INST:NSEL '):
            selected = int(command[-1])
        if (command.startswith(('CURR:STEP ', 'VOLT:STEP '))
                or command in ('CURR:UP','CURR:DOWN','VOLT:UP','VOLT:DOWN')):
            physically_mutated.append((selected,command))
    expected_mutations = []
    for ch, kind, step in plan:
        prefix = 'CURR' if kind=='current' else 'VOLT'
        expected_mutations.extend([(ch,f'{prefix}:STEP {step:.3f}'),
            (ch,f'{prefix}:UP'),(ch,f'{prefix}:UP'),
            (ch,f'{prefix}:DOWN'),(ch,f'{prefix}:DOWN')])
    require(physically_mutated==expected_mutations,
            'Physical STEP commands, selected channels or order mismatch')
    # For every command, check a real instrument query gives the expected
    # updated register value before the next mutation (not just stage results).
    mutation_indices = [i for i,c in enumerate(wire) if c.startswith((
        'CURR:STEP ', 'VOLT:STEP ')) or c in ('CURR:UP','CURR:DOWN','VOLT:UP','VOLT:DOWN')]
    reply_by_query_position = {}
    query_cursor = iter(reads)
    for i,cmd in enumerate(wire):
        if cmd.endswith('?'):
            try: r = next(query_cursor)
            except StopIteration: raise AuditError('Wire query without read')
            require(r['command']==cmd, 'Wire read pairing mismatch')
            reply_by_query_position[i]=r.get('response')
    try: next(query_cursor)
    except StopIteration: pass
    else: raise AuditError('Unmatched read records')
    for index, (ch,kind,step) in enumerate(plan):
        prefix = 'CURR' if kind=='current' else 'VOLT'
        initial = (.100 if ch==1 else .050) if kind=='current' else (3.30 if ch==1 else 1.00)
        expected_vals = (initial+step, initial+2*step, initial+step, initial)
        for offset in range(5):
            mutation_id = 5*index+offset
            begin = mutation_indices[mutation_id]
            end = mutation_indices[mutation_id+1] if mutation_id+1<len(mutation_indices) else len(wire)
            wanted_query = prefix+(':STEP?' if offset==0 else '?')
            candidates = [reply_by_query_position[p] for p in range(begin+1,end)
                          if wire[p]==wanted_query]
            require(bool(candidates), 'No immediate hardware readback after STEP operation')
            expected = step if offset==0 else expected_vals[offset-1]
            require(abs(_numeric(candidates[0])-expected)<.00049,
                    f'Physical readback mismatch CH{ch}/{kind}/{step}/{offset}')

    case_events = [x for x in records if x.get('event')=='batch_case_result']
    require(len(case_events)==len(plan), 'Missing case results')
    for idx,(item, (ch, kind, step)) in enumerate(zip(case_events,plan)):
        initial = (.100 if ch==1 else .050) if kind=='current' else (3.30 if ch==1 else 1.00)
        expected = [initial+step,initial+2*step,initial+step,initial]
        require(item.get('index')==idx+1 and item.get('channel')==ch
                and item.get('kind')==kind and item.get('outcome')=='PASS'
                and abs(_numeric(item.get('increment'))-step)<1e-9
                and abs(_numeric(item.get('initial'))-initial)<.00049
                and abs(_numeric(item.get('readback_step'))-step)<.00049,
                'Case metadata inconsistent')
        steps = item.get('steps',[])
        require(len(steps)==4 and [v.get('direction') for v in steps]
                ==['up','up','down','down'] and all(
                abs(_numeric(v.get('setpoint'))-want)<.00049 for v,want in zip(steps,expected)),
                'Case sequence values inconsistent')
    aggregate = [x for x in records if x.get('event')=='batch_results']
    require(len(aggregate)==1 and aggregate[0].get('cases')==[
        {k:v for k,v in event.items() if k not in ('event','utc','monotonic_s','test_id')}
        for event in case_events] and aggregate[0].get('case_count')==len(plan)
        and aggregate[0].get('analog_measurement')=='NOT_RUN',
        'Aggregated case results inconsistent')
    encoded = json.dumps(aggregate[0]['cases'], sort_keys=True,
                         separators=(',',':')).encode('ascii')
    require(hashlib.sha256(encoded).hexdigest()==aggregate[0].get('results_sha256'),
            'Results digest mismatch')
    return {'status':'REVIEWABLE_PASS', 'scope':'OFF-only digital; analog NOT_RUN',
            'matrix':matrix,'cases':len(plan),'queries':len(queries),
            'writes':sum(x.get('event')=='write' for x in records),
            'error_polls':len(errors),'off_readbacks':len(output_reads),
            'firmware_observations':sorted(set(versions)),
            'source_commit':start[0]['software'].get('sha'),
            'config_sha256':start[0]['config_sha256']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path)
    parser.add_argument('--expected-sha256',required=True)
    args = parser.parse_args(argv)
    try:
        data=args.events.read_bytes()
        digest=hashlib.sha256(data).hexdigest()
        require(digest.lower()==args.expected_sha256.lower(), 'Original evidence SHA mismatch')
        results=audit([json.loads(line) for line in data.decode('utf-8').splitlines()])
        results['evidence_sha256']=digest
    except (AuditError, ValueError, OSError, UnicodeError, KeyError, TypeError) as exc:
        print(f'REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-D BATCH EVIDENCE REVIEWABLE PASS (not analog proof)')
    print(json.dumps(results,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
