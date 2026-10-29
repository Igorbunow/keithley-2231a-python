#!/usr/bin/env python3
"""OFFLINE fake service gateway. NO real device/serial/socket transport available."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from keithley2231a.service import ServicePolicyError, ServiceProfile
from keithley2231a.service_gateway_offline import OfflineServiceGateway
from keithley2231a.service_simulator import FakeBench


def _no_duplicate_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ServicePolicyError(f'duplicate parameters: {key}')
        data[key] = value
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='OFFLINE IN-MEMORY FAKE SCPI GATEWAY; no hardware access')
    parser.add_argument('--service-profile', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--params-json', default='{}')
    parser.add_argument('--show-required-acks', action='store_true')
    parser.add_argument('--ack')
    parser.add_argument('--sim-ack')
    parser.add_argument('--critical-ack')
    parser.add_argument('--inject-fault-step', type=int)
    parser.add_argument('--inject-fault-phase', default='send')
    args = parser.parse_args(argv)
    try:
        profile = ServiceProfile.load(args.service_profile)
        gateway = OfflineServiceGateway(profile, FakeBench(profile.model, profile.serial))
        if args.show_required_acks:
            print(gateway.required_ack(args.operation))
            print(gateway.required_sim_ack(args.operation))
            critical = gateway.required_critical_ack(args.operation)
            if critical:
                print(critical)
            return 0
        params = json.loads(args.params_json, object_pairs_hook=_no_duplicate_keys)
        if type(params) is not dict:
            raise ServicePolicyError('parameters must form a JSON object')
        if set(params) & {'acknowledgement', 'simulation_ack', 'critical_ack',
                          'inject_fault_step', 'inject_fault_phase'}:
            raise ServicePolicyError('parameters may not override approvals or fault controls')
        report = gateway.simulate(args.operation, acknowledgement=args.ack,
                                  simulation_ack=args.sim_ack,
                                  critical_ack=args.critical_ack,
                                  inject_fault_step=args.inject_fault_step,
                                  inject_fault_phase=args.inject_fault_phase, **params)
        print(f'{report.status} sha256={report.report_sha256} seq={report.sequence}')
        for event in report.events:
            print(f'{event.kind}: {event.command} {event.detail}')
        print(report.warning)
        return 0 if report.status == 'SIMULATED_PASS' else 3
    except (ServicePolicyError, TypeError, ValueError) as exc:
        print(f'REJECTED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
