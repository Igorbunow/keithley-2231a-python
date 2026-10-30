#!/usr/bin/env python3
"""R4.5-F-B offline-only, hash-locked and hardware-audited I-V data export.

Exports numeric metrology from the accepted R4.5-F-A CR40 five-point JSONL.
Never opens an instrument, never copies raw identity/serial/SCPI log text to export.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil

from audit_r4_5_iv_evidence import audit, AuditError

SAMPLE_MODE = 'SEQUENTIAL_NOT_SYNCHRONIZED'
COLUMNS = (
    'point','direction','point_utc','point_elapsed_s',
    'commanded_v','digital_v','psu_v','psu_a','psu_meas_w',
    'kel_v','kel_a','kel_w','dmm_v',
    'r_kel_ohm','r_dmm_kel_ohm','psu_minus_dmm_mv',
    'kel_minus_dmm_mv','kel_minus_psu_current_ma','kel_minus_psu_power_mw',
    'psu_sample_elapsed_s','kel_sample_elapsed_s',
    'dmm_sample_elapsed_s','power_sample_elapsed_s','sample_span_s',
    'sampling',
)
SEGMENT_COLUMNS = (
    'segment','direction','from_point','to_point',
    'delta_dmm_v','delta_kel_a','incremental_ohm','incremental_siemens',
)
EXPECTED_LEVELS = (3.0, 3.1, 3.2, 3.1, 3.0)


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise AuditError(reason)


def finite(value: object, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value), f'Non-finite or non-numeric {label}')
    return float(value)


def unique_object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value: str):
    raise AuditError(f'Non-JSON numeric constant: {value}')


def read_and_audit(path: Path, expected_sha256: str):
    require(bool(re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256)),
            'The expected SHA-256 must be exactly 64 hexadecimal characters')
    data = path.read_bytes()
    require(len(data) <= 30_000_000, 'Evidence exceeds the bounded exporter input size')
    sha256 = hashlib.sha256(data).hexdigest()
    require(sha256 == expected_sha256.lower(), 'Original evidence SHA-256 mismatch')
    text = data.decode('utf-8')
    require(text.endswith('\n'), 'Truncated JSONL: missing terminal newline')
    lines = text.splitlines()
    require(100 <= len(lines) <= 100_000 and all(line.strip() for line in lines),
            'Empty or unbounded event stream')
    rows = [json.loads(line, object_pairs_hook=unique_object_pairs,
                       parse_constant=reject_constant) for line in lines]
    require(all(isinstance(row, dict) for row in rows), 'A JSONL entry is not an object')
    review = audit(rows)  # Exact wire, hashes of runner/stage, gates, instruments, OFF and physics.
    require(review.get('status') == 'REVIEWABLE_PASS', 'Independent hardware review did not pass')
    return rows, review, sha256


def utc_string(text: object) -> str:
    require(isinstance(text, str) and len(text) < 64, 'Missing or invalid point UTC')
    date = datetime.fromisoformat(text)
    require(date.tzinfo is not None and date.utcoffset() is not None,
            'Point timestamp must include UTC offset')
    return date.isoformat()


def prepare_export(rows: list[dict], review: dict, sha256: str) -> tuple[list[dict], list[dict], dict]:
    require(review.get('status') == 'REVIEWABLE_PASS', 'Only reviewed evidence can be exported')
    start = next((x for x in rows if x.get('event') == 'start'), None)
    require(start is not None and start.get('variant') == 'RESISTIVE_IV_CR40',
            'Exporter only supports the reviewed CR40 I-V pilot')
    t0 = finite(start['monotonic_s'], 'run monotonic origin')
    raw_points = [x for x in rows if x.get('event') == 'iv_point']
    require(len(raw_points) == 5, 'Expected exactly five I-V readings')
    points = []
    for i, p in enumerate(raw_points):
        require(p.get('index') == i and p.get('sampling') == SAMPLE_MODE,
                'Unreviewed I-V point or synchronization assertion')
        source = {k: finite(p.get(k), k) for k in (
            'commanded_v','digital_v','psu_v','psu_a','psu_meas_w',
            'kel_v','kel_a','kel_w','dmm_v','kel_effective_ohm')}
        require(abs(source['commanded_v'] - EXPECTED_LEVELS[i]) < 1e-9,
                'Unexpected I-V setpoint')
        require(source['kel_a'] > 0, 'Cannot calculate resistance at zero or negative current')
        raw_t = p.get('timestamps_monotonic_s')
        require(isinstance(raw_t, dict), 'Missing separate instrument times')
        timing = {k: finite(raw_t.get(k), f'{k} monotonic time') for k in
                  ('psu','kel','dmm','power')}
        require(list(timing.values()) == sorted(timing.values()) and
                t0 < timing['psu'] <= timing['power'] <= finite(p['monotonic_s'], 'point time'),
                'Unordered measurement timestamps')
        ratio = source['kel_v'] / source['kel_a']
        require(abs(ratio - source['kel_effective_ohm']) < 1e-8,
                'Recorded effective resistance disagrees with raw U/I')
        obj = {
            'point': i,
            'direction': ('start','up','up','down','down')[i],
            'point_utc': utc_string(p.get('utc')),
            'point_elapsed_s': p['monotonic_s'] - t0,
            **{k: source[k] for k in ('commanded_v','digital_v','psu_v','psu_a',
                                    'psu_meas_w','kel_v','kel_a','kel_w','dmm_v')},
            'r_kel_ohm': ratio,
            'r_dmm_kel_ohm': source['dmm_v'] / source['kel_a'],
            'psu_minus_dmm_mv': (source['psu_v'] - source['dmm_v']) * 1000,
            'kel_minus_dmm_mv': (source['kel_v'] - source['dmm_v']) * 1000,
            'kel_minus_psu_current_ma': (source['kel_a'] - source['psu_a']) * 1000,
            'kel_minus_psu_power_mw': (source['kel_w'] - source['psu_meas_w']) * 1000,
            'psu_sample_elapsed_s': timing['psu'] - t0,
            'kel_sample_elapsed_s': timing['kel'] - t0,
            'dmm_sample_elapsed_s': timing['dmm'] - t0,
            'power_sample_elapsed_s': timing['power'] - t0,
            'sample_span_s': timing['power'] - timing['psu'],
            'sampling': SAMPLE_MODE,
        }
        require(all(math.isfinite(v) for v in obj.values() if type(v) in (int,float)),
                'Non-finite derived I-V value')
        points.append(obj)

    segments = []
    for i in range(1, 5):
        du = points[i]['dmm_v'] - points[i-1]['dmm_v']
        di = points[i]['kel_a'] - points[i-1]['kel_a']
        require(abs(du) > 0.05 and abs(di) > .001 and du * di > 0,
                'Ill-conditioned or reversed measurement segment')
        segments.append({'segment':i,'direction':'up' if i<=2 else 'down',
                         'from_point':i-1,'to_point':i,
                         'delta_dmm_v':du,'delta_kel_a':di,
                         'incremental_ohm':du/di,
                         'incremental_siemens':di/du})

    # Ordinary least squares of I(V) over all five *sequential* samples; no
    # accuracy, coverage, or confidence interval inferred from this fit.
    n = len(points)
    xs = [p['dmm_v'] for p in points]
    ys = [p['kel_a'] for p in points]
    x_mean = sum(xs)/n; y_mean = sum(ys)/n
    denominator = sum((x-x_mean)**2 for x in xs)
    require(denominator > 1e-9, 'Voltage span too narrow for regression')
    slope = sum((x-x_mean)*(y-y_mean) for x,y in zip(xs,ys)) / denominator
    require(slope > 0, 'Non-positive I–V regression slope')
    intercept = y_mean - slope*x_mean
    residual = [y - (slope*x+intercept) for x,y in zip(xs,ys)]
    ss_tot = sum((y-y_mean)**2 for y in ys)
    r_squared = 1 - sum(d*d for d in residual)/ss_tot if ss_tot > 0 else None
    extreme_du = xs[2] - xs[0]
    extreme_di = ys[2] - ys[0]
    require(extreme_du > 0 and extreme_di > 0,'Invalid forward extreme points')
    summary = {
        'status': 'OFFLINE_ANALYSIS_OF_REVIEWED_HARDWARE_EVIDENCE',
        'source_sha256': sha256,
        'source_commit': review['source_commit'],
        'source_dirty': review['source_dirty'],
        'source_test_id': 'R45FIVA',
        'source_variant': 'RESISTIVE_IV_CR40',
        'sampling': SAMPLE_MODE,
        'reference_load_setting_ohm': 40.0,
        'count_points': 5,
        'count_steps': 4,
        'mean_absolute_kel_resistance_ohm': sum(p['r_kel_ohm'] for p in points)/5,
        'min_absolute_kel_resistance_ohm': min(p['r_kel_ohm'] for p in points),
        'max_absolute_kel_resistance_ohm': max(p['r_kel_ohm'] for p in points),
        'two_point_forward_incremental_ohm': extreme_du/extreme_di,
        'ols_all_points_conductance_s': slope,
        'ols_all_points_equivalent_ohm': 1/slope,
        'ols_all_points_intercept_a': intercept,
        'ols_all_points_r_squared': r_squared,
        'dmm_return_error_mv': (xs[-1] - xs[0])*1000,
        'kel_return_error_ma': (ys[-1] - ys[0])*1000,
        'dmm_repeated_midpoint_error_mv': (xs[3]-xs[1])*1000,
        'kel_repeated_midpoint_error_ma': (ys[3]-ys[1])*1000,
        'maximum_measured_kel_power_w': max(p['kel_w'] for p in points),
        'point_sample_span_s': [p['sample_span_s'] for p in points],
        'observed_kel_current_display_quantum_a': 0.0001,
        'review_scope': review['scope'],
        'limitations': [
            'All measurements are host-sequenced, NOT simultaneously sampled.',
            'The observed KEL current display quantum is not a calibration uncertainty.',
            'Absolute U/I resistance and delta-U/delta-I resistance measure different quantities.',
            'Two-point and least-squares slope can be sensitive to rounded current samples and offsets.',
            'OLS R-squared is descriptive only, not an accuracy or validation certificate.',
            'Source dirty flag retained; source hashes and primary evidence SHA were reviewed.',
            'This characterizes the KEL103 CR40 reference branch, not an unknown DUT.',
            'No transient response, independent cutoff, metrological calibration, or DUT current measurement is claimed.',
        ],
        'files': ['points.csv','segments.csv','summary.json'],
    }
    return points, segments, summary


def csv_text(rows, fields):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output,fieldnames=fields,lineterminator='\n')
    writer.writeheader()
    for row in rows:
        require(set(row)==set(fields), 'Unexpected CSV export columns')
        writer.writerow(row)
    return output.getvalue()


def export_directory(destination: Path, points, segments, summary):
    require(not destination.exists() and not destination.is_symlink(),
            'Output directory already exists; no overwrite allowed')
    outputs = {
        'points.csv': csv_text(points,COLUMNS),
        'segments.csv': csv_text(segments,SEGMENT_COLUMNS),
        'summary.json': json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',
    }
    destination.mkdir(mode=0o700,parents=False,exist_ok=False)
    try:
        for name, contents in outputs.items():
            with (destination/name).open('x',encoding='utf-8',newline='') as out:
                out.write(contents)
    except BaseException:
        shutil.rmtree(destination)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events',type=Path,help='Original unmodified R4.5-F-A events.jsonl')
    parser.add_argument('--expected-sha256',required=True,help='Independent expected raw SHA-256')
    parser.add_argument('--output-dir',type=Path,help='New directory for 3 sanitized outputs (no overwrite)')
    parser.add_argument('--dry-run',action='store_true',help='Audit and analyze, but write no files')
    args = parser.parse_args(argv)
    if not args.dry_run and args.output_dir is None:
        parser.error('--output-dir is required unless --dry-run is selected')
    try:
        rows, review, sha = read_and_audit(args.events,args.expected_sha256)
        points, segments, summary = prepare_export(rows,review,sha)
        if not args.dry_run:
            dest = args.output_dir
            require(dest.resolve() != args.events.resolve(), 'Destination cannot be source evidence')
            export_directory(dest,points,segments,summary)
    except (AuditError, OSError, ValueError, TypeError, KeyError,
            UnicodeError, AttributeError, OverflowError, ZeroDivisionError) as exc:
        print(f'R4.5-F-B EXPORT REJECTED / no HW status changed: {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-B OFFLINE VERIFIED ANALYSIS (no instrument I/O; not a new HW qualification)')
    print(json.dumps({k: summary[k] for k in (
        'source_sha256','count_points','count_steps','mean_absolute_kel_resistance_ohm',
        'two_point_forward_incremental_ohm','ols_all_points_equivalent_ohm',
        'dmm_return_error_mv','maximum_measured_kel_power_w')},indent=2))
    if args.dry_run:
        print('DRY RUN: no export files written')
    else:
        print(f'Files saved in: {args.output_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
