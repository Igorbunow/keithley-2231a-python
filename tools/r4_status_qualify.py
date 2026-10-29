"""R4.2 hardware status qualification. Default: OFFLINE preflight only."""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests/hardware'))

from bench_config import EXAMPLE, load_bench, activate_bench, bench_summary
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_psu
from qualification import Evidence, software_metadata
from r4_status_stage import (INITIAL_TOKEN, STAGE, run_status)


def preflight(bench, *, consume_events):
    print('[R4.2 HARDWARE / NOT_RUN UNTIL REVIEWED]')
    print(bench_summary(bench))
    print('One Keithley only. No DMM, KEL103, oscilloscope, DUT or jumper connected.\n'
          'CH1/CH2/CH3 physically OFF; Independent mode; Output Recall=OFF.\n'
          'Permitted writes ONLY: SYST:REM, INST:NSEL 1/2/3 for readback, SYST:LOC.\n'
          'No OUTP, *CLS, *RST, STAT:PRES, mask writes or setpoint writes.\n'
          'Read-only mode: SYST:MODU?, 4 standard enable masks, 8 nested enable masks.\n'
          'SYST:ERR? is automatically queried after each wire command, consuming the\n'
          'error queue (but not the event latches). Existing errors abort the run.\n'
          'Every write/query, reply and outcome is stored in immutable-path JSONL.\n'
          'First nonzero SCPI error / malformed reply / lost link aborts without retry.\n'
          'If communication becomes uncertain, NO additional SCPI; check physical OFF.\n'
          'No automatic output shutdown or restore: test never energizes outputs.')
    if consume_events:
        print('DESTRUCTIVE PHASE: *ESR?, STAT:QUES:EVEN?, STAT:OPER:EVEN?\n'
              'These three event queries can clear accumulated event latches.\n'
              'Separate exact operator approval is mandatory immediately before them.\n'
              'No second event read; only use when losing saved latched events is acceptable.')
    print('START GATE:', INITIAL_TOKEN)
    print('FINAL GATE: CONFIRM R4STATUS CH1=OFF CH2=OFF CH3=OFF')


def r4_software_provenance():
    """Include both R4 hardware entry point and stage in the evidence identity.

    qualification.software_metadata() predates this runner and its historical
    source digest does not include tools/r4_status_qualify.py. Preserve that
    digest and add separate SHA256 fields for the missing concrete files.
    """
    info = software_metadata()
    info['r4_runner_sha256'] = hashlib.sha256(
        (ROOT / 'tools/r4_status_qualify.py').read_bytes()).hexdigest()
    info['r4_stage_sha256'] = hashlib.sha256(
        (ROOT / 'tests/hardware/r4_status_stage.py').read_bytes()).hexdigest()
    return info


def record_pre_open_approvals(evidence, bench_sha256):
    """Record pre-open terminal approvals retrospectively, not replay them.

    The real operator gates have already completed BEFORE Evidence is opened.
    This supplemental record makes standalone JSONL review possible; it does
    not claim these approvals occurred at the record's timestamp.
    """
    evidence.add('pre_open_approvals',
                 approved_before_evidence_open=True,
                 gates=['BENCH', 'INITIAL'],
                 config_sha256=bench_sha256)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE,
                        help='existing complete bench TOML; mandatory for hardware')
    parser.add_argument('--execute', action='store_true',
                        help='one explicitly approved physical run')
    parser.add_argument('--consume-events', action='store_true',
                        help='separately gated destructive event phase; requires --execute')
    parser.add_argument('--evidence', type=Path,
                        help='new JSONL path; existing files never overwritten')
    args = parser.parse_args(argv)
    if args.consume_events and not args.execute:
        parser.error('--consume-events requires --execute (preflight: omit --consume-events)')
    if args.evidence is not None and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(f'invalid bench config: {exc}')
    preflight(bench, consume_events=args.consume_events)
    if not args.execute:
        print('DRY RUN: no USB discovery, no transport opened, no SCPI issued.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('example bench configuration cannot run on hardware')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('hardware execution requires an interactive TTY')
    if args.evidence is None:
        suffix = 'EVENT' if args.consume_events else 'READ_ONLY'
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + f'_R4_STATUS_{suffix}/events.jsonl')
    # No device access before the full bench config and USB identity gate.
    try:
        verify_serial_adapter(bench.psu)
        print('USB adapter identity: path and VID:PID verified')
        operator_gate(f'CONFIRM BENCH R4STATUS SHA256={bench.sha256[:12].upper()}',
                      'Verify configured USB endpoint and Keithley panel labels')
        operator_gate(INITIAL_TOKEN, 'Verify all terminals empty, no jumper, OFF and Recall=OFF')
    except (BaseException) as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='EVENT' if args.consume_events else 'READ_ONLY',
                     config_sha256=bench.sha256, software=r4_software_provenance())
        record_pre_open_approvals(evidence, bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if 'evidence' in locals():
            try:
                evidence.add('outcome', outcome='ABORTED',
                             reason=f'pre-stage {type(exc).__name__}',
                             phase='EVENT' if args.consume_events else 'READ_ONLY',
                             physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED before stage: {type(exc).__name__}: {exc}')
        return 2
    def gate(token):
        operator_gate(token, 'R4 hardware one-time confirmation')
        evidence.add('operator_confirmation', gate='EVENT' if token.startswith('CONFIRM R4STATUS CONSUME') else 'FINAL')
    outcome = run_status(transport, evidence, consume_events=args.consume_events,
                         event_gate=gate, final_gate=gate)
    print('Evidence:', evidence.path)
    return 0 if outcome == 'PASS' else 2

if __name__ == '__main__':
    raise SystemExit(main())
