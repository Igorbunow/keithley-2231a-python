"""R5-A: capability descriptions have no instrument I/O or inferred calibration."""
import json
import math
import unittest
from dataclasses import FrozenInstanceError

from keithley2231a import Keithley2231A, Profile, get_capabilities
from keithley2231a.capabilities import KEITHLEY_2231A_CAPABILITIES


class StaticCapabilityTests(unittest.TestCase):
    def test_get_capabilities_requires_no_transport(self):
        self.assertIs(get_capabilities(), Keithley2231A.capabilities())

    def test_model_and_identity(self):
        c = get_capabilities()
        self.assertEqual(c.schema_version, 2)
        self.assertEqual(c.model, 'Keithley 2231A-30-3')
        self.assertIs(c, KEITHLEY_2231A_CAPABILITIES)

    def test_rated_ranges_match_guarded_profile(self):
        c = get_capabilities()
        p = Profile.unrestricted()
        self.assertEqual([x.number for x in c.channels], [1, 2, 3])
        for ch in c.channels:
            limits = p.channels[ch.number - 1]
            self.assertEqual(ch.rated_voltage_v, limits.max_voltage)
            self.assertEqual(ch.rated_current_a, limits.max_current)
            self.assertEqual(ch.voltage_setting_step_v, 0.01)
            self.assertEqual(ch.current_setting_step_a, .001)

    def test_channel_access_is_strict(self):
        c = get_capabilities()
        for key in [True, 0, 4, '1', 1.0, None]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.channel(key)
        self.assertEqual(c.channel(3).rated_voltage_v, 5.0)

    def test_immutable_dataclasses(self):
        c = get_capabilities()
        with self.assertRaises(FrozenInstanceError):
            c.model = 'modified'
        with self.assertRaises(FrozenInstanceError):
            c.channels[0].rated_current_a = 10
        with self.assertRaises(FrozenInstanceError):
            c.features[0].instrument_native = False

    def test_json_compatible_snapshot_is_a_copy(self):
        c = get_capabilities()
        d = c.to_dict()
        json.dumps(d, allow_nan=False)
        d['channels'][0]['rated_voltage_v'] = 999
        d['features'][0]['constraints'].append('untrusted')
        d['notes'].append('override')
        self.assertEqual(c.channel(1).rated_voltage_v, 30)
        self.assertNotIn('untrusted', c.features[0].constraints)
        self.assertNotIn('override', c.to_dict()['notes'])

    def test_feature_names_unique_and_evidence_valid(self):
        c = get_capabilities()
        self.assertEqual(len(c.features), len(set(x.name for x in c.features)))
        self.assertTrue(all(f.evidence in {'bounded_hw','simulated_only','documented_only','absent'} for f in c.features))
        for f in c.features:
            if f.evidence == 'absent':
                self.assertFalse(f.driver_exposed)
                self.assertFalse(f.instrument_native)
            if f.evidence == 'documented_only':
                self.assertFalse(f.driver_exposed)

    def test_trigger_is_documented_not_qualified(self):
        f = get_capabilities().feature('hardware_triggered_setpoint')
        self.assertTrue(f.instrument_native)
        self.assertFalse(f.driver_exposed)
        self.assertEqual(f.evidence, 'documented_only')

    def test_sweep_is_host_not_hardware(self):
        c = get_capabilities()
        self.assertFalse(c.feature('native_iv_sweep').instrument_native)
        self.assertFalse(c.feature('native_iv_sweep').driver_exposed)
        self.assertFalse(c.feature('atomic_voltage_current_sample').instrument_native)

    def test_transparent_tcp_bridge_is_not_native_ethernet(self):
        self.assertFalse(get_capabilities().feature('native_ethernet').instrument_native)

    def test_series_parallel_require_physical_wiring(self):
        for mode in ('series','parallel'):
            c = get_capabilities().feature(mode)
            self.assertTrue(c.instrument_native)
            self.assertTrue(c.driver_exposed)
            self.assertIn('physical jumpers required', c.constraints)

    def test_current_readback_not_precision_meter(self):
        c = get_capabilities()
        self.assertEqual(c.readback_current_nominal_resolution_a, .001)
        spec = c.current_readback_nominal_accuracy
        self.assertAlmostEqual(spec.nominal_absolute_bound(.003), .010006)
        self.assertEqual(spec.nominal_temperature_c, (20, 30))
        self.assertFalse(c.to_dict()['readback']['current_nominal_accuracy']['calibrated_here'])

    def test_voltage_readback_reading_is_conditional(self):
        c = get_capabilities()
        spec = c.voltage_readback_nominal_accuracy
        self.assertAlmostEqual(spec.nominal_absolute_bound(1.0), .0206)
        self.assertIn('Static model', c.notes[0])
        self.assertFalse(c.to_dict()['readback']['readings_atomic'])

    def test_accuracy_rejects_nonfinite(self):
        spec = get_capabilities().current_readback_nominal_accuracy
        for val in (float('nan'), float('inf'), -float('inf'), True, '1'):
            with self.subTest(val=val), self.assertRaises(ValueError):
                spec.nominal_absolute_bound(val)
        self.assertAlmostEqual(spec.nominal_absolute_bound(-.002), .010004)

    def test_unknown_feature_rejected_without_fallback(self):
        for item in ('', 'native_visa', None, 4):
            with self.subTest(item=item), self.assertRaises(ValueError):
                get_capabilities().feature(item)

    def test_no_automatic_hw_qualification_in_metadata(self):
        c = get_capabilities()
        self.assertFalse(any('calibrated' in f.evidence for f in c.features))
        self.assertFalse(c.to_dict()['readback']['current_nominal_accuracy']['calibrated_here'])
        self.assertFalse(c.feature('safe_off_on_link_loss').instrument_native)


if __name__ == '__main__':
    unittest.main()
