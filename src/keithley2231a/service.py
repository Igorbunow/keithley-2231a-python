"""Typed service-profile and SCPI grammar for the Keithley device driver.

This module intentionally has NO transport import, execute method or physical
hardware access. Planning alone cannot transmit SCPI. The guarded device.service_operation()
method is the sole driver service-dispatch path and is disabled for calibration
without a reviewed manufacturer procedure.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


class ServicePolicyError(ValueError):
    """Service-only policy or command validation failed; never continue."""


# Explicit operation names, not arbitrary SCPI strings or wildcard privileges.
# All remain OFFLINE SIMULATION ONLY; ``calibration_initialize`` is deliberately
# not included in any example profile because it replaces coefficients.
PERMISSIONS = frozenset({
    'wait_pending', 'self_test', 'clear_events', 'reset_settings',
    'reset_status', 'trigger_bus', 'save_setup', 'recall_setup',
    'apply_channel', 'calibration_unlock', 'calibration_voltage',
    'calibration_current', 'calibration_save', 'calibration_initialize', 'calibration_lock',
    'calibration_label', 'power_on_status_write',
})
RISK = {
    'wait_pending': 'high', 'self_test': 'high',
    'clear_events': 'high', 'reset_settings': 'critical',
    'reset_status': 'high', 'trigger_bus': 'critical',
    'save_setup': 'high', 'recall_setup': 'critical',
    'apply_channel': 'critical', 'calibration_unlock': 'critical',
    'calibration_voltage': 'critical', 'calibration_current': 'critical',
    'calibration_save': 'critical', 'calibration_initialize': 'critical',
    'calibration_lock': 'critical', 'calibration_label': 'critical', 'power_on_status_write': 'high',
}
SOURCES = {
    'wait_pending': '5-5', 'self_test': '5-5',
    'clear_events': '5-2', 'reset_settings': '5-3',
    'reset_status': '5-20', 'trigger_bus': '5-4',
    'save_setup': '5-5', 'recall_setup': '5-5',
    'apply_channel': '5-6', 'calibration_unlock': '5-7',
    'calibration_voltage': '5-8', 'calibration_current': '5-8',
    'calibration_save': '5-9', 'calibration_initialize': '5-9',
    'calibration_lock': '5-7', 'calibration_label': '5-9', 'power_on_status_write': '5-6',
}


def _exact_string(value: object, label: str) -> str:
    if type(value) is not str or not value or not value.isascii() or not value.isprintable():
        raise ServicePolicyError(f'{label} must be nonempty printable ASCII')
    return value


def _number(value: object, *, low: str, high: str, label: str) -> str:
    """No bool, float coercion, NaN, scientific notation or silent rounding."""
    if type(value) not in (int, str, Decimal):
        raise ServicePolicyError(f'{label} needs exact decimal value')
    raw = str(value)
    if re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', raw) is None:
        raise ServicePolicyError(f'{label} must be nonnegative plain decimal')
    try:
        d = Decimal(raw)
    except InvalidOperation as exc:
        raise ServicePolicyError(f'{label}: invalid decimal') from exc
    if not Decimal(low) <= d <= Decimal(high):
        raise ServicePolicyError(f'{label}: outside {low}..{high}')
    return raw


@dataclass(frozen=True)
class ServiceProfile:
    """Separate service profile; ordinary Profile.from_json rejects this schema."""
    model: str
    serial: str
    allowed_operations: frozenset[str]
    source_sha256: str

    @classmethod
    def load(cls, path: str | Path) -> 'ServiceProfile':
        raw = Path(path).read_bytes()
        def _reject_duplicates(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ServicePolicyError(f'duplicate service profile key: {key}')
                obj[key] = value
            return obj
        try:
            data = json.loads(raw, object_pairs_hook=_reject_duplicates)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ServicePolicyError('service profile is not valid JSON') from exc
        if type(data) is not dict or set(data) != {'mode', 'model', 'serial', 'allowed_operations'}:
            raise ServicePolicyError('service profile must contain exactly mode/model/serial/allowed_operations')
        if data['mode'] != 'service':
            raise ServicePolicyError('ordinary profile cannot grant service operations')
        if data['model'] != '2231A-30-3':
            raise ServicePolicyError('service mode requires exact model')
        serial = _exact_string(data['serial'], 'serial')
        if serial.startswith(('REPLACE', 'EXAMPLE', 'YOUR_')):
            raise ServicePolicyError('placeholder serial forbidden')
        ops = data['allowed_operations']
        if type(ops) is not list or not ops or any(type(op) is not str for op in ops):
            raise ServicePolicyError('allowed_operations must be nonempty explicit list')
        if len(ops) != len(set(ops)) or not set(ops) <= PERMISSIONS:
            raise ServicePolicyError('duplicate or unknown service operations')
        return cls('2231A-30-3', serial, frozenset(ops), hashlib.sha256(raw).hexdigest())


@dataclass(frozen=True)
class ServiceStep:
    command: str
    query: bool
    permission: str
    manual_page: str
    risk: str
    warning: str = ''


@dataclass(frozen=True)
class ServicePlan:
    """Immutable, inspectable proposal. No execute(), send(), or transport."""
    mode: str
    serial: str
    profile_sha256: str
    operation: str
    steps: tuple[ServiceStep, ...]
    warning: str


class ServicePlanner:
    """Authorizes only offline proposals, with explicit per-action acknowledgement.

    The acknowledgement format is intentionally action- and file-hash-bound;
    this is not an authentication mechanism and MUST NOT be reused by a live
    service runtime as its sole authorization.
    """
    def __init__(self, profile: ServiceProfile):
        if type(profile) is not ServiceProfile:
            raise TypeError('A separate ServiceProfile is mandatory')
        self.profile = profile

    def required_ack(self, operation: str) -> str:
        if operation not in PERMISSIONS:
            raise ServicePolicyError('unknown operation')
        return f'SERVICE PLAN {operation} {self.profile.serial} {self.profile.source_sha256[:12]}'

    def plan(self, operation: str, *, acknowledgement: str, **kwargs: object) -> ServicePlan:
        if operation not in self.profile.allowed_operations:
            raise ServicePolicyError(f'{operation}: permission absent from service profile')
        if acknowledgement != self.required_ack(operation):
            raise ServicePolicyError(f'{operation}: explicit per-operation confirmation required')
        cmd = self._build(operation, kwargs)
        steps = tuple(ServiceStep(c, q, operation, SOURCES[operation], RISK[operation], note)
                      for c, q, note in cmd)
        return ServicePlan('SERVICE_REVIEW', self.profile.serial,
                           self.profile.source_sha256, operation, steps,
                           'Service commands require an approved manufacturer procedure and'
                           ' explicit driver authorization; do not use without necessity.')

    @staticmethod
    def _build(operation: str, args: Mapping[str, object]) -> tuple[tuple[str, bool, str], ...]:
        static = {
            'wait_pending': ('*WAI', False), 'self_test': ('*TST?', True),
            'clear_events': ('*CLS', False), 'reset_settings': ('*RST', False),
            'reset_status': ('STAT:PRES', False), 'trigger_bus': ('*TRG', False),
            'calibration_save': ('CAL:SAV', False),
            'calibration_initialize': ('CAL:INIT', False),
            'calibration_lock': ('CAL:SEC ON', False),
        }
        if operation in static:
            if args: raise ServicePolicyError('unexpected service arguments')
            c, q = static[operation]
            return ((c, q, 'may alter outputs/status or block'),)
        if operation in ('save_setup', 'recall_setup'):
            if set(args) != {'slot'} or type(args['slot']) is not int or not 0 <= args['slot'] <= 30:
                raise ServicePolicyError('slot must be integer 0..30')
            return ((f'{"*SAV" if operation == "save_setup" else "*RCL"} {args["slot"]}', False,
                     'nonvolatile write or unsafe recall'),)
        if operation == 'power_on_status_write':
            if set(args) != {'state'} or type(args['state']) is not int or args['state'] not in (0,1):
                raise ServicePolicyError('power-on status flag must be exact 0 or 1')
            # Manual is not explicit on write form; deliberately keep as non-executable placeholder.
            return ((f'*PSC {args["state"]}', False, 'syntax inferred; exact write argument NOT VERIFIED'),)
        if operation == 'apply_channel':
            if set(args) != {'channel', 'voltage', 'current'}:
                raise ServicePolicyError('explicit channel, voltage and current required')
            ch = args['channel']
            if type(ch) is not int or ch not in (1, 2, 3):
                raise ServicePolicyError('channel must be integer 1..3')
            v = _number(args['voltage'], low='0', high='5' if ch == 3 else '30', label='voltage')
            i = _number(args['current'], low='0', high='3', label='current')
            return ((f'APPL CH{ch},{v},{i}', False, 'can change live output/setpoints'),)
        if operation == 'calibration_unlock':
            if set(args) != {'intent'} or args['intent'] != 'REQUIRES_EXTERNAL_SECRET':
                raise ServicePolicyError('unlock requires explicit external secret workflow')
            return (('CAL:SEC OFF,<PASSWORD_FROM_SECURE_OPERATOR_INPUT>', False,
                     'placeholder, never wire-send; do not write passwords to evidence'),)
        if operation in ('calibration_voltage', 'calibration_current'):
            if set(args) != {'channel', 'points'}:
                raise ServicePolicyError('channel and complete ordered calibration points required')
            ch = args['channel']
            if type(ch) is not int or ch not in (1, 2, 3):
                raise ServicePolicyError('calibration channel must be integer 1..3')
            points = args['points']
            count = 4 if operation == 'calibration_voltage' else 2
            if type(points) not in (list, tuple) or len(points) != count:
                raise ServicePolicyError(f'exactly {count} ordered external measurements required')
            unit = 'V' if operation == 'calibration_voltage' else 'A'
            kind = 'VOLT' if operation == 'calibration_voltage' else 'CURR'
            result: list[tuple[str, bool, str]] = [(f'INST:NSEL {ch}', False,
                     'channel selection; verify before every live calibration point')]
            for idx, measurement in enumerate(points, 1):
                value = _number(measurement, low='0',
                                high=('5.5' if ch == 3 else '31') if unit == 'V' else '3.5',
                                label=f'external point P{idx} [{unit}]')
                result.append((f'CAL:{kind}:LEV P{idx}', False,
                               'may energize or reconfigure output; needs independent reference'))
                result.append((f'CAL:{kind} {value}', False,
                               'writes calibration coefficient from external measurement'))
            return tuple(result)
        if operation == 'calibration_label':
            if set(args) != {'label'}:
                raise ServicePolicyError('calibration label required')
            label = _exact_string(args['label'], 'calibration label')
            if len(label) > 22 or any(c in label for c in ('"', "'", '\\', ';')):
                raise ServicePolicyError('calibration label must be <=22 safe ASCII characters')
            return ((f'CAL:STR "{label}"', False, 'changes calibration metadata'),)
        raise ServicePolicyError('service operation unknown or not implemented')
