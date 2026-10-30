"""R4.5-D gated batch of all 9 current (or 9 voltage) OFF-only STEP cases.

DEFAULT DRY RUN has zero instrument I/O.  Do not connect any load, DMM or DUT.
This is DIGITAL-only; a powered CV/CC STEP test needs different safety gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_psu
from qualification import Evidence, software_metadata
from r4_5_step_batch_stage import (STAGE, INITIAL_TOKEN, FINAL_BATCH_TOKEN,
    PROFILE_TOKEN, cases, matrix_token, run_step_batch)


def provenance():
    info = software_metadata()
    for key, path in (
        ('r45_batch_runner_sha256', ROOT / 'tools/r4_5_step_batch_qualify.py'),
        ('r45_batch_stage_sha256', ROOT / 'tests/hardware/r4_5_step_batch_stage.py')):
        info[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--matrix', choices=('current','voltage','all'), default='current')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(f'Invalid bench: {exc}')
    plan = cases(args.matrix)
    print('[R4.5-D STEP BATCH; OFF-ONLY; HW NOT_RUN until executed and audited]')
    print(bench_summary(bench))
    print(f'{len(plan)} CASES IN ONE SESSION:')
    for ch, kind, step in plan:
        print(f'  CH{ch} {kind} step {step:.3f}: UP UP DOWN DOWN; before/after OFF')
    print('Only one Independent profile applied. All PSU outputs physically OFF, ALL terminals EMPTY.')
    print('NO KEL103, NO DMM7510, NO DUT, no jumpers, no power on; Recall OFF.')
    print('Any failed case -> stop entire batch; no auto-retry, no skip, no reconnect.')
    print('On known link, emergency OUTP 0 + all-channel digital OFF; then physical OFF gate.')
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('PROFILE GATE:', PROFILE_TOKEN)
    print('BATCH GATE:', matrix_token(args.matrix))
    print('FINAL GATE:', FINAL_BATCH_TOKEN)
    if not args.execute:
        print('DRY RUN: no serial enumeration, open, or SCPI.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench is not authorized for hardware')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Physical hardware requires interactive TTY')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('PSU serial timeout must be <= 3s')
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + f'_R45D_{args.matrix}_BATCH_OFF/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R45STEPBATCH SHA256={bench.sha256[:12].upper()}',
                      'Verify exact PSU serial adapter and bench configuration')
        operator_gate(INITIAL_TOKEN, 'Independently verify CH1/CH2/CH3 OFF and EMPTY terminals')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', matrix=args.matrix, planned_cases=len(plan),
                     phase='STEP_BATCH_OFF', config_sha256=bench.sha256,
                     software=provenance())
        evidence.add('pre_open_approvals', gates=['BENCH','INITIAL'],
                     approved_before_evidence_open=True, config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED',
                             reason=f'pre-stage {type(exc).__name__}',
                             physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}: {exc}')
        return 2

    def profile_gate(token, adapter):
        operator_gate(token, 'Permit one bounded OFF Independent profile', adapter)

    def batch_gate(token, adapter):
        operator_gate(token, 'Authorize ordered OFF-only step matrix', adapter)

    def final_gate(token):
        operator_gate(token, 'Independently verify all three outputs physically OFF and terminals EMPTY')
        evidence.add('operator_confirmation', gate='FINAL_PHYSICAL')

    result = run_step_batch(transport, evidence, matrix=args.matrix,
                            profile_gate=profile_gate, batch_gate=batch_gate,
                            final_gate=final_gate)
    print('Evidence:', evidence.path)
    print('STOP: not HW REVIEWED until independent original JSONL audit.')
    return 0 if result == 'PASS' else 2

if __name__ == '__main__':
    raise SystemExit(main())
