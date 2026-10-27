"""Shared, configuration-derived connection confirmation for hardware stages.

No port is opened here. USB identity is checked via OS discovery, not by
retyping VID:PID; operator confirmation is still required for physical wiring.
"""
from __future__ import annotations

from pathlib import Path


_NAMES = {'psu': 'Keithley 2231A', 'load': 'KEL103',
          'meter': 'DMM7510', 'scope': 'TDS3064B'}


def required_devices(stage_id: str, *, recall=False, powered=False, high_current=False):
    """All connections which a stage may open, including nested adapters."""
    if stage_id not in {f'H{x:02}' for x in range(1, 13)} | {'H04P'}:
        raise ValueError(f'unknown qualification stage: {stage_id}')
    if stage_id == 'H05':
        return ('psu', 'load', 'scope')
    if stage_id == 'H08':
        return ('psu', 'meter')
    if stage_id in ('H09', 'H10') and (powered or high_current):
        return ('psu', 'load', 'meter')
    if stage_id in ('H03', 'H04P', 'H06', 'H07', 'H11') and not (stage_id == 'H06' and recall):
        return ('psu', 'load')
    return ('psu',)


def verify_serial_adapter(instrument, available_ports=None):
    """Require configured path AND configured VID:PID; do not open UART."""
    if instrument.transport != 'serial':
        raise ValueError('USB identity check requires serial transport')
    if available_ports is None:
        from serial.tools import list_ports
        available_ports = list_ports.comports()
    selected = Path(instrument.port).resolve()
    expected = (int(instrument.usb_vid, 16), int(instrument.usb_pid, 16))
    for candidate in available_ports:
        if Path(candidate.device).resolve() != selected:
            continue
        actual = (candidate.vid, candidate.pid)
        if actual != expected:
            observed = 'unknown' if None in actual else f'{actual[0]:04X}:{actual[1]:04X}'
            raise ValueError(f'USB adapter {instrument.port}: observed {observed}, '
                             f'configured {expected[0]:04X}:{expected[1]:04X}')
        return True
    raise ValueError(f'USB adapter {instrument.port} is not present in serial-port inventory')


def connection_token(bench, stage_id, *, recall=False, powered=False, high_current=False,
                     profile_only=False):
    suffix = ('-PROFILE-ONLY' if profile_only else '-RECALL' if recall else '-HIGH-CURRENT' if high_current
              else '-POWERED' if powered else '')
    return f'CONFIRM BENCH {stage_id}{suffix} SHA256={bench.sha256[:12].upper()}'


def connection_card(bench, stage_id, devices, *, recall=False, powered=False, high_current=False,
                    profile_only=False):
    lines = [f'ПОДКЛЮЧЕНИЯ ИЗ BENCH CONFIG: {bench.source.name} (SHA256 {bench.sha256})',
             f'Этап: {stage_id}; используются: {", ".join(devices)}']
    for name in devices:
        device = getattr(bench, name)
        if device.transport == 'serial':
            detail = (f'{device.port}, {device.baudrate} baud, 8N1, '
                      f'USB VID:PID={device.usb_vid.upper()}:{device.usb_pid.upper()}, '
                      f'timeout={device.timeout_s:g} s')
        else:
            detail = (f'{device.host}:{device.network_port}, {device.transport}, '
                      f'timeout={device.timeout_s:g} s')
            if device.transport == 'tcp':
                detail += f', terminator={device.terminator!r}'
        lines.append(f'  {_NAMES[name]} ({device.model}): {detail}')
    lines.append('Проверьте физическое соответствие приборов, кабелей и указанных интерфейсов. '
                 'Данные порта НЕ вводятся вручную; USB VID:PID проверяются автоматически по ОС.')
    lines.append('Это подтверждение соединений НЕ разрешает включение выходов и НЕ заменяет '
                 'отдельное подтверждение схемы/нагрузки.')
    lines.append('Общий токен: ' + connection_token(bench, stage_id, recall=recall,
                                                  powered=powered, high_current=high_current,
                                                  profile_only=profile_only))
    return '\n'.join(lines)
