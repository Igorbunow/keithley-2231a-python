"""Read-only, best-effort inventory of every configured qualification instrument.

This is not H01 and cannot award a hardware qualification PASS. In particular,
identity responses do not prove that PSU or load outputs are OFF.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable

from .factory import open_psu, open_load, open_meter, open_scope


@dataclass(frozen=True)
class ProbeResult:
    name: str
    endpoint: str
    status: str
    detail: str
    duration_s: float
    error_type: str | None = None


def _identity_text(raw: str) -> str:
    """Keep manufacturer/model/firmware for diagnostics, not serial numbers."""
    parts = raw.strip().split(',')
    if len(parts) >= 3:
        parts[2] = '[REDACTED]'
    else:
        raw = re.sub(r'(?i)(SN\s*:\s*)[^,\s]+', r'\1[REDACTED]', raw)
        return raw[:240]
    return ','.join(parts)[:240]


def _endpoint(device) -> str:
    if device.transport == 'serial':
        return f'{device.port} ({device.baudrate} baud, {device.usb_vid}:{device.usb_pid})'
    return f'{device.host}:{device.network_port} ({device.transport})'


def _psu(config, evidence) -> str:
    # Reuse the same VID/PID verification and transport as qualification H01.
    from qualification import verify_serial_adapter
    device = config.psu
    verify_serial_adapter(device.port, f'{device.usb_vid}:{device.usb_pid}')
    transport = open_psu(config)
    try:
        identity = transport.query('*IDN?')
        if 'KEITHLEY' not in identity.upper() or device.model not in identity.upper():
            raise ValueError('Keithley identity/model mismatch')
        return _identity_text(identity)
    finally:
        transport.close()


def _load(config, evidence) -> str:
    load = open_load(config, evidence)
    try:
        identity = load.identify()
        state = load.input_state()  # Query only, never change the load state.
        return f'{_identity_text(identity)}; INPUT={state}'
    finally:
        load.close()


def _meter(config, evidence) -> str:
    meter = open_meter(config, evidence)
    try:
        identity = meter.identify()
        return _identity_text(identity) + '; LANG=SCPI'
    finally:
        meter.close()


def _scope(config, evidence) -> str:
    # Existing adapter ensures bounded e*Scope communication and logs replies.
    scope = open_scope(config, evidence)
    identity = scope.command('*IDN?')
    parts = [part.strip().upper() for part in identity.split(',')]
    if len(parts) < 2 or parts[0] != 'TEKTRONIX' or parts[1] not in ('TDS 3064B', 'TDS3064B'):
        raise ValueError('TDS3064B identity/model mismatch')
    return _identity_text(identity)


PROBES: tuple[tuple[str, Callable], ...] = (
    ('psu', _psu), ('load', _load), ('meter', _meter), ('scope', _scope)
)


def run_inventory(config, evidence, *, probes=None) -> list[ProbeResult]:
    """Check each instrument once; a failure must never skip the next probe."""
    tasks = PROBES if probes is None else probes
    results = []
    for name, probe in tasks:
        start = time.monotonic()
        status, detail, error_type = 'PASS', '', None
        try:
            detail = probe(config, evidence)
        except Exception as exc:
            status, error_type = 'FAIL', type(exc).__name__
            detail = str(exc) or error_type
        result = ProbeResult(name=name, endpoint=_endpoint(getattr(config, name)),
                             status=status, detail=detail,
                             duration_s=round(time.monotonic() - start, 3),
                             error_type=error_type)
        results.append(result)
        evidence.add('equipment_probe', instrument=name, endpoint=result.endpoint,
                     status=status, detail=detail, duration_s=result.duration_s,
                     error_type=error_type)
    evidence.add('equipment_inventory_summary', total=len(results),
                 passed=sum(item.status == 'PASS' for item in results),
                 failed=sum(item.status == 'FAIL' for item in results))
    return results


def print_inventory(results: list[ProbeResult], evidence_path) -> None:
    print('\nRead-only equipment inventory / Диагностика подключений:')
    for result in results:
        print(f'  [{result.status:4}] {result.name:5} {result.endpoint}')
        print(f'         {result.error_type + ": " if result.error_type else ""}{result.detail} '
              f'({result.duration_s:.3f} s)')
    passed = sum(item.status == 'PASS' for item in results)
    print(f'Итого: доступно {passed}/{len(results)}, недоступно {len(results) - passed}/{len(results)}.')
    print(f'Журнал: {evidence_path}')
    print('Это только проверка связи/идентификации, не проверка выходов, режимов и безопасности.')
