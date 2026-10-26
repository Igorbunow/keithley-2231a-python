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
| LOAD-01 | KEL103 H03 control over UDP or USB CDC serial | UDP physically exercised on CH2; USB IDN, CR setup/readback, INPUT control and telemetry observed | **USB HW OBSERVED** (not a complete pass) | USB H03 CH3 test review; status interpretation remains open |
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
| MET-01 | Read voltage/current/status and inferred CV/CC | SIM TESTED | **HW OBSERVED (H03; OPER3 manual discrepancy)** | CV/CC confirmed by KEL103 and panels; calibrated DMM accuracy unverified; [diagnosis](hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md) |
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

`tools/hw_qualify.py list`, `preflight` and `run` default to dry run; no port opens.
H01 has a read-only scenario with its own interactive gate: remote/local control,
selected-channel queries, all-channel ON status bits and two error queue reads.
H02 has a manually gated OFF-only profile scenario. Its initial hardware attempt
failed on an in-session `*IDN?` firmware mismatch, then the corrected scenario passed;
the offline suite is 48/48. See the [H02 operator card](H02_PREFLIGHT.ru.md) and both
physical reports below. H03 is PARTIAL: reviewed CH1 and CH2 CV/CC points pass; CH3 CV
and CC were both observed; the device operation-mode response still deviates from the manual (diagnosis below). KEL103 UDP
control was exercised on hardware for CH2. H04–H12/O01/O02 remain pending individual
review.

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

### H02 preparation — 2026-10-26

Prepared `apply_profile()` / `verify_profile()` with all channels at 1.00 V / 0.100 A,
4.00 V voltage cap and 0.500 A software current cap, outputs OFF, load disconnected.
The runner rejects command-plan drift before transmission and checks the error queue
after every write except LOC. Prepared regressions cover the successful trace,
error 170, uncertain I/O, cap mismatch, initial ON and prevention of ON transmission.
These checks were **SIM TESTED** before the hardware run. The first H02 attempt **FAILED**:
the first `*IDN?` returned firmware `1.04-1.04`; the driver's second identity query
returned `1.04-0.01`. All three profile writes completed, every observed
`SYST:ERR?` returned `0, "No error"`, and all channel OFF bits read 0. The runner's
identity comparison happened too late, after writes. The operator then confirmed
all outputs OFF, load OFF/disconnected, and setpoints 1.00 V / 0.100 A on CH1–CH3.
The library now latches an identity change and aborts before profile writes; focused
offline regression is verified in the fixed suite (48/48). On the corrected H02 rerun,
both `*IDN?` responses matched at firmware `1.04-0.01`; all SCPI error replies remained
`0, "No error"`, the profile readbacks passed on all channels, and OFF bits were 0/0/0.
The operator confirmed all outputs OFF, load OFF/disconnected, and 1.00 V / 0.100 A
setpoints on every channel. See [first attempt](hardware_reports/2026-10-25T213651.354322Z_H02_qualification.md)
and [corrected H02 PASS](hardware_reports/2026-10-25T214134.648837Z_H02_qualification.md).
H02 is **HW OBSERVED PASS** for the no-load profile application/readback; PRF-01/PRF-02
broader electrical behavior remains **HW UNVERIFIED**. No later stage is authorized.

### H03 — CH1/CH2 CV/CC reviewed PASS; CH3 status requires diagnosis

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
SIM TESTED**. The subsequently executed powered diagnostic is reviewed below; H03 remains **PARTIAL**.


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
the instrument register return different bits without masking evidence. H03 remains
**PARTIAL**, MET-01 is **HW OBSERVED** within the reported load comparisons, and H04 is
not started. The manufacturer question is prepared in the report and has not been sent.

### H03 external implementation review — 2026-10-26

[Source review](hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md#external-implementation-and-issue-review--2026-10-26):
two official Python examples and four independent Python drivers implement no CV/CC
status decoder. A C driver uses a questionable group event query; it does not document
a fix for our CH3 operation-condition discrepancy. No exact matching public report or
verified firmware correction was located in the sources inspected. No hardware access
or additional library patch resulted; H03 remains **PARTIAL**.

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
H03 remains PARTIAL; H04 NOT_RUN.

Offline result summary: `reports/hardware/2026-10-26T014250.764487Z_CH3_workaround_offline/result.json`.
