# Hardware qualification procedure (operator-run, never CI)

[English](HARDWARE_TESTS.md) | [Русский](HARDWARE_TESTS.ru.md) · [Feature matrix](VERIFICATION.md)


> Current qualification follows the [compact H01–H12/O01/O02 matrix](hardware_reports/qualification_matrix_2026-10-25.md)
> and [offline audit / H01 gate](hardware_reports/2026-10-25_offline_audit.md).
> Use `python3 tools/hw_qualify.py preflight H01` first. Explicit operator approval
> is required before any serial access and separately for every powered group,
> rewiring, mains cycle or live-link interruption. Begin with offline I/O failure
> simulation; H07 is a 2–5 minute run. Diode DUTs and longer stress runs need
> separate approval. The overview below does not authorize those actions.

H02: [OFF-only profile operator card](H02_PREFLIGHT.ru.md). Offline regression: 48/48.
The first physical run failed because `*IDN?` changed firmware field mid-session and
the runner detected it only after profile writes. A library guard now aborts on an
identity change before configuration. The corrected H02 rerun passed with identical
`1.04-0.01` identity replies and a clear instrument error queue. See both evidence
reports and verification log. H02 is reviewed PASS; H03 requires a fresh approval after
the failed first attempt and safety-path correction.

H03: [KEL103 load and transition operator card](H03_PREFLIGHT.ru.md). CH1, CH2 and CH3
CV/CC points have physical measurements and live display confirmations. The last CH3 run
compared both Keithley/KEL103 readings within tolerance and recorded 190 `SYST:ERR?`
replies of `0, "No error"`. However, CH3 returned operation status `8` in both CV and CC,
while questionable status returned `1` (CV) and `2` (CC). This contradicts the manual's
mode-bit definition for both channel summaries; status interpretation remains unresolved
and H03 is PARTIAL. All channels and KEL103 INPUT were read and confirmed OFF. See the
[latest H03 report](hardware_reports/2026-10-26T002312.484924Z_H03_qualification.md).

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
