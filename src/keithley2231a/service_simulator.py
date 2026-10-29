"""Strictly offline service state-machine simulator; NEVER a SCPI transport.

It models approvals, preconditions, ordered operations and terminal failures.
No hardware access, transport parameter, socket/serial import, or live execute API.
A simulated result is NOT a vendor command validation or a calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

from .service import RISK, ServicePlanner, ServicePolicyError, ServiceProfile


@dataclass(frozen=True)
class FakeBench:
    """Operator-defined SYNTHETIC state, not a measurement/readback."""
    model: str
    serial: str
    channel_off: tuple[bool, bool, bool] = (True, True, True)
    topology: str = 'independent'
    recall_off: bool = True
    idle: bool = True
    link_known: bool = True


@dataclass(frozen=True)
class SimulatedEvent:
    kind: str
    command: str
    detail: str


@dataclass(frozen=True)
class SimulationReport:
    status: str  # SIMULATED_PASS or SIMULATED_ABORTED, never HW PASS
    operation: str
    serial: str
    profile_sha256: str
    sequence: int
    events: tuple[SimulatedEvent, ...]
    report_sha256: str
    disclaimer: str = 'SYNTHETIC ONLY; no PSU/USB/UART/voltage/current observed.'


def verify_simulation_report(report: SimulationReport) -> bool:
    """Check deterministic integrity only; a SHA is NOT a signature/authentication."""
    if type(report) is not SimulationReport:
        return False
    payload = {
        'status': report.status, 'operation': report.operation, 'serial': report.serial,
        'profile_sha256': report.profile_sha256, 'sequence': report.sequence,
        'events': [(e.kind, e.command, e.detail) for e in report.events],
        'disclaimer': report.disclaimer,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                            separators=(',', ':')).encode('ascii')
    return hashlib.sha256(serialized).hexdigest() == report.report_sha256


class ServiceSimulator:
    """In-memory simulator with synthetic pre/post-conditions and NO transport.

    Each action recompiles its typed command proposal through ServicePlanner;
    no arbitrary SCPI or caller-created ServicePlan can be injected.
    Explicit per-action profile acknowledgement, sequence-bound simulation
    acknowledgement and a second critical acknowledgement are all required.
    These are process-local safety gates, NOT tamper-proof access control.
    """
    def __init__(self, profile: ServiceProfile, fixture: FakeBench):
        if type(profile) is not ServiceProfile or type(fixture) is not FakeBench:
            raise TypeError('ServiceProfile and explicit FakeBench required')
        if fixture.model != profile.model or fixture.serial != profile.serial:
            raise ServicePolicyError('synthetic fixture identity does not match service profile')
        self._planner = ServicePlanner(profile)
        self._fixture = fixture
        self._reports: list[SimulationReport] = []
        self._failed = False
        self._unlocked = False
        self._calibrated: dict[int, set[str]] = {}
        self._saved = False

    @property
    def reports(self) -> tuple[SimulationReport, ...]:
        return tuple(self._reports)

    @property
    def failed(self) -> bool:
        return self._failed

    def required_ack(self, operation: str) -> str:
        return self._planner.required_ack(operation)

    def required_sim_ack(self, operation: str) -> str:
        self._planner.required_ack(operation)  # known operation
        p = self._planner.profile
        return (f'SIMULATE ONLY {operation} {p.serial} '
                f'{p.source_sha256[:12]} SEQ={len(self._reports) + 1}')

    def required_critical_ack(self, operation: str) -> str | None:
        self._planner.required_ack(operation)
        if RISK[operation] != 'critical':
            return None
        p = self._planner.profile
        return (f'SIMULATE CRITICAL {operation} {p.serial} '
                f'{p.source_sha256[:12]} SEQ={len(self._reports) + 1}')

    def _precondition(self, operation: str, args: Mapping[str, object]) -> None:
        f = self._fixture
        if (type(f.channel_off) is not tuple or len(f.channel_off) != 3 or
                any(x is not True for x in f.channel_off) or
                f.topology != 'independent' or f.recall_off is not True or
                f.idle is not True or f.link_known is not True):
            raise ServicePolicyError('synthetic OFF/independent/idle/known-link gate not met')
        calibration = operation.startswith('calibration_')
        if self._unlocked and not calibration:
            raise ServicePolicyError('no non-calibration actions while synthetic CAL unlocked')
        if operation == 'calibration_unlock':
            if self._unlocked or self._saved:
                raise ServicePolicyError('CAL unlock duplicate or after save')
        elif operation in ('calibration_voltage', 'calibration_current'):
            channel = args.get('channel')
            if not self._unlocked or self._saved:
                raise ServicePolicyError('CAL points require unlocked, unsaved session')
            if type(channel) is int and operation in self._calibrated.get(channel, set()):
                raise ServicePolicyError('CAL channel/type may be completed only once')
        elif operation == 'calibration_save':
            if not self._unlocked or self._saved or not self._calibrated:
                raise ServicePolicyError('CAL save requires pending complete adjustment')
            if any(k != {'calibration_voltage', 'calibration_current'}
                   for k in self._calibrated.values()):
                raise ServicePolicyError('CAL save requires both V and I completed per touched channel')
        elif operation == 'calibration_lock':
            if not self._unlocked:
                raise ServicePolicyError('CAL lock requires unlocked state')
            # CAL:SEC ON must remain available for safe closure even after
            # incomplete/unsaved points. Such a session must NOT earn PASS.
        elif operation == 'calibration_label':
            if not self._unlocked or self._saved:
                raise ServicePolicyError('CAL label requires unlocked unsaved state')
        elif operation == 'calibration_initialize':
            if self._unlocked or self._saved or self._calibrated:
                raise ServicePolicyError('CAL initialize requires separate fresh session')
        elif calibration:
            raise ServicePolicyError('unknown calibration transition')

    def _apply_transition(self, operation: str, args: Mapping[str, object]) -> None:
        if operation == 'calibration_unlock':
            self._unlocked = True
        elif operation in ('calibration_voltage', 'calibration_current'):
            self._calibrated.setdefault(args['channel'], set()).add(operation)
        elif operation == 'calibration_save':
            self._saved = True
        elif operation == 'calibration_lock':
            self._unlocked = False
        elif operation == 'calibration_initialize':
            # No fake settings are reset: explicit simulated marker only.
            self._saved = True

    def _report(self, operation: str, status: str, events: list[SimulatedEvent]) -> SimulationReport:
        p = self._planner.profile
        n = len(self._reports) + 1
        payload = {
            'status': status, 'operation': operation, 'serial': p.serial,
            'profile_sha256': p.source_sha256, 'sequence': n,
            'events': [(e.kind, e.command, e.detail) for e in events],
            'disclaimer': SimulationReport.__dataclass_fields__['disclaimer'].default,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                         separators=(',', ':')).encode('ascii')
        report = SimulationReport(status, operation, p.serial, p.source_sha256,
                                  n, tuple(events), hashlib.sha256(raw).hexdigest())
        self._reports.append(report)
        return report

    def simulate(self, operation: str, *, acknowledgement: str, simulation_ack: str,
                 critical_ack: str | None = None,
                 inject_fault_step: int | None = None,
                 inject_fault: str = 'timeout', **kwargs: object) -> SimulationReport:
        if self._failed:
            raise ServicePolicyError('previous synthetic fault permanently ended this session')
        if acknowledgement != self.required_ack(operation):
            raise ServicePolicyError('service profile action approval missing')
        if simulation_ack != self.required_sim_ack(operation):
            raise ServicePolicyError('unique sequence-bound simulation approval missing')
        required_critical = self.required_critical_ack(operation)
        if critical_ack != required_critical:
            raise ServicePolicyError('critical action requires independent confirmation')
        # Only the simulator can construct the plan. No externally supplied
        # ServicePlan, command string, transport or driver is accepted.
        plan = self._planner.plan(operation, acknowledgement=acknowledgement, **kwargs)
        self._precondition(operation, kwargs)
        if (inject_fault_step is not None and
                (type(inject_fault_step) is not int or not 1 <= inject_fault_step <= len(plan.steps))):
            raise ServicePolicyError('fault step must index one planned synthetic command')
        if inject_fault not in ('timeout', 'scpi_error', 'output_on'):
            raise ServicePolicyError('unrecognized synthetic fault type')
        events = [SimulatedEvent('GATE', '', 'SYNTHETIC fixture only, profile and approvals checked')]
        for index, step in enumerate(plan.steps, 1):
            events.append(SimulatedEvent('SCPI_NOT_SENT', step.command,
                                         f'fake step {index}/{len(plan.steps)}; {step.risk} risk'))
            if index == inject_fault_step:
                events.append(SimulatedEvent('INJECTED_FAULT', step.command, inject_fault))
                self._failed = True
                return self._report(operation, 'SIMULATED_ABORTED', events)
            if step.query:
                events.append(SimulatedEvent('FAKE_REPLY', step.command,
                                             'SYNTHETIC_RESULT_NOT_INSTRUMENT'))
            events.append(SimulatedEvent('FAKE_ERROR_CHECK', 'SYST:ERR?',
                                         'SYNTHETIC_0_NOT_INSTRUMENT'))
        unfinished_lock = (operation == 'calibration_lock' and bool(self._calibrated)
                           and not self._saved)
        self._apply_transition(operation, kwargs)
        events.append(SimulatedEvent('FINAL_GATE', '', 'SYNTHETIC OFF verified (not physical)'))
        if unfinished_lock:
            events.append(SimulatedEvent('INCOMPLETE_CAL', '',
                          'lock was simulated, but adjustments are NOT saved/qualified'))
            self._failed = True
            return self._report(operation, 'SIMULATED_ABORTED', events)
        return self._report(operation, 'SIMULATED_PASS', events)
