#!/usr/bin/env python3
"""R4.5-F-A: operator-gated, CH1-only fixed 5-point resistive I-V reference."""
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
from r4_5_iv_characterization_stage import (INITIAL_TOKEN, POWER_TOKEN, FINAL_TOKEN,
                                             EXPECTED_V, DIRECTIONS, run_iv)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence and not args.execute:
        parser.error('--evidence requires --execute')
    try: bench = load_bench(args.config)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(f'Invalid bench config: {exc}')
    print('[R4.5-F-A RESISTIVE I-V PILOT; HW NOT_RUN / NOT_REVIEWED]')
    print(bench_summary(bench))
    print('ONLY CH1/Independent, KEL103 CR40, DMM7510 FRONT DCV parallel; CH2/CH3 empty.')
    print('Fixed voltage V:', ', '.join(f'{x:.2f}' for x in EXPECTED_V))
    print('Exactly one enable and', '/'.join(DIRECTIONS).upper(), '; no DUT or other topology.')
    print('Strict power <=0.60W, I limit 0.150A, powered checkpoint <=300s, no retries.')
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('POWER GATE:', POWER_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no USB, UART or LAN instruments opened; no SCPI transmitted.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example config prohibited on real instruments')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Interactive local TTY required for powered experiment')
    if (bench.psu.transport != 'serial' or bench.load.transport != 'serial'
            or bench.meter.transport != 'tcp'):
        parser.error('Requires PSU serial, KEL serial and DMM native TCP')
    if not (0 < bench.psu.timeout_s <= 3 and 0 < bench.load.timeout_s <= 3
            and 0 < bench.meter.timeout_s <= 6):
        parser.error('Instrument timeouts exceed reviewed bounds')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+
            '_R45F_CH1_resistive_IV_FIVE_POINTS/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        verify_serial_adapter(bench.load)
        operator_gate(f'CONFIRM BENCH R45FIVA SHA256={bench.sha256[:12].upper()}',
                      'Verify exact serial adapter identities and DMM TCP endpoint')
        operator_gate(INITIAL_TOKEN,
                      'Physically inspect all PSU OFF, KEL OFF, wiring and Recall OFF')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    psu = load = meter = None
    try:
        evidence = Evidence(args.evidence, 'R45FIVA')
        software = software_metadata()
        for key, relative in (
                ('r45f_runner_sha256', 'tools/r4_5_iv_characterize.py'),
                ('r45f_stage_sha256', 'tests/hardware/r4_5_iv_characterization_stage.py')):
            software[key] = hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
        evidence.add('start', variant='RESISTIVE_IV_CR40', software=software,
                     config_sha256=bench.sha256, commanded_voltages=EXPECTED_V,
                     directions=DIRECTIONS, power_ceiling_w=.60)
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'], config_sha256=bench.sha256)
        activate_bench(bench)
        load = open_load(bench, evidence)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('stage_error', exception_type=type(exc).__name__,
                              detail='Instrument open failure; physical OFF required')
            except BaseException: pass
        for instrument in (psu, load, meter):
            if instrument is not None:
                try: instrument.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; physically verify OFF')
        return 2
    outcome = run_iv(psu, load, meter, evidence, gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until independent original-JSONL audit')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
