"""R4.5-F-D 1N4933 contract: no instrument access, no powered permissions."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
import r4_5_1n4933_plan as dut


class DiodePlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=json.loads((ROOT/'docs/examples/R45FD_1N4933_forward_plan.json').read_text())

    def test_design_only_never_hardware_authorized(self):
        result=dut.analyze(copy.deepcopy(self.plan))
        self.assertEqual(result['status'],'DESIGN_CHECK_PASS_HW_NOT_AUTHORIZED')
        self.assertIs(result['hardware_authorized'],False)
        self.assertIs(result['measurement_roles']['independent_dut_current_sensor'],False)
        self.assertEqual(result['setpoints_v'],[.8,.9,1.,.9,.8])
        self.assertAlmostEqual(result['short_circuit_current_upper_ma'],1000/95)
        self.assertLess(result['short_circuit_ballast_power_upper_w'],.011)

    def test_verified_resistor_still_does_not_authorize_hardware(self):
        p=copy.deepcopy(self.plan)
        p['ballast']['physically_checked_with_power_off']=True
        p['ballast']['measured_ohm']=99.7
        result=dut.analyze(p)
        self.assertFalse(result['hardware_authorized'])
        self.assertEqual(result['ballast_physical_measurement_ohm'],99.7)

    def test_dmm_cannot_be_replaced_by_kel(self):
        p=copy.deepcopy(self.plan)
        p['program']['dmm_connection']='KEL103'
        with self.assertRaises(dut.PlanError): dut.analyze(p)

    def test_no_kEL_or_parallel_branches(self):
        for name in ('kel103_physically_disconnected','other_branches_absent',
                     'ch2_ch3_physically_disconnected','external_power_absent'):
            p=copy.deepcopy(self.plan);p['wiring'][name]=False
            with self.subTest(field=name), self.assertRaises(dut.PlanError):dut.analyze(p)

    def test_wrong_polarity_and_identity_rejected(self):
        for name in ('banded_cathode_on_ch1_minus','anode_after_ballast'):
            p=copy.deepcopy(self.plan);p['wiring'][name]=False
            with self.subTest(name=name),self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['part']='1N4937'
        with self.assertRaises(dut.PlanError):dut.analyze(p)

    def test_no_power_expansion_or_arbitrary_sweep(self):
        cases=[('psu_current_limit_a',.021),('psu_maximum_voltage_v',1.1),
               ('maximum_powered_s',91),('mode','REVERSE_CV')]
        for key,value in cases:
            p=copy.deepcopy(self.plan);p['program'][key]=value
            with self.subTest(key=key),self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['program']['source_voltages_v'][2]=2.0
        with self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['program']['step_directions']=['up']*4
        with self.assertRaises(dut.PlanError):dut.analyze(p)

    def test_ballast_incorrect_or_missing_verification_rejected(self):
        for key,bad in (('nominal_ohm',0),('nominal_ohm',220),
                        ('power_rating_w',.1),('tolerance_percent',10)):
            p=copy.deepcopy(self.plan);p['ballast'][key]=bad
            with self.subTest(key=key),self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['ballast']['measured_ohm']=99.9
        with self.assertRaises(dut.PlanError):dut.analyze(p)
        for measured in (94.99,105.01,False):
            p=copy.deepcopy(self.plan);p['ballast'].update(
                physically_checked_with_power_off=True, measured_ohm=measured)
            with self.subTest(measured=measured),self.assertRaises(dut.PlanError):dut.analyze(p)

    def test_nonfinite_boolean_numbers_and_extra_keys_rejected(self):
        for key,value in (('nominal_ohm',True),('power_rating_w',float('nan'))):
            p=copy.deepcopy(self.plan);p['ballast'][key]=value
            with self.subTest(key=key),self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['extra']=1
        with self.assertRaises(dut.PlanError):dut.analyze(p)
        p=copy.deepcopy(self.plan);p['wiring']['all_outputs_initially_off']=1
        with self.assertRaises(dut.PlanError):dut.analyze(p)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'plan.json';path.write_text('{"x":1,"x":2}')
            with self.assertRaises(dut.PlanError):dut.read_plan(path)

    def test_no_reads_from_instruments_in_cli(self):
        cmd=[sys.executable,str(ROOT/'tools/r4_5_1n4933_plan.py'),
             '--plan',str(ROOT/'docs/examples/R45FD_1N4933_forward_plan.json')]
        p=subprocess.run(cmd, text=True,capture_output=True,timeout=8)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout)
        self.assertFalse(result['hardware_authorized'])
        self.assertNotIn('PASS_POWERED',p.stdout)


if __name__=='__main__': unittest.main()
