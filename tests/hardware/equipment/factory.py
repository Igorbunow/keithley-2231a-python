"""Single construction/ownership route for qualification equipment.

Do not auto-fallback between transports, auto-reconnect, or mutate outputs on open.
"""
from __future__ import annotations

from bench_config import BenchConfig
from .kel103 import KEL103Serial, KEL103UDP
from .dmm7510 import DMM7510
from .tds3064 import TDS3064B


def open_psu(config: BenchConfig):
    from keithley2231a import SerialTransport
    device = config.psu
    return SerialTransport(device.port, baudrate=device.baudrate, timeout=device.timeout_s)


def open_load(config: BenchConfig, evidence):
    device = config.load
    if device.transport == "serial":
        return KEL103Serial(device.port, timeout=device.timeout_s, evidence=evidence,
                            expected_vid=int(device.usb_vid, 16),
                            expected_pid=int(device.usb_pid, 16),
                            baudrate=device.baudrate)
    if device.transport == "udp":
        return KEL103UDP(device.host, port=device.network_port, timeout=device.timeout_s,
                         evidence=evidence)
    raise ValueError("unsupported KEL103 transport")


def open_meter(config: BenchConfig, evidence):
    from keithley2231a import TcpTransport
    device = config.meter
    return DMM7510(TcpTransport(device.host, device.network_port,
                                timeout=device.timeout_s, terminator=device.terminator.encode('ascii')),
                   evidence)


def open_scope(config: BenchConfig, evidence):
    return TDS3064B(evidence.path.parent, evidence, host=config.scope.host,
                    port=config.scope.network_port, timeout_s=config.scope.timeout_s)
