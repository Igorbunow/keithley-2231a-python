# Configuration and laboratory recipes

[English](RECIPES.md) | [Русский](RECIPES.ru.md)

## Basic design rules

- **Apply a profile with outputs OFF.** A profile must be immutable once armed.
- Modes **Series** and **Parallel** require physical **external** connections; SCPI only changes the instrument's combined-mode behavior/readback. Confirm wiring with `confirmed_wiring='series'` or `'parallel'` while OFF.
- CV/CC is automatically selected based on load; expected regulation is a diagnostic policy, not forced state.
- All examples in this document are templates and **HW UNVERIFIED** until accompanied by a hardware report. Never run with valuable DUTs without low-energy qualification.

## Three profile choices

| Mode | Construction | Permissions |
| --- | --- | --- |
| Limited & configurable | `Profile('name', (ChannelProfile(...), ...))` | Change setpoints only up to profile limits; output per channel |
| Full rated range | `Profile.unrestricted()` | All rated values (CH1/2 30 V/3 A; CH3 5 V/3 A), no extra user caps |
| Power only | `Profile(..., access='power_only', power_sequence=(PowerStep(...), ...))` | Frozen levels, predefined ON order, reverse OFF order; no arbitrary setpoint changes |

The `power_sequence` is an immutable tuple of `(channel, delay_before_enable_s)` represented as `PowerStep(channel, delay_s)`; no repeats. If the tuple is present, per-channel `output()` is forbidden. Without one, `power_on/off` use all three channels, and individual `output()` is permitted. The profile stores **initial** setpoints that get applied while outputs are OFF, which are then frozen in `power_only`.

### JSON example for an operator-only profile

File: [`examples/power_only.json`](../examples/power_only.json). Import with `Profile.from_json(...)` then `psu.apply_profile(...)`. Avoid distributing real-world, unreviewed limit values as universal safe defaults.

## Measurement logging

```python
from keithley2231a import Acquisition

log = Acquisition(psu, interval_s=2.0, csv_path='runs/power.csv')
log.run(duration_s=120)
log.export_summary('runs/power_summary.json')
print(log.stats[1].as_dict())
log.reset_statistics(1, reset_energy=False)
```

Records UTC/monotonic timestamps, actual voltage/current, inferred power, status, output state and setpoints. Statistics use Welford's algorithm. Ah/Wh use trapezoidal integration with actual intervals, excluding large gaps and output-OFF spans. Do not interpret it as battery discharge capacity or fast switching waveform capture.

## Example: static resistor or diode I–V curve

```python
from keithley2231a import voltage_sweep

# With an appropriately rated R in series with diode (for example 220 ohms),
# independently checked current limits and suitable instrument profile:
# points = voltage_sweep(psu, channel=1, voltages=[0.0, 0.5, 1.0, 1.5],
#                        current_limit_a=0.020, settle_s=0.5,
#                        csv_path='runs/diode.csv')
# for point in points:
#     u_diode_estimated = point.measured_v - point.measured_a * 220.0
```

No reverse-bias, negative current or two-quadrant sink capability exists. The expression above neglects wiring drop, DUT temperature and resistor tolerance; best practice: connect an external DMM directly across diode and put it in an observer callback. `voltage_sweep` causes global OFF on exit, and leaves the session disarmed. Check other channels are not powering unrelated loads before starting.

## Bias experiments with multiple channels / PSUs

```python
from keithley2231a import BenchCoordinator, BenchStep, BenchAction

# first and second are Keithley2231A objects with safe, armed profiles.
# meter_adapter defines measure() -> dict[str, float], and can wrap any DMM.
bench = BenchCoordinator({'source_a': first, 'source_b': second},
                         {'dmm': meter_adapter})
steps = (
    BenchStep(0, (BenchAction('source_a', 'set', channel=1, voltage=1.0),),
              capture=True, label='base'),
    BenchStep(1.0, (BenchAction('source_b', 'set', channel=1, voltage=2.0),),
              capture=True, label='biased'),
)
# captures = bench.run(steps)  # OFFs all supplies after completion by default
```

Callers can implement loops over several channel setpoints (transistor curves, sensor calibration or power-rail optimization) while sampling a DMM. Because each `set_channel()` is profile-checked, `power_only` devices reject `set` actions. To switch a `power_only` PSU use `BenchAction('source_a', 'power_on')` / `power_off`.

### Timing/synchronization warning

`BenchCoordinator` timestamps its actions/measurements and runs them **sequentially on the host**. It does **not** provide exact simultaneous operation, sub-ms timing or SCPI hardware triggers. Deterministic synchronization of DMMs, oscilloscopes and multiple supplies requires adapters with real hardware trigger wiring or PTP/shared clock and device-specific characterization. The instrument's `*TRG` capability is not yet qualified by this library.

## Future Ethernet adapter requirements

Expose a **single writer** raw TCP-to-UART link `host:port` (e.g., `psu-lab.local:5025`); maintain command/response framing and one-connection ownership. Implement a clearly documented idle/disconnect policy. Prefer an isolated LAN and hardware watch/kill circuit; **TCP close is not guaranteed to de-energize the PSU**.
