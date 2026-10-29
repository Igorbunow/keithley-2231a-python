"""O02-A: strictly in-memory model of two independently gated PSU sessions.

This module has NO physical transport, driver method, socket, serial interface or
SCPI dispatcher.  It must not be used as an equipment runner.  A simulated
command is an assertion about fault containment, never evidence of HW OFF.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
import time

class SimulationSafetyError(RuntimeError):
    """Synthetic offline model error; unrelated to physical Keithley safety."""



class SimulatedLinkLoss(RuntimeError):
    """A fictional device lost its acknowledgement; actual output is unknown."""


@dataclass(frozen=True)
class SimIdentity:
    alias: str
    serial: str
    endpoint: str
    voltage_cap_v: float = 2.0
    current_cap_a: float = 0.1

    def __post_init__(self):
        if self.alias not in ('A', 'B'):
            raise ValueError('O02-A aliases must be A or B')
        if (not isinstance(self.serial, str) or not self.serial.startswith('SIM-')
                or not isinstance(self.endpoint, str) or not self.endpoint.startswith('sim://')
                or any(x in self.serial for x in ('\n', '\r'))
                or any(x in self.endpoint for x in ('\n', '\r'))):
            raise ValueError('O02-A accepts synthetic SIM identities only')
        for name, value, top in (('voltage cap', self.voltage_cap_v, 5.0),
                                 ('current cap', self.current_cap_a, 1.0)):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= top:
                raise ValueError(f'invalid O02-A {name}')


@dataclass(frozen=True)
class SimStep:
    alias: str
    voltage_v: float
    current_limit_a: float


class SimulatedSource:
    """Exact-type-only in-memory fake. Commands are symbolic, not SCPI or I/O.

    Supported synthetic faults:
      identify_wrong, identify_lost, preflight_lost, enable_lost_after_on,
      enable_stuck_off, enable_verify_stale_off, off_lost, off_stuck_on,
      off_verify_lost.
    """
    FAULTS = frozenset(('identify_wrong', 'identify_lost', 'preflight_lost',
                        'enable_lost_after_on', 'enable_stuck_off',
                        'enable_verify_stale_off', 'off_lost', 'off_stuck_on',
                        'off_verify_lost'))

    def __init__(self, identity: SimIdentity, *, initially_on=False, faults=()):
        if type(identity) is not SimIdentity or type(initially_on) is not bool:
            raise TypeError('O02-A source requires a synthetic identity and boolean state')
        faults = frozenset(faults)
        if not faults <= self.FAULTS:
            raise ValueError('unknown O02-A simulated fault')
        self.identity = identity
        self.faults = faults
        self._on = initially_on
        self.uncertain = False
        self.commands: list[str] = []

    def _record(self, operation):
        if self.uncertain:
            raise SimulatedLinkLoss('simulated link already uncertain; further commands forbidden')
        self.commands.append(operation)

    def identify(self):
        self._record('IDENTIFY')
        if 'identify_lost' in self.faults:
            self.uncertain = True
            raise SimulatedLinkLoss('identity reply lost')
        if 'identify_wrong' in self.faults:
            return SimIdentity(self.identity.alias, 'SIM-OTHER', self.identity.endpoint)
        return self.identity

    def read_off(self, phase):
        self._record('READ_' + phase)
        if (phase == 'INITIAL' and 'preflight_lost' in self.faults) or (
                phase == 'AFTER_OFF' and 'off_verify_lost' in self.faults):
            self.uncertain = True
            raise SimulatedLinkLoss('output readback lost')
        if phase == 'AFTER_ON' and 'enable_verify_stale_off' in self.faults:
            return True  # fabricated OFF readback while actual ON
        return not self._on

    def enable(self, voltage_v, current_limit_a):
        self._record('ENABLE')
        if 'enable_stuck_off' not in self.faults:
            self._on = True
        if 'enable_lost_after_on' in self.faults:
            self.uncertain = True
            raise SimulatedLinkLoss('ON command acknowledged by no reply')

    def disable(self):
        self._record('DISABLE')
        if 'off_lost' in self.faults:
            self.uncertain = True
            raise SimulatedLinkLoss('OFF command acknowledgement lost')
        if 'off_stuck_on' not in self.faults:
            self._on = False

    @property
    def simulated_physical_on(self):
        """For fault-injection assertions ONLY; never a trusted readback."""
        return self._on


def _validate_step(step: SimStep, expected: SimIdentity):
    if type(step) is not SimStep or step.alias != expected.alias:
        raise ValueError('invalid O02-A step identity')
    for value, cap, quantum, name in (
        (step.voltage_v, expected.voltage_cap_v, 0.01, 'voltage'),
        (step.current_limit_a, expected.current_cap_a, 0.001, 'current')):
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 < value <= cap
                or abs(value / quantum - round(value / quantum)) > 1e-6):
            raise ValueError(f'O02-A {name} exceeds synthetic cap/resolution')


def audit_dual_simulation_evidence(evidence_path: str | Path, *, expected_outcome=None) -> str:
    """Independent replay of O02-A synthetic JSONL. A PASS is SIMULATED ONLY."""
    try:
        rows = [json.loads(line) for line in Path(evidence_path).read_text(encoding='utf-8').splitlines()]
    except (ValueError, OSError) as exc:
        raise SimulationSafetyError('O02-A evidence cannot be read') from exc
    if len(rows) < 2 or any(type(x) is not dict for x in rows):
        raise SimulationSafetyError('O02-A missing or malformed evidence')
    *events, summary = rows
    if (set(summary) != {'simulation_only', 'event', 'outcome', 'final_states', 'reason'}
            or summary['simulation_only'] is not True or summary['event'] != 'final_outcome'
            or summary['outcome'] not in ('PASS', 'ABORTED')
            or type(summary['final_states']) is not dict
            or set(summary['final_states']) != {'A', 'B'}
            or any(x not in ('OFF', 'ON', 'UNKNOWN') for x in summary['final_states'].values())
            or (summary['reason'] is not None and type(summary['reason']) is not str)
            or (expected_outcome is not None and summary['outcome'] != expected_outcome)):
        raise SimulationSafetyError('O02-A inconsistent outcome or final states')
    previous_time = None
    for i, item in enumerate(events):
        if (set(item) - {'seq', 'simulation_only', 'monotonic_s', 'event', 'source',
                        'endpoint', 'serial', 'voltage_v', 'current_limit_a',
                        'state', 'error_type', 'A_serial', 'B_serial',
                        'A_endpoint', 'B_endpoint'}
                or item.get('seq') != i or type(item.get('seq')) is not int
                or item.get('simulation_only') is not True
                or type(item.get('event')) is not str
                or item.get('source') not in ('A', 'B', None)
                or type(item.get('monotonic_s')) not in (float, int)
                or not math.isfinite(item['monotonic_s'])
                or (previous_time is not None and item['monotonic_s'] < previous_time)):
            raise SimulationSafetyError('O02-A corrupted simulation event/order/time')
        previous_time = item['monotonic_s']
    if not events or events[0].get('event') != 'simulation_manifest':
        raise SimulationSafetyError('O02-A missing synthetic identity manifest')
    manifest = events[0]
    if (type(manifest.get('A_serial')) is not str or type(manifest.get('B_serial')) is not str
            or not manifest['A_serial'].startswith('SIM-')
            or not manifest['B_serial'].startswith('SIM-')
            or manifest['A_serial'] == manifest['B_serial']
            or type(manifest.get('A_endpoint')) is not str
            or type(manifest.get('B_endpoint')) is not str
            or not manifest['A_endpoint'].startswith('sim://')
            or not manifest['B_endpoint'].startswith('sim://')
            or manifest['A_endpoint'] == manifest['B_endpoint']):
        raise SimulationSafetyError('O02-A invalid or shared synthetic identities')
    for e in events:
        if e['event'] == 'identity_ok':
            alias = e['source']
            if (alias not in ('A', 'B') or e.get('serial') != manifest[alias + '_serial']
                    or e.get('endpoint') != manifest[alias + '_endpoint']):
                raise SimulationSafetyError('O02-A identity mismatch in evidence')
    def positions(kind):
        return [(i, e['source']) for i, e in enumerate(events) if e['event'] == kind]
    if summary['outcome'] == 'PASS':
        ids = positions('identity_ok')
        offs = positions('initial_off')
        approvals = positions('two_distinct_approvals')
        ons = positions('enabled')
        final = positions('final_readback')
        if (len(ids) != 2 or [x[1] for x in ids] != ['A', 'B']
                or len(offs) != 2 or [x[1] for x in offs] != ['A', 'B']
                or len(approvals) != 1 or approvals[0][1] is not None
                or len(ons) != 2 or [x[1] for x in ons] != ['A', 'B']
                or len(final) != 2 or [x[1] for x in final] != ['B', 'A']
                or not (ids[-1][0] < approvals[0][0] < ons[0][0] < final[0][0])
                or summary['final_states'] != {'A': 'OFF', 'B': 'OFF'}
                or summary['reason'] is not None
                or any(e['event'] in ('abort', 'shutdown_failed', 'shutdown_skipped_unknown',
                                      'identity_mismatch', 'preflight_failure') for e in events)
                or any(e['event'] == 'final_readback' and e.get('state') != 'OFF' for e in events)):
            raise SimulationSafetyError('O02-A PASS is not supported by separate simulated gates/OFF')
    else:
        if (not positions('abort') and summary['final_states'] == {'A': 'OFF', 'B': 'OFF'}):
            raise SimulationSafetyError('O02-A ABORTED is not explained by evidence')
    return summary['outcome']


class DualOfflineSimulation:
    """One-shot independent dual-PSU simulation; absolutely no real instruments.

    PASS means only both synthetic output readbacks OFF AND evidence persisted.
    An uncertain session is never queried or retried, and the other session
    is still independently shut down. Approvals are separate and identity-bound.
    """

    def __init__(self, expected_a: SimIdentity, expected_b: SimIdentity,
                 source_a: SimulatedSource, source_b: SimulatedSource):
        if (type(expected_a) is not SimIdentity or type(expected_b) is not SimIdentity
                or expected_a.alias != 'A' or expected_b.alias != 'B'):
            raise ValueError('O02-A requires ordered A/B simulated identities')
        if (expected_a.serial == expected_b.serial or expected_a.endpoint == expected_b.endpoint):
            raise SimulationSafetyError('O02-A distinct serial IDs and physical endpoints required')
        if type(source_a) is not SimulatedSource or type(source_b) is not SimulatedSource or source_a is source_b:
            raise TypeError('O02-A takes only two distinct exact in-memory SimulatedSource instances')
        self.expected = {'A': expected_a, 'B': expected_b}
        self.sources = {'A': source_a, 'B': source_b}
        self.trusted = {'A': False, 'B': False}
        self.final_states = {'A': 'UNKNOWN', 'B': 'UNKNOWN'}
        self.events = []
        self.outcome = 'NOT_RUN'
        self.reason = None
        self._used = False

    def approval_token(self, alias):
        if alias not in ('A', 'B'):
            raise ValueError('unknown source')
        spec = self.expected[alias]
        return f'CONFIRM O02-A SIM {alias} SERIAL={spec.serial} PORT={spec.endpoint} ISOLATED=YES'

    def _event(self, event, alias=None, **values):
        self.events.append({'seq': len(self.events), 'simulation_only': True,
                            'monotonic_s': time.monotonic(), 'event': event,
                            'source': alias, **values})

    def _preflight(self):
        # Identify and read both sessions independently.  A failure on A must
        # not prevent inspection/cleanup of a valid B identity (or vice versa).
        problems = []
        for alias in ('A', 'B'):
            unit = self.sources[alias]
            try:
                actual = unit.identify()
                if actual != self.expected[alias]:
                    self._event('identity_mismatch', alias)
                    problems.append(f'{alias} SIM identity/endpoint/profile mismatch')
                    continue  # Never send output commands to an unknown device.
                self.trusted[alias] = True
                self._event('identity_ok', alias, endpoint=actual.endpoint, serial=actual.serial)
                if not unit.read_off('INITIAL'):
                    problems.append(f'{alias} SIM initially ON; refusing to start')
                else:
                    self._event('initial_off', alias)
            except Exception as exc:
                problems.append(f'{alias} SIM preflight {type(exc).__name__}')
                self._event('preflight_failure', alias, error_type=type(exc).__name__)
        if problems:
            raise SimulationSafetyError('; '.join(problems))

    def _shutdown_both(self):
        # Never allow failure of B to skip A; no requests on uncertain/wrong identity.
        for alias in ('B', 'A'):
            unit = self.sources[alias]
            if not self.trusted[alias] or unit.uncertain:
                self.final_states[alias] = 'UNKNOWN'
                self._event('shutdown_skipped_unknown', alias)
                continue
            try:
                unit.disable()  # at most once per session, no retries
                off = unit.read_off('AFTER_OFF')
                self.final_states[alias] = 'OFF' if off else 'ON'
                self._event('final_readback', alias, state=self.final_states[alias])
            except Exception as exc:
                self.final_states[alias] = 'UNKNOWN'
                self._event('shutdown_failed', alias, error_type=type(exc).__name__)

    def _save(self, evidence_path: str | Path, provisional):
        path = Path(evidence_path)
        # No file work until both independent OFF attempts complete. Write a
        # private temporary file, audit it, then atomically publish via hardlink
        # with NO-REPLACE semantics. No partial/unchecked PASS can be published.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             prefix='.o02-a-', suffix='.jsonl.tmp',
                                             dir=path.parent, delete=False) as stream:
                tmp = Path(stream.name)
                for event in self.events:
                    stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
                stream.write(json.dumps(dict(simulation_only=True, event='final_outcome',
                                             outcome=provisional, final_states=self.final_states,
                                             reason=self.reason), ensure_ascii=False) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            audit_dual_simulation_evidence(tmp, expected_outcome=provisional)
            os.link(tmp, path)  # atomic no-overwrite, same directory/filesystem
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def run(self, *, approval_a, approval_b, steps: tuple[SimStep, SimStep], evidence_path) -> str:
        if self._used:
            raise SimulationSafetyError('O02-A simulation is one-shot; cannot reuse approvals')
        self._used = True
        # Validate every requested setpoint before any simulated access or power change.
        if type(steps) is not tuple or len(steps) != 2 or any(type(s) is not SimStep for s in steps):
            raise ValueError('O02-A exactly two A/B synthetic steps required')
        if (steps[0].alias, steps[1].alias) != ('A', 'B'):
            raise ValueError('O02-A sequence must include A then B exactly once')
        for step in steps:
            _validate_step(step, self.expected[step.alias])
        if evidence_path is None:
            raise ValueError('O02-A evidence path required')
        self._event('simulation_manifest', A_serial=self.expected['A'].serial,
                    B_serial=self.expected['B'].serial,
                    A_endpoint=self.expected['A'].endpoint,
                    B_endpoint=self.expected['B'].endpoint)
        provisional = 'ABORTED'
        try:
            self._preflight()
            if approval_a != self.approval_token('A') or approval_b != self.approval_token('B'):
                raise SimulationSafetyError('O02-A two separate identity-bound simulation approvals required')
            self._event('two_distinct_approvals')
            for step in steps:
                unit = self.sources[step.alias]
                # Every ON rechecks BOTH simulated channels OFF, until first ON.
                if step.alias == 'A' and any(self.sources[a].read_off('BEFORE_ON') is not True for a in ('A', 'B')):
                    raise SimulationSafetyError('O02-A unexpected initial simulated ON')
                unit.enable(step.voltage_v, step.current_limit_a)
                if unit.read_off('AFTER_ON'):
                    raise SimulationSafetyError(f'{step.alias} SIM ON not confirmed')
                self._event('enabled', step.alias, voltage_v=float(step.voltage_v),
                            current_limit_a=float(step.current_limit_a))
            provisional = 'PASS'
        except Exception as exc:
            self.reason = f'{type(exc).__name__}: {exc}'
            self._event('abort', error_type=type(exc).__name__)
        finally:
            self._shutdown_both()
            if any(self.final_states[a] != 'OFF' for a in ('A', 'B')):
                provisional = 'ABORTED'
                self.reason = (self.reason or 'Independent OFF/readback not verified on both simulated sources')
            # A regressed or nonfinite host clock is not evidence of ordered actions.
            times = [e['monotonic_s'] for e in self.events]
            if (any(not math.isfinite(t) for t in times)
                    or any(end < start for start, end in zip(times, times[1:]))):
                provisional = 'ABORTED'
                self.reason = self.reason or 'Synthetic host monotonic clock regressed'
                self._event('abort', error_type='ClockRegression')
            self.outcome = 'ABORTED'  # only promote after evidence is persisted
            try:
                self._save(evidence_path, provisional)
                # Independent re-read catches serialization mistakes before PASS.
                audit_dual_simulation_evidence(evidence_path, expected_outcome=provisional)
            except Exception as exc:
                self.reason = f'Evidence persistence failed ({type(exc).__name__}); {self.reason or ""}'.strip()
                self._event('evidence_failure', error_type=type(exc).__name__)
                return self.outcome
            self.outcome = provisional
        return self.outcome


def simulate_pair(*, fault_on: str | None = None, fault: str | None = None,
                  initial_on: str | None = None):
    """Create only synthetic, unconnected A/B devices for an offline scenario."""
    if fault_on not in (None, 'A', 'B') or initial_on not in (None, 'A', 'B'):
        raise ValueError('unknown O02-A simulated alias')
    if (fault_on is None) != (fault is None):
        raise ValueError('O02-A source and fault must be supplied together')
    if fault is not None and fault not in SimulatedSource.FAULTS:
        raise ValueError('unknown simulated fault')
    a = SimIdentity('A', 'SIM-O02-A-0001', 'sim://o02/a')
    b = SimIdentity('B', 'SIM-O02-B-0002', 'sim://o02/b')
    sa = SimulatedSource(a, initially_on=initial_on == 'A',
                         faults=(fault,) if fault_on == 'A' and fault else ())
    sb = SimulatedSource(b, initially_on=initial_on == 'B',
                         faults=(fault,) if fault_on == 'B' and fault else ())
    return DualOfflineSimulation(a, b, sa, sb)
