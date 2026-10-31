"""R5-E9 pure OFF-only recipient uncertainty model; no instrument I/O.

This is a conservative model of hypothetical digital setpoint changes, NOT a
parser for INST:COUP, an SCPI script, or a hardware qualification procedure.
"""
from __future__ import annotations

from itertools import product

from tools.plan_r5_e5_trigger_event import ChannelBounds, PlanningError, plan_offline


def _on_grid(value: float, quantum: float) -> bool:
    return abs(value / quantum - round(value / quantum)) < 1e-6


def plan_non_neutral_off(channels: tuple[ChannelBounds, ...]) -> dict:
    """Enumerate all 8 hypothetical recipient subsets, including no response.

    Reuse the R5-E5 three-channel physical/profile bounds. Neither selection
    nor coupling is assumed to isolate a channel. Real firmware could exhibit
    other behavior, so these alternatives are risk examples, not predictions.
    """
    channels = tuple(channels)
    if any(type(c) is not ChannelBounds or type(c.channel) is not int for c in channels):
        raise PlanningError('Expected exact ChannelBounds with integer channels')
    base = plan_offline(channels)
    ordered = tuple(sorted(channels, key=lambda c: c.channel))
    for c in ordered:
        for value, quantum in ((c.active_voltage_v, .01), (c.trigger_voltage_v, .01),
                               (c.active_current_a, .001), (c.trigger_current_a, .001)):
            if not _on_grid(value, quantum):
                raise PlanningError(f'CH{c.channel}: OFF digital plan violates native V/I grid')
    changed = tuple(c.channel for c in ordered if
                    (c.active_voltage_v != c.trigger_voltage_v or
                     c.active_current_a != c.trigger_current_a))
    if not changed:
        raise PlanningError('Non-neutral analysis requires at least one changed preset')

    # Every subset is a hypothetical responder set, NOT a claim that any such
    # set is configurable by the undocumented coupling syntax or firmware.
    hypotheses = []
    for bits in product((False, True), repeat=3):
        responders = [c.channel for c, bit in zip(ordered, bits) if bit]
        digital_after = [
            dict(channel=c.channel,
                 voltage_v=c.trigger_voltage_v if bit else c.active_voltage_v,
                 current_a=c.trigger_current_a if bit else c.active_current_a)
            for c, bit in zip(ordered, bits)
        ]
        hypotheses.append(dict(hypothetical_recipients=responders,
                               hypothetical_ordinary_registers=digital_after))
    return {
        'stage': 'R5-E9',
        'status': 'SOFTWARE_ONLY_MODEL_NO_SCPI_NO_HARDWARE_AUTHORIZATION',
        'coupling': 'UNKNOWN_NOT_QUALIFIED',
        'event': 'NOT_SENT',
        'outputs': 'MUST_REMAIN_PHYSICALLY_OFF',
        'all_potential_recipients': [1, 2, 3],
        'potentially_changed_channels': list(changed),
        'hypothesis_count': len(hypotheses),
        'hypothetical_outcomes': hypotheses,
        'validated_envelope': base,
        'model_limitations': [
            'Manual query arguments, reply grammar and grouping semantics unresolved',
            'No assumption that INST:NSEL isolates recipients of a global bus trigger',
            'Hypothetical ordinary register changes are not guaranteed SCPI behavior',
            'No analog, timing, synchronization or digital actuation evidence',
            'A real run must independently capture all original ordinary and TRIG registers',
            'A lost response after *TRG forbids blind replay or automatic restoration',
            'Setpoint product is not independent hardware protection',
        ],
    }
