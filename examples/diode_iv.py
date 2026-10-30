"""Small profile-driven diode I-V example; NOT a laboratory orchestrator.

Default: plan only (no instrument connection). --run requires local operator
confirmation for EACH channel. DMM integration is an injected observer callback;
no second instrument driver lives in the Keithley 2231A library.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Callable, Mapping

from keithley2231a import (ChannelProfile, Profile, SerialTransport, Keithley2231A)
from iv_sweep import voltage_sweep
from iv_profile import IVProfileError, IVPlan, ChannelPlan, load_iv_profile, describe


def _device_profile(plan: IVPlan) -> Profile:
    # Non-selected channels are configured to low inert setpoints, never ON.
    caps = {c.channel: c for c in plan.channels}
    return Profile('iv-example-' + plan.name,
        tuple(ChannelProfile(max_voltage=max(caps[ch].voltages) if ch in caps else .1,
                             max_current=caps[ch].current_limit_a if ch in caps else .001,
                             initial_voltage=0.0, initial_current=0.0)
              for ch in (1, 2, 3)), wiring='independent', lock_front_panel=True)


def _meter_wrapper(plan: ChannelPlan, callback):
    def observer(source_reading):
        reading = callback(source_reading)
        if type(reading) is not dict or set(reading) != {'current_a'}:
            raise IVProfileError('External meter must return exactly {current_a: float}')
        current = reading['current_a']
        import math
        if (type(current) not in (int, float) or not math.isfinite(current) or
                current < 0 or current > plan.current_limit_a + .002):
            raise IVProfileError('Invalid external ammeter reading: fail closed')
        return {'external_current_a': float(current)}
    return observer


def _require_all_off(device: Keithley2231A, *, channel: int, phase: str) -> None:
    """Check real per-channel OFF readbacks; never infer them from an OFF write."""
    states = device.output_states()
    if states != {1: False, 2: False, 3: False}:
        raise IVProfileError(
            f'CH{channel} {phase}: every channel must have a confirmed OFF readback; '
            f'got {states!r}; operator must verify physical OFF'
        )


def run_profile(device: Keithley2231A, plan: IVPlan, *, output_dir: Path,
                approve: Callable[[str], None],
                external_observers: Mapping[int, Callable] | None = None) -> list[Path]:
    """Execute independent channels sequentially through the existing sweep.

    An external observer may be supplied by a separate integration project;
    it owns its physical connections and metrological limitations. No automatic
    reconfiguration or unwitnessed rewiring is performed between channels.
    """
    from pathlib import Path
    external_observers = external_observers or {}
    if not isinstance(output_dir, Path) or output_dir.exists():
        raise IVProfileError('Fresh, nonexisting output directory required')
    if any(c.selected_source == 'EXTERNAL_REQUIRED' and
           not callable(external_observers.get(c.channel)) for c in plan.channels):
        raise IVProfileError('External ammeter required: supply approved observer or use force')
    if not callable(approve):
        raise IVProfileError('Interactive operator confirmations required')
    # Ensure no output can be energized before whole-profile validation.
    _device_profile(plan)
    output_dir.mkdir(parents=True, exist_ok=False)
    produced = []
    verified = []
    for cp in plan.channels:
        ch = cp.channel
        # This readback cannot verify physical wiring, which is an operator gate.
        _require_all_off(device, channel=ch, phase='before wiring check')
        approve(f'CONFIRM IV CH{ch} PSU_ALL_OFF BALLAST_DIODE_POLARITY_WIRING_CHECKED')
        device.apply_profile(_device_profile(plan), confirmed_wiring='independent')
        _require_all_off(device, channel=ch, phase='after profile application')
        approve(f'CONFIRM IV CH{ch} ENABLE_ONLY_SELECTED_CHANNEL')
        observer = (_meter_wrapper(cp, external_observers[ch])
                    if cp.selected_source == 'EXTERNAL_REQUIRED' else None)
        # voltage_sweep() commands a global emergency_off() on every outcome.
        points = voltage_sweep(device, ch, cp.voltages, current_limit_a=cp.current_limit_a,
                               settle_s=plan.settle_s, observer=observer, stop_on_cc=True)
        if len(points) != len(cp.voltages) or any(p.regulation != 'CV' for p in points):
            raise IVProfileError('Sweep stopped early or left CV; no successful curve export')
        # emergency_off() has already run. Verify all three channels independently.
        _require_all_off(device, channel=ch, phase='after sweep')
        approve(f'CONFIRM IV CH{ch} CH1_CH2_CH3_PHYSICALLY_OFF')
        file = output_dir / f'channel_{ch}.csv'
        with file.open('x', newline='', encoding='utf-8') as fh:
            writer = csv.writer(fh)
            writer.writerow(('index', 'timestamp_utc', 'requested_psu_v', 'measured_psu_v',
                             'selected_current_a', 'psu_readback_a',
                             'estimated_diode_v', 'regulation', 'source',
                             'accuracy_qualified', 'forced_low_accuracy'))
            for point in points:
                i = (point.external['external_current_a']
                     if observer else point.measured_a)
                writer.writerow((point.index, point.timestamp_utc, point.requested_v, point.measured_v,
                                 i, point.measured_a, point.measured_v - i*cp.ballast_ohm,
                                 point.regulation, cp.selected_source, False, cp.force))
        produced.append(file)
        verified.append({
            'channel': ch,
            'point_count': len(points),
            'csv': file.name,
            'csv_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
            'current_source': cp.selected_source,
            'force': cp.force,
            'digital_off_confirmed': True,
            'physical_off_operator_confirmed': True,
            'current_accuracy_qualified': False,
        })
    # Presence of this sentinel means ALL requested channels finished. Earlier CSV
    # files without it are only partial results and are NOT successful sessions.
    with (output_dir / 'completed.json').open('x', encoding='utf-8') as fh:
        json.dump({
            'schema': 1,
            'status': 'EXAMPLE_COMPLETED_NOT_INDEPENDENT_HW_QUALIFICATION',
            'profile_name': plan.name,
            'profile_description_sha256': hashlib.sha256(json.dumps(
                describe(plan), ensure_ascii=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8')).hexdigest(),
            'channels': verified,
            'accuracy_qualified': False,
            'independent_scpi_audit_available': False,
            'source': 'HOST_TIMED_PSU_EXAMPLE',
        }, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    return produced


def _tty_gate(token: str):
    print('Check actual OFF/wiring and type exactly:', token)
    if input('> ').strip() != token:
        raise IVProfileError('Operator confirmation refused; physically verify OFF')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--port', help='Serial port (required only for --run)')
    p.add_argument('--output-dir', type=Path, help='New output directory, --run only')
    p.add_argument('--run', action='store_true', help='Operator-gated physical sweep')
    args = p.parse_args(argv)
    try:
        plan = load_iv_profile(args.profile)
    except IVProfileError as exc:
        p.error(str(exc))
    print(json.dumps(describe(plan), ensure_ascii=False, indent=2))
    if not args.run:
        print('DRY RUN: no ports opened, no SCPI or power mutation')
        return 0
    if not args.port or not args.output_dir:
        p.error('--port and --output-dir are mandatory for --run')
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('Interactive local TTY required; unattended runs forbidden')
    if any(c.selected_source == 'EXTERNAL_REQUIRED' for c in plan.channels):
        p.error('External DMM required; CLI cannot orchestrate it. Inject a verified '
                'observer from a separate integration project, or explicitly force PSU screening')
    if args.output_dir.exists():
        p.error('Output directory must not exist; no overwrite allowed')
    try:
        with Keithley2231A(SerialTransport(args.port, timeout=3)) as psu:
            paths = run_profile(psu, plan, output_dir=args.output_dir,
                                approve=_tty_gate)
    except BaseException:
        print('STOP: verify CH1/CH2/CH3 physically OFF; no automatic recovery/retry',
              file=sys.stderr)
        raise
    print('EXAMPLE ONLY, CURRENT ACCURACY UNQUALIFIED:', *(str(x) for x in paths))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
