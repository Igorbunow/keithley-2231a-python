"""Verify six original powered STEP JSONL logs against the reviewed R5-D3 index.

Offline only: never opens VISA, USB, serial ports or instruments.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from audit_r5_d2_step_powered import read_and_audit

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / 'docs/reviews/R5_D3_STEP_HW_EVIDENCE_INDEX_2026-10-31.json'
)


def verify_matrix(manifest: dict, paths: list[Path]) -> dict:
    """Fail on missing, duplicate, substituted or inconsistent original evidence."""
    if manifest.get('schema_version') != 1 or manifest.get('kind') != 'bounded_reviewed_step_evidence_index':
        raise ValueError('Unknown R5-D3 evidence manifest')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or len(cases) != 6:
        raise ValueError('Expected six reviewed cases')
    expected = {(ch, mode) for ch in (1, 2, 3) for mode in ('CV', 'CC')}
    labels = [(c.get('channel'), c.get('mode')) for c in cases]
    if set(labels) != expected or len(set(labels)) != 6:
        raise ValueError('Duplicate or absent reviewed channel/mode')
    if len(paths) != 6:
        raise ValueError('Provide exactly six original JSONL files')
    claimed = {c['original_sha256']: c for c in cases}
    if len(claimed) != 6:
        raise ValueError('Nonunique original SHA-256')
    verified = []
    seen = set()
    for path in paths:
        review = read_and_audit(path)
        digest = review['original_sha256']
        case = claimed.get(digest)
        if case is None or digest in seen:
            raise ValueError('Unknown/duplicate original evidence: ' + path.name)
        seen.add(digest)
        if (review['channel'], review['mode'].upper()) != (case['channel'], case['mode']):
            raise ValueError('Manifest channel/mode mismatch')
        if (review['snapshots'], review['mutations'], review['scpi_queries']) != (
            case['snapshots'], case['mutations'], case['scpi_queries']
        ):
            raise ValueError('Manifest evidence count mismatch')
        raw = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        start = raw[0]
        if (start['software'] != manifest['software'] or
                start['config_sha256'] != manifest['provenance']['config_sha256'] or
                len(raw) != case['events']):
            raise ValueError('Changed software/config/evidence count')
        snapshots = [r for r in raw if r['event'] == 'powered_step_snapshot']
        for name, field in (('dmm_voltage_v', 'dmm_v'),
                            ('kel_current_a', 'kel_a'),
                            ('psu_current_a', 'psu_a')):
            observed = [s[field] for s in snapshots]
            if len(observed) != 5 or any(
                not math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
                for a, b in zip(observed, case[name])
            ):
                raise ValueError('Manifest analog data mismatch: ' + name)
        verified.append({'channel': case['channel'], 'mode': case['mode'],
                         'sha256': digest, 'status': 'RAW_REAUDITED_MATCH'})
    return {'status': 'SIX_ORIGINALS_REAUDITED_MATCH_BOUNDED_REVIEW',
            'not_full_range_or_general_api_qualification': True,
            'cases': sorted(verified, key=lambda x: (x['mode'], x['channel']))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('original_jsonl', type=Path, nargs='+')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        report = verify_matrix(manifest, args.original_jsonl)
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'REJECTED / NOT QUALIFIED: {type(exc).__name__}: {exc}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
