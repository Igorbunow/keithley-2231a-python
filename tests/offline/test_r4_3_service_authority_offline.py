"""Fail-closed offline service-session authority model. Never attach a device."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from keithley2231a.service import PERMISSIONS, ServicePolicyError
from keithley2231a.service_authority_offline import (
    OfflineServiceAuthority, OfflineServicePermit, AuthorizedFakeReport)
from keithley2231a.service_simulator import FakeBench


class OfflineAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'service.json'
        self.path.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
                              'serial': 'FAKE-0001', 'allowed_operations': sorted(PERMISSIONS)}))
        self.fixture = FakeBench('2231A-30-3', 'FAKE-0001')
        self.auth = OfflineServiceAuthority(self.path, self.fixture)

    def open(self, auth=None):
        a = auth or self.auth
        a.open_service(session_ack=a.required_session_ack())
        return a

    def prepare(self, op='clear_events', *, auth=None, **kwargs):
        a = auth or self.auth
        return a.prepare(op, acknowledgement=a.required_ack(op), **kwargs)

    def perform(self, permit, *, auth=None, **fault):
        a = auth or self.auth
        return a.simulate(permit, action_ack=a.required_action_ack(permit),
                          acknowledgement=a.required_ack(permit.operation),
                          simulation_ack=a.required_sim_ack(permit.operation),
                          critical_ack=a.required_critical_ack(permit.operation), **fault)

    def test_normal_ownership_excludes_service_and_vice_versa(self):
        self.auth.claim_normal()
        with self.assertRaises(ServicePolicyError): self.open()
        self.auth.release_normal()
        self.open()
        with self.assertRaises(ServicePolicyError): self.auth.claim_normal()
        self.assertEqual(self.auth.owner, 'service')
        self.auth.close_service()
        with self.assertRaises(ServicePolicyError): self.open()
        with self.assertRaises(ServicePolicyError): self.auth.claim_normal()

    def test_denies_without_session_approval(self):
        with self.assertRaises(ServicePolicyError): self.auth.open_service(session_ack='')
        with self.assertRaises(ServicePolicyError): self.prepare()
        self.assertFalse(self.auth.reports)

    def test_fake_report_contains_plan_and_gateway_evidence(self):
        self.open()
        p = self.prepare()
        r = self.perform(p)
        self.assertIsInstance(r, AuthorizedFakeReport)
        self.assertEqual(r.result.status, 'SIMULATED_PASS')
        self.assertIn('FAKE_WRITE', [e.kind for e in r.result.events])
        self.assertEqual(r.result.events[1].command, '*CLS')
        self.assertEqual(len(r.permit_sha256), 64)
        self.auth.close_service()
        self.assertEqual(self.auth.owner, 'closed')

    def test_approvals_are_bound_to_exact_plan_and_sequence(self):
        self.open()
        p = self.prepare('apply_channel', channel=1, voltage='2', current='0.1')
        self.assertEqual(len(p.plan_sha256), 64)
        self.assertIn(p.plan_sha256, self.auth.required_action_ack(p))
        self.assertNotEqual(p.plan_sha256, self.auth.required_action_ack(
            replace(p, plan_sha256='0'*64)).split('PLAN=')[-1])
        with self.assertRaises(ServicePolicyError): self.prepare('clear_events')
        self.assertEqual(self.perform(p).result.status, 'SIMULATED_PASS')
        p2 = self.prepare('apply_channel', channel=1, voltage='2.01', current='0.1')
        self.assertNotEqual(p.plan_sha256, p2.plan_sha256)
        self.assertEqual(p2.sequence, 2)
        self.assertEqual(self.perform(p2).result.sequence, 2)

    def test_refuse_wrong_action_ack_is_terminal_before_fake_wire(self):
        self.open()
        p = self.prepare()
        with self.assertRaises(ServicePolicyError):
            self.auth.simulate(p, action_ack='not approved',
                acknowledgement=self.auth.required_ack(p.operation),
                simulation_ack=self.auth.required_sim_ack(p.operation))
        self.assertTrue(self.auth.terminal)
        self.assertEqual(self.auth.owner, 'unknown')
        self.assertEqual(self.auth.reports, ())
        with self.assertRaises(ServicePolicyError): self.perform(p)

    def test_profile_change_between_issue_and_use_aborts(self):
        self.open()
        p = self.prepare()
        self.path.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
           'serial': 'FAKE-0001', 'allowed_operations': ['clear_events']}))
        with self.assertRaisesRegex(ServicePolicyError, 'profile changed'):
            self.perform(p)
        self.assertEqual(self.auth.reports, ())
        self.assertTrue(self.auth.terminal)

    def test_profile_remove_between_issue_and_use_aborts(self):
        self.open()
        p = self.prepare()
        self.path.unlink()
        with self.assertRaisesRegex(ServicePolicyError, 'unreadable'):
            self.perform(p)
        self.assertTrue(self.auth.terminal)

    def test_mutated_fake_fixture_aborts_even_if_bools_remain_true(self):
        self.open()
        p = self.prepare()
        object.__setattr__(self.fixture, 'topology', 'parallel')
        with self.assertRaisesRegex(ServicePolicyError, 'fixture changed'):
            self.perform(p)
        self.assertEqual(self.auth.reports, ())

    def test_every_bad_fixture_state_blocks_open_before_fake_io(self):
        for f in (replace(self.fixture, channel_off=(True, False, True)),
                  replace(self.fixture, topology='parallel'),
                  replace(self.fixture, idle=False),
                  replace(self.fixture, link_known=False),
                  replace(self.fixture, recall_off=False),
                  replace(self.fixture, channel_off=(1, True, True))):
            with self.subTest(f=f):
                a = OfflineServiceAuthority(self.path, f)
                with self.assertRaises(ServicePolicyError): self.open(a)
                self.assertEqual(a.reports, ())

    def test_fake_error_latches_terminal_and_no_retry(self):
        self.open()
        p = self.prepare('self_test')
        r = self.perform(p, inject_fault_step=1, inject_fault_phase='reply')
        self.assertEqual(r.result.status, 'SIMULATED_ABORTED')
        self.assertEqual(self.auth.owner, 'unknown')
        self.assertTrue(self.auth.terminal)
        with self.assertRaises(ServicePolicyError): self.perform(p)
        with self.assertRaises(ServicePolicyError): self.prepare()
        self.assertEqual(len(self.auth.reports), 1)

    def test_unexpected_fake_dispatch_exception_still_latches_unknown(self):
        self.open()
        p = self.prepare()
        class BrokenFakeGateway:
            failed = False
            def __init__(self, original): self._original = original
            @property
            def reports(self): return self._original.reports
            def required_sim_ack(self, op): return self._original.required_sim_ack(op)
            def required_critical_ack(self, op): return self._original.required_critical_ack(op)
            def simulate(self, *args, **kwargs):
                raise RuntimeError('synthetic unexpected gateway error')
        # Precompute acknowledgements before substituting the broken fake.
        sim_ack = self.auth.required_sim_ack(p.operation)
        critical_ack = self.auth.required_critical_ack(p.operation)
        self.auth._gateway = BrokenFakeGateway(self.auth._gateway)
        with self.assertRaisesRegex(ServicePolicyError, 'RuntimeError'):
            self.auth.simulate(p, action_ack=self.auth.required_action_ack(p),
                               acknowledgement=self.auth.required_ack(p.operation),
                               simulation_ack=sim_ack, critical_ack=critical_ack)
        self.assertTrue(self.auth.terminal)
        self.assertEqual(self.auth.owner, 'unknown')

    def test_calibration_unlock_does_not_release_ordinary_session(self):
        self.open()
        unlock = self.prepare('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.assertEqual(self.perform(unlock).result.status, 'SIMULATED_PASS')
        with self.assertRaisesRegex(ServicePolicyError, 'unlocked'):
            self.auth.close_service()
        with self.assertRaises(ServicePolicyError): self.auth.claim_normal()
        lock = self.prepare('calibration_lock')
        outcome = self.perform(lock)
        self.assertEqual(outcome.result.status, 'SIMULATED_PASS')
        self.auth.close_service()

    def test_incomplete_calibration_lock_latches_unknown(self):
        self.open()
        p = self.prepare('calibration_unlock', intent='REQUIRES_EXTERNAL_SECRET')
        self.perform(p)
        p = self.prepare('calibration_voltage', channel=1, points=['0', '1', '2', '3'])
        self.perform(p)
        p = self.prepare('calibration_lock')
        self.assertEqual(self.perform(p).result.status, 'SIMULATED_ABORTED')
        self.assertTrue(self.auth.terminal)
        with self.assertRaises(ServicePolicyError): self.auth.close_service()

    def test_permit_foreign_or_reused_is_rejected(self):
        self.open()
        p = self.prepare()
        with self.assertRaises(ServicePolicyError): self.perform(replace(p))
        self.perform(p)
        with self.assertRaises(ServicePolicyError): self.perform(p)
        self.assertFalse(self.auth.terminal)
        self.assertEqual(len(self.auth.reports), 1)

    def test_unsupported_transport_and_raw_scpi_are_rejected(self):
        self.open()
        for args in ({'port': '/dev/ttyUSB1'}, {'transport': object()},
                     {'command': '*RST'}, {'driver': object()}, {'socket': object()}):
            with self.subTest(args=args):
                with self.assertRaises(ServicePolicyError):
                    self.prepare('clear_events', **args)
        self.assertEqual(self.auth.reports, ())
        with self.assertRaises(TypeError):
            OfflineServiceAuthority(self.path, object())
        with self.assertRaises(TypeError):
            OfflineServiceAuthority(self.path, self.fixture, transport=object())

    def test_normal_profile_and_wrong_serial_refused(self):
        self.path.write_text(json.dumps({'mode': 'normal', 'model': '2231A-30-3',
                  'serial': 'FAKE-0001', 'allowed_operations': ['clear_events']}))
        with self.assertRaises(ServicePolicyError):
            OfflineServiceAuthority(self.path, self.fixture)
        with self.assertRaises(ServicePolicyError):
            OfflineServiceAuthority(self.path, FakeBench('2231A-30-3', 'FAKE-OTHER'))

    def test_permit_is_frozen_and_cannot_be_mutated_via_normal_assignment(self):
        self.open()
        p = self.prepare()
        self.assertIsInstance(p, OfflineServicePermit)
        with self.assertRaises(FrozenInstanceError): p.plan_sha256 = '0'*64
        self.assertEqual(self.perform(p).result.status, 'SIMULATED_PASS')

    def test_cli_preview_and_one_offline_simulation(self):
        tool = Path(__file__).resolve().parents[2] / 'tools/service_authority_offline.py'
        cmd = [sys.executable, str(tool), '--service-profile', str(self.path),
               '--operation', 'clear_events']
        preview = subprocess.run(cmd + ['--preview-acks'], capture_output=True,
                                 text=True, timeout=10)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        lines = preview.stdout.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn('PREVIEW ONLY', lines[-1])
        result = subprocess.run(cmd + ['--session-ack', lines[0], '--ack', lines[1],
                              '--action-ack', lines[2], '--sim-ack', lines[3]],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SIMULATED_PASS', result.stdout)
        self.assertIn('FAKE_WRITE: *CLS', result.stdout)
        self.assertIn('NOT PHYSICAL EVIDENCE', result.stdout)

    def test_cli_refuses_real_io_options_and_duplicate_args(self):
        tool = Path(__file__).resolve().parents[2] / 'tools/service_authority_offline.py'
        common = [sys.executable, str(tool), '--service-profile', str(self.path),
                  '--operation', 'clear_events']
        for extra in (['--execute'], ['--port', '/dev/ttyUSB1'],
                      ['--config', 'bench.toml']):
            with self.subTest(extra=extra):
                p = subprocess.run(common + extra, capture_output=True,
                                   text=True, timeout=10)
                self.assertNotEqual(p.returncode, 0)
        p = subprocess.run(common + ['--params-json', '{"slot":1,"slot":2}',
                                    '--preview-acks'], capture_output=True,
                           text=True, timeout=10)
        self.assertEqual(p.returncode, 2)
        self.assertIn('duplicate', p.stderr)

    def test_profile_permission_revocation_is_terminal(self):
        self.open()
        permit = self.prepare('reset_settings')
        self.path.write_text(json.dumps({'mode': 'service', 'model': '2231A-30-3',
            'serial': 'FAKE-0001', 'allowed_operations': ['self_test']}))
        with self.assertRaises(ServicePolicyError): self.perform(permit)
        self.assertEqual(self.auth.owner, 'unknown')
        self.assertEqual(len(self.auth.reports), 0)

    def test_synthetic_plan_authority_not_exposed_via_normal_driver(self):
        root = Path(__file__).resolve().parents[2]
        device = (root / 'src/keithley2231a/device.py').read_text()
        self.assertNotIn('service_authority_offline', device)
        self.assertNotIn('OfflineServiceAuthority', device)
        authority = (root / 'src/keithley2231a/service_authority_offline.py').read_text()
        for dangerous in ('import serial', 'from serial', 'socket.', 'SerialTransport',
                          'TcpTransport', 'diagnostic_write', 'subprocess.'):
            self.assertNotIn(dangerous, authority)


if __name__ == '__main__':
    unittest.main()
