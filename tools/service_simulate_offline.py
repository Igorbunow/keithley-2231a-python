#!/usr/bin/env python3
"""Single-operation synthetic service simulation: NO hardware access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from keithley2231a.service import ServicePolicyError, ServiceProfile
from keithley2231a.service_simulator import FakeBench, ServiceSimulator


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ServicePolicyError(f'duplicate simulation parameter key: {key}')
        result[key] = value
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='FAKE SERVICE SIMULATION; NEVER OPENS HARDWARE')
    parser.add_argument('--service-profile', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--params-json', default='{}')
    parser.add_argument('--show-required-acks', action='store_true')
    parser.add_argument('--ack')
    parser.add_argument('--sim-ack')
    parser.add_argument('--critical-ack')
    args = parser.parse_args(argv)
    try:
        profile = ServiceProfile.load(args.service_profile)
        # FakeBench is derived from a *profile*, NOT from USB/SCPI/OS discovery.
        simulator = ServiceSimulator(profile, FakeBench(profile.model, profile.serial))
        if args.show_required_acks:
            print(simulator.required_ack(args.operation))
            print(simulator.required_sim_ack(args.operation))
            critical = simulator.required_critical_ack(args.operation)
            if critical is not None:
                print(critical)
            return 0
        params = json.loads(args.params_json, object_pairs_hook=_reject_duplicate_keys)
        if type(params) is not dict:
            raise ServicePolicyError('params-json must be JSON object')
        result = simulator.simulate(args.operation, acknowledgement=args.ack,
                                    simulation_ack=args.sim_ack,
                                    critical_ack=args.critical_ack, **params)
        print(result.status, result.report_sha256)
        for event in result.events:
            print(f'{event.kind}: {event.command} {event.detail}')
        print(result.disclaimer)
        return 0 if result.status == 'SIMULATED_PASS' else 3
    except (ServicePolicyError, TypeError, ValueError) as exc:
        print(f'REJECTED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
