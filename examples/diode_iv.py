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
import math
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


def _diode_dcv_wrapper(plan: ChannelPlan, callback):
    """A DMM voltage across diode, NOT series current or PSU current readback."""
    def observer(reading):
        if not reading.enabled or reading.regulation != 'CV':
            raise IVProfileError('Source left CV or is OFF; no valid diode observation')
        if not (0 <= reading.voltage_v <= max(plan.voltages) + .06 and
                0 <= reading.current_a <= plan.current_limit_a + .002):
            raise IVProfileError('Source channel outside bounded voltage/current envelope')
        dmm = callback(reading)
        if type(dmm) is not dict or set(dmm) != {'voltage_v'}:
            raise IVProfileError('Diode DCV observer must return exactly {voltage_v: float}')
        v = dmm['voltage_v']
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= reading.voltage_v:
            raise IVProfileError('Invalid direct diode DCV, polarity or voltage relationship')
        i = (reading.voltage_v - v) / plan.ballast_ohm
        if not 0 <= i <= plan.current_limit_a + .002:
            raise IVProfileError('Ballast-derived current exceeds fixed envelope')
        return {'diode_dcv_v': float(v), 'ballast_calculated_current_a': i}
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
                external_observers: Mapping[int, Callable] | None = None,
                diode_voltage_observers: Mapping[int, Callable] | None = None,
                max_powered_s: float | None = None) -> list[Path]:
    """Execute independent channels sequentially through the existing sweep.

    An external observer may be supplied by a separate integration project;
    it owns its physical connections and metrological limitations. No automatic
    reconfiguration or unwitnessed rewiring is performed between channels.
    """
    from pathlib import Path
    external_observers = external_observers or {}
    diode_voltage_observers = diode_voltage_observers or {}
    if not isinstance(output_dir, Path) or output_dir.exists():
        raise IVProfileError('Fresh, nonexisting output directory required')
    if any(c.selected_source == 'EXTERNAL_REQUIRED' and
           not callable(external_observers.get(c.channel)) for c in plan.channels):
        raise IVProfileError('External ammeter required: supply approved observer or use force')
    if any(c.selected_source == 'EXTERNAL_DIODE_DCV_BALLAST' and
           not callable(diode_voltage_observers.get(c.channel)) for c in plan.channels):
        raise IVProfileError('Diode DMM DCV observer required for whole selected run')
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
                    if cp.selected_source == 'EXTERNAL_REQUIRED' else
                    _diode_dcv_wrapper(cp, diode_voltage_observers[ch])
                    if cp.selected_source == 'EXTERNAL_DIODE_DCV_BALLAST' else None)
        # voltage_sweep() commands a global emergency_off() on every outcome.
        points = voltage_sweep(device, ch, cp.voltages, current_limit_a=cp.current_limit_a,
                               settle_s=plan.settle_s, observer=observer, stop_on_cc=True,
                               max_powered_s=max_powered_s)
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
                             'accuracy_qualified', 'forced_low_accuracy',
                             'measured_diode_dcv_v', 'method', 'precision_note'))
            for point in points:
                voltage_method = cp.selected_source == 'EXTERNAL_DIODE_DCV_BALLAST'
                i = (point.external['ballast_calculated_current_a'] if voltage_method else
                     point.external['external_current_a'] if observer else point.measured_a)
                v_diode = (point.external['diode_dcv_v'] if voltage_method else
                           point.measured_v - i*cp.ballast_ohm)
                writer.writerow((point.index, point.timestamp_utc, point.requested_v, point.measured_v,
                                 i, point.measured_a, v_diode,
                                 point.regulation, cp.selected_source, False, cp.force,
                                 point.external['diode_dcv_v'] if voltage_method else '',
                                 'PSU_DCV_MINUS_DMM_DIODE_DCV_OVER_R_SEQUENTIAL' if voltage_method else
                                 'CURRENT_BASED_ESTIMATE', 'UNQUALIFIED'))
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
            'external_voltage_used': cp.selected_source == 'EXTERNAL_DIODE_DCV_BALLAST',
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
    if any(c.selected_source in ('EXTERNAL_REQUIRED', 'EXTERNAL_DIODE_DCV_BALLAST') for c in plan.channels):
        p.error('External meter required: use guarded equipment runner for DCV, or '
                'inject an observer via run_profile; do not silently use PSU current')
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
