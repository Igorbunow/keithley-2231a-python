#!/usr/bin/env python3
"""Offline-only sanitized CSV/JSON/SVG export of independently audited R45FD evidence.

No instrument I/O, calibration, interpolation, or automatic hardware qualification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_r4_5_1n4933_evidence import audit, load_original, LEVELS, AuditError
from r4_5_iv_export import csv_text, export_files, finite, require, utc_string

SAMPLING = 'SEQUENTIAL_NOT_SYNCHRONIZED'
METHOD = 'SEQUENTIAL_PSU_V_MINUS_DMM_DIODE_V_OVER_MEASURED_R'
POINT_COLUMNS = (
    'point', 'direction', 'point_utc', 'point_elapsed_s',
    'commanded_v', 'digital_v', 'psu_v', 'diode_v', 'ballast_drop_v',
    'ballast_ohm', 'screening_current_ma', 'psu_current_ma',
    'psu_minus_screening_current_ma', 'diode_estimated_power_mw',
    'psu_sample_elapsed_s', 'dmm_sample_elapsed_s', 'sample_gap_ms',
    'sampling', 'current_method', 'current_accuracy',
)
SEGMENT_COLUMNS = (
    'segment', 'direction', 'from_point', 'to_point',
    'delta_diode_v', 'delta_screening_current_ma',
    'incremental_resistance_ohm', 'incremental_conductance_s',
)
EXPECTED_FILES = ('points.csv', 'segments.csv', 'summary.json', 'iv_forward.svg')


def prepare_export(rows: list[dict], review: dict, original_sha256: str):
    require(review.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS' and
            review.get('qualification') == '1N4933_FORWARD_STEADY_STATE_SCREENING_ONLY' and
            review.get('current_accuracy') == 'UNQUALIFIED_NO_INDEPENDENT_DUT_AMMETER',
            'Independent diode evidence review not accepted')
    start = next((r for r in rows if r.get('event') == 'start'), None)
    require(start is not None and start.get('variant') == '1N4933_FORWARD_100OHM',
            'Unexpected DUT or hardware topology')
    t0 = finite(start.get('monotonic_s'), 'start time')
    raw = [r for r in rows if r.get('event') == 'diode_iv_point']
    require(len(raw) == len(LEVELS) == len(review.get('points', [])),
            'Exactly five independently reviewed measurements are required')
    points = []
    for index, (point, reviewed, expected_v) in enumerate(zip(raw, review['points'], LEVELS)):
        require(point.get('index') == index and point.get('preset_v') == expected_v and
                point.get('sampling') == SAMPLING and
                point.get('current_method') == METHOD and
                point.get('independent_current_sensor') is False and
                point.get('weak_current_unquantified') is False,
                'Unreviewed point, sampling or current measurement claim')
        raw_numeric = {key: finite(point.get(key), key) for key in (
            'digital_v', 'psu_voltage_v', 'psu_current_a', 'diode_voltage_v',
            'resistor_drop_v', 'screening_current_a', 'diode_estimated_power_w', 'monotonic_s')}
        voltage = raw_numeric['diode_voltage_v']
        estimated = raw_numeric['screening_current_a']
        require(abs(voltage - finite(reviewed.get('diode_voltage_v'), 'review voltage')) <= 1e-9 and
                abs(estimated * 1000 - finite(reviewed.get('screening_current_ma'), 'review current')) <= 1e-7 and
                abs(raw_numeric['psu_current_a'] * 1000 -
                    finite(reviewed.get('psu_current_ma'), 'review PSU current')) <= 1e-7,
                'Point differs from audited physical samples')
        stamps = point.get('timestamps_monotonic_s')
        require(isinstance(stamps, dict), 'Missing separate PSU/DMM times')
        psu_t = finite(stamps.get('psu'), 'PSU timestamp')
        dmm_t = finite(stamps.get('dmm'), 'DMM timestamp')
        require(t0 < psu_t < dmm_t < raw_numeric['monotonic_s'] and
                dmm_t - psu_t < 1, 'Invalid sequential sample chronology')
        points.append({
            'point': index,
            'direction': ('start', 'up', 'up', 'down', 'down')[index],
            'point_utc': utc_string(point.get('utc')),
            'point_elapsed_s': raw_numeric['monotonic_s'] - t0,
            'commanded_v': expected_v,
            'digital_v': raw_numeric['digital_v'],
            'psu_v': raw_numeric['psu_voltage_v'],
            'diode_v': voltage,
            'ballast_drop_v': raw_numeric['resistor_drop_v'],
            'ballast_ohm': 99.86,
            'screening_current_ma': estimated * 1000,
            'psu_current_ma': raw_numeric['psu_current_a'] * 1000,
            'psu_minus_screening_current_ma': (raw_numeric['psu_current_a'] - estimated) * 1000,
            'diode_estimated_power_mw': raw_numeric['diode_estimated_power_w'] * 1000,
            'psu_sample_elapsed_s': psu_t - t0,
            'dmm_sample_elapsed_s': dmm_t - t0,
            'sample_gap_ms': (dmm_t - psu_t) * 1000,
            'sampling': SAMPLING,
            'current_method': METHOD,
            'current_accuracy': 'UNQUALIFIED',
        })

    segments = []
    for index in range(1, len(points)):
        direction = 'up' if index <= 2 else 'down'
        # Signed in natural measurement order, unlike stage's magnitude convention.
        du = points[index]['diode_v'] - points[index-1]['diode_v']
        di_ma = points[index]['screening_current_ma'] - points[index-1]['screening_current_ma']
        require(du * di_ma > 0 and abs(du) > 1e-6 and abs(di_ma) > .001,
                'Invalid I-V segment or unstable derivative')
        segments.append({
            'segment': index, 'direction': direction,
            'from_point': index-1, 'to_point': index,
            'delta_diode_v': du,
            'delta_screening_current_ma': di_ma,
            'incremental_resistance_ohm': du / (di_ma / 1000),
            'incremental_conductance_s': (di_ma / 1000) / du,
        })

    review_points = review['points']
    require(review_points[0]['preset_v'] == .8 and review_points[2]['preset_v'] == 1.,
            'Unexpected reviewed profile')
    summary = {
        'status': 'OFFLINE_ANALYSIS_OF_INDEPENDENTLY_REVIEWED_HARDWARE_EVIDENCE',
        'source_sha256': original_sha256,
        'source_test_id': 'R45FD',
        'source_variant': '1N4933_FORWARD_100OHM',
        'source_dirty_at_measurement': start.get('software', {}).get('dirty'),
        'source_provenance_hashes': review['provenance'],
        'qualification': review['qualification'],
        'sampling': SAMPLING,
        'current_method': METHOD,
        'current_accuracy': review['current_accuracy'],
        'physical_ballast_ohm_operator_measured': 99.86,
        'resistor_temperature_c_operator_reported': 26.,
        'count_points': len(points),
        'count_segments': len(segments),
        'powered_duration_s': review['powered_duration_s'],
        'return_diode_error_mv': review['return_diode_error_mv'],
        'return_current_error_ua': review['return_current_error_ua'],
        'mean_psu_minus_resistor_current_ma': review['mean_psu_minus_resistor_current_ma'],
        'max_abs_psu_resistor_difference_ma': review['max_abs_psu_resistor_difference_ma'],
        'maximum_screening_current_ma': max(p['screening_current_ma'] for p in points),
        'maximum_estimated_diode_power_mw': max(p['diode_estimated_power_mw'] for p in points),
        'point_sample_gap_ms': [p['sample_gap_ms'] for p in points],
        'scpi_zero_error_polls': {
            'psu': review['scpi']['psu_zero_error_polls'],
            'dmm': review['scpi']['dmm_zero_error_polls'],
        },
        'limitations': [
            'Current is estimated using two host-sequenced voltage measurements and an operator-measured resistor.',
            'PSU current disagreement is unresolved; no independent DUT current calibration was performed.',
            'Graph and slopes are descriptive only: no uncertainty bounds, transient response, or interpolation.',
            'Forward steady-state five-point screening only; no reverse-leakage, breakdown or recovery claim.',
            'This offline export does not constitute additional hardware qualification.',
        ],
        'files': list(EXPECTED_FILES),
    }
    require(summary['source_dirty_at_measurement'] is True and
            all(isinstance(value, str) and len(value) == (40 if key == 'commit' else 64)
                for key, value in summary['source_provenance_hashes'].items()),
            'Unexpected reviewed provenance')
    return points, segments, summary


def render_svg(points: list[dict]) -> str:
    """A self-contained, noninterpolated five-point SVG; no untrusted labels."""
    require(len(points) == 5, 'SVG requires five audited measurements')
    xs = [finite(p['diode_v'], 'plot diode voltage') for p in points]
    ys = [finite(p['screening_current_ma'], 'plot estimated current') for p in points]
    require(all(.2 < x < 1.0 for x in xs) and all(0 < y < 20 for y in ys),
            'Plot values outside the reviewed envelope')
    # Fixed axes so a later export cannot visually exaggerate tiny differences.
    x_min, x_max = .50, .62
    y_min, y_max = 0., 5.
    require(all(x_min <= x <= x_max and y_min <= y <= y_max for x, y in zip(xs, ys)),
            'Plot would clip audited measurements')
    def xy(x, y):
        return 90 + (x-x_min)/(x_max-x_min)*610, 370 - (y-y_min)/(y_max-y_min)*280
    def polyline(indices, color):
        locs = ' '.join(f'{xy(xs[i],ys[i])[0]:.2f},{xy(xs[i],ys[i])[1]:.2f}' for i in indices)
        return f'<polyline points="{locs}" fill="none" stroke="{color}" stroke-width="2.2"/>'
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 780 505" role="img" aria-label="Five audited steady-state forward I-V screening points for 1N4933">',
             '<rect width="780" height="505" fill="white"/>',
             '<text x="90" y="35" font-family="sans-serif" font-size="19" font-weight="bold">1N4933: forward I-V (five reviewed points)</text>',
             '<text x="90" y="56" font-family="sans-serif" font-size="12">Sequential measurements; current estimated using 99.86 ohm ballast; accuracy unqualified</text>',
             '<path d="M90 90 V370 H700" fill="none" stroke="#333" stroke-width="1.5"/>']
    for x in (.50,.52,.54,.56,.58,.60,.62):
        p, _ = xy(x, 0)
        lines.append(f'<path d="M{p:.2f} 370 v5" stroke="#555"/><text x="{p:.2f}" y="393" text-anchor="middle" font-family="sans-serif" font-size="11">{x:.2f}</text>')
    for y in range(6):
        _, p = xy(.50, y)
        lines.append(f'<path d="M85 {p:.2f} H700" stroke="#ddd"/><text x="78" y="{p+4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11">{y}</text>')
    lines.extend([
        polyline([0,1,2], '#205fac'), polyline([2,3,4], '#ba5432'),
        '<text x="395" y="429" text-anchor="middle" font-family="sans-serif" font-size="13">Diode voltage, V (DMM7510)</text>',
        '<text x="22" y="233" transform="rotate(-90 22 233)" text-anchor="middle" font-family="sans-serif" font-size="13">Estimated current, mA</text>',
    ])
    for index, (x, y) in enumerate(zip(xs, ys)):
        px, py = xy(x,y)
        color = '#205fac' if index<=2 else '#ba5432'
        lines.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4" fill="{color}" stroke="white" stroke-width="1.5"/>')
    lines.extend([
        '<path d="M90 454 h24" stroke="#205fac" stroke-width="2.2"/><text x="120" y="458" font-family="sans-serif" font-size="12">Ascending</text>',
        '<path d="M230 454 h24" stroke="#ba5432" stroke-width="2.2"/><text x="260" y="458" font-family="sans-serif" font-size="12">Descending</text>',
        '<text x="90" y="485" font-family="sans-serif" font-size="11">Descriptive screening only - not a calibrated current measurement</text>',
        '</svg>',
    ])
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path, help='Original, unmodified R45FD events.jsonl')
    parser.add_argument('--expected-sha256', required=True, help='Full SHA-256 of original evidence')
    parser.add_argument('--output-dir', type=Path, help='New directory for four sanitized outputs (never overwrite)')
    parser.add_argument('--dry-run', action='store_true', help='Audit and analyze only, write nothing')
    args = parser.parse_args(argv)
    if not args.dry_run and args.output_dir is None:
        parser.error('--output-dir is required unless --dry-run is used')
    try:
        rows, original_sha256 = load_original(args.events, args.expected_sha256)
        review = audit(rows)
        points, segments, summary = prepare_export(rows, review, original_sha256)
        svg = render_svg(points)
        if not args.dry_run:
            require(args.output_dir.resolve() != args.events.resolve(),
                    'Output destination must differ from input evidence')
            outputs = {
                'points.csv': csv_text(points, POINT_COLUMNS),
                'segments.csv': csv_text(segments, SEGMENT_COLUMNS),
                'summary.json': json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                'iv_forward.svg': svg,
            }
            export_files(args.output_dir, outputs)
    except (AuditError, OSError, KeyError, IndexError, TypeError,
            ValueError, UnicodeError, OverflowError, ZeroDivisionError) as exc:
        print(f'R4.5-F-D EXPORT REJECTED / no HW status changed: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-D OFFLINE VERIFIED ANALYSIS (no instrument I/O; not new HW qualification)')
    print(json.dumps({k:summary[k] for k in (
        'source_sha256','count_points','count_segments','maximum_screening_current_ma',
        'return_diode_error_mv','mean_psu_minus_resistor_current_ma')}, indent=2))
    print('DRY RUN: no export files written' if args.dry_run else f'Files saved in: {args.output_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
