"""SIMULATOR ONLY: no real output is switched by these tests."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_driver import FakeTransport, profile
from keithley2231a import (Profile, ChannelProfile, PowerStep, ProfileError,
                            LimitsError, Keithley2231A, SafetyError, voltage_sweep,
                            BenchCoordinator, BenchStep, BenchAction)


def power_profile(steps=()):
    p = profile()
    return Profile('locked-rack', p.channels, access='power_only',
                   power_sequence=tuple(steps), lock_front_panel=True)

class TestAccessProfiles(unittest.TestCase):
    def test_unrestricted_is_rated_not_raw(self):
        p = Profile.unrestricted()
        self.assertEqual([(x.max_voltage,x.max_current) for x in p.channels],
                         [(30,3),(30,3),(5,3)])
        fake=FakeTransport(); psu=Keithley2231A(fake); psu.apply_profile(p)
        psu.set_channel(1, voltage=30, current=3)
        psu.set_channel(3, voltage=5, current=3)
        with self.assertRaises(LimitsError): psu.set_channel(3, voltage=5.01)
        with self.assertRaises(LimitsError): psu.set_channel(1, voltage=30.01)

    def test_sequence_validation(self):
        with self.assertRaises(ProfileError): PowerStep(4)
        with self.assertRaises(ProfileError): PowerStep(1,float('nan'))
        with self.assertRaises(ProfileError): power_profile([PowerStep(1), PowerStep(1)])
        p=profile(wiring='tracking')
        with self.assertRaises(ProfileError): Profile('bad', p.channels, wiring='tracking', access='power_only')

    def test_lockdown_blocks_setpoints_arbitrary_sequences_and_timers(self):
        fake=FakeTransport(); psu=Keithley2231A(fake)
        psu.apply_profile(power_profile([PowerStep(2,.01),PowerStep(1,.02)]))
        for action in (lambda: psu.set_channel(1, voltage=1.5),
                       lambda: psu.output(1, True),
                       lambda: psu.sequence([(0,1,True)]),
                       lambda: psu.timer(1,1),
                       lambda: psu.remote(lock_panel=False),
                       lambda: psu.remote(lock_panel=True),
                       lambda: psu.beep()):
            before = len(fake.cmds)
            with self.assertRaises(SafetyError): action()
            self.assertEqual(len(fake.cmds), before)
        with patch('keithley2231a.device.time.sleep') as sleeper:
            psu.power_on()
            self.assertEqual([x for x in fake.cmds if x == 'CHAN:OUTP 1'], ['CHAN:OUTP 1']*2)
            # First CH2, then CH1
            starts = [i for i,c in enumerate(fake.cmds) if c == 'CHAN:OUTP 1']
            self.assertEqual(fake.cmds[starts[0]-1], 'INST:NSEL 2')
            self.assertEqual(fake.cmds[starts[1]-1], 'INST:NSEL 1')
            psu.power_off()
            stops = [i for i,c in enumerate(fake.cmds) if c == 'CHAN:OUTP 0']
            self.assertEqual(fake.cmds[stops[0]-1], 'INST:NSEL 1')
            self.assertEqual(fake.cmds[stops[1]-1], 'INST:NSEL 2')
            self.assertEqual(sleeper.call_count,4)

    def test_json_profile_power_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'profile.json'
            f.write_text('''{"name":"test","access":"power_only","channels":{
                "1":{"max_voltage":3.3,"max_current":0.2},
                "2":{"max_voltage":5,"max_current":0.2},
                "3":{"max_voltage":3.3,"max_current":0.2}},
                "power_sequence":[{"channel":1,"delay_s":0.0},{"channel":3,"delay_s":0.2}]}''')
            p=Profile.from_json(f)
            self.assertEqual(p.access,'power_only')
            self.assertEqual(p.power_sequence[1],PowerStep(3,.2))

    def test_power_on_failure_trips_and_off(self):
        fake=FakeTransport(); psu=Keithley2231A(fake)
        psu.apply_profile(power_profile([PowerStep(1,0),PowerStep(2,0)]))
        fake.failed_query='CHAN:OUTP?'
        with self.assertRaises(SafetyError): psu.power_on()
        self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)
        self.assertFalse(psu.armed)

class TestSweep(unittest.TestCase):
    def test_sweep_csv_and_stop_off(self):
        fake=FakeTransport(); psu=Keithley2231A(fake); psu.apply_profile(profile())
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'curve.csv'
            with patch('keithley2231a.sweep.time.sleep'):
                pts=voltage_sweep(psu,1,[0.1,0.2,0.3],current_limit_a=.02,
                                  observer=lambda r: {'dmm_v':r.voltage_v-.01},csv_path=path)
            self.assertEqual(len(pts),3)
            self.assertEqual(pts[1].requested_v, .2)
            self.assertIn('dmm_v',path.read_text())
            self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)
            self.assertFalse(psu.armed)

    def test_sweep_requires_safe_access_and_limits(self):
        psu=Keithley2231A(FakeTransport()); psu.apply_profile(power_profile())
        with self.assertRaises(SafetyError): voltage_sweep(psu,1,[1],current_limit_a=.01)
        psu.emergency_off(); psu.apply_profile(profile())
        with self.assertRaises(ValueError): voltage_sweep(psu,1,[4],current_limit_a=.01)
        self.assertTrue(psu.armed) # preflight rejects without interacting

    def test_sweep_rejects_other_live_channels(self):
        fake=FakeTransport(); psu=Keithley2231A(fake); psu.apply_profile(profile())
        psu.output(2, True)
        before=len(fake.cmds)
        with self.assertRaises(SafetyError): voltage_sweep(psu,1,[.2],current_limit_a=.02)
        self.assertEqual(fake.flags['OUTP:STAT:ALL'],0) # Fake global flag ignores individual ON
        self.assertTrue(fake.values[2]['CHAN:OUTP']) # No unintended global OFF on preflight

    def test_observer_exception_causes_off(self):
        fake=FakeTransport(); psu=Keithley2231A(fake); psu.apply_profile(profile())
        with patch('keithley2231a.sweep.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'dmm failed'):
                voltage_sweep(psu,1,[.2],current_limit_a=.02,
                              observer=lambda r: (_ for _ in ()).throw(RuntimeError('dmm failed')))
        self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)

class Meter:
    def measure(self):return {'V':1.25}

class TestBench(unittest.TestCase):
    def test_two_supplies_dmm(self):
        one=FakeTransport(); two=FakeTransport()
        a=Keithley2231A(one); b=Keithley2231A(two)
        a.apply_profile(profile());b.apply_profile(profile())
        session=BenchCoordinator({'a':a,'b':b},{'dmm':Meter()})
        with patch('keithley2231a.bench.time.sleep'):
            captures=session.run((BenchStep(0,(BenchAction('a','output',1,enabled=True),),True,'run'),))
        self.assertEqual(captures[0].meter_readings['dmm']['V'],1.25)
        self.assertEqual(set(captures[0].supply_readings),{'a','b'})
        self.assertEqual(one.flags['OUTP:STAT:ALL'],0)
        self.assertEqual(two.flags['OUTP:STAT:ALL'],0)

    def test_bench_exception_forces_off(self):
        fake=FakeTransport(); psu=Keithley2231A(fake);psu.apply_profile(profile())
        bench=BenchCoordinator({'psu':psu})
        with patch('keithley2231a.bench.time.sleep'):
            with self.assertRaises(ValueError):
                bench.run([BenchStep(0,(BenchAction('psu','bad'),))])
        self.assertEqual(fake.flags['OUTP:STAT:ALL'],0)

if __name__=='__main__': unittest.main()
