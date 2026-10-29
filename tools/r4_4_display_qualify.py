"""R4.4-B read-only display qualification: default DRY RUN; no automatic power."""
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
from r4_4_display_stage import STAGE, INITIAL_TOKEN, FINAL_TOKEN, run_display


def provenance():
    info = software_metadata()
    for key, path in (
            ('r44_display_runner_sha256', ROOT / 'tools/r4_4_display_qualify.py'),
            ('r44_display_stage_sha256', ROOT / 'tests/hardware/r4_4_display_stage.py')):
        info[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


def preflight(bench):
    print('[R4.4-B DISP? OFF-ONLY; HW NOT_RUN until manually executed/reviewed]')
    print(bench_summary(bench))
    for line in (
        'One Keithley only; CH1/CH2/CH3 physically OFF; all terminals empty.',
        'Independent topology; Recall OFF; no DUT, jumpers, KEL103, or other owner.',
        'Only new query DISP?; no display writes; may report 0/1 or ON/OFF.',
        'Allowed session writes: SYST:REM, channel selection, SYST:LOC.',
        'No output ON/OFF, setpoint, topology, trigger, reset or event commands.',
        'One SYST:ERR? poll follows each valid query (consumes error queue).',
        'Unknown reply/timeout => ABORTED, no retry/automatic OFF/LOC.',
        'No profile is applied. Scalar queries MEAS:POW? / FETC:* are DEFERRED',
        'to a separate operator-gated low-power qualification with external load.',
        'Physical OFF must be rechecked at the final gate.',
    ):
        print(line)
    print('START GATE:', INITIAL_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(f'invalid bench configuration: {exc}')
    preflight(bench)
    if not args.execute:
        print('DRY RUN: no USB enumeration, no transport opened, no SCPI issued.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench cannot be used for hardware execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Hardware execution requires interactive TTY')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('R4.4 display qualification requires PSU timeout <= 3 seconds')
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + '_R44_DISPLAY_OFF/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R44DISPLAY SHA256={bench.sha256[:12].upper()}',
                      'Verify the exact configured PSU serial adapter')
        operator_gate(INITIAL_TOKEN,
                      'Confirm outputs OFF, all terminals EMPTY, independent topology')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='DISPLAY_OFF_QUERY_ONLY',
                     config_sha256=bench.sha256, software=provenance())
        evidence.add('pre_open_approvals', approved_before_evidence_open=True,
                     gates=['BENCH', 'INITIAL'], config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED', reason=f'pre-stage {type(exc).__name__}',
                             phase='DISPLAY_OFF_QUERY_ONLY', physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED before stage: {type(exc).__name__}: {exc}')
        return 2

    def final_gate(token):
        operator_gate(token, 'Physically reconfirm all output channels OFF')
        evidence.add('operator_confirmation', gate='FINAL')

    outcome = run_display(transport, evidence, final_gate=final_gate)
    print('Evidence:', evidence.path)
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
