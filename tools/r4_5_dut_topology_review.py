#!/usr/bin/env python3
"""R4.5-F-C design-only check for a low-energy, single-branch DUT I-V fixture.

Pure offline contract checker. No instrument access, evidence creation, physical
authorization, or permission to use existing powered STEP guards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

SCHEMA = 'R45FC_DUT_SCREENING_PLAN_V1'
TOPOLOGY = 'PSU_CH1_BALLAST_DUT_SINGLE_SERIES_DMM_DCV_ACROSS_DUT'
DIRS = ('UP', 'UP', 'DOWN', 'DOWN')
MEASUREMENTS = {
    'dut_voltage': 'DMM7510_FRONT_DCV_ACROSS_DUT',
    'current': 'KEITHLEY_CH1_MEAS_CURR_TOTAL_OUTPUT',
    'kel103': 'PHYSICALLY_DISCONNECTED',
    'current_independent': False,
    'additional_parallel_loads': False,
}
ROOT_FIELDS = {'schema','topology','dut','measurements','ballast','limits'}
DUT_FIELDS = {'kind','identifier','rated_forward_voltage_v','rated_forward_current_a',
              'rated_power_w','externally_powered','polarity_marked'}
BALLAST_FIELDS = {'minimum_verified_ohm','rated_power_w','wiring'}
LIMIT_FIELDS = {'start_voltage_v','step_voltage_v','maximum_voltage_v',
                'psu_current_limit_a','maximum_dut_power_w','maximum_powered_s'}


class PlanError(ValueError):
    """An unsafe, unsupported or ambiguous DUT measurement proposal."""


def fail(reason):
    raise PlanError(reason)


def require(test, reason):
    if not test:
        fail(reason)


def object_no_duplicates(pairs):
    value = {}
    for key, val in pairs:
        require(key not in value, 'Duplicate JSON key is forbidden')
        value[key] = val
    return value


def reject_constant(value):
    fail('Non-JSON numeric constant forbidden')


def exact_object(obj, fields, label):
    require(type(obj) is dict, f'{label} must be an object')
    require(set(obj) == fields,
            f'{label} fields mismatch; missing={sorted(fields-set(obj))}, extra={sorted(set(obj)-fields)}')


def number(obj, name, *, minimum=0, maximum=10**9, strictly_positive=True):
    x = obj[name]
    require(type(x) in (int,float) and math.isfinite(x), f'{name} must be a finite number')
    require(minimum <= x <= maximum and (not strictly_positive or x > 0),
            f'{name} outside allowed envelope')
    return float(x)


def read_plan(path: Path):
    # Path read is the *only* file operation; input size is strictly bounded.
    require(path.is_file(), 'Plan file missing')
    require(path.stat().st_size <= 12_000, 'Plan exceeds 12kB limit')
    raw = path.read_bytes()
    require(len(raw) > 0, 'Empty plan')
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=object_no_duplicates,
                         parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError('Plan is not strict UTF-8 JSON') from exc
    return obj, hashlib.sha256(raw).hexdigest()


def review(plan, *, source_sha256='NOT_PROVIDED'):
    """Return an *offline design result*, never an authorization to energize."""
    exact_object(plan, ROOT_FIELDS, 'root')
    require(plan['schema'] == SCHEMA, 'Unsupported schema')
    require(plan['topology'] == TOPOLOGY, 'Unsupported/unreviewed DUT topology')
    m = plan['measurements']
    exact_object(m, set(MEASUREMENTS), 'measurements')
    # Exact types: bool must not masquerade as an integer.
    for k,v in MEASUREMENTS.items():
        require(type(m[k]) is type(v) and m[k] == v,
                f'Unsupported measurement route or misleading current claim: {k}')
    dut = plan['dut']; exact_object(dut, DUT_FIELDS, 'dut')
    require(dut['kind'] == 'PASSIVE_NON_ENERGY_STORING_TWO_TERMINAL',
            'Unknown, externally-powered, active or energy-storing DUT is not approved')
    ident = dut['identifier']
    require(type(ident) is str and 2 <= len(ident) <= 100 and ident.strip() == ident
            and not ident.upper().startswith(('TBD','EXAMPLE','UNSPECIFIED')),
            'A specific DUT identifier is required (not a placeholder)')
    require(dut['externally_powered'] is False, 'External DUT power is prohibited')
    require(dut['polarity_marked'] is True, 'Verified DUT polarity is mandatory')
    dv = number(dut,'rated_forward_voltage_v')
    di = number(dut,'rated_forward_current_a')
    dp = number(dut,'rated_power_w')

    b = plan['ballast']; exact_object(b, BALLAST_FIELDS, 'ballast')
    require(b['wiring'] == 'SERIES_BETWEEN_PSU_CH1_PLUS_AND_DUT_PLUS',
            'A verified external series ballast is required')
    r = number(b, 'minimum_verified_ohm', minimum=50, maximum=100_000)
    rp = number(b, 'rated_power_w', minimum=0.01, maximum=100)

    lim = plan['limits']; exact_object(lim, LIMIT_FIELDS, 'limits')
    start = number(lim,'start_voltage_v',minimum=0.01, maximum=1.0)
    step = number(lim,'step_voltage_v',minimum=.01,maximum=.10)
    top = number(lim,'maximum_voltage_v',minimum=.05,maximum=1.0)
    current = number(lim,'psu_current_limit_a',minimum=.001,maximum=.020)
    power = number(lim,'maximum_dut_power_w',minimum=.0001,maximum=.020)
    seconds = number(lim,'maximum_powered_s',minimum=5,maximum=30)
    require(abs(start + 2*step - top) <= 1e-9,
            'Only fixed START → UP → UP → DOWN → DOWN, with matching maximum, is supported')
    require(top <= dv and current <= di and power <= dp,
            'Proposed envelope exceeds a stated DUT forward rating')
    # Upper bound under conservative supply voltage + current limit (not measured power).
    require(top*current <= power + 1e-12,
            'DUT power cap is smaller than voltage × current upper envelope')
    require(r*current >= top - 1e-12,
            'Series ballast cannot limit a shorted passive DUT to the stated current ceiling')
    require(2*top*top/r <= rp + 1e-12,
            'Series ballast rated power lacks the required 2x conservative margin')
    voltages = (start, start+step, top, start+step, start)
    return {
        'status': 'DESIGN_CHECK_PASS_POWERED_NOT_AUTHORIZED',
        'schema': SCHEMA,
        'plan_sha256': source_sha256,
        'proposed_voltages_v': [round(v,9) for v in voltages],
        'directions': list(DIRS),
        'topology': TOPOLOGY,
        'measured_dut_voltage_source': m['dut_voltage'],
        'screening_current_source': m['current'],
        'independent_current_measurement': False,
        'maximum_psu_v': top,
        'maximum_psu_current_a': current,
        'envelope_power_upper_w': top*current,
        'ballast_minimum_verified_ohm': r,
        'ballast_worst_case_w': top*top/r,
        'maximum_powered_s': seconds,
        'hardware_authorized': False,
        'data_qualification': 'TOPOLOGY_SCREENING_NOT_METROLOGY',
        'limitations': [
            'NO instrument communication, output enable, or powered DUT permission.',
            'KEL103 is physically disconnected and does not measure DUT current.',
            'Keithley output current includes all connected branches including DMM leakage.',
            'Only one verified passive DUT branch and a series ballast are considered.',
            'DMM measures DUT voltage at its own probes; readings are host-sequenced.',
            'Nominal ballast and PSU current-limit settings are not independent protection.',
            'All limits rely on explicitly provided physical device ratings; unverified ratings invalidate this design.',
            'Separate hardware runner, physical gate and current sensor decision required.',
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True, help='Strict offline JSON DUT plan')
    args = parser.parse_args(argv)
    try:
        proposal, checksum = read_plan(args.plan)
        result = review(proposal, source_sha256=checksum)
    except (PlanError, OSError, RecursionError) as exc:
        print('R4.5-F-C DESIGN_REJECTED / HW NOT_AUTHORIZED:', type(exc).__name__+': '+str(exc),
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
