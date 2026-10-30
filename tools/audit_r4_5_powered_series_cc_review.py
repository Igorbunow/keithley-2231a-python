#!/usr/bin/env python3
"""Additional CC-specific independent audit of R4.5-E-C powered five-point JSONL.

Read-only. First runs the complete R4.5-E-C series auditor; additionally binds
physical CURR? replies to each on-wire mutation and independently verifies the
KEL current segment metadata, resistance, and return observations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from audit_r4_5_powered_series_evidence import audit, AuditError, need, number

LEVELS = (0.100, 0.105, 0.110, 0.105, 0.100)
DIRECTIONS = ('CURR:UP', 'CURR:UP', 'CURR:DOWN', 'CURR:DOWN')


def verify_cc_wire(records):
    traces = [x['trace'] for x in records if x.get('event') == 'driver_trace']
    selected = 1
    enabled = False
    was_enabled = False
    moves = []
    ch1_curr_reads = []
    ch1_step_reads = []
    after_enable_writes = []
    for index, tr in enumerate(traces):
        kind, command = tr.get('event'), tr.get('command')
        if kind == 'write':
            if command.startswith('INST:NSEL '):
                selected = int(command.rsplit(' ', 1)[-1])
            elif command == 'CHAN:OUTP 1':
                need(selected == 1 and not was_enabled, 'Incorrect CH1 enable')
                enabled = was_enabled = True
            elif command == 'OUTP 0':
                enabled = False
            if was_enabled and enabled and command != 'CHAN:OUTP 1':
                after_enable_writes.append(command)
            if command in DIRECTIONS:
                moves.append((index, command, selected, enabled))
        elif kind == 'reply':
            if selected == 1 and command in ('CURR?', 'CURR:STEP?'):
                entry = (index, number(tr.get('response')))
                (ch1_curr_reads if command == 'CURR?' else ch1_step_reads).append(entry)
    need(was_enabled and len(moves) == 4, 'Missing powered CC moves')
    need([m[1] for m in moves] == list(DIRECTIONS) and
         all(m[2] == 1 and m[3] for m in moves), 'Wrong channel/order/power for CC moves')
    allowed_while_on = {'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3',
                        'CURR:UP', 'CURR:DOWN', 'SYST:REM'}
    need(all(c in allowed_while_on for c in after_enable_writes),
         'Unexpected configuration/output write while powered')
    need(len(ch1_step_reads) >= 4 and all(abs(v - .005) < 1e-9
         for _,v in ch1_step_reads), 'STEP register changed during CC series')
    for n,(idx,cmd,channel,powered) in enumerate(moves):
        before = [v for j,v in ch1_curr_reads if j < idx]
        after = [v for j,v in ch1_curr_reads if idx < j and
                 (n == 3 or j < moves[n+1][0])]
        step_before = [v for j,v in ch1_step_reads if j < idx]
        need(before and after and step_before,
             f'CC step {n+1} lacks real CURR?/CURR:STEP? wire replies')
        need(abs(before[-1] - LEVELS[n]) < 1e-9 and
             abs(after[0] - LEVELS[n+1]) < 1e-9 and
             abs(step_before[-1] - .005) < 1e-9,
             f'CC step {n+1} does not match physical register readback')
        # All subsequent powered CH1 CURR? reads in this interval must remain
        # at the new level, not just the first readback after the mutation.
        need(all(abs(v - LEVELS[n+1]) < 1e-9 for v in after),
             f'CC step {n+1} drifted before the next powered mutation')
    return {'physical_digital_levels_a':list(LEVELS),
            'wire_step_readbacks':len(ch1_step_reads),
            'powered_mutations':list(DIRECTIONS)}


def verify_cc_physics(records):
    snaps = [x for x in records if x.get('event') == 'powered_step_snapshot']
    segments = [x for x in records if x.get('event') == 'powered_series_segment']
    final = [x for x in records if x.get('event') == 'powered_series_return']
    need(len(snaps) == 5 and len(segments) == 4 and len(final) == 1,
         'CC five-point records incomplete')
    kel = [number(s['kel_a']) for s in snaps]
    dmm = [number(s['dmm_v']) for s in snaps]
    psu = [number(s['psu_a']) for s in snaps]
    resistance = []
    bias_ma = []
    voltage_bias_mv = []
    power_difference_mw = []
    for s in snaps:
        u, i = number(s['kel_v']), number(s['kel_a'])
        need(i > 0, 'KEL current not positive')
        equivalent = u / i
        need(10.7 <= equivalent <= 11.3,
             'KEL measured U/I inconsistent with programmed CR 11 ohm')
        resistance.append(equivalent)
        bias_ma.append((number(s['kel_a']) - number(s['psu_a'])) * 1000)
        voltage_bias_mv.append((number(s['psu_v']) - number(s['dmm_v'])) * 1000)
        power_difference_mw.append((number(s['kel_w']) - number(s['psu_meas_w'])) * 1000)
    kel_changes = []
    dmm_changes = []
    for j, segment in enumerate(segments):
        sign = 1 if j < 2 else -1
        delta_i = sign * (kel[j+1] - kel[j])
        delta_v = sign * (dmm[j+1] - dmm[j])
        kel_changes.append(delta_i)
        dmm_changes.append(delta_v)
        need(abs(number(segment.get('kel_signed_delta_a')) - delta_i) < 1e-10,
             f'CC segment {j+1}: summary KEL current delta differs from samples')
        need(abs(number(segment.get('dmm_signed_delta_v')) - delta_v) < 1e-10,
             f'CC segment {j+1}: summary DMM voltage delta differs from samples')
        need(0.0035 <= delta_i <= 0.0065 and
             0.035 <= delta_v <= 0.075 and
             abs(delta_v - 11 * delta_i) < 0.010,
             f'CC segment {j+1}: current/voltage change physically inconsistent')
    need(abs(kel[-1]-kel[0]) < .002 and abs(dmm[-1]-dmm[0]) < .035,
         'CC did not return to its initial physical point')
    need(abs(number(final[0]['kel_return_error_a']) - abs(kel[-1]-kel[0])) < 1e-10,
         'CC return current metadata inconsistent')
    return {'dmm_v':dmm, 'kel_a':kel,
            'signed_kel_segments_ma':[x*1000 for x in kel_changes],
            'signed_dmm_segments_mv':[x*1000 for x in dmm_changes],
            'kel_equivalent_ohm':resistance,
            'kel_minus_psu_current_ma':bias_ma,
            'psu_minus_dmm_voltage_mv':voltage_bias_mv,
            'kel_minus_psu_power_mw':power_difference_mw,
            'kel_return_error_ma':abs(kel[-1]-kel[0])*1000,
            'dmm_return_error_mv':abs(dmm[-1]-dmm[0])*1000,
            'dmm_repeated_105ma_error_mv':abs(dmm[1]-dmm[3])*1000}


def audit_cc(records):
    result = audit(records)
    need(result['mode'] == 'cc', 'CC review must not accept a CV run')
    # This is the separately gated CC run built on top of the previously
    # reviewed five-point CV stage; source SHA may change only in later audits.
    need(result['source_commit'] == '75b6aa0013dc9dea082de33c82397e8b818a6c9e',
         'Source commit differs from observed CC hardware provenance')
    wire = verify_cc_wire(records)
    physics = verify_cc_physics(records)
    return {'status':'REVIEWABLE_PASS','scope':result['scope'],
            'source_commit':result['source_commit'],
            'source_dirty':result['source_dirty'],
            'config_sha256':result['config_sha256'],
            'scpi_queries':result['scpi_queries'],
            'scpi_writes':result['scpi_writes'],
            'scpi_error_polls':result['scpi_error_polls'],
            'off_readbacks':result['off_readbacks'],
            'wire':wire,'physics':physics}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        data = args.events.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        need(digest.lower() == args.expected_sha256.lower(), 'Original evidence SHA mismatch')
        records = [json.loads(line) for line in data.decode('utf-8').splitlines()]
        result = audit_cc(records)
        result['evidence_sha256'] = digest
    except (AuditError, OSError, ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, OverflowError, ZeroDivisionError) as exc:
        print(f'R4.5-E-C CC REVIEW REJECTED: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-E-C CC HARDWARE REVIEWABLE PASS (five powered steady-state points)')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
