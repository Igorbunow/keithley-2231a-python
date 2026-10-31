"""R5-E10 software-only non-neutral trigger protocol and synthetic digital review.

No instrument connection, SCPI dispatch, hardware runner, or operator authorization.
An offline consistency result MUST NEVER be reported as a hardware qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import json
import math

from tools.plan_r5_e5_trigger_event import BOUNDED_EXAMPLE, ChannelBounds, PlanningError
from tools.plan_r5_e9_trigger_recipients import plan_non_neutral_off


REGISTER_QUERIES = ('VOLT?', 'CURR?', 'VOLT:TRIG?', 'CURR:TRIG?')


@dataclass(frozen=True)
class ChannelDigital:
    """One channel's four separately read digital registers and OFF readback.

    This is NOT a single atomic instrument snapshot or physical OFF evidence.
    """
    channel: int
    ordinary_voltage_v: float
    ordinary_current_a: float
    trigger_voltage_v: float
    trigger_current_a: float
    output_off_readback: bool


class ProtocolError(ValueError):
    """Invalid or unsafe offline protocol observation, not an instrument result."""


def _captured(rows: tuple[ChannelDigital, ...], bounds: tuple[ChannelBounds, ...]):
    if not isinstance(rows, (tuple, list)) or len(rows) != 3:
        raise ProtocolError('Exactly three channel digital captures are mandatory')
    if any(type(row) is not ChannelDigital or type(row.channel) is not int
           for row in rows):
        raise ProtocolError('Capture must use exact ChannelDigital with integer channel')
    by_channel = {row.channel: row for row in rows}
    if len(by_channel) != 3 or set(by_channel) != {1, 2, 3}:
        raise ProtocolError('Missing, duplicate or invalid channel capture')
    limits = {row.channel: row for row in bounds}
    for channel in (1, 2, 3):
        row = by_channel[channel]
        limit = limits[channel]
        if type(row.output_off_readback) is not bool or not row.output_off_readback:
            raise ProtocolError(f'CH{channel}: digital OFF readback unconfirmed')
        for key, maximum, quantum in (
            ('ordinary_voltage_v', limit.voltage_limit_v, .01),
            ('ordinary_current_a', limit.current_limit_a, .001),
            ('trigger_voltage_v', limit.voltage_limit_v, .01),
            ('trigger_current_a', limit.current_limit_a, .001),
        ):
            value = getattr(row, key)
            if (type(value) not in (float, int) or not math.isfinite(value)
                    or value < 0 or value > maximum
                    or abs(value / quantum - round(value / quantum)) >= 1e-6):
                raise ProtocolError(f'CH{channel}: invalid/off-grid {key}')
    return by_channel


def protocol_plan(channels: tuple[ChannelBounds, ...]) -> dict:
    """Describe mandatory readbacks and human gates; emit NO SCPI script."""
    risk_model = plan_non_neutral_off(channels)
    return {
        'stage': 'R5-E10',
        'status': 'SOFTWARE_PROTOCOL_DRAFT_NOT_AUTHORIZED',
        'event': 'NOT_SENT',
        'coupling': 'UNKNOWN_NOT_QUALIFIED',
        'all_channels_potential_recipients': [1, 2, 3],
        'capture_sets': ['original_before_any_mutation',
                         'staged_before_event', 'post_event', 'restored'],
        'registers_per_capture': [f'CH{ch}:{cmd}' for ch in (1, 2, 3)
                                  for cmd in REGISTER_QUERIES],
        'register_count_per_capture': 12,
        'additional_per_capture': ['CH1_OFF_READBACK', 'CH2_OFF_READBACK',
                                   'CH3_OFF_READBACK'],
        'sequence_review_only': [
            'separate_operator_bench_and_physical_OFF_approval',
            'capture_and_verify_12_original_registers_before_any_mutation',
            'review_restoration_of_original_ordinary_AND_trigger_registers',
            'stage_bounded_independent_OFF_profile_and_trigger_presets',
            'read_back_all_12_staged_registers_and_three_OFF_states',
            'separate_specific_one_bus_event_operator_approval',
            'at_most_one_global_bus_event_if_later_separately_authorized',
            'read_back_all_12_post_event_registers_and_three_OFF_states',
            'restore_only_with_certain_communication_and_preapproved_procedure',
            'read_back_all_12_original_registers_and_three_OFF_states',
            'operator_physical_final_OFF_and_original_raw_evidence',
            'independent_wire_order_and_JSONL_SHA256_review',
        ],
        'abort_policy': 'STOP_NO_RETRY_NO_BLIND_RESTORE_PHYSICAL_OFF_IF_UNCERTAIN',
        'no_coupling_queries_or_writes': True,
        'no_output_enable': True,
        'no_hardware_runner': True,
        'hardware_permission': False,
        'envelope': risk_model['validated_envelope'],
        'model_limitations': [
            'No default INST:COUP behavior, channel isolation or recipient proof',
            'Post-event readbacks are sequential, not atomic or analog measurements',
            'OFF readback and a host checkpoint cannot replace physical OFF or interlock',
            'Original ordinary AND trigger registers must be restored and verified',
            'A timed-out or uncertain transaction prevents speculative restoration',
            'Only an original independently audited JSONL can support hardware review',
        ],
    }


def assess_synthetic_digital_trace(
    channels: tuple[ChannelBounds, ...],
    original: tuple[ChannelDigital, ...],
    staged: tuple[ChannelDigital, ...],
    post_event: tuple[ChannelDigital, ...],
    restored: tuple[ChannelDigital, ...],
    *,
    injected_event_count: int,
    synthetic_link_certain: bool,
    synthetic_error_queue_clear: bool,
) -> dict:
    """Validate synthetic observations; never grant a live SCPI or HW permission.

    The caller's flags are MOCK assertions and are not independent evidence.
    Real evidence MUST be audited from original wire events by a distinct tool.
    """
    plan = plan_non_neutral_off(channels)
    bounds = tuple(sorted(channels, key=lambda row: row.channel))
    if type(injected_event_count) is not int or injected_event_count != 1:
        raise ProtocolError('Exactly one injected synthetic event required')
    if type(synthetic_link_certain) is not bool or not synthetic_link_certain:
        raise ProtocolError('Uncertain simulated link: no replay or blind restoration')
    if type(synthetic_error_queue_clear) is not bool or not synthetic_error_queue_clear:
        raise ProtocolError('Nonzero/unknown simulated error queue: abort')
    start = _captured(original, bounds)
    ready = _captured(staged, bounds)
    after = _captured(post_event, bounds)
    end = _captured(restored, bounds)

    for ch in (1, 2, 3):
        target = next(row for row in bounds if row.channel == ch)
        row = ready[ch]
        expected = (target.active_voltage_v, target.active_current_a,
                    target.trigger_voltage_v, target.trigger_current_a)
        actual = (row.ordinary_voltage_v, row.ordinary_current_a,
                  row.trigger_voltage_v, row.trigger_current_a)
        if actual != expected:
            raise ProtocolError(f'CH{ch}: staged registers differ from bounded plan')
        if (after[ch].trigger_voltage_v != row.trigger_voltage_v
                or after[ch].trigger_current_a != row.trigger_current_a):
            raise ProtocolError(f'CH{ch}: unexpected trigger preset mutation after event')
        if end[ch] != start[ch]:
            raise ProtocolError(f'CH{ch}: original ordinary/trigger registers not restored')

    # Digital register patterns alone are compatible with possible subsets;
    # they cannot establish actual coupling or an analog source transition.
    matching = []
    for bits in product((False, True), repeat=3):
        if all((after[ch].ordinary_voltage_v, after[ch].ordinary_current_a) ==
               ((ready[ch].trigger_voltage_v, ready[ch].trigger_current_a) if bit else
                (ready[ch].ordinary_voltage_v, ready[ch].ordinary_current_a))
               for ch, bit in zip((1, 2, 3), bits)):
            matching.append([ch for ch, bit in zip((1, 2, 3), bits) if bit])
    if not matching:
        raise ProtocolError('Post-event synthetic ordinary registers match no hypothesis')
    return {
        'stage': 'R5-E10',
        'status': 'SYNTHETIC_CONSISTENCY_ONLY_NOT_HW_REVIEW',
        'event': 'INJECTED_ONLY_NOT_SENT',
        'hardware_permission': False,
        'restoration': 'SYNTHETIC_VALUES_MATCH_ORIGINAL_NOT_PHYSICALLY_VERIFIED',
        'matching_hypothetical_recipient_sets': matching,
        'coupling': 'UNKNOWN_NOT_QUALIFIED',
        'analog': 'NOT_MEASURED',
        'wire_evidence': 'NOT_AUDITED',
        'required_registers_per_capture': 12,
        'source_model_status': plan['status'],
        'warning': 'Do not feed manually transcribed data as hardware proof.',
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(protocol_plan(BOUNDED_EXAMPLE), sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
