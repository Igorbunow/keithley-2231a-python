#!/usr/bin/env python3
"""R4.5-F-D gated 1N4933 low-power forward I-V; never auto-execute."""
from __future__ import annotations

import argparse
import hashlib
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'tests/hardware'))
sys.path.insert(0, str(ROOT/'tools'))

from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_meter, open_psu
from qualification import Evidence, software_metadata
from r4_5_1n4933_plan import PlanError, analyze, read_plan
from r4_5_1n4933_forward_stage import (INITIAL_TOKEN, POWER_TOKEN, FINAL_TOKEN,
                                         VOLTAGES, run_forward)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=EXAMPLE)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--evidence', type=Path)
    args = p.parse_args(argv)
    if args.evidence and not args.execute:
        p.error('--evidence requires --execute')
    try:
        plan, plan_sha = read_plan(args.plan)
        design = analyze(plan, sha256=plan_sha)
        bench = load_bench(args.config)
    except (OSError, UnicodeError, ValueError, PlanError) as exc:
        p.error(f'Invalid approved input: {exc}')
    measured = design['ballast_physical_measurement_ohm']
    if measured is not None and not math.isclose(measured, 99.86, rel_tol=0, abs_tol=1e-9):
        p.error('This run is approved only for the reported 99.86-ohm resistor')
    if plan['program']['maximum_powered_s'] != 90:
        p.error('This run is fixed at a maximum 90-second host checkpoint')
    print('[R4.5-F-D 1N4933 FORWARD I-V; HW NOT_RUN / NOT_REVIEWED]')
    print(bench_summary(bench))
    print('Plan SHA256:', plan_sha)
    print('Only CH1 + 100-ohm ballast + 1N4933; DMM FRONT DCV across diode.')
    print('No KEL103 connection, no CH2/CH3 load, no external supply or jumper.')
    print('Source voltage V:', VOLTAGES, '; current limit 0.020A; host checkpoint <=90s.')
    print('Measured ballast:', measured, 'ohm (operator report, not independently verified).')
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('POWER GATE:', POWER_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no instruments opened; no SCPI or output mutations.')
        return 0
    if not design['ballast_operator_claimed_verified'] or measured is None:
        p.error('Powered execution needs actual OFF-state ballast measurement in plan')
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        p.error('Example bench configuration prohibited for execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('Interactive local TTY required; no noninteractive power enable')
    if (bench.psu.transport != 'serial' or bench.meter.transport != 'tcp' or
            not 0 < bench.psu.timeout_s <= 3 or not 0 < bench.meter.timeout_s <= 6):
        p.error('Only reviewed serial PSU and TCP DMM transports/timeouts')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+
            '_R45FD_1N4933_forward_five_points/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate('CONFIRM R45FD BENCH SHA256='+bench.sha256[:12].upper(),
                      'Verify serial adapter identity, DMM TCP endpoint, no KEL connection')
        operator_gate(INITIAL_TOKEN,
                      'With PSU OFF, inspect wiring, 99.86-ohm resistor and its >=0.25W rating')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    meter = psu = None
    try:
        evidence = Evidence(args.evidence, 'R45FD')
        software = software_metadata()
        for name, path in (
            ('runner_sha256', 'tools/r4_5_1n4933_forward_qualify.py'),
            ('stage_sha256', 'tests/hardware/r4_5_1n4933_forward_stage.py')):
            software[name] = hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
        evidence.add('start', variant='1N4933_FORWARD_100OHM', software=software,
                     config_sha256=bench.sha256, plan_sha256=plan_sha,
                     resistor_measured_ohm=measured, resistor_temp_c=26.0,
                     operator_physical_claim_only=True, commanded_voltages=VOLTAGES,
                     note='KEL103 physically disconnected; no independent DUT ammeter')
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'])
        activate_bench(bench)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('stage_error', exception_type=type(exc).__name__,
                              detail='Instrument open failed; physical PSU OFF required')
            except BaseException: pass
        for instrument in (meter, psu):
            if instrument is not None:
                try: instrument.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; verify physical OFF')
        return 2
    outcome = run_forward(psu, meter, evidence, actual_ohm=measured, gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until independent original-JSONL audit')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
