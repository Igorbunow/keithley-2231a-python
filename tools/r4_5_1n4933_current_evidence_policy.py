#!/usr/bin/env python3
"""Fail-closed offline current-evidence policy and cross-method comparison.

Four hash-locked original runs, each passed through its own independent auditor.
This is NOT a new hardware stage, synchronous comparison, or calibration.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_r4_5_powered_step_evidence import AuditError, need
from audit_r4_5_1n4933_evidence import audit as audit_forward, load_original
from audit_r4_5_1n4933_ballast_evidence import (
    audit_ballast, EVENTS as BALLAST_EVENTS, BENCH_SHA,
)
from audit_r4_5_1n4933_series_dci_evidence import (
    audit as audit_series, EVENTS as SERIES_EVENTS,
)
from r4_5_iv_export import csv_text, export_files, finite

STATUS = 'INDEPENDENT_CROSS_METHOD_POLICY_PASS'
COLUMNS = (
    'method', 'source_sha256', 'preset_v', 'diagnostic_current_ma',
    'psu_readback_ma', 'psu_minus_diagnostic_ma', 'sample_role',
    'operational_evidence', 'metrology_qualification', 'timing',
)
SOURCE_ROLES = (
    ('SEQUENTIAL_PSU_MINUS_DMM_DIODE_OVER_R', 'FIVE_POINT_SCREENING_ONLY'),
    ('DIRECT_BALLAST_DCV_OVER_R', 'SINGLE_POINT_DIAGNOSTIC_ONLY'),
    ('DMM7510_FRONT_SERIES_DCI', 'SINGLE_POINT_DIAGNOSTIC_ONLY'),
)


def review_originals(files: dict[str, tuple[Path, str]]):
    """Always re-audit the originals; reports supplied by users are not trusted."""
    need(set(files) == {'forward', 'ballast_reversed', 'ballast_positive', 'series'},
         'Four separate original evidence inputs are required')
    raw = {}
    sha = {}
    for role in ('forward', 'ballast_reversed', 'ballast_positive', 'series'):
        path, digest = files[role]
        allowed = BALLAST_EVENTS if role.startswith('ballast_') else (
            SERIES_EVENTS if role == 'series' else None)
        raw[role], sha[role] = load_original(path, digest, allowed_events=allowed)
    need(len(set(sha.values())) == 4, 'Evidence files must represent four distinct original runs')
    need(all(rows[0].get('config_sha256') == BENCH_SHA for rows in raw.values()),
         'Hardware bench changed between audited runs')
    forward = audit_forward(raw['forward'])
    negative = audit_ballast(raw['ballast_reversed'], successful=False)
    positive = audit_ballast(raw['ballast_positive'], successful=True)
    series = audit_series(raw['series'])
    need(forward.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         negative.get('run') == 'REVERSED_LEADS_SAFE_ABORT' and
         negative.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         positive.get('run') == 'POSITIVE_LEADS_PASS' and
         positive.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         series.get('status') == 'INDEPENDENT_SERIES_DCI_EVIDENCE_REVIEW_PASS',
         'Independent source audit rejected')
    need(raw['ballast_positive'][0].get('software') ==
         raw['ballast_reversed'][0].get('software') and
         raw['ballast_positive'][0].get('plan_sha256') ==
         raw['ballast_reversed'][0].get('plan_sha256'),
         'Ballast positive and reverse runs have different source or plan')
    need(abs(negative['ballast_voltage_v'] + positive['ballast_voltage_v']) < .02 and
         abs(negative['psu_current_ma'] - positive['psu_current_ma']) < .5 and
         abs(negative['psu_voltage_v'] - positive['psu_voltage_v']) < .05,
         'Opposite DMM polarity does not match similar load')
    return forward, positive, negative, series, sha


def build_policy(forward: dict, ballast: dict, negative: dict,
                 series: dict, sha: dict):
    """Separate observed safe operation from unqualified measurement accuracy."""
    need(set(sha) == {'forward', 'ballast_reversed', 'ballast_positive', 'series'} and
         len(set(sha.values())) == 4 and
         all(isinstance(h, str) and len(h) == 64 and
             all(c in '0123456789abcdef' for c in h) for h in sha.values()),
         'Unverified source hashes')
    need(forward.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         forward.get('current_accuracy') == 'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER' and
         forward.get('qualification') == '1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY' and
         len(forward.get('points', [])) == 5 and
         ballast.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         ballast.get('run') == 'POSITIVE_LEADS_PASS' and
         ballast.get('current_accuracy') == 'UNQUALIFIED' and
         negative.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
         negative.get('run') == 'REVERSED_LEADS_SAFE_ABORT' and
         negative.get('current_accuracy') == 'UNQUALIFIED' and
         series.get('status') == 'INDEPENDENT_SERIES_DCI_EVIDENCE_REVIEW_PASS' and
         series.get('source_current_accuracy') == 'UNQUALIFIED' and
         series.get('scope') == 'ONE_POINT_DIAGNOSTIC_NOT_CURRENT_CALIBRATION' and
         series.get('physical_off_confirmed') is True,
         'Unreviewed source status or unsupported metrology claim')
    selected = forward['points'][1]
    need(finite(selected.get('preset_v'), 'forward preset') == .9,
         'Forward 0.90 V upward point is missing')
    points = (
        (selected['screening_current_ma'], selected['psu_current_ma']),
        (ballast['ballast_estimated_current_ma'], ballast['psu_current_ma']),
        (series['dmm_direct_current_ma'], series['psu_current_ma']),
    )
    rendered = []
    for i, (role, source, timing) in enumerate((
        ('forward', SOURCE_ROLES[0], 'SEQUENTIAL_PSU_AND_DMM'),
        ('ballast_positive', SOURCE_ROLES[1], 'SEQUENTIAL_PSU_AND_DMM'),
        ('series', SOURCE_ROLES[2], 'SEQUENTIAL_PSU_AND_DMM'),
    )):
        measure, psu = (finite(points[i][0], 'diagnostic current'),
                        finite(points[i][1], 'PSU readback'))
        need(.1 < measure < 8 and 0 <= psu < 8 and
             abs(psu - measure) < 10, 'Cross-method value outside audited envelope')
        rendered.append({
            'method': source[0], 'source_sha256': sha[role], 'preset_v': .9,
            'diagnostic_current_ma': measure,
            'psu_readback_ma': psu,
            'psu_minus_diagnostic_ma': psu - measure,
            'sample_role': source[1],
            'operational_evidence': 'INDEPENDENTLY_REVIEWED_SAFE_OFF',
            'metrology_qualification': 'UNQUALIFIED', 'timing': timing,
        })
    need(negative.get('ballast_estimated_current_ma') is None and
         finite(negative.get('ballast_voltage_v'), 'reversed voltage') < -.02,
         'Reverse-polarity abort was incorrectly accepted as current measurement')
    spec = finite(series.get('source_readback_spec_at_25C_pm5C_ma'), 'spec bound')
    diff = abs(rendered[-1]['psu_minus_diagnostic_ma'])
    need(10 <= spec < 11 and series.get('observed_difference_within_nominal_spec') is
         (diff <= spec), 'Incorrect manufacturer nominal-readback bound')
    summary = {
        'status': STATUS,
        'scope': 'OFFLINE_COMPARISON_OF_SEPARATE_RUNS_NOT_CALIBRATION',
        'hardware_stage_status': 'UNCHANGED',
        'safety_evidence': {
            'forward': 'PASS_VERIFIED_OFF',
            'ballast_positive': 'PASS_VERIFIED_OFF',
            'ballast_reversed': 'EXPECTED_SAFETY_ABORT_VERIFIED_OFF',
            'series': 'PASS_VERIFIED_OFF',
        },
        'metrology_policy': {
            'current_accuracy': 'UNQUALIFIED',
            'psu_readback_role': 'SAFETY_AND_CONTROL_TELEMETRY_NOT_PRECISE_IV_CURRENT',
            'resistor_subtraction_role': 'FORWARD_SCREENING_ONLY',
            'direct_ballast_dcv_role': 'DIAGNOSTIC_ONLY',
            'dmm_series_dci_role': 'PREFERRED_DIAGNOSTIC_CURRENT_NOT_CALIBRATION',
            'cross_run_bias_correction_allowed': False,
            'calibrated_curve_allowed': False,
            'nominal_psu_spec_25c_pm5c_ma': spec,
            'nominal_psu_spec_conditions_verified': False,
        },
        'source_sha256': sha,
        'resistor_ohm_operator_reported': 99.86,
        'count_comparable_diagnostic_records': len(rendered),
        'ballast_reversed_is_not_a_current_point': True,
        'constraints': [
            'All three diagnostic values were taken in SEPARATE runs with changed DMM topology.',
            'Do not combine these readings into one physical I-V curve or apply bias correction.',
            'DMM series current includes unknown burden voltage; OFF reading is not a calibration.',
            'The 2231A current readback specification is a conditional manufacturer limit, not verified accuracy.',
            'Only the existing audited safety scope is accepted; no new hardware qualification.',
        ],
        'files': ['points.csv', 'summary.json'],
    }
    return rendered, summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('forward', 'ballast-reversed', 'ballast-positive', 'series'):
        p.add_argument('--'+name, type=Path, required=True)
        p.add_argument('--'+name+'-sha256', required=True)
    p.add_argument('--output-dir', type=Path, help='Exclusive new folder; omit for dry-run')
    args = p.parse_args(argv)
    try:
        inputs = {key: (getattr(args, key), getattr(args, key+'_sha256')) for key in (
            'forward', 'ballast_reversed', 'ballast_positive', 'series')}
        review = review_originals(inputs)
        points, summary = build_policy(*review)
        if args.output_dir is not None:
            need(not any(args.output_dir.resolve() == path.resolve()
                         for path, _ in inputs.values()),
                 'Output path cannot be an input evidence file')
            export_files(args.output_dir, {
                'points.csv': csv_text(points, COLUMNS),
                'summary.json': json.dumps(summary, ensure_ascii=False, indent=2,
                                           sort_keys=True, allow_nan=False)+'\n',
            })
    except (AuditError, OSError, ValueError, TypeError, KeyError, OverflowError,
            AttributeError, ZeroDivisionError, UnicodeError) as exc:
        print(f'R4.5-F-H REJECTED (no HW status changed): {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-H INDEPENDENT MIXED-METHOD POLICY PASS (OFFLINE; NO HARDWARE I/O)')
    print(json.dumps({'status':summary['status'],
                      'source_sha256':summary['source_sha256'],
                      'count_comparable_diagnostic_records':len(points),
                      'psu_minus_series_dci_ma':points[-1]['psu_minus_diagnostic_ma'],
                      'metrology_qualification':summary['metrology_policy']['current_accuracy'],
                      'calibrated_curve_allowed':False}, indent=2))
    print('DRY RUN: no export files written' if args.output_dir is None else
          f'Files saved in: {args.output_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
