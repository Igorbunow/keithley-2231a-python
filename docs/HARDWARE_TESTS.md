# Hardware qualification procedure (operator-run, never CI)

[English](HARDWARE_TESTS.md) | [Русский](HARDWARE_TESTS.ru.md) · [Feature matrix](VERIFICATION.md)


> Current qualification follows the [compact H01–H12/O01/O02 matrix](hardware_reports/qualification_matrix_2026-10-25.md)
> and [offline audit / H01 gate](hardware_reports/2026-10-25_offline_audit.md).
> Use `python3 tools/hw_qualify.py preflight H01` first. Explicit operator approval
> is required before any serial access and separately for every powered group,
> rewiring, mains cycle or live-link interruption. Begin with offline I/O failure
> simulation; H07 is a 2–5 minute run. Diode DUTs and longer stress runs need
> separate approval. The overview below does not authorize those actions.

H02: [OFF-only profile operator card](H02_PREFLIGHT.ru.md). Its baseline profile
application was physically observed. The pending protection replay uses distinct
CH1/CH2/CH3 setpoints and caps: V=`1.00/1.50/2.00 V`, I=`0.100/0.200/0.300 A`,
V-cap=`2.00/3.00/4.00 V`, software I-cap=`0.300/0.400/0.500 A`. It checks panel
clamp, API soft rejection before SCPI and an explicitly gated instrument rejection.
These new checks are offline tested only and await their own hardware gate.

### Cross-channel value rule

For a multi-channel qualification, assign distinct, safe initial voltage setpoints,
current setpoints and protection/profile limits to each channel whenever the test
allows it. Record the CH1→CH3 mapping in the operator token and evidence. This makes
channel-selection, write-routing and readback mix-ups visible. If an electrical
comparison requires equal values, retain that equal-value point and add a separate
varied channel check; do not use identical values as the only cross-channel check.
Never vary values beyond the limits of the hardware profile or the documented test.

H03: [KEL103 load and transition operator card](H03_PREFLIGHT.ru.md). CH1, CH2 and CH3
CV/CC points have physical measurements and live display confirmations. The last CH3 run
compared both Keithley/KEL103 readings within tolerance and recorded 192 `SYST:ERR?`
replies of `0, "No error"`. However, CH3 returned operation status `8` in both CV and CC,
while questionable status returned `1` (CV) and `2` (CC). This contradicts the manual's
mode-bit definition for both channel summaries; status interpretation remains unresolved
and H03 is PARTIAL. All channels and KEL103 INPUT were read and confirmed OFF. The corrected library now returns CV/CC from questionable status on physical CH3 replay, while retaining operation status `8` as an unqualified manual discrepancy. H03 remains PARTIAL. All outputs/load were confirmed OFF. See the
[latest H03 report](hardware_reports/2026-10-26T003341.714219Z_H03_qualification.md).

The next CH3 resume aborted before configuration: KEL103 UDP `*IDN?` timed out;
cleanup then read `-200, "Execution error"` after `OUTP 0`. The error response cannot
identify its originating command. Emergency shutdown now enters Remote first and passed
the 56/56 offline suite; physical replay is pending fresh approval. See the
[attempt report](hardware_reports/2026-10-25T233455.502881Z_H03_qualification.md).

The H03 runner now supports an explicitly selected KEL103 USB CDC serial link at
`/dev/ttyACM0`, 115200 8N1. Offline tests cover LF framing and command allowlisting.
The baud/framing are community-derived. An authorized attempt physically read KEL103
V3.30 and INPUT OFF over USB; CR setup and powered points remain unverified. H03 then
stopped on error 170 because the queue was not sampled before `SYST:REM`; attribution is
unknown. The runner now records that baseline before any mutation. Automatic LAN-to-USB
failover is disabled because delivery can be uncertain.

**WARNING:** writing `apply_profile`, `set_channel`, `OUTP` or `power_on()` can turn off a running DUT, change output voltage or energize terminals. No unattended execution; test only with qualified operators. This file provides procedure suggestions, not proof of completed tests or a safety guarantee.

## Stage 0 — bench preparation

- Disconnect expensive DUTs and batteries. Use appropriate resistor loads and an independently calibrated DMM; for current/short tests use rated electronic loads rather than deliberate destructive shorts.
- Check mains input range and protective earth; confirm instrument front-panel **Output Recall Off** and physical manual OFF accessible.
- Start with all outputs OFF; physically inspect TTL DB9 wiring; record model/firmware/repository git commit. Configure serial 9600 8N1 or a trusted single-client bridge.
- Choose an independent safe test profile with low voltage/current; document every proposed limit versus dummy-load capability.
- Capture *both* SCPI results and independent terminal voltage/current. Do not expose serial number or credentials in public reports.

## Stage 1 — no-write communication and firmware compatibility

```bash
python examples/probe.py --port /dev/ttyUSB1
```

Record `*IDN?`, SCPI version, selected channel readbacks, basic output states, status registers and any `SYST:ERR?` status; note this query may drain the instrument error queue. Compare front panel to readbacks. **Do not proceed if malformed/inconsistent replies are observed**. A successful `*IDN?` alone does not qualify output commands.

## Stage 2 — OFF-first profile, no DUT

1. Ensure outputs are OFF on the front panel and the load is disconnected; apply only a conservative profile (`examples/safe_3ch.json`). Record any unsolicited ON transition (fail).
2. Verify all three `VOLT?`, `CURR?`, `VOLT:LIM?`, `VOLT:LIM:STAT?` responses, combo flags and `OUTP:STAT:ALL?` against expected bounds.
3. Attempt a software-out-of-bounds setpoint **without sending it to the instrument** and confirm rejection.
4. With safe dummy wiring, exercise `verify_profile()` after altering a limit externally; check trip logic and instrument OFF. Programmatic checks are not instantaneous OVP.

## Stage 3 — outputs, CC/CV, hardware timer

1. At low voltage/current, switch only CH1 ON then OFF; repeat CH2/CH3. Verify independent terminal voltages with a DMM and front-panel indicators.
2. Increase load slowly to transition from CV into CC; confirm actual panel mode and `STAT:OPER:INST:ISUMx:COND?` bit mapping and response latency. Verify per-channel profile trip on mode mismatch.
3. Test timer at low energy (e.g., 2 seconds) and record whether *remote* output activation starts it, its error in seconds, and if it changes other channels.
4. During a safe dummy-load run disconnect the serial cable and/or kill the TCP bridge. Record if output stays ON; design external protection accordingly. Do not claim emergency SCPI OFF can work through a lost link.

## Stage 4 — power-only profile

- Apply static 3-channel values with `access='power_only'`; verify denied setpoint/timer/arbitrary power-sequence operations **before energizing** a dummy load.
- Configure `(CH1 0.0 s, CH2 0.5 s, CH3 1.0 s)` and capture electrical edges on scope or logic+voltage probes. Confirm *actual* order and measured inter-edge intervals; software delays will include SCPI latency.
- Call `power_off()`. Confirm CH3→CH2→CH1 and gaps corresponding to the reverse plan. Repeat on command failure and verify attempted global OFF.
- Repeat with an empty `power_sequence` and independent output control, documenting its different permissions.

## Stage 5 — Series/Parallel/Tracking

**ONLY with no powered loads and after consulting the exact vendor terminal diagram.** Series and Parallel are externally wired: **SCPI commands never make internal power-terminal connections**.

1. With outputs OFF, physically install the external series jumpers according to the manual, check continuity and polarity. Validate aggregate U/I through a dummy load and a separate meter. Validate CH3 output remains as expected.
2. Remove/rearrange jumpers **with all outputs OFF**, then configure parallel output, equal programmed voltages and appropriate current-sharing/wire sizing; check current distribution and total measurement.
3. Tracking needs its own test: ratio of initial voltages, response to setpoint changes and the documented timer interaction.
4. Record SCPI readbacks and actual terminal measurements separately: manual may describe combined mode in terms of *readings*, not electronic rewiring.

## Stage 6 — monitoring, sweeps and more than one instrument

- Log 30+ min at controlled constant load; independently sample a DMM periodically. Compare timestamp drift, missing frames, statistics, Ah/Wh and CSV file after process restart.
- Use low-energy resistor or diode+resistor for `voltage_sweep`, observe staircase voltage and measured I, verify protective cutoff and final OFF. No reverse current or battery discharge tests.
- Connect two PSUs (each with its own profile) and one DMM. Test sequential coordinator behavior and timestamp dispersion; document it as software timing, **not simultaneous trigger**.
- Test transport reconnect, burst requests, stale replies, unexpected control traffic and failure to recover OFF without false safety claims.

## Reports and acceptance

Fill [`hardware_reports/TEMPLATE.md`](hardware_reports/TEMPLATE.md) for each ID, include equipment/firmware/source commit/environment, expected-vs-actual values, screenshots/logs, and explicit pass/fail. Update the exact feature row in [`VERIFICATION.md`](VERIFICATION.md). Keep raw reports private until serial numbers, device identities and network configuration are redacted. No automatic promotion from simulator tests.


H03 reviewed disposition: **PASS for the tested functional scope** (all three channels in CV and CC, panel/load comparison and final OFF). Caveat: CH3 OPER returns ON only (`8`) for CV and CC on firmware 1.04-1.04; QUES and library mode readback agree with the panel. The runner's raw PARTIAL is retained as a status-model discrepancy, not a functional failure. H04 completed **PASS for OFF-only scope**: distinct 2.00/3.00/4.00 V Max Volt Set values clamped panel attempts of 2.01/3.01/4.01 V; SCPI confirmed the clamp; API requests above per-channel V/I caps were rejected before SCPI; tamper checks passed. All outputs stayed OFF and `SYST:ERR?` remained `0, "No error"`. H04-Powered also passed for the low-power profile scope with 21 measured points; unrestricted rated limits are not verified. See the H04P report and qualification matrix.

## H04-Powered — profile boundary supplement (completed)

H04 remains OFF-only for unrestricted rated limits. H04-Powered is an additive
low-energy group: 21 automated readings across independent CH1–CH3, with a KEL103
controlled as a CR load. The runner uses `control` and `power_only` profiles capped at
4.50 V / 0.500 A and allows only one PSU output ON. CV points use 47 Ω (56 Ω at
4.50 V); CC points use 22/11/5 Ω at 0.100/0.250/0.500 A. The 11 Ω point provides
clear current-limit headroom at 0.250 A; 12 Ω sat too close to the CV/CC boundary on
the physical load. Each channel also gets a
3.30 V baseline and its assigned 2.20/3.30/4.40 V point. Requests for 4.51 V and
0.501 A must be rejected in software before SCPI. No unrestricted profile is energized.

Before any load connection, the runner applies the 4.50 V cap and, with every output
OFF, first sends direct `VOLT 4.51` writes on CH1–CH3. Each instrument readback must
reject the request with SCPI error `120, "Parameter overflowed"`, retain the prior
3.30 V setpoint, and return an empty error queue after the expected response is read.
It then checks that a 4.51 V library request fails before SCPI. Finally, it displays the
per-channel readbacks and asks the operator to confirm that front-panel `Protection
Settings → Max Volt Set` is On at 4.50 V and that a fresh V-set=4.51 V panel attempt
clamps to 4.50 V on each channel. The runner reads back the result and restores 3.30 V.
This order tests the direct instrument clamp before the separate panel and local
library V/I-cap checks.
H04 performs the panel/API check with distinct 2.00/3.00/4.00 V maxima and varied
setpoints/current limits. These panel checks follow the vendor
manual's Max Volt Set description (maximum voltage adjustable using V-set).

Before execution, select exactly one KEL103 link (UDP or USB serial), confirm Output
Recall Off, all PSU outputs OFF and KEL INPUT OFF, disconnect other circuits and remove
all jumpers. The runner configures CR and collects KEL103 and PSU readings itself.
Operator pauses occur only to connect the load to CH1, move it CH1→CH2 and CH2→CH3,
then disconnect it from CH3, always with PSU outputs and KEL INPUT OFF. Follow the
exact H04P preflight token; any mismatch or unexpected instrument error stops the stage.
The direct over-limit SCPI probe expects and records error `120, "Parameter overflowed"`,
then verifies the prior setpoint and an empty error queue. The runner allows 5000 normal
PSU commands / 900 seconds and reserves a bounded tail for OFF and state verification
after a command/time budget fault. After enabling a point, it allows up to 3 seconds for
the OPER ON status bit to appear while requiring the selected output readback to remain
ON and every other channel OFF. Offline verification passed 73/73, including the
simulated 21-point flow. The 2026-10-26 physical run passed all 21 readings and profile
guards. Three direct `VOLT 4.51` probes returned the expected
`120, "Parameter overflowed"`, preserved 3.30 V, and left the error queue clear. Panel
attempts on all channels clamped at 4.50 V. Measured CV/CC points agreed with KEL103
within runner thresholds; output and load INPUT were verified OFF after each point and
physically confirmed OFF at the end. Identification replies reported both firmware
strings `1.04-1.04` and `1.04-0.01`. CH3 OPER showed only ON (`8`) in CV and CC while
QUES and independent measurements agreed with the mode. This is a status-bit caveat.
Outcome: PASS / HW OBSERVED; no calibrated metrology or unrestricted rated limits are
claimed. See the [H04P report](hardware_reports/2026-10-26T123057.662164Z_H04P_qualification.md),
[raw event log](../reports/hardware/2026-10-26T123057.662164Z_H04P/events.jsonl), and
[qualification matrix](hardware_reports/qualification_matrix_2026-10-25.md).
