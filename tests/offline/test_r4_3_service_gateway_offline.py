"""Offline-only fake service execution boundary, never real hardware."""
from __future__ import annotations

from dataclasses import replace
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from keithley2231a.service import PERMISSIONS, ServicePolicyError, ServiceProfile
from keithley2231a.service_gateway_offline import (OfflineServiceGateway,
                                                     verify_gateway_report)
from keithley2231a.service_simulator import FakeBench


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'service.json'
        self.path.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
                'serial': 'FAKE-0001', 'allowed_operations': sorted(PERMISSIONS)}))
        self.profile = ServiceProfile.load(self.path)
        self.gateway = OfflineServiceGateway(self.profile,
                                             FakeBench(self.profile.model, self.profile.serial))

    def perform(self, op: str, *, gateway=None, **kwargs):
        g = self.gateway if gateway is None else gateway
        return g.simulate(op, acknowledgement=g.required_ack(op),
                          simulation_ack=g.required_sim_ack(op),
                          critical_ack=g.required_critical_ack(op), **kwargs)

    def test_simple_synthetic_write_has_error_poll_and_no_real_claim(self):
        r = self.perform('clear_events')
        self.assertEqual(r.status, 'SIMULATED_PASS')
        self.assertTrue(verify_gateway_report(r))
        self.assertEqual([(e.kind, e.command) for e in r.events],
            [('FAKE_PREFLIGHT', ''), ('FAKE_WRITE', '*CLS'),
             ('FAKE_ERROR_POLL', 'SYST:ERR?'), ('FAKE_FINAL_OFF', '')])
        self.assertIn('NO SCPI TRANSMITTED', r.warning)
        self.assertEqual(r.sequence, 1)

    def test_query_returns_only_synthetic_text_not_device_measurement(self):
        r = self.perform('self_test')
        kinds = [e.kind for e in r.events]
        self.assertEqual(kinds, ['FAKE_PREFLIGHT', 'FAKE_QUERY', 'FAKE_REPLY',
                                 'FAKE_ERROR_POLL', 'FAKE_FINAL_OFF'])
        self.assertEqual(r.events[1].command, '*TST?')
        self.assertIn('SYNTHETIC_ONLY', r.events[2].detail)
        self.assertNotIn('Keithley instruments', r.events[2].detail)

    def test_no_raw_transport_connection_or_caller_plan(self):
        for x in ({'transport': object()}, {'port': '/dev/ttyUSB1'},
                  {'command': '*RST'}, {'driver': object()}, {'socket': object()}):
            with self.subTest(x=list(x)):
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', **x)
        self.assertFalse(self.gateway.reports)
        with self.assertRaises(TypeError):
            OfflineServiceGateway(self.profile, object())
        with self.assertRaises(TypeError):
            OfflineServiceGateway(self.profile, FakeBench(self.profile.model,
                                      self.profile.serial), transport=object())

    def test_normal_profile_does_not_grant_service_and_identity_is_bound(self):
        with self.assertRaises(TypeError):
            OfflineServiceGateway(object(), FakeBench('2231A-30-3', 'FAKE-0001'))
        with self.assertRaises(ServicePolicyError):
            OfflineServiceGateway(self.profile, FakeBench(self.profile.model, 'OTHER'))
        self.path.write_text(json.dumps({'mode': 'normal', 'model': '2231A-30-3',
                        'serial': 'FAKE-0001', 'allowed_operations': ['clear_events']}))
        with self.assertRaises(ServicePolicyError):
            ServiceProfile.load(self.path)

    def test_all_approval_layers_enforced_before_fake_io(self):
        op = 'reset_settings'
        ack = self.gateway.required_ack(op)
        sim_ack = self.gateway.required_sim_ack(op)
        for overrides in ({'acknowledgement': ''}, {'simulation_ack': ''},
                          {'critical_ack': ''}):
            params = {'acknowledgement': ack, 'simulation_ack': sim_ack,
                      'critical_ack': self.gateway.required_critical_ack(op)}
            params.update(overrides)
            with self.assertRaises(ServicePolicyError):
                self.gateway.simulate(op, **params)
        self.assertEqual(self.gateway.reports, ())
        self.assertEqual(self.perform(op).status, 'SIMULATED_PASS')

    def test_denied_permission_stays_denied_in_gateway(self):
        self.path.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
                            'serial': 'FAKE-0001', 'allowed_operations': ['self_test']}))
        p = ServiceProfile.load(self.path)
        g = OfflineServiceGateway(p, FakeBench(p.model, p.serial))
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_initialize', gateway=g)
        self.assertFalse(g.reports)

    def test_fake_off_precondition_checked_before_dispatch(self):
        for fault_fixture in (FakeBench(self.profile.model, self.profile.serial,
                                         channel_off=(True, False, True)),
                              FakeBench(self.profile.model, self.profile.serial,
                                         topology='parallel'),
                              FakeBench(self.profile.model, self.profile.serial,
                                         idle=False),
                              FakeBench(self.profile.model, self.profile.serial,
                                         link_known=False),
                              FakeBench(self.profile.model, self.profile.serial,
                                         recall_off=False)):
            with self.subTest(fixture=fault_fixture):
                g = OfflineServiceGateway(self.profile, fault_fixture)
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', gateway=g)
                self.assertEqual(g.reports, ())

    def test_fault_send_blocks_all_following_fake_operations(self):
        g = self.gateway
        r = self.perform('clear_events', inject_fault_step=1)
        self.assertEqual(r.status, 'SIMULATED_ABORTED')
        self.assertNotIn('FAKE_WRITE', [e.kind for e in r.events])
        self.assertFalse(any(e.kind == 'FAKE_ERROR_POLL' for e in r.events))
        self.assertTrue(g.failed)
        with self.assertRaises(ServicePolicyError):
            self.perform('self_test')
        self.assertEqual(len(g.reports), 1)

    def test_query_reply_fault_does_not_poll_errors_or_repeat(self):
        r = self.perform('self_test', inject_fault_step=1, inject_fault_phase='reply')
        self.assertEqual(r.status, 'SIMULATED_ABORTED')
        self.assertEqual([e.kind for e in r.events],
                         ['FAKE_PREFLIGHT', 'FAKE_QUERY', 'INJECTED_FAULT'])
        with self.assertRaises(ServicePolicyError):
            self.perform('self_test')

    def test_error_poll_fault_fails_closed_without_next_step(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        r = self.perform('calibration_voltage', channel=1, points=['1','2','3','4'],
                         inject_fault_step=3, inject_fault_phase='error_poll')
        self.assertEqual(r.status, 'SIMULATED_ABORTED')
        self.assertEqual(len([e for e in r.events if e.kind == 'FAKE_WRITE']), 3)
        self.assertEqual(len([e for e in r.events if e.kind == 'FAKE_ERROR_POLL']), 2)
        self.assertEqual([e.command for e in r.events if e.kind == 'FAKE_WRITE'][-1],
                         'CAL:VOLT 1')
        self.assertTrue(self.gateway.failed)

    def test_output_on_fault_stops_even_without_scpi_error(self):
        r = self.perform('clear_events', inject_fault_step=1, inject_fault_phase='output_on')
        self.assertEqual(r.status, 'SIMULATED_ABORTED')
        self.assertNotIn('FAKE_FINAL_OFF', [e.kind for e in r.events])
        self.assertTrue(self.gateway.failed)

    def test_fault_spec_rejected_without_consuming_approval(self):
        for spec in ({'inject_fault_step': 0}, {'inject_fault_step': True},
                     {'inject_fault_step': 100}, {'inject_fault_step': 1,
                                                'inject_fault_phase': 'reply'},
                     {'inject_fault_phase': 'invalid'}):
            with self.subTest(spec=spec):
                with self.assertRaises(ServicePolicyError):
                    self.perform('clear_events', **spec)
        self.assertEqual(self.gateway.reports, ())
        self.assertEqual(self.perform('clear_events').sequence, 1)

    def test_one_simulation_sequence_number_and_profile_hash(self):
        r1 = self.perform('clear_events')
        r2 = self.perform('wait_pending')
        self.assertEqual((r1.sequence, r2.sequence), (1, 2))
        self.assertEqual(r1.profile_sha256, self.profile.source_sha256)
        self.assertNotEqual(r1.simulator_report_sha256, r1.report_sha256)
        self.assertTrue(verify_gateway_report(r1))
        self.assertTrue(verify_gateway_report(r2))
        self.assertNotEqual(r1.report_sha256, r2.report_sha256)

    def test_report_tamper_is_caught_but_hash_is_not_authentication(self):
        r = self.perform('clear_events')
        self.assertFalse(verify_gateway_report(replace(r, status='HW PASS')))
        self.assertFalse(verify_gateway_report(replace(r, sequence=2)))
        self.assertFalse(verify_gateway_report(replace(r, events=())))
        self.assertFalse(verify_gateway_report(replace(r, report_sha256='0'*64)))

    def test_calibration_unlock_save_lock_synthetic_state(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.perform('calibration_voltage', channel=2, points=['1','2','3','4'])
        with self.assertRaises(ServicePolicyError):
            self.perform('calibration_save')
        self.perform('calibration_current', channel=2, points=['0.1','0.2'])
        self.assertEqual(self.perform('calibration_save').status, 'SIMULATED_PASS')
        lock = self.perform('calibration_lock')
        self.assertEqual(lock.status, 'SIMULATED_PASS')
        self.assertEqual([e.command for e in lock.events if e.kind == 'FAKE_WRITE'],
                         ['CAL:SEC ON'])

    def test_incomplete_calibration_can_fake_lock_but_not_pass(self):
        self.perform('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.perform('calibration_voltage', channel=1, points=['1','2','3','4'])
        lock = self.perform('calibration_lock')
        self.assertEqual(lock.status, 'SIMULATED_ABORTED')
        self.assertIn('CAL:SEC ON', [e.command for e in lock.events if e.kind == 'FAKE_WRITE'])
        self.assertIn('INCOMPLETE_CAL', [e.kind for e in lock.events])
        self.assertTrue(self.gateway.failed)

    def test_all_service_operations_are_synthetic_not_really_executed(self):
        basic = {'save_setup': {'slot': 0}, 'recall_setup': {'slot': 30},
                 'power_on_status_write': {'state': 1},
                 'apply_channel': {'channel': 1, 'voltage': '1', 'current': '0.1'},
                 'calibration_unlock': {'intent': 'REQUIRES_EXTERNAL_SECRET'},
                 'calibration_label': {'label': 'TEST2026'}}
        for operation in sorted(PERMISSIONS):
            with self.subTest(operation=operation):
                g = OfflineServiceGateway(self.profile,
                                          FakeBench(self.profile.model, self.profile.serial))
                if operation in ('calibration_voltage', 'calibration_current',
                                 'calibration_save', 'calibration_lock', 'calibration_label'):
                    self.perform('calibration_unlock', gateway=g,
                                 intent='REQUIRES_EXTERNAL_SECRET')
                    if operation in ('calibration_save',):
                        self.perform('calibration_voltage', gateway=g, channel=1,
                                     points=['1','2','3','4'])
                        self.perform('calibration_current', gateway=g, channel=1,
                                     points=['0.1','0.2'])
                params = dict(basic.get(operation, {}))
                if operation in ('calibration_voltage', 'calibration_current'):
                    params = {'channel': 1, 'points': ['1','2','3','4'] if
                              operation == 'calibration_voltage' else ['0.1','0.2']}
                r = self.perform(operation, gateway=g, **params)
                self.assertTrue(verify_gateway_report(r))
                self.assertFalse(any(e.kind.startswith('HW_') for e in r.events))
                self.assertTrue(all('FAKE_' in e.kind or e.kind == 'INCOMPLETE_CAL'
                                    for e in r.events))

    def test_cli_offline_ack_flow_and_simulated_result(self):
        cli = Path(__file__).resolve().parents[2] / 'tools' / 'service_gateway_offline.py'
        base = [sys.executable, str(cli), '--service-profile', str(self.path),
                '--operation', 'clear_events']
        show = subprocess.run(base + ['--show-required-acks'], capture_output=True,
                              text=True, check=False, timeout=10)
        self.assertEqual(show.returncode, 0, show.stderr)
        acks = show.stdout.strip().splitlines()
        self.assertEqual(len(acks), 2)
        run = subprocess.run(base + ['--ack', acks[0], '--sim-ack', acks[1]],
                             capture_output=True, text=True, check=False, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('SIMULATED_PASS', run.stdout)
        self.assertIn('FAKE_WRITE: *CLS', run.stdout)
        self.assertIn('NO SCPI TRANSMITTED', run.stdout)

    def test_cli_refuses_live_flags_and_duplicate_json_parameters(self):
        cli = Path(__file__).resolve().parents[2] / 'tools' / 'service_gateway_offline.py'
        base = [sys.executable, str(cli), '--service-profile', str(self.path),
                '--operation', 'apply_channel']
        for bad in (['--execute'], ['--port', '/dev/ttyUSB1'],
                    ['--config', 'bench.local.toml']):
            r = subprocess.run(base + bad, capture_output=True, text=True,
                               check=False, timeout=10)
            self.assertEqual(r.returncode, 2)
        r = subprocess.run(base + ['--params-json', '{"slot":1,"slot":2}'],
                           capture_output=True, text=True, check=False, timeout=10)
        self.assertEqual(r.returncode, 2)
        self.assertIn('duplicate parameters', r.stderr)

    def test_code_has_no_transport_or_hardware_imports(self):
        source = (Path(__file__).resolve().parents[2] / 'src' / 'keithley2231a' /
                  'service_gateway_offline.py').read_text()
        for forbidden in ('import serial', 'import socket', 'from .device',
                          'from .transport', 'subprocess', 'serial_for_url',
                          'os.open', 'open_port', 'connect(', 'execute('):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
