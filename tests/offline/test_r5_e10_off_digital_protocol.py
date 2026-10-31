"""R5-E10 software protocol controls and synthetic negatives; no hardware I/O."""
from __future__ import annotations

from dataclasses import replace
import contextlib
from io import StringIO
import itertools
import json
import math
import unittest

from tools.plan_r5_e5_trigger_event import BOUNDED_EXAMPLE, PlanningError
from tools.plan_r5_e10_off_digital_protocol import (
    ChannelDigital, ProtocolError, REGISTER_QUERIES,
    assess_synthetic_digital_trace, main, protocol_plan,
)


def capture(bounds, *, source='plan'):
    if source == 'original':
        return tuple(ChannelDigital(b.channel, .5, .05, .4, .04, True)
                     for b in bounds)
    return tuple(ChannelDigital(b.channel, b.active_voltage_v, b.active_current_a,
                                b.trigger_voltage_v, b.trigger_current_a, True)
                 for b in bounds)


def hypothetical_post(staged, recipients):
    return tuple(replace(row,
                         ordinary_voltage_v=(row.trigger_voltage_v if row.channel in recipients
                                             else row.ordinary_voltage_v),
                         ordinary_current_a=(row.trigger_current_a if row.channel in recipients
                                             else row.ordinary_current_a))
                 for row in staged)


class R5E10DigitalProtocolOffline(unittest.TestCase):
    def setUp(self):
        self.bounds = BOUNDED_EXAMPLE
        self.original = capture(self.bounds, source='original')
        self.staged = capture(self.bounds)
        self.post = hypothetical_post(self.staged, (1, 3))

    def review(self, **overrides):
        values = dict(channels=self.bounds, original=self.original,
                      staged=self.staged, post_event=self.post,
                      restored=self.original, injected_event_count=1,
                      synthetic_link_certain=True, synthetic_error_queue_clear=True)
        values.update(overrides)
        return assess_synthetic_digital_trace(**values)

    def test_protocol_requires_four_sets_of_twelve_and_off_readbacks(self):
        report = protocol_plan(self.bounds)
        self.assertEqual(report['status'], 'SOFTWARE_PROTOCOL_DRAFT_NOT_AUTHORIZED')
        self.assertEqual(report['event'], 'NOT_SENT')
        self.assertFalse(report['hardware_permission'])
        self.assertEqual(report['register_count_per_capture'], 12)
        self.assertEqual(len(report['registers_per_capture']), 12)
        self.assertEqual(len(set(report['registers_per_capture'])), 12)
        self.assertEqual(report['registers_per_capture'],
                         [f'CH{ch}:{cmd}' for ch in (1, 2, 3) for cmd in REGISTER_QUERIES])
        self.assertEqual(len(report['capture_sets']), 4)
        self.assertEqual(len(report['additional_per_capture']), 3)

    def test_no_hardware_executor_and_restoration_gate(self):
        report = protocol_plan(self.bounds)
        self.assertTrue(report['no_coupling_queries_or_writes'])
        self.assertTrue(report['no_output_enable'])
        self.assertTrue(report['no_hardware_runner'])
        self.assertIn('restore_only_with_certain_communication_and_preapproved_procedure',
                      report['sequence_review_only'])
        self.assertIn('independent_wire_order_and_JSONL_SHA256_review',
                      report['sequence_review_only'])
        self.assertTrue(any('physical OFF' in x for x in report['model_limitations']))
        self.assertNotIn('port', report)
        self.assertNotIn('execute', report)

    def test_all_eight_synthetic_patterns_not_coupling_evidence(self):
        for bits in itertools.product((False, True), repeat=3):
            recipients = {ch for ch, bit in zip((1, 2, 3), bits) if bit}
            with self.subTest(recipients=recipients):
                outcome = self.review(post_event=hypothetical_post(self.staged, recipients))
                self.assertEqual(outcome['matching_hypothetical_recipient_sets'],
                                 [sorted(recipients)])
                self.assertEqual(outcome['status'],
                                 'SYNTHETIC_CONSISTENCY_ONLY_NOT_HW_REVIEW')
                self.assertEqual(outcome['event'], 'INJECTED_ONLY_NOT_SENT')
                self.assertEqual(outcome['coupling'], 'UNKNOWN_NOT_QUALIFIED')
                self.assertFalse(outcome['hardware_permission'])

    def test_unresolvable_recipients_stay_ambiguous(self):
        from dataclasses import replace as edit
        neutral = tuple(edit(b, trigger_voltage_v=b.active_voltage_v,
                             trigger_current_a=b.active_current_a)
                        for b in self.bounds[1:])
        bounds = (self.bounds[0],) + neutral
        staged = capture(bounds)
        result = self.review(channels=bounds, staged=staged,
                             post_event=hypothetical_post(staged, (1,)))
        self.assertEqual(len(result['matching_hypothetical_recipient_sets']), 4)
        self.assertIn([1], result['matching_hypothetical_recipient_sets'])
        self.assertIn([1, 2, 3], result['matching_hypothetical_recipient_sets'])

    def test_non_neutral_plan_is_required(self):
        bounds = tuple(replace(b, trigger_voltage_v=b.active_voltage_v,
                               trigger_current_a=b.active_current_a) for b in self.bounds)
        with self.assertRaises(PlanningError):
            protocol_plan(bounds)

    def test_staged_mismatch_denied(self):
        wrong = (replace(self.staged[0], ordinary_voltage_v=3.31),) + self.staged[1:]
        with self.assertRaisesRegex(ProtocolError, 'staged registers'):
            self.review(staged=wrong)

    def test_restored_ordinary_mismatch_denied(self):
        wrong = (replace(self.original[0], ordinary_current_a=.051),) + self.original[1:]
        with self.assertRaisesRegex(ProtocolError, 'not restored'):
            self.review(restored=wrong)

    def test_restored_trigger_mismatch_denied(self):
        wrong = self.original[:2] + (replace(self.original[2], trigger_voltage_v=.41),)
        with self.assertRaisesRegex(ProtocolError, 'not restored'):
            self.review(restored=wrong)

    def test_post_trigger_preset_modified_denied(self):
        wrong = self.post[:1] + (replace(self.post[1], trigger_current_a=.081),) + self.post[2:]
        with self.assertRaisesRegex(ProtocolError, 'preset mutation'):
            self.review(post_event=wrong)

    def test_unmodeled_post_setpoint_denied(self):
        wrong = self.post[:1] + (replace(self.post[1], ordinary_voltage_v=1.01),) + self.post[2:]
        with self.assertRaisesRegex(ProtocolError, 'no hypothesis'):
            self.review(post_event=wrong)

    def test_one_and_only_one_synthetic_event(self):
        for count in (0, 2, -1, True, 1.0, None):
            with self.subTest(count=count), self.assertRaisesRegex(ProtocolError,
                                                                  'Exactly one'):
                self.review(injected_event_count=count)

    def test_uncertain_link_denied_before_restoration(self):
        for uncertain in (False, 1, None, 'true'):
            with self.subTest(uncertain=uncertain), self.assertRaisesRegex(
                    ProtocolError, 'Uncertain simulated link'):
                self.review(synthetic_link_certain=uncertain)

    def test_nonzero_or_unknown_error_queue_denied(self):
        for state in (False, 0, None, '0'):
            with self.subTest(state=state), self.assertRaisesRegex(
                    ProtocolError, 'error queue'):
                self.review(synthetic_error_queue_clear=state)

    def test_missing_duplicate_and_reordered_channel_handling(self):
        self.assertEqual(self.review(original=tuple(reversed(self.original)))['status'],
                         'SYNTHETIC_CONSISTENCY_ONLY_NOT_HW_REVIEW')
        for bad in (self.original[:2], self.original + self.original[:1],
                    (self.original[0], self.original[0], self.original[2]),
                    (replace(self.original[0], channel=True),) + self.original[1:],
                    (replace(self.original[0], channel=1.0),) + self.original[1:]):
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                self.review(original=bad)

    def test_all_capture_phases_require_off_readback(self):
        for phase in ('original', 'staged', 'post_event', 'restored'):
            original = (self.post if phase == 'post_event' else self.original if phase == 'restored'
                        else getattr(self, phase))
            bad = original[:1] + (replace(original[1], output_off_readback=False),) + original[2:]
            with self.subTest(phase=phase), self.assertRaisesRegex(ProtocolError, 'OFF'):
                self.review(**{phase: bad})

    def test_off_readback_is_exact_boolean(self):
        for value in (1, None, 'OFF'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                self.review(original=(replace(self.original[0], output_off_readback=value),)
                            + self.original[1:])

    def test_nonnumeric_nonfinite_and_bools_denied(self):
        for value in (True, float('nan'), math.inf, -1, '0.5'):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                self.review(original=(replace(self.original[0], ordinary_voltage_v=value),)
                            + self.original[1:])

    def test_off_grid_ordinary_and_trigger_denied(self):
        for field, value in (('ordinary_voltage_v', .505),
                             ('ordinary_current_a', .0505),
                             ('trigger_voltage_v', .405),
                             ('trigger_current_a', .0405)):
            with self.subTest(field=field), self.assertRaisesRegex(ProtocolError, 'off-grid'):
                self.review(original=(replace(self.original[0], **{field: value}),)
                            + self.original[1:])

    def test_out_of_bounded_envelope_denied_even_for_originals(self):
        for field, value in (('ordinary_voltage_v', 29.0),
                             ('ordinary_current_a', 1.0),
                             ('trigger_voltage_v', 5.0),
                             ('trigger_current_a', .5)):
            with self.subTest(field=field), self.assertRaises(ProtocolError):
                self.review(original=(replace(self.original[0], **{field: value}),)
                            + self.original[1:])

    def test_cli_is_print_only_and_requires_no_communication_configuration(self):
        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(main([]), 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report['status'], 'SOFTWARE_PROTOCOL_DRAFT_NOT_AUTHORIZED')
        self.assertFalse(report['hardware_permission'])
        for candidate in ('port', 'host', 'run', 'execute', 'output_dir'):
            self.assertNotIn(candidate, report)

    def test_inputs_are_not_mutated(self):
        baseline = (self.original, self.staged, self.post)
        output = self.review()
        output['matching_hypothetical_recipient_sets'][0].append(999)
        self.assertEqual(self.review()['matching_hypothetical_recipient_sets'], [[1, 3]])
        self.assertEqual((self.original, self.staged, self.post), baseline)


if __name__ == '__main__':
    unittest.main()
