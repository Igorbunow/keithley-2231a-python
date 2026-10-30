"""R4.5-D gated one-case STEP mutation of physically OFF Keithley 2231A.

Default is DRY RUN. No load, DMM, DUT, or connected output terminals.
Never uses the powered output-enable commands. Real hardware needs TTY gates.
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
from r4_5_step_mutation_stage import (STAGE, INITIAL_TOKEN, PROFILE_TOKEN,
    FINAL_TOKEN, VOLTAGE_STEPS, CURRENT_STEPS, case_token, run_step_mutation,
    validate_case)


def provenance():
    info = software_metadata()
    for key, path in (
            ('r45d_runner_sha256', ROOT / 'tools/r4_5_step_mutation_qualify.py'),
            ('r45d_stage_sha256', ROOT / 'tests/hardware/r4_5_step_mutation_stage.py')):
        info[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--kind', choices=('voltage', 'current'), default='voltage')
    parser.add_argument('--increment', type=float, default=.010)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    try:
        validate_case(args.channel, args.kind, args.increment)
    except ValueError as exc:
        parser.error(str(exc))
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(f'invalid bench: {exc}')
    print('[R4.5-D SOURCE STEP MUTATION OFF; HW NOT_RUN until separately reviewed]')
    print(bench_summary(bench))
    print(f'ONE CASE: CH{args.channel} {args.kind} increment {args.increment:.3f} '
          'with UP, UP, DOWN, DOWN. No other case automatically started.')
    for line in (
        'One Keithley only. All outputs physically OFF and ALL terminals EMPTY.',
        'Independent topology, Recall OFF, no other owner; no load, DUT, jumpers.',
        'Profile changes OFF presets: CH1 3.30V/.100A; CH2/3 1.00V/.050A.',
        'Only one STEP set and exactly UP/UP/DOWN/DOWN through production driver.',
        'Every write has SCPI error polling; digital readback tolerance <0.0005 V/A.',
        'Never OUTP 1/CHAN:OUTP 1. No triggers, events, resets, sweep or auto retry.',
        'Unknown UART => no further SCPI; operator must physically verify OFF.',
        'Finish with global OUTP 0, verified three-channel OFF and manual FINAL gate.',
        'SYST:ERR? consumes error queue. Analog resolution is NOT tested here.',
    ):
        print(line)
    print('INITIAL GATE:', INITIAL_TOKEN)
    print('PROFILE GATE:', PROFILE_TOKEN)
    print('MUTATION GATE:', case_token(args.channel, args.kind, args.increment))
    print('FINAL GATE:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no USB enumeration, transport opened or SCPI issued.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench cannot execute hardware')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Hardware requires interactive TTY')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('PSU UART timeout must be <= 3 s')
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + f'_R45D_CH{args.channel}_{args.kind}_{args.increment:.3f}_OFF/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R45STEPMUT SHA256={bench.sha256[:12].upper()}',
                      'Verify PSU adapter and bench hash')
        operator_gate(INITIAL_TOKEN, 'Physically inspect all three outputs OFF, empty terminals')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='STEP_MUTATION_OFF', channel=args.channel,
                     kind=args.kind, increment=args.increment, config_sha256=bench.sha256,
                     software=provenance())
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
                     approved_before_evidence_open=True, config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED', reason=f'pre-stage {type(exc).__name__}',
                             physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED before stage: {type(exc).__name__}: {exc}')
        return 2

    def profile_gate(token, adapter):
        operator_gate(token, 'Permit bounded OFF profile preset reconfiguration', adapter)

    def mutation_gate(token, adapter):
        operator_gate(token, 'Permit ONE STEP setting and UP UP DOWN DOWN while OFF', adapter)

    def final_gate(token):
        operator_gate(token, 'Independently inspect PSU CH1/2/3 OFF and empty terminals')
        evidence.add('operator_confirmation', gate='FINAL_PHYSICAL')

    outcome = run_step_mutation(transport, evidence, channel=args.channel,
                                kind=args.kind, increment=args.increment,
                                profile_gate=profile_gate, mutation_gate=mutation_gate,
                                final_gate=final_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until complete original JSONL audit.')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
