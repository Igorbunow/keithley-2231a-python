"""R4.5-B: bounded source observations. DRY RUN by default, no physical I/O."""
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
from r4_5_source_stage import (STAGE, INITIAL_TOKEN, PROFILE_TOKEN,
                               FINAL_TOKEN, run_source)


def provenance():
    info = software_metadata()
    for key, path in (
            ('r45_source_runner_sha256', ROOT / 'tools/r4_5_source_qualify.py'),
            ('r45_source_stage_sha256', ROOT / 'tests/hardware/r4_5_source_stage.py')):
        info[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def preflight(bench, steps):
    print('[R4.5-B SOURCE OFF-ONLY; HW NOT_RUN until separately executed and reviewed]')
    print(bench_summary(bench))
    for item in (
        'One Keithley; physically OFF, all terminals EMPTY, no DUT/load/jumpers.',
        'Independent topology, Recall OFF; no other equipment owner.',
        'Query INST? exactly once; compare with INST:NSEL? without changing selection.',
        'No outputs enabled, triggers, reset, event queries or STEP writes.',
        'A STEP run reprograms OFF-channel presets via the existing independent profile.',
        'STEP readings: CH1 VOLT:STEP? then CURR:STEP?; real firmware format unknown.',
        'Untrusted/unknown reply => ABORT, no retry on uncertain UART, manual OFF.',
        'One SYST:ERR? after each valid request (consumes error queue).',
        'SYST:LOC has no readback; operator must confirm OFF at end.',
        f'STEP PROFILE PHASE: {"REQUESTED" if steps else "NOT REQUESTED"}',
    ):
        print(item)
    print('INITIAL GATE:', INITIAL_TOKEN)
    if steps:
        print('SECOND PROFILE GATE:', PROFILE_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--steps', action='store_true',
                        help='second gate: reconfigure OFF presets then observe two CH1 steps')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if (args.steps or args.evidence is not None) and not args.execute:
        parser.error('--steps / --evidence require --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(f'invalid bench configuration: {exc}')
    preflight(bench, args.steps)
    if not args.execute:
        print('DRY RUN: no USB enumeration, transport opened or SCPI issued.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench cannot be used for hardware execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Hardware execution requires interactive TTY')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('R4.5 qualification requires PSU timeout <= 3 seconds')
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + ('_R45_SOURCE_STEPS_OFF/events.jsonl' if args.steps
               else '_R45_SOURCE_INST_OFF/events.jsonl'))
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R45SOURCE SHA256={bench.sha256[:12].upper()}',
                      'Verify the exact PSU serial adapter')
        operator_gate(INITIAL_TOKEN,
                      'Verify CH1/CH2/CH3 physically OFF and terminals EMPTY')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    ev = None
    try:
        ev = Evidence(args.evidence, STAGE)
        ev.add('start', variant='STEP_OFF_PROFILE' if args.steps else 'INST_OFF_QUERY_ONLY',
               config_sha256=bench.sha256, software=provenance())
        ev.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
               approved_before_evidence_open=True, config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if ev is not None:
            try:
                ev.add('outcome', outcome='ABORTED', reason=f'pre-stage {type(exc).__name__}',
                       physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED before stage: {type(exc).__name__}: {exc}')
        return 2

    def profile_gate(token, adapter):
        operator_gate(token, 'Confirm all output LEDs OFF, terminals EMPTY, and preset changes approved', adapter)

    def final_gate(token):
        operator_gate(token, 'Physically reconfirm CH1/CH2/CH3 OFF and empty terminals')
        ev.add('operator_confirmation', gate='FINAL')

    outcome = run_source(transport, ev, steps=args.steps,
                         profile_gate=profile_gate, final_gate=final_gate)
    print('Evidence:', ev.path)
    print('STOP: HW NOT_REVIEWED until independent audit of original SCPI evidence.')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
