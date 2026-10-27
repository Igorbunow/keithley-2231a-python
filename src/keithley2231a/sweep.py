"""Guarded host-timed DC voltage sweeps for static I-V characterization.

NOT a precision SMU/curve tracer: 2231A cannot sink current or sense at DUT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import csv
import math
from pathlib import Path
import time
from typing import Callable, Mapping, Sequence

from .device import Keithley2231A, Reading, SafetyError

@dataclass(frozen=True)
class SweepPoint:
    index: int
    requested_v: float
    timestamp_utc: str
    monotonic_s: float
    measured_v: float
    measured_a: float
    measured_w: float
    regulation: str
    external: dict[str, float] = field(default_factory=dict)


def voltage_sweep(device: Keithley2231A, channel: int, voltages: Sequence[float], *,
                  current_limit_a: float, settle_s: float = 0.3,
                  observer: Callable[[Reading], Mapping[str, float]] | None = None,
                  stop_on_cc: bool = False,
                  csv_path: str | Path | None = None) -> list[SweepPoint]:
    """Step voltage, measure after settling, optionally sample external instruments.

    Output starts OFF, then set first voltage/current before enabling. All output
    channels MUST already be in an approved safe state. Only independent topology
    with mutable (access='control') profile supported. Leaves all outputs OFF
    via global emergency_off on completion, including success.

    Measurements are NOT simultaneous. An `observer` may read a DMM after each
    PSU sample and return named finite numeric values for CSV/logging.
    """
    if not device.armed or device.profile is None or device.faulted:
        raise SafetyError('Apply a verified profile before sweeping')
    if device.profile.access != 'control' or device.profile.wiring != 'independent':
        raise SafetyError('Sweeps require control access and independent wiring')
    device._check_channel(channel)
    if not voltages:
        raise ValueError('voltages cannot be empty')
    if not math.isfinite(settle_s) or settle_s < 0:
        raise ValueError('settle_s must be finite and nonnegative')
    cap = device.profile.channels[channel-1]
    if (not isinstance(current_limit_a, (int,float)) or isinstance(current_limit_a,bool)
        or not math.isfinite(current_limit_a) or not 0 < current_limit_a <= cap.max_current
        or abs(current_limit_a * 1000-round(current_limit_a * 1000)) > 1e-6):
        raise ValueError('current_limit_a outside profiled range or unsupported resolution')
    for v in voltages:
        if (not isinstance(v, (int,float)) or isinstance(v,bool)
            or not math.isfinite(v) or not 0 <= v <= cap.max_voltage
            or abs(v*100-round(v*100)) > 1e-6):
            raise ValueError('voltage step outside profile or unsupported 10 mV resolution')
    # Avoid silently shutting down unrelated running channels on sweep exit.
    # Multi-source/bias experiments belong to a separate orchestration project.
    for other in (1, 2, 3):
        if other != channel and device.read_channel(other).enabled:
            raise SafetyError('voltage_sweep requires all other channels OFF; use a separate lab orchestrator for bias experiments')
    # Prepare files before energizing DUT. Stream rows only after successful sweeps.
    result = []
    try:
        device.verify_profile()
        device.output(channel, False)
        device.set_channel(channel, voltage=float(voltages[0]), current=float(current_limit_a))
        device.output(channel, True)
        for index, voltage in enumerate(voltages):
            device.set_channel(channel, voltage=float(voltage))
            time.sleep(settle_s)
            sample = device.read_channel(channel)
            external = dict(observer(sample)) if observer else {}
            if any(not isinstance(k,str) or not k for k in external):
                raise ValueError('observer keys must be nonempty strings')
            if any((not isinstance(x, (int,float)) or isinstance(x,bool) or not math.isfinite(x)) for x in external.values()):
                raise ValueError('observer results must contain finite numbers only')
            point = SweepPoint(index=index, requested_v=float(voltage),
                               timestamp_utc=sample.timestamp_utc,
                               monotonic_s=sample.monotonic_s,
                               measured_v=sample.voltage_v, measured_a=sample.current_a,
                               measured_w=sample.power_w, regulation=sample.regulation,
                               external={k:float(v) for k,v in external.items()})
            result.append(point)
            if stop_on_cc and sample.regulation == 'CC':
                break
    finally:
        # Global OFF is intentional: errors or normal completion both de-energize.
        device.emergency_off()
    if csv_path is not None:
        file = Path(csv_path)
        file.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted({k for point in result for k in point.external})
        with file.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['index','requested_v','timestamp_utc','monotonic_s',
                        'measured_v','measured_a','measured_w','regulation',*keys])
            for point in result:
                w.writerow([point.index,point.requested_v,point.timestamp_utc,
                            point.monotonic_s,point.measured_v,point.measured_a,
                            point.measured_w,point.regulation,
                            *[point.external.get(k,'') for k in keys]])
    return result
