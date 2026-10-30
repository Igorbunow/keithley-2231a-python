#!/usr/bin/env python3
"""Fail-closed offline I-V current-source selection from audited R45FD JSONL.

Only the five-point resistor-derived screening current exists in this run.
A later single-point series-DCI reading cannot be substituted for these points.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from audit_r4_5_1n4933_evidence import audit, load_original, AuditError
from iv_current_source_policy import (
    CurrentCandidate, PSU_READBACK, SEQUENTIAL_DIODE_DCV_OVER_R,
    select_current_series,
)
from r4_5_1n4933_export import prepare_export
from r4_5_iv_export import csv_text, export_files, finite

POINT_COLUMNS = (
    'point', 'direction', 'diode_v', 'selected_current_ma', 'current_source',
    'psu_control_readback_ma', 'run_sha256', 'sample_gap_ms', 'sampling',
    'metrology_qualification',
)
SEGMENT_COLUMNS = (
    'segment', 'from_point', 'to_point', 'direction', 'delta_diode_v',
    'delta_selected_current_ma', 'descriptive_incremental_ohm',
)


def select_audited_export(rows, review: dict, run_sha256: str, *,
                          source: str = 'auto', intent: str = 'diagnostic_screening'):
    """Reuse the existing full R45FD points audit and data construction."""
    points, _, original_summary = prepare_export(rows, review, run_sha256)
    candidates = []
    for p in points:
        index = p['point']
        candidates.append((
            CurrentCandidate(run_sha256, index, SEQUENTIAL_DIODE_DCV_OVER_R,
                             p['screening_current_ma'], 'DIAGNOSTIC_SCREENING',
                             'SEQUENTIAL_NOT_SYNCHRONIZED', 'UNQUALIFIED'),
            CurrentCandidate(run_sha256, index, PSU_READBACK,
                             p['psu_current_ma'], 'PSU_CONTROL_TELEMETRY',
                             'SEQUENTIAL_NOT_SYNCHRONIZED', 'UNQUALIFIED'),
        ))
    selected, selection = select_current_series(
        candidates, source=source, intent=intent,
        independently_audited=(review.get('status') == 'INDEPENDENT_EVIDENCE_REVIEW_PASS'))
    selected_rows = []
    for point, chosen in zip(points, selected):
        selected_rows.append({
            'point': point['point'], 'direction': point['direction'],
            'diode_v': point['diode_v'],
            'selected_current_ma': chosen.current_ma,
            'current_source': chosen.method,
            'psu_control_readback_ma': point['psu_current_ma'],
            'run_sha256': run_sha256,
            'sample_gap_ms': point['sample_gap_ms'],
            'sampling': chosen.sampling, 'metrology_qualification': chosen.qualification,
        })
    segments = []
    for idx in range(1, len(selected_rows)):
        prev, cur = selected_rows[idx-1:idx+1]
        dv = finite(cur['diode_v'], 'diode U') - finite(prev['diode_v'], 'previous diode U')
        di = finite(cur['selected_current_ma'], 'current') - finite(prev['selected_current_ma'], 'previous current')
        if not (dv * di > 0 and abs(di) > 0.001 and abs(dv) > 0.000001):
            raise AuditError('Invalid forward screening segment')
        segments.append({
            'segment': idx, 'from_point': idx-1, 'to_point': idx,
            'direction': 'up' if idx <= 2 else 'down',
            'delta_diode_v': dv, 'delta_selected_current_ma': di,
            'descriptive_incremental_ohm': dv / (di / 1000),
        })
    summary = {
        'status': 'OFFLINE_AUDITED_CURRENT_SELECTION_SCREENING_ONLY',
        'source_test_id': original_summary['source_test_id'],
        'source_sha256': run_sha256,
        'source_provenance_hashes': original_summary['source_provenance_hashes'],
        'hardware_qualification': original_summary['qualification'],
        'hardware_stage_status': 'UNCHANGED',
        'selected_current_source': selection,
        'count_points': len(selected_rows),
        'count_segments': len(segments),
        'metrology_qualification': 'UNQUALIFIED',
        'calibrated_iv_allowed': False,
        'sampling': 'SEQUENTIAL_NOT_SYNCHRONIZED',
        'psu_readback_role': 'CONTROL_AND_SAFETY_TELEMETRY_ONLY',
        'limitations': [
            'No synchronous voltage/current measurements or uncertainty budget.',
            'Resistor is operator-measured, not a calibrated current shunt.',
            'Series-DMM current was only measured in a different, single-point run.',
            'Neither cross-run substitution nor automatic readback bias correction is allowed.',
            'Current choice does not change existing hardware qualification or prove accuracy.',
            'Only five steady-state forward screening points; no reverse/recovery qualification.',
        ],
        'files': ['points.csv','segments.csv','summary.json'],
    }
    return selected_rows, segments, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path, help='Original five-point R45FD JSONL')
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--current-source', default='auto',
                        choices=('auto', 'SEQUENTIAL_PSU_MINUS_DIODE_DCV_OVER_R',
                                 'DIRECT_BALLAST_DCV_OVER_R','DMM_SERIES_DCI','PSU_READBACK'))
    parser.add_argument('--intent', default='diagnostic_screening',
                        choices=('diagnostic_screening','metrology'))
    parser.add_argument('--output-dir', type=Path, help='New directory, exclusive creation')
    args = parser.parse_args(argv)
    try:
        rows, sha = load_original(args.events, args.expected_sha256)
        review = audit(rows)
        points, segments, summary = select_audited_export(
            rows, review, sha, source=args.current_source, intent=args.intent)
        if args.output_dir is not None:
            if args.events.resolve() == args.output_dir.resolve():
                raise AuditError('Output cannot replace original evidence')
            export_files(args.output_dir, {
                'points.csv': csv_text(points, POINT_COLUMNS),
                'segments.csv': csv_text(segments, SEGMENT_COLUMNS),
                'summary.json': json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                           indent=2, allow_nan=False)+'\n',
            })
    except (AuditError, OSError, ValueError, TypeError, KeyError, OverflowError,
            AttributeError, ZeroDivisionError, UnicodeError) as exc:
        print(f'R4.5-F-I REJECTED (HW status unchanged): {type(exc).__name__}: {exc}')
        return 2
    print('R4.5-F-I AUDITED CURRENT SOURCE SELECTED (OFFLINE; SCREENING ONLY)')
    print(json.dumps({
        'status': summary['status'], 'source_sha256': sha,
        'source': summary['selected_current_source']['selection'],
        'count_points': len(points), 'count_segments': len(segments),
        'metrology_qualification': 'UNQUALIFIED',
        'calibrated_iv_allowed': False,
    }, indent=2))
    print('DRY RUN: no export files written' if args.output_dir is None else
          f'Files saved in: {args.output_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
