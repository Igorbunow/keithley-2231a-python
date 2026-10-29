"""Service path through the PRODUCTION Keithley driver over POSIX virtual UART.

No physical connection. Check the exact received command lines and fail-stop.
"""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from keithley2231a import Keithley2231A, SafetyError, SerialTransport
from keithley2231a.service import ServicePlanner, ServicePolicyError, ServiceProfile
from support.virtual_uart import PTYTransport, UARTPeer


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pty')
class ServiceVirtualUartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'service.json'
        self.profile_data = dict(mode='service', model='2231A-30-3',
                                 serial='LAB-UNIT-001', allowed_operations=[
                                     'clear_events', 'reset_settings', 'wait_pending',
                                     'calibration_save', 'calibration_initialize',
                                     'calibration_voltage', 'self_test',
                                     'power_on_status_write', 'calibration_unlock'])
        self.save_profile()
        self.peer = UARTPeer()
        self.addCleanup(self.peer.close)
        try:
            import serial  # noqa: F401
        except ImportError:
            self.transport = PTYTransport(self.peer.path)
        else:
            # On the operator machine, use the *production* pyserial adapter.
            self.transport = SerialTransport(self.peer.path, timeout=.35)
        self.addCleanup(self.transport.close)
        self.dev = Keithley2231A(self.transport, expected_serial='LAB-UNIT-001', service_mode=True)

    def save_profile(self):
        self.path.write_text(json.dumps(self.profile_data))

    def permits(self, op, **params):
        profile = ServiceProfile.load(self.path)
        planner = ServicePlanner(profile)
        ack = planner.required_ack(op)
        plan = planner.plan(op, acknowledgement=ack, **params)
        plan_hash = hashlib.sha256(json.dumps(
            [(step.command, step.query) for step in plan.steps],
            separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
        base = f'{op} {profile.serial} {profile.source_sha256[:12]} {plan_hash[:12]}'
        return dict(acknowledgement=ack,
                    service_confirmation=f'EXECUTE SERVICE {base}',
                    critical_confirmation=f'CRITICAL SERVICE {base}')

    def run_service(self, op, **params):
        return self.dev.service_operation(self.path, op, **self.permits(op, **params), **params)

    def assert_no_service_wire(self, token):
        self.assertNotIn(token, self.peer.rx)

    def test_real_driver_writes_clear_events_once_with_one_error_poll(self):
        self.assertEqual(self.run_service('clear_events'), ())
        self.assertEqual(self.peer.rx.count('*CLS'), 1)
        self.assertIn(b'*CLS\r\n', self.peer.rx_bytes)
        i=self.peer.rx.index('*CLS')
        self.assertEqual(self.peer.rx[i+1], 'SYST:ERR?')
        self.assertEqual(self.peer.rx.count('*IDN?'), 1)
        self.assertIn('OUTP:TRAC?', self.peer.rx)
        self.assertEqual(self.peer.rx.count('CHAN:OUTP?'), 6)
        self.assertFalse(self.dev.faulted)

    def test_virtual_uart_wait_pending_uses_actual_common_dispatch(self):
        self.run_service('wait_pending')
        self.assertEqual(self.peer.rx.count('*WAI'),1)
        self.assertEqual(self.peer.rx[self.peer.rx.index('*WAI')+1],'SYST:ERR?')

    def test_critical_operation_requires_third_approval(self):
        params=self.permits('reset_settings'); params['critical_confirmation']=''
        with self.assertRaises(SafetyError):
            self.dev.service_operation(self.path, 'reset_settings', **params)
        self.assertEqual(self.peer.rx, [])
        self.run_service('reset_settings')
        self.assertEqual(self.peer.rx.count('*RST'),1)

    def test_no_service_right_denied_before_any_uart_byte(self):
        self.profile_data['allowed_operations']=['wait_pending'];self.save_profile()
        with self.assertRaises(ServicePolicyError): self.run_service('reset_settings')
        self.assertEqual(self.peer.rx,[])

    def test_wrong_ack_denied_before_uart(self):
        params=self.permits('clear_events');params['acknowledgement']='YES'
        with self.assertRaises(ServicePolicyError):
            self.dev.service_operation(self.path,'clear_events',**params)
        self.assertEqual(self.peer.rx,[])

    def test_profile_hash_change_revokes_old_ack(self):
        params=self.permits('clear_events'); self.path.write_bytes(self.path.read_bytes()+b' ')
        with self.assertRaises(ServicePolicyError):
            self.dev.service_operation(self.path,'clear_events',**params)
        self.assertEqual(self.peer.rx,[])

    def test_normal_debug_api_cannot_write_service_even_on_virtual_uart(self):
        debug=Keithley2231A(self.transport,debug=True,debug_bypass_profile=True)
        with self.assertRaises(SafetyError): debug.diagnostic_write('*RST')
        with self.assertRaises(SafetyError): debug.diagnostic_query('*TST?')
        self.assertEqual(self.peer.rx,[])

    def test_wrong_serial_stops_before_service_command(self):
        self.peer.serial='OTHER'
        with self.assertRaises(Exception):self.run_service('clear_events')
        self.assert_no_service_wire('*CLS')
        self.assertTrue(self.dev.faulted)

    def test_any_on_output_stops_before_service_command(self):
        self.peer.off_by_channel[2]=1
        with self.assertRaises(SafetyError):self.run_service('clear_events')
        self.assert_no_service_wire('*CLS')
        self.assertTrue(self.dev.faulted)

    def test_combined_topology_stops_before_service_command(self):
        self.peer.overrides['OUTP:PARALLEL?']='1'
        with self.assertRaises(SafetyError):self.run_service('clear_events')
        self.assert_no_service_wire('*CLS')

    def test_preflight_lost_reply_does_not_continue(self):
        self.peer.drop='OUTP:PARALLEL?'
        with self.assertRaises(Exception):self.run_service('clear_events')
        self.assert_no_service_wire('*CLS')
        self.assertNotIn('OUTP:TRAC?',self.peer.rx)
        self.assertTrue(self.dev.faulted)

    def test_service_scpi_error_blocks_next_operation(self):
        self.peer.fault_code='120,"Different error"'
        with self.assertRaises(Exception):self.run_service('clear_events')
        self.assertEqual(self.peer.rx.count('*CLS'),1)
        before=list(self.peer.rx)
        with self.assertRaises(SafetyError):self.run_service('wait_pending')
        self.assertEqual(self.peer.rx,before)

    def test_calibration_saving_and_reset_forbidden_on_wire(self):
        for op in ('calibration_save','calibration_initialize'):
            with self.subTest(op=op):
                with self.assertRaises(SafetyError):self.run_service(op)
                self.assertEqual(self.peer.rx,[])

    def test_unverified_commands_forbidden_before_uart(self):
        for op,params in [('self_test',{}),('power_on_status_write',{'state':1}),
                          ('calibration_unlock',{'intent':'REQUIRES_EXTERNAL_SECRET'})]:
            with self.subTest(op=op):
                with self.assertRaises(SafetyError):self.run_service(op,**params)
                self.assertEqual(self.peer.rx,[])

    def test_normal_profile_cannot_turn_into_service(self):
        self.profile_data['mode']='control';self.save_profile()
        with self.assertRaises(ServicePolicyError):self.run_service('wait_pending')
        self.assertEqual(self.peer.rx,[])

    def test_parameter_bound_approval_prevents_changed_setpoint_on_wire(self):
        self.profile_data['allowed_operations'].append('save_setup')
        self.save_profile()
        old = self.permits('save_setup', slot=1)
        with self.assertRaises(SafetyError):
            self.dev.service_operation(self.path, 'save_setup', **old, slot=2)
        self.assertEqual(self.peer.rx, [])

    def test_service_disabled_by_default_even_with_full_profile_ack(self):
        normal = Keithley2231A(self.transport, expected_serial='LAB-UNIT-001')
        with self.assertRaises(SafetyError):
            normal.service_operation(self.path, 'clear_events', **self.permits('clear_events'))
        self.assertEqual(self.peer.rx, [])

    def test_service_session_blocks_normal_output_control(self):
        with self.assertRaises(SafetyError):
            self.dev.diagnostic_write('OUTP 1')  # debug=False; no bytes
        with self.assertRaises(SafetyError):
            self.dev.output_states()
        self.assertEqual(self.peer.rx, [])

    def test_service_approval_single_use_per_driver_instance(self):
        self.run_service('clear_events')
        with self.assertRaises(SafetyError): self.run_service('clear_events')
        self.assertEqual(self.peer.rx.count('*CLS'), 1)
        self.assertEqual(self.peer.rx.count('*IDN?'), 1)

    def test_service_mode_and_debug_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            Keithley2231A(self.transport, service_mode=True, debug=True)
        self.assertEqual(self.peer.rx, [])

    def test_armed_driver_refuses_service_before_any_new_uart(self):
        # Exercise real driver state invariant, not a separate fake service session.
        self.dev._armed=True
        with self.assertRaises(SafetyError):self.run_service('clear_events')
        self.assertEqual(self.peer.rx,[])

    def test_no_legacy_fake_execution_modules(self):
        root=Path(__file__).resolve().parents[2]
        for name in ('service_authority_offline.py','service_gateway_offline.py','service_simulator.py'):
            self.assertFalse((root/'src/keithley2231a'/name).exists())

if __name__=='__main__':unittest.main()
