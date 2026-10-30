#!/usr/bin/env python3
"""R4.5-E-C: one manually gated five-point powered CV *or* CC hardware series."""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests/hardware'))

from bench_config import load_bench, activate_bench, bench_summary, EXAMPLE
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_load, open_meter, open_psu
from qualification import Evidence, software_metadata
from r4_5_powered_step_stage import POINTS
from r4_5_powered_series_stage import (INITIAL_TOKEN, FINAL_TOKEN, DIRECTIONS,
                                        series_token, run_powered_series)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--mode', choices=tuple(POINTS), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(f'Invalid bench config: {exc}')
    print('[R4.5-E-C FIVE-POINT POWERED STEP; HW NOT_RUN until executed and audited]')
    print(bench_summary(bench))
    p = POINTS[args.mode]
    print(f'ONE MODE: CH1 {args.mode.upper()}, V3.30 / I{p["limit"]:.3f}, KEL103 CR{p["cr"]:g}Ω,')
    print(f'          STEP {p["step"]:.3f} {p["kind"]}, UP UP DOWN DOWN.')
    print('CH1 +/- to KEL103 +/- and DMM7510 FRONT DCV in parallel; CH2/CH3 empty.')
    print('All PSU outputs and KEL INPUT initially OFF; no DUT, jumpers or other owner.')
    print('R4.5-E-C FIXED SERIES: one CH1 enable, UP UP DOWN DOWN, five sequential physical snapshots.')
    print('POWER GATE:', series_token(args.mode))
    print('FINAL GATE:', FINAL_TOKEN)
    print('No retries, no triggers, no other channel, no second mode, no unbounded sweep.')
    if not args.execute:
        print('DRY RUN: no USB/LAN instrumentation opened, no SCPI sent.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example config prohibited for real hardware')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Real hardware requires local interactive TTY')
    if (bench.psu.transport != 'serial' or bench.load.transport != 'serial'
            or bench.meter.transport != 'tcp'):
        parser.error('Requires PSU UART, KEL103 serial and DMM7510 LAN TCP')
    if not (0 < bench.psu.timeout_s <= 3 and 0 < bench.load.timeout_s <= 3
            and 0 < bench.meter.timeout_s <= 6):
        parser.error('Instrument timeouts exceed approved bounds')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+
            f'_R45E_CH1_{args.mode}_FOUR_STEPS/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        verify_serial_adapter(bench.load)
        operator_gate(f'CONFIRM BENCH R45EPOWERED SHA256={bench.sha256[:12].upper()}',
                      'Verify exact PSU/KEL serial adapters, DMM TCP endpoint')
        operator_gate(INITIAL_TOKEN,
                      'Physically inspect CH1/KEL/DMM polarities, CH2/CH3 empty, all outputs OFF')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    psu = load = meter = None
    try:
        evidence = Evidence(args.evidence, 'R45ESERIES')
        software = software_metadata()
        for key, relative in (('r45ec_runner_sha256', 'tools/r4_5_powered_series_qualify.py'),
                              ('r45ec_stage_sha256', 'tests/hardware/r4_5_powered_series_stage.py')):
            software[key] = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
        evidence.add('start', variant='CH1_FIVE_POWERED_SNAPSHOTS', mode=args.mode,
                     point=POINTS[args.mode], directions=DIRECTIONS,
                     software=software, config_sha256=bench.sha256)
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'],
                     config_sha256=bench.sha256)
        activate_bench(bench)
        load = open_load(bench, evidence)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('stage_error', exception_type=type(exc).__name__,
                              detail='instrument open failure; physical OFF required')
            except BaseException: pass
        for instrument in (psu, load, meter):
            if instrument is not None:
                try: instrument.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; physically verify OFF')
        return 2
    outcome = run_powered_series(psu, load, meter, evidence, mode=args.mode,
                                 gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until independent JSONL audit; no automatic other mode')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
