"""Revalidate a pinned R5-E7-HW original JSONL against the reviewed index.

Read-only. Does not open devices, infer coupling or authorize hardware actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_r5_e7_neutral_trigger import AuditError, audit_path
INDEX = ROOT / 'docs/reviews/R5_E7_NEUTRAL_TRIGGER_HW_EVIDENCE_INDEX_2026-10-31.json'


def audit_review(path: Path, *, index: Path = INDEX) -> dict:
    record = json.loads(index.read_text(encoding='utf-8'))
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != record['original_sha256']:
        raise AuditError('Original JSONL SHA-256 does not match hardware review')
    result = audit_path(path)
    if result['status'] != record['status']:
        raise AuditError('Hardware audit status diverged from reviewed scope')
    for key, expected in (
        ('source_sha256',record['source_sha256']),
        ('evidence_sha256',record['original_sha256']),
        ('trigger_count',record['trigger_count']),
        ('records',record['record_count']),
        ('queries',record['wire_query_count']),
        ('original',record['original_presets']),
        ('neutral',record['neutral_presets']),
    ):
        if result.get(key) != expected:
            raise AuditError('Reviewed evidence mismatch: '+key)
    first = json.loads(original.splitlines()[0]); software = first['software']
    for key, want in (
        ('sha',record['git_head_at_run']),
        ('r5e7_runner_sha256',record['runner_sha256']),
        ('r5e7_stage_sha256',record['stage_sha256']),
    ):
        if software.get(key) != want:
            raise AuditError('Source provenance mismatch: '+key)
    if first.get('config_sha256') != record['config_sha256']:
        raise AuditError('Bench provenance mismatch')
    if software.get('dirty') is not record['working_tree_dirty_at_run']:
        raise AuditError('Git dirty state mismatch')
    # Index totals are immutable assertions about the original physical trace.
    events = [json.loads(line) for line in original.splitlines()]
    if (sum(e.get('event') == 'write' for e in events) != record['wire_write_count'] or
            sum(e.get('event') == 'read' and e.get('command') == 'SYST:ERR?'
                for e in events) != record['scpi_error_query_count'] or
            sum(e.get('event') == 'read' and e.get('command') == 'CHAN:OUTP?'
                for e in events) != record['off_readback_count']):
        raise AuditError('Reviewed SCPI command counts mismatch')
    return {'status':result['status'], 'stage':record['stage'],
            'evidence_sha256':result['evidence_sha256'],
            'channels':result['channels'], 'trigger_count':result['trigger_count'],
            'off_readbacks':record['off_readback_count'],
            'scope':'NEUTRAL_OFF_ONLY_NOT_ACTUATION'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    args=parser.parse_args(argv)
    try:
        outcome=audit_review(args.events)
    except (AuditError, OSError, ValueError, KeyError, TypeError) as exc:
        print('R5-E8 AUDIT REJECTED: '+str(exc))
        return 2
    print(json.dumps(outcome,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
