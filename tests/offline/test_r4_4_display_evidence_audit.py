"""Fail-closed evidence checks; entirely synthetic, no equipment access."""
from __future__ import annotations

import hashlib
import json
import unittest

from tools.audit_r4_4_display_evidence import audit, EvidenceAuditError


def fixture():
    rows = []
    clock = 1.0

    def add(event, **fields):
        nonlocal clock
        clock += .01
        rows.append({'utc': '2026-10-29T00:00:00+00:00',
                     'monotonic_s': clock, 'test_id': 'R44DISPLAY',
                     'event': event, **fields})

    identity = 'Keithley instruments, 2231A-30-3, [REDACTED], 1.04-1.04'

    def query(command, response):
        add('query', command=command)
        add('driver_trace', trace={'event': 'query', 'command': command})
        add('driver_trace', trace={'event': 'reply', 'command': command, 'response': response})
        add('read', command=command, response=response)
        if command != 'SYST:ERR?':
            query('SYST:ERR?', '0, "No error"')

    def write(command):
        add('write', command=command)
        add('driver_trace', trace={'event': 'write', 'command': command})
        if command != 'SYST:LOC':
            query('SYST:ERR?', '0, "No error"')

    values = {'display_enabled': True, 'display_query_count': 1}
    digest = hashlib.sha256(json.dumps(values, sort_keys=True,
                separators=(',', ':')).encode('ascii')).hexdigest()
    add('start', variant='DISPLAY_OFF_QUERY_ONLY', config_sha256='a'*64,
        software={'sha': 'test-sha', 'dirty': False})
    add('pre_open_approvals', approved_before_evidence_open=True,
        gates=['BENCH', 'INITIAL'], config_sha256='a'*64)
    query('*IDN?', identity)
    add('psu_identity', command='*IDN?', response=identity)
    write('SYST:REM')
    query('OUTP:SER?', '0')
    for ch in (1,2,3):
        write(f'INST:NSEL {ch}')
        query('INST:NSEL?', str(ch))
        query('CHAN:OUTP?', '0')
    write('INST:NSEL 1')
    query('OUTP:SER?', '0')
    query('OUTP:PARALLEL?', '0')
    query('OUTP:TRAC?', '0')
    query('DISP?', '1')
    query('OUTP:SER?', '0')
    for ch in (1,2,3):
        write(f'INST:NSEL {ch}')
        query('INST:NSEL?', str(ch))
        query('CHAN:OUTP?', '0')
    write('INST:NSEL 1')
    query('*IDN?', identity)
    write('SYST:LOC')
    add('results', values=values, results_sha256=digest)
    add('operator_confirmation', gate='FINAL')
    add('outcome', outcome='PASS', phase='DISPLAY_OFF_QUERY_ONLY',
        physical_off_confirmed=True, link_known=True,
        firmware_observations=['1.04-1.04'])
    return rows


class EvidenceAuditTests(unittest.TestCase):
    def test_valid_synthetic_evidence(self):
        result = audit(fixture())
        self.assertEqual(result['status'], 'REVIEWABLE_PASS')
        self.assertEqual(result['output_checks'], 6)

    def test_unauthorized_output_write_is_rejected(self):
        records = fixture()
        write = next(r for r in records if r['event'] == 'write')
        write['command'] = 'OUTP 1'
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_hidden_nonzero_instrument_error_is_rejected(self):
        records = fixture()
        for record in records:
            if record['event'] == 'driver_trace' and record['trace']['event'] == 'reply' and record['trace']['command'] == 'SYST:ERR?':
                record['trace']['response'] = '120, "Error"'
                break
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_missing_last_off_pass_rejected(self):
        records = fixture()
        last = [r for r in records if r['event'] == 'read' and r.get('command') == 'CHAN:OUTP?'][-1]
        records.remove(last)
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_bad_final_confirmation_rejected(self):
        records = fixture()
        next(r for r in records if r['event'] == 'operator_confirmation')['gate'] = 'INITIAL'
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_forged_results_hash_rejected(self):
        records = fixture()
        next(r for r in records if r['event'] == 'results')['results_sha256'] = '0'*64
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_enabled_channel_in_second_sweep_is_rejected(self):
        records = fixture()
        matching = [r for r in records if r['event'] == 'driver_trace'
                    and r['trace']['event'] == 'reply'
                    and r['trace']['command'] == 'CHAN:OUTP?']
        matching[-1]['trace']['response'] = '1'
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_duplicate_display_query_is_rejected(self):
        records = fixture()
        matching = [r for r in records if r['event'] == 'driver_trace'
                    and r['trace']['event'] == 'reply'
                    and r['trace']['command'] == 'DISP?']
        matching[0]['trace']['command'] = 'CHAN:OUTP?'
        with self.assertRaises(EvidenceAuditError):
            audit(records)

    def test_missing_local_rejected(self):
        records = fixture()
        records = [r for r in records if not (r.get('command') == 'SYST:LOC' or r.get('trace', {}).get('command') == 'SYST:LOC')]
        with self.assertRaises(EvidenceAuditError):
            audit(records)


if __name__ == '__main__':
    unittest.main()
