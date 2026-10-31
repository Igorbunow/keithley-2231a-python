"""R5-C: static SCPI evidence scopes cannot become runtime authorization."""
import json
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from keithley2231a import Keithley2231A, get_capabilities
from keithley2231a.capabilities import HardwareEvidence, InstrumentFeature


class CapabilityScopeTests(unittest.TestCase):
    def setUp(self):
        self.caps = get_capabilities()

    def test_schema_is_explicitly_versioned(self):
        self.assertEqual(self.caps.schema_version, 2)
        self.assertEqual(self.caps.to_dict()['schema_version'], 2)

    def test_readback_is_scoped_to_reviewed_channels_and_kinds(self):
        f = self.caps.feature('source_step_query')
        self.assertTrue(f.instrument_native and f.driver_exposed)
        self.assertEqual(f.evidence, 'bounded_hw')
        self.assertEqual({(s.channels, s.operation, s.output_state) for s in f.hardware_evidence}, {
            ((1, 2, 3), 'voltage_step_readback', 'OFF'),
            ((1, 2, 3), 'current_step_readback', 'OFF'),
        })

    def test_off_configuration_is_not_a_powered_qualification(self):
        f = self.caps.feature('source_step_off_configuration')
        self.assertTrue(f.driver_exposed)
        self.assertTrue(all(s.output_state == 'OFF' and s.regulation is None for s in f.hardware_evidence))
        self.assertEqual({s.observation for s in f.hardware_evidence}, {'digital_readback'})

    def test_off_mutation_has_independent_scope(self):
        f = self.caps.feature('source_step_off_mutation')
        self.assertEqual({s.operation for s in f.hardware_evidence},
                         {'voltage_step_up_down', 'current_step_up_down'})
        self.assertFalse(any(s.output_state == 'POWERED' for s in f.hardware_evidence))

    def test_powered_step_includes_all_channels_and_historical_ch1(self):
        f = self.caps.feature('source_step_powered_qualification')
        self.assertTrue(f.instrument_native and f.driver_exposed)
        self.assertEqual({(s.channels, s.regulation, s.operation) for s in f.hardware_evidence}, {
            ((1,), 'CV', 'voltage_step_up_down'),
            ((1,), 'CC', 'current_step_up_down'),
            ((1, 2, 3), 'CV', 'voltage_step_up_down'),
            ((1, 2, 3), 'CC', 'current_step_up_down'),
        })
        self.assertTrue(all(s.output_state == 'POWERED' for s in f.hardware_evidence))
        self.assertTrue(all(s.observation == 'analog_steady_state' for s in f.hardware_evidence))
        self.assertIn('Qualification context only', f.constraints[0])

    def test_evidence_references_are_actual_bundled_docs(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        for f in self.caps.features:
            for e in f.hardware_evidence:
                with self.subTest(feature=f.name, reference=e.reference):
                    self.assertTrue((root / e.reference).is_file())

    def test_trigger_and_bulk_families_remain_inaccessible(self):
        for name in ('hardware_triggered_setpoint', 'trigger_channel_coupling',
                     'bulk_apply_setpoints', 'enable_channels_function'):
            with self.subTest(name=name):
                f = self.caps.feature(name)
                self.assertTrue(f.instrument_native)
                self.assertFalse(f.driver_exposed)
                self.assertEqual(f.evidence, 'documented_only')
                self.assertEqual(f.hardware_evidence, ())

    def test_capabilities_are_passive_and_transport_free(self):
        with patch('keithley2231a.transport.SerialTransport', side_effect=AssertionError('port opened')):
            c = Keithley2231A.capabilities()
            self.assertIs(c, self.caps)
            self.assertEqual(c.to_dict()['schema_version'], 2)

    def test_live_state_is_not_inferred_from_static_snapshot(self):
        d = self.caps.to_dict()
        self.assertNotIn('connected', d)
        self.assertNotIn('outputs_enabled', d)
        self.assertNotIn('firmware_verified', d)
        self.assertFalse(d['readback']['current_nominal_accuracy']['calibrated_here'])

    def test_scope_copy_mutation_isolated(self):
        d = self.caps.to_dict()
        d['features'][0]['hardware_evidence'].append({'bad': True})
        f = next(f for f in d['features'] if f['name'] == 'source_step_query')
        f['hardware_evidence'][0]['channels'].append(3)
        self.assertEqual(self.caps.feature('source_step_query').hardware_evidence[0].channels, (1, 2, 3))
        json.dumps(self.caps.to_dict(), allow_nan=False)

    def test_frozen_evidence(self):
        e = self.caps.feature('source_step_query').hardware_evidence[0]
        with self.assertRaises(FrozenInstanceError):
            e.channels = (1, 2, 3)

    def test_reject_invalid_scope_channel_and_output(self):
        for channels in ((), (0,), (4,), (True,), (1, 1), ('1',)):
            with self.subTest(channels=channels), self.assertRaises(ValueError):
                HardwareEvidence(channels, 'readback', 'OFF', None, 'digital_readback', 'source')
        with self.assertRaises(ValueError):
            HardwareEvidence((1,), 'readback', 'UNKNOWN', None, 'digital_readback', 'source')

    def test_reject_invalid_scope_regulation_and_observation(self):
        with self.assertRaises(ValueError):
            HardwareEvidence((1,), 'readback', 'OFF', 'CV', 'digital_readback', 'source')
        with self.assertRaises(ValueError):
            HardwareEvidence((1,), 'readback', 'POWERED', 'UNKNOWN', 'analog_steady_state', 'source')
        with self.assertRaises(ValueError):
            HardwareEvidence((1,), 'readback', 'POWERED', 'CV', 'calibrated', 'source')

    def test_reject_invented_bounded_hardware_for_unexposed_feature(self):
        e = HardwareEvidence((1,), 'foo', 'OFF', None, 'digital_readback', 'source')
        for native, exposed, evidence in ((True, False, 'bounded_hw'),
                                          (False, True, 'bounded_hw'),
                                          (True, True, 'simulated_only')):
            with self.subTest(native=native, exposed=exposed, evidence=evidence):
                with self.assertRaises(ValueError):
                    InstrumentFeature('foo', native, exposed, evidence, hardware_evidence=(e,))

    def test_absent_and_service_are_not_upgraded(self):
        for name in ('native_iv_sweep', 'atomic_voltage_current_sample',
                     'native_ethernet', 'safe_off_on_link_loss'):
            f = self.caps.feature(name)
            self.assertFalse(f.instrument_native or f.driver_exposed)
            self.assertEqual(f.evidence, 'absent')
            self.assertFalse(f.hardware_evidence)

    def test_serialized_published_scope_has_no_authorization_flag(self):
        d = self.caps.to_dict()
        feature = next(f for f in d['features'] if f['name'] == 'source_step_powered_qualification')
        self.assertEqual(len(feature['hardware_evidence']), 4)
        self.assertEqual({tuple(x['channels']) for x in feature['hardware_evidence']}, {(1,), (1, 2, 3)})
        self.assertFalse(any('authorization' in x or 'may_execute' in x for x in feature['hardware_evidence']))


if __name__ == '__main__':
    unittest.main()
