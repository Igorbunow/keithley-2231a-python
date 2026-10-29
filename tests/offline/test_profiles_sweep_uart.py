"""Power-only profiles, guarded sequences and voltage sweeps over production UART.

The fixture returns digital SCPI values; it is not a physical source/load model.
All tests use Keithley2231A, not a second SCPI dispatcher.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from keithley2231a import (ChannelProfile, Keithley2231A, LimitsError,
                          PowerStep, Profile, ProfileError, SafetyError,
                          voltage_sweep)
from support.virtual_uart import UARTPeer, open_test_transport
from test_driver import profile


def power_profile(steps=()):
    p = profile()
    return Profile('locked-rack', p.channels, access='power_only',
                   power_sequence=tuple(steps), lock_front_panel=True)


class PureProfileValidation(unittest.TestCase):
    """Data-model checks intentionally do not open a serial device."""

    def test_sequence_validation(self):
        with self.assertRaises(ProfileError): PowerStep(4)
        with self.assertRaises(ProfileError): PowerStep(1, float('nan'))
        with self.assertRaises(ProfileError): power_profile([PowerStep(1), PowerStep(1)])
        p = profile(wiring='tracking')
        with self.assertRaises(ProfileError):
            Profile('bad', p.channels, wiring='tracking', access='power_only')

    def test_json_profile_power_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'profile.json'
            f.write_text('''{"name":"test","access":"power_only","channels":{
                "1":{"max_voltage":3.3,"max_current":0.2},
                "2":{"max_voltage":5,"max_current":0.2},
                "3":{"max_voltage":3.3,"max_current":0.2}},
                "power_sequence":[{"channel":1,"delay_s":0.0},{"channel":3,"delay_s":0.2}]}''')
            p = Profile.from_json(f)
            self.assertEqual(p.access, 'power_only')
            self.assertEqual(p.power_sequence[1], PowerStep(3, .2))


@unittest.skipUnless(os.name == 'posix', 'requires POSIX pseudo-terminal')
class ProfilesAndSweepUART(unittest.TestCase):
    def device(self, applied=None, **peer_kwargs):
        peer = UARTPeer(profile_registers=True, **peer_kwargs)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.2)
        self.addCleanup(wire.close)
        psu = Keithley2231A(wire)
        if applied is not None:
            psu.apply_profile(applied, confirmed_wiring='independent')
            self.assertTrue(psu.armed)
        return psu, peer

    def assert_clean_wire(self, peer):
        self.assertEqual(peer.unsupported, [])
        self.assertEqual(peer.rx_bytes, [(s + '\r\n').encode('ascii') for s in peer.rx])

    def assert_sequence(self, commands, token):
        selected = 1
        result = []
        for cmd in commands:
            if cmd.startswith('INST:NSEL '):
                selected = int(cmd.split()[-1])
            elif cmd == token:
                result.append(selected)
        return result

    def test_unrestricted_is_rated_not_raw(self):
        p = Profile.unrestricted()
        self.assertEqual([(c.max_voltage, c.max_current) for c in p.channels],
                         [(30, 3), (30, 3), (5, 3)])
        dev, peer = self.device(p)
        dev.set_channel(1, voltage=30, current=3)
        dev.set_channel(3, voltage=5, current=3)
        self.assertEqual(peer.channel_registers[3]['VOLT'], '5.00')
        before = list(peer.rx)
        with self.assertRaises(LimitsError): dev.set_channel(3, voltage=5.01)
        with self.assertRaises(LimitsError): dev.set_channel(1, voltage=30.01)
        self.assertEqual(peer.rx, before)
        self.assert_clean_wire(peer)

    def test_lockdown_blocks_setpoints_arbitrary_sequences_and_timers(self):
        dev, peer = self.device(power_profile([PowerStep(2, .01), PowerStep(1, .02)]))
        for operation in (lambda: dev.set_channel(1, voltage=1.5),
                          lambda: dev.output(1, True),
                          lambda: dev.sequence([(0, 1, True)]),
                          lambda: dev.timer(1, 1),
                          lambda: dev.remote(lock_panel=False),
                          lambda: dev.remote(lock_panel=True),
                          lambda: dev.beep()):
            before = list(peer.rx)
            with self.assertRaises(SafetyError): operation()
            self.assertEqual(peer.rx, before)
        with patch('keithley2231a.device.time.sleep') as sleeper:
            dev.power_on()
            self.assertTrue(peer.await_command('CHAN:OUTP 1', occurrences=2))
            self.assertEqual(self.assert_sequence(peer.rx, 'CHAN:OUTP 1'), [2, 1])
            dev.power_off()
            self.assertTrue(peer.await_command('CHAN:OUTP 0', occurrences=2))
            self.assertEqual(self.assert_sequence(peer.rx, 'CHAN:OUTP 0')[-2:], [1, 2])
            self.assertEqual(sleeper.call_count, 4)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assert_clean_wire(peer)

    def test_three_channel_order_and_reverse_delays(self):
        steps = [PowerStep(1, 0.), PowerStep(2, 1.), PowerStep(3, 1.)]
        dev, peer = self.device(power_profile(steps))
        with patch('keithley2231a.device.time.sleep') as sleeper:
            dev.power_on()
            self.assertTrue(peer.await_command('CHAN:OUTP 1', occurrences=3))
            self.assertEqual(self.assert_sequence(peer.rx, 'CHAN:OUTP 1'), [1, 2, 3])
            dev.power_off()
            self.assertTrue(peer.await_command('CHAN:OUTP 0', occurrences=3))
            self.assertEqual(self.assert_sequence(peer.rx, 'CHAN:OUTP 0')[-3:], [3, 2, 1])
            self.assertEqual(sleeper.call_args_list,
                             [call(0.), call(1.), call(1.), call(1.), call(1.), call(0.)])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assert_clean_wire(peer)

    def test_power_on_readback_failure_latches_uncertain_link(self):
        dev, peer = self.device(power_profile([PowerStep(1, 0), PowerStep(2, 0)]))
        # The reply is lost *after* a real request reaches the UART.  The
        # library must not claim outputs OFF or replay safety commands.
        peer.drop = 'CHAN:OUTP?'
        before = list(peer.rx)
        with self.assertRaises(SafetyError): dev.power_on()
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertTrue(peer.await_command('CHAN:OUTP?'))
        self.assertNotIn('OUTP 0', peer.rx[len(before):])
        self.assert_clean_wire(peer)

    def test_power_on_valid_mismatch_trips_with_verified_off(self):
        dev, peer = self.device(power_profile([PowerStep(1, 0)]),
                                response_overrides={'CHAN:OUTP?': '0'})
        baseline = peer.rx.count('OUTP 0')
        with patch('keithley2231a.device.time.sleep'):
            with self.assertRaises(SafetyError):
                dev.power_on()
        self.assertFalse(dev.armed)
        self.assertTrue(dev.faulted)
        self.assertTrue(peer.await_command('OUTP 0', occurrences=baseline + 1))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assert_clean_wire(peer)

    def test_sweep_invalid_quotas_refused_before_scpi(self):
        dev, peer = self.device(profile())
        baseline = list(peer.rx)
        arguments = (
            {'voltages': []},
            {'voltages': [.2], 'settle_s': float('nan')},
            {'voltages': [.2], 'settle_s': -.1},
            {'voltages': [.2], 'current_limit_a': .201},
            {'voltages': [.2], 'current_limit_a': True},
            {'voltages': [.205]},
            {'voltages': [float('nan')]},
            {'voltages': [True]},
        )
        for opts in arguments:
            with self.subTest(opts=opts):
                volts = opts['voltages']
                kwargs = dict(current_limit_a=.02, settle_s=0)
                kwargs.update({k: v for k, v in opts.items() if k != 'voltages'})
                with self.assertRaises(ValueError):
                    voltage_sweep(dev, 1, volts, **kwargs)
                self.assertEqual(peer.rx, baseline)
        self.assertTrue(dev.armed)
        self.assert_clean_wire(peer)

    def test_sweep_csv_and_stop_off(self):
        dev, peer = self.device(profile())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'curve.csv'
            with patch('keithley2231a.sweep.time.sleep'):
                points = voltage_sweep(dev, 1, [.1, .2, .3], current_limit_a=.02,
                                       observer=lambda r: {'dmm_v': r.voltage_v - .01},
                                       csv_path=path)
            self.assertEqual(len(points), 3)
            self.assertEqual(points[1].requested_v, .2)
            with path.open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 3)
            self.assertIn('dmm_v', rows[0])
            self.assertEqual([row['requested_v'] for row in rows], ['0.1', '0.2', '0.3'])
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertFalse(dev.armed)
        self.assert_clean_wire(peer)

    def test_sweep_rejects_unsafe_access_and_limits_before_io(self):
        dev, peer = self.device(power_profile())
        baseline = list(peer.rx)
        with self.assertRaises(SafetyError): voltage_sweep(dev, 1, [1], current_limit_a=.01)
        self.assertEqual(peer.rx, baseline)
        dev.emergency_off()
        dev.apply_profile(profile(), confirmed_wiring='independent')
        baseline = list(peer.rx)
        with self.assertRaises(ValueError): voltage_sweep(dev, 1, [4], current_limit_a=.01)
        self.assertEqual(peer.rx, baseline)
        self.assertTrue(dev.armed)
        self.assert_clean_wire(peer)

    def test_sweep_rejects_other_live_channels_without_turning_them_off(self):
        dev, peer = self.device(profile())
        dev.output(2, True)
        baseline_off = peer.rx.count('OUTP 0')
        with self.assertRaises(SafetyError): voltage_sweep(dev, 1, [.2], current_limit_a=.02)
        self.assertTrue(peer.off_by_channel[2])
        self.assertEqual(peer.rx.count('OUTP 0'), baseline_off)
        self.assertTrue(dev.armed)
        self.assert_clean_wire(peer)

    def test_sweep_observer_exception_still_turns_output_off(self):
        dev, peer = self.device(profile())
        with patch('keithley2231a.sweep.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'dmm failed'):
                voltage_sweep(dev, 1, [.2], current_limit_a=.02,
                              observer=lambda r: (_ for _ in ()).throw(RuntimeError('dmm failed')))
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertFalse(dev.armed)
        self.assert_clean_wire(peer)

    def test_sweep_invalid_observer_result_still_turns_output_off(self):
        dev, peer = self.device(profile())
        with patch('keithley2231a.sweep.time.sleep'):
            with self.assertRaises(ValueError):
                voltage_sweep(dev, 1, [.2], current_limit_a=.02,
                              observer=lambda _: {'bad': float('nan')})
        self.assertTrue(peer.await_command('OUTP 0', occurrences=2))
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertFalse(dev.armed)
        self.assert_clean_wire(peer)


if __name__ == '__main__':
    unittest.main()
