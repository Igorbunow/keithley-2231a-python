"""R3.4 offline topology-transition and regulation-evidence regression tests.

No hardware transport or qualification runner is used here.
"""
import unittest
from dataclasses import replace

from keithley2231a import (ChannelProfile, Keithley2231A, ParallelProfile,
                          SafetyError, SeriesProfile)
from support.qualification_transport import FakeTransport
from tests.test_driver import profile


class CombinedSource(FakeTransport):
    """Simulate only the aggregate Series limit-register context."""

    def write(self, command):
        super().write(command)
        if command == 'OUTP:SER 1':
            self.values[1]['VOLT:LIM'] = (
                self.values[1]['VOLT:LIM'] + self.values[2]['VOLT:LIM'])
            self.values[1]['VOLT:LIM:STAT'] = 0.


def series_profile():
    return SeriesProfile('series', (2., 3.), 4.5, .3, 2., .1,
                         ChannelProfile(4., .5, 1., .1))


def parallel_profile():
    return ParallelProfile('parallel', 3.0, .3, 2.0, .1,
                           ChannelProfile(4., .5, 1., .1))


class TopologyAndStatusTests(unittest.TestCase):
    def _inherited_parallel(self, source):
        """Represent an energized mode inherited from a previous process."""
        source.flags['OUTP:PARALLEL'] = 1.
        source.flags['OUTP:STAT:ALL'] = 1.
        for ch in (1, 2, 3):
            source.values[ch]['CHAN:OUTP'] = 1.

    def test_new_session_uses_actual_parallel_mode_for_all_target_topologies(self):
        for topology in ('independent', 'series', 'parallel'):
            with self.subTest(target=topology):
                source = CombinedSource()
                self._inherited_parallel(source)
                dev = Keithley2231A(source)
                if topology == 'independent':
                    requested, wiring = profile(), 'independent'
                elif topology == 'series':
                    requested, wiring = series_profile(), 'series'
                else:
                    requested, wiring = parallel_profile(), 'parallel'
                dev.apply_profile(requested, confirmed_wiring=wiring)
                commands = source.cmds
                inherited_query = commands.index('OUTP:PARALLEL?')
                shutdown = commands.index('OUTP 0', inherited_query)
                readback = commands.index('OUTP:STAT:ALL?', shutdown)
                clear_mode = commands.index('OUTP:PARALLEL 0', readback)
                self.assertLess(shutdown, readback)
                self.assertLess(readback, clear_mode)
                self.assertTrue(dev.armed)
                self.assertEqual(dev.output_states(), {1: False, 2: False, 3: False})

    def test_stuck_parallel_output_blocks_mode_change_and_setpoints(self):
        class RefusesOff(CombinedSource):
            def write(self, command):
                if command == 'OUTP 0' and self.flags['OUTP:PARALLEL']:
                    self.cmds.append(command)
                    return
                super().write(command)

        for topology in ('independent', 'series', 'parallel'):
            with self.subTest(target=topology):
                source = RefusesOff()
                self._inherited_parallel(source)
                dev = Keithley2231A(source)
                requested, wiring = {
                    'independent': (profile(), 'independent'),
                    'series': (series_profile(), 'series'),
                    'parallel': (parallel_profile(), 'parallel'),
                }[topology]
                with self.assertRaisesRegex(SafetyError, 'could not be switched OFF'):
                    dev.apply_profile(requested, confirmed_wiring=wiring)
                self.assertTrue(dev.faulted)
                self.assertFalse(dev.armed)
                self.assertIsNone(dev.profile)
                self.assertEqual(source.flags['OUTP:PARALLEL'], 1.)
                self.assertNotIn('OUTP:PARALLEL 0', source.cmds)
                self.assertFalse(any(cmd.startswith(('VOLT ', 'CURR ', 'VOLT:LIM '))
                                     for cmd in source.cmds))

    def test_malformed_parallel_off_acknowledgement_aborts_without_followup_io(self):
        class BadOffReply(CombinedSource):
            def query(self, command):
                if command == 'OUTP:STAT:ALL?':
                    self.cmds.append(command)
                    return 'not-a-boolean'
                return super().query(command)

        source = BadOffReply()
        self._inherited_parallel(source)
        dev = Keithley2231A(source)
        with self.assertRaises(Exception):
            dev.apply_profile(series_profile(), confirmed_wiring='series')
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)
        self.assertEqual(source.cmds[-1], 'OUTP:STAT:ALL?')
        self.assertNotIn('OUTP:PARALLEL 0', source.cmds)
        self.assertEqual(source.flags['OUTP:PARALLEL'], 1.)

    def test_calibration_bit_prevents_cv_cc_claim_even_when_bits_agree(self):
        source = FakeTransport()
        dev = Keithley2231A(source)
        dev.apply_profile(profile(), confirmed_wiring='independent')
        dev.output(3, True)
        source.operation_status_masks[3] = 13  # CV + CAL + ON
        source.questionable_status_masks[3] = 1  # CV
        reading = dev.read_channel(3)
        self.assertTrue(reading.enabled)
        self.assertEqual(reading.regulation, 'UNKNOWN')
        self.assertIsNone(reading.regulation_source)
        self.assertEqual((reading.operation_status_mask, reading.questionable_status_mask),
                         (13, 1))

    def test_required_regulation_rejects_calibration_condition(self):
        source = FakeTransport()
        settings = profile()
        settings = replace(settings, channels=(
            settings.channels[0], settings.channels[1],
            replace(settings.channels[2], expected_regulation='cv')))
        dev = Keithley2231A(source)
        dev.apply_profile(settings, confirmed_wiring='independent')
        dev.output(3, True)
        source.operation_status_masks[3] = 13
        source.questionable_status_masks[3] = 1
        with self.assertRaisesRegex(SafetyError, 'unexpected regulation'):
            dev.read_channel(3)
        self.assertTrue(dev.faulted)
        self.assertFalse(dev.armed)

    def test_series_conflicting_mode_evidence_stays_unknown_with_raw_masks(self):
        source = CombinedSource()
        dev = Keithley2231A(source)
        dev.apply_profile(series_profile(), confirmed_wiring='series')
        source.values[1]['VOLT:LIM:STAT'] = 0.  # Observed combined limitation
        dev.verify_profile()  # Must not claim an active combined hardware limiter.
        dev.combined_outputs(True, confirm_all_channels_safe=True)
        source.operation_status_masks[1] = 9  # OPER CV + ON
        source.questionable_status_masks[1] = 2  # QUES CC
        reading = dev.read_channel(1)
        self.assertEqual(reading.label, 'CH1+CH2')
        self.assertEqual(reading.regulation, 'UNKNOWN')
        self.assertEqual(reading.regulation_source, None)
        self.assertEqual((reading.operation_status_mask, reading.questionable_status_mask),
                         (9, 2))


if __name__ == '__main__':
    unittest.main()
