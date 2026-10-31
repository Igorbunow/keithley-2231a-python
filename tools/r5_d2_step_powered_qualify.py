"""R5-D2: a single CH1/CH2/CH3 native STEP CV/CC hardware case (dry-run default)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests/hardware'))

from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_psu, open_load, open_meter
from qualification import Evidence, software_metadata
from r4_5_powered_step_stage import POINTS
from r5_d2_powered_step_stage import (STAGE, FINAL_TOKEN, DIRECTIONS, initial_token,
                                     enable_token, run_case, validate_case)


def provenance():
    info = software_metadata()
    for name, filename in (
        ('r5d2_runner_sha256', 'tools/r5_d2_step_powered_qualify.py'),
        ('r5d2_stage_sha256', 'tests/hardware/r5_d2_powered_step_stage.py')):
        info[name] = hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--channel', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--mode', choices=('cv', 'cc'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    validate_case(args.channel, args.mode)
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(f'Invalid bench configuration: {exc}')
    p = POINTS[args.mode]
    print('[R5-D2 POWERED STEP: HW NOT_RUN/NOT_REVIEWED]')
    print(bench_summary(bench))
    print(f'One case only: CH{args.channel} {args.mode.upper()}, 3.30 V / '
          f'{p["limit"]:.3f} A, KEL103 CR{p["cr"]:g} Ω; '
          f'{p["kind"]} STEP {p["step"]:.3f}, {DIRECTIONS}.')
    print('PSU CH selected only -> KEL103; DMM FRONT DCV PARALLEL load; '
          'other PSU channels EMPTY and OFF.')
    print('NO diode, NO series/parallel/tracking jumpers, NO external source.')
    print('Maximum 0.60 W, one CH ON, no automatic retry or automatic next case.')
    print('Sequential PSU/KEL/DMM measurements are NOT simultaneous.')
    print('Loss of PSU/KEL link -> UNKNOWN until manually verified OFF.')
    print('INITIAL GATE:', initial_token(args.channel, args.mode))
    print('ENABLE GATE:', enable_token(args.channel, args.mode))
    print('FINAL GATE:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no USB enumeration, port opening, SCPI or power mutation')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench may not be used for execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Physical hardware requires interactive local TTY')
    if bench.psu.transport != 'serial' or bench.load.transport != 'serial' or bench.meter.transport != 'tcp':
        parser.error('Only PSU UART, KEL103 serial, DMM7510 TCP path is permitted')
    if not (0 < bench.psu.timeout_s <= 3 and 0 < bench.load.timeout_s <= 3
            and 0 < bench.meter.timeout_s <= 6):
        parser.error('Unapproved bench timeouts')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ') +
            f'_R5D2_CH{args.channel}_{args.mode}_FOUR_STEP/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        verify_serial_adapter(bench.load)
        operator_gate(f'CONFIRM BENCH R5D2 SHA256={bench.sha256[:12].upper()}',
                      'Verify PSU/KEL USB adapters and DMM TCP endpoint')
        operator_gate(initial_token(args.channel, args.mode),
                      'Physically verify OFF and selected-channel wiring, load polarity and DMM FRONT DCV')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    psu = load = meter = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', phase='POWERED_STEP_FOUR', channel=args.channel,
                     mode=args.mode, point=p, config_sha256=bench.sha256,
                     directions=list(DIRECTIONS), software=provenance())
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
                     approved_before_evidence_open=True,
                     config_sha256=bench.sha256)
        activate_bench(bench)
        load = open_load(bench, evidence)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED',
                             reason=f'pre-stage {type(exc).__name__}',
                             physical_off_confirmed=False, digital_off_confirmed=False)
            except BaseException: pass
        for item in (psu, load, meter):
            if item is not None:
                try: item.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; physically verify OFF')
        return 2
    outcome = run_case(psu, load, meter, evidence,
                       channel=args.channel, mode=args.mode, gate=operator_gate)
    print('Original evidence:', evidence.path)
    print('STOP: no other channel/mode until independent offline audit and new approval.')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
