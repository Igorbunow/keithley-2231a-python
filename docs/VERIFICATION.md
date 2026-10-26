# Feature verification matrix

**Current source requalification:** H01 passed read-only. H02 passed its varied OFF-only profile, panel clamp, library soft-cap and gated instrument-rejection checks (HW OBSERVED). H03 passed all six CV/CC points on the current runner with load/display comparisons and final OFF confirmation; CH3 OPER status caveat remains documented. H04 passed its OFF-only scope on the current runner with distinct panel limits, API soft-cap rejection, tamper checks and final OFF confirmation. H05 passed the low-power three-channel sequence with TDS capture, KEL103/PSU readbacks, clean error queue and final OFF confirmation. H06 timer subgroup passed on hardware: CH1/2/3 expired at 3.051/3.063/3.056 s and re-enabled with timer disabled; 841 error-queue reads were clean and final OFF was confirmed. Its first attempt had aborted before energizing due runner defects, subsequently corrected and covered by the 95/95 offline suite. Output Recall passed four hardware mains-cycle cases: OFF `111→000`, ON `101→101`, `010→010`, `000→000`. Final physical OFF and restored Recall OFF/Save Last ON were confirmed. Offline results do not qualify hardware behavior.

[English](VERIFICATION.md) | [Русский](VERIFICATION.ru.md) · [Hardware test procedure](HARDWARE_TESTS.md)

The separate `H06 --recall` runner is **SIM TESTED 97/97** and **PASS (HW
OBSERVED)** for its four manually gated power-cycle cases. Save Last remained ON,
no load was connected, and each boot state was captured before profile or output
writes. All 970 `SYST:ERR?` replies were `0, "No error"`; final outputs were
confirmed OFF and the panel settings restored. [Procedure](H06_OUTPUT_RECALL.ru.md)
and [hardware evidence](hardware_reports/2026-10-26T201550.519080Z_H06_qualification.md).

## Meaning of statuses

- **HW VERIFIED** — observed using a physical PSU and a reproducible evidence report with model, firmware, software revision and test conditions. It is **not** an IEC/UL safety certification.
- **HW OBSERVED** — actual instrument response reported by operator, but no complete reproducible bench qualification report yet.
- **SIM TESTED** — exercised by automated fake-transport test(s), **not** proof that a real instrument understands commands or follows expected timing.
- **HW UNVERIFIED** — not yet confirmed on the physical model/firmware; it may be simulator-tested.
- **PLANNED** — no usable implementation yet.

**Scope of known physical observations:** the operator reports the tested unit's menu firmware as Main `1.04` / Aux `0.01`. Raw `*IDN?` revision fields have varied between `1.04-1.04` and `1.04-0.01`; those fields are recorded but are not device identity. The driver checks manufacturer/model/serial and supports optional expected-serial binding. Other firmware revisions are not hard-blocked, but behavior remains unqualified until tested.

| ID | Feature | Simulator | Physical hardware | Evidence required |
| --- | --- | --- | --- | --- |
| COM-01 | UART DB9 TTL communications 9600 8N1 | transport modeled | **HW OBSERVED (current H01 shared-path report; CP2102, 9600 8N1)** | [H01 evidence](hardware_reports/2026-10-26T145345.082772Z_H01_qualification.md) |
| COM-02 | `SYST:REM` + `*IDN?` | basic query fake | **HW OBSERVED (current H01; IDN `Keithley instruments, 2231A-30-3`, firmware field `1.04-0.01`)** | [H01 evidence](hardware_reports/2026-10-26T145345.082772Z_H01_qualification.md) |
| COM-03 | Additional read-only SCPI (`VOLT?`, `STAT?`, `SYST:VERS?`) | SIM TESTED | **HW OBSERVED (current H01; channel setpoints, caps, output/timer status, measurements, status and error queue)** | [H01 evidence](hardware_reports/2026-10-26T145345.082772Z_H01_qualification.md) |
| COM-04 | Raw TCP-to-UART bridge | SIM TESTED (local loopback) | **HW UNVERIFIED** | MCU firmware/version + loss/fragment tests |
| COM-05 | Shared normal/diagnostic dispatcher, explicit profile bypass, driver-controlled tracing | SIM TESTED | **HW UNVERIFIED (replay required)** | [Requalification plan](REQUALIFICATION.ru.md) |
| LOAD-01 | KEL103 control over UDP or USB CDC serial | SIM TESTED (UDP and USB CDC fixtures) | **HW OBSERVED (USB serial in current H03; KEL103 V3.30)** | CR 40/12 Ω configuration, INPUT ON/OFF and live readings verified; [H03 report](hardware_reports/2026-10-26T174052.170220Z_H03_qualification.md) |
| PRF-01 | Applying independent guarded profile with distinct per-channel setpoints | SIM TESTED (1/1.5/2 V and 0.1/0.2/0.3 A) | **HW OBSERVED PASS (H02)** | [H02 report](hardware_reports/2026-10-26T164414.981773Z_H02_qualification.md) |
| PRF-02 | Hardware `VOLT:LIM` setpoint ceiling setting/readback, varied by channel | SIM TESTED (2/3/4 V) | **HW OBSERVED PASS (H02)** | [H02 report](hardware_reports/2026-10-26T164414.981773Z_H02_qualification.md) |
| PRF-03 | Software cap/health integrity alarms | SIM TESTED | **HW OBSERVED PASS (current H04; OFF-only tamper)** | out-of-band limit and setpoint changes detected; profile disarmed; [H04 report](hardware_reports/2026-10-26T182004.069364Z_H04_qualification.md) |
| PRF-04 | Full rated-range profile (`unrestricted()`) | SIM TESTED | **HW OBSERVED PASS (current H04; OFF-only)** | rated-bound profile readbacks, zero voltage setpoint, outputs OFF; [H04 report](hardware_reports/2026-10-26T182004.069364Z_H04_qualification.md) |
| PRF-05 | `power_only` API denylist/fixed preset | SIM TESTED | **HW OBSERVED PASS (current H04 OFF-only; H04P fixed presets energized)** | profile guards and fixed preset exercised; timer behavior remains unverified; [H04 report](hardware_reports/2026-10-26T182004.069364Z_H04_qualification.md) |
| PRF-06 | Per-channel `Max Volt Set` clamp, library soft cap, instrument rejection under explicitly gated profile bypass | SIM TESTED with distinct CH values | **HW OBSERVED PASS (H02 and current H04; OFF-only)** | Panel 2.01/3.01/4.01→2/3/4 V; API rejected above individual V/I caps before SCPI; gated debug probes returned error 120 and preserved 1/1.5/2 V. `VOLT:LIM` is a setpoint ceiling, not independent analog OVP. [H02](hardware_reports/2026-10-26T164414.981773Z_H02_qualification.md), [H04](hardware_reports/2026-10-26T182004.069364Z_H04_qualification.md) |
| PRF-07 | Low-power profile CV/CC boundaries and independent KEL103 comparison | SIM TESTED | **PASS; HW OBSERVED (current H03 runner, six points)** | Distinct initial V/I and profile caps; live display checks; CH3 OPER=`8` caveat with QUES/library/panel agreement. [H03 report](hardware_reports/2026-10-26T174052.170220Z_H03_qualification.md). H04P fixed preset and unrestricted rated limits are separate scopes. |
| SEQ-01 | Fixed `power_only` CH1→CH2→CH3 power-up and reverse power-down, profile guards and measured delays | **SIM TESTED** | **PASS; HW OBSERVED (H05, firmware field 1.04-1.04)** | TDS 3064B x10 recorded all six edges in order; edge-onset intervals 2.551/2.491/4.200/1.189/1.140 s; KEL103 CR22 on CH1 read 0.9976 V, 0.0452 A, 0.0450 W; PSU readbacks and all error responses recorded, final outputs/KEL INPUT OFF. 0.5 V CH2 chatter is preserved as diagnostic; common-negative wiring does not qualify channel isolation. [H05 evidence](hardware_reports/2026-10-26T190417.647970Z_H05_qualification.md) |
| OUT-01 | Individual output enable/disable | SIM TESTED | **HW OBSERVED PASS (H03; each output separately enabled and verified OFF)** | per-channel `CHAN:OUTP?`, KEL103 measurement and live display check; [H03 report](hardware_reports/2026-10-26T174052.170220Z_H03_qualification.md) |
| OUT-02 | Per-channel timers, remote-on expiry, re-enable and Output Recall | **SIM TESTED (H06 runner; shared driver path)** | **PASS (HW OBSERVED)** — timer expiry/re-enable on CH1–CH3 and four Recall power cycles | 3.0 s timer expiry: CH1 3.051 s, CH2 3.063 s, CH3 3.056 s; each timer-disabled re-enable held 0.5 s. Recall OFF `111→000`; Recall ON `101→101`, `010→010`, `000→000`. 970 clean error-queue replies; final all-off confirmed and Recall OFF restored. [Recall evidence](hardware_reports/2026-10-26T201550.519080Z_H06_qualification.md) · [timer results](hardware_reports/2026-10-26T195235.744227Z_H06_qualification.md) · [first aborted attempt](hardware_reports/2026-10-26T193545.109045Z_H06_qualification.md) |
| OUT-03 | Series combination control/readback | SIM TESTED | **HW UNVERIFIED** | real **external** wiring + dummy load/limits |
| OUT-04 | Parallel combination control/readback | SIM TESTED | **HW UNVERIFIED** | real **external** wiring + dummy load/limits |
| OUT-05 | Tracking and ratio/timer behavior | SIM TESTED (basic) | **HW UNVERIFIED** | real tracking readback and timed output |
| MET-01 | Read voltage/current/status and inferred CV/CC | SIM TESTED | **HW OBSERVED PASS (current H03; CH3 OPER caveat)** | Six KEL103 comparisons; CH3 OPER stays 8 while QUES distinguishes CV/CC; calibrated DMM accuracy unverified; [H03 report](hardware_reports/2026-10-26T174052.170220Z_H03_qualification.md) |
| MET-02 | CSV, min/max/mean/stddev, Ah/Wh arithmetic | SIM TESTED (H07 runner: CSV recomputation, real-clock gap model, resets, stop deadline and mismatch shutdown) | **HW OBSERVED PASS (H07; 180 s, 44 samples/channel; one gap/channel excluded from integration; independent CSV statistics/Ah/Wh recomputation and both resets passed; 2,035 clean error-queue replies; final OFF confirmed)** | [passing report](hardware_reports/2026-10-26T205248.277386Z_H07_qualification.md); [first stopped attempt](hardware_reports/2026-10-26T204422.181415Z_H07_qualification.md) |
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

`tools/hw_qualify.py list`, `preflight` and `run` default to dry run; no port opens.
H01 has a read-only scenario with its own interactive gate: remote/local control,
selected-channel queries, all-channel ON status bits and two error queue reads.
H02 has a manually gated OFF-only profile scenario. The corrected run passed varied
profile application/readback, panel clamps, API soft rejection and instrument error 120
probes; see [H02 report](hardware_reports/2026-10-26T164414.981773Z_H02_qualification.md).
The first panel attempt remains recorded as incomplete because its operator instructions
were ambiguous. H03 current-runner requalification passed six CV/CC points with distinct
initial V/I values and per-channel caps; the runner sets the shared 4.00 V test point only
with outputs OFF. CH3 retains an OPER status caveat; see the current report. KEL103 USB
serial control was exercised on hardware in H03; UDP control was separately exercised on
CH2. H04 is reviewed **PASS for OFF-only scope on the current runner**; the first attempt stopped because the manual panel protection values were still 4 V on all channels, then the corrected attempt passed. H04-Powered is reviewed **PASS for its bounded profile scope (HW OBSERVED)**. H05 is reviewed **PASS for its bounded power-sequence scope (HW OBSERVED)**; see the report for exact readings and the measurement definition. H06 is reviewed **PASS for timer and Output Recall scopes (HW OBSERVED)**. H07 is reviewed **PASS (HW OBSERVED)** for bounded monitoring and CSV arithmetic; remaining H08–H12/O01/O02 await individual review.

H04 runner preparation is **SIM TESTED**, not hardware verified. The approved offline
suite `PYTHONPATH=src python -m unittest discover -s tests -v` passed **68/68** after
adding OFF-only rated-range and `power_only` profile checks, active-profile immutability,
and detection of external voltage-limit and exact-setpoint changes. The fake transport
confirmed that no output-ON command is sent. The initial run exposed a syntax error and,
after that was fixed, an undersized 400-command budget; the runner now has a 1000-command
and 180-second bound. At this preparation snapshot, H04 was **NOT_RUN on hardware**;
the later reviewed result is recorded below.

Safety changes are simulation-tested: exact authorized V/I and voltage-limit
readbacks, unexpected ON detection, per-channel OFF checks, inherited timer
disable, strict identity/boolean inputs, and transport failure latching.
Series/parallel enabling is temporarily refused pending combined-setpoint
qualification; combined readings expose unknown setpoints as `None`. Tracking
setpoint changes remain unavailable. No lock readback command is documented;
external panel unlock cannot be claimed detectable. `VOLT:LIM` is a maximum
settable voltage, not independent measured-voltage OVP. Timer start on remote ON,
combined readbacks and all electrical behavior remain HW UNVERIFIED.

## Historical H04 disposition — 2026-10-26

The corrected H04 run completed with **PASS for OFF-only scope**. Per-channel profile
limits were 2.00/3.00/4.00 V, setpoints 1.00/2.00/3.00 V and current settings
0.100/0.200/0.300 A, with software current caps 0.200/0.300/0.400 A. The operator
attempted 2.01/3.01/4.01 V at the front panel; the instrument clamped to 2.00/3.00/4.00 V.
SCPI readback confirmed those clamps, then the runner restored the per-channel setpoints.
Library requests above each V/I cap were rejected before SCPI. Unrestricted zero-voltage
settings, protected `power_only` values, limit/setpoint tamper detection and profile
immutability passed. All observed `SYST:ERR?` replies were `0, "No error"`; channel
status/readback remained OFF. The runner restored 1.00 V / 0.100 A on CH1/CH2 and
1.00 V / 0.101 A on CH3; the operator confirmed all outputs OFF and the load disconnected.
The instrument was accessed on `/dev/ttyUSB3` at 9600 8N1. The menu firmware is Main
1.04 / Aux 0.01; `*IDN?` produced both `1.04-0.01` and `1.04-1.04` during the run,
recorded as raw revision observations rather than identity. No output was energized in
H04; powered limits and electrical regulation were checked separately in H04-Powered,
which passed its bounded 4.50/4.51 V clamp and low-power regulation scope. The reference manual describes
`Max Volt Set` as the maximum settable through V-set
([Tektronix/Keithley 2231A reference manual, section 3](https://download.tek.com/manual/077100401_Referencemanual.pdf)).
See the [completed H04 report](hardware_reports/2026-10-26T095243.287282Z_H04_qualification.md),
[expanded H04 attempt](hardware_reports/2026-10-26T093558.386640Z_H04_qualification.md)
and [initial H04 profile report](hardware_reports/2026-10-26T091619.638967Z_H04_qualification.md).
This disposition belongs to an earlier runner revision. In the current-runner replay, the
first attempt failed its manual panel clamp check: CH1 `VOLT?` returned `1.00 V` instead of
`2.00 V`; the operator clarified that Max Volt Set had remained `4.00 V` on every channel.
The corrected attempt explicitly set the panel limits per channel and passed all OFF-only
checks; see the [current H04 report](hardware_reports/2026-10-26T182004.069364Z_H04_qualification.md)
and [first failed attempt](hardware_reports/2026-10-26T180828.280308Z_H04_qualification.md).
H04-Powered and H05 have completed separately; H06 is reviewed PASS for timer and Output Recall. H07 is reviewed PASS (HW OBSERVED) for bounded monitoring and CSV arithmetic; H08–H12/O01/O02 remain pending review/approval.

## H04-Powered reviewed disposition — current-runner replay, 2026-10-26

H04-Powered completed with **PASS for the bounded low-power profile scope**; the
verification level remains **HW OBSERVED** because this is physical observation, not
calibrated metrology. This replay used the shared-path runner from source commit
`ba3fabfe3afa7d1703b1dfa42b2c8b034dd36bf8`; the documented matrix-gate assertion was
pending in the working tree.
All 21 CV/CC and fixed `power_only` readings passed their KEL103 comparison thresholds.
The three direct `VOLT 4.51` probes each returned the expected `120, "Parameter overflowed"`,
preserved the 3.30 V setpoint, and left subsequent `SYST:ERR?` replies at
`0, "No error"`. Panel requests also clamped at 4.50 V on each channel. API cap guards
and `power_only` immutability checks passed before SCPI. All outputs and KEL103 INPUT
were verified OFF after each point and physically confirmed OFF at completion.

CH3 `STAT:OPER:INST:ISUM3:COND?` returned `8` while enabled for both CV and CC; its
QUES status was `1` in CV and `2` in CC, and KEL103 measurements confirmed the modes.
This is the same non-blocking status-bit caveat seen in H03. Firmware strings in `*IDN?`
replies varied between `1.04-1.04` and `1.04-0.01`. No calibrated DMM was used and
unrestricted rated limits were not energized. The current run recorded 2,833 clean
`0, "No error"` replies and exactly three expected `120, "Parameter overflowed"`
responses from the gated hard-limit probes. Panel clamps, API rejects, all 21 readings,
and final physical OFF confirmation passed. See the [current report](hardware_reports/2026-10-26T182637.334275Z_H04P_qualification.md),
[raw event log](../reports/hardware/2026-10-26T182637.334275Z_H04P/events.jsonl),
[prior reviewed report](hardware_reports/2026-10-26T123057.662164Z_H04P_qualification.md), and
[qualification matrix](hardware_reports/qualification_matrix_2026-10-25.md).

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

### H02 preparation — 2026-10-26

Prepared `apply_profile()` / `verify_profile()` with all channels at 1.00 V / 0.100 A,
4.00 V voltage cap and 0.500 A software current cap, outputs OFF, load disconnected.
The runner rejects command-plan drift before transmission and checks the error queue
after every write except LOC. Prepared regressions cover the successful trace,
error 170, uncertain I/O, cap mismatch, initial ON and prevention of ON transmission.
These checks were **SIM TESTED** before the hardware run. The first H02 attempt **FAILED**:
the first `*IDN?` returned revision `1.04-1.04`; the driver's second identity query
returned `1.04-0.01`. The operator later confirmed the menu's Main 1.04 / Aux 0.01.
This revision-field variation was initially treated as an identity change; the identity key now
uses manufacturer/model/serial and both revisions are retained in evidence. All three profile writes completed, every observed
`SYST:ERR?` returned `0, "No error"`, and all channel OFF bits read 0. The runner's
identity comparison happened too late, after writes. The operator then confirmed
all outputs OFF, load OFF/disconnected, and setpoints 1.00 V / 0.100 A on CH1–CH3.
The library now aborts on manufacturer/model/serial changes before profile writes;
firmware revision changes alone do not block use. On the corrected H02 rerun,
both `*IDN?` responses matched at firmware `1.04-0.01`; all SCPI error replies remained
`0, "No error"`, the profile readbacks passed on all channels, and OFF bits were 0/0/0.
The operator confirmed all outputs OFF, load OFF/disconnected, and 1.00 V / 0.100 A
setpoints on every channel. See [first attempt](hardware_reports/2026-10-25T213651.354322Z_H02_qualification.md)
and [corrected H02 PASS](hardware_reports/2026-10-25T214134.648837Z_H02_qualification.md).
H02 is **HW OBSERVED PASS** for the no-load profile application/readback; PRF-01/PRF-02
broader electrical behavior remained **HW UNVERIFIED**. At that time no later stage was authorized.

### H02 voltage protection completion criteria — preparation

Operator feedback identified three required H02 checks missing from the earlier baseline:
panel attempts above each channel's limit; normal library API rejection before SCPI; and,
after a separate gate, three `debug_bypass_profile` probes that must return instrument
error 120. H02 now uses distinct per-channel values: V-set 1/1.5/2 V, I-set 0.1/0.2/0.3 A,
V-cap 2/3/4 V and software I-cap 0.3/0.4/0.5 A. The panel probes are 2.01/3.01/4.01 V;
debug probes use the same attempts and must preserve initial setpoints. The runner keeps
commands on the driver's shared dispatch, requires every output OFF and the load
disconnected, and reapplies the profile after the panel edit. These checks and their
offline regressions are **SIM TESTED** only; they have not been repeated on hardware.
`VOLT:LIM`/`Max Volt Set` is a setpoint ceiling, not independent analog OVP.
See the [updated H02 card](H02_PREFLIGHT.ru.md).

### H02 replay on the shared driver path — 2026-10-26

The corrected varied-value replay is **HW OBSERVED PASS** for the complete H02
OFF-only scope: profile application/readback, panel clamp on all channels, API
pre-SCPI rejection, and explicitly armed debug-bypass instrument rejection. The
panel returned 2/3/4 V after 2.01/3.01/4.01 V requests; instrument probes returned
`120, "Parameter overflowed"` and preserved 1/1.5/2 V setpoints. Outputs and load
were confirmed OFF. The earlier attempt stopped before CH2/CH3 because panel
instructions were ambiguous; it is retained as an incomplete historical attempt.
See [corrected H02 report](hardware_reports/2026-10-26T164414.981773Z_H02_qualification.md).

[Current-source H02 evidence](hardware_reports/2026-10-26T150528.467723Z_H02_qualification.md).
The initial readbacks were CH1/CH2/CH3 = 2.20/3.30/4.40 V at 0.100 A, each with
4.50 V limit enabled and output/timer OFF. The library applied and verified 1.00 V /
0.100 A, 4.00 V limit enabled, independent topology, and outputs/timers OFF on all
three channels. All 167 error-queue checks returned `0, "No error"`. Three unarmed ON
requests and 18 invalid V/I setpoint attempts were rejected before SCPI. Final internal
measurements with outputs off were 0.00371838/0.00152016/0 V and 0/0/0.000290878 A;
measurement accuracy was not evaluated. Operator confirmed physical OFF. This is
**HW OBSERVED PASS** for the no-load profile scope; H03 is the next group and remains
separately gated.

### H03 — all six CV/CC functional points reviewed PASS; CH3 OPER caveat recorded

The first run used long, thin leads and stopped after the CH1 CC voltage difference
exceeded its criterion. It measured CV as Keithley 3.99906 V / 0.0978727 A /
0.392112 W versus KEL103 3.967 V / 0.099 A / 0.3933 W, then CC as Keithley 3.08919 V /
0.249801 A / 0.77151 W versus KEL103 3.008 V / 0.250 A / 0.7539 W. Global `OUTP 0`
returned `0, "No error"` but status remained 10 (CC+ON). The operator manually
confirmed all OFF. The voltage drops divided by load current were 0.324 Ω and 0.325 Ω,
suggesting lead/contact resistance; this is an inference.

After installing short 10 AWG leads, a second run measured CH1 CV as Keithley
3.9988 V / 0.0986824 A / 0.39477 W and KEL103 3.998 V / 0.099 A / 0.3982 W. CH1 CC
measured Keithley 3.01538 V / 0.249777 A / 0.75292 W and KEL103 3.008 V / 0.250 A /
0.7539 W. The CC SCPI status was 10 and the panel showed CC. The operator confirmed
all channels and load OFF. Both CH1 points are now reviewed PASS. CH2 CV/CC were later measured and
the operator confirmed both displays. CV: Keithley 3.9931 V / 0.0986671 A /
0.394513 W, status 9; KEL103 3.9924 V / 0.0999 A / 0.3988 W. CC: Keithley
3.00772 V / 0.249603 A / 0.750575 W, status 10; KEL103 3.0037 V / 0.2503 A /
0.7518 W. All error-queue replies were `0, "No error"`; both instruments were
confirmed OFF after each point.

CH3 is distinct: its nameplate voltage rating is 5 V (CH1/CH2: 30 V); this run's
4.00 V / 0.500 A cap was within that rating. At the CH3 CV point, Keithley returned
3.99326 V / 0.0998173 A, but `STAT:OPER:INST:ISUM3:COND?` returned `8` (ON only,
without CV/CC bits) on repeated reads. The documented operation-condition query is
channel-specific and assigns CV/CC to bits 0/1 and ON to bit 3. No error-queue error
occurred; no panel-mode confirmation was obtained before the fail-safe shutdown. The
point is therefore FAIL/UNVERIFIED, not CV PASS. See the
[latest event log](../reports/hardware/2026-10-25T231834.474816Z_H03/events.jsonl)
and [H03 report](hardware_reports/2026-10-25T231834.474816Z_H03_qualification.md).

A read-only KEL103 UDP preflight on port 18190 returned V3.30, INPUT OFF, function CR
and zero measurements. Bounded CR 40/12 Ω and INPUT ON/OFF writes with readback were
physically exercised on CH2 and returned zero instrument errors. The CH3 questionable-
status diagnostic is prepared but has not been run. See the
[H03 report](hardware_reports/2026-10-25T231834.474816Z_H03_qualification.md) and
[event log](../reports/hardware/2026-10-25T231834.474816Z_H03/events.jsonl).

The Keithley manual defines bit 3 as output ON and bits 0/1 as CV/CC in the operation
channel summary. The library
emergency-off path enters Remote, requests global OFF, checks all channels and
individually issues `CHAN:OUTP 0` for any remaining ON channel. The full offline suite is
56/56 SIM TESTED.
H03 remains PARTIAL because CH3 mode-status telemetry is unqualified. The latest live-gated
USB run completed both CH3 points. CV: Keithley 3.99576 V / 0.0998173 A / 0.399012 W (SCPI power);
KEL103 4.0003 V / 0.0999 A / 0.3996 W. CC: Keithley 2.98781 V / 0.249532 A /
0.745347 W (SCPI power); KEL103 2.9840 V / 0.2487 A / 0.7421 W. The operator confirmed PSU CV then
CC, KEL CR and INPUT ON at the live gates. Both electrical comparisons met H03 tolerances.
All 192 Keithley `SYST:ERR?` replies were `0, "No error"`. For both powered points,
`STAT:OPER:INST:ISUM3:COND?` returned `8` (ON only), while
`STAT:QUES:INST:ISUM3:COND?` returned `1` (CV) and `2` (CC), respectively. The Keithley
manual specifies CV/CC bits 0/1 in both channel summary registers and ON bit 3 in the
operation summary. The library defect is now localized: `read_channel()` decoded regulation
only from operation status. It now reads both registers, uses a single documented mode when
the other has no mode bit, returns both masks on `Reading`, and reports UNKNOWN if the
registers conflict. The strict H03 runner still records operation status separately. Offline
regression passed 60/60. A powered CH3 replay physically verified that `read_channel()` now
returns CV/CC from questionable status for both modes; operation status remains `8`, so the
strict runner correctly leaves H03 PARTIAL. All three outputs and KEL103 INPUT were read OFF,
and the operator confirmed the final OFF state. See the [latest report](hardware_reports/2026-10-26T003341.714219Z_H03_qualification.md)
and [raw event log](../reports/hardware/2026-10-26T003341.714219Z_H03/events.jsonl).

An earlier CH3 resume stopped before load or PSU profile configuration: KEL103 `*IDN?`
timed out. During cleanup, the first Keithley command was `OUTP 0` while the instrument
was in Local; the following `SYST:ERR?` returned `-200, "Execution error"`. This response
does not identify its originating command. The library now sends `SYST:REM` before
emergency OFF. The corrected path is simulation-tested only; see the
[attempt report](hardware_reports/2026-10-25T233455.502881Z_H03_qualification.md).

The KEL103 USB CDC serial path is an explicit H03 option
(`--load-transport serial --load-serial-port /dev/ttyACM0`, 115200 8N1). It uses the
existing H03 query/write allowlist, latches uncertain link failures and never switches
automatically from UDP. USB command framing/baud are community-derived; KORAD confirms
the interface exists but does not publish protocol details on its product page. In the
latest authorized attempt USB returned KEL103 V3.30 and INPUT OFF; shutdown's `:INP OFF`
also read back OFF. The later authorized H03 CH3 run verified USB CR 40 Ω setup/readback,
INPUT ON/OFF control, and KEL telemetry while powered (HW OBSERVED, not a complete
stage pass). The Keithley error queue returned `170, "Invalid command"` on the earlier
attempt after `SYST:REM`, but no queue baseline
was taken before that command, so attribution is unknown. The runner now checks and
records the queue before any mutation. The current offline suite passes 59/59. See the
[H03 procedure](H03_PREFLIGHT.ru.md), [earlier attempt report](hardware_reports/2026-10-25T235449.217553Z_H03_qualification.md), and [powered CH3 report](hardware_reports/2026-10-25T235757.702842Z_H03_qualification.md).


### H03 status diagnosis — OFF baseline and prepared powered probe

The direct-pyserial OFF probe independently of the library transport/device wrappers
returned operation masks 0/0/0. Short/full query forms agreed; questionable masks were
16/0/0 (CH1 bit 4 is recorded without assigning an undocumented meaning). All 33 error
reads were clear and both identity replies reported firmware 1.04-1.04. A second OFF
probe read all six operation/questionable ISUM enable masks as 0, with 12 clear error
reads. No output/setpoint/topology/enable writes occurred. See the
[OFF baseline and mask evidence](hardware_reports/2026-10-26T004315.108014Z_H03_status_off.md).

A safety defect in the H03 harness was independently reproduced offline: individual-ON
authorization was checked after transport transmission. The check now precedes transmission.
Mistyped operator confirmations wait for another answer without logging the rejected text.
The diagnostic `--diagnose-status` mode is restricted to H03/CH3 and records exact status
TX/RX bytes, short/full forms, early and post-display samples (>=5 s), and selected-channel
dependence. It reads enable masks without modifying them. Offline regression: **64/64
SIM TESTED**. The subsequently executed powered diagnostic is reviewed below; its raw runner outcome was PARTIAL on the redundant OPER mode check.


### H03 powered status diagnosis — 2026-10-26, completed

[Diagnostic report and manufacturer clarification draft](hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md).
Both CV/CC comparisons and display gates passed. All **274** error replies were clear.
Direct wire capture established `OPER3=8` (`38 0a`) in both modes, while `QUES3=1/2`
and library regulation agreed with CV/CC. Short/full commands agreed. CV snapshots at
2.521/82.668/83.514 s and CC snapshots at 2.541/47.937/48.778 s were unchanged, including
with CH1 selected. Both enable masks were 0; output states and final physical OFF verified.

The original library mode-reading defect is corrected and physically replayed; the new
harness authorization fix and diagnostic flow remain **64/64 SIM TESTED** for their offline
cases. The strict operation-mode discrepancy is confirmed at the device response boundary.
Its internal cause (firmware versus manual) is unproven; no further Python change can make
the instrument register return different bits without masking evidence. Reviewed H03 is
**PASS for functional operation with an OPER status caveat**. MET-01 is **HW OBSERVED**
within the reported load comparisons; calibrated DMM accuracy is unverified. At that report snapshot H04 was NOT_RUN; the later H04 run is PASS for OFF-only scope. The manufacturer question is prepared in the report and has not been sent.

### H03 external implementation review — 2026-10-26

[Source review](hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md#external-implementation-and-issue-review--2026-10-26):
two official Python examples and four independent Python drivers implement no CV/CC
status decoder. A C driver uses a questionable group event query; it does not document
a fix for our CH3 operation-condition discrepancy. No exact matching public report or
verified firmware correction was located in the sources inspected. No hardware access
or additional library patch resulted. This review does not change functional measurements: H03 is **PASS with the OPER caveat**.

### CH3 workaround API — offline validated, 66/66

The existing dual-condition decoder remains in use. `Reading.regulation_source`
now identifies operation/questionable/both evidence, or None for OFF/UNKNOWN/skipped
checks. No SCPI commands or mode conflict rules changed. Additional regressions
cover source attribution and CH3 required-mode trip behavior. After operator approval
`CONFIRM CH3 WORKAROUND OFFLINE`, the full offline command
`PYTHONPATH=src python -m unittest discover -s tests -v` passed **66/66** tests
(exit 0, unittest duration 0.289 s), without instrument access. This is **SIM TESTED**. The underlying
QUES-based CV/CC behavior is HW OBSERVED in the existing report; the new metadata
is not hardware verified. See [workaround and scope](../README.md#ch3-status-workaround-and-limitations).
H03 is reviewed PASS with the OPER caveat; at this offline snapshot H04 had not yet run. The current H04 outcome is PASS for OFF-only scope.

Offline result summary: `reports/hardware/2026-10-26T014250.764487Z_CH3_workaround_offline/result.json`.


### H03 reviewed stage disposition

**PASS for the tested functional scope**, with a recorded CH3 status-register caveat.
All six independent channel/mode points, live displays, KEL103 comparisons and final
OFF checks passed review. CH3 regulation readback uses QUES when OPER lacks its redundant
CV/CC bits; ON/OFF, U/I and setpoints remain readable. The diagnostic runner's raw
PARTIAL outcome records its strict manual bit comparison and remains in the raw report.
This does not block use of CH3. No calibrated DMM was used. H04 was NOT_RUN when this H03 review was written; the later H04 outcome is PASS for OFF-only scope.
See [H03 evidence](hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md).

### H04-Powered preparation snapshot (before the physical run)

H04-Powered was prepared as a low-energy supplement to the OFF-only H04 matrix
row. It defines 21 automated KEL103-controlled readings across CH1–CH3 (CV, CC and
fixed `power_only` presets), with operator pauses only to connect, move or disconnect
the load. The profile ceiling is 4.50 V / 0.500 A, one channel energized at a time;
out-of-profile requests are checked for local rejection before SCPI. The full offline
suite passed **70/70** (`PYTHONPATH=src python3 -m unittest discover -s tests -v`),
including the simulated 21-point H04-Powered flow. Initial tests exposed a mismatched
preflight assertion and an undersized command budget that also blocked OFF cleanup;
both were fixed, and the runner now reserves bounded shutdown commands. At that earlier
snapshot H04-Powered was **SIM TESTED only** and no instrument or load had been accessed;
its later physical result is recorded in the H04-Powered reviewed disposition above.

### H03 connected-load replay — 2026-10-26

[Current connected-load report](hardware_reports/2026-10-26T155700.328147Z_H03_qualification.md).
All six CV/CC points were executed with KEL103 USB connected. PSU and KEL readings met
the comparison tolerances, display confirmations matched, and all logged error queries
returned `0, "No error"`. The strict runner reports PARTIAL because CH3 OPER remained 8
for both modes; QUES returned 1/2 and display/KEL independently confirmed CV/CC. The
operator later confirmed `CONFIRM H03 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF`. Functional H03
is reviewed PASS with that telemetry caveat. A preceding 15:40 run initially recorded a
voltage mismatch, then the operator clarified the load was physically disconnected; it
is an unloaded scenario and excluded from connected-load qualification. See the
[correction addendum](hardware_reports/2026-10-26T154020.641252Z_H03_qualification.md).
