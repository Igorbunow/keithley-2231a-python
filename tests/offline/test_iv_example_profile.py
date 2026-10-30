"""R4.5-F-J: minimal I-V example profile and real-driver virtual-UART guards.

These are virtual PSU replies, NOT CH2/CH3 physical qualifications.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'examples'))
from iv_profile import IVProfileError, load_iv_profile, describe
from diode_iv import run_profile, main
from support.virtual_uart import UARTPeer, open_test_transport
from keithley2231a import Keithley2231A


def channel(ch, *, points=True, **kw):
    c = {'channel':ch, 'current_limit_a':.02, 'ballast_ohm':99.86,
         'ballast_rating_w':.25, 'max_current_error_ma':.1,
         'current_source':'auto', 'force':False}
    c.update({'points_v':[.8,.9]} if points else
             {'range_v':{'start_v':.8,'stop_v':1.0,'step_v':.1}})
    c.update(kw)
    return c


def payload(*channels):
    return {'schema':1, 'name':'test-iv', 'settle_s':0.0, 'channels':list(channels)}


class PureProfileTests(unittest.TestCase):
    def read(self, obj):
        with tempfile.TemporaryDirectory() as td:
            file = Path(td)/'plan.json'
            file.write_text(json.dumps(obj), encoding='utf-8')
            return load_iv_profile(file)

    def test_force_allows_psu_diagnostic_only(self):
        plan = self.read(payload(channel(1, force=True)))
        cp = plan.channels[0]
        self.assertEqual(cp.selected_source, 'PSU_READBACK_DIAGNOSTIC')
        self.assertGreater(cp.nominal_psu_readback_error_ma, cp.required_error_ma)
        self.assertFalse(describe(plan)['meter_accuracy_verified'])

    def test_auto_requires_external_when_tolerance_tight(self):
        self.assertEqual(self.read(payload(channel(2))).channels[0].selected_source,
                         'EXTERNAL_REQUIRED')

    def test_psu_explicit_inadequate_rejected(self):
        with self.assertRaises(IVProfileError):
            self.read(payload(channel(1,current_source='psu')))

    def test_nominal_psu_readback_with_loose_target(self):
        p = self.read(payload(channel(3,max_current_error_ma=20)))
        self.assertEqual(p.channels[0].selected_source, 'PSU_READBACK_DIAGNOSTIC')
        self.assertFalse(p.channels[0].force)

    def test_explicit_external_never_overridden(self):
        p = self.read(payload(channel(1,current_source='external',force=False)))
        self.assertEqual(p.channels[0].selected_source, 'EXTERNAL_REQUIRED')

    def test_range_exact(self):
        p = self.read(payload(channel(2,points=False)))
        self.assertEqual(p.channels[0].voltages, (.8,.9,1.0))

    def test_three_channels_in_order(self):
        p = self.read(payload(channel(3),channel(1),channel(2)))
        self.assertEqual([c.channel for c in p.channels], [3,1,2])

    def test_two_channels_not_combined(self):
        p = self.read(payload(channel(2),channel(3)))
        self.assertEqual(len(p.channels), 2)

    def test_malformed_profiles_refused(self):
        bad = [
            payload(), payload(channel(1),channel(1)), payload(channel(4)),
            payload(channel(True)), payload(channel(1,force='true')),
            payload(channel(1,current_source='unknown')),
            payload(channel(1,current_limit_a=.0001)),
            payload(channel(1,current_limit_a=.0105)),
            payload(channel(1,ballast_ohm=5)),
            payload(channel(1,ballast_rating_w=.01)),
            payload(channel(1,ballast_ohm=1,ballast_rating_w=.25)),
            payload(channel(1,max_current_error_ma=0)),
            payload(channel(1,max_current_error_ma=float('nan'))),
            payload(channel(1,points_v=[.005])),
            payload(channel(1,points_v=[float('inf')])),
            payload(channel(1,points_v=[5.01])),
            payload(channel(1,points_v=[0])),
            payload(channel(1,points_v=[.1]*101)),
            payload(channel(1,points_v=[])),
            payload(channel(1,range_v={'start_v':.8,'stop_v':1,'step_v':.07})),
            payload(channel(1,range_v={'start_v':1,'stop_v':.8,'step_v':.1})),
            payload(channel(1,range_v={'start_v':0,'stop_v':5,'step_v':.01})),
            payload(channel(1,range_v={'start_v':0,'stop_v':1,'step_v':0})),
            payload(channel(1,range_v={'start_v':.8,'stop_v':.9,'step_v':.1}, points_v=[.8])),
            dict(payload(channel(1)), schema=2),
            dict(payload(channel(1)), surplus=True),
        ]
        for v in bad:
            with self.subTest(case=str(v)[:85]):
                with self.assertRaises(IVProfileError): self.read(v)

    def test_ch23_hw_candidate_profile_dry_run_and_instrument_limits(self):
        candidate = ROOT/'examples'/'diode_iv_ch23_qualification_profile.json'
        plan = load_iv_profile(candidate)
        self.assertEqual([p.channel for p in plan.channels], [2, 3])
        self.assertEqual(plan.channels[0].voltages, (.8,.9,.8))
        self.assertEqual(plan.channels[1].voltages, (.8,.9))
        self.assertTrue(all(p.force for p in plan.channels))
        with patch('diode_iv.SerialTransport') as transport:
            self.assertEqual(main(['--profile', str(candidate)]), 0)
            transport.assert_not_called()

    def test_json_duplicate_key_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'x.json'
            p.write_text('{"schema":1,"schema":1,"name":"x","settle_s":0,"channels":[]}',
                         encoding='utf-8')
            with self.assertRaises(IVProfileError): load_iv_profile(p)

    def test_cli_no_tty_never_constructs_serial_transport(self):
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td)/'p.json'
            profile.write_text(json.dumps(payload(channel(1, force=True))), encoding='utf-8')
            with patch('diode_iv.sys.stdin') as stdin, patch('diode_iv.sys.stdout') as stdout, \
                 patch('diode_iv.SerialTransport') as transport:
                stdin.isatty.return_value = False
                stdout.isatty.return_value = True
                with self.assertRaises(SystemExit):
                    main(['--profile', str(profile), '--run', '--port','/nonexistent',
                          '--output-dir',str(Path(td)/'output')])
                transport.assert_not_called()

    def test_default_cli_has_no_hardware_access(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'plan.json'
            p.write_text(json.dumps(payload(channel(1, force=True))),encoding='utf-8')
            self.assertEqual(main(['--profile',str(p)]), 0)

    def test_cli_run_refused_without_tty(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'plan.json'
            p.write_text(json.dumps(payload(channel(1, force=True))),encoding='utf-8')
            with patch('diode_iv.sys.stdin') as stdin, patch('diode_iv.sys.stdout') as stdout:
                stdin.isatty.return_value = False
                stdout.isatty.return_value = True
                with self.assertRaises(SystemExit) as error:
                    main(['--profile',str(p),'--run','--port','/nonexistent',
                          '--output-dir',str(Path(td)/'output')])
            self.assertEqual(error.exception.code, 2)


@unittest.skipUnless(os.name == 'posix', 'requires POSIX virtual UART')
class ProfiledExampleVirtualUART(unittest.TestCase):
    def load(self, *channels):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'p.json'
            p.write_text(json.dumps(payload(*channels)),encoding='utf-8')
            return load_iv_profile(p)

    def device(self):
        peer = UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire = open_test_transport(peer, timeout=.4)
        self.addCleanup(wire.close)
        return Keithley2231A(wire), peer

    def test_each_channel_real_driver_sweeps_and_verified_off(self):
        plan = self.load(channel(1, force=True), channel(2, force=True),
                         channel(3, force=True))
        dev, peer = self.device()
        gates = []
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                paths = run_profile(dev, plan, output_dir=Path(td)/'result',
                                    approve=gates.append)
            self.assertEqual(len(paths),3)
            self.assertEqual(len(gates),9)
            for ch, path in zip((1,2,3), paths):
                self.assertTrue(path.exists())
                text = path.read_text(encoding='utf-8')
                self.assertEqual(text.count('PSU_READBACK_DIAGNOSTIC'),2)
                self.assertIn(f'CH{ch}', gates[(ch-1)*3])
            summary = json.loads((Path(td)/'result'/'completed.json').read_text())
            self.assertEqual(summary['status'],
                             'EXAMPLE_COMPLETED_NOT_INDEPENDENT_HW_QUALIFICATION')
            self.assertEqual([c['channel'] for c in summary['channels']], [1,2,3])
            self.assertTrue(all(c['digital_off_confirmed'] and
                                c['physical_off_operator_confirmed']
                                for c in summary['channels']))
            for row, path in zip(summary['channels'], paths):
                import hashlib
                self.assertEqual(row['csv_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        selected = 1
        enabled = []
        for cmd in peer.rx:
            if cmd.startswith('INST:NSEL '): selected = int(cmd.split()[-1])
            if cmd == 'CHAN:OUTP 1': enabled.append(selected)
        self.assertEqual(enabled,[1,2,3])
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(peer.unsupported, [])

    def test_missing_external_observer_blocks_all_scpi(self):
        plan = self.load(channel(1), channel(2,force=True))
        dev, peer = self.device()
        with tempfile.TemporaryDirectory() as td:
            before = list(peer.rx)
            with self.assertRaises(IVProfileError):
                run_profile(dev,plan,output_dir=Path(td)/'result',approve=lambda _:None)
            self.assertEqual(peer.rx,before)

    def test_invalid_external_result_triggers_shutdown(self):
        plan = self.load(channel(1))
        dev,peer = self.device()
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                with self.assertRaises(IVProfileError):
                    run_profile(dev,plan,output_dir=Path(td)/'result',
                                approve=lambda _:None,
                                external_observers={1:lambda _: {'current_a':float('nan')}})
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertTrue(any(c == 'OUTP 0' for c in peer.rx))

    def test_operator_refusal_after_first_channel_stops_before_second(self):
        plan = self.load(channel(1,force=True), channel(2,force=True))
        dev,peer = self.device()
        gates=[]
        def refusal(token):
            gates.append(token)
            if len(gates)==3: raise IVProfileError('Manual OFF unconfirmed')
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                with self.assertRaises(IVProfileError):
                    run_profile(dev,plan,output_dir=Path(td)/'result',approve=refusal)
            self.assertFalse((Path(td)/'result'/'completed.json').exists())
        self.assertEqual(len(gates),3)
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'),1)
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_preexisting_other_channel_on_refuses_before_first_enable(self):
        plan = self.load(channel(2, force=True))
        dev, peer = self.device()
        peer.off_by_channel[1] = 1
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(IVProfileError, 'confirmed OFF readback'):
                run_profile(dev, plan, output_dir=Path(td)/'result',
                            approve=lambda _: self.fail('No operator gate before digital OFF'))
            self.assertFalse((Path(td)/'result'/'completed.json').exists())
        self.assertNotIn('CHAN:OUTP 1', peer.rx)

    def test_unconfirmed_final_digital_off_is_not_success(self):
        plan = self.load(channel(2,force=True), channel(3,force=True))
        dev, peer = self.device()
        gates = []
        original_states = dev.output_states
        count = [0]
        def bad_post_sweep():
            count[0] += 1
            # Calls: before wiring, after apply, after sweep; refuse 3rd.
            if count[0] == 3:
                return {1:False, 2:True, 3:False}
            return original_states()
        with tempfile.TemporaryDirectory() as td:
            with patch.object(dev, 'output_states', side_effect=bad_post_sweep), \
                 patch('examples.iv_sweep.time.sleep'):
                with self.assertRaisesRegex(IVProfileError, 'confirmed OFF readback'):
                    run_profile(dev, plan, output_dir=Path(td)/'result',
                                approve=gates.append)
            self.assertFalse((Path(td)/'result'/'completed.json').exists())
        self.assertEqual(peer.rx.count('CHAN:OUTP 1'), 1)
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertEqual(len(gates), 2)

    def test_no_output_dir_overwrite_before_scpi(self):
        plan = self.load(channel(1,force=True))
        dev,peer = self.device()
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(IVProfileError):
                run_profile(dev,plan,output_dir=Path(td),approve=lambda _:None)
        self.assertEqual(peer.rx,[])
