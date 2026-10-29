#!/usr/bin/env python3
"""Strictly OFFLINE, per-plan service authorization demonstration. No PSU I/O."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from keithley2231a.service import ServicePolicyError, ServiceProfile
from keithley2231a.service_authority_offline import OfflineServiceAuthority
from keithley2231a.service_simulator import FakeBench


def _unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ServicePolicyError(f'duplicate service argument: {key}')
        obj[key] = value
    return obj


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='OFFLINE service authorization model; FAKE SCPI only')
    p.add_argument('--service-profile', required=True)
    p.add_argument('--operation', required=True)
    p.add_argument('--params-json', default='{}')
    p.add_argument('--preview-acks', action='store_true')
    p.add_argument('--session-ack')
    p.add_argument('--ack')
    p.add_argument('--action-ack')
    p.add_argument('--sim-ack')
    p.add_argument('--critical-ack')
    p.add_argument('--inject-fault-step', type=int)
    p.add_argument('--inject-fault-phase', default='send')
    args = p.parse_args(argv)
    try:
        profile = ServiceProfile.load(args.service_profile)
        gate = OfflineServiceAuthority(args.service_profile, FakeBench(profile.model, profile.serial))
        params = json.loads(args.params_json, object_pairs_hook=_unique_keys)
        if type(params) is not dict:
            raise ServicePolicyError('params-json must be an object')
        if set(params) & {'acknowledgement', 'action_ack', 'simulation_ack',
                          'critical_ack', 'session_ack', 'inject_fault_step',
                          'inject_fault_phase', 'permit'}:
            raise ServicePolicyError('parameters cannot override confirmations or fake controls')
        if args.preview_acks:
            gate.open_service(session_ack=gate.required_session_ack())
            ticket = gate.prepare(args.operation, acknowledgement=gate.required_ack(args.operation), **params)
            print(gate.required_session_ack())
            print(gate.required_ack(args.operation))
            print(gate.required_action_ack(ticket))
            print(gate.required_sim_ack(args.operation))
            critical = gate.required_critical_ack(args.operation)
            if critical is not None: print(critical)
            print('PREVIEW ONLY; no fake command sent, no device observed')
            return 0
        gate.open_service(session_ack=args.session_ack)
        ticket = gate.prepare(args.operation, acknowledgement=args.ack, **params)
        report = gate.simulate(ticket, action_ack=args.action_ack,
                               acknowledgement=args.ack, simulation_ack=args.sim_ack,
                               critical_ack=args.critical_ack,
                               inject_fault_step=args.inject_fault_step,
                               inject_fault_phase=args.inject_fault_phase)
        print(f'{report.result.status} fake_plan_sha256={ticket.plan_sha256}')
        for e in report.result.events:
            print(f'{e.kind}: {e.command} {e.detail}')
        print(report.warning)
        return 0 if report.result.status == 'SIMULATED_PASS' else 3
    except (ValueError, TypeError, OSError, ServicePolicyError) as exc:
        print(f'REJECTED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
