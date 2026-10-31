"""R5-E9 pure analysis and fail-closed tests: no device connection or SCPI."""
from __future__ import annotations

from dataclasses import replace
import itertools
import math
import unittest

from keithley2231a.device import Keithley2231A
from tools.plan_r5_e5_trigger_event import BOUNDED_EXAMPLE, PlanningError
from tools.plan_r5_e9_trigger_recipients import plan_non_neutral_off


class R5E9OfflineRecipientModel(unittest.TestCase):
    def test_all_eight_subsets_with_no_channel_isolation_assumption(self):
        result = plan_non_neutral_off(BOUNDED_EXAMPLE)
        self.assertEqual(result['status'],
                         'SOFTWARE_ONLY_MODEL_NO_SCPI_NO_HARDWARE_AUTHORIZATION')
        self.assertEqual(result['event'], 'NOT_SENT')
        self.assertEqual(result['coupling'], 'UNKNOWN_NOT_QUALIFIED')
        self.assertEqual(result['all_potential_recipients'], [1, 2, 3])
        self.assertEqual(result['potentially_changed_channels'], [1, 2, 3])
        self.assertEqual(result['hypothesis_count'], 8)
        seen = {tuple(x['hypothetical_recipients'])
                for x in result['hypothetical_outcomes']}
        self.assertEqual(seen, {tuple(k for k, take in zip((1, 2, 3), bits) if take)
                                for bits in itertools.product((False, True), repeat=3)})
        self.assertIn((), seen)
        self.assertIn((1, 2, 3), seen)
        self.assertFalse(any(k in result for k in ('port', 'host', 'execute', 'run')))

    def test_hypothetical_values_are_per_channel_and_not_measured(self):
        out = plan_non_neutral_off(BOUNDED_EXAMPLE)
        for possibility in out['hypothetical_outcomes']:
            recipients = set(possibility['hypothetical_recipients'])
            for row, original in zip(possibility['hypothetical_ordinary_registers'],
                                     BOUNDED_EXAMPLE):
                self.assertEqual(row['channel'], original.channel)
                use_trigger = original.channel in recipients
                self.assertEqual(row['voltage_v'], original.trigger_voltage_v if use_trigger
                                 else original.active_voltage_v)
                self.assertEqual(row['current_a'], original.trigger_current_a if use_trigger
                                 else original.active_current_a)
        self.assertTrue(any('not guaranteed' in x for x in out['model_limitations']))

    def test_partially_neutral_must_still_consider_every_channel(self):
        rows = (BOUNDED_EXAMPLE[0],
                replace(BOUNDED_EXAMPLE[1], trigger_voltage_v=1., trigger_current_a=.1),
                replace(BOUNDED_EXAMPLE[2], trigger_voltage_v=1., trigger_current_a=.1))
        out = plan_non_neutral_off(rows)
        self.assertEqual(out['potentially_changed_channels'], [1])
        self.assertEqual(len(out['hypothetical_outcomes']), 8)
        self.assertEqual(out['all_potential_recipients'], [1, 2, 3])

    def test_neutral_does_not_pretend_to_qualify_non_neutral_event(self):
        rows = tuple(replace(c, trigger_voltage_v=c.active_voltage_v,
                             trigger_current_a=c.active_current_a) for c in BOUNDED_EXAMPLE)
        with self.assertRaisesRegex(PlanningError, 'Non-neutral'):
            plan_non_neutral_off(rows)

    def test_channel_metadata_and_missing_channels_rejected(self):
        for rows in (BOUNDED_EXAMPLE[:2], BOUNDED_EXAMPLE + BOUNDED_EXAMPLE[:1],
                     (BOUNDED_EXAMPLE[0], BOUNDED_EXAMPLE[0], BOUNDED_EXAMPLE[2]),
                     (replace(BOUNDED_EXAMPLE[0], channel=True),) + BOUNDED_EXAMPLE[1:],
                     (replace(BOUNDED_EXAMPLE[0], channel=1.0),) + BOUNDED_EXAMPLE[1:]):
            with self.subTest(rows=rows), self.assertRaises(PlanningError):
                plan_non_neutral_off(rows)

    def test_ch3_and_other_channel_profile_bounds_remain_mandatory(self):
        for rows in (
            (replace(BOUNDED_EXAMPLE[0], trigger_voltage_v=3.6),) + BOUNDED_EXAMPLE[1:],
            BOUNDED_EXAMPLE[:2] + (replace(BOUNDED_EXAMPLE[2], trigger_voltage_v=5.01,
                                                  voltage_limit_v=5.),),
            BOUNDED_EXAMPLE[:1] + (replace(BOUNDED_EXAMPLE[1], trigger_current_a=.101),)
                                  + BOUNDED_EXAMPLE[2:],
            BOUNDED_EXAMPLE[:2] + (replace(BOUNDED_EXAMPLE[2], voltage_limit_v=6.),),
        ):
            with self.subTest(rows=rows), self.assertRaises(PlanningError):
                plan_non_neutral_off(rows)

    def test_nonfinite_negative_boolean_values_rejected(self):
        for bad in (float('nan'), math.inf, -1., True):
            with self.subTest(bad=bad):
                rows = BOUNDED_EXAMPLE[:2] + (replace(BOUNDED_EXAMPLE[2],
                                                     trigger_voltage_v=bad),)
                with self.assertRaises(PlanningError):
                    plan_non_neutral_off(rows)

    def test_off_grid_values_rejected_even_inside_profile_limits(self):
        for name, bad in (('trigger_voltage_v', .805), ('active_voltage_v', 1.005),
                          ('trigger_current_a', .0805), ('active_current_a', .0995)):
            with self.subTest(name=name), self.assertRaisesRegex(PlanningError, 'grid'):
                row = replace(BOUNDED_EXAMPLE[1], **{name: bad})
                plan_non_neutral_off((BOUNDED_EXAMPLE[0], row, BOUNDED_EXAMPLE[2]))

    def test_no_coupling_command_form_becomes_a_debug_bypass_route(self):
        # Check production policy, not an independently invented SCPI parser.
        for command in ('INST:COUP?', 'INST:COUP? CH1', 'INST:COUP CH1.CH2',
                        'INST:COUP:TRIG?', 'INST:COUP:TRIG? CH3',
                        'INSTrument:COUPle:TRIGger? CH2',
                        'INSTrument:COUPle CH1', 'INST:COUP:TRIG CH1.CH3'):
            with self.subTest(command=command):
                self.assertTrue(Keithley2231A._service_only_command(command))

    def test_pure_planning_does_not_modify_input(self):
        frozen = tuple(BOUNDED_EXAMPLE)
        a = plan_non_neutral_off(frozen)
        a['hypothetical_outcomes'][0]['hypothetical_ordinary_registers'][0]['voltage_v'] = 999
        b = plan_non_neutral_off(frozen)
        self.assertNotEqual(b['hypothetical_outcomes'][0]['hypothetical_ordinary_registers'][0]
                            ['voltage_v'], 999)
        self.assertEqual(tuple(BOUNDED_EXAMPLE), frozen)


if __name__ == '__main__':
    unittest.main()
