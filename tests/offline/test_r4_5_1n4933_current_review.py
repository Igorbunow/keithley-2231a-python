"""R45FE current disagreement: offline arithmetic and no new hardware permission."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import r4_5_1n4933_current_review as cr
from test_r4_5_1n4933_export import fixture
from r4_5_1n4933_export import prepare_export

PLAN = Path(__file__).resolve().parents[2] / 'docs/examples/R45FE_1N4933_current_crosscheck_plan.json'


class CurrentReviewTest(unittest.TestCase):
    def setUp(self):
        self.rows, self.review = fixture()
        self.plan = json.loads(PLAN.read_text(encoding='utf-8'))
        self.sha = 'f' * 64
        self.points, self.segments, self.summary = prepare_export(
            self.rows, self.review, self.sha)

    def result(self):
        return cr.review_current(self.points, self.summary, self.sha, self.plan)

    def test_original_measurement_math(self):
        res = self.result()
        self.assertEqual(res['status'], 'OFFLINE_DIAGNOSTIC_PASS_NO_NEW_HW_QUALIFICATION')
        self.assertEqual(res['sample_count'], 5)
        self.assertEqual(len(res['steps']), 4)
        self.assertAlmostEqual(res['residual_mean_ma'], -1.0672585347486483)
        self.assertAlmostEqual(res['residual_span_ma'], .053563456839575)
        self.assertAlmostEqual(res['residual_sample_sd_ma'], .024162237163686)
        self.assertAlmostEqual(res['equivalent_99p86ohm_mean_residual_mv'],
                               -106.576437278998, places=5)

    def test_old_midpoint_projections_not_measurements(self):
        projection = self.result()['future_direct_ballast_dcv_pilot']
        self.assertEqual(projection['status'], 'DESIGN_ONLY_HW_NOT_AUTHORIZED')
        self.assertAlmostEqual(projection['projected_ballast_drop_v_from_old_psu_current'],
                               .00230122 * 99.86)
        self.assertAlmostEqual(projection['projected_ballast_drop_v_from_old_voltage_subtraction'],
                               .899556 - .5646302)
        self.assertTrue(projection['these_are_predictions_not_new_measurements'])

    def test_matched_step_deltas(self):
        steps = self.result()['steps']
        self.assertAlmostEqual(steps[0]['delta_psu_ma'], .84877)
        self.assertAlmostEqual(steps[0]['delta_resistor_estimate_ma'],
                               3.3539535349489293 - 2.5063799319046662)
        self.assertEqual([p['direction'] for p in steps], ['up','up','down','down'])

    def test_time_gaps_preserve_sequential_claim(self):
        res = self.result()
        self.assertEqual(res['current_method'], cr.prepare_export.__globals__['METHOD'])
        for gap in res['sequential_source_minus_dmm_sample_gap_ms']:
            self.assertAlmostEqual(gap, 30., places=7)
        self.assertIn('SEQUENTIAL', res['current_method'])

    def test_plan_hardware_authorization_blocked(self):
        self.plan['hardware_authorized'] = True
        with self.assertRaises(cr.AuditError): self.result()

    def test_plan_execute_blocked(self):
        self.plan['execute_supported'] = True
        with self.assertRaises(cr.AuditError): self.result()

    def test_plan_wrong_meter_role_blocked(self):
        self.plan['dmm_role'] = 'DMM_CURRENT_INPUT'
        with self.assertRaises(cr.AuditError): self.result()

    def test_plan_changed_voltage_blocked(self):
        self.plan['initial_preset_v'] = 5
        with self.assertRaises(cr.AuditError): self.result()

    def test_plan_wrong_wiring_blocked(self):
        self.plan['operator_rewire_only_when_psu_off'] = False
        with self.assertRaises(cr.AuditError): self.result()

    def test_plan_changed_current_limit_blocked(self):
        self.plan['current_limit_a'] = .1
        with self.assertRaises(cr.AuditError): self.result()

    def test_unqualified_status_required(self):
        self.summary['current_accuracy'] = 'CALIBRATED'
        with self.assertRaises(cr.AuditError): self.result()

    def test_wrong_evidence_hash_rejected(self):
        self.summary['source_sha256'] = 'a' * 64
        with self.assertRaises(cr.AuditError): self.result()

    def test_wrong_resistor_rejected(self):
        self.points[0]['ballast_ohm'] = 100.0
        with self.assertRaises(cr.AuditError): self.result()

    def test_tampered_summary_rejected(self):
        self.summary['mean_psu_minus_resistor_current_ma'] += 0.3
        with self.assertRaises(cr.AuditError): self.result()

    def test_rejected_current_out_of_range(self):
        self.points[0]['psu_current_ma'] = 40
        with self.assertRaises(cr.AuditError): self.result()

    def test_cli_uses_existing_audit_export_path_and_no_output_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root/'events.jsonl'; path.write_text('{"event":"start"}\n')
            with patch.object(cr, 'load_original', return_value=(self.rows, self.sha)) as loader, \
                 patch.object(cr, 'audit', return_value=self.review) as audit, \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                code = cr.main([str(path), '--expected-sha256', self.sha,
                                '--plan',str(PLAN)])
            self.assertEqual(code, 0)
            loader.assert_called_once()
            audit.assert_called_once()
            result = json.loads(out.getvalue())
            self.assertEqual(result['status'], 'OFFLINE_DIAGNOSTIC_PASS_NO_NEW_HW_QUALIFICATION')
            self.assertEqual(list(root.iterdir()), [path])

    def test_real_loader_rejects_wrong_sha(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'events.jsonl'; path.write_text('{"event":"start"}\n')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cr.main([str(path), '--expected-sha256','f'*64,
                                          '--plan',str(PLAN)]), 2)

    def test_cli_rejects_plan_with_hw_access(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            plan = dict(self.plan, hardware_authorized=True)
            planfile = root/'plan.json'; planfile.write_text(json.dumps(plan))
            with patch.object(cr, 'load_original') as loader, \
                 contextlib.redirect_stdout(io.StringIO()):
                code=cr.main([str(root/'events.jsonl'), '--expected-sha256', self.sha,
                              '--plan',str(planfile)])
            self.assertEqual(code, 2)
            loader.assert_not_called()

    def test_script_has_no_io_or_power_dispatch(self):
        script=Path(cr.__file__).read_text(encoding='utf-8')
        for dangerous in ('import serial','import socket', 'SerialTransport(', 'TcpTransport(',
                          'CHAN:OUTP', '.output(', 'diagnostic_write(', '--execute'):
            self.assertNotIn(dangerous, script)


if __name__ == '__main__':
    unittest.main()
