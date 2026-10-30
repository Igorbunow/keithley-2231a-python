#!/usr/bin/env python3
"""R4.5-F-D: strictly OFFLINE, fixed 1N4933 forward I-V fixture plan.

The plan is not a measurement, a powered permit, or a command sequencer.
It cannot import any instrument driver, open a device, or send SCPI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

SCHEMA = 'R45FD_1N4933_FORWARD_PILOT_V1'
TOPOLOGY = 'CH1_PLUS_100OHM_ANODE_1N4933_CATHODE_CH1_MINUS_DMM_ACROSS_DIODE'
PRESETS = (0.80, 0.90, 1.00, 0.90, 0.80)
STEP_DIRECTIONS = ('up', 'up', 'down', 'down')
R_MIN_OHM = 95.0  # specified 100 ohm, 5% tolerance, not yet physically verified
R_MAX_OHM = 105.0
PSU_MAX_V = 1.00
CURRENT_LIMIT_A = 0.020
SUPPLY_POWER_ENVELOPE_W = PSU_MAX_V * CURRENT_LIMIT_A
BALLAST_WORST_SHORT_W = PSU_MAX_V**2 / R_MIN_OHM
DIODE_TEST_POWER_ENVELOPE_W = PSU_MAX_V**2 / R_MIN_OHM  # short-circuit-bound


class PlanError(ValueError):
    pass


def require(cond: bool, message: str) -> None:
    if not cond:
        raise PlanError(message)


def unique_pairs(pairs):
    obj = {}
    for k, v in pairs:
        require(k not in obj, 'Duplicate JSON key forbidden')
        obj[k] = v
    return obj


def reject_constant(_):
    raise PlanError('Nonfinite JSON constant forbidden')


def exact(obj, names, label):
    require(type(obj) is dict and set(obj) == set(names), f'Unexpected {label} schema')


def decimal_number(x, label, low, high):
    require(type(x) in (int, float) and math.isfinite(x) and low <= x <= high,
            f'{label} must be finite and within {low}..{high}')
    return float(x)


def read_plan(path: Path):
    require(path.is_file() and path.stat().st_size <= 8192, 'Plan missing or too large')
    data = path.read_bytes()
    require(data, 'Empty plan')
    try:
        obj = json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs,
                         parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlanError('Plan must be valid UTF-8 JSON') from exc
    return obj, hashlib.sha256(data).hexdigest()


def analyze(plan, *, sha256='NOT_PROVIDED'):
    exact(plan, ('schema','part','topology','wiring','ballast','program'), 'root')
    require(plan['schema'] == SCHEMA, 'Wrong schema')
    require(plan['part'] == '1N4933', 'Only physical 1N4933 is allowed')
    require(plan['topology'] == TOPOLOGY, 'Unreviewed topology')
    wiring = plan['wiring']
    exact(wiring, ('anode_after_ballast','banded_cathode_on_ch1_minus',
                   'dmm_front_dcv_across_diode','kel103_physically_disconnected',
                   'ch2_ch3_physically_disconnected','other_branches_absent',
                   'all_outputs_initially_off','external_power_absent'), 'wiring')
    for k, v in wiring.items():
        require(type(v) is bool and v is True, f'Physical wiring assertion missing: {k}')
    b = plan['ballast']
    exact(b, ('nominal_ohm','tolerance_percent','power_rating_w',
              'physically_checked_with_power_off','measured_ohm'), 'ballast')
    require(decimal_number(b['nominal_ohm'],'nominal ohm',100,100)==100,
            'Nominal ballast must be exactly 100 ohm')
    require(decimal_number(b['tolerance_percent'],'tolerance',5,5)==5,
            'Only 100 ohm 5 percent ballast is considered')
    require(decimal_number(b['power_rating_w'],'ballast rated power',.25,100) >= .25,
            'A ballast rated at least 0.25W is required')
    require(type(b['physically_checked_with_power_off']) is bool,
            'Physical resistor check must be explicit boolean')
    if b['physically_checked_with_power_off']:
        measured = decimal_number(b['measured_ohm'],'measured ballast',R_MIN_OHM,R_MAX_OHM)
    else:
        require(b['measured_ohm'] is None, 'Do not claim measurement before a physical check')
        measured = None
    program = plan['program']
    exact(program, ('mode','source_voltages_v','step_directions','psu_current_limit_a',
                    'psu_maximum_voltage_v','maximum_powered_s','dmm_connection'), 'program')
    require(program['mode'] == 'FORWARD_CV_ONLY', 'Reverse and CC testing prohibited')
    require(type(program['source_voltages_v']) is list and
            len(program['source_voltages_v']) == 5 and all(
                type(v) in (int, float) and math.isfinite(v) and abs(v-target)<1e-9
                for v,target in zip(program['source_voltages_v'],PRESETS)),
            'Source schedule must be exactly 0.8 0.9 1.0 0.9 0.8')
    require(type(program['step_directions']) is list and
            program['step_directions'] == list(STEP_DIRECTIONS),
            'Only UP UP DOWN DOWN is permitted')
    decimal_number(program['psu_current_limit_a'], 'PSU current limit', .020, .020)
    decimal_number(program['psu_maximum_voltage_v'], 'PSU max voltage', 1.0, 1.0)
    decimal_number(program['maximum_powered_s'], 'powered deadline', 45, 90)
    require(program['dmm_connection'] == 'FRONT_DCV_DIODE_ANODE_POSITIVE_CATHODE_NEGATIVE',
            'DMM must read across the diode with verified polarity')
    require(PSU_MAX_V / R_MIN_OHM < CURRENT_LIMIT_A,
            'Physical ballast short-circuit ceiling must be below electronic current limit')
    require(2*BALLAST_WORST_SHORT_W <= b['power_rating_w'],
            'Ballast lacks conservative power margin')
    return dict(
        status='DESIGN_CHECK_PASS_HW_NOT_AUTHORIZED',
        plan_sha256=sha256, part='1N4933',
        intended_measurement='STEADY_STATE_FORWARD_SCREENING_NOT_CALIBRATION',
        setpoints_v=list(PRESETS), step_directions=list(STEP_DIRECTIONS),
        ballast_nominal_ohm=100.0, ballast_tolerance_percent=5.0,
        ballast_physical_measurement_ohm=measured,
        ballast_operator_claimed_verified=b['physically_checked_with_power_off'],
        conservative_ballast_min_ohm=R_MIN_OHM,
        short_circuit_current_upper_ma=1000*PSU_MAX_V/R_MIN_OHM,
        short_circuit_ballast_power_upper_w=BALLAST_WORST_SHORT_W,
        source_electronic_current_limit_a=CURRENT_LIMIT_A,
        supply_voltage_x_current_limit_upper_w=SUPPLY_POWER_ENVELOPE_W,
        theoretical_resistor_bound_on_diode_power_w=DIODE_TEST_POWER_ENVELOPE_W,
        measurement_roles={
            'diode_voltage':'DMM7510_FRONT_DCV_DIRECTLY_ACROSS_DIODE',
            'screening_current':'(KEITHLEY_CH1_MEAS_VOLT-DMM7510_DIODE_V)/ACTUAL_SERIES_R',
            'cross_check_only':'KEITHLEY_CH1_MEAS_CURR_TOTAL_OUTPUT',
            'kel103':'PHYSICALLY_DISCONNECTED',
            'independent_dut_current_sensor':False,
        },
        hardware_authorized=False,
        current_accuracy='UNQUALIFIED; PSU voltage + DMM sequential, R tolerance, leads',
        limitations=[
            'This tool never communicates with the bench or permits power ON.',
            'A 100 ohm 5% resistor rated >=0.25W must be physically inspected and measured OFF.',
            'If resistor or diode is missing, open, reversed, or shorted, abort the run.',
            'A physical resistor does not prevent faulty external wiring or other supplies.',
            '1N4933 datasheet VF<=1.2V is specified at 1A, not at the few mA of this pilot.',
            'KEL103 is absent, so previous KEL-powered qualification is not reusable.',
            'A NEW topology-specific gated runner and UART fault tests are required.',
            'Forward curve samples are limited to 3 distinct source voltage levels.',
            'Reverse leakage, breakdown, recovery and absolute calibration are NOT tested.',
        ],
    )


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    args=p.parse_args(argv)
    try:
        plan,sha=read_plan(args.plan)
        result=analyze(plan,sha256=sha)
    except (OSError, PlanError, RecursionError, OverflowError) as exc:
        print(f'R4.5-F-D DESIGN_REJECTED / HW NOT_AUTHORIZED: {type(exc).__name__}: {exc}',
              file=sys.stderr)
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
