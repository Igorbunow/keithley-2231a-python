#!/usr/bin/env python3
"""Offline read-only H01/H02/H03/H04P trace audit from JSONL.

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


def audit_aux_revision_records(records: list[dict]) -> dict:
    """Review the independent OFF-only channel/version experiment from saved events."""
    from collections import Counter
    import re
    issues = []
    if not records or {r.get('test_id') for r in records} != {'H01_AUX_REVISION'}:
        raise ValueError('Expected H01_AUX_REVISION evidence only')
    wire = [(i,r['trace']['command']) for i,r in enumerate(records)
            if r.get('event') == 'driver_trace' and r.get('trace',{}).get('event') in ('write','query')]
    cmds = [c for _,c in wire]
    sent = [r.get('command') for r in records if r.get('event') in ('query','write')]
    if not cmds or Counter(cmds) != Counter(sent):
        issues.append('Driver wire trace and adapter send records differ')
    for i,command in enumerate(cmds):
        if command not in ('SYST:ERR?', 'SYST:LOC') and (i+1 >= len(cmds) or cmds[i+1] != 'SYST:ERR?'):
            issues.append(f'Missing automatic SYST:ERR? after command #{i+1}')
        if command == 'SYST:ERR?' and i and cmds[i-1] == command:
            issues.append(f'Adjacent SYST:ERR? at command #{i+1}')
        if '?' not in command and command not in ('SYST:REM','SYST:LOC') and not re.fullmatch(r'INST:NSEL [123]', command):
            issues.append(f'Forbidden state-changing command #{i+1}: {command}')
    checks = [r for r in records if r.get('event')=='read' and r.get('command')=='SYST:ERR?']
    if len(checks) != cmds.count('SYST:ERR?'):
        issues.append('Error queue evidence count differs from wire')
    for r in checks:
        if not re.fullmatch(r'0\s*,.+', str(r.get('response',''))):
            issues.append('Unexpected instrument SCPI error or malformed reply')
    for r in records:
        if r.get('event') != 'read': continue
        command,answer = r.get('command'),str(r.get('response','')).strip()
        if command in ('CHAN:OUTP?', 'OUTP:STAT:ALL?') and answer != '0':
            issues.append(f'Output not OFF in readback: {command} {answer!r}')
        if re.fullmatch(r'STAT:OPER:INST:ISUM[123]:COND\?', command or ''):
            if not re.fullmatch(r'[0-9]{1,5}',answer) or int(answer)>65535 or int(answer)&8:
                issues.append(f'Invalid or active output status in {command}')
        if command == '*IDN?' and '[REDACTED]' not in answer:
            issues.append('Identity serial was not redacted')
    plan = (1,1,2,2,3,3,1,3,2,1)
    samples = [(i,r) for i,r in enumerate(records) if r.get('event')=='aux_channel_observation']
    if [s.get('selected_channel') for _,s in samples] != list(plan) or len(samples)!=len(plan):
        issues.append('Missing or out-of-order channel observations')
    for j,(row_idx,row) in enumerate(samples):
        if (row.get('index') != j or row.get('output_off_verified') is not True or
                not isinstance(row.get('revision'),str) or not row['revision']):
            issues.append(f'Invalid channel observation #{j+1}')
        prior_idn = next((r for r in reversed(records[:row_idx]) if r.get('event')=='read' and r.get('command')=='*IDN?'), None)
        prior_sel = next((r for r in reversed(records[:row_idx]) if r.get('event')=='read' and r.get('command')=='INST:NSEL?'), None)
        if (prior_idn is None or prior_sel is None or
                prior_sel.get('response') != str(row.get('selected_channel')) or
                not str(prior_idn.get('response','')).strip().endswith(', '+row.get('revision',''))):
            issues.append(f'Observation #{j+1} does not match verified selection/identity')
    if len([r for r in records if r.get('event')=='read' and r.get('command')=='CHAN:OUTP?']) < 16:
        issues.append('Missing initial/final CHAN:OUTP? OFF sweeps')
    if len([r for r in records if r.get('event')=='bench_connection_confirmation']) != 1 or len([r for r in records if r.get('event')=='approval']) != 1:
        issues.append('Missing or repeated bench/physical gate')
    core = [r for r in records if r.get('event')=='query_outcome']
    final = [r for r in records if r.get('event')=='outcome']
    if len(core)!=1 or core[0].get('outcome')!='PASS' or len(final)!=1 or final[0].get('outcome')!='PASS' or final[0].get('physical_off_confirmed') is not True:
        issues.append('Missing verified PASS and final physical OFF confirmation')
    return dict(test_id='H01_AUX_REVISION', status='FAIL' if issues else 'PASS',
                wire_commands=len(cmds), error_polls=len(checks), observed_error_codes=[],
                aux_drift_events=sum(r.get('event')=='firmware_revision_drift' for r in records),
                historical_deviations=[], issues=issues)



def audit_h04p_records(records: list[dict]) -> dict:
    """Independently audit H04P wire order, guards and 21 U/I/P points.

    Offline only: no instrument imports. Driver trace is the SCPI wire order;
    both vendors' actual writes are ordered using the shared JSONL timeline.
    """
    from collections import Counter
    import math
    import re

    if not records or {r.get('test_id') for r in records} != {'H04P'}:
        raise ValueError('Expected exactly one H04P evidence stream')
    issues, caveats = [], []
    wire = [(idx, r['trace']['command']) for idx, r in enumerate(records)
            if r.get('event') == 'driver_trace' and
            r.get('trace', {}).get('event') in ('write', 'query')]
    cmds = [cmd for _, cmd in wire]
    sent = [r['command'] for r in records if r.get('event') in ('write', 'query')]
    if not cmds or Counter(cmds) != Counter(sent):
        issues.append('PSU driver wire trace disagrees with adapter requests')
    trace_reply = [(r['trace'].get('command'), r['trace'].get('response'))
                   for r in records if r.get('event') == 'driver_trace' and
                   r.get('trace', {}).get('event') == 'reply']
    evidence_reply = [(r.get('command'), r.get('response')) for r in records
                      if r.get('event') == 'read']
    if not trace_reply or Counter(trace_reply) != Counter(evidence_reply):
        issues.append('PSU driver replies disagree with adapter read evidence')
    if [c for c in cmds if c.endswith('?')] != [c for c, _ in trace_reply]:
        issues.append('PSU wire queries and replies do not match in chronological order')

    allowed_write = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT 0|'
        r'VOLT (?:0|2\.20|3\.00|3\.30|4\.40|4\.50|4\.51)|'
        r'CURR (?:0|0\.100|0\.250|0\.500)|'
        r'VOLT:LIM 4\.50|VOLT:LIM:STAT 1)$')
    allowed_query = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|'
        r'OUTP:TIM:(?:STAT|DEL)\?|MEAS:(?:VOLT|CURR|POW)\?)$')
    for pos, (idx, cmd) in enumerate(wire):
        if not (allowed_query.fullmatch(cmd) if cmd.endswith('?')
                else allowed_write.fullmatch(cmd)):
            issues.append(f'Unapproved H04P wire command #{pos+1}: {cmd}')
        if cmd != 'SYST:ERR?' and cmd != 'SYST:LOC':
            if pos+1 == len(cmds) or cmds[pos+1] != 'SYST:ERR?':
                issues.append(f'Missing immediate SYST:ERR? after wire #{pos+1}: {cmd}')
        if cmd == 'SYST:ERR?' and pos and cmds[pos-1] == cmd:
            issues.append(f'Adjacent SYST:ERR? queries #{pos}, #{pos+1}')

    # Every nonzero response must be a hardware voltage clamp rejection on OFF.
    error_reads = [r for r in records if r.get('event') == 'read'
                   and r.get('command') == 'SYST:ERR?']
    nonzero = []
    for r in error_reads:
        try:
            response = r['response']
            if not isinstance(response, str) or ',' not in response:
                raise ValueError('malformed reply')
            code = int(response.split(',', 1)[0].strip())
        except (KeyError, TypeError, ValueError):
            issues.append('Malformed error queue response')
            continue
        if code:
            nonzero.append(code)
    if len(error_reads) != cmds.count('SYST:ERR?'):
        issues.append('Missing SCPI error replies')
    if nonzero != [120, 120, 120]:
        issues.append(f'Expected exactly 3 SCPI error 120 responses, got {nonzero}')
    expected = [r for r in records if r.get('event') == 'expected_instrument_rejection']
    if len(expected) != 3 or any(r.get('command') != 'VOLT 4.51' or
                                  r.get('outcome') != 'EXPECTED_REJECTION' or
                                  not str(r.get('response','')).startswith('120,') for r in expected):
        issues.append('Missing 3 explicit 4.51 V hardware rejection records')

    selected = 1
    psu_on, psu_off, max_probe = [], [], []
    for idx, cmd in wire:
        if cmd.startswith('INST:NSEL '):
            selected = int(cmd[-1])
        elif cmd == 'CHAN:OUTP 1':
            psu_on.append((idx, selected))
        elif cmd == 'CHAN:OUTP 0':
            psu_off.append((idx, selected))
        elif cmd == 'VOLT 4.51':
            max_probe.append((idx, selected))
    if [c for _, c in max_probe] != [1, 2, 3]:
        issues.append('Max voltage SCPI probes must address CH1, CH2, CH3 in order')
    # Pair the 3 voltage-probe errors to their immediate on-wire command.
    for idx, cmd in max_probe:
        next_read = next((r for r in records[idx+1:] if r.get('event') == 'read' and
                          r.get('command') == 'SYST:ERR?'), None)
        if next_read is None or not str(next_read.get('response','')).startswith('120,'):
            issues.append('VOLT 4.51 missing immediately expected error 120')
    if len(psu_on) != 21 or len(psu_off) != 21:
        issues.append(f'Expected 21 selected-channel PSU ON/OFF pairs, got {len(psu_on)}/{len(psu_off)}')
    if [c for _,c in psu_on] != [ch for ch in (1,2,3) for _ in range(7)]:
        issues.append('PSU channel ON order is not 7 points per CH1, CH2, CH3')
    if [c for _,c in psu_off] != [ch for ch in (1,2,3) for _ in range(7)]:
        issues.append('PSU channel OFF order disagrees with enabled channel')

    kel_writes = [(i, r.get('command')) for i,r in enumerate(records)
                  if r.get('event') == 'kel103_write']
    allowed_kel = re.compile(r'^:(?:FUNC CR|INP (?:ON|OFF)|RES (?:5|11|22|47|56)OHM)$')
    if any(not allowed_kel.fullmatch(str(cmd)) for _,cmd in kel_writes):
        issues.append('Unexpected KEL103 write command')
    kel_on = [i for i,c in kel_writes if c == ':INP ON']
    kel_off = [i for i,c in kel_writes if c == ':INP OFF']
    kel_func = [i for i,c in kel_writes if c == ':FUNC CR']
    kel_res = [(i,c) for i,c in kel_writes if c.startswith(':RES ')]
    if not all(len(x) == 21 for x in (kel_on, kel_off, kel_func, kel_res)):
        issues.append('Expected 21 KEL103 INPUT ON/OFF and function/resistance setups')
    for j in range(min(21,len(kel_on),len(kel_off),len(kel_func),len(kel_res))):
        previous_end = kel_off[j-1] if j else -1
        if not (previous_end < kel_func[j] < kel_res[j][0] < kel_on[j] < kel_off[j]):
            issues.append(f'KEL103 CR modified while active or cycle out of order #{j+1}')

    measurement = [(i,r) for i,r in enumerate(records) if r.get('event') == 'measurement']
    if len(measurement) != 21:
        issues.append(f'Expected 21 H04P measurement events, got {len(measurement)}')
    if len(psu_on) == len(psu_off) == len(kel_on) == len(kel_off) == len(measurement) == 21:
        for j in range(21):
            on, channel = psu_on[j]
            off, off_channel = psu_off[j]
            mid, m = measurement[j]
            if not (kel_on[j] < on < mid < off < kel_off[j]):
                issues.append(f'Unsafe PSU/KEL sequencing around measurement #{j+1}')
            if off_channel != channel or m.get('channel') != channel:
                issues.append(f'Wrong channel at H04P measurement #{j+1}')
            if j and (not kel_off[j-1] < kel_on[j]):
                issues.append(f'Overlapping load cycles at #{j+1}')
            # Require actual OFF response after this PSU OFF, before KEL OFF.
            off_readbacks = [r for r in records[off+1:kel_off[j]]
                             if r.get('event') == 'read' and
                             r.get('command') == 'CHAN:OUTP?' and
                             str(r.get('response')).strip() == '0']
            if len(off_readbacks) < 3:
                issues.append(f'Missing 3 OFF readbacks before KEL INPUT OFF #{j+1}')

    # Validate physical readbacks against *the channel selected on the wire*,
    # not against only the measurement's claimed channel number.
    # The instrument may start with *any* channel selected. Until the first
    # INST:NSEL write or readback, there is no documented CH1 default. Local
    # panel use likewise invalidates the previously tracked selection.
    select = None
    selected_readbacks = []
    for row_index, row in enumerate(records):
        if row.get('event') != 'driver_trace':
            continue
        t = row.get('trace', {})
        cmd, kind = t.get('command'), t.get('event')
        if kind == 'write' and cmd == 'SYST:LOC':
            select = None  # Operator may change selection while in Local.
        if kind == 'write' and isinstance(cmd,str) and cmd.startswith('INST:NSEL '):
            select = int(cmd[-1])
        if kind == 'reply' and cmd == 'INST:NSEL?':
            if select is None:
                raw = t.get('response')
                if raw not in ('1', '2', '3'):
                    issues.append('Invalid selected-channel readback before first selection or after Local')
                else:
                    select = int(raw)
            elif str(t.get('response')) != str(select):
                issues.append('INST:NSEL? reply disagrees with selected channel')
        if kind == 'reply' and cmd == 'CHAN:OUTP?':
            if str(t.get('response')) not in ('0', '1'):
                issues.append('Malformed channel-output state readback')
            selected_readbacks.append((row_index, select, str(t.get('response'))))
    if len(psu_on)==len(psu_off)==len(kel_off)==21:
        for j, ((on,channel),(off,_)) in enumerate(zip(psu_on,psu_off)):
            live = [(ch,value) for idx,ch,value in selected_readbacks if on < idx < off]
            if not all((ch, '1' if ch==channel else '0') in live for ch in (1,2,3)):
                issues.append(f'Missing ON/neighbor-OFF selected channel readback #{j+1}')
            off_states = [(ch,value) for idx,ch,value in selected_readbacks if off<idx<kel_off[j]]
            if not all((ch,'0') in off_states for ch in (1,2,3)):
                issues.append(f'Missing final 3-channel OFF selected readbacks #{j+1}')

    gate_events = [(i,r) for i,r in enumerate(records) if r.get('event')=='operator_gate']
    if len(gate_events) != 5:
        issues.append('Missing operator panel, connect, move, disconnect gates')
    else:
        expected_gate = ('MAXV','CONNECT CH1','MOVE CH1->CH2','MOVE CH2->CH3','DISCONNECT CH3')
        for j, (idx,r) in enumerate(gate_events):
            if expected_gate[j] not in str(r.get('token','')):
                issues.append(f'Wrong operator gate #{j+1}')
        if len(psu_on)==21 and not (gate_events[1][0] < psu_on[0][0] and
                 kel_off[6] < gate_events[2][0] < psu_on[7][0] and
                 kel_off[13] < gate_events[3][0] < psu_on[14][0] and
                 kel_off[20] < gate_events[4][0]):
            issues.append('Operator movement gate missing between physically OFF channel groups')

    def finite(m, field):
        value = m[field]
        if isinstance(value, bool): raise ValueError(field)
        value = float(value)
        if not math.isfinite(value) or value < 0: raise ValueError(field)
        return value

    expectations = []
    channel_voltages = {1:2.2,2:3.3,3:4.4}
    for channel in (1,2,3):
        expectations.extend([
            (channel,'control','common_3.30V','CV',3.3,.1,47),
            (channel,'control','channel_value','CV',channel_voltages[channel],.1,47),
            (channel,'control','profile_voltage_boundary','CV',4.5,.1,56),
            (channel,'control','current_limit_0.100A','CC',3.0,.1,22),
            (channel,'control','current_limit_0.250A','CC',3.0,.25,11),
            (channel,'control','current_limit_0.500A','CC',3.0,.5,5),
            (channel,'power_only','fixed_channel_preset','CV',channel_voltages[channel],.1,47)])
    for j, (_,m) in enumerate(measurement):
        if j >= len(expectations): break
        ch,phase,point,mode,voltage,current,resistance = expectations[j]
        if (m.get('channel'), m.get('phase'), m.get('point'), m.get('actual_mode'),m.get('expected_mode')) != (ch,phase,point,mode,mode):
            issues.append(f'Wrong H04P point / mode / channel #{j+1}')
        try:
            (psu_v,psu_i,psu_api,psu_scpi,kel_v,kel_i,kel_p,r_v,set_v,set_i)=tuple(finite(m,key) for key in (
                'keithley_voltage_v','keithley_current_a','keithley_power_api_w',
                'keithley_power_scpi_w','kel103_voltage_v','kel103_current_a',
                'kel103_power_w','load_resistance_ohm','set_voltage_v','set_current_limit_a'))
            if (abs(set_v-voltage)>.0049 or abs(set_i-current)>.00049 or
                    abs(r_v-resistance)>.001 or abs(psu_v-kel_v)>.06 or
                    abs(psu_i-kel_i)>.03 or abs(psu_api-psu_v*psu_i)>1e-8 or
                    abs(psu_scpi-psu_v*psu_i)>.025 or
                    abs(kel_p-kel_v*kel_i)>.025 or abs(psu_scpi-kel_p)>.025 or
                    max(psu_api,psu_scpi,kel_p)>2.25):
                issues.append(f'H04P numeric / power inconsistency at point #{j+1}')
            actual_p_limit=finite(m,'power_difference_max_w')
            if actual_p_limit>.025+1e-9:
                if not caveats:
                    caveats.append('HISTORICAL_POWER_TOLERANCE: captured runner used 0.31 W; '
                                   'independent recheck used 0.025 W and passed')
        except (KeyError, ValueError, TypeError, OverflowError):
            issues.append(f'Malformed/non-finite H04P measurement #{j+1}')
        # The independent status check preserves CH3's demonstrated OPER=8
        # quirk, but never accepts contradictory or ambiguous CV/CC bits.
        oper, ques = m.get('keithley_operation_status'), m.get('keithley_questionable_status')
        expected_oper = 9 if mode == 'CV' else 10
        expected_ques = 1 if mode == 'CV' else 2
        if (type(oper) is not int or type(ques) is not int or
                oper not in ((8,expected_oper) if ch==3 else (expected_oper,)) or
                ques != expected_ques):
            issues.append(f'Contradictory CV/CC status masks at H04P point #{j+1}')
        if j < len(kel_res) and kel_res[j][1] != f':RES {resistance}OHM':
            issues.append(f'KEL103 resistance command mismatch at point #{j+1}')

    for event in ('max_voltage_panel_limit','max_voltage_panel_readback','profile'):
        rows=[r for r in records if r.get('event')==event]
        if len(rows)!=3 or [r.get('channel') for r in rows]!=[1,2,3]:
            issues.append(f'Missing three-channel {event} evidence')
    if len([r for r in records if r.get('event')=='bench_connection_confirmation'])!=1:
        issues.append('Missing unique bench connection confirmation')
    if len([r for r in records if r.get('event')=='approval'])!=1:
        issues.append('Missing unique H04P wiring approval')
    core=[r for r in records if r.get('event')=='query_outcome']
    final=[r for r in records if r.get('event')=='outcome']
    if len(core)!=1 or core[0].get('outcome')!='PASS' or core[0].get('command_count')!=len(cmds):
        issues.append('Missing core PASS / wrong PSU command count')
    if len(final)!=1 or final[0].get('outcome')!='PASS' or final[0].get('physical_off_confirmed') is not True:
        issues.append('Missing final physically confirmed OFF and PASS')
    if not cmds or cmds[-1]!='SYST:LOC':
        issues.append('Keithley did not finish with SYST:LOC')
    return dict(test_id='H04P', status='FAIL' if issues else
                'PASS_WITH_REVIEW_NOTES' if caveats else 'PASS',
                wire_commands=len(cmds), error_polls=len(error_reads),
                observed_error_codes=nonzero,
                aux_drift_events=sum(r.get('event')=='firmware_revision_drift' for r in records),
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
    if stages == {'H01_AUX_REVISION'}:
        return audit_aux_revision_records(records)
    if stages == {'H03_SINGLE_CVCC'}:
        return audit_single_records(records)
    if stages == {'H03'}:
        return audit_full_h03_records(records)
    if stages == {'H04P'}:
        return audit_h04p_records(records)
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
    parser.add_argument('evidence', type=Path, nargs='+', help='H01/H01_AUX_REVISION/H02/H03/H03_SINGLE_CVCC/H04P events.jsonl files')
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
