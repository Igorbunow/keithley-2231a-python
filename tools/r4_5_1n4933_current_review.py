#!/usr/bin/env python3
"""Offline-only analysis of the unexplained R45FD PSU / ballast-current mismatch.

The ORIGINAL bounded evidence must first pass the existing independent auditor.
This module cannot open a device or authorize a powered measurement.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from audit_r4_5_1n4933_evidence import AuditError, audit, load_original
from r4_5_1n4933_export import prepare_export
from r4_5_iv_export import finite, require

PLAN_TAG = 'R45FE_1N4933_BALLAST_DIRECT_DCV_OFFLINE_PLAN'
SCOPE = 'OFFLINE_REVIEW_AND_FUTURE_OPERATOR_GATED_DIRECT_BALLAST_VOLTAGE_PLAN'


def validate_plan(plan: object) -> dict:
    require(isinstance(plan, dict) and plan.get('id') == PLAN_TAG,
            'Wrong current cross-check plan')
    require(plan.get('hardware_authorized') is False and
            plan.get('execute_supported') is False and
            plan.get('topology') == 'CH1_SERIES_99P86OHM_1N4933' and
            plan.get('dmm_role') == 'FRONT_DCV_DIRECTLY_ACROSS_BALLAST' and
            plan.get('operator_rewire_only_when_psu_off') is True and
            plan.get('other_channels_off') is True and
            plan.get('kel_disconnected') is True,
            'Unsafe plan: no hardware access or automatic approval is permitted')
    require(type(plan.get('initial_preset_v')) in (int, float) and
            finite(plan['initial_preset_v'], 'initial voltage') == .9 and
            type(plan.get('current_limit_a')) in (int, float) and
            finite(plan['current_limit_a'], 'current limit') == .02 and
            type(plan.get('ballast_ohm')) in (int, float) and
            finite(plan['ballast_ohm'], 'ballast resistance') == 99.86,
            'Cross-check limits are not the reviewed bounded values')
    require(plan.get('next_action') == 'REQUEST_SEPARATE_HARDWARE_APPROVAL',
            'Cross-check must stop before physical testing')
    return plan


def review_current(points: list[dict], summary: dict, original_sha: str, plan: dict) -> dict:
    """Descriptive arithmetic only; not an uncertainty budget or calibration."""
    validate_plan(plan)
    require(summary.get('source_sha256') == original_sha and
            summary.get('count_points') == 5 and
            summary.get('qualification') == '1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY' and
            summary.get('current_accuracy') == 'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER' and
            summary.get('sampling') == 'SEQUENTIAL_NOT_SYNCHRONIZED',
            'Accepted source provenance, sampling or current accuracy missing')
    require(len(points) == 5 and
            [p['commanded_v'] for p in points] == [.8, .9, 1., .9, .8],
            'Unexpected source voltage sequence')
    estimated = [finite(p['screening_current_ma'], 'estimated current') for p in points]
    observed = [finite(p['psu_current_ma'], 'source current') for p in points]
    residual = [src - calc for src, calc in zip(observed, estimated)]
    require(all(0 < i < 20 for i in estimated) and all(0 <= i < 20 for i in observed),
            'Point currents outside the reviewed range')
    require(all(abs(float(p['ballast_ohm']) - 99.86) < 1e-9 for p in points),
            'Resistor value differs from operator measurement')
    require(abs(sum(residual)/5 - float(summary['mean_psu_minus_resistor_current_ma'])) < 1e-6,
            'Independent review current disagreement mismatch')
    mean = sum(residual)/5
    # Sample SD is descriptive of just five, non-independent samples.
    sample_sd = math.sqrt(sum((v - mean) ** 2 for v in residual) / 4)
    deltas = [
        {'step': i, 'direction': 'up' if i <= 2 else 'down',
         'delta_psu_ma': observed[i] - observed[i - 1],
         'delta_resistor_estimate_ma': estimated[i] - estimated[i - 1],
         'delta_disagreement_ma': residual[i] - residual[i - 1]}
        for i in range(1, 5)
    ]
    require(all(math.isfinite(v) for item in deltas for v in item.values() if type(v) in (int, float)),
            'Non-finite increment')
    # Hypothesis-separating future measurement: DMM DIRECTLY on ballast, not
    # subtracting the Keithley channel voltage from the DMM diode voltage.
    # Predictions are ONLY nominal values projected from the old session.
    midpoint = points[1]
    v_from_psu = midpoint['psu_current_ma'] / 1000 * 99.86
    v_from_prior_subtraction = midpoint['ballast_drop_v']
    require(.0 < v_from_psu < .9 and .0 < v_from_prior_subtraction < .9,
            'Nominal cross-check projections outside safe envelope')
    output = {
        'status': 'OFFLINE_DIAGNOSTIC_PASS_NO_NEW_HW_QUALIFICATION',
        'scope': SCOPE,
        'source_sha256': original_sha,
        'source_qualification': summary['qualification'],
        'current_accuracy': summary['current_accuracy'],
        'current_method': summary['current_method'],
        'sample_count': 5,
        'residual_ma': residual,
        'residual_mean_ma': mean,
        'residual_sample_sd_ma': sample_sd,
        'residual_span_ma': max(residual) - min(residual),
        'equivalent_99p86ohm_mean_residual_mv': mean * 99.86,
        'sequential_source_minus_dmm_sample_gap_ms': [p['sample_gap_ms'] for p in points],
        'steps': deltas,
        'future_direct_ballast_dcv_pilot': {
            'status': 'DESIGN_ONLY_HW_NOT_AUTHORIZED',
            'preset_v': plan['initial_preset_v'],
            'current_limit_a': plan['current_limit_a'],
            'voltage_limit_v': 1.05,
            'ballast_ohm': plan['ballast_ohm'],
            'dmm_connection': plan['dmm_role'],
            'projected_ballast_drop_v_from_old_psu_current': v_from_psu,
            'projected_ballast_drop_v_from_old_voltage_subtraction': v_from_prior_subtraction,
            'projection_difference_mv': (v_from_prior_subtraction - v_from_psu) * 1000,
            'these_are_predictions_not_new_measurements': True,
            'next_action': plan['next_action'],
        },
        'limitations': [
            'Five sequential samples are NOT an instrument uncertainty or calibration study.',
            'A near-constant difference does NOT identify a faulty instrument or a wiring cause.',
            'The two projected ballast drops come from the SAME old run, not independent DMM readings.',
            'No physical rewiring, DMM range change or output enabling is authorized by this tool.',
        ],
    }
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='Original reviewed hardware JSONL')
    parser.add_argument('--expected-sha256', required=True, help='Expected complete original SHA-256')
    parser.add_argument('--plan', type=Path, default=Path('docs/examples/R45FE_1N4933_current_crosscheck_plan.json'))
    args = parser.parse_args(argv)
    try:
        plan = validate_plan(json.loads(args.plan.read_text(encoding='utf-8')))
        rows, original_sha = load_original(args.evidence, args.expected_sha256)
        review = audit(rows)
        points, _segments, summary = prepare_export(rows, review, original_sha)
        result = review_current(points, summary, original_sha, plan)
    except (AuditError, OSError, ValueError, TypeError, KeyError, IndexError,
            OverflowError, ZeroDivisionError, UnicodeError) as exc:
        print(f'R4.5-F-E OFFLINE REVIEW REJECTED: {type(exc).__name__}: {exc}')
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
