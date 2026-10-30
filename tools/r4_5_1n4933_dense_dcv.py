#!/usr/bin/env python3
"""Manually gated 1N4933 dense *forward* sweep with DMM DCV across diode.

No power is applied without --execute and independent operator gates. Uses the
existing validated equipment adapter and the application-side example; does
not implement a second DMM/PSU protocol or modify the library.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for part in ('src', 'examples', 'tests/hardware', 'tools'):
    sys.path.insert(0, str(ROOT / part))

from bench_config import EXAMPLE, activate_bench, bench_summary, load_bench
from common import operator_gate
from connection_gate import verify_serial_adapter
from diode_iv import run_profile
from equipment.factory import open_meter, open_psu
from iv_profile import load_iv_profile, describe, IVProfileError
from keithley2231a import Keithley2231A
from qualification import Evidence, software_metadata
from r4_status_stage import _redacted_trace

PROFILE = ROOT / 'examples/diode_iv_ch1_dense_dcv_profile.json'
INITIAL = ('CONFIRM R45FK PSU_CH1_PLUS_R99.86_DIODE_ANODE CATHODE_STRIPE_CH1_MINUS '
           'DMM7510_FRONT_INPUT_HI_ANODE_INPUT_LO_CATHODE_DCV NO_AMPS_CONNECTION '
           'CH2_CH3_EMPTY_ALL_OFF KEL_DISCONNECTED NO_JUMPERS NO_EXTERNAL_POWER '
           'R99.86_MEASURED_OFF RESISTOR_GE_0.25W')
FINAL = 'CONFIRM R45FK CH1=OFF CH2=OFF CH3=OFF DIODE_NOT_ENERGIZED'
MAX_POWERED_S = 240


def validate_fixed_plan(plan):
    if (len(plan.channels) != 1 or plan.channels[0].channel != 1 or
            plan.channels[0].selected_source != 'EXTERNAL_DIODE_DCV_BALLAST' or
            plan.channels[0].force or plan.channels[0].current_limit_a != .020 or
            plan.channels[0].ballast_ohm != 99.86 or
            len(plan.channels[0].voltages) != 61 or
            plan.channels[0].voltages != load_iv_profile(PROFILE).channels[0].voltages or
            plan.settle_s != .25):
        raise IVProfileError('Hardware runner fixed to reviewed 61-point CH1 DMM DCV plan')
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=EXAMPLE)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--output-dir', type=Path)
    args = p.parse_args(argv)
    try:
        plan = load_iv_profile(PROFILE)
        validate_fixed_plan(plan)
        bench = load_bench(args.config)
    except (ValueError, OSError, UnicodeError, TypeError) as exc:
        p.error(f'Invalid profile or bench configuration: {exc}')
    print('[R4.5-F-K 61-POINT CH1 DMM DCV; HW NOT_RUN/NOT_REVIEWED]')
    print(bench_summary(bench))
    print(json.dumps(describe(plan), ensure_ascii=False, indent=2))
    print('DMM FRONT DCV INPUT HI diode anode, INPUT LO striped cathode; AMPS jack EMPTY.')
    print('CH1+: 99.86 ohm >=0.25 W -> diode anode; striped cathode -> CH1-.')
    print('CH2/CH3 OFF and unconnected, KEL disconnected, no Series/Parallel/Tracking jumpers.')
    print('Voltage 0.60..1.20..0.60V, 0.02V steps, 61 samples; CH1 20mA limit.')
    print('240s host checkpoint is NOT an independent hardware watchdog.')
    print('INITIAL GATE:', INITIAL)
    print('FINAL GATE:', FINAL)
    if not args.execute:
        print('DRY RUN: no ports opened, no SCPI or power mutation')
        return 0
    if args.output_dir is None or args.output_dir.exists():
        p.error('--execute requires a NEW --output-dir (no overwrite)')
    if args.config.expanduser().resolve() == EXAMPLE.resolve():
        p.error('Example bench config prohibited for execution')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('Interactive local TTY required; no unattended run')
    if (bench.psu.transport != 'serial' or bench.meter.transport != 'tcp' or
            not 0 < bench.psu.timeout_s <= 3 or not 0 < bench.meter.timeout_s <= 6):
        p.error('Only reviewed serial PSU + TCP DMM endpoints/timeouts')
    if not args.output_dir.parent.exists():
        p.error('Output parent directory must already exist')
    evidence_path = args.output_dir.with_suffix('.events.jsonl')
    if evidence_path.exists():
        p.error('Evidence file already exists; no overwrite')
    try:
        verify_serial_adapter(bench.psu)
        operator_gate(f'CONFIRM R45FK BENCH SHA256={bench.sha256[:12].upper()}',
                      'Check actual serial adapter and DMM TCP endpoint')
        operator_gate(INITIAL, 'Physically check output OFF, diode polarity, resistor and FRONT DCV wiring')
    except BaseException as exc:
        print(f'ABORTED BEFORE OPEN: {type(exc).__name__}: {exc}')
        return 2
    evidence = Evidence(evidence_path, 'R45FK')
    meta = software_metadata()
    meta.update({key: hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                 for key,path in (('runner_sha256','tools/r4_5_1n4933_dense_dcv.py'),
                     ('example_sha256','examples/diode_iv.py'),
                     ('profile_sha256','examples/diode_iv_ch1_dense_dcv_profile.json'))})
    evidence.add('start', software=meta, bench_sha256=bench.sha256,
                 topology='CH1_R99.86_1N4933_DMM_FRONT_DCV_ACROSS_DIODE',
                 current_method='SEQUENTIAL_PSU_MINUS_DMM_DCV_OVER_R',
                 current_accuracy='UNQUALIFIED', sample_count=61,
                 power_checkpoint_s=MAX_POWERED_S)
    meter = None
    opened_psu = None
    outcome = 'ABORTED'
    try:
        activate_bench(bench)
        opened_psu = open_psu(bench)
        with Keithley2231A(opened_psu, debug=True,
                debug_trace=lambda record: _redacted_trace(evidence, record)) as psu:
            evidence.add('psu_identity', response=psu.identity())
            if psu.output_states() != {1:False, 2:False, 3:False}:
                raise IVProfileError('PSU outputs must all be OFF before DMM configuration')
            for command in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
                if psu._bool(psu.diagnostic_query(command)):
                    raise IVProfileError('Combined topology enabled; no power authorization')
            meter = open_meter(bench, evidence)
            meter.configure_voltage(10)
            baseline = meter.measure()['voltage_v']
            evidence.add('dmm_off_diode_dcv_v', voltage_v=baseline)
            if not -.05 <= baseline <= .05:
                raise IVProfileError('Non-zero DMM voltage while PSU OFF; verify wiring')
            def measure_diode(_source_reading):
                value = meter.measure()['voltage_v']
                evidence.add('diode_dcv_sample', dmm_voltage_v=value,
                             note='SEQUENTIAL_NOT_SIMULTANEOUS; UNVALIDATED')
                return {'voltage_v':value}
            paths = run_profile(psu, plan, output_dir=args.output_dir,
                                approve=lambda token: operator_gate(token, 'Check selected CH1 wiring and actual OFF'),
                                diode_voltage_observers={1:measure_diode},
                                max_powered_s=MAX_POWERED_S)
            evidence.add('files_complete', files=[{'name':x.name,
                           'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in paths],
                         digital_off_confirmed=True, operator_off_confirmed=True)
            if psu.output_states() != {1:False, 2:False, 3:False}:
                raise IVProfileError('Unexpected ON after all samples')
        # PSU session has completed and independently checked OFF.
        operator_gate(FINAL, 'Physically verify all PSU channels OFF and diode not energized')
        evidence.add('operator_final_off', confirmed=True)
        outcome = 'PASS_PENDING_INDEPENDENT_REVIEW'
        evidence.add('outcome', status=outcome, accuracy='UNQUALIFIED')
        print('R4.5-F-K PASS (independent evidence audit pending):', args.output_dir)
        print('Raw evidence:', evidence_path)
        return 0
    except BaseException as exc:
        # Outer final OFF refusal must never leave a successful completion marker.
        marker = args.output_dir / 'completed.json'
        if marker.is_file():
            try: marker.unlink()
            except OSError: pass
        try: evidence.add('stage_error', exception_type=type(exc).__name__, detail=str(exc))
        except BaseException: pass
        try: evidence.add('outcome', status='ABORTED', digital_off='UNKNOWN_IF_LINK_LOST')
        except BaseException: pass
        print(f'R4.5-F-K ABORTED: {type(exc).__name__}: {exc}; MANUALLY VERIFY ALL OFF; NO RECONNECT/RETRY')
        return 2
    finally:
        if meter is not None:
            try: meter.close()
            except BaseException: pass
        elif opened_psu is not None:
            try: opened_psu.close()
            except BaseException: pass

if __name__ == '__main__':
    raise SystemExit(main())
