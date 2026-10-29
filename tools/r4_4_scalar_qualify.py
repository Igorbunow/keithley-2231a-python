"""R4.4-C: explicit operator-gated CH1 scalar qualification; DRY RUN by default."""
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
from equipment.factory import open_psu, open_load, open_meter
from qualification import Evidence, software_metadata
from r4_4_scalar_stage import (STAGE, INITIAL_TOKEN, ENABLE_TOKEN, FINAL_TOKEN,
                               run_scalar)


def provenance():
    result = software_metadata()
    for key, path in (
            ('r44_scalar_runner_sha256', ROOT/'tools/r4_4_scalar_qualify.py'),
            ('r44_scalar_stage_sha256', ROOT/'tests/hardware/r4_4_scalar_stage.py')):
        result[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def preflight(bench):
    print('[R4.4-C SCALAR LOW-POWER; HW NOT_RUN until manually executed and reviewed]')
    print(bench_summary(bench))
    print('Independent CH1: exactly 3.30 V, 0.150 A limit, KEL103 CR40Ω, max 0.60 W.')
    print('KEL103: USB serial only, INPUT OFF initially; DMM7510 DCV 10 V, FRONT terminals.')
    print('Physical wiring: KEL and DMM in parallel across CH1; CH2/CH3 EMPTY, NO DUT/JUMPERS.')
    print('DMM :RST is DMM-only. Keithley profile explicitly reconfigures OFF channel presets.')
    print('No auto-reconnect, no fallback transport, no repeated output ON, no unapproved topology.')
    print('FETC voltage/current/power may be stale; only finite/bounded numeric answers are checked.')
    print('SCPI faults/transport uncertainty => no retry on uncertain link, manual physical OFF.')
    print('Physical OFF must be confirmed even after unsuccessful/aborted stage.')
    print('Config requires explicit [qualification.r44_scalar] exact four-field block.')
    print('START GATE:', INITIAL_TOKEN)
    print('POWER GATE:', ENABLE_TOKEN)
    print('FINAL GATE:', FINAL_TOKEN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=EXAMPLE)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.evidence and not args.execute:
        parser.error('--evidence requires --execute')
    try:
        bench = load_bench(args.config)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(f'invalid bench configuration: {exc}')
    preflight(bench)
    if not args.execute:
        print('DRY RUN: no instrument transport opened, no SCPI sent, no output enabled.')
        return 0
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        parser.error('Example bench cannot be used for hardware execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error('Hardware execution requires local interactive TTY')
    if bench.r44_scalar is None:
        parser.error('Add exact [qualification.r44_scalar] to bench.local.toml before execution')
    if bench.load.transport != 'serial':
        parser.error('R4.4-C allows KEL103 USB serial only')
    if not 0 < bench.psu.timeout_s <= 3 or not 0 < bench.load.timeout_s <= 3:
        parser.error('PSU and load timeouts must be <= 3 s')
    if not 0 < bench.meter.timeout_s <= 6 or bench.meter.transport != 'tcp':
        parser.error('DMM7510 must use bounded TCP timeout <= 6 s')
    if args.evidence is None:
        args.evidence = ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
            + '_R44_SCALAR_CH1/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        verify_serial_adapter(bench.load)
        operator_gate(f'CONFIRM BENCH R44SCALAR SHA256={bench.sha256[:12].upper()}',
                      'Confirm both USB serial devices and DMM7510 configured LAN endpoint.')
        operator_gate(INITIAL_TOKEN,
                      'Confirm actual CH1 cable polarity, KEL INPUT OFF, DMM FRONT, no DUT or jumpers.')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = None
    psu = load = meter = None
    try:
        evidence = Evidence(args.evidence, STAGE)
        evidence.add('start', variant='LOW_POWER_SCALAR_CH1_ONLY',
                     software=provenance(), config_sha256=bench.sha256,
                     fixed_point={'v': 3.30, 'i_limit': .150, 'cr': 40.0, 'p_max': .60})
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
                     config_sha256=bench.sha256)
        activate_bench(bench)
        load = open_load(bench, evidence)
        meter = open_meter(bench, evidence)
        psu = open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try: evidence.add('outcome', outcome='ABORTED', reason='connection open failure',
                              physical_off_confirmed=False)
            except BaseException: pass
        for item in (psu, load, meter):
            if item is not None:
                try: item.close()
                except BaseException: pass
        print(f'ABORTED BEFORE STAGE: {type(exc).__name__}: {exc}; verify OFF physically')
        return 2
    outcome = run_scalar(psu, load, meter, evidence, gate=operator_gate)
    print('Evidence:', evidence.path)
    print('STOP: HW NOT_REVIEWED until full SCPI and external measurement audit.')
    return 0 if outcome == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
