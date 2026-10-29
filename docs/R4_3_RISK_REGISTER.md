# R4.3-A — risk disposition before extending the single-PSU SCPI API

**Status:** DESIGN / NO NEW ON-WIRE COMMANDS. Based on the original
Keithley 2231A-30-3 Reference Manual 077100401, §5 (September 2014) and the
bounded hardware verification of R4.2-A/B/C. Decisions below do **not**
authorize use or establish firmware 1.04-1.04 compatibility for untested forms.

## Safety-first command dispositions

| Vendor family | Contract risk | Current disposition / next condition |
|---|---|---|
| `*OPC?`, `*WAI` | Completion query can block or affect standard event status; WAI blocks later messages | **`*OPC?` QUERY-ONLY / HW REVIEWED PASS (one idle OFF-only `1`, 0.207 s, 2026-10-29)**: transport timeout <=3s, strict reply `1`, no retry; `*WAI` **DEFER** (write, no response, not a status snapshot) |
| `*PSC?` / `*PSC` | Reads vs changes power-on state of status enable registers | `*PSC?` R3.5.41 query-only HW REVIEWED PASS (one `0` reply, OFF-only on 2026-10-29), 0/1 strict response; no `*PSC` write, power-cycle or default-value assumption |
| `*TST?` | Self-test can have nontrivial execution time or effects | **DEFER**. Never run as routine health check until power/output behavior and shutdown/timeout protocol are reviewed |
| `*RST`, `*CLS`, `STAT:PRES` | Reset/configuration changes or irreversible clearing of diagnostics | **BLOCKED** by default. `*CLS` and `STAT:PRES` must not be conflated with explicit R4.2 event consumption |
| `*TRG`, `TRIGger`, `VOLT/CURR:TRIGgered` | Triggered source actions can change voltage/current | **DEFER** pending profile-aware gate, independent OFF transition, and dedicated controlled hardware qualification |
| `APPLy`, `APPLy:VOLT`, `APPLy:CURR` | Can change several channels/limits at once | **DEFER**. No raw/bulk bypass of per-channel caps, topology invariants or per-command readbacks |
| `VOLT/CURR:UP`, `DOWN`, `STEP` | Increment/decrement and step state may depend on current channel and previous settings | **DEFER**. Must never bypass bounded absolute setpoint validation or live profile integrity |
| `*SAV`, `*RCL` | Nonvolatile persistence; recall may change current settings or output-recall assumptions | **DEFER**. No automatic invocation or startup restore |
| `DISPlay`/text control | May obscure physical warnings/panel presentation, unrelated to safety-critical supply control | **LOW PRIORITY**; not required in the guarded core |
| `CALibration:*` | Calibration/service settings can irreversibly alter the instrument | **SEPARATE OFFLINE service-profile plans only** (R3.5.45); no live execution, standard/debug driver blocks service commands |

## Gates for the next technical increment

1. Validate **exact spelling, argument range and read/write side effects** for
   each chosen leaf command against its individual §5 manual page; the R4.1
   family table is not a verified allowlist.
2. Select **at most one** narrowly useful query or mutation per increment,
   and show why existing APIs do not already cover the need. Explicitly assess
   on-wire side effects and whether the operation would consume event/error
   evidence. `*OPC?` is not automatically an innocuous passive read.
3. Keep the existing fail-closed common SCPI dispatcher, bounded response
   validation, session fault latch and no retries after uncertain communication.
   Prohibit background SCPI or implicit reset/clear in new diagnostic methods.
4. Write and audit offline wire/fault tests **before** an operator-approved,
   OFF-only real-PSU test with a separate full JSONL trace and physical gate.
   More hazardous tests need a separate and explicitly justified plan.
5. Do not begin R4.3 hardware experiments simply because R4.2 received a
   bounded hardware PASS. Calibration/service, multi-PSU and MCU/bridge remain
   out of scope.

R4.2-B reviewed evidence: [R4_2_EVENT_review_2026-10-29.md](hardware_reports/R4_2_EVENT_review_2026-10-29.md).
Original manual: https://download.tek.com/manual/077100401_Referencemanual.pdf.

## R4.3-B PSC observational pilot (R3.5.41)

`power_on_status_clear_state()` reads one `*PSC?` through the public guarded
PSU driver; it does **not** read events or modify status enables. New hardware
runner independently gates one physical OFF-only PSU and compares `*ESE?` /
`*SRE?` before and after reading *PSC?. The two values should be equal in
the absence of an outside controller. **HW REVIEWED PASS** for one OFF-only
`*PSC? = 0` observation, with zero ESE/SRE before and after. This does not
prove power-on clearing, retention of nonzero enable masks or SRQ behavior.
See [independent review](hardware_reports/R4_3_PSC_review_2026-10-29.md)
and [hardware procedure](R4_3_PSC_HARDWARE.ru.md).

## R4.3-C *OPC? idle observational pilot (R3.5.43)

`operation_complete()` uses exactly one `*OPC?`, returns True only for integer
reply `1`, and requires a fresh, unarmed, profile-free and monitor-free driver
session. The reference manual (§5 p.5-3) documents completion semantics but does
not state an explicit maximum execution duration or unambiguous status-register
side-effect split between `*OPC` and `*OPC?`. Therefore **do not assume pure
read-only event preservation**, and never use this as a real-time watchdog.
The OFF-only hardware stage caps the configured serial response timeout at
3 seconds, checks all three channels and ESE/SRE before/after, and will not
retry, send shutdown, or reconnect after a failed query. `*WAI` remains blocked;
no trigger, queued-work, or pending-operation test is authorized.

Observed on hardware: **HW REVIEWED PASS**, one idle `*OPC?=1` in 0.207 s;
zero ESE/SRE before and after, six channel OFF readbacks. This is no proof
of queued-operation synchronization, max latency, OPC bit behavior, or event-latch
preservation. Review: [R4_3_OPC_review_2026-10-29.md](hardware_reports/R4_3_OPC_review_2026-10-29.md).

Protocol: [R4_3_OPC_HARDWARE.ru.md](R4_3_OPC_HARDWARE.ru.md).

## R4.3-D Service-mode offline foundation (R3.5.45)

An isolated `ServiceProfile` and `ServicePlanner` compile only explicit,
per-operation typed command plans. They do not execute or import a transport.
The normal driver blocks service commands before automatic identity or Remote
setup, including under `debug_bypass_profile=True`; note that direct UART
access by untrusted Python code is outside library protection. `*TST?` and
`*PSC` write forms have reference-manual ambiguities and are **unverified**.
The calibration sequence is an offline proposal only; no complete manufacturer
adjustment method, hardware verification, external references or runtime gateway
has been qualified. See `R4_3_SERVICE_MODE.ru.md`; **HW NOT_RUN** throughout.


## R3.5.49: архитектурная корректировка

Синтетические service simulator/gateway/authority удалены. ServiceProfile и
ServicePlanner теперь выполняют только политику и проверку грамматики;
реальный `Keithley2231A.service_operation()` выполняет разрешённые шаги через
общий dispatcher/Transport. Программная квалификация через псевдо-UART: это
проверка байтов и fail-closed пути, **не hardware PASS**. Калибровка и
неоднозначные команды по-прежнему не имеют wire-допуска.
