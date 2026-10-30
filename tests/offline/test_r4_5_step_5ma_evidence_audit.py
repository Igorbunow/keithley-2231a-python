"""R4.5-D CH1 5mA current OFF-only evidence: PTY + fail-closed audit tests."""
from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from support.virtual_uart import UARTPeer, open_test_transport

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests/hardware'))
from qualification import Evidence
from r4_5_step_mutation_stage import run_step_mutation
from tools.audit_r4_5_step_mutation_evidence import audit, AuditError


@unittest.skipUnless(os.name == 'posix', 'PTY required')
class R45DCurrentFiveMilliampsEvidenceUART(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / 'current_005.jsonl'
        cls.peer = UARTPeer(profile_registers=True)
        transport = open_test_transport(cls.peer, timeout=.20)
        evidence = Evidence(cls.path, 'R45STEPMUT')
        evidence.add('start', variant='STEP_MUTATION_OFF', channel=1,
                     kind='current', increment=.005, config_sha256='a'*64,
                     software={'sha': '86711a0' + '0'*33, 'dirty': False,
                               'source_sha256': '1'*64,
                               'r45d_runner_sha256': '2'*64,
                               'r45d_stage_sha256': '3'*64})
        evidence.add('pre_open_approvals', gates=['BENCH', 'INITIAL'],
                     approved_before_evidence_open=True,
                     config_sha256='a'*64)

        def final_gate(_):
            evidence.add('operator_confirmation', gate='FINAL_PHYSICAL')

        with contextlib.redirect_stdout(io.StringIO()):
            cls.result = run_step_mutation(
                transport, evidence, channel=1, kind='current', increment=.005,
                profile_gate=lambda *args: None,
                mutation_gate=lambda *args: None,
                final_gate=final_gate)
        cls.peer.await_command('SYST:LOC')
        cls.records = [json.loads(line) for line in cls.path.read_text().splitlines()]

    @classmethod
    def tearDownClass(cls):
        cls.peer.close()
        cls.temp.cleanup()

    def mutate(self, fn):
        rows = deepcopy(self.records)
        fn(rows)
        return rows

    def rejected(self, fn):
        with self.assertRaises(AuditError):
            audit(self.mutate(fn))

    @staticmethod
    def set_firmware(rows, value):
        next(x for x in rows if x['event'] == 'psu_identity')['firmware'] = value
        next(x for x in rows if x['event'] == 'outcome')['firmware_observations'] = [value]
        for row in rows:
            if row['event'] == 'read' and row.get('command') == '*IDN?':
                row['response'] = row['response'].rsplit(', ', 1)[0] + ', ' + value
            if row['event'] == 'driver_trace' and row['trace'].get('event') == 'reply' and row['trace'].get('command') == '*IDN?':
                trace = row['trace']
                trace['response'] = trace['response'].rsplit(', ', 1)[0] + ', ' + value

    def test_real_5ma_shape_is_accepted(self):
        self.assertEqual(self.result, 'PASS')
        report = audit(self.records)
        self.assertEqual(report['setpoints'], [.105, .110, .105, .100])
        self.assertEqual((report['kind'], report['step']), ('current', .005))
        self.assertEqual(report['firmware_observation'], '1.04-1.04')

    def test_observed_104_001_identity_accepted_only_when_all_layers_agree(self):
        report = audit(self.mutate(lambda rows: self.set_firmware(rows, '1.04-0.01')))
        self.assertEqual(report['firmware_observation'], '1.04-0.01')

    def test_unobserved_firmware_rejected(self):
        self.rejected(lambda rows: self.set_firmware(rows, '1.04-0.02'))

    def test_firmware_outcome_mismatch_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='outcome').update(
            firmware_observations=['1.04-0.01']))

    def test_idn_wire_identity_mismatch_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='driver_trace'
            and x['trace']['event']=='reply' and x['trace']['command']=='*IDN?')['trace'].update(
                response='Keithley instruments, 2231A-30-3, [REDACTED], 1.04-0.01'))

    def test_step_register_write_missing_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='driver_trace'
            and x['trace']['event']=='write' and x['trace']['command']=='CURR:STEP 0.005')['trace'].update(
                command='CURR:STEP 0.010'))

    def test_wrong_increment_readback_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='read' and
            x['command']=='CURR:STEP?').update(response='0.010'))

    def test_wrong_midpoint_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='driver_trace' and
            x['trace']['event']=='reply' and x['trace']['command']=='CURR?' and
            abs(float(x['trace']['response'])-0.110)<0.000001)['trace'].update(response='0.109'))

    def test_enabled_output_rejected(self):
        self.rejected(lambda rows: next(x for x in rows if x['event']=='driver_trace' and
            x['trace']['event']=='reply' and x['trace']['command']=='CHAN:OUTP?')['trace'].update(response='1'))

    def test_missing_error_poll_rejected(self):
        self.rejected(lambda rows: rows.remove(next(x for x in rows if
            x['event']=='driver_trace' and x['trace']['event']=='query' and
            x['trace']['command']=='SYST:ERR?')))

    def test_missing_physical_gate_rejected(self):
        self.rejected(lambda rows: rows.__setitem__(slice(None), [x for x in rows if not
            (x['event']=='operator_confirmation' and x['gate']=='FINAL_PHYSICAL')]))


if __name__ == '__main__':
    unittest.main()
