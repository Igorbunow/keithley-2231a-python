"""Offline checks for the bounded H11 I-V qualification stage."""
import sys
import inspect
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parent / 'hardware'))
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from h11_stage import H11Scope, approval_token, h11, profile_evidence
from keithley2231a import ChannelProfile, Profile, SafetyError


class TestH11Stage(unittest.TestCase):
    def test_gate_and_scope_are_bounded_to_the_approved_low_energy_sweep(self):
        self.assertIn('CR=40OHM', approval_token())
        self.assertIn('I_LIMIT=0.040A', approval_token())
        scope = H11Scope.__new__(H11Scope)
        scope.selected = 1
        scope.authorized_channel = None
        scope.outputs = {1: False, 2: False, 3: False}
        for command, query in (
                ('VOLT 2.50', False), ('CHAN:OUTP 1', False),
                ('OUTP 1', False), ('SYST:RESET', False)):
            with self.subTest(command=command), self.assertRaises((ValueError, SafetyError)):
                scope._validate(command, query)
        scope._validate('SYST:ERR?', True)
        scope.authorized_channel = 1
        scope._validate('CHAN:OUTP 1', False)

    def test_runner_reads_error_queue_through_library_diagnostic_api(self):
        source = inspect.getsource(h11)
        self.assertIn("dev.diagnostic_query('SYST:ERR?')", source)
        self.assertNotIn('io.query(', source)

    def test_profile_evidence_uses_public_channel_profile_fields(self):
        profile = Profile('test', (ChannelProfile(2.0, .1, .5, .04),
                                   ChannelProfile(3.0, .2, 1.5, .2),
                                   ChannelProfile(4.0, .3, 2.0, .3)))
        self.assertEqual(profile_evidence(profile)[0], {
            'max_voltage_v': 2.0, 'max_current_a': .1,
            'initial_voltage_v': .5, 'initial_current_a': .04})


if __name__ == '__main__':
    unittest.main()
