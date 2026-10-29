"""R4.3 *OPC? idle-only hardware gate. Dry-run unless --execute is explicit."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
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
from r4_3_opc_stage import STAGE, INITIAL_TOKEN, run_opc


def preflight(bench):
    print('[R4.3 OPC? / HW NOT_RUN until operator approval and evidence review]')
    print(bench_summary(bench))
    lines = (
        'Single Keithley, idle: CH1/CH2/CH3 physically OFF, terminals empty.',
        'Independent topology; Recall OFF; no other application controlling PSU.',
        'The only new SCPI query is *OPC?; the sole accepted response is 1.',
        'The query MAY WAIT for previous operations; do not use if PSU is busy.',
        'Serial transport response timeout must be finite and <= 3 seconds.',
        'Other observations: ESE/SRE before/after; IDN, topology and OFF.',
        'SYST:ERR? drains the error queue after valid SCPI commands.',
        'Writes allowed ONLY: SYST:REM, INST:NSEL 1/2/3, SYST:LOC.',
        'Forbidden: *OPC write, *WAI, *ESR?, event queries, *CLS, *RST,',
        ' triggering, power/setpoint/memory writes; no automatic OUTP 0.',
        'On timeout/malformed/lost reply: no retries and NO more SCPI.',
        'No test of pending operations, OPC event bit, or asynchronous timing.',
    )
    for line in lines:
        print(line)
    print('START GATE:', INITIAL_TOKEN)
    print('FINAL GATE: CONFIRM R43OPC CH1=OFF CH2=OFF CH3=OFF')


def provenance():
    info = software_metadata()
    for field, path in (
            ('r43_opc_runner_sha256', ROOT / 'tools/r4_3_opc_qualify.py'),
            ('r43_opc_stage_sha256', ROOT / 'tests/hardware/r4_3_opc_stage.py')):
        info[field] = hashlib.sha256(path.read_bytes()).hexdigest()
    return info


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
    if args.evidence is None:
        args.evidence = ROOT / 'reports/hardware' / (
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + '_R43_OPC_IDLE/events.jsonl')
    if not 0 < bench.psu.timeout_s <= 3.0:
        parser.error('R4.3 OPC requires a PSU transport timeout <= 3 seconds')
    try:
        verify_serial_adapter(bench.psu)
        print('USB adapter path and VID:PID verified')
        operator_gate(f'CONFIRM BENCH R43OPC SHA256={bench.sha256[:12].upper()}',
                      'Verify Keithley identity and exact configured USB adapter')
        operator_gate(INITIAL_TOKEN,
                      'Verify all three outputs OFF and terminals EMPTY')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='OPC_IDLE_QUERY',
                     config_sha256=bench.sha256, software=provenance())
        evidence.add('pre_open_approvals', approved_before_evidence_open=True,
                     gates=['BENCH', 'INITIAL'], config_sha256=bench.sha256)
        activate_bench(bench)
        transport = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:
                evidence.add('outcome', outcome='ABORTED',
                             reason=f'pre-stage {type(exc).__name__}',
                             phase='OPC_IDLE_QUERY', physical_off_confirmed=False)
            except BaseException:
                pass
        print(f'ABORTED before stage: {type(exc).__name__}: {exc}')
        return 2

    def final_gate(token):
        operator_gate(token, 'Recheck physical OFF after SYST:LOC')
        evidence.add('operator_confirmation', gate='FINAL')

    outcome = run_opc(transport, evidence, final_gate=final_gate)
    print('Evidence:', evidence.path)
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
