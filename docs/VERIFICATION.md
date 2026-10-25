# Feature verification matrix

[English](VERIFICATION.md) | [Русский](VERIFICATION.ru.md) · [Hardware test procedure](HARDWARE_TESTS.md)

## Meaning of statuses

- **HW VERIFIED** — observed using a physical PSU and a reproducible evidence report with model, firmware, software revision and test conditions. It is **not** an IEC/UL safety certification.
- **HW OBSERVED** — actual instrument response reported by operator, but no complete reproducible bench qualification report yet.
- **SIM TESTED** — exercised by automated fake-transport test(s), **not** proof that a real instrument understands commands or follows expected timing.
- **HW UNVERIFIED** — not yet confirmed on the physical model/firmware; it may be simulator-tested.
- **PLANNED** — no usable implementation yet.

**Scope of known physical observations:** owner connected a TTL USB–UART adapter to Keithley 2231A-30-3, firmware `1.04-1.04`, and successfully issued `SYSTem:REMote` and received valid `*IDN?`. The initial observation is supplemented by the aborted H01 report below; full hardware qualification remains incomplete. In the strict *report-backed* sense, all features still require a repeatable published test record. Do not mistake old *observed* behaviour for all firmware variants.

| ID | Feature | Simulator | Physical hardware | Evidence required |
| --- | --- | --- | --- | --- |
| COM-01 | UART DB9 TTL communications 9600 8N1 | transport modeled | **HW VERIFIED (H01)** | repeatable report including connection and `*IDN?` |
| COM-02 | `SYST:REM` + `*IDN?` | basic query fake | **HW VERIFIED (H01)** | terminal record fw 1.04-1.04 |
| COM-03 | Additional read-only SCPI (`VOLT?`, `STAT?`, `SYST:VERS?`) | SIM TESTED | **HW VERIFIED (H01 subset only)** | `examples/probe.py` output vs front panel |
| COM-04 | Raw TCP-to-UART bridge | SIM TESTED (local loopback) | **HW UNVERIFIED** | MCU firmware/version + loss/fragment tests |
| PRF-01 | Applying independent guarded profile, output-OFF-first | SIM TESTED | **HW UNVERIFIED** | no-load SCPI and front panel + readbacks |
| PRF-02 | Hardware `VOLT:LIM` setting/readback | SIM TESTED | **HW UNVERIFIED** | instrument limits verified on dummy load |
| PRF-03 | Software cap/health integrity alarms | SIM TESTED | **HW UNVERIFIED** | out-of-band modification test with safe load |
| PRF-04 | Full rated-range profile (`unrestricted()`) | SIM TESTED | **HW UNVERIFIED** | readbacks at rated bounds, outputs OFF |
| PRF-05 | `power_only` API denylist/fixed sequence | SIM TESTED | **HW UNVERIFIED** | timing, on/off order, attempt blocked changes |
| OUT-01 | Individual output enable/disable | SIM TESTED | **HW UNVERIFIED** | per-channel lamp/readback/independent dummy load |
| OUT-02 | Timers and command-triggered expiration | SIM TESTED | **HW UNVERIFIED** | characterize manual/remote operation |
| OUT-03 | Series combination control/readback | SIM TESTED | **HW UNVERIFIED** | real **external** wiring + dummy load/limits |
| OUT-04 | Parallel combination control/readback | SIM TESTED | **HW UNVERIFIED** | real **external** wiring + dummy load/limits |
| OUT-05 | Tracking and ratio/timer behavior | SIM TESTED (basic) | **HW UNVERIFIED** | real tracking readback and timed output |
| MET-01 | Read voltage/current/status and inferred CV/CC | SIM TESTED | **HW UNVERIFIED** | verify bit mapping using real load and DMM |
| MET-02 | CSV, min/max/mean/stddev, Ah/Wh arithmetic | SIM TESTED | **HW UNVERIFIED** | duration run + independent reference |
| IV-01 | Single-channel voltage sweep + external sample callback | SIM TESTED | **HW UNVERIFIED** | resistor/diode test with series protection |
| BEN-01 | Multi-PSU host coordination / generic meter | SIM TESTED | **HW UNVERIFIED** | dual PSU + DMM comparison with clock uncertainty |
| SYN-01 | Hardware-trigger synchronization | **PLANNED** | **PLANNED** | electrical trigger validation, timing metrology |
| BRG-01 | MCU Ethernet bridge firmware and watchdog | **PLANNED** | **PLANNED** | implementation + physical link loss tests |

## Rules for promoting to HW VERIFIED

1. Use the controlled procedure in [`HARDWARE_TESTS.md`](HARDWARE_TESTS.md). Log test revision, PSU model, firmware, connection, measured conditions, environment and firmware state.
2. Attach human-readable results in `docs/hardware_reports/YYYY-MM-DD_<feature>_<firmware>.md`. Remove serial numbers or redact them before publication.
3. Record both expected and actual SCPI replies, front-panel checks, oscilloscope/DMM evidence for electrical functions, and pass/fail.
4. Do not label a feature as verified solely because it was run with a live port; observe the claimed physical behavior and test error paths.
5. Treat different firmware revisions and modified driver versions as requiring a compatibility check. Re-run safety-critical tests after changes.
6. Do not publish a universal safety guarantee, even after successful hardware tests.

A green GitHub Actions badge, by definition, reports **SIM TESTED only**. The hardware result must be a human-reviewed report, not an automatically merged badge.

## Qualification preparation — 2026-10-25

The [exact H01–H12/O01/O02 matrix](hardware_reports/qualification_matrix_2026-10-25.md)
initially recorded **NOT_RUN** for every new hardware group; the current H01 attempt is documented below. UART, `SYST:REM` and `*IDN?`
remain **HW OBSERVED**, initially supported by the [operator observation](hardware_reports/2026-10-25_uart_idn_observation.md).
No physical instrument was accessed during this preparation.

Offline baseline: **31 passed**. After focused regressions: **46 passed**, Python
3.13.5 / pyserial 3.5, `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
These results are **SIM TESTED** only. See the [audit](hardware_reports/2026-10-25_offline_audit.md).

`tools/hw_qualify.py list`, `preflight H01` and `run H01` default to dry run;
no port opens. Only H01 has an executable scenario at this stage, requiring a
specific interactive gate. It changes remote/local control mode, queries the
selected channel and all three ON status bits, and consumes one error queue item.
It never writes output/setpoint/topology/timer controls. H02–H12/O01/O02 execution
is guarded until preceding evidence, load specifications and individual cards
are reviewed; their test outcomes remain NOT_RUN.

Safety changes are simulation-tested: exact authorized V/I and voltage-limit
readbacks, unexpected ON detection, per-channel OFF checks, inherited timer
disable, strict identity/boolean inputs, and transport failure latching.
Series/parallel enabling is temporarily refused pending combined-setpoint
qualification; combined readings expose unknown setpoints as `None`. Tracking
setpoint changes remain unavailable. No lock readback command is documented;
external panel unlock cannot be claimed detectable. `VOLT:LIM` is a maximum
settable voltage, not independent measured-voltage OVP. Timer start on remote ON,
combined readbacks and all electrical behavior remain HW UNVERIFIED.

## H01 attempt — 2026-10-26 MSK / 2026-10-25 UTC

**H01: ABORTED**; [sanitized physical evidence](hardware_reports/2026-10-25T210502.973378Z_H01_preflight.md).
Revision `99f2c442649eb7b357a0da603c81330028b6a475`, clean tree at execution,
firmware `1.04-1.04`, UART `/dev/ttyUSB1`, 9600 8N1, CP2102 `10C4:EA60`.
COM-01/02 remain HW OBSERVED, now with a recorded authorized attempt. COM-03
is **HW OBSERVED only for** `SYST:VERS?=1991.1`, `INST:NSEL?=1`, channel operation
conditions `0/0/0`, `OUTP:STAT:ALL?=0` and `OUTP:SER?=0`. All remaining read-only
queries and powered behavior remain HW UNVERIFIED. No HW VERIFIED promotion.

An I/O exception occurred on `OUTP:PARA?`; no response was recorded. Original
exception details were not preserved, so unsupported abbreviation is a hypothesis,
not a proven cause. No error-queue read, LOC restore, reconnect or output command
followed. Port closed; final physical OFF awaits operator confirmation. Remaining
groups H02–H12/O01/O02 are NOT_RUN and require separate approval.

### H01 correction pending retest — 2026-10-26

Operator observed error `170, Invalid command` in the instrument menu after H01.
The last query was `OUTP:PARA?`. The manual's OUTPUT syntax is `PARallel`, while
its example uses PARA inconsistently. The library now uses full `OUTP:PARALLEL`
for all writes/queries, and H01 uses the same canonical name. The emulator
rejects the ambiguous form; **46 offline tests pass**. No hardware retest yet:
H01 remains ABORTED, corrected command compatibility remains HW UNVERIFIED.
The revised H01 preserves an initial known 170 separately, requires final error
code 0, records exception types, and stops on every new error. No H02 until the
error is resolved and H01 completes successfully under a fresh operator gate.

### H01 retest PASS — 2026-10-26 MSK

[Physical evidence and exact scope](hardware_reports/2026-10-25T211550.545588Z_H01_preflight.md).
Base revision `99f2c442649eb7b357a0da603c81330028b6a475` with uncommitted canonical
parallel-command patch; source SHA256 `6bb6eb7808af5dde318884bcb445b62257b087abc9625fa25c84d8d98bc25266`.
Firmware 1.04-1.04, CP2102 UART 9600 8N1. All 28 read-only queries completed;
`OUTP:PARALLEL?=0`; error code 0 at beginning/end; IDN and OFF bits repeated.
Operator confirmed all three PSU channels OFF, panel setpoints 1 V / 0.1 A.

COM-01/COM-02 are HW VERIFIED for this bounded UART/REM/IDN scenario. COM-03
is HW VERIFIED only for read-only selected-channel queries on CH1–CH3 and
all-channel OFF status reads. Positive ON/CV/CC, applied caps,
timer expiration, metering accuracy and active combined modes remain HW UNVERIFIED.
CH1 `VOLT:LIM?=30.1 V` with state=0 was merely observed, not a safe enabled cap.
The parallel **query** defect is resolved in this scenario; canonical setters
remain pending H02/H10. H02–H12/O01/O02 remain NOT_RUN. No commit or next stage
was performed; prior ABORTED evidence is retained.

### H01 readback addendum — CH2/CH3

[Timestamped SCPI evidence](hardware_reports/2026-10-25T212311.676405Z_H01_CH2_CH3_readback.md).
After selecting each channel for queries and restoring CH1, both readbacks were
`VOLT?=1 V`, `CURR?=0.1 A`; `CHAN:OUTP?=0`, timer state=0. OFF bits for all
channels were 0 before and after, and the error queue was `0, "No error"` before
and after. CH2/CH3 limits read 30.1 V / 5.1 V with limit state=0; these values
are observations, not active protection. PSU meter readings are uncalibrated and
were not compared with an independent meter. The H01 read-only setpoint snapshot
now covers all channels. Powered behavior remains unverified.
