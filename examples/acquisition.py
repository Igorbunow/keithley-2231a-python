"""Streaming telemetry, statistical aggregation and A.h / W.h integration."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
import csv
import math
import threading
import time
from pathlib import Path
from typing import Callable
from keithley2231a.device import Keithley2231A, Reading

@dataclass
class RollingStats:
    """Welford population standard deviation. O(1) memory, indefinite lifetime."""
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float = math.inf
    maximum: float = -math.inf

    def add(self, value: float):
        self.count += 1
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)

    @property
    def stddev(self):
        return math.sqrt(max(0, self.m2 / self.count)) if self.count else math.nan

    def as_dict(self):
        return {'count': self.count,
                'min': self.minimum if self.count else None,
                'max': self.maximum if self.count else None,
                'mean': self.mean if self.count else None,
                'stddev': self.stddev if self.count else None}

@dataclass
class ChannelStats:
    voltage: RollingStats = field(default_factory=RollingStats)
    current: RollingStats = field(default_factory=RollingStats)
    power: RollingStats = field(default_factory=RollingStats)
    ampere_hours: float = 0.0
    watt_hours: float = 0.0
    duration_s: float = 0.0
    skipped_gaps: int = 0
    _previous: Reading | None = None

    def add(self, value: Reading, *, max_gap_s: float = 15.0):
        self.voltage.add(value.voltage_v)
        self.current.add(value.current_a)
        self.power.add(value.power_w)
        prev = self._previous
        if prev is not None:
            dt = value.monotonic_s - prev.monotonic_s
            if dt <= 0 or dt > max_gap_s:
                self.skipped_gaps += 1
            elif prev.enabled and value.enabled:
                self.duration_s += dt
                self.ampere_hours += (prev.current_a + value.current_a) * .5 * dt / 3600.0
                self.watt_hours += (prev.power_w + value.power_w) * .5 * dt / 3600.0
        self._previous = value

    def reset_statistics(self, *, reset_energy: bool = False):
        self.voltage, self.current, self.power = RollingStats(), RollingStats(), RollingStats()
        if reset_energy:
            self.ampere_hours = 0.0
            self.watt_hours = 0.0
            self.duration_s = 0.0
            self.skipped_gaps = 0
            self._previous = None

    def as_dict(self):
        return {'voltage_v': self.voltage.as_dict(), 'current_a': self.current.as_dict(),
                'power_w': self.power.as_dict(), 'Ah': self.ampere_hours,
                'Wh': self.watt_hours, 'active_duration_s': self.duration_s,
                'skipped_gaps': self.skipped_gaps}

class Acquisition:
    """Read channels sequentially: sample times are staggered, not synchronized.

    A CSV row stores device-queried V and I measured at slightly different instants;
    VI and energy are estimates. Do not use for fast transient or precise metrology.
    """
    HEADER = ['timestamp_utc','monotonic_s','channel','label','voltage_v','current_a',
              'power_w','regulation','enabled','voltage_set_v','current_limit_set_a']

    def __init__(self, device: Keithley2231A, *, interval_s: float = 2.0,
                 csv_path: str | Path | None = None, max_gap_s: float | None = None):
        if not math.isfinite(interval_s) or interval_s < 0.1:
            raise ValueError('interval >= 0.1 seconds')
        self.device = device
        self.interval_s = interval_s
        self.max_gap_s = max_gap_s if max_gap_s is not None else max(15, 5 * interval_s)
        if not math.isfinite(self.max_gap_s) or self.max_gap_s <= 0: raise ValueError('invalid gap')
        self.csv_path = Path(csv_path) if csv_path is not None else None
        self.stats = {1: ChannelStats(), 2: ChannelStats(), 3: ChannelStats()}
        self._lock = threading.RLock()

    def reset_statistics(self, channel: int | None = None, *, reset_energy: bool = False):
        with self._lock:
            for ch in ((channel,) if channel else (1,2,3)):
                self.stats[ch].reset_statistics(reset_energy=reset_energy)

    def sample(self) -> list[Reading]:
        profile = self.device.profile
        if profile is None: raise ValueError('apply_profile() first')
        channels = (1, 3) if profile.wiring in ('series', 'parallel') else (1,2,3)
        results = []
        for ch in channels:
            # Always poll even if recording is disabled: do not disable protection!
            reading = self.device.read_channel(ch)
            if profile.wiring in ('series', 'parallel'):
                record = profile.channel3.record if ch == 3 else True
            else:
                record = profile.channels[ch-1].record
            if record:
                with self._lock: self.stats[ch].add(reading, max_gap_s=self.max_gap_s)
                results.append(reading)
        return results

    def export_summary(self, path: str | Path):
        """Persist active profile and statistics separately from time series CSV."""
        profile = self.device.profile
        data = {
            'generated_utc': datetime.now(timezone.utc).isoformat(),
            'instrument': self.device.instrument_id,
            'profile': asdict(profile) if profile is not None else None,
            'interval_requested_s': self.interval_s,
            'max_gap_s': self.max_gap_s,
            'stats_by_channel': {str(ch): st.as_dict() for ch, st in self.stats.items()}}
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

    def run(self, duration_s: float | None = None, *, stop_event: threading.Event | None = None,
            on_sample: Callable[[list[Reading]], None] | None = None) -> dict[int, ChannelStats]:
        if duration_s is not None and duration_s <= 0: raise ValueError('duration_s must be positive')
        stop = stop_event or threading.Event()
        start = time.monotonic()
        file = None
        try:
            if self.csv_path:
                self.csv_path.parent.mkdir(parents=True, exist_ok=True)
                existed = self.csv_path.exists() and self.csv_path.stat().st_size != 0
                file = self.csv_path.open('a', newline='', encoding='utf-8')
                writer = csv.writer(file)
                if not existed: writer.writerow(self.HEADER)
            else:
                writer = None
            next_due = start
            while not stop.is_set() and (duration_s is None or time.monotonic() - start < duration_s):
                wait = max(0, next_due - time.monotonic())
                if duration_s is not None:
                    wait = min(wait, max(0, start + duration_s - time.monotonic()))
                if stop.wait(wait): break
                # Waiting for the next interval may reach the acquisition deadline.
                if duration_s is not None and time.monotonic() - start >= duration_s:
                    break
                readings = self.sample()
                if writer:
                    for r in readings:
                        writer.writerow([r.timestamp_utc, f'{r.monotonic_s:.6f}', r.channel, r.label,
                                         r.voltage_v, r.current_a, r.power_w, r.regulation,
                                         int(r.enabled), r.voltage_set_v, r.current_limit_set_a])
                    file.flush()
                if on_sample: on_sample(readings)
                # No catch-up burst if one scan takes longer than requested interval.
                next_due = max(next_due + self.interval_s, time.monotonic())
            return self.stats
        except BaseException:
            try: self.device.emergency_off()
            except Exception: pass
            raise
        finally:
            if file: file.close()
