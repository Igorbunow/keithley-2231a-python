#!/usr/bin/env python3
"""Offline read-only H01/H02/H03_SINGLE_CVCC trace audit from JSONL.

Does not import instrument drivers or create connections. It validates actual
recorded wire-command order, not the incomplete command display in the terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def audit_single_records(records: list[dict]) -> dict:
    """Strict CH1-only powered trace audit, independent of terminal display."""
    issues = []
    if not records or {r.get('test_id') for r in records} != {'H03_SINGLE_CVCC'}:
        raise ValueError('Expected one H03_SINGLE_CVCC evidence stream')
    wire = [(index, r['trace']['command']) for index, r in enumerate(records)
            if r.get('event') == 'driver_trace' and
            r.get('trace', {}).get('event') in ('write', 'query')]
    commands = [command for _, command in wire]
    sends = [r.get('command') for r in records if r.get('event') in ('write', 'query')]
    # The driver may emit an internal identity/bootstrap query while the
    # adapter's outer query is still in progress. Adapter request logging
    # then precedes the true on-wire ordering. Compare exact command counts;
    # driver_trace itself is the authoritative chronological wire record.
    from collections import Counter
    if not commands or Counter(commands) != Counter(sends):
        issues.append('Driver trace command counts differ from adapter requests')
    for index, command in enumerate(commands):
        if command not in ('SYST:ERR?', 'SYST:LOC'):
            if index + 1 == len(commands) or commands[index + 1] != 'SYST:ERR?':
                issues.append(f'Missing SCPI error check after #{index+1}: {command}')
        if command == 'SYST:ERR?' and index and commands[index - 1] == command:
            issues.append(f'Adjacent duplicate error checks at #{index}, #{index+1}')
    if any(c in ('OUTP 1', '*CLS', '*RST') or c.startswith('OUTP:PARALLEL 1') or
           c.startswith('OUTP:SER 1') or c.startswith('OUTP:TRAC 1') for c in commands):
        issues.append('Forbidden global ON/reset/combined-mode write')
    error_checks = [r for r in records if r.get('event') == 'read' and r.get('command') == 'SYST:ERR?']
    if len(error_checks) != commands.count('SYST:ERR?'):
        issues.append('Mismatch between SCPI ERR responses and commands')
    for r in error_checks:
        try:
            code = int(r.get('response', '').split(',', 1)[0])
        except (TypeError, ValueError):
            issues.append('Malformed SCPI error queue evidence')
            continue
        if code != 0:
            issues.append(f'Unexpected SCPI error code: {code}')
    selected = 1
    enabled_wires = []
    for index, command in wire:
        if command.startswith('INST:NSEL '):
            selected = int(command[-1])
        if command == 'CHAN:OUTP 1':
            if selected != 1:
                issues.append(f'Output ON sent while CH{selected} was selected')
            enabled_wires.append(index)
    if len(enabled_wires) != 2:
        issues.append(f'Expected exactly two CH1 ON commands, got {len(enabled_wires)}')
    token_gates = [(i, r.get('mode')) for i, r in enumerate(records)
                   if r.get('event') == 'operator_gate' and r.get('authorized_output') == 'CH1 only']
    if [mode for _, mode in token_gates] != ['CV', 'CC']:
        issues.append('Missing ordered CV and CC output-enable operator gates')
    kel_ons = [i for i,r in enumerate(records)
               if r.get('event') == 'kel103_write' and r.get('command') == ':INP ON']
    kel_offs = [i for i,r in enumerate(records)
                if r.get('event') == 'kel103_write' and r.get('command') == ':INP OFF']
    if len(kel_ons) != 2 or len(kel_offs) != 2:
        issues.append('Expected two KEL INPUT ON/OFF cycles')
    if len(enabled_wires) == len(token_gates) == len(kel_ons) == len(kel_offs) == 2:
        for j, on_index in enumerate(enabled_wires):
            low = -1 if j == 0 else enabled_wires[j-1]
            if not (low < token_gates[j][0] < on_index and low < kel_ons[j] < on_index):
                issues.append(f'CH1 ON #{j+1} not preceded by the corresponding load/gate')
            next_on = enabled_wires[j+1] if j+1 < 2 else len(records)
            psu_off = [i for i,r in enumerate(records) if on_index < i < next_on and
                       r.get('event') == 'write' and r.get('command') in ('CHAN:OUTP 0','OUTP 0')]
            if not psu_off or not any(psu_off[0] < off < next_on for off in kel_offs):
                issues.append(f'CH1 ON #{j+1} lacks ordered PSU OFF -> KEL INPUT OFF')
    measurements = [r.get('mode') for r in records if r.get('event') == 'single_cvcc_measurement']
    if measurements != ['CV', 'CC']:
        issues.append(f'Missing ordered measured CV/CC points: {measurements}')
    # Independently recheck numeric evidence; do not trust the PASS flag alone.
    import math
    measured = [r for r in records if r.get('event') == 'single_cvcc_measurement']
    profile = [r for r in records if r.get('event') == 'single_cvcc_profile']
    if len(profile) != 1:
        issues.append('Missing unique validated single-CVCC profile evidence')
    for j,m in enumerate(measured):
        try:
            vals = [float(m[k]) for k in ('psu_voltage_v', 'psu_current_a', 'psu_power_w',
                                            'kel103_voltage_v', 'kel103_current_a', 'kel103_power_w')]
            psu_v,psu_i,psu_p,kel_v,kel_i,kel_p=vals
            if (any(not math.isfinite(v) or v < 0 for v in vals) or
                    abs(psu_p-psu_v*psu_i)>1e-8 or
                    abs(kel_p-kel_v*kel_i)>0.31 or
                    abs(psu_v-kel_v)>0.06 or abs(psu_i-kel_i)>0.03 or
                    (profile and (psu_p>float(profile[0]['max_power_w']) or
                                  kel_p>float(profile[0]['max_power_w']))) or
                    m.get('regulation') != m.get('mode') or m.get('channel') != 1):
                issues.append(f'Invalid or contradictory single-CVCC measurement #{j+1}')
        except (ValueError, TypeError, KeyError, OverflowError):
            issues.append(f'Malformed single-CVCC measurement #{j+1}')
    display_events = [(i,r) for i,r in enumerate(records)
                      if r.get('event') == 'operator_display_confirmation']
    measurement_positions = [i for i,r in enumerate(records)
                             if r.get('event') == 'single_cvcc_measurement']
    if len(display_events) != 2 or len(measurement_positions) != 2:
        issues.append('Missing ordered operator review of two measured points')
    else:
        for j, (idx, event) in enumerate(display_events):
            if (event.get('mode') != ('CV','CC')[j] or
                    idx <= measurement_positions[j] or
                    j >= len(enabled_wires) or
                    not (enabled_wires[j] < measurement_positions[j] < idx)):
                issues.append(f'Missing in-order display confirmation for point #{j+1}')
    core = [r for r in records if r.get('event') == 'query_outcome']
    final = [r for r in records if r.get('event') == 'outcome']
    if len(core) != 1 or core[0].get('outcome') != 'PASS':
        issues.append('Core did not record one PASS')
    if len(final) != 1 or final[0].get('outcome') != 'PASS' or final[0].get('physical_off_confirmed') is not True:
        issues.append('Missing final operator-confirmed OFF and final PASS')
    if len([r for r in records if r.get('event') == 'bench_connection_confirmation']) != 1:
        issues.append('Missing unique config-bound bench gate evidence')
    if len([r for r in records if r.get('event') == 'approval' and r.get('variant')=='h03_single_cvcc']) != 1:
        issues.append('Missing unique wiring gate evidence')
    return dict(test_id='H03_SINGLE_CVCC', status='FAIL' if issues else 'PASS',
                wire_commands=len(commands), error_polls=len(error_checks),
                observed_error_codes=[], aux_drift_events=sum(r.get('event')=='firmware_revision_drift' for r in records),
                historical_deviations=[], issues=issues)


def audit_full_h03_records(records: list[dict]) -> dict:
    """Audit all 6 powered H03 points, including both instruments and gates.

    A pre-fix full-H03 run with its old connection-only ON permission is
    explicitly marked historical; it must not be silently reported clean.
    """
    from collections import Counter
    import math

    issues = []
    caveats = []
    if not records or {r.get('test_id') for r in records} != {'H03'}:
        raise ValueError('Expected exactly one full H03 evidence stream')
    wire = [(i, r['trace']['command']) for i,r in enumerate(records)
            if r.get('event') == 'driver_trace'
            and r.get('trace', {}).get('event') in ('query','write')]
    cmds = [cmd for _,cmd in wire]
    sends = [r.get('command') for r in records if r.get('event') in ('write','query')]
    if not wire or Counter(cmds) != Counter(sends):
        issues.append('PSU wire sequence/counts disagree with adapter requests')
    for i,cmd in enumerate(cmds):
        if cmd == 'SYST:ERR?' and i and cmds[i-1] == cmd:
            issues.append(f'Adjacent duplicate SCPI error queries at command #{i+1}')
        if cmd not in ('SYST:ERR?', 'SYST:LOC'):
            if i + 1 >= len(cmds) or cmds[i+1] != 'SYST:ERR?':
                issues.append(f'Missing SCPI error check after command #{i+1} ({cmd})')
    if any(c in ('OUTP 1','*RST','*CLS') or
           c.startswith(('OUTP:SER 1','OUTP:PARALLEL 1','OUTP:TRAC 1')) for c in cmds):
        issues.append('Forbidden global output ON, combined mode or reset')
    error_reads = [r for r in records if r.get('event') == 'read' and r.get('command')=='SYST:ERR?']
    if len(error_reads) != cmds.count('SYST:ERR?'):
        issues.append('SYST:ERR? replies missing from wire evidence')
    for e in error_reads:
        try: code = int(e.get('response','').split(',',1)[0])
        except (TypeError, ValueError): code = None
        if code != 0:
            issues.append(f'Unexpected/malformed SCPI error reply: {e.get("response")}')
    intended = [(ch,mode) for ch in (1,2,3) for mode in ('CV','CC')]
    on = []; off = []
    selected=1
    for idx,(record_idx, cmd) in enumerate(wire):
        if cmd.startswith('INST:NSEL '):
            try: selected=int(cmd.split(' ',1)[1])
            except ValueError: issues.append('Malformed channel selection')
        if cmd == 'CHAN:OUTP 1': on.append((record_idx, selected))
        if cmd == 'CHAN:OUTP 0': off.append((record_idx,selected))
    if [ch for _,ch in on] != [ch for ch,_ in intended]:
        issues.append(f'Unexpected powered-channel sequence: {[ch for _,ch in on]}')
    if [ch for _,ch in off] != [ch for ch,_ in intended]:
        issues.append(f'Unexpected per-channel OFF sequence: {[ch for _,ch in off]}')
    kel_ons = [i for i,r in enumerate(records) if r.get('event')=='kel103_write' and r.get('command')==':INP ON']
    kel_offs = [i for i,r in enumerate(records) if r.get('event')=='kel103_write' and r.get('command')==':INP OFF']
    measurements = [(i,r) for i,r in enumerate(records) if r.get('event')=='measurement']
    displays = [(i,r) for i,r in enumerate(records) if r.get('event')=='operator_display_confirmation']
    on_gates = [(i,r) for i,r in enumerate(records)
                if r.get('event')=='operator_gate' and 'authorized_output' in r]
    connect_gates = [(i,r) for i,r in enumerate(records)
                     if r.get('event')=='operator_gate' and 'connection' in r]
    setups = [(i,r) for i,r in enumerate(records) if r.get('event')=='kel103_setup_verified']
    if any(len(seq)!=6 for seq in (on,off,kel_ons,kel_offs,measurements,displays,connect_gates,setups)):
        issues.append('Full H03 requires exactly 6 connected, measured and OFF-confirmed CV/CC cycles')
    if not on_gates:
        caveats.append('HISTORICAL_ON_GATE_SCOPE: full H03 used connection gates, not a separate PSU-enable gate')
    elif len(on_gates)!=6:
        issues.append('Incomplete explicit per-point PSU-enable gates')
    n=min(len(on),len(off),len(kel_ons),len(kel_offs),len(measurements),len(displays),
          len(connect_gates),len(setups),6)
    for j in range(n):
        channel, mode = intended[j]
        on_idx, on_ch = on[j]
        off_idx, off_ch = off[j]
        meas_idx, m = measurements[j]
        display_idx, display = displays[j]
        connect_idx, connect = connect_gates[j]
        setup_idx, setup = setups[j]
        if not (connect_idx < kel_ons[j] < setup_idx < on_idx < meas_idx < display_idx < off_idx < kel_offs[j]):
            issues.append(f'CH{channel} {mode}: invalid PSU/KEL ON, measurement, gate or OFF order')
        if j+1 < len(on) and not kel_offs[j] < on[j+1][0]:
            issues.append(f'CH{channel} {mode}: next output ON before load INPUT OFF')
        if on_ch != channel or off_ch != channel:
            issues.append(f'CH{channel} {mode}: wrong channel selected for ON/OFF')
        for element, label in ((connect,'connection'),(setup,'load setup'),(m,'measurement'),
                               (display,'display')):
            if element.get('channel')!=channel or (label!='display' and element.get('mode')!=mode):
                issues.append(f'CH{channel} {mode}: mismatched {label} event identity')
        if display.get('keithley_panel_mode')!=mode:
            issues.append(f'CH{channel} {mode}: display-regulation confirmation mismatch')
        if on_gates:
            if j >= len(on_gates):
                issues.append(f'CH{channel} {mode}: missing distinct PSU-enable gate')
            else:
                gi,g=on_gates[j]
                if not (setup_idx < gi < on_idx and g.get('channel')==channel
                        and g.get('mode')==mode
                        and g.get('authorized_output')==f'CH{channel} only'):
                    issues.append(f'CH{channel} {mode}: invalid PSU-enable gate')
        if (m.get('channel'),m.get('mode'))!=(channel,mode):
            issues.append(f'Measurement channel/mode mismatch at point #{j+1}')
            continue
        try:
            psu_v,psu_i,psu_p,kel_v,kel_i,kel_p,scpi_p = (
                float(m[field]) for field in ('keithley_voltage_v','keithley_current_a',
                    'keithley_power_api_w','kel103_voltage_v','kel103_current_a',
                    'kel103_power_w','keithley_power_scpi_w'))
            if any(not math.isfinite(v) or v < 0 for v in (psu_v,psu_i,psu_p,
                                                           kel_v,kel_i,kel_p,scpi_p)):
                raise ValueError('Nonfinite or negative measurement')
            if not (abs(psu_v-kel_v)<=0.06
                    and abs(psu_i-kel_i)<=0.03
                    and abs(psu_p-psu_v*psu_i)<1e-8
                    and abs(scpi_p-psu_p)<=0.31
                    and abs(kel_p-kel_v*kel_i)<=0.31):
                issues.append(f'CH{channel} {mode}: measured power/current/voltage mismatch')
            if m.get('keithley_status_mask',0) & 8 == 0:
                issues.append(f'CH{channel} {mode}: Keithley output-ON status missing')
        except (KeyError, TypeError, ValueError, OverflowError):
            issues.append(f'CH{channel} {mode}: malformed numeric measurement')
    status = [r for r in records if r.get('event')=='status_caveat']
    for e in status:
        expected_ques=1 if e.get('mode')=='CV' else 2 if e.get('mode')=='CC' else None
        if (e.get('channel')!=3 or e.get('operation_status')!=8 or
                e.get('questionable_status')!=expected_ques or
                e.get('library_regulation')!=e.get('mode')):
            issues.append('Unrecognized status caveat: do not silently accept register mismatch')
    if status:
        caveats.append(f'CH3_REGULATION_SOURCE: {len(status)} mode(s) inferred from QUES, not OPER')
    if len([r for r in records if r.get('event')=='status_unqualified']):
        issues.append('Unqualified mode-status event present')
    core=[r for r in records if r.get('event')=='query_outcome']
    final=[r for r in records if r.get('event')=='outcome']
    if len(core)!=1 or core[0].get('outcome')!='PASS':
        issues.append('H03 core did not PASS')
    if len(final)!=1 or final[0].get('outcome')!='PASS' or final[0].get('physical_off_confirmed') is not True:
        issues.append('Missing final PASS + physical OFF confirmation')
    if (sum(r.get('event')=='bench_connection_confirmation' for r in records)!=1 or
            sum(r.get('event')=='approval' for r in records)!=1):
        issues.append('Missing/duplicate physical and bench connection gates')
    return dict(test_id='H03',
                status='FAIL' if issues else 'PASS_WITH_REVIEW_NOTES' if caveats else 'PASS',
                wire_commands=len(cmds), error_polls=len(error_reads),
                observed_error_codes=[], aux_drift_events=sum(r.get('event')=='firmware_revision_drift' for r in records),
                historical_deviations=caveats, issues=issues)


def audit(path: Path, *, acknowledge_historical_h02_panel_poll: bool = False) -> dict:
    """Audit a saved run; optionally acknowledge exactly one known pre-fix H02 poll.

    The exception is limited to SYST:LOC -> operator panel approval ->
    SYST:REM -> SYST:ERR? -> SYST:ERR?; other deviations still fail.
    """
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('Empty evidence')
    stages = {item.get('test_id') for item in records}
    if stages == {'H03_SINGLE_CVCC'}:
        return audit_single_records(records)
    if stages == {'H03'}:
        return audit_full_h03_records(records)
    if len(stages) != 1 or next(iter(stages)) not in ('H01', 'H02'):
        raise ValueError('Expected one H01, H02, H03, or H03_SINGLE_CVCC evidence run')
    stage = next(iter(stages))
    wire = [(row_index, item['trace']['command']) for row_index, item in enumerate(records)
            if item.get('event') == 'driver_trace'
            and item.get('trace', {}).get('event') in ('write', 'query')]
    commands = [command for _, command in wire]
    sent = [e['command'] for e in records if e.get('event') in ('write', 'query') and 'command' in e]
    issues = []
    historical_deviations = []
    if not commands or commands != sent:
        issues.append('Recorded driver trace does not match adapter send records')
    for index, command in enumerate(commands):
        if command not in ('SYST:ERR?', 'SYST:LOC'):
            if index + 1 >= len(commands) or commands[index + 1] != 'SYST:ERR?':
                issues.append(f'No automatic error check after operation #{index + 1}: {command}')
        if command == 'SYST:ERR?' and index and commands[index - 1] == 'SYST:ERR?':
            is_panel_return = (stage == 'H02' and index >= 3
                               and commands[index - 3:index + 1]
                               == ['SYST:LOC', 'SYST:REM', 'SYST:ERR?', 'SYST:ERR?']
                               and any(row.get('event') == 'approval'
                                       and row.get('phase') == 'front_panel_max_voltage'
                                       for row in records[wire[index - 3][0]:wire[index - 2][0]]))
            if (acknowledge_historical_h02_panel_poll and is_panel_return
                    and not historical_deviations):
                historical_deviations.append(
                    f'KNOWN PRE-FIX H02 panel-return duplicate at commands '
                    f'#{index}, #{index + 1}; saved trace is NOT clean')
            else:
                issues.append(f'Duplicate adjacent error polls at commands #{index}, #{index + 1}')
    forbidden = {'OUTP 1', 'CHAN:OUTP 1', '*RST', '*CLS'}
    for index, command in enumerate(commands):
        if command in forbidden:
            issues.append(f'Unexpected state-changing command #{index + 1}: {command}')
    checks = [e for e in records if e.get('event') == 'read' and e.get('command') == 'SYST:ERR?']
    if len(checks) != commands.count('SYST:ERR?'):
        issues.append('Error queue response records do not match wire error queries')
    nonzero = []
    for entry in checks:
        try:
            code = int(entry['response'].split(',', 1)[0])
        except (ValueError, AttributeError, KeyError):
            issues.append('Malformed recorded error queue response')
            continue
        if code != 0:
            nonzero.append(code)
    expected = [e for e in records if e.get('event') == 'expected_instrument_rejection']
    if nonzero != [120] * len(expected):
        issues.append('Nonzero SCPI errors do not match recorded expected rejections')
    if stage == 'H01' and nonzero:
        issues.append('H01 must not create expected SCPI errors')
    outcome = [e for e in records if e.get('event') == 'query_outcome']
    if len(outcome) != 1 or outcome[0].get('outcome') != 'PASS':
        issues.append('Missing or non-PASS query outcome')
    fw = [e for e in records if e.get('event') == 'firmware_revision_drift']
    status = ('FAIL' if issues else
              'HISTORICAL_DEVIATION_REVIEWED' if historical_deviations else 'PASS')
    return dict(test_id=stage, status=status,
                wire_commands=len(commands), error_polls=len(checks),
                observed_error_codes=nonzero, aux_drift_events=len(fw),
                historical_deviations=historical_deviations, issues=issues)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, nargs='+', help='H01/H02/H03/H03_SINGLE_CVCC events.jsonl files')
    parser.add_argument('--acknowledge-historical-h02-panel-poll', action='store_true',
                        help='Only for saved pre-fix FULL H02: explicitly acknowledge the '
                             'one known adjacent SYST:ERR? after manual panel SYST:REM; '
                             'reports HISTORICAL_DEVIATION_REVIEWED, never clean PASS')
    args = parser.parse_args(argv)
    result = 0
    for path in args.evidence:
        try:
            summary = audit(path, acknowledge_historical_h02_panel_poll=
                            args.acknowledge_historical_h02_panel_poll)
            print(f'{path}: {summary["status"]} {summary["test_id"]} '
                  f'wire={summary["wire_commands"]} error_polls={summary["error_polls"]} '
                  f'nonzero={summary["observed_error_codes"]} '
                  f'firmware_drift={summary["aux_drift_events"]}')
            for deviation in summary['historical_deviations']:
                print(f'  REVIEWED HISTORICAL DEVIATION: {deviation}')
            for issue in summary['issues']:
                print(f'  FAIL: {issue}')
            if summary['issues']:
                result = 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f'{path}: FAIL (invalid evidence: {type(exc).__name__})')
            result = 1
    return result


if __name__ == '__main__':
    raise SystemExit(main())
