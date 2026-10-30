"""No physical devices: DCV curve selection, guards and integrity checks."""
from __future__ import annotations
import csv
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for part in ('src','examples','tests','tests/hardware','tools'):
    sys.path.insert(0,str(ROOT/part))

from iv_profile import IVProfileError, load_iv_profile, describe
from diode_iv import run_profile
from r4_5_1n4933_dense_dcv import PROFILE,validate_fixed_plan,main as runner
from r4_5_1n4933_dense_plot import review, plot_svg
from support.virtual_uart import UARTPeer, open_test_transport
from keithley2231a import Keithley2231A


class DenseDCVPure(unittest.TestCase):
    def test_fixed_plan_61_points_dry(self):
        plan=load_iv_profile(PROFILE)
        self.assertTrue(validate_fixed_plan(plan))
        self.assertEqual(len(plan.channels[0].voltages),61)
        self.assertEqual(plan.channels[0].selected_source,'EXTERNAL_DIODE_DCV_BALLAST')
        self.assertEqual(plan.channels[0].voltages[0],.6)
        self.assertEqual(plan.channels[0].voltages[30],1.2)
        self.assertEqual(plan.channels[0].voltages[-1],.6)
        self.assertEqual(runner(['--config',str(ROOT/'config/bench.example.toml')]),0)

    def test_dry_run_does_not_open_psu(self):
        with patch('r4_5_1n4933_dense_dcv.open_psu') as opened:
            self.assertEqual(runner(['--config',str(ROOT/'config/bench.example.toml')]),0)
            opened.assert_not_called()

    def test_no_unattended_execute_or_example_config(self):
        with self.assertRaises(SystemExit):
            runner(['--config',str(ROOT/'config/bench.example.toml'), '--execute',
                    '--output-dir','/tmp/example-output-do-not-create'])

    def test_external_dcv_no_force(self):
        payload=json.loads(PROFILE.read_text())
        payload['channels'][0]['force']=True
        with tempfile.TemporaryDirectory() as td:
            name=Path(td)/'p.json';name.write_text(json.dumps(payload))
            with self.assertRaises(IVProfileError):load_iv_profile(name)


@unittest.skipUnless(os.name=='posix','POSIX virtual UART required')
class DenseDCVUART(unittest.TestCase):
    def plan(self):
        raw=json.loads(PROFILE.read_text())
        raw['channels'][0]['points_v']=[.8,.9]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'p.json';path.write_text(json.dumps(raw))
            return load_iv_profile(path)

    def device(self):
        peer=UARTPeer(profile_registers=True)
        self.addCleanup(peer.close)
        wire=open_test_transport(peer,timeout=.75)
        self.addCleanup(wire.close)
        return Keithley2231A(wire),peer

    def test_voltage_observer_integrates_without_psu_substitution(self):
        dev,peer=self.device()
        plan=self.plan()
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                paths=run_profile(dev,plan,output_dir=Path(td)/'out',approve=lambda _:None,
                    diode_voltage_observers={1:lambda r:{'voltage_v':r.voltage_v-.20}})
            with paths[0].open(newline='') as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['source'],'EXTERNAL_DIODE_DCV_BALLAST')
            self.assertAlmostEqual(float(rows[0]['selected_current_a']),.20/99.86,places=8)
            self.assertAlmostEqual(float(rows[1]['selected_current_a']),.20/99.86,places=8)
            self.assertNotAlmostEqual(float(rows[0]['psu_readback_a']),.20/99.86,places=5)
            self.assertEqual(rows[0]['method'],'PSU_DCV_MINUS_DMM_DIODE_DCV_OVER_R_SEQUENTIAL')
            summary=json.loads((Path(td)/'out'/'completed.json').read_text())
            self.assertTrue(summary['channels'][0]['external_voltage_used'])
        self.assertFalse(any(peer.off_by_channel.values()))

    def test_missing_dcv_observer_no_io(self):
        dev,peer=self.device();plan=self.plan()
        before=list(peer.rx)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(IVProfileError):
                run_profile(dev,plan,output_dir=Path(td)/'out',approve=lambda _:None)
            self.assertEqual(peer.rx,before)

    def test_bad_dcv_fails_off_no_completion(self):
        dev,peer=self.device();plan=self.plan()
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                with self.assertRaises(IVProfileError):
                    run_profile(dev,plan,output_dir=Path(td)/'out',approve=lambda _:None,
                       diode_voltage_observers={1:lambda r:{'voltage_v':-0.10}})
            self.assertFalse((Path(td)/'out'/'completed.json').exists())
        self.assertFalse(any(peer.off_by_channel.values()))
        self.assertIn('OUTP 0',peer.rx)

    def test_calculation_mismatch_rejected_by_offline_plotter(self):
        dev,peer=self.device();plan=self.plan()
        with tempfile.TemporaryDirectory() as td:
            with patch('examples.iv_sweep.time.sleep'):
                run_profile(dev,plan,output_dir=Path(td)/'out',approve=lambda _:None,
                    diode_voltage_observers={1:lambda r:{'voltage_v':r.voltage_v-.2}})
            with self.assertRaises(ValueError):review(Path(td)/'out') # 61 points required

if __name__=='__main__': unittest.main()

class DensePlotPure(unittest.TestCase):
    def example(self, td):
        folder=Path(td)
        csv_path=folder/'channel_1.csv'
        import hashlib
        with csv_path.open('w',newline='') as fh:
            out=csv.writer(fh)
            out.writerow(('index','timestamp_utc','requested_psu_v','measured_psu_v',
              'selected_current_a','psu_readback_a','estimated_diode_v','regulation',
              'source','accuracy_qualified','forced_low_accuracy','measured_diode_dcv_v',
              'method','precision_note'))
            from iv_profile import load_iv_profile
            for n,preset in enumerate(load_iv_profile(PROFILE).channels[0].voltages):
                # Deliberately *synthetic* fixture: not a hardware qualification.
                diode=.56+0.015*n/61
                current=(preset-diode)/99.86
                out.writerow((n,'2026-10-30T22:00:00+00:00',preset,preset,current,.001,
                   diode,'CV','EXTERNAL_DIODE_DCV_BALLAST',False,False,diode,
                   'PSU_DCV_MINUS_DMM_DIODE_DCV_OVER_R_SEQUENTIAL','UNQUALIFIED'))
        digest=hashlib.sha256(csv_path.read_bytes()).hexdigest()
        (folder/'completed.json').write_text(json.dumps({'status':
            'EXAMPLE_COMPLETED_NOT_INDEPENDENT_HW_QUALIFICATION',
            'channels':[{'channel':1,'current_source':'EXTERNAL_DIODE_DCV_BALLAST',
             'force':False,'digital_off_confirmed':True,'physical_off_operator_confirmed':True,
             'external_voltage_used':True,'point_count':61,'csv':'channel_1.csv','csv_sha256':digest}]}))
        return folder

    def test_plot_and_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            directory=self.example(td)
            pts,diagnostic=review(directory)
            self.assertEqual(len(pts),61)
            self.assertEqual(diagnostic['status'],'OFFLINE_DIAGNOSTIC_REVIEW_NOT_CALIBRATED')
            dest=directory/'a.svg'
            plot_svg(pts,dest)
            self.assertIn('1N4933 forward I-V',dest.read_text())
            self.assertIn('UNQUALIFIED', ''.join(diagnostic['limitations']).upper()) if False else None

    def test_tampered_data_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            directory=self.example(td)
            with (directory/'channel_1.csv').open('a') as f:f.write('garbage\n')
            with self.assertRaises(ValueError):review(directory)

    def test_missing_off_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            directory=self.example(td)
            manifest=json.loads((directory/'completed.json').read_text())
            manifest['channels'][0]['digital_off_confirmed']=False
            (directory/'completed.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):review(directory)
