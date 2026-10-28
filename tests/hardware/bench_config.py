"""Validated configuration for the qualification bench: one source of endpoints.

No port or network connection is opened when importing or parsing this file.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
import math
import re
try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "config/bench.example.toml"
_ACTIVE = ContextVar("bench_config", default=None)


@dataclass(frozen=True)
class Instrument:
    model: str
    transport: str
    port: str | None = None
    host: str | None = None
    network_port: int | None = None
    baudrate: int | None = None
    timeout_s: float = 2.0
    usb_vid: str | None = None
    usb_pid: str | None = None
    terminator: str = "\n"


@dataclass(frozen=True)
class H03SingleCVCC:
    """Operator-reviewed one-channel test point; no implicit defaults."""
    channel: int
    initial_voltage_v: tuple[float, float, float]
    initial_current_a: tuple[float, float, float]
    voltage_cap_v: tuple[float, float, float]
    current_cap_a: tuple[float, float, float]
    cv_voltage_v: float
    cv_current_limit_a: float
    cv_resistance_ohm: float
    cc_voltage_v: float
    cc_current_limit_a: float
    cc_resistance_ohm: float
    max_power_w: float

    def validate(self):
        if type(self.channel) is not int or self.channel != 1:
            raise ValueError('H03 single CV/CC qualification is CH1-only')
        for name in ('initial_voltage_v', 'initial_current_a', 'voltage_cap_v', 'current_cap_a'):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(values) != 3:
                raise ValueError(f'H03 single {name}: exactly three channels required')
            if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
                raise ValueError(f'H03 single {name}: positive finite numbers required')
        for name in ('cv_voltage_v', 'cv_current_limit_a', 'cv_resistance_ohm',
                     'cc_voltage_v', 'cc_current_limit_a', 'cc_resistance_ohm', 'max_power_w'):
            x = getattr(self, name)
            if type(x) not in (int, float) or not math.isfinite(x) or x <= 0:
                raise ValueError(f'H03 single {name}: positive finite number required')
        if any(v > c for v, c in zip(self.initial_voltage_v, self.voltage_cap_v)) or \
           any(v > c for v, c in zip(self.initial_current_a, self.current_cap_a)):
            raise ValueError('H03 single: initial setpoints exceed software caps')
        # Absolute qualification envelope; TOML cannot enlarge approved energy or
        # transport scope. CH2/CH3 remain OFF with conservative presets.
        if any(v > c for v, c in zip(self.voltage_cap_v, (4.00, 4.05, 4.10))) or \
           any(v > c for v, c in zip(self.current_cap_a, (.300, .400, .500))):
            raise ValueError('H03 single: caps exceed reviewed channel envelope')
        if self.cv_voltage_v > min(4.0, self.voltage_cap_v[0]) or \
           self.cc_voltage_v > min(4.0, self.voltage_cap_v[0]) or \
           self.cv_current_limit_a > min(.300, self.current_cap_a[0]) or \
           self.cc_current_limit_a > min(.250, self.current_cap_a[0]) or \
           self.max_power_w > 1.2:
            raise ValueError('H03 single: CV/CC point exceeds reviewed ceiling')
        if self.cv_resistance_ohm != 40.0 or self.cc_resistance_ohm != 12.0:
            raise ValueError('H03 single: only reviewed KEL103 CR40/CR12 are supported')
        if self.cv_voltage_v / self.cv_resistance_ohm >= self.cv_current_limit_a or \
           self.cc_voltage_v / self.cc_resistance_ohm <= self.cc_current_limit_a:
            raise ValueError('H03 single: configured CV/CC modes cannot be differentiated')
        if (self.cv_voltage_v * self.cv_current_limit_a > self.max_power_w + 1e-9 or
                self.cc_voltage_v * self.cc_current_limit_a > self.max_power_w + 1e-9):
            raise ValueError('H03 single: configured power ceiling is too low for setpoints')
        return self


@dataclass(frozen=True)
class BenchConfig:
    psu: Instrument
    load: Instrument
    meter: Instrument
    scope: Instrument
    h03_single_cvcc: H03SingleCVCC | None
    source: Path
    sha256: str

    def validate(self):
        if self.h03_single_cvcc is not None:
            self.h03_single_cvcc.validate()
        for name, device in (("psu", self.psu), ("load", self.load),
                             ("meter", self.meter), ("scope", self.scope)):
            if not device.model or not isinstance(device.timeout_s, (float, int)) or not 0 < device.timeout_s <= 120:
                raise ValueError(f"{name}: invalid model or timeout")
            if device.transport in ("serial",):
                if not device.port or not device.port.startswith("/dev/") or not isinstance(device.baudrate, int) or not 0 < device.baudrate <= 3000000:
                    raise ValueError(f"{name}: invalid serial connection")
                for field in ("usb_vid", "usb_pid"):
                    number = getattr(device, field)
                    if number is None or not re.fullmatch(r"[0-9a-fA-F]{4}", number):
                        raise ValueError(f"{name}: invalid {field}")
            else:
                if not device.host or not isinstance(device.network_port, int) or not 0 < device.network_port < 65536:
                    raise ValueError(f"{name}: invalid network connection")
                # Accept hostnames and IP addresses. Reject whitespace/URL syntax.
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", device.host):
                    raise ValueError(f"{name}: invalid network host")
        if self.psu.transport != "serial" or self.load.transport not in ("serial", "udp"):
            raise ValueError("unsupported PSU/load transport configuration")
        if self.meter.transport != "tcp" or self.scope.transport != "escope_http":
            raise ValueError("unsupported DMM7510/TDS3064 transport configuration")
        if self.psu.model != "2231A-30-3" or self.load.model != "KEL103" or self.meter.model != "DMM7510" or self.scope.model != "TDS3064B":
            raise ValueError("unexpected bench instrument models")
        return self


def load_bench(path: str | Path):
    source = Path(path).expanduser().resolve()
    raw = source.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    if set(data) not in ({'devices'}, {'devices', 'qualification'}) or \
       set(data['devices']) != {'psu', 'load', 'meter', 'scope'}:
        raise ValueError('bench config must define devices and optional qualification section')
    point = None
    if 'qualification' in data:
        if set(data['qualification']) != {'h03_single_cvcc'}:
            raise ValueError('only qualification.h03_single_cvcc is currently supported')
        raw_point = data['qualification']['h03_single_cvcc']
        if not isinstance(raw_point, dict) or set(raw_point) != set(H03SingleCVCC.__dataclass_fields__):
            raise ValueError('H03 single: specify the complete exact configuration schema')
        for key in ('initial_voltage_v', 'initial_current_a', 'voltage_cap_v', 'current_cap_a'):
            if not isinstance(raw_point[key], list):
                raise ValueError(f'H03 single {key}: expected array')
            raw_point[key] = tuple(raw_point[key])
        point = H03SingleCVCC(**raw_point).validate()
    kwargs = {}
    for name, options in data["devices"].items():
        allowed = set(Instrument.__dataclass_fields__)
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValueError(f"{name}: unknown configuration field")
        kwargs[name] = Instrument(**options)
    return BenchConfig(**kwargs, h03_single_cvcc=point, source=source, sha256=hashlib.sha256(raw).hexdigest()).validate()


def activate_bench(config: BenchConfig):
    return _ACTIVE.set(config.validate())


def current_bench() -> BenchConfig:
    config = _ACTIVE.get()
    if config is None:
        # Preflight/tests only; the command runner explicitly activates its config.
        return load_bench(EXAMPLE)
    return config


def bench_summary(config: BenchConfig):
    """Operator-readable connection information from a single validated config."""
    devices = config
    def describe(device):
        return (f"{device.port} @ {device.baudrate} baud 8N1" if device.transport == "serial"
                else f"{device.host}:{device.network_port} via {device.transport}")
    return (f"Bench config: {config.source.name} (SHA256 {config.sha256[:16]}…)\n"
            f"Keithley: {describe(devices.psu)}; KEL103: {describe(devices.load)}; "
            f"DMM7510: {describe(devices.meter)}; TDS3064B: {describe(devices.scope)}")
