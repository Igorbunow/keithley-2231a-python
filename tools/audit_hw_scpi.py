#!/usr/bin/env python3
"""Offline read-only H01/H02 SCPI trace audit from saved evidence JSONL.

Does not import instrument drivers or create connections. It validates actual
recorded wire-command order, not the incomplete command display in the terminal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def audit(path: Path, *, acknowledge_historical_h02_panel_poll: bool = False) -> dict:
    """Audit a saved run; optionally acknowledge exactly one known pre-fix H02 poll.

    The exception is limited to SYST:LOC -> operator panel approval ->
    SYST:REM -> SYST:ERR? -> SYST:ERR?; other deviations still fail.
    """
    records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not records:
        raise ValueError('Empty evidence')
    stages = {item.get('test_id') for item in records}
    if len(stages) != 1 or next(iter(stages)) not in ('H01', 'H02'):
        raise ValueError('Expected one H01 or H02 evidence run')
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
    parser.add_argument('evidence', type=Path, nargs='+', help='H01/H02 events.jsonl files')
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
