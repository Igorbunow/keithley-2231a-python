#!/usr/bin/env python3
"""O02-A synthetic demonstration; NO --execute, transport or physical PSU access."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experiments.o02_dual_psu.dual_offline import SimStep, SimulatedSource, simulate_pair


def main(argv=None):
    parser = argparse.ArgumentParser(description='O02-A synthetic PSU pair; strictly offline, no hardware')
    parser.add_argument('--evidence', required=True, type=Path,
                        help='new JSONL path; existing data will never be overwritten')
    parser.add_argument('--fault-source', choices=('A', 'B'))
    parser.add_argument('--fault', choices=sorted(SimulatedSource.FAULTS))
    parser.add_argument('--initially-on', choices=('A', 'B'))
    args = parser.parse_args(argv)
    if (args.fault_source is None) != (args.fault is None):
        parser.error('--fault-source and --fault must be specified together')
    sim = simulate_pair(fault_on=args.fault_source, fault=args.fault,
                        initial_on=args.initially_on)
    result = sim.run(approval_a=sim.approval_token('A'),
                     approval_b=sim.approval_token('B'),
                     steps=(SimStep('A', 0.5, 0.04), SimStep('B', 0.75, 0.05)),
                     evidence_path=args.evidence)
    print(f'O02-A SIMULATION ONLY: {result}; {sim.final_states}; {sim.reason or "no faults"}')
    print(f'No physical instruments were opened. JSONL: {args.evidence}')
    return 0 if result == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
