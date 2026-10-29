"""Offline service-authorization/ownership MODEL. Absolutely no hardware transport.

This module demonstrates fail-closed, per-plan and per-session gates. It has no
serial, socket, driver, device, raw-SCPI callback or live-execution API. The
in-memory consent strings and SHA256 fingerprints are NOT authentication,
anti-tamper security, interprocess locks or real safety interlocks.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .service import ServicePlanner, ServicePolicyError, ServiceProfile, ServicePlan
from .service_gateway_offline import GatewayReport, OfflineServiceGateway, verify_gateway_report
from .service_simulator import FakeBench


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          allow_nan=False, separators=(',', ':')).encode('ascii')
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise ServicePolicyError('service authorization requires canonical JSON parameters') from exc


def _hash(data: object) -> str:
    return hashlib.sha256(_canonical(data)).hexdigest()


def _plan_digest(plan: ServicePlan) -> str:
    return _hash({'mode': plan.mode, 'serial': plan.serial,
                  'profile_sha256': plan.profile_sha256,
                  'operation': plan.operation, 'warning': plan.warning,
                  'steps': [(s.command, s.query, s.permission, s.risk,
                             s.manual_page, s.warning) for s in plan.steps]})


def _fixture_digest(fixture: FakeBench) -> str:
    if type(fixture) is not FakeBench:
        raise TypeError('exact FakeBench required; no device handles allowed')
    return _hash({'model': fixture.model, 'serial': fixture.serial,
                  'channel_off': fixture.channel_off, 'topology': fixture.topology,
                  'recall_off': fixture.recall_off, 'idle': fixture.idle,
                  'link_known': fixture.link_known})


@dataclass(frozen=True)
class OfflineServicePermit:
    """Process-local, one-shot fake permit. Never valid for real SCPI."""
    serial: str
    profile_sha256: str
    operation: str
    sequence: int
    plan_sha256: str
    fixture_sha256: str
    params_json: str
    warning: str = 'OFFLINE PERMIT, NOT A PASSWORD OR HARDWARE AUTHORIZATION'


@dataclass(frozen=True)
class AuthorizedFakeReport:
    permit_sha256: str
    result: GatewayReport
    warning: str = 'FAKE AUTHORIZATION AND FAKE SCPI; NOT PHYSICAL EVIDENCE'


class OfflineServiceAuthority:
    """Session owner + exact-plan permission checks for FakeBench only.

    A normal-session claim and a service claim exclude one another on THIS
    object. They do not coordinate processes or lock a real instrument.
    """
    def __init__(self, profile_file: str | Path, fixture: FakeBench):
        if type(fixture) is not FakeBench:
            raise TypeError('FakeBench required: no live equipment permitted')
        self._path = Path(profile_file)
        self._profile = ServiceProfile.load(self._path)
        self._fixture = fixture
        if fixture.model != self._profile.model or fixture.serial != self._profile.serial:
            raise ServicePolicyError('fake serial/model does not match service profile')
        self._fixture_sha = _fixture_digest(fixture)
        self._planner = ServicePlanner(self._profile)
        self._gateway = OfflineServiceGateway(self._profile, fixture)
        self._owner = 'free'
        self._pending: OfflineServicePermit | None = None
        self._terminal = False
        self._cal_unlocked = False
        self._closed = False

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def terminal(self) -> bool:
        return self._terminal

    @property
    def reports(self) -> tuple[GatewayReport, ...]:
        return self._gateway.reports

    def _assert_valid(self) -> None:
        if self._terminal or self._closed or self._gateway.failed:
            raise ServicePolicyError('fake authority is terminal/closed')
        if _fixture_digest(self._fixture) != self._fixture_sha:
            self._abort('synthetic fixture changed')
        try:
            current = ServiceProfile.load(self._path)
        except (OSError, ServicePolicyError) as exc:
            self._abort('service profile unreadable or changed')
            raise AssertionError('unreachable') from exc
        if current != self._profile:
            self._abort('service profile changed during session')
        f = self._fixture
        if (type(f.channel_off) is not tuple or len(f.channel_off) != 3 or
                any(v is not True for v in f.channel_off) or
                f.topology != 'independent' or f.recall_off is not True or
                f.idle is not True or f.link_known is not True):
            self._abort('synthetic OFF/idle/known-link gate failed')

    def _abort(self, reason: str) -> None:
        self._terminal = True
        self._owner = 'unknown'
        self._pending = None
        raise ServicePolicyError(f'OFFLINE AUTHORITY ABORTED: {reason}; no recovery on this object')

    def claim_normal(self) -> None:
        self._assert_valid()
        if self._owner != 'free':
            raise ServicePolicyError('fake controller already owned')
        self._owner = 'normal'

    def release_normal(self) -> None:
        self._assert_valid()
        if self._owner != 'normal':
            raise ServicePolicyError('fake normal owner does not hold session')
        self._owner = 'free'

    def required_session_ack(self) -> str:
        return f'OFFLINE SERVICE SESSION {self._profile.serial} {self._profile.source_sha256}'

    def open_service(self, *, session_ack: str) -> None:
        self._assert_valid()
        if self._owner != 'free':
            raise ServicePolicyError('another fake session owns controller')
        if session_ack != self.required_session_ack():
            raise ServicePolicyError('explicit profile-bound service session approval missing')
        self._owner = 'service'

    def required_ack(self, operation: str) -> str:
        return self._planner.required_ack(operation)

    def required_sim_ack(self, operation: str) -> str:
        return self._gateway.required_sim_ack(operation)

    def required_critical_ack(self, operation: str) -> str | None:
        return self._gateway.required_critical_ack(operation)

    def prepare(self, operation: str, *, acknowledgement: str,
                **kwargs: object) -> OfflineServicePermit:
        self._assert_valid()
        if self._owner != 'service' or self._pending is not None:
            raise ServicePolicyError('exclusive fake service ownership and zero pending permit required')
        plan = self._planner.plan(operation, acknowledgement=acknowledgement, **kwargs)
        if not 1 <= len(plan.steps) <= 24:
            raise ServicePolicyError('offline service command budget exceeded')
        param_bytes = _canonical(kwargs)
        # JSON parameters are deliberately narrower than Python Planner inputs.
        params = json.loads(param_bytes)
        if type(params) is not dict:
            raise ServicePolicyError('parameters must be object')
        if _plan_digest(self._planner.plan(operation, acknowledgement=acknowledgement,
                                          **params)) != _plan_digest(plan):
            raise ServicePolicyError('parameters are not round-trip stable')
        permit = OfflineServicePermit(self._profile.serial, self._profile.source_sha256,
                                      operation, len(self.reports) + 1, _plan_digest(plan),
                                      self._fixture_sha, param_bytes.decode('ascii'))
        self._pending = permit
        return permit

    @staticmethod
    def required_action_ack(permit: OfflineServicePermit) -> str:
        if type(permit) is not OfflineServicePermit:
            raise TypeError('an exact offline permit is required')
        return (f'OFFLINE AUTHORIZE PLAN {permit.operation} {permit.serial} '
                f'SEQ={permit.sequence} PROFILE={permit.profile_sha256} PLAN={permit.plan_sha256}')

    def simulate(self, permit: OfflineServicePermit, *, action_ack: str,
                 acknowledgement: str, simulation_ack: str,
                 critical_ack: str | None = None,
                 inject_fault_step: int | None = None,
                 inject_fault_phase: str = 'send') -> AuthorizedFakeReport:
        self._assert_valid()
        if self._owner != 'service' or permit is not self._pending:
            raise ServicePolicyError('missing or foreign/stale one-shot fake permit')
        # All approvals are checked BEFORE consuming the permit/fake dispatch.
        if action_ack != self.required_action_ack(permit):
            self._abort('exact plan approval missing')
        if (acknowledgement != self.required_ack(permit.operation) or
                simulation_ack != self.required_sim_ack(permit.operation) or
                critical_ack != self.required_critical_ack(permit.operation)):
            self._abort('per-operation approval mismatch')
        try:
            kwargs = json.loads(permit.params_json)
            plan = self._planner.plan(permit.operation, acknowledgement=acknowledgement, **kwargs)
            if (permit.serial != self._profile.serial or
                    permit.profile_sha256 != self._profile.source_sha256 or
                    permit.fixture_sha256 != self._fixture_sha or
                    permit.sequence != len(self.reports) + 1 or
                    permit.plan_sha256 != _plan_digest(plan)):
                self._abort('prepared plan/fixture/profile/sequence mismatch')
        except (ValueError, TypeError) as exc:
            self._abort('invalid pending plan')
            raise AssertionError('unreachable') from exc
        self._pending = None  # consumption occurs before any fake dispatch
        try:
            result = self._gateway.simulate(
                permit.operation, acknowledgement=acknowledgement,
                simulation_ack=simulation_ack, critical_ack=critical_ack,
                inject_fault_step=inject_fault_step,
                inject_fault_phase=inject_fault_phase, **kwargs)
        except Exception as exc:
            # Unexpected fake-dispatch failures must also latch the authority.
            self._abort(f'fake gateway refused or failed: {type(exc).__name__}')
            raise AssertionError('unreachable') from exc
        if not verify_gateway_report(result):
            self._abort('fake gateway report integrity mismatch')
        if permit.operation == 'calibration_unlock' and result.status == 'SIMULATED_PASS':
            self._cal_unlocked = True
        elif permit.operation == 'calibration_lock' and result.status == 'SIMULATED_PASS':
            self._cal_unlocked = False
        if result.status != 'SIMULATED_PASS':
            self._terminal = True
            self._owner = 'unknown'
        permit_sha = _hash({'serial': permit.serial, 'profile_sha256': permit.profile_sha256,
                            'operation': permit.operation, 'sequence': permit.sequence,
                            'plan_sha256': permit.plan_sha256, 'fixture_sha256': permit.fixture_sha256,
                            'params_json': permit.params_json})
        return AuthorizedFakeReport(permit_sha, result)

    def close_service(self) -> None:
        self._assert_valid()
        if self._owner != 'service' or self._pending is not None:
            raise ServicePolicyError('fake service is not ready for release')
        if self._cal_unlocked:
            raise ServicePolicyError('synthetic calibration is still unlocked; no normal session')
        self._owner = 'closed'
        self._closed = True
