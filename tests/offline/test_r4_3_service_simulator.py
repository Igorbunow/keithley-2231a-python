"""R4.3-E: isolated fake-only service gateway; NEVER a hardware qualification."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from keithley2231a.service import PERMISSIONS, ServicePolicyError, ServiceProfile
from keithley2231a.service_simulator import (FakeBench, ServiceSimulator,
                                             verify_simulation_report)

ROOT = Path(__file__).resolve().parents[2]


class ServiceSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / 'service.json'
        self.p.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
                                      'serial': 'SIM-UNIT-0001',
                                      'allowed_operations': sorted(PERMISSIONS)}), encoding='utf-8')
        self.profile = ServiceProfile.load(self.p)
        self.sim = ServiceSimulator(self.profile, FakeBench(self.profile.model, self.profile.serial))

    def perform(self, op, *, sim=None, **kw):
        s = sim or self.sim
        return s.simulate(op, acknowledgement=s.required_ack(op),
                          simulation_ack=s.required_sim_ack(op),
                          critical_ack=s.required_critical_ack(op), **kw)

    def test_identity_cannot_be_mismatched_or_inferred_from_transport(self):
        with self.assertRaises(ServicePolicyError):
            ServiceSimulator(self.profile, FakeBench('2231A-30-3', 'OTHER'))
        with self.assertRaises(ServicePolicyError):
            ServiceSimulator(self.profile, FakeBench('NOT_2231A', self.profile.serial))
        with self.assertRaises(TypeError):
            ServiceSimulator(self.profile, object())

    def test_fixture_refuses_non_off_combined_or_unavailable(self):
        fixture = FakeBench(self.profile.model, self.profile.serial)
        for values in ({'channel_off': (True, False, True)},
                       {'channel_off': (True, 1, True)}, {'channel_off': [True, True, True]},
                       {'topology': 'parallel'}, {'recall_off': False},
                       {'idle': False}, {'link_known': False}):
            with self.subTest(values=values):
                s = ServiceSimulator(self.profile, replace(fixture, **values))
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', sim=s)
                self.assertEqual(s.reports, ())

    def test_no_approval_no_commands_and_no_reports(self):
        for bad in ('', 'YES', self.sim.required_ack('clear_events')[:-1]):
            with self.assertRaises(ServicePolicyError):
                self.sim.simulate('clear_events', acknowledgement=bad,
                                  simulation_ack=self.sim.required_sim_ack('clear_events'))
        self.assertEqual(self.sim.reports, ())

    def test_second_sequence_ack_mandatory_and_one_use(self):
        op = 'clear_events'
        profile_ack = self.sim.required_ack(op)
        first = self.sim.required_sim_ack(op)
        with self.assertRaises(ServicePolicyError):
            self.sim.simulate(op, acknowledgement=profile_ack, simulation_ack='')
        r = self.perform(op)
        self.assertEqual(r.sequence, 1)
        with self.assertRaises(ServicePolicyError):
            self.sim.simulate(op, acknowledgement=profile_ack, simulation_ack=first)
        self.assertEqual(self.perform(op).sequence, 2)

    def test_critical_requires_third_independent_ack(self):
        op = 'reset_settings'
        with self.assertRaises(ServicePolicyError):
            self.sim.simulate(op, acknowledgement=self.sim.required_ack(op),
                              simulation_ack=self.sim.required_sim_ack(op))
        self.assertEqual(self.sim.reports, ())
        self.assertEqual(self.perform(op).status, 'SIMULATED_PASS')

    def test_never_accept_raw_wire_or_caller_created_plan(self):
        with self.assertRaises((TypeError, ServicePolicyError)):
            self.perform('raw_write', command='*RST')
        with self.assertRaises((TypeError, ServicePolicyError)):
            self.perform('clear_events', command='*RST')
        with self.assertRaises((TypeError, ServicePolicyError)):
            self.perform('clear_events', transport=object())
        self.assertFalse(self.sim.reports)

    def test_fake_trace_is_not_provenance_of_hardware(self):
        report = self.perform('self_test')
        self.assertEqual(report.status, 'SIMULATED_PASS')
        self.assertTrue(verify_simulation_report(report))
        self.assertEqual(report.serial, self.profile.serial)
        self.assertEqual(report.profile_sha256, self.profile.source_sha256)
        self.assertIn('SYNTHETIC', report.disclaimer)
        self.assertEqual([e.kind for e in report.events],
                         ['GATE', 'SCPI_NOT_SENT', 'FAKE_REPLY', 'FAKE_ERROR_CHECK', 'FINAL_GATE'])
        self.assertEqual(report.events[1].command, '*TST?')
        self.assertNotIn('0', report.events[2].detail)  # not a vendor self-test result
        self.assertFalse(verify_simulation_report(replace(report, status='HW PASS')))
        self.assertFalse(verify_simulation_report(replace(report, sequence=2)))
        self.assertFalse(verify_simulation_report(replace(report, report_sha256='0'*64)))

    def test_single_fault_permanently_latches_and_never_runs_next_step(self):
        for fault in ('timeout', 'scpi_error', 'output_on'):
            with self.subTest(fault=fault):
                sim = ServiceSimulator(self.profile, FakeBench(self.profile.model, self.profile.serial))
                r = self.perform('calibration_initialize', sim=sim,
                                 inject_fault_step=1, inject_fault=fault)
                self.assertEqual(r.status, 'SIMULATED_ABORTED')
                self.assertTrue(sim.failed)
                self.assertFalse(any(e.kind == 'FAKE_ERROR_CHECK' for e in r.events))
                self.assertTrue(verify_simulation_report(r))
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', sim=sim)
                self.assertEqual(len(sim.reports), 1)

    def test_multi_step_fault_never_emits_next_fake_command(self):
        sim = ServiceSimulator(self.profile, FakeBench(self.profile.model, self.profile.serial))
        self.perform('calibration_unlock', sim=sim, intent='REQUIRES_EXTERNAL_SECRET')
        r = self.perform('calibration_voltage', sim=sim, channel=1,
                         points=['1','2','3','4'], inject_fault_step=4)
        self.assertEqual(r.status, 'SIMULATED_ABORTED')
        sent = [e.command for e in r.events if e.kind == 'SCPI_NOT_SENT']
        self.assertEqual(sent, ['INST:NSEL 1', 'CAL:VOLT:LEV P1',
                                'CAL:VOLT 1', 'CAL:VOLT:LEV P2'])
        self.assertTrue(sim.failed)

    def test_fault_specification_validated_before_first_step(self):
        for fault_step in (0, -1, 2, True, '1'):
            with self.subTest(fault_step=fault_step):
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', inject_fault_step=fault_step)
        with self.assertRaises(ServicePolicyError):
            self.perform('clear_events', inject_fault='unknown')
        self.assertFalse(self.sim.reports)

    def test_calibration_points_require_unlock_and_cannot_repeat(self):
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_voltage', channel=1, points=['1','2','3','4'])
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.perform('calibration_voltage', channel=1, points=['1','2','3','4'])
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_voltage', channel=1, points=['1','2','3','4'])
        self.assertEqual(len(self.sim.reports), 2)

    def test_calibration_save_needs_both_full_point_sequences(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')
        voltage = self.perform('calibration_voltage', channel=2, points=['1','2','3','4'])
        self.assertEqual(len([e for e in voltage.events if e.kind == 'SCPI_NOT_SENT']), 9)
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')
        current = self.perform('calibration_current', channel=2, points=['1','2'])
        self.assertEqual(len([e for e in current.events if e.kind == 'SCPI_NOT_SENT']), 5)
        self.assertEqual(self.perform('calibration_save').status, 'SIMULATED_PASS')
        self.assertEqual(self.perform('calibration_lock').status, 'SIMULATED_PASS')
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')

    def test_calibration_lock_still_works_after_unsaved_adjustment_but_no_pass(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.perform('calibration_voltage', channel=1, points=['1', '2', '3', '4'])
        lock = self.perform('calibration_lock')
        self.assertEqual(lock.status, 'SIMULATED_ABORTED')
        self.assertIn('CAL:SEC ON', [e.command for e in lock.events
                                      if e.kind == 'SCPI_NOT_SENT'])
        self.assertTrue(any(e.kind == 'INCOMPLETE_CAL' for e in lock.events))
        self.assertTrue(self.sim.failed)
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')

    def test_partial_second_channel_rejects_calibration_save(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        for op, ch, pts in [('calibration_voltage', 1, ['1','2','3','4']),
                            ('calibration_current', 1, ['1','2']),
                            ('calibration_voltage', 3, ['1','2','3','4'])]:
            self.perform(op, channel=ch, points=pts)
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')
        self.perform('calibration_current', channel=3, points=['1','2'])
        self.assertEqual(self.perform('calibration_save').status, 'SIMULATED_PASS')

    def test_no_noncalibration_operations_when_unlocked(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        for op, kwargs in [('reset_settings', {}), ('wait_pending', {}),
                           ('clear_events', {}), ('calibration_initialize', {})]:
            with self.subTest(op=op):
                with self.assertRaises(ServicePolicyError):
                    self.perform(op, **kwargs)
        self.perform('calibration_lock')
        self.assertEqual(self.perform('wait_pending').status, 'SIMULATED_PASS')

    def test_initialize_independent_critical_high_risk(self):
        report = self.perform('calibration_initialize')
        self.assertEqual(report.status, 'SIMULATED_PASS')
        self.assertEqual([e.command for e in report.events if e.kind == 'SCPI_NOT_SENT'], ['CAL:INIT'])
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.assertTrue(verify_simulation_report(report))

    def test_permissions_frozen_to_profile(self):
        self.p.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
                                      'serial': 'SIM-UNIT-0001',
                                      'allowed_operations': ['self_test']}))
        sim = ServiceSimulator(ServiceProfile.load(self.p), FakeBench('2231A-30-3', 'SIM-UNIT-0001'))
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_initialize', sim=sim)
        self.assertFalse(sim.reports)

    def test_ack_bound_to_profile_source_sha(self):
        old_ack = self.sim.required_ack('clear_events')
        old_sim = self.sim.required_sim_ack('clear_events')
        self.p.write_bytes(self.p.read_bytes() + b' ')
        profile2 = ServiceProfile.load(self.p)
        sim2 = ServiceSimulator(profile2, FakeBench(profile2.model, profile2.serial))
        with self.assertRaises(ServicePolicyError):
            sim2.simulate('clear_events', acknowledgement=old_ack,
                          simulation_ack=old_sim)
        self.assertFalse(sim2.reports)

    def test_every_permission_has_a_typed_synthetic_path(self):
        sample_params = {
            'save_setup': {'slot': 0}, 'recall_setup': {'slot': 30},
            'power_on_status_write': {'state': 0},
            'apply_channel': {'channel': 1, 'voltage': '3.30', 'current': '0.1'},
            'calibration_unlock': {'intent': 'REQUIRES_EXTERNAL_SECRET'},
            'calibration_voltage': {'channel': 1, 'points': ['1','2','3','4']},
            'calibration_current': {'channel': 1, 'points': ['0.1','0.2']},
            'calibration_label': {'label': 'REF2026'},
        }
        for operation in sorted(PERMISSIONS):
            with self.subTest(operation=operation):
                sim = ServiceSimulator(self.profile, FakeBench(self.profile.model,
                                                                 self.profile.serial))
                if operation in ('calibration_voltage', 'calibration_current',
                                 'calibration_label', 'calibration_lock', 'calibration_save'):
                    self.perform('calibration_unlock', sim=sim,
                                 intent='REQUIRES_EXTERNAL_SECRET')
                if operation in ('calibration_save', 'calibration_lock'):
                    self.perform('calibration_voltage', sim=sim,
                                 channel=1, points=['1','2','3','4'])
                    self.perform('calibration_current', sim=sim,
                                 channel=1, points=['0.1','0.2'])
                    self.perform('calibration_save', sim=sim)
                    if operation == 'calibration_save':
                        # The action was already recorded in this session.
                        report = sim.reports[-1]
                        self.assertEqual(report.operation, operation)
                        continue
                report = self.perform(operation, sim=sim, **sample_params.get(operation, {}))
                self.assertEqual(report.status, 'SIMULATED_PASS')
                self.assertTrue(verify_simulation_report(report))
                self.assertTrue(any(e.kind == 'SCPI_NOT_SENT' for e in report.events))

    def test_simulation_cli_rejects_duplicate_json_arguments(self):
        cli = ROOT/'tools/service_simulate_offline.py'
        args = [sys.executable, str(cli), '--service-profile', str(self.p),
                '--operation', 'self_test', '--params-json', '{"slot":0,"slot":1}']
        p = subprocess.run(args, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('duplicate simulation parameter', p.stderr)

    def test_no_hardware_import_or_transport_argument(self):
        src = (ROOT/'src/keithley2231a/service_simulator.py').read_text()
        self.assertNotIn('from .transport', src)
        self.assertNotIn('from .device', src)
        self.assertNotIn('import serial', src)
        self.assertNotIn('import socket', src)
        self.assertNotIn('def execute(', src)
        self.assertNotIn('Transport(', src)
        import inspect
        self.assertNotIn('transport', inspect.signature(ServiceSimulator).parameters)
        self.assertNotIn('transport', inspect.signature(ServiceSimulator.simulate).parameters)

    def test_cli_only_fake_and_rejects_live_flags(self):
        cli = ROOT/'tools/service_simulate_offline.py'
        args = [sys.executable, str(cli), '--service-profile', str(self.p),
                '--operation', 'self_test']
        req = subprocess.run(args + ['--show-required-acks'], capture_output=True, text=True)
        self.assertEqual(req.returncode, 0, req.stderr)
        ack, sim_ack = req.stdout.strip().splitlines()
        run = subprocess.run(args + ['--ack', ack, '--sim-ack', sim_ack], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('SIMULATED_PASS', run.stdout)
        self.assertIn('SCPI_NOT_SENT: *TST?', run.stdout)
        self.assertIn('SYNTHETIC ONLY', run.stdout)
        bad = subprocess.run(args + ['--execute'], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('unrecognized arguments', bad.stderr)
        for prohibited in ('--config', '--port', '--connect'):
            bad = subprocess.run(args + [prohibited, '/dev/ttyUSB1'], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)


if __name__ == '__main__':
    unittest.main()
