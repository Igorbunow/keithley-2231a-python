#!/usr/bin/env python3
"""R4.5-F-E single guarded direct-ballast-DMM diagnostic; dry-run by default."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for part in ('src', 'tests/hardware', 'tools'):
    sys.path.insert(0, str(ROOT/part))

from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_meter, open_psu
from qualification import Evidence, software_metadata
from r4_5_1n4933_ballast_stage import (PRESET_V, BALLAST_OHM, CURRENT_LIMIT_A,
    INITIAL_TOKEN, POWER_TOKEN, FINAL_TOKEN, run_ballast)
from r4_5_1n4933_current_review import validate_plan

PLAN = ROOT/'docs/examples/R45FE_1N4933_current_crosscheck_plan.json'
def check_plan(path):
    """Use the existing R45FE design-only validator; it cannot authorize power."""
    contents = path.read_bytes()
    validate_plan(json.loads(contents))
    return hashlib.sha256(contents).hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=EXAMPLE)
    p.add_argument('--plan', type=Path, default=PLAN)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--evidence', type=Path)
    args = p.parse_args(argv)
    if args.evidence and not args.execute:
        p.error('--evidence requires --execute')
    try:
        plan_sha = check_plan(args.plan)
        bench = load_bench(args.config)
    except (ValueError, OSError, UnicodeError, TypeError) as exc:
        p.error(f'Invalid fixed plan or bench configuration: {exc}')
    print('[R4.5-F-E DIRECT BALLAST DCV; HW NOT_RUN / NOT_REVIEWED]')
    print(bench_summary(bench))
    print('Plan SHA256:', plan_sha)
    print('DMM FRONT HI CH1+ side of resistor, LO diode-anode side.')
    print('One CH1 preset 0.90V, 0.020A current limit, 1.05V voltage cap.')
    print('CH2/CH3 OFF; KEL103 disconnected. No external supplies/jumpers.')
    print('Host checkpoint <=90s, NOT an independent hardware shutdown.')
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('POWER GATE:', POWER_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no ports opened, no SCPI or output mutations.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        p.error('Example bench configuration prohibited for execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('Interactive local TTY required; no unattended output enable')
    if (bench.psu.transport != 'serial' or bench.meter.transport != 'tcp' or
            not 0 < bench.psu.timeout_s <= 3 or not 0 < bench.meter.timeout_s <= 6):
        p.error('Only reviewed serial PSU and TCP DMM transports/timeouts')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+
            '_R45FE_1N4933_direct_ballast_point/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate('CONFIRM R45FE BENCH SHA256='+bench.sha256[:12].upper(),
                      'Verify serial adapter and DMM TCP endpoint; KEL disconnected')
        operator_gate(INITIAL_TOKEN,
                      'Check resistor 99.86ohm >=0.25W, diode stripe, DCV FRONT, PSU OFF')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = meter = psu = None
    try:
        evidence = Evidence(args.evidence, 'R45FE')
        software = software_metadata()
        for key, filename in (
            ('runner_sha256', 'tools/r4_5_1n4933_ballast_qualify.py'),
            ('stage_sha256', 'tests/hardware/r4_5_1n4933_ballast_stage.py')):
            software[key] = hashlib.sha256((ROOT/filename).read_bytes()).hexdigest()
        evidence.add('start', variant='1N4933_DIRECT_99P86OHM_BALLAST_SINGLE_POINT',
                     software=software, config_sha256=bench.sha256,
                     plan_sha256=plan_sha, ballast_ohm=BALLAST_OHM,
                     commanded_preset_v=PRESET_V, current_limit_a=CURRENT_LIMIT_A,
                     operator_physical_claim_only=True, dmm_role='FRONT_DCV_DIRECT_ACROSS_BALLAST',
                     current_accuracy='UNQUALIFIED',
                     note='KEL103 disconnected; no independent DUT ammeter')
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'])
        activate_bench(bench)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('stage_error', exception_type=type(exc).__name__,
                              detail='Open failed; physical PSU OFF required')
            except BaseException: pass
        for instrument in (meter, psu):
            if instrument is not None:
                try: instrument.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}; verify physical OFF')
        return 2
    outcome = run_ballast(psu, meter, evidence, gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until independent original-JSONL audit')
    return 0 if outcome == 'PASS' else 2

if __name__ == '__main__':
    raise SystemExit(main())
