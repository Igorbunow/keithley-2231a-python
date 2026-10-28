"""CLI-only H03 single CH1 CV/CC orchestration, no import-time hardware I/O.

Separate from legacy H03. Directly binds connection gates and evidence to the
validated TOML; delegates *all* PSU output writes to the restricted stage core.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import sys

from connection_gate import (connection_card, connection_token, required_devices,
                             verify_serial_adapter)
from equipment.factory import open_psu, open_load
from single_cvcc_stage import run_single_cvcc

FINAL_TOKEN = 'CONFIRM H03-SINGLE CH1=OFF CH2=OFF CH3=OFF KEL103=OFF'


def wiring_token(bench):
    point = bench.h03_single_cvcc
    point.validate()
    return (f'CONFIRM H03-SINGLE CH1 CVCC ALL_OFF KEL=INPUT_OFF '
            f'CH2=OPEN CH3=OPEN NO_JUMPERS RECALL=OFF '
            f'CV=V{point.cv_voltage_v:.2f}_I{point.cv_current_limit_a:.3f}_CR{point.cv_resistance_ohm:g} '
            f'CC=V{point.cc_voltage_v:.2f}_I{point.cc_current_limit_a:.3f}_CR{point.cc_resistance_ohm:g} '
            f'MAXP={point.max_power_w:.2f}')


def run_h03_single_cli(bench):
    """Perform exactly one interactive CH1 pass, or fail before opening ports."""
    # Import runtime helpers only after qualification.main has loaded. No
    # duplicated device endpoints or other stage runners are consulted here.
    from qualification import Evidence, ROOT, report, software_metadata, stage_passed, stages

    point = bench.h03_single_cvcc
    if point is None:
        print('BLOCKED: single CV/CC point missing; ports not opened.')
        return 2
    point.validate()
    if bench.load.transport != 'serial':
        print('BLOCKED: CH1 single CV/CC requires configured KEL103 USB serial; ports not opened.')
        return 2
    if not all(stage_passed(stages()[stage][-1]) for stage in ('H01', 'H02')):
        print('BLOCKED: reviewed H01 and full H02 PASS prerequisites are required; ports not opened.')
        return 2
    if not sys.stdin.isatty():
        print('BLOCKED: interactive local terminal required; ports not opened.')
        return 2

    devices = required_devices('H03')
    bench_gate = connection_token(bench, 'H03', single_channel_cvcc=True)
    wire_gate = wiring_token(bench)
    print(connection_card(bench, 'H03', devices, single_channel_cvcc=True))
    print('SAFETY: only CH1 may energize; KEL103 + → CH1+, − → CH1−; CH2/CH3 free. '
          'No DUT, no jumpers, Output Recall OFF, source mains switch accessible.')
    print('First gate approves the configured serial links only; the second '
          'acknowledges the physical OFF/unloaded preconditions, not an ON command.')
    try:
        for name in devices:
            device = getattr(bench, name)
            verify_serial_adapter(device)
            print(f'USB {name}: {device.port} VID:PID match PASS')
        # Both confirmations must be explicit BEFORE opening either instrument.
        if input(f'Подтвердите подключения:\n{bench_gate}\n> ').strip() != bench_gate:
            print('BLOCKED: bench gate mismatch; no ports opened.')
            return 2
        if input(f'Подтвердите исходную схему/OFF:\n{wire_gate}\n> ').strip() != wire_gate:
            print('BLOCKED: wiring gate mismatch; no ports opened.')
            return 2
    except (EOFError, KeyboardInterrupt):
        print('ABORTED: initial gate interrupted, no ports opened.')
        return 2
    except (ImportError, OSError, TypeError, ValueError) as exc:
        print(f'BLOCKED: USB inventory/preflight failed ({type(exc).__name__}); no ports opened.')
        return 2

    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_H03_SINGLE_CVCC/events.jsonl',
                        'H03_SINGLE_CVCC')
    evidence.add('metadata', **software_metadata(),
                 topology='Independent CH1 only; no jumpers/DUT; CH2/3 open',
                 subgroup='CH1_single_cv_cc',
                 load='KEL103 USB serial, INPUT OFF at entry; CH1 only',
                 proposed_caps=f'CH1 {point.voltage_cap_v[0]:.2f} V / {point.current_cap_a[0]:.3f} A; '
                               f'max configured power {point.max_power_w:.2f} W')
    evidence.add('bench_configuration', sha256=bench.sha256, source=bench.source.name,
                 devices={name: {'model': getattr(bench, name).model,
                                 'transport': getattr(bench, name).transport}
                          for name in devices})
    evidence.add('bench_connection_confirmation', token=bench_gate,
                 sha256=bench.sha256, devices=list(devices), serial_os_verified=list(devices))
    evidence.add('approval', token=wire_gate, variant='h03_single_cvcc',
                 authorization='START ONLY; per-point output ON requires additional gates')
    load = transport = None
    outcome = 'ABORTED'
    try:
        # Neither constructor requests PSU ON or KEL INPUT ON. Source-core owns
        # every subsequent SCPI write. No alternative transport/failover.
        load = open_load(bench, evidence)
        transport = open_psu(bench)
    except BaseException as exc:
        evidence.add('open_error', exception_type=type(exc).__name__)
        for item in (transport, load):
            if item is not None:
                try: item.close()
                except BaseException: pass
        evidence.add('outcome', outcome='ABORTED', physical_off_confirmed=False,
                     stop_reason='Device open failure; no further SCPI. Physical OFF verification required')
        print(f'ABORTED: connection open failed; check physical OFF. Evidence: {evidence.path}')
        return 2
    try:
        outcome = run_single_cvcc(transport, evidence, load, point)
    except BaseException as exc:
        # Core normally owns cleanup and closes both links; an unexpected
        # exception is never reason to retry after an uncertain link.
        evidence.add('runner_unhandled', exception_type=type(exc).__name__)
        outcome = 'ABORTED'
        for item in (transport, load):
            try: item.close()
            except BaseException: pass

    physically_off = False
    try:
        if input(f'Физически проверьте все выходы OFF и KEL INPUT OFF:\n{FINAL_TOKEN}\n> ').strip() == FINAL_TOKEN:
            physically_off = True
    except (EOFError, KeyboardInterrupt):
        pass
    if not physically_off and outcome == 'PASS':
        outcome = 'PARTIAL'
    evidence.add('outcome', outcome=outcome, physical_off_confirmed=physically_off,
                 stop_reason='H03 single CH1 complete; STOP for evidence review, do not continue full H03')
    report_path = ROOT / f'docs/hardware_reports/{now}_H03_SINGLE_CVCC_qualification.md'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8') as stream, contextlib.redirect_stdout(stream):
        report(evidence.path)
    print(f'{outcome}; Evidence: {evidence.path}\nПротокол для review: {report_path}\n'
          'STOP: do not run full H03 or any other energized stage without separate review.')
    return 0 if outcome == 'PASS' else 2
