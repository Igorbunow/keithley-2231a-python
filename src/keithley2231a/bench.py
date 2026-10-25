"""Host-timed bench orchestration; synchronous SCPI is NOT hard synchronization.

Extend with PSU devices using the public Keithley2231A API and with external
meters providing a simple `measure()` mapping. No vendor-specific DMM required.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Callable, Mapping, Protocol, Sequence
from .device import Keithley2231A, Reading

class Meter(Protocol):
    def measure(self) -> Mapping[str, float]: ...

@dataclass(frozen=True)
class BenchAction:
    supply: str
    command: str  # set, output, power_on, power_off
    channel: int | None = None
    voltage: float | None = None
    current: float | None = None
    enabled: bool | None = None

@dataclass(frozen=True)
class BenchStep:
    delay_s: float
    actions: tuple[BenchAction, ...] = ()
    capture: bool = True
    label: str = ''

@dataclass(frozen=True)
class BenchCapture:
    label: str
    timestamp_utc: str
    monotonic_s: float
    supply_readings: dict[str, dict[int, Reading]]
    meter_readings: dict[str, dict[str, float]]

class BenchCoordinator:
    """A best-effort host-side coordinator for many PSUs and DMMs.

    SCPI/UART transactions happen sequentially; snapshots have timestamps and
    MUST NOT be interpreted as simultaneous measurements. Add hardware triggers
    in dedicated device adapters when deterministic timing is required.
    """
    def __init__(self, supplies: Mapping[str, Keithley2231A],
                 meters: Mapping[str, Meter] | None = None):
        self.supplies = dict(supplies)
        self.meters = dict(meters or {})
        if not self.supplies:
            raise ValueError('at least one power supply is required')
        if any(not isinstance(k,str) or not k for k in (*self.supplies, *self.meters)):
            raise ValueError('instrument names must be nonempty strings')
        if set(self.supplies) & set(self.meters):
            raise ValueError('supply and meter names must not overlap')
        if len({id(dev) for dev in self.supplies.values()}) != len(self.supplies):
            raise ValueError('same supply cannot be registered twice')

    def snapshot(self, *, label: str = '') -> BenchCapture:
        result: dict[str, dict[int, Reading]] = {}
        for name, psu in self.supplies.items():
            if psu.profile is None:
                raise ValueError(f'supply {name} has no active profile')
            channels = (1,3) if psu.profile.wiring in ('series','parallel') else (1,2,3)
            result[name] = {ch: psu.read_channel(ch) for ch in channels}
        other = {}
        for name, instrument in self.meters.items():
            readings = dict(instrument.measure())
            for metric, value in readings.items():
                if not isinstance(metric,str) or not metric or not isinstance(value,(int,float)) or not math.isfinite(value):
                    raise ValueError(f'invalid meter reading from {name}')
            other[name] = readings
        return BenchCapture(label, datetime.now(timezone.utc).isoformat(),time.monotonic(),result,other)

    def _action(self, action: BenchAction):
        if action.supply not in self.supplies:
            raise ValueError(f'unknown supply {action.supply!r}')
        psu = self.supplies[action.supply]
        if action.command == 'set':
            if action.channel is None: raise ValueError('set requires channel')
            psu.set_channel(action.channel, voltage=action.voltage, current=action.current)
        elif action.command == 'output':
            if action.channel is None or type(action.enabled) is not bool:
                raise ValueError('output requires channel and enabled=bool')
            psu.output(action.channel, action.enabled)
        elif action.command == 'power_on': psu.power_on()
        elif action.command == 'power_off': psu.power_off()
        else: raise ValueError(f'unknown action {action.command!r}')

    def run(self, steps: Sequence[BenchStep], *, shutdown_on_finish: bool = True) -> list[BenchCapture]:
        """Run actions and snapshots; OFF all supplies on error or by default.

        Delays are measured after the previous step, not absolute precision
        timestamps; UART scans can extend intervals. Manual preflight required.
        """
        for s in steps:
            if not isinstance(s, BenchStep) or not isinstance(s.delay_s,(int,float)) or not math.isfinite(s.delay_s) or s.delay_s < 0:
                raise ValueError('each step needs finite delay_s >= 0')
        captures = []
        failed = True
        try:
            for step in steps:
                time.sleep(step.delay_s)
                for action in step.actions:
                    self._action(action)
                if step.capture:
                    captures.append(self.snapshot(label=step.label))
            failed = False
            return captures
        finally:
            if shutdown_on_finish or failed:
                errors = []
                for name, psu in reversed(list(self.supplies.items())):
                    try: psu.emergency_off()
                    except Exception as exc: errors.append((name, exc))
                if errors:
                    raise RuntimeError(f'could not confirm shutdown of {len(errors)} supply/supplies: {errors}')
