"""OFFLINE-ONLY in-memory service execution contract for fault-injection tests.

This is *not* a service transport and has no serial/socket/device imports,
external I/O callback, raw command API, or live execution option. Even the
SCPI-looking strings are never transmitted to any real instrument. It checks the
simulation-to-dispatch boundary before a separately designed live gateway.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .service import ServicePlanner, ServicePolicyError, ServiceProfile
from .service_simulator import FakeBench, ServiceSimulator, verify_simulation_report


MAX_FAKE_STEPS = 24
_FAULT_PHASES = frozenset({'send', 'reply', 'error_poll', 'output_on'})


@dataclass(frozen=True)
class GatewayEvent:
    kind: str
    command: str
    detail: str


@dataclass(frozen=True)
class GatewayReport:
    status: str   # SIMULATED_PASS / SIMULATED_ABORTED, never HW PASS
    operation: str
    sequence: int
    serial: str
    profile_sha256: str
    simulator_report_sha256: str
    events: tuple[GatewayEvent, ...]
    report_sha256: str
    warning: str = 'IN-MEMORY FAKE ONLY. NO SCPI TRANSMITTED OR PHYSICAL STATE OBSERVED.'


def _digest(*, status: str, operation: str, sequence: int, serial: str,
            profile_sha256: str, simulator_report_sha256: str,
            events: tuple[GatewayEvent, ...], warning: str) -> str:
    payload = {'status': status, 'operation': operation, 'sequence': sequence,
               'serial': serial, 'profile_sha256': profile_sha256,
               'simulator_report_sha256': simulator_report_sha256,
               'events': [(e.kind, e.command, e.detail) for e in events],
               'warning': warning}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                        separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def verify_gateway_report(report: GatewayReport) -> bool:
    """Detect accidental changes to report contents; not a security signature."""
    if type(report) is not GatewayReport or type(report.events) is not tuple:
        return False
    if any(type(ev) is not GatewayEvent for ev in report.events):
        return False
    return _digest(status=report.status, operation=report.operation,
                   sequence=report.sequence, serial=report.serial,
                   profile_sha256=report.profile_sha256,
                   simulator_report_sha256=report.simulator_report_sha256,
                   events=report.events, warning=report.warning) == report.report_sha256


class OfflineServiceGateway:
    """Fake dispatcher: accepts only typed action approvals and fake fixture.

    No transport is accepted, not even through constructor kwargs. The
    in-memory 'wire' below is a record of actions, not a hardware interface.
    Gateway faults latch terminally and cannot be cleared on the same object.
    """
    def __init__(self, profile: ServiceProfile, fixture: FakeBench):
        if type(profile) is not ServiceProfile or type(fixture) is not FakeBench:
            raise TypeError('a ServiceProfile and an exact FakeBench are mandatory')
        self._planner = ServicePlanner(profile)
        self._simulator = ServiceSimulator(profile, fixture)
        self._failed = False
        self._reports: list[GatewayReport] = []

    @property
    def reports(self) -> tuple[GatewayReport, ...]:
        return tuple(self._reports)

    @property
    def failed(self) -> bool:
        return self._failed

    def required_ack(self, operation: str) -> str:
        return self._simulator.required_ack(operation)

    def required_sim_ack(self, operation: str) -> str:
        return self._simulator.required_sim_ack(operation)

    def required_critical_ack(self, operation: str) -> str | None:
        return self._simulator.required_critical_ack(operation)

    def _store(self, status: str, operation: str, simulation_sha: str,
               sequence: int, events: list[GatewayEvent]) -> GatewayReport:
        p = self._planner.profile
        ev = tuple(events)
        warning = GatewayReport.__dataclass_fields__['warning'].default
        digest = _digest(status=status, operation=operation, sequence=sequence,
                         serial=p.serial, profile_sha256=p.source_sha256,
                         simulator_report_sha256=simulation_sha, events=ev,
                         warning=warning)
        report = GatewayReport(status, operation, sequence, p.serial,
                               p.source_sha256, simulation_sha, ev, digest)
        self._reports.append(report)
        if status != 'SIMULATED_PASS':
            self._failed = True
        return report

    def simulate(self, operation: str, *, acknowledgement: str,
                 simulation_ack: str, critical_ack: str | None = None,
                 inject_fault_step: int | None = None,
                 inject_fault_phase: str = 'send', **kwargs: object) -> GatewayReport:
        if self._failed:
            raise ServicePolicyError('gateway fault is terminal; create a new fake scenario')
        if inject_fault_step is not None and (type(inject_fault_step) is not int
                                               or inject_fault_step < 1):
            raise ServicePolicyError('fake fault step must be positive integer')
        if inject_fault_phase not in _FAULT_PHASES:
            raise ServicePolicyError('fake fault phase is not supported')
        # Compile a fresh, profile-authorized typed plan, never caller-owned SCPI.
        # All approvals and the calibration state machine are independently
        # checked again by the existing simulator before any fake dispatch.
        plan = self._planner.plan(operation, acknowledgement=acknowledgement, **kwargs)
        if (not plan.steps or len(plan.steps) > MAX_FAKE_STEPS or
                (inject_fault_step is not None and inject_fault_step > len(plan.steps))):
            raise ServicePolicyError('fake command budget/fault location violated')
        if (inject_fault_step is not None and inject_fault_phase == 'reply' and
                not plan.steps[inject_fault_step - 1].query):
            raise ServicePolicyError('reply fault requires a query step')
        sim = self._simulator.simulate(operation, acknowledgement=acknowledgement,
                                       simulation_ack=simulation_ack,
                                       critical_ack=critical_ack, **kwargs)
        if not verify_simulation_report(sim):
            self._failed = True
            raise ServicePolicyError('in-memory simulation integrity failed')
        # Prevent any accidental divergence between simulator and gateway plans.
        planned_commands = tuple(step.command for step in plan.steps)
        simulated_commands = tuple(e.command for e in sim.events if e.kind == 'SCPI_NOT_SENT')
        if planned_commands != simulated_commands:
            self._failed = True
            raise ServicePolicyError('simulation/dispatcher command disagreement')
        events = [GatewayEvent('FAKE_PREFLIGHT', '',
                               'synthetic IDN matched; CH1 CH2 CH3 OFF; Independent; idle')]
        for ix, step in enumerate(plan.steps, 1):
            if inject_fault_step == ix and inject_fault_phase == 'send':
                events.append(GatewayEvent('INJECTED_FAULT', step.command, 'fake write/query failure'))
                return self._store('SIMULATED_ABORTED', operation, sim.report_sha256,
                                   sim.sequence, events)
            events.append(GatewayEvent('FAKE_QUERY' if step.query else 'FAKE_WRITE',
                                       step.command, f'in-memory step {ix}/{len(plan.steps)}'))
            if step.query:
                if inject_fault_step == ix and inject_fault_phase == 'reply':
                    events.append(GatewayEvent('INJECTED_FAULT', step.command,
                                               'fake response timeout or malformed result'))
                    return self._store('SIMULATED_ABORTED', operation, sim.report_sha256,
                                       sim.sequence, events)
                events.append(GatewayEvent('FAKE_REPLY', step.command,
                                           'SYNTHETIC_ONLY; no actual Keithley response'))
            if inject_fault_step == ix and inject_fault_phase == 'error_poll':
                events.append(GatewayEvent('INJECTED_FAULT', 'SYST:ERR?',
                                           'fake timeout/nonzero error queue'))
                return self._store('SIMULATED_ABORTED', operation, sim.report_sha256,
                                   sim.sequence, events)
            events.append(GatewayEvent('FAKE_ERROR_POLL', 'SYST:ERR?',
                                       'FAKE 0, "No error" (NOT A DEVICE REPLY)'))
            if inject_fault_step == ix and inject_fault_phase == 'output_on':
                events.append(GatewayEvent('INJECTED_FAULT', '',
                                           'fake CHx unexpectedly ON; no subsequent commands'))
                return self._store('SIMULATED_ABORTED', operation, sim.report_sha256,
                                   sim.sequence, events)
        events.append(GatewayEvent('FAKE_FINAL_OFF', '',
                                   'synthetic CH1 CH2 CH3 OFF only; no measurement'))
        if sim.status != 'SIMULATED_PASS':
            events.append(GatewayEvent('INCOMPLETE_CAL', '',
                                       'simulator rejected incomplete/unsaved adjustment'))
        return self._store(sim.status, operation, sim.report_sha256,
                           sim.sequence, events)
