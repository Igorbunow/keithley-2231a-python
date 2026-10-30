"""Bounded, application-side I-V example profile (NOT PSU driver capability).

This parser is pure: it cannot contact instruments. The profile's ``force``
only accepts low-resolution PSU readback for an explicitly diagnostic curve;
it never overrides power, wiring, or current limits.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


class IVProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ChannelPlan:
    channel: int
    voltages: tuple[float, ...]
    current_limit_a: float
    ballast_ohm: float
    ballast_rating_w: float
    required_error_ma: float
    requested_source: str
    force: bool
    selected_source: str
    nominal_psu_readback_error_ma: float


@dataclass(frozen=True)
class IVPlan:
    name: str
    settle_s: float
    channels: tuple[ChannelPlan, ...]


def _num(v, name):
    if type(v) not in (int, float) or not math.isfinite(v):
        raise IVProfileError(f'{name}: finite numeric value required')
    return float(v)


def _millivolts(v, name):
    x = _num(v, name)
    if abs(x * 100 - round(x * 100)) > 1e-7:
        raise IVProfileError(f'{name}: PSU voltage resolution is 0.01 V')
    return x


def _voltage_list(spec):
    if ('points_v' in spec) == ('range_v' in spec):
        raise IVProfileError('Provide exactly one of points_v and range_v')
    if 'points_v' in spec:
        points = spec['points_v']
        if type(points) is not list or not (1 <= len(points) <= 100):
            raise IVProfileError('points_v must have 1..100 points')
        return tuple(_millivolts(v, 'points_v') for v in points)
    raw = spec['range_v']
    if type(raw) is not dict or set(raw) != {'start_v', 'stop_v', 'step_v'}:
        raise IVProfileError('range_v requires start_v, stop_v, step_v')
    start, stop, step = (_millivolts(raw[k], k) for k in
                         ('start_v', 'stop_v', 'step_v'))
    if start < 0 or stop < start or step <= 0:
        raise IVProfileError('range_v must ascend with a positive step')
    s, t, d = (int(round(x * 100)) for x in (start, stop, step))
    if (t - s) % d:
        raise IVProfileError('step_v must exactly reach stop_v; no implicit rounding')
    n = (t - s) // d + 1
    if n > 100:
        raise IVProfileError('range_v exceeds 100 points')
    return tuple((s + i * d) / 100 for i in range(n))


def _strict_pairs(pairs):
    result = {}
    for key, val in pairs:
        if key in result:
            raise IVProfileError(f'Duplicate profile key: {key}')
        result[key] = val
    return result


def load_iv_profile(filepath: str | Path) -> IVPlan:
    """Validate the entire JSON before any device is opened or output enabled."""
    try:
        payload = json.loads(Path(filepath).read_text(encoding='utf-8'),
                             object_pairs_hook=_strict_pairs,
                             parse_constant=lambda x: (_ for _ in ()).throw(IVProfileError(x)))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IVProfileError(f'Invalid IV profile: {exc}') from exc
    if type(payload) is not dict or set(payload) != {'schema', 'name', 'settle_s', 'channels'}:
        raise IVProfileError('Expected schema, name, settle_s and channels only')
    if type(payload['schema']) is not int or payload['schema'] != 1:
        raise IVProfileError('Unsupported IV example profile schema')
    name = payload['name']
    if type(name) is not str or not (1 <= len(name.strip()) <= 80):
        raise IVProfileError('Nonempty name up to 80 characters required')
    settle = _num(payload['settle_s'], 'settle_s')
    if not 0 <= settle <= 5:
        raise IVProfileError('settle_s outside 0..5 s')
    raw_channels = payload['channels']
    if type(raw_channels) is not list or not (1 <= len(raw_channels) <= 3):
        raise IVProfileError('Select one to three independent channels')
    parsed = []
    used = set()
    expected = {'channel', 'current_limit_a', 'ballast_ohm', 'ballast_rating_w',
                'max_current_error_ma', 'current_source', 'force'}
    for spec in raw_channels:
        if type(spec) is not dict or not (set(spec) == expected | {'points_v'} or
                                       set(spec) == expected | {'range_v'}):
            raise IVProfileError('Unknown/missing channel-profile fields')
        ch = spec['channel']
        if type(ch) is not int or ch not in (1, 2, 3) or ch in used:
            raise IVProfileError('Each independent channel 1/2/3 must appear once')
        used.add(ch)
        voltages = _voltage_list(spec)
        vmax = max(voltages)
        if min(voltages) < 0 or vmax <= 0 or vmax > 5.0:
            raise IVProfileError('Example voltages must remain in (0, 5] V envelope')
        limit = _num(spec['current_limit_a'], 'current_limit_a')
        if not (.001 <= limit <= .05) or abs(limit * 1000 - round(limit * 1000)) > 1e-7:
            raise IVProfileError('Current limit must be 1..50 mA at 1 mA resolution')
        r = _num(spec['ballast_ohm'], 'ballast_ohm')
        watts = _num(spec['ballast_rating_w'], 'ballast_rating_w')
        if not (1 <= r <= 100000 and .25 <= watts <= 100):
            raise IVProfileError('Invalid resistor resistance/rating')
        if vmax / r > limit + 1e-12:
            raise IVProfileError('Even a shorted diode must be current-limited by ballast')
        if vmax * vmax / r > watts:
            raise IVProfileError('Ballast exceeds its rated power with shorted diode')
        required = _num(spec['max_current_error_ma'], 'max_current_error_ma')
        if not (0 < required <= 1000):
            raise IVProfileError('Current error target must be positive')
        requested = spec['current_source']
        if requested not in ('auto', 'psu', 'external', 'ballast_dcv'):
            raise IVProfileError('current_source must be auto, psu, external or ballast_dcv')
        force = spec['force']
        if type(force) is not bool:
            raise IVProfileError('force must be a JSON boolean')
        # Vendor's nominal PSU readback: ±(0.2% of reading + 10 mA) at 25±5 C.
        # A conservative maximum within the example's ballast/current envelope.
        psu_bound = 10.0 + .002 * min(1000 * limit, 1000 * vmax / r)
        suitable = psu_bound <= required
        if requested == 'ballast_dcv':
            # Voltage-readback alone contributes this lower uncertainty bound.
            # Other errors (DMM, resistor, lead drops, timing) remain unqualified.
            voltage_readback_floor_ma = (0.020 + 0.0006 * vmax) / r * 1000
            if required < voltage_readback_floor_ma:
                raise IVProfileError('Requested current error below nominal PSU voltage readback floor')
            if force:
                raise IVProfileError('force not permitted with direct diode DCV')
            selected = 'EXTERNAL_DIODE_DCV_BALLAST'
        elif requested == 'external':
            if force:
                raise IVProfileError('force cannot override an explicit external source')
            selected = 'EXTERNAL_REQUIRED'
        elif suitable or force:
            selected = 'PSU_READBACK_DIAGNOSTIC'
        elif requested == 'psu':
            raise IVProfileError('PSU readback exceeds required accuracy; use force=true or external')
        else:
            selected = 'EXTERNAL_REQUIRED'
        parsed.append(ChannelPlan(ch, voltages, limit, r, watts, required,
                                  requested, force, selected, psu_bound))
    return IVPlan(name.strip(), settle, tuple(parsed))


def describe(plan: IVPlan) -> dict:
    return {
        'name': plan.name,
        'scope': 'EXAMPLE_ONLY_NOT_INSTRUMENT_CALIBRATION',
        'sweep_type': 'HOST_TIMED_SEQUENTIAL_CHANNELS',
        'meter_accuracy_verified': False,
        'psu_readback_spec_condition': '25 +/- 5 C; not verified by this profile',
        'channels': [{
            'channel': c.channel, 'voltages_v': list(c.voltages),
            'current_limit_a': c.current_limit_a, 'selected_source': c.selected_source,
            'required_error_ma': c.required_error_ma,
            'nominal_psu_readback_error_ma': c.nominal_psu_readback_error_ma,
            'force': c.force, 'accuracy_qualified': False,
        } for c in plan.channels],
    }
