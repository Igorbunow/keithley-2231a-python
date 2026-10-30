"""R4.5-F-C offline fail-closed DUT topology contract tests."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'))
from r4_5_dut_topology_review import (PlanError, read_plan, review, main,
                                     TOPOLOGY, SCHEMA, MEASUREMENTS)


def valid_plan():
    return {
        'schema': SCHEMA,
        'topology': TOPOLOGY,
        'dut': {
            'kind': 'PASSIVE_NON_ENERGY_STORING_TWO_TERMINAL',
            'identifier': 'bench-known-passive-resistor-250ohm',
            'rated_forward_voltage_v': 1.0,
            'rated_forward_current_a': .02,
            'rated_power_w': .1,
            'externally_powered': False,
            'polarity_marked': True,
        },
        'measurements': dict(MEASUREMENTS),
        'ballast': {
            'minimum_verified_ohm': 200,
            'rated_power_w': .125,
            'wiring': 'SERIES_BETWEEN_PSU_CH1_PLUS_AND_DUT_PLUS',
        },
        'limits': {
            'start_voltage_v': .3,
            'step_voltage_v': .1,
            'maximum_voltage_v': .5,
            'psu_current_limit_a': .005,
            'maximum_dut_power_w': .0025,
            'maximum_powered_s': 20,
        },
    }


class OfflineTopologyReviewTests(unittest.TestCase):
    def assert_rejected(self, plan):
        with self.assertRaises(PlanError): review(plan)

    def test_pass_is_only_a_design_result(self):
        result = review(valid_plan(), source_sha256='a'*64)
        self.assertEqual(result['status'], 'DESIGN_CHECK_PASS_POWERED_NOT_AUTHORIZED')
        self.assertFalse(result['hardware_authorized'])
        self.assertFalse(result['independent_current_measurement'])
        self.assertEqual(result['proposed_voltages_v'], [.3,.4,.5,.4,.3])
        self.assertEqual(result['directions'], ['UP','UP','DOWN','DOWN'])
        self.assertEqual(result['data_qualification'], 'TOPOLOGY_SCREENING_NOT_METROLOGY')
        self.assertEqual(result['plan_sha256'], 'a'*64)

    def test_valid_plan_file_cli_has_no_artifacts_or_device_access(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'plan.json'; path.write_text(json.dumps(valid_plan()))
            out=io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(['--plan',str(path)]), 0)
            parsed=json.loads(out.getvalue())
            self.assertEqual(parsed['hardware_authorized'], False)
            self.assertEqual([x.name for x in Path(td).iterdir()], ['plan.json'])

    def test_invalid_file_returns_2_without_creating_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'plan.json'; path.write_text('{"wrong": true}')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--plan',str(path)]), 2)
            self.assertEqual([x.name for x in Path(td).iterdir()], ['plan.json'])

    def test_all_incorrect_topologies_and_meter_routes_rejected(self):
        variations = [
            ('topology', 'PSU_DUT_KEL_PARALLEL'),
            ('topology', 'PSU_DUT_DMM_CURRENT_INLINE'),
            ('schema', 'R45FC_DUT_SCREENING_PLAN_V2'),
        ]
        for field, value in variations:
            with self.subTest(field=field,value=value):
                p=valid_plan(); p[field]=value; self.assert_rejected(p)
        for key, value in (
            ('dut_voltage', 'KEITHLEY_MEAS_VOLT'),
            ('current', 'KEL103_MEAS_CURR'),
            ('kel103', 'ON_PARALLEL'),
            ('current_independent', True),
            ('current_independent', 0),
            ('additional_parallel_loads', True),
            ('additional_parallel_loads', 0),
        ):
            with self.subTest(key=key,value=value):
                p=valid_plan(); p['measurements'][key]=value; self.assert_rejected(p)

    def test_dut_kinds_and_unverified_ratings_rejected(self):
        mutations = {
            'kind': ['ACTIVE', 'DIODE_UNCHARACTERIZED'],
            'identifier': ['TBD-123', '', ' EXAMPLE', 17],
            'externally_powered': [True, 0],
            'polarity_marked': [False, 1],
            'rated_forward_voltage_v': [.49, 0, None, True, float('nan')],
            'rated_forward_current_a': [.004, -1, '0.01'],
            'rated_power_w': [.002, 0, float('inf')],
        }
        for field, bad_values in mutations.items():
            for bad in bad_values:
                with self.subTest(field=field,bad=bad):
                    p=valid_plan(); p['dut'][field]=bad; self.assert_rejected(p)

    def test_ballast_understrength_or_wrong_wiring_rejected(self):
        for key,bad in (
            ('minimum_verified_ohm',0),('minimum_verified_ohm',50),
            ('minimum_verified_ohm',True),('minimum_verified_ohm',100001),
            ('rated_power_w',.001), ('rated_power_w',float('inf')),
            ('wiring','PARALLEL'),('wiring',None),
        ):
            with self.subTest(key=key,bad=bad):
                p=valid_plan(); p['ballast'][key]=bad; self.assert_rejected(p)

    def test_voltage_current_power_and_time_guards(self):
        cases = [
            ('start_voltage_v',0),('start_voltage_v',.9),
            ('step_voltage_v',.001), ('step_voltage_v',.11),
            ('maximum_voltage_v',1.1), ('maximum_voltage_v',.6),
            ('psu_current_limit_a',.03),('psu_current_limit_a',.006),
            ('maximum_dut_power_w',.001), ('maximum_dut_power_w',.021),
            ('maximum_powered_s',31),('maximum_powered_s',4),
        ]
        for key,bad in cases:
            with self.subTest(key=key,bad=bad):
                p=valid_plan(); p['limits'][key]=bad; self.assert_rejected(p)

    def test_unknown_fields_and_missing_fields_rejected(self):
        for key in ('measurements','dut','ballast','limits'):
            p=valid_plan(); p[key]['new_setting']='unsupported'; self.assert_rejected(p)
            p=valid_plan(); p[key].pop(next(iter(p[key]))); self.assert_rejected(p)
        p=valid_plan(); p['operator_override']=True; self.assert_rejected(p)
        p=valid_plan(); del p['ballast']; self.assert_rejected(p)

    def test_nonobject_and_nonfinite_rejected(self):
        for bad in (None, [], 'string', 4, True):
            self.assert_rejected(bad)
        for key in ('measurements','dut','ballast','limits'):
            p=valid_plan(); p[key]=[]; self.assert_rejected(p)

    def test_invalid_json_duplicate_key_nonstandard_numeric_and_large(self):
        for payload in (
            b'{"a":1,"a":2}',
            b'{"x": NaN}',
            b'{"x": Infinity}',
            b'{"x": -Infinity}',
            b'\xff',
            b'',
            b' ' * 12001,
        ):
            with self.subTest(payload=payload[:20]):
                with tempfile.TemporaryDirectory() as td:
                    file=Path(td)/'p.json'; file.write_bytes(payload)
                    with self.assertRaises(PlanError): read_plan(file)

    def test_example_intentionally_fails_closed(self):
        path=ROOT/'docs/examples/R45FC_DUT_plan.example.json'
        obj,_=read_plan(path)
        self.assert_rejected(obj)

    def test_bounds_pass_at_envelope_edges_but_not_hardware(self):
        p=valid_plan()
        p['limits'].update(start_voltage_v=.8, step_voltage_v=.1, maximum_voltage_v=1.,
                           psu_current_limit_a=.01, maximum_dut_power_w=.01,
                           maximum_powered_s=30)
        p['dut'].update(rated_forward_voltage_v=1.,rated_forward_current_a=.01,
                        rated_power_w=.01)
        p['ballast'].update(minimum_verified_ohm=100,rated_power_w=.05)
        result=review(p)
        self.assertEqual(result['maximum_psu_v'],1.)
        self.assertFalse(result['hardware_authorized'])


if __name__=='__main__':
    unittest.main()
