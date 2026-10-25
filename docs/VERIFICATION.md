# Feature verification matrix

[English](VERIFICATION.md) | [Русский](VERIFICATION.ru.md) · [Hardware test procedure](HARDWARE_TESTS.md)

## Meaning of statuses

- **HW VERIFIED** — observed using a physical PSU and a reproducible evidence report with model, firmware, software revision and test conditions. It is **not** an IEC/UL safety certification.
- **HW OBSERVED** — actual instrument response reported by operator, but no complete reproducible bench qualification report yet.
- **SIM TESTED** — exercised by automated fake-transport test(s), **not** proof that a real instrument understands commands or follows expected timing.
- **HW UNVERIFIED** — not yet confirmed on the physical model/firmware; it may be simulator-tested.
- **PLANNED** — no usable implementation yet.

**Scope of known physical observations:** owner connected a TTL USB–UART adapter to Keithley 2231A-30-3, firmware `1.04-1.04`, and successfully issued `SYSTem:REMote` and received valid `*IDN?`. The original terminal transcript is the sole evidence; no official report or full hardware qualification has yet been filed. In the strict *report-backed* sense, all features still require a repeatable published test record. Do not mistake old *observed* behaviour for all firmware variants.

| ID | Feature | Simulator | Physical hardware | Evidence required |
| --- | --- | --- | --- | --- |
| COM-01 | UART DB9 TTL communications 9600 8N1 | transport modeled | **HW OBSERVED** | repeatable report including connection and `*IDN?` |
| COM-02 | `SYST:REM` + `*IDN?` | basic query fake | **HW OBSERVED** | terminal record fw 1.04-1.04 |
| COM-03 | Additional read-only SCPI (`VOLT?`, `STAT?`, `SYST:VERS?`) | SIM TESTED | **HW UNVERIFIED** | `examples/probe.py` output vs front panel |
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
records **NOT_RUN** for every new hardware group. UART, `SYST:REM` and `*IDN?`
remain **HW OBSERVED**, supported only by the [operator observation](hardware_reports/2026-10-25_uart_idn_observation.md).
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
