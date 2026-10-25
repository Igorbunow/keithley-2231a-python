"""Immutable, validated user profiles. Per-channel limits cannot exceed nameplate."""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path

class ProfileError(ValueError):
    pass
class LimitsError(ProfileError):
    pass

@dataclass(frozen=True)
class ChannelProfile:
    max_voltage: float
    max_current: float
    initial_voltage: float = 0.0
    initial_current: float = 0.1
    # auto: no required CV/CC state; cv/cc trips on mismatch during monitoring
    expected_regulation: str = "auto"
    # max monitored voltage/current, defaults to profile setpoint caps
    record: bool = True
    # Fault if measured output V/I exceeds these caps (+ readback quantization margin)
    trip_on_measurement: bool = True

    def validate(self, ch: int) -> None:
        v_max = 5.0 if ch == 3 else 30.0
        for label in ("max_voltage", "max_current", "initial_voltage", "initial_current"):
            val = getattr(self, label)
            if not isinstance(val, (float, int)) or isinstance(val, bool) or not math.isfinite(val):
                raise ProfileError(f"CH{ch}: {label} must be finite number")
        if not (0 < self.max_voltage <= v_max): raise LimitsError(f"CH{ch}: 0 < max_voltage <= {v_max}")
        if not (0 < self.max_current <= 3): raise LimitsError(f"CH{ch}: 0 < max_current <= 3")
        if not (0 <= self.initial_voltage <= self.max_voltage): raise LimitsError(f"CH{ch}: initial_voltage outside profile")
        if not (0 <= self.initial_current <= self.max_current): raise LimitsError(f"CH{ch}: initial_current outside profile")
        # PSU can accept 0.01 V / 0.001 A increments; do not silently round UP.
        for label, step in (("max_voltage", .01), ("initial_voltage", .01),
                            ("max_current", .001), ("initial_current", .001)):
            val = getattr(self, label)
            if abs(val / step - round(val / step)) > 1e-6:
                raise ProfileError(f"CH{ch}: {label} resolution {step}")
        if self.expected_regulation not in ("auto", "cv", "cc"):
            raise ProfileError("expected_regulation must be auto/cv/cc")
        if not isinstance(self.record, bool) or not isinstance(self.trip_on_measurement, bool):
            raise ProfileError("record and trip_on_measurement must be boolean")

@dataclass(frozen=True)
class PowerStep:
    """Channel energizing order; delay occurs BEFORE this step when powering ON.

    Power-down traverses the same steps in reverse, using each step's original
    delay before the preceding step in the original power-up order.
    """
    channel: int
    delay_s: float = 0.0

    def __post_init__(self):
        if type(self.channel) is not int or self.channel not in (1, 2, 3):
            raise ProfileError('PowerStep channel must be 1, 2 or 3')
        if (not isinstance(self.delay_s, (float, int)) or isinstance(self.delay_s, bool)
                or not math.isfinite(self.delay_s) or self.delay_s < 0):
            raise ProfileError('PowerStep delay_s must be finite and >= 0')

@dataclass(frozen=True)
class Profile:
    name: str
    channels: tuple[ChannelProfile, ChannelProfile, ChannelProfile]
    wiring: str = "independent"  # independent, series, parallel, tracking
    # Separate from combining: Tracking has voltage ratios based on initial setpoints.
    lock_front_panel: bool = True
    # seconds > 0; periodic verification during monitoring
    verify_every_s: float = 5.0
    # control: full guarded APIs; power_only: only OFF/ON of preset outputs.
    access: str = "control"
    # Power-up sequence; reverse is automatically used for power-down.
    # Only independent wiring currently supports timed channel order.
    power_sequence: tuple[PowerStep, ...] = ()

    def __post_init__(self):
        if not isinstance(self.channels, tuple) or len(self.channels) != 3:
            raise ProfileError("channels must be immutable tuple of 3 ChannelProfile entries")
        if not all(isinstance(x, ChannelProfile) for x in self.channels):
            raise ProfileError("all channels must be ChannelProfile")
        for ch, settings in enumerate(self.channels, 1): settings.validate(ch)
        if not isinstance(self.name, str) or not self.name.strip():
            raise ProfileError("profile name required")
        if not isinstance(self.lock_front_panel, bool):
            raise ProfileError("lock_front_panel must be bool")
        if self.wiring not in ("independent", "series", "parallel", "tracking"):
            raise ProfileError("invalid wiring mode")
        if not isinstance(self.verify_every_s, (int, float)) or isinstance(self.verify_every_s, bool) or not math.isfinite(self.verify_every_s) or self.verify_every_s <= 0:
            raise ProfileError("verify_every_s must be > 0")
        if self.access not in ("control", "power_only"):
            raise ProfileError('access must be control/power_only')
        if not isinstance(self.power_sequence, tuple) or not all(isinstance(s, PowerStep) for s in self.power_sequence):
            raise ProfileError('power_sequence must be immutable tuple of PowerStep')
        channels = [step.channel for step in self.power_sequence]
        if len(channels) != len(set(channels)):
            raise ProfileError('power_sequence channels cannot repeat')
        if self.power_sequence and self.wiring != 'independent':
            raise ProfileError('power_sequence requires independent wiring')
        if self.access == 'power_only' and self.wiring != 'independent':
            raise ProfileError('power_only currently requires independent wiring')
        if self.wiring == "tracking":
            if self.channels[0].initial_voltage == 0 or self.channels[1].initial_voltage == 0:
                raise ProfileError("tracking ratio requires nonzero initial voltage on CH1, CH2")

    @classmethod
    def from_json(cls, filepath: str | Path) -> "Profile":
        data = json.loads(Path(filepath).read_text(encoding="utf-8"))
        allowed = {"name", "channels", "wiring", "lock_front_panel", "verify_every_s",
                   "access", "power_sequence"}
        unknown = set(data) - allowed
        if unknown: raise ProfileError(f"unknown profile keys: {unknown}")
        raw = data["channels"]
        if set(raw) != {"1", "2", "3"}: raise ProfileError("channels must be exactly 1,2,3")
        return cls(name=data["name"], channels=tuple(ChannelProfile(**raw[str(ch)]) for ch in (1,2,3)),
                   wiring=data.get("wiring", "independent"),
                   lock_front_panel=data.get("lock_front_panel", True),
                   verify_every_s=data.get("verify_every_s", 5.0),
                   access=data.get("access", "control"),
                   power_sequence=tuple(PowerStep(**s) for s in data.get('power_sequence', [])))

    @classmethod
    def unrestricted(cls, *, name: str = "rated-range", lock_front_panel: bool = False) -> "Profile":
        """No user-specific reduced limits; still constrained by PSU ratings.

        IMPORTANT: This is NOT a raw/unsafe SCPI bypass. It enforces hardware
        voltage caps at nameplate values and still requires apply_profile().
        """
        return cls(name=name, channels=(
            ChannelProfile(30.0, 3.0, 0.0, 0.1),
            ChannelProfile(30.0, 3.0, 0.0, 0.1),
            ChannelProfile(5.0, 3.0, 0.0, 0.1),
        ), lock_front_panel=lock_front_panel)
