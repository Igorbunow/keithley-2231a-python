"""R5-E7 operator-gated OFF-only neutral *TRG qualification.

Default is dry-run without instrument I/O. Physical execution requires an
interactive operator, empty terminals and all CH1/CH2/CH3 OFF. One event only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests/hardware'))
from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from equipment.factory import open_psu
from qualification import Evidence, software_metadata
from r5_e7_neutral_trigger_stage import (STAGE,PHASE,INITIAL_TOKEN,PROFILE_TOKEN,
     EVENT_TOKEN,FINAL_TOKEN,run_neutral_trigger)


def provenance():
    info=software_metadata()
    for key,path in (('r5e7_runner_sha256','tools/r5_e7_neutral_trigger_qualify.py'),
                     ('r5e7_stage_sha256','tests/hardware/r5_e7_neutral_trigger_stage.py')):
        info[key]=hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
    return info


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=EXAMPLE)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--evidence',type=Path)
    args=p.parse_args(argv)
    if args.evidence is not None and not args.execute:
        p.error('--evidence requires --execute')
    try:
        bench=load_bench(args.config)
    except (ValueError,OSError,UnicodeError) as exc:
        p.error(f'Invalid bench configuration: {exc}')
    print('[R5-E7 NEUTRAL *TRG OFF-ONLY; HW NOT_RUN/NOT_REVIEWED]')
    print(bench_summary(bench))
    for line in (
        'ONE global *TRG event; ALL CH1/CH2/CH3 considered potential responders.',
        'All PSU output terminals EMPTY; NO load, meter, DUT, wires or jumpers.',
        'OFF-only independent profile overwrites normal inactive V/I setpoints.',
        'Preserve originals, stage all six trigger registers to matching active values,',
        'verify neutral readbacks, send exactly ONE *TRG, restore all original registers.',
        'Coupling NOT changed or queried. No output-enable. No automatic retry.',
        'On any error, stop; if UART unknown, register restoration is NOT guaranteed.',
    ):print(line)
    for name,token in [('INITIAL',INITIAL_TOKEN),('PROFILE',PROFILE_TOKEN),
                       ('EVENT',EVENT_TOKEN),('FINAL',FINAL_TOKEN)]:
        print(name+':',token)
    if not args.execute:
        print('DRY RUN: no USB enumeration, transport opening, SCPI or trigger event')
        return 0
    if args.config.expanduser().resolve()==EXAMPLE.resolve():
        p.error('Example bench prohibited for hardware execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('Interactive local TTY required for hardware execution')
    if not 0<bench.psu.timeout_s<=3.0:
        p.error('PSU UART timeout must be <=3 seconds')
    if args.evidence is None:
        args.evidence=ROOT/'reports/hardware'/(
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')+
            '_R5E7_NEUTRAL_BUS_TRIGGER_OFF/events.jsonl')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM BENCH R5E7 SHA256={bench.sha256[:12].upper()}',
                      'Verify PSU adapter and bench hash')
        operator_gate(INITIAL_TOKEN,'Physically inspect OFF, all terminals EMPTY')
    except BaseException as exc:
        print(f'ABORT BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence=None
    try:
        evidence=Evidence(args.evidence,STAGE)
        evidence.add('start',phase=PHASE,config_sha256=bench.sha256,software=provenance())
        evidence.add('pre_open_approvals',gates=['BENCH','INITIAL'],
                     approved_before_evidence_open=True,config_sha256=bench.sha256)
        activate_bench(bench)
        transport=open_psu(bench)
    except BaseException as exc:
        if evidence is not None:
            try:evidence.add('outcome',outcome='ABORTED',reason=f'prestage {type(exc).__name__}',
                             phase=PHASE,physical_off_confirmed=False,hardware_review='PENDING')
            except BaseException:pass
        print(f'ABORT BEFORE STAGING: {type(exc).__name__}: {exc}')
        return 2
    result=run_neutral_trigger(transport,evidence,
        profile_gate=lambda token,adapter:operator_gate(token,
            'Authorize OFF-only independent profile',adapter),
        event_gate=lambda token,adapter:operator_gate(token,
            'Authorize ONE neutral bus *TRG; all outputs physically OFF',adapter),
        final_gate=lambda token:operator_gate(token,
            'Verify CH1/CH2/CH3 physically OFF and all terminals empty'))
    print('Original evidence:',evidence.path)
    print('STOP: hardware status PENDING independent JSONL audit and human review')
    return 0 if result=='PASS' else 2

if __name__=='__main__':
    raise SystemExit(main())
