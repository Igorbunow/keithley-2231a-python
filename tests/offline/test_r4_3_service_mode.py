"""Service policy and typed plans: *zero* hardware transports involved."""
from __future__ import annotations
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from keithley2231a.service import (ServiceProfile, ServicePlanner,
                                  ServicePolicyError, PERMISSIONS)
from keithley2231a.profile import Profile, ProfileError
from keithley2231a import Keithley2231A, SafetyError
from test_driver import FakeTransport

ROOT = Path(__file__).resolve().parents[2]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'service.json'
        self.data = dict(mode='service', model='2231A-30-3', serial='LAB-UNIT-001',
                         allowed_operations=sorted(PERMISSIONS))
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data), encoding='utf8')

    def planner(self):
        return ServicePlanner(ServiceProfile.load(self.path))

    def plan(self, op, **kwargs):
        p = self.planner()
        return p.plan(op, acknowledgement=p.required_ack(op), **kwargs)

    def test_default_plan_cannot_execute_or_get_transport(self):
        plan = self.plan('self_test')
        self.assertEqual(plan.mode, 'SERVICE_REVIEW')
        self.assertEqual([s.command for s in plan.steps], ['*TST?'])
        self.assertTrue(plan.warning)
        for name in ('execute', 'connect', 'transport', 'send', 'write', 'query'):
            self.assertFalse(hasattr(plan, name), name)

    def test_ordinary_profile_rejects_service_schema(self):
        with self.assertRaises((ProfileError, KeyError)):
            Profile.from_json(self.path)

    def test_bad_modes_models_serial_are_rejected(self):
        for key, val in [('mode','control'),('model','2231A'),('serial',''),
                         ('serial','REPLACE_WITH_ACTUAL_SERIAL'),('serial',7),
                         ('serial','UNIT\n1')]:
            with self.subTest(key=key, val=val):
                old=self.data[key]; self.data[key]=val; self.save()
                with self.assertRaises(ServicePolicyError): ServiceProfile.load(self.path)
                self.data[key]=old

    def test_unknown_keys_and_missing_fields_rejected(self):
        self.data['allow_all'] = True; self.save()
        with self.assertRaises(ServicePolicyError): ServiceProfile.load(self.path)
        del self.data['allow_all']; del self.data['serial']; self.save()
        with self.assertRaises(ServicePolicyError): ServiceProfile.load(self.path)

    def test_duplicate_json_keys_rejected(self):
        self.path.write_text('{"mode":"service","mode":"service",'
                             '"model":"2231A-30-3","serial":"LAB-UNIT-001",'
                             '"allowed_operations":["self_test"]}')
        with self.assertRaises(ServicePolicyError): ServiceProfile.load(self.path)

    def test_permissions_require_exact_nonempty_unique_list(self):
        for value in ([], ['*'], ['self_test', 'self_test'], ['self_test', 'unknown'],
                      'self_test', [1], [True]):
            with self.subTest(value=value):
                self.data['allowed_operations'] = value; self.save()
                with self.assertRaises(ServicePolicyError): ServiceProfile.load(self.path)

    def test_no_permission_means_no_plan(self):
        self.data['allowed_operations']=['self_test']; self.save()
        p=self.planner()
        with self.assertRaises(ServicePolicyError):
            p.plan('calibration_initialize', acknowledgement=p.required_ack('calibration_initialize'))

    def test_ack_mandatory_and_bound_to_profile_bytes(self):
        p=self.planner()
        for ack in ('', 'yes', 'SERVICE', p.required_ack('reset_settings').lower(),
                    p.required_ack('self_test')):
            with self.subTest(ack=ack):
                with self.assertRaises(ServicePolicyError):
                    p.plan('reset_settings', acknowledgement=ack)
        self.data['allowed_operations'].remove('wait_pending'); self.save()
        changed=self.planner()
        with self.assertRaises(ServicePolicyError):
            changed.plan('reset_settings', acknowledgement=p.required_ack('reset_settings'))

    def test_static_commands_all_mapped_and_no_arg_acceptance(self):
        expected = {'wait_pending':'*WAI','self_test':'*TST?','clear_events':'*CLS',
                    'reset_settings':'*RST','reset_status':'STAT:PRES','trigger_bus':'*TRG',
                    'calibration_save':'CAL:SAV','calibration_initialize':'CAL:INIT',
                    'calibration_lock':'CAL:SEC ON'}
        for op,cmd in expected.items():
            with self.subTest(op=op):
                self.assertEqual([s.command for s in self.plan(op).steps],[cmd])
                with self.assertRaises(ServicePolicyError): self.plan(op, ignored=1)

    def test_calibration_init_independent_privilege(self):
        self.data['allowed_operations'] = ['calibration_voltage', 'calibration_current', 'calibration_save']
        self.save()
        with self.assertRaises(ServicePolicyError):
            self.planner().plan('calibration_initialize',
                 acknowledgement=self.planner().required_ack('calibration_initialize'))

    def test_setup_slots_bounded(self):
        for op,prefix in [('save_setup','*SAV'),('recall_setup','*RCL')]:
            for slot in (0,30):
                self.assertEqual(self.plan(op,slot=slot).steps[0].command,f'{prefix} {slot}')
            for bad in (-1,31,True,1.0,'1',None):
                with self.subTest(op=op,bad=bad):
                    with self.assertRaises(ServicePolicyError): self.plan(op,slot=bad)

    def test_apply_never_rounds_or_accepts_shortcuts(self):
        self.assertEqual(self.plan('apply_channel', channel=3, voltage='5.00', current='0.100').steps[0].command,
                         'APPL CH3,5.00,0.100')
        for params in (dict(channel=4,voltage='1',current='1'),
                       dict(channel=1,voltage='30.01',current='1'),
                       dict(channel=3,voltage='5.01',current='1'),
                       dict(channel=1,voltage='1',current='3.1'),
                       dict(channel=1,voltage='1e2',current='1'),
                       dict(channel=1,voltage=True,current='1'),
                       dict(channel=1,voltage=1.2,current='1'),
                       dict(channel=1,voltage='1;*RST',current='1'),
                       dict(channel=True,voltage='1',current='1'),
                       dict(channel=1,voltage='1',current='NaN')):
            with self.subTest(params=params):
                with self.assertRaises(ServicePolicyError):
                    self.plan('apply_channel', **params)

    def test_calibration_voltage_four_points_ordered(self):
        p=self.plan('calibration_voltage', channel=1, points=['0.1','10','20','30.0002'])
        self.assertEqual([s.command for s in p.steps],
            ['INST:NSEL 1','CAL:VOLT:LEV P1','CAL:VOLT 0.1',
             'CAL:VOLT:LEV P2','CAL:VOLT 10',
             'CAL:VOLT:LEV P3','CAL:VOLT 20',
             'CAL:VOLT:LEV P4','CAL:VOLT 30.0002'])
        self.assertTrue(all(s.risk=='critical' for s in p.steps))

    def test_calibration_current_two_points_ordered(self):
        p=self.plan('calibration_current', channel=3, points=['0.100','3.0002'])
        self.assertEqual([s.command for s in p.steps],
            ['INST:NSEL 3','CAL:CURR:LEV P1','CAL:CURR 0.100',
             'CAL:CURR:LEV P2','CAL:CURR 3.0002'])

    def test_calibration_rejects_missing_measurements_or_bad_channel(self):
        for op,points in [('calibration_voltage',['1','2','3']),
                          ('calibration_current',['1','2','3'])]:
            with self.assertRaises(ServicePolicyError): self.plan(op, channel=1, points=points)
        for ch in (0,4,'1',True):
            with self.assertRaises(ServicePolicyError):
                self.plan('calibration_current',channel=ch,points=['1','2'])
        with self.assertRaises(ServicePolicyError):
            self.plan('calibration_voltage',channel=3,points=['0','1','2','30'])
        with self.assertRaises(ServicePolicyError):
            self.plan('calibration_voltage',channel=1,points=['0','1',Decimal('NaN'),'30'])

    def test_calibration_secret_never_inserted_in_plan(self):
        with self.assertRaises(ServicePolicyError):
            self.plan('calibration_unlock',password='2231')
        plan=self.plan('calibration_unlock',intent='REQUIRES_EXTERNAL_SECRET')
        self.assertIn('<PASSWORD_',plan.steps[0].command)
        self.assertNotIn('2231',plan.steps[0].command)

    def test_calibration_string_escaping(self):
        self.assertEqual(self.plan('calibration_label',label='CAL 2026').steps[0].command,
                         'CAL:STR "CAL 2026"')
        for label in ('a'*23,'"bad"','bad;*RST','line\n','',1):
            with self.subTest(label=label):
                with self.assertRaises(ServicePolicyError): self.plan('calibration_label',label=label)

    def test_psc_is_flagged_unverified(self):
        p=self.plan('power_on_status_write',state=0)
        self.assertIn('NOT VERIFIED',p.steps[0].warning)
        for bad in (2,'0',True,1.0):
            with self.assertRaises(ServicePolicyError):
                self.plan('power_on_status_write',state=bad)

    def test_regular_driver_debug_bypass_blocks_service_writes_before_remote_or_idn(self):
        blocked = ('*RST','*CLS','*WAI','*OPC','*PSC 0','*TRG','*SAV 0','*RCL 1',
                   'STAT:PRES','CAL:SEC OFF','CALIBRATION:SAVE','CAL:VOLT:LEV P1',
                   'CAL:CURR:LEV P1','APPL CH1,3,1','SOUR:APPL CH2,4,1',
                   'VOLT:TRIG 10','CURR:STEP 0.1','TRIG:SOUR BUS')
        for cmd in blocked:
            with self.subTest(cmd=cmd):
                wire=FakeTransport()
                dev=Keithley2231A(wire,debug=True,debug_bypass_profile=True)
                with self.assertRaises(SafetyError): dev.diagnostic_write(cmd)
                self.assertEqual(wire.cmds, [])

    def test_regular_driver_debug_bypass_blocks_self_test_query(self):
        wire=FakeTransport(); dev=Keithley2231A(wire,debug=True,debug_bypass_profile=True)
        with self.assertRaises(SafetyError): dev.diagnostic_query('*TST?')
        self.assertEqual(wire.cmds, [])

    def test_cli_no_execute_option_and_no_transport_import(self):
        p=subprocess.run([sys.executable,str(ROOT/'tools/service_plan_offline.py'),
                          '--service-profile',str(self.path),'--operation','self_test',
                          '--execute'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        self.assertIn('unrecognized arguments',p.stderr)
        src=(ROOT/'src/keithley2231a/service.py').read_text()
        self.assertNotIn('from .transport import',src)
        self.assertNotIn('from .device import',src)
        self.assertNotIn('def execute(',src)

    def test_cli_success_is_offline_preview(self):
        p=self.planner()
        out=subprocess.run([sys.executable,str(ROOT/'tools/service_plan_offline.py'),
                          '--service-profile',str(self.path),'--operation','self_test',
                          '--ack',p.required_ack('self_test')],capture_output=True,text=True)
        self.assertEqual(out.returncode,0,out.stderr)
        self.assertIn('SERVICE_REVIEW',out.stdout)
        self.assertIn('*TST?',out.stdout)

    def test_provenance_changes_on_profile_byte_change(self):
        a=self.planner().profile.source_sha256
        self.path.write_bytes(self.path.read_bytes()+b' ')
        self.assertNotEqual(self.planner().profile.source_sha256,a)


if __name__=='__main__': unittest.main()
