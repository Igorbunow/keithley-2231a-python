#!/usr/bin/env python3
"""Review typed service SCPI plans OFFLINE. No execute option or transports."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from keithley2231a.service import ServicePlanner, ServicePolicyError, ServiceProfile


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='SIMULATION ONLY; NEVER sends SCPI')
    parser.add_argument('--service-profile', required=True)
    parser.add_argument('--operation', required=True)
    parser.add_argument('--params-json', default='{}', help='explicit JSON object, no unsafe raw SCPI')
    parser.add_argument('--show-required-ack', action='store_true')
    parser.add_argument('--ack', help='exact confirmation for the one operation')
    args = parser.parse_args(argv)
    try:
        planner = ServicePlanner(ServiceProfile.load(args.service_profile))
        if args.show_required_ack:
            print(planner.required_ack(args.operation))
            return 0
        if args.ack is None:
            raise ServicePolicyError('use --show-required-ack and explicit --ack')
        params = json.loads(args.params_json)
        if type(params) is not dict:
            raise ServicePolicyError('params-json must be an object')
        plan = planner.plan(args.operation, acknowledgement=args.ack, **params)
        print('SIMULATION_ONLY / NO HARDWARE ACCESS / NOT A CALIBRATION PROCEDURE')
        for idx, step in enumerate(plan.steps, 1):
            print(f'{idx:02d} [{step.risk.upper()}] {step.command} '
                  f'(manual §5 p.{step.manual_page})')
        print(plan.warning)
        return 0
    except (ServicePolicyError, ValueError, TypeError) as exc:
        print(f'REJECTED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
