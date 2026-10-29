# R4.3-A — risk disposition before extending the single-PSU SCPI API

**Status:** DESIGN / NO NEW ON-WIRE COMMANDS. Based on the original
Keithley 2231A-30-3 Reference Manual 077100401, §5 (September 2014) and the
bounded hardware verification of R4.2-A/B/C. Decisions below do **not**
authorize use or establish firmware 1.04-1.04 compatibility for untested forms.

## Safety-first command dispositions

| Vendor family | Contract risk | Current disposition / next condition |
|---|---|---|
| `*OPC?`, `*WAI` | Can wait for queued operations; completion semantics differ from status snapshots | **REVIEW** before any live query; specify timeout/interaction with the transport and concurrent guard first |
| `*PSC?` / `*PSC` | Reads vs changes power-on state of status enable registers | Query may be a candidate after manual syntax/side-effect review; **no write** and no inherited-default assumption |
| `*TST?` | Self-test can have nontrivial execution time or effects | **DEFER**. Never run as routine health check until power/output behavior and shutdown/timeout protocol are reviewed |
| `*RST`, `*CLS`, `STAT:PRES` | Reset/configuration changes or irreversible clearing of diagnostics | **BLOCKED** by default. `*CLS` and `STAT:PRES` must not be conflated with explicit R4.2 event consumption |
| `*TRG`, `TRIGger`, `VOLT/CURR:TRIGgered` | Triggered source actions can change voltage/current | **DEFER** pending profile-aware gate, independent OFF transition, and dedicated controlled hardware qualification |
| `APPLy`, `APPLy:VOLT`, `APPLy:CURR` | Can change several channels/limits at once | **DEFER**. No raw/bulk bypass of per-channel caps, topology invariants or per-command readbacks |
| `VOLT/CURR:UP`, `DOWN`, `STEP` | Increment/decrement and step state may depend on current channel and previous settings | **DEFER**. Must never bypass bounded absolute setpoint validation or live profile integrity |
| `*SAV`, `*RCL` | Nonvolatile persistence; recall may change current settings or output-recall assumptions | **DEFER**. No automatic invocation or startup restore |
| `DISPlay`/text control | May obscure physical warnings/panel presentation, unrelated to safety-critical supply control | **LOW PRIORITY**; not required in the guarded core |
| `CALibration:*` | Calibration/service settings can irreversibly alter the instrument | **EXCLUDE** from public API and from automatic / debug hardware tests |

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
