"""Recheck three original R5-E4 OFF-only hardware JSONL; NO instrument I/O.

The fixed reviewed hashes avoid accidental mixing with different runs. A pass
qualifies only trigger-register write/read/restore, not *TRG or coupling.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from tools.audit_r5_e4_trigger_write import audit_path

INDEX = Path(__file__).resolve().parents[1] / "docs/reviews/R5_E4_TRIGGER_WRITE_HW_EVIDENCE_INDEX_2026-10-31.json"
STATUS = 'REVIEWED_CH123_OFF_ONLY_NOT_TRIGGER_ACTUATION'

class MatrixAuditError(ValueError):
    pass

def validate(reports, index):
    def need(condition, message):
        if not condition: raise MatrixAuditError(message)
    need(len(reports)==3, 'Exactly three reports required')
    expected={r['channel']:r for r in index['channels']}
    need(set(expected)=={1,2,3}, 'Index must have all three channels')
    seen=set()
    for report in reports:
        ch=report['channel']; need(ch in expected and ch not in seen, 'Duplicate/missing channel')
        seen.add(ch); entry=expected[ch]
        need(report['status']=='REVIEWABLE_PASS_OFF_ONLY_NOT_TRIGGER_ACTUATION', 'Not a reviewed OFF-only result')
        need(report['original_sha256']==entry['original_jsonl_sha256'], 'Original evidence SHA-256 mismatch')
        need(report['source_sha256']==index['source_sha256'], 'Source SHA-256 drift')
        need(report['firmware']==index['firmware'], 'Firmware variant drift')
        need(report['digital_off_confirmed'] is True and report['physical_off_confirmed'] is True,
             'Unconfirmed OFF')
        need(report['scpi_queries']==entry['queries'] and report['off_readbacks']==entry['off_readbacks'],
             'SCPI evidence counts differ')
        for field,prefix in [('staged','preset'),('original','original'),('restored','restored')]:
            actual=report[field]
            for property,unit in [('voltage_v','voltage_v'),('current_a','current_a')]:
                wanted=entry[f'{prefix}_{unit}']
                need(type(actual[property]) in (float,int) and abs(actual[property]-wanted)<.00049,
                     f'Incorrect CH{ch} {field} {property}')
    need(seen=={1,2,3}, 'Missing channel')
    need(index['actuation']=='NOT_RUN' and index['analog']=='NOT_RUN' and
         index['trigger_coupling']=='NOT_QUALIFIED', 'Index overclaims trigger evidence')
    return {'status':STATUS,'channels':[1,2,3],'individual_results':3,
            'actuation':'NOT_RUN','coupling':'NOT_QUALIFIED','analog':'NOT_RUN',
            'source_sha256':index['source_sha256'],
            'jsonl_sha256_by_channel':{str(ch):expected[ch]['original_jsonl_sha256'] for ch in (1,2,3)}}

def audit_three(paths, index_path=INDEX):
    paths=list(paths)
    if len(paths)!=3: raise MatrixAuditError('Exactly three JSONL paths required')
    if len({str(Path(p).resolve()) for p in paths})!=3:
        raise MatrixAuditError('Duplicate JSONL paths')
    index=json.loads(Path(index_path).read_text(encoding='utf-8'))
    reports=[audit_path(Path(p)) for p in paths]
    return validate(reports,index)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jsonl',nargs=3, type=Path)
    args=parser.parse_args(argv)
    try: report=audit_three(args.jsonl)
    except (MatrixAuditError,OSError,ValueError,KeyError,TypeError) as exc:
        print(f'R5-E5 MATRIX REJECTED: {exc}')
        return 2
    print(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
