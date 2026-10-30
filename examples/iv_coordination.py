"""O01: host-timed sequential PSU/DMM observer for a guarded voltage sweep.

No hardware trigger, no simultaneous sampling and no transport/reconnection logic.
A bounded observer failure propagates into voltage_sweep(), whose finally block
performs the existing global emergency OFF. Physical wiring approval and DMM
configuration are the responsibility of a separately reviewed hardware stage.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path
import time

from keithley2231a.device import Reading, SafetyError
from .iv_sweep import voltage_sweep, SweepPoint


# Fields inserted by the observer must stay numeric: voltage_sweep's external
# contract requires finite floats, and its CSV already owns the source UTC.
TIMING_FIELDS = (
    'dmm_voltage_v',
    'dmm_query_start_monotonic_s',
    'dmm_query_end_monotonic_s',
    'source_dmm_gap_s',
    'source_dmm_window_s',
    'dmm_query_duration_s',
    'psu_dmm_delta_v',
)


def _limit(value, name, *, minimum=0.0, maximum=60.0):
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not minimum < value <= maximum):
        raise ValueError(f'{name} must be a finite number in ({minimum}, {maximum}]')
    return float(value)


class TimedVoltageObserver:
    """Read a configured external DMM after a PSU Reading and audit host timing.

    The time window starts at the PSU Reading's *completion* timestamp and
    ends after the DMM query returns. It is NOT a measurement-synchronization
    uncertainty bound: integration times, settling and instrument latencies
    remain unknown without additional hardware characterization.

    This object never sends PSU commands. Use it only inside a guarded runner
    such as voltage_sweep() which shuts PSU outputs OFF if the observer fails.
    """

    def __init__(self, meter, *, max_start_lag_s=2.0, max_window_s=5.0,
                 max_voltage_difference_v=0.03):
        self.meter = meter
        self.max_start_lag_s = _limit(max_start_lag_s, 'max_start_lag_s')
        self.max_window_s = _limit(max_window_s, 'max_window_s')
        self.max_voltage_difference_v = _limit(max_voltage_difference_v,
                                               'max_voltage_difference_v', maximum=1.0)
        if self.max_window_s < self.max_start_lag_s:
            raise ValueError('max_window_s must be >= max_start_lag_s')
        if getattr(meter, 'live', None) is not True or getattr(meter, 'configured', None) is not True:
            raise SafetyError('DMM must be configured and live before O01 sampling')
        if not callable(getattr(meter, 'measure', None)):
            raise TypeError('DMM must implement measure()')

    def __call__(self, source: Reading) -> dict[str, float]:
        stamp = source.monotonic_s
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not math.isfinite(source.voltage_v)):
            raise SafetyError('Invalid PSU timestamp or voltage for O01')
        if not source.enabled or source.regulation not in ('CV', 'CC'):
            raise SafetyError('O01 requires an explicitly enabled CV/CC source')
        if getattr(self.meter, 'live', None) is not True or getattr(self.meter, 'configured', None) is not True:
            raise SafetyError('DMM is no longer configured/live')
        start = time.monotonic()
        gap = start - stamp
        if not math.isfinite(gap) or not 0 <= gap <= self.max_start_lag_s:
            raise SafetyError('O01 PSU-to-DMM start lag exceeds bound or clock regressed')
        result = self.meter.measure()  # no retries on transport failures
        if getattr(self.meter, 'live', None) is not True or getattr(self.meter, 'configured', None) is not True:
            raise SafetyError('DMM session became unready during measurement')
        end = time.monotonic()
        window, duration = end - stamp, end - start
        if (not math.isfinite(end) or not math.isfinite(window)
                or not math.isfinite(duration) or duration < 0
                or window > self.max_window_s):
            raise SafetyError('O01 PSU/DMM window exceeds bound or clock regressed')
        if not isinstance(result, dict) or set(result) != {'voltage_v'}:
            raise SafetyError('DMM must return exactly one voltage_v reading')
        value = result['voltage_v']
        if type(value) not in (int, float) or not math.isfinite(value):
            raise SafetyError('Invalid DMM voltage')
        difference = float(value) - source.voltage_v
        if abs(difference) > self.max_voltage_difference_v:
            raise SafetyError('O01 PSU/DMM voltage disagreement')
        return dict(dmm_voltage_v=float(value), dmm_query_start_monotonic_s=start,
                    dmm_query_end_monotonic_s=end, source_dmm_gap_s=gap,
                    source_dmm_window_s=window, dmm_query_duration_s=duration,
                    psu_dmm_delta_v=difference)


def verify_coordinated_csv(path: str | Path, points: list[SweepPoint]) -> int:
    """Cross-check *every* CSV field against returned points after outputs OFF."""
    fields = ('index', 'requested_v', 'timestamp_utc', 'monotonic_s',
              'measured_v', 'measured_a', 'measured_w', 'regulation',
              *sorted(TIMING_FIELDS))
    with Path(path).open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != fields:
            raise SafetyError('O01 CSV columns mismatch')
        rows = list(reader)
    if len(rows) != len(points):
        raise SafetyError('O01 CSV row count mismatch')
    for point, row in zip(points, rows):
        if (None in row or None in row.values()
                or row['timestamp_utc'] != point.timestamp_utc
                or row['regulation'] != point.regulation):
            raise SafetyError('O01 CSV text fields mismatch')
        expected = dict(index=point.index, requested_v=point.requested_v,
                        monotonic_s=point.monotonic_s, measured_v=point.measured_v,
                        measured_a=point.measured_a, measured_w=point.measured_w,
                        **point.external)
        if set(point.external) != set(TIMING_FIELDS):
            raise SafetyError('O01 point missing timing evidence')
        for col, reference in expected.items():
            try:
                value = float(row[col])
            except (ValueError, TypeError, KeyError) as exc:
                raise SafetyError(f'O01 CSV invalid {col}') from exc
            if (not math.isfinite(value) or not math.isfinite(reference)
                    or not math.isclose(value, reference, rel_tol=1e-12, abs_tol=1e-12)):
                raise SafetyError(f'O01 CSV {col} disagrees with measured point')
        ext = point.external
        if (ext['source_dmm_gap_s'] < 0 or ext['dmm_query_duration_s'] < 0
                or ext['source_dmm_window_s'] < 0
                or not math.isclose(ext['source_dmm_window_s'],
                                     ext['source_dmm_gap_s'] + ext['dmm_query_duration_s'],
                                     abs_tol=1e-9)):
            raise SafetyError('O01 invalid recorded time intervals')
    return len(rows)


def coordinated_voltage_sweep(device, meter, channel: int, voltages, *,
                              current_limit_a: float, settle_s=0.3,
                              csv_path: str | Path,
                              max_start_lag_s=2.0, max_window_s=5.0,
                              max_voltage_difference_v=0.03) -> list[SweepPoint]:
    """Reuse the existing guarded voltage_sweep and its mandatory OFF cleanup.

    There is no new output command path, USB/LAN discovery, DMM setup or wiring
    approval here. An approved instrument-specific stage must gate those before
    invoking this API on hardware. CSV is mandatory for O01 qualification.
    """
    if csv_path is None:
        raise ValueError('O01 requires a CSV evidence path')
    observer = TimedVoltageObserver(meter, max_start_lag_s=max_start_lag_s,
                                    max_window_s=max_window_s,
                                    max_voltage_difference_v=max_voltage_difference_v)
    points = voltage_sweep(device, channel, voltages,
                           current_limit_a=current_limit_a, settle_s=settle_s,
                           observer=observer, csv_path=csv_path)
    verify_coordinated_csv(csv_path, points)
    return points
