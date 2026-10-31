"""OFFLINE trigger-event risk planning ONLY. Never opens a transport or sends SCPI.

Coupling behavior is unqualified. All channels are treated as potentially
affected by any bus trigger; this is NOT a hardware execution procedure.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import json
import math

@dataclass(frozen=True)
class ChannelBounds:
    channel: int
    active_voltage_v: float
    active_current_a: float
    trigger_voltage_v: float
    trigger_current_a: float
    voltage_limit_v: float
    current_limit_a: float

BOUNDED_EXAMPLE = (
    ChannelBounds(1,3.30,.150,2.20,.120,3.50,.150),
    ChannelBounds(2,1.00,.100,.80,.080,1.50,.100),
    ChannelBounds(3,1.00,.100,.80,.080,1.50,.100),
)

class PlanningError(ValueError):
    pass

def plan_offline(channels):
    channels=tuple(channels)
    if len(channels)!=3 or {getattr(c,'channel',None) for c in channels}!={1,2,3}:
        raise PlanningError('All three distinct channels are mandatory; coupling unqualified')
    combined=0.
    examples=[]
    for c in sorted(channels,key=lambda c:c.channel):
        values=(c.active_voltage_v,c.active_current_a,c.trigger_voltage_v,
                c.trigger_current_a,c.voltage_limit_v,c.current_limit_a)
        if any(type(v) not in (float,int) or not math.isfinite(v) or v<0 for v in values):
            raise PlanningError('Unbounded/nonfinite trigger setpoint')
        max_voltage=5. if c.channel==3 else 30.
        if (not 0<c.voltage_limit_v<=max_voltage or not 0<c.current_limit_a<=3.
                or max(c.active_voltage_v,c.trigger_voltage_v)>c.voltage_limit_v
                or max(c.active_current_a,c.trigger_current_a)>c.current_limit_a):
            raise PlanningError('A channel would exceed its bounded OFF test profile')
        # Conservative setpoint envelope ONLY, not a measured dissipation or power protection.
        max_p=c.voltage_limit_v*c.current_limit_a
        combined+=max_p
        examples.append(dict(channel=c.channel,baseline_v=c.active_voltage_v,
                    baseline_a=c.active_current_a,trigger_v=c.trigger_voltage_v,
                    trigger_a=c.trigger_current_a,setpoint_envelope_w=max_p))
    return dict(status='DRY_RUN_NO_SCPI_NO_TRIGGER_AUTHORIZATION',
                potential_responders=[1,2,3],coupling='UNKNOWN_NOT_QUALIFIED',
                outputs='MUST_REMAIN_PHYSICALLY_OFF',
                all_channels_checked=True, channels=examples,
                aggregate_setpoint_envelope_w=round(combined,6),
                event='NOT_SENT',analog='NOT_MEASURED',
                note='Setpoint-envelope products are NOT hardware power limits or a measured protection.')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    print(json.dumps(plan_offline(BOUNDED_EXAMPLE),indent=2,sort_keys=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
