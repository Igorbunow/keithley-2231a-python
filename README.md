# keithley-2231a-python

[**English**](README.md) | [**Русский**](docs/README.ru.md)

**Python control library for the Keithley 2231A-30-3 three-channel DC power supply.**
Provides SCPI over USB–TTL UART, optional transparent TCP–UART bridges, immutable safety profiles, per-channel sequencing, measurement logs/statistics, DC voltage sweeps (including I–V curves), and extensible multi-instrument lab orchestration.

> **Experimental software / hardware qualification in progress.** A successful simulator/CI run does not validate an output command on a real instrument. Physical qualification records list the instrument's menu firmware as Main 1.04 / Aux 0.01; raw `*IDN?` revision fields have returned both `1.04-1.04` and `1.04-0.01`. Firmware is reported and recorded, not hard-coded as an API compatibility gate. See [verification matrix](docs/VERIFICATION.md) before powering a DUT. This is NOT a safety-rated protection system.

**Version:** 0.2.0 · **Python:** 3.10+ · **License:** [MIT](LICENSE) · **Manufacturer affiliation:** none (independent community project).

## Why this library?

- **Three independent channels:** CH1/CH2: 0–30 V, 0–3 A; CH3: 0–5 V, 0–3 A. 195 W nameplate combined output rating.
- **Series / parallel / tracking:** API support, with explicit physical-wiring confirmation for Series/Parallel. **SCPI does NOT insert external series/parallel jumpers or switch the output terminals internally.** Connections must be installed and inspected **while outputs are off**. The 2231A combined modes also change metering semantics; read the vendor manual.
- **Conservative profiles:** per-channel V/I bounds, hardware voltage limit setting, output-off-first profile arming, optional CV/CC expectation and periodic integrity checks.
- **Full-rated-range profile:** `Profile.unrestricted()` allows all **rated** V/I settings, without extra user-specific reduced caps; it is **not** a raw SCPI bypass or permission to exceed instrument ratings.
- **Power-only profile:** output levels and topology are frozen; operators may switch power on/off. Fixed `power_sequence` enables channels in order and disables in reverse. No arbitrary channel toggles when a sequence is specified.
- **Measurement and automation:** per-channel CSV logs, min/max/mean/population σ, Ah/Wh integration; guarded voltage sweeps with optional DMM callback; multiple PSUs and meters via host-side scheduler.
- **Replaceable transports:** UART `SerialTransport('/dev/ttyUSB1')`, future MCU bridge `TcpTransport('psu-lab.local', 5025)`; TCP is a transparent UART tunnel, **not native Ethernet, VISA or VXI-11**.

## Hardware and safety first

1. Rear DB9 is **TTL-level UART**, NOT ±RS-232. Use a correctly configured and wired USB–TTL adapter; never connect a conventional RS-232 dongle directly. The instrument serial protocol uses 9600 baud, 8N1.
2. The PSU output is **two-terminal only**, without remote Kelvin sensing. Measure voltage at DUT terminals with a separate DMM when lead drop matters.
3. **Series/Parallel require external power wiring.** Software can only configure the instrument's combined control/measurement modes. Confirm terminal polarity, load, cable current capability and the vendor wiring diagram before arming.
4. Power supply CV/CC is selected dynamically based on load and V/I setpoints; `expected_regulation='cv'`/`'cc'` means **monitor and trip on mismatch**, not force an internal regulator mode.
5. Front-panel locks, periodic polling, hardware `VOLT:LIM`, and SCPI OFF are **not independent electrical protection**. In particular, when serial/TCP communication fails, OFF may not reach the PSU. Use external interlocks, fuses, supervision and appropriate protective circuits for unattended or risky tests.
6. Check the instrument **Output Recall = Off** setting after a power-cycle; otherwise it may restore powered outputs without the Python controller.
7. **The PSU cannot sink current.** Do not use it as a battery electronic load, directly connect a charged battery, or infer battery discharge capacity from PSU-delivered Ah. Sweep scripts must use safe current limits and appropriate DUT series resistors.
8. Never use test scripts on valuable loads until their SCPI commands and transitions have been qualified on dummy loads.

## Instrument identity and firmware

The driver validates manufacturer and model but does not pin the firmware revision.
The firmware field is retained in the raw `instrument_id` response and qualification
evidence. For benches with multiple identical supplies, bind a driver instance to the
expected serial number; a mismatch is rejected during identity checking before profile
settings are written:

```python
psu = Keithley2231A(SerialTransport('/dev/ttyUSB1'), expected_serial='12345678')
print(psu.identity())  # raw *IDN? response, including the firmware revision
print(psu.instrument_key)  # stable manufacturer/model/serial tuple
```

Omit `expected_serial` when serial binding is not needed. Firmware behavior remains
qualification-specific: record the version shown in the instrument menu and the raw
`*IDN?` revision in every hardware report; a new revision still needs hardware review.

[Keithley/Tektronix official reference manual (PDF)](https://download.tek.com/manual/077100401_Referencemanual.pdf) · [Upstream product documentation](https://www.tek.com/en/tektronix-and-keithley-dc-power-supplies/keithley-2220-2230-2231-series-manual-0)

## CH3 status workaround and limitations

CH3 remains available through the normal profile, `set_channel(3, ...)`,
`output(3, ...)` and `read_channel(3)` APIs. On the tested instrument (menu Main 1.04 /
Aux 0.01), its operation condition reports **8** in both CV and CC: the ON bit
is present but the documented mode bits are absent. Its questionable condition
correctly reports **1 (CV)** or **2 (CC)**. The library accepts an unambiguous mode
from either condition register when the other has no mode bits. No opt-in or
channel-specific fabricated status is needed.

```python
# Existing armed profile; this call does not enable the output.
reading = psu.read_channel(3)
print(reading.enabled, reading.regulation, reading.regulation_source)
print(reading.operation_status_mask, reading.questionable_status_mask)
# Observed ON/CV case: True, 'CV', 'questionable'; raw masks 8, 1.
```

`Reading.regulation_source` is `'operation'`, `'questionable'`, or `'both'` for a
resolved ON mode; it is `None` for OFF, UNKNOWN or skipped regulation checks.
`enabled` is independently read with `CHAN:OUTP?`. ON/OFF integrity checks use the
operation ON bit, which worked in the observed CH3 runs. Successful writes and a
clear error queue alone are never treated as proof of output state or regulation.
Both condition queries must succeed; communication failures still trip. Conflicting
or ambiguous modes remain UNKNOWN; a required `expected_regulation='cv'/'cc'`
therefore still trips on mismatch or UNKNOWN. CV/CC is determined by load and V/I
setpoints, not forced by that profile setting. Raw masks remain unchanged.

Limits: CH3 is rated **0–5 V, 0–3 A**, constrained further by the active profile.
Physical evidence covers independent CV at 4 V / 0.500 A with CR 40 ohms and CC at
4 V / 0.250 A with CR 12 ohms, plus individual ON/OFF and setpoint readbacks.
This does not qualify the full rated range, CH3 timers or all later stages.
There is no redundant OPER confirmation of CH3's CV/CC on this firmware.
The newly exposed source field and additional regressions are **SIM TESTED**
(66/66 offline tests passed); the underlying QUES-based mode reading was already
physically observed. The source field itself has not been replayed on hardware.

The [reference manual](https://download.tek.com/manual/077100401_Referencemanual.pdf)
sections 5-20/21 and the status model describe group event/summary registers, not a
single response containing all three channels' complete live CV/CC states. Use
per-channel `...ISUM1/2/3:COND?` queries; group event reads are not a substitute.
`Acquisition.sample()` already polls all three independent channels sequentially,
with separate timestamps; it is not an atomic snapshot. No new group command is
assumed. See the [physical diagnosis](docs/hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md)
and [verification matrix](docs/VERIFICATION.md). H03's manual-conformance result
remains PARTIAL while manufacturer clarification is pending.

## Installation and offline tests

```bash
git clone https://github.com/<YOUR-ACCOUNT>/keithley-2231a-python.git
cd keithley-2231a-python
python3 -m venv .venv
source .venv/bin/activate                  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

`git clone` URL is a placeholder until **you** create the repository. There is currently no claimed PyPI release. Plotting is optional: `python -m pip install -e '.[plot]'`.

### First connection: read-only

```bash
python examples/probe.py --port /dev/ttyUSB1
```

The probe does not enable outputs or set V/I. The actual instrument communication result already obtained by the project owner is `*IDN? -> Keithley instruments, 2231A-30-3, ..., 1.04-1.04` using a TTL-USB adapter. **Nothing else is yet hardware-qualified.**

## Examples

### Guarded operation (output stays OFF during profile setup)

```python
from keithley2231a import Keithley2231A, SerialTransport, Profile

with Keithley2231A(SerialTransport('/dev/ttyUSB1')) as psu:
    psu.apply_profile(Profile.from_json('examples/safe_3ch.json'))
    psu.verify_profile()
    psu.set_channel(1, voltage=1.20, current=0.10)
    # Explicit human check of load/wiring required before psu.output(1, True)
```

`apply_profile` **immediately switches ALL outputs OFF** and rewrites configuration. Don't run while powering a device.

### Full rated range; no reduced application limits

```python
profile = Profile.unrestricted()       # CH1/2 ≤30 V/3 A, CH3 ≤5 V/3 A
# psu.apply_profile(profile)           # intentionally OFF-first
# psu.set_channel(1, voltage=12.0, current=1.0)
```

### Operator profile (power only)

```python
from keithley2231a import Profile, ChannelProfile, PowerStep

profile = Profile(
    name='digital-board', access='power_only', wiring='independent',
    lock_front_panel=True,
    channels=(
        ChannelProfile(3.3, .15, initial_voltage=3.3, initial_current=.10),
        ChannelProfile(5.0, .25, initial_voltage=5.0, initial_current=.20),
        ChannelProfile(3.3, .10, initial_voltage=0.0, initial_current=.05),
    ),
    power_sequence=(PowerStep(1, 0.0), PowerStep(2, 0.5)),
)
# psu.apply_profile(profile)  # all outputs OFF
# psu.power_on()              # CH1, wait 0.5 s, CH2
# psu.power_off()             # CH2, wait 0.5 s, CH1
# set_channel(), timer(), arbitrary sequence() are forbidden while armed
```

If the power sequence is empty, `power_on()/power_off()` use CH1→CH2→CH3 / reverse order, and individual `output(channel, enabled)` remains permitted. CH3 is not enabled in the explicit two-step example.

### DC I–V characteristics and external meters

```python
from keithley2231a import voltage_sweep

# After an appropriate guarded profile has been armed; with a safe resistive DUT:
# points = voltage_sweep(psu, 1, [0.0, 0.1, 0.2],
#                        current_limit_a=0.020, settle_s=0.3,
#                        observer=lambda reading: {'dmm_v': external_dmm.read_voltage()},
#                        csv_path='runs/iv.csv')
```

Uncomment only after validating the test circuit and the external meter API. The source is stepped in DC voltage; PSU current and voltage are acquired sequentially. **It is not an SMU or high-speed curve tracer.** `voltage_sweep()` requires other channels OFF and attempts global OFF in `finally`, even after successful completion. For powered bias sources, use the multi-instrument coordinator instead. For diodes include a **series resistor** and measure DUT voltage directly with a DMM or estimate `Vdiode ≈ Vpsu − I·R` (lead drops ignored).

### Many PSUs and other makers' instruments

Use `BenchCoordinator({'psu-a': first, 'psu-b': second}, {'dmm': adapter})` with any external meter implementing `measure() -> dict[str, float]`; `BenchStep` / `BenchAction` automate settings and capture. Every reading carries timestamps, but **software coordination cannot provide true simultaneous switching or synchronous sampling**. External trigger adapters/hardware timing are a future extension. See [recipes](docs/RECIPES.md).

### UART vs future Ethernet bridge

```python
from keithley2231a import SerialTransport, TcpTransport
serial_io = SerialTransport('/dev/ttyUSB1', timeout=3.0)
serial_io.close()
# A future MCU with a transparent single-client bridge:
# network_io = TcpTransport('psu-lab.local', 5025, timeout=3.0)
```

The MCU needs correct framing, single-writer ownership, TCP half-open detection and an **independent** failsafe if physical protection is required. Do **not** expose raw SCPI TCP directly to the Internet. Reconnecting does not imply outputs are OFF.

## Documentation

| English | Русский |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | [Архитектура](docs/ARCHITECTURE.ru.md) |
| [Configuration & recipes](docs/RECIPES.md) | [Профили и примеры](docs/RECIPES.ru.md) |
| [Hardware verification matrix](docs/VERIFICATION.md) | [Матрица аппаратной проверки](docs/VERIFICATION.ru.md) |
| [Hardware test procedure](docs/HARDWARE_TESTS.md) | [Протокол испытаний](docs/HARDWARE_TESTS.ru.md) |
| [Contributing](CONTRIBUTING.md) | [Участие в разработке](docs/CONTRIBUTING.ru.md) |
| [Roadmap](ROADMAP.md) | [Дорожная карта](docs/ROADMAP.ru.md) |

## Status and roadmap

- **HW OBSERVED:** serial UART link, `SYST:REMote`, `*IDN?` on firmware 1.04-1.04; repeatable formal verification is still needed.
- **SIM TESTED:** protected profile operations, host sequencing, serial/TCP protocol models, statistics, sweep, multi-instrument coordination (30 simulator tests in current source tree).
- **HW UNVERIFIED:** changing setpoints, outputs, voltage limits, timers, CV/CC status bit mappings, and series/parallel/tracking behaviours on real hardware.
- **PLANNED:** hardware-trigger coordination, MCU network bridge firmware, true concurrently timestamped multi-meter acquisitions, generic vendor driver protocol, more complete safe SCPI coverage, and additional instrument integration tests.

Do not merge hardware verification claims based solely on CI green status. Hardware reports go under [`docs/hardware_reports/`](docs/hardware_reports/README.md).

## Licensing, trademarks and contributions

Original code and documentation are MIT-licensed; see [LICENSE](LICENSE). Third-party dependencies retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Keithley and Tektronix are trademarks of their respective owners. **This is an independent, unofficial library**, not approved, supported or warranted by either manufacturer.

Contributions and hardware test results welcome: [CONTRIBUTING.md](CONTRIBUTING.md).
