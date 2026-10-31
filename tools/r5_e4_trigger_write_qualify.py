"""R5-E4 native trigger preset OFF write/read/restore qualification.

Default DRY RUN. --execute is interactive and requires empty output terminals.
No output-enable, trigger execution, coupling or automatic hardware retries.
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
from r5_e4_trigger_write_stage import (STAGE, PHASE, INITIAL_TOKEN, PROFILE_TOKEN,
                                        FINAL_TOKEN, stage_token, run_trigger_write)


def provenance():
    info = software_metadata()
    for key, filename in (('r5e4_runner_sha256', 'tools/r5_e4_trigger_write_qualify.py'),
                          ('r5e4_stage_sha256', 'tests/hardware/r5_e4_trigger_write_stage.py')):
        info[key] = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--channel', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(f'Invalid bench config: {exc}')
    print('[R5-E4 TRIGGER PRESET OFF WRITE/RESTORE; HW NOT_RUN/NOT_REVIEWED]')
    print(bench_summary(bench))
    print(f'ONE CASE: CH{args.channel}; read initial, stage two presets, verify, restore original, verify.')
    for line in (
        'All PSU terminals EMPTY; all outputs physically OFF; no KEL/DMM/DUT.',
        'Applies OFF independent profile and overwrites INACTIVE normal setpoints.',
        'Writes only selected-channel VOLT:TRIG and CURR:TRIG then restores originals.',
        'NO *TRG, NO coupling, NO output-enable, NO automatic retry after uncertainty.',
        'Abort may leave changed trigger registers: verify OFF and inspect manually.',
        'End: verify all three OFF, SYST:LOC, physical operator confirmation.',
    ):
        print(line)
    print('INITIAL:', INITIAL_TOKEN)
    print('PROFILE:', PROFILE_TOKEN)
    print('STAGE:', stage_token(args.channel))
    print('FINAL:', FINAL_TOKEN)
    if not args.execute:
        print('DRY RUN: no USB enumeration, transport opening or instrument I/O')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench prohibited for hardware execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Interactive local TTY required for real hardware')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('PSU UART timeout must be <=3 s')
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + f'_R5E4_CH{args.channel}_TRIGGER_WRITE_RESTORE_OFF/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R5E4 SHA256={bench.sha256[:12].upper()}',
                      'Check PSU adapter and bench hash')
        operator_gate(INITIAL_TOKEN,
                      'Physically inspect all outputs OFF, terminals empty and no jumpers')
    except BaseException as exc:
        print(f'ABORT BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', phase=PHASE,
                     channel=args.channel, config_sha256=bench.sha256,
                     software=provenance())
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
                     approved_before_evidence_open=True, config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED', reason=f'prestage: {type(exc).__name__}',
                             phase=PHASE, channel=args.channel,
                             physical_off_confirmed=False, hardware_review='PENDING')
            except BaseException:
                pass
        print(f'ABORT before stage: {type(exc).__name__}: {exc}')
        return 2

    def profile_gate(token, adapter):
        operator_gate(token, 'Authorize OFF-only independent preset configuration', adapter)

    def stage_gate(token, adapter):
        operator_gate(token, 'Authorize one bounded trigger-write/restore cycle', adapter)

    def final_gate(token):
        operator_gate(token, 'Physically inspect PSU CH1/CH2/CH3 OFF and empty terminals')

    result = run_trigger_write(transport, evidence, channel=args.channel,
                                  profile_gate=profile_gate,
                                  stage_gate=stage_gate, final_gate=final_gate)
    print('Original evidence:', evidence.path)
    print('STOP: Hardware status PENDING until independent audit and human review')
    return 0 if result == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
