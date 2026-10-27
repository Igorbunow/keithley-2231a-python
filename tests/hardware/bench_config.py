"""Validated configuration for the qualification bench: one source of endpoints.

No port or network connection is opened when importing or parsing this file.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from pathlib import Path
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
class BenchConfig:
    psu: Instrument
    load: Instrument
    meter: Instrument
    scope: Instrument
    source: Path
    sha256: str

    def validate(self):
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
    if set(data) != {"devices"} or set(data["devices"]) != {"psu", "load", "meter", "scope"}:
        raise ValueError("bench config must declare exactly psu/load/meter/scope")
    kwargs = {}
    for name, options in data["devices"].items():
        allowed = set(Instrument.__dataclass_fields__)
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValueError(f"{name}: unknown configuration field")
        kwargs[name] = Instrument(**options)
    return BenchConfig(**kwargs, source=source, sha256=hashlib.sha256(raw).hexdigest()).validate()


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
