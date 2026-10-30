"""R4.5-E-B: one operator-gated CH1 analog STEP; DRY RUN by default."""
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
from r4_5_powered_step_stage import (STAGE, POINTS, INITIAL_TOKEN, FINAL_TOKEN,
                                      power_token, run_powered_step)


def provenance():
    result = software_metadata()
    for key, relative in (
        ('r45e_runner_sha256', 'tools/r4_5_powered_step_qualify.py'),
        ('r45e_stage_sha256', 'tests/hardware/r4_5_powered_step_stage.py')):
        result[key] = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
    return result


def preflight(bench, mode):
    p = POINTS[mode]
    print('[R4.5-E-B ONE-POINT POWERED STEP; HW NOT_RUN until hardware executed and audited]')
    print(bench_summary(bench))
    print(f'ONE CASE: CH1 {mode.upper()}, 3.30V / {p["limit"]:.3f}A, '
          f'KEL103 CR{p["cr"]:g}Ω, {p["kind"]} UP {p["step"]:.3f}.')
    print('Independent PSU CH1 +/- -> KEL103 +/-; DMM7510 FRONT DCV in PARALLEL CH1.')
    print('CH2 CH3 EMPTY; no DUT, jumpers or external sources; all PSU outputs OFF, KEL INPUT OFF.')
    print('KEL103 USB serial + DMM7510 SCPI LAN; hardware-safe cabling and polarities required.')
    print('Exactly one power ON and one STEP UP; no restart, DOWN, auto retry or next case.')
    print('Mandatory independent PSU/KEL/DMM live measurements BEFORE and AFTER STEP.')
    print('Power cap 0.60 W; CH1 <=3.50V / <=0.150A; CC and CV separate runs.')
    print('No global OUTP 1. On fault: independent PSU and KEL OFF on trustworthy links.')
    print('UART uncertainty -> NO further PSU commands, physical emergency shutdown required.')
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('POWER GATE:', power_token(mode))
    print('FINAL GATE:', FINAL_TOKEN)


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
    preflight(bench, args.mode)
    if not args.execute:
        print('DRY RUN: no enumeration, transport, instrument I/O or change to outputs.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example config prohibited for real hardware')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Real hardware requires local interactive TTY')
    if (bench.psu.transport != 'serial' or bench.load.transport != 'serial'
            or bench.meter.transport != 'tcp'):
        parser.error('R4.5-E requires PSU UART, KEL103 serial, DMM7510 LAN TCP')
    if not (0 < bench.psu.timeout_s <= 3 and 0 < bench.load.timeout_s <= 3
            and 0 < bench.meter.timeout_s <= 6):
        parser.error('Timeouts exceed approved PSU<=3s, KEL<=3s, DMM<=6s')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ') +
            f'_R45E_CH1_{args.mode}_ONE_STEP/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        verify_serial_adapter(bench.load)
        operator_gate(f'CONFIRM BENCH R45EPOWERED SHA256={bench.sha256[:12].upper()}',
                      'Confirm exact PSU/KEL USB serial adapters and DMM LAN endpoint')
        operator_gate(INITIAL_TOKEN,
                      'Physically inspect every cable, polarity, DMM FRONT, all outputs OFF')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    psu = load = meter = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='CH1_ONE_POWERED_STEP', mode=args.mode,
                     point=POINTS[args.mode], software=provenance(),
                     config_sha256=bench.sha256)
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'],
                     config_sha256=bench.sha256)
        activate_bench(bench)
        load = open_load(bench, evidence)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('stage_error', exception_type=type(exc).__name__,
                              detail='one instrument could not open; manual OFF required')
            except BaseException: pass
        for instrument in (psu, load, meter):
            if instrument is not None:
                try: instrument.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; physically verify OFF')
        return 2
    outcome = run_powered_step(psu, load, meter, evidence,
                               mode=args.mode, gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until independently audited; do not auto-run other mode')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
