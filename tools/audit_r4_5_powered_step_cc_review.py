"""R4.5-E-B CC-specific, read-only review layered over the common evidence auditor.

Use the immutable original JSONL. This does not touch instruments or require
private bench configuration. Steady-state measurements ONLY, not transient proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, audit, need, number, one


def check_cc_consistency(records):
    """Additional CC physics checks after the base wire/evidence audit passes."""
    snaps = [e for e in records if e.get('event') == 'powered_step_snapshot']
    need(len(snaps) == 2 and [s.get('label') for s in snaps] == ['before', 'after'],
         'CC snapshots missing or out of order')
    first, second = snaps
    need(first.get('mode') == second.get('mode') == 'CC', 'Not CC measurements')
    for i, s in enumerate(snaps):
        kel_i = number(s.get('kel_a'))
        kel_u = number(s.get('kel_v'))
        dmm_u = number(s.get('dmm_v'))
        psu_i = number(s.get('psu_a'))
        psu_u = number(s.get('psu_v'))
        pw = number(s.get('psu_meas_w'))
        kw = number(s.get('kel_w'))
        expected_i = 0.100 + i * 0.005
        need(abs(kel_i - expected_i) <= 0.003, 'CC current is not near commanded limit')
        need(abs(psu_i - kel_i) <= 0.002, 'PSU/KEL current disagreement')
        need(10.5 <= kel_u / kel_i <= 11.5 and 10.5 <= dmm_u / kel_i <= 11.5,
             'Measured effective resistance conflicts with 11 ohm CR')
        need(abs(dmm_u - kel_u) <= 0.010, 'DMM/KEL voltage discrepancy')
        need(abs(psu_u - dmm_u) <= 0.012, 'PSU/DMM voltage discrepancy')
        need(abs(pw - psu_u * psu_i) <= 0.002, 'PSU power inconsistent with U*I')
        need(abs(kw - kel_u * kel_i) <= 0.002, 'KEL power inconsistent with U*I')
        times = s.get('timestamps_monotonic_s', {})
        vtimes = [number(times.get(k)) for k in ('psu_s', 'kel_s', 'dmm_s', 'power_s')]
        need(vtimes == sorted(vtimes) and (vtimes[2] - vtimes[0]) < 2.0,
             'Slow or out-of-order three-instrument sampling')
    delta_i = number(second['kel_a']) - number(first['kel_a'])
    delta_psu_i = number(second['psu_a']) - number(first['psu_a'])
    delta_dmm_v = number(second['dmm_v']) - number(first['dmm_v'])
    delta_kel_v = number(second['kel_v']) - number(first['kel_v'])
    delta_psu_w = number(second['psu_meas_w']) - number(first['psu_meas_w'])
    delta_kel_w = number(second['kel_w']) - number(first['kel_w'])
    need(0.0035 <= delta_i <= 0.0065, 'KEL CC step differs from 5mA')
    need(abs(delta_psu_i - delta_i) <= 0.0015, 'PSU/KEL CC step disagreement')
    need(abs(delta_dmm_v - 11.0 * delta_i) <= 0.006,
         'DMM voltage change disagrees with KEL current change x 11 ohm')
    need(abs(delta_kel_v - 11.0 * delta_i) <= 0.006,
         'KEL voltage change disagrees with its current change x 11 ohm')
    need(abs(delta_psu_w - delta_kel_w) <= 0.003,
         'PSU/KEL power increments disagree')
    step_write = one([e for e in records if e.get('event') == 'write'
                      and e.get('command') == 'CURR:UP'], 'CURR:UP')
    step_time = number(step_write.get('monotonic_s'))
    after_time = number(second['timestamps_monotonic_s']['psu_s'])
    need(0 < after_time - step_time <= 15,
         'First post-step analog sample outside bounded steady-state review interval')
    return {
        'kel_current_delta_a': delta_i,
        'psu_current_delta_a': delta_psu_i,
        'dmm_voltage_delta_v': delta_dmm_v,
        'kel_voltage_delta_v': delta_kel_v,
        'psu_power_delta_w': delta_psu_w,
        'kel_power_delta_w': delta_kel_w,
        'post_step_first_sample_delay_s': after_time - step_time,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        data = args.events.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        need(digest.lower() == args.expected_sha256.lower(), 'CC original evidence SHA-256 mismatch')
        records = [json.loads(line) for line in data.decode('utf-8').splitlines()]
        result = audit(records)
        need(result['mode'] == 'cc', 'Expected CC only; CV must not pass CC review')
        result['cc_consistency'] = check_cc_consistency(records)
        result['evidence_sha256'] = digest
    except (AuditError, ValueError, TypeError, KeyError, OSError, UnicodeError,
            AttributeError, ZeroDivisionError, OverflowError) as exc:
        print(f'R4.5-E-B CC REJECTED / NOT HW PASS: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-E-B CC HARDWARE REVIEWABLE PASS (one steady-state powered 5mA STEP)')
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
