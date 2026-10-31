# Roadmap — Keithley 2231A-30-3 (single-instrument library)

**Single authoritative roadmap / Единственный актуальный roadmap.** The old
`docs/ROADMAP.ru.md` was removed in R3.5.34 as obsolete and misleading.
Project scope: Python API for **one Keithley 2231A-30-3**, not a general
bench controller. Actual release versions are decided at release time; do
not use outdated `v0.2/v0.3/v0.4/v0.5` sketches as engineering milestones.

## Completed: R1–R3.5 (qualified scope only)

- R1: bench config, isolated hardware adapters, reproducible offline tests.
- R2: common guarded SCPI path, profiles, no duplicate driver dispatch.
- R3: safety state machine, topology checks, evidence, transport faults.
- R3.5: H01–H12 hardware qualification in **documented bounded scopes**;
  O01 single-PSU + DMM observer reviewed PASS (sequential sampling, not sync).
- Caveats remain: CH3 OPER mode bits, Series CC conflicting status, firmware
  Aux revision behavior, unmeasured Parallel branch-sharing, USB-loss hardware
  fault not performed. Tests or observed output do not imply metrology calibration.

## R3.5.50 — extend actual driver + virtual UART testing

The existing R4.3 `*PSC?` and idle `*OPC?` hardware qualification functions now
run against a strict POSIX virtual UART peer, through the production driver and,
when installed, `SerialTransport`. Assert exact 75-command traces and fail-stop
on invalid readback/timeout/SCPI error/output ON. This adds **offline evidence**
only. Historical mock-transport tests remain and will be migrated selectively,
without inventing a parallel driver or service execution layer.

## R3.5.51 — R4.2 status-mask tests on actual driver wire

Migrate the two R4.2-A/C test modules from `FakeTransport` subclasses to
production `Keithley2231A` over the strict POSIX virtual UART. Check full
wire order, CRLF bytes, four top-level and eight nested enable masks, module
revision observation and fail-stop on malformed/missing replies. One test
injects an already-armed in-memory driver state (not a physical arming test).
The test UART returns only canned allowlisted replies; no real instrument,
calibration or event-consuming reads. Other historical mocks remain pending.

## R3.5.52 — event-consumption tests on actual driver wire

Replace the last R4.2-B `FakeTransport` event-test subclass with the production
`Keithley2231A` over the existing strict virtual UART. The test peer exposes
only three read-to-clear latch fixtures and deliberately consumes the latch
before a response can be lost. Assert exact OFF preflight, channel restoration,
wire CRLF, one explicitly authorized event query, and no retries or implicit
`SYST:ERR?`/event clears. Compare the pre-event command sequence against the
previously independently reviewed R4.2 EVENT evidence, accounting for the
hardware stage's own separate SCPI error polls. No new hardware or calibration
commands and no new execution layer. Actual physical latch-clearing effect
remains unverified by a repeated hardware read.

## R3.5.54 — powered fault shutdown over virtual UART

Replace three historical armed SCPI-error `FakeTransport` tests with direct
production-driver + real PTY transport checks, adding ON→fault→OFF wire
verification and uncertain-link failure injection. The virtual peer supports
only the few typed registers needed for real `apply_profile()` and `output()`.
No new service abstraction or live hardware execution; actual physical
shutdown still requires operator-confirmed hardware evidence.

## R3.5.57 — topology gates and profile-integrity checks over virtual UART

Replace seven historical test methods from the stateful `FakeTransport`
fixture with eight production-driver tests over the strict POSIX UART. Preserve the
physical-wiring approval gates for Independent/Tracking/Series/Parallel,
verify zero bytes before an invalid confirmation, and cover post-configuration
fail-stop cleanup in all three applicable profile-application paths. Verify
selected-channel alias detection, inherited timer disable, frozen and updated
setpoint integrity, and unauthorized external output activation. The minimal
UART fixture permits `OUTP:SER 1` / `OUTP:PARALLEL 1` only for deterministic
injected-fault traces; it does **not** simulate combined hardware behavior or
establish safe electrical wiring. Physical H09/H10 evidence is unchanged.
No new production code, hardware tests, or service operations.

## R4.4-A — safe scalar/display reads (software-only)

After R3.5.64, five documented query forms are exposed through the existing
production driver and exercised with an explicit-reply POSIX UART peer:
`DISP?`, `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?`, `FETC:POW?`.
No display write, triggered source action, reset, persistent configuration,
status-latch consumption, hardware power enable, or new execution engine.
`measure_power()` does not replace `read_channel()`'s sequential U×I; fetch
values are not guaranteed fresh or synchronized. **SIM TESTED, HW NOT_RUN**;
independent hardware confirmation is a separate operator gate.
See `docs/SCPI_COVERAGE.md` and `docs/R4_3_RISK_REGISTER.md`.

## Next: R4 — SCPI coverage (current priority)

1. **R4.1 inventory:** review vendor 2231A reference manual 077100401 §5,
   map documented command families to the driver's actual on-wire methods,
   coverage status, side effects, safe scope, and evidence. Start with
   [`docs/SCPI_COVERAGE.md`](docs/SCPI_COVERAGE.md). No hardware commands.
2. **R4.2 safe gaps:** add justified status/identity/readback operations only
   through the shared dispatcher; unit tests and fail-closed behavior first.
   R4.2-A/C read-only status group: HW REVIEWED PASS (2026-10-29, bounded);
   R4.2-B event-consuming queries: **HW REVIEWED PASS** in a bounded one-shot
   OFF-only run (2026-10-29); actual latch clearing was not measured.
3. **R4.3 risky gaps:** start with the command-level risk disposition
   [`docs/R4_3_RISK_REGISTER.md`](docs/R4_3_RISK_REGISTER.md); review
   trigger/apply/step/save/recall/display commands separately. Never expose
   arbitrary write access or bypass existing profile and output gates.
   R3.5.49 removes synthetic service execution layers, routes service SCPI
   through the single driver under explicit service-profile authorization,
   and tests actual driver/transport bytes over a POSIX virtual UART.
4. **R4.4 verification:** trace auditor, firmware caveats, documented exclusions,
   targeted operator-approved hardware checks only if indispensable.

Completion: every vendor SCPI **family** has an explicit disposition, including
unsupported/unsafe commands. Completeness never means blindly executing them.

## R4.3-H architecture correction (R3.5.49)

Removed the three duplicate synthetic service execution layers. One real
`Keithley2231A` driver and its guarded SCPI dispatcher are now exercised over
a virtual UART for service-path tests. Separate service profile authorizes each
operation; dangerous or unverified calibration commands remain wire-blocked.
A virtual UART test is NOT a real-firmware or metrology qualification.

## R5 — ergonomic high-level API and capabilities

R5-A: immutable, read-only model capability contract for one 2231A-30-3,
exposing native hardware support separately from guarded driver exposure
and bounded evidence. No live SCPI commands, runtime identification or bench
orchestration. See `docs/R5_A_STATIC_DEVICE_CAPABILITIES.ru.md`. This remains
a software-only increment until the user verifies the full regression.

- Channel API (CH1/CH2/CH3) *delegating* to common guards, not copying SCPI.
- Accurate device capability metadata and observed-vs-simulated distinctions.
- Typed profile validation, versioned serialization, documented ownership and
  connection/close/unknown-link semantics.

## R5-B — exhaustive SCPI family disposition (2026-10-31, OFFLINE)

Reviewed all 86 documented family rows against the R5-A code and the manufacturer §5. The prior R4.1 register understated bounded STEP/UP/DOWN support. Current disposition: **53 W (at least one guarded route), 25 R (deferred/risk), 8 X (calibration)**. These are family counts, not percent of complete SCPI verb/argument support or HW verified status. See `docs/R5_B_SCPI_GAP_AUDIT.ru.md` and the updated `docs/SCPI_COVERAGE.md`.

- **R5-C, software-only:** strengthen typed capability descriptors, distinguish CH1-only STEP evidence, native trigger setpoints and trigger coupling (`documented_only`) from driver-exposed features, correct stale `source_step()` docstring; keep SCPI code unchanged unless separately justified.
- **R5-C completed in software patch (pending operator regression):** schema v2 typed
  `HardwareEvidence` evidence envelopes for STEP query/OFF configure/OFF mutation
  and limited CH1 powered CV/CC qualification; trigger/coupling/APPLy/OUTP:ENAB
  remain documented-only. Do not treat evidence metadata as control permission.
  See `docs/R5_C_STEP_SCOPED_CAPABILITIES.ru.md`.
- **R5-D, conditional research:** scoped OFF-only readback of `VOLT:TRIG?`, `CURR:TRIG?` and `INST:COUP?`, first virtual UART, then individually approved real hardware if indispensable.
- **R5-E, conditional and separately approved:** only if useful, supervised trigger/multichannel APPLy feasibility. No auto-enable, no assumed atomicity, no bypass of profiles, no normal-path calibration writes.
- Continue channel ergonomics, ownership/transport contracts and R6 packaging irrespective of whether the optional hardware investigations are taken.

## R6 — release hardening

- Offline CI, optional dependencies and packaging (`wheel`/`sdist`).
- Exclude `tests/hardware/`, private bench config, raw evidence and `experiments/`
  from the published library. Documentation + firmware compatibility matrix.
- Targeted hardware requalification for changed hazardous paths only; no
  ungrounded `HW VERIFIED` or unattended-safety claims.

## Outside this library — frozen prototypes

- `experiments/o02_dual_psu/`: O02-A synthetic two-PSU draft only. It is
  **not** a driver, not HW qualified, not to be used in R4/R5/R6.
- O02-B/C multi-PSU timing, MCU bridges, hardware triggers, generic instrument
  drivers, and bench automation belong to a potential **separate top-level
  project** after requirements and hardware are established.
- O01 test-only DMM integration remains a *bounded qualification adapter*, not
  an invitation to put a generic bench orchestrator into `src/`.

#### R4.3-B (*PSC? read-only, R3.5.41)

One guarded query-only method with strict 0/1 response and a separate OFF-only
hardware qualification tool. `*PSC` writes, power cycles and all dangerous R4.3
commands remain unsupported. **HW REVIEWED PASS** for the single OFF-only
`*PSC? = 0` query on 2026-10-29; this does not test the power-on setting
effect or nonzero enable masks. Protocol: `docs/R4_3_PSC_HARDWARE.ru.md`;
independent [evidence review](docs/hardware_reports/R4_3_PSC_review_2026-10-29.md).


#### R4.3-C (idle `*OPC?` query, R3.5.43)

One narrowly guarded completion query for a fresh, profile-free session;
`*WAI`/`*OPC` writes blocked. OFF-only hardware gate with finite serial timeout
and full JSONL evidence has **HW REVIEWED PASS (bounded idle-only,
2026-10-29)**: one `*OPC?=1` response in 0.207 s, six OFF readbacks,
unchanged zero ESE/SRE masks. No general asynchronous timing,
status latch preservation, or trigger verification is claimed. See
[the independent review](docs/hardware_reports/R4_3_OPC_review_2026-10-29.md).

#### R3.5.53 — R4.2 / error-response regression over virtual UART

Four former `FakeTransport`-backed tests of malformed identity, selected
channel, output readback and unarmed SCPI error are now verified through
`Keithley2231A` on a strict virtual UART, with additional malformed/timeout
error-response scenarios. No driver changes, physical qualification or service
execution. Other legacy fakes remain for separate case-by-case migration.

## R3.5.55 — пакетная миграция трёх программных групп

1. Публичный `*PSC?`: 0/1, некорректные ответы, потеря UART, защита
   при faulted и наблюдение без изменения включённого выхода.
2. Публичный `*OPC?`: один запрос, строгий ответ `1`, отказ до передачи
   при armed/profile/sequencer, отсутствие повтора после неопределённости.
3. Monitor профиля: реальный `apply_profile()` и фоновый поток через PTY,
   обнаружение дрейфа VOLT/CURR/LIMIT/TOPOLOGY, подтверждённое отключение
   в тестовом peer и запрет OFF при потере UART-ответа.

Отдельные hardware runner тесты ограничений продолжают проверять свои
независимые защитные условия; остальные исторические `FakeTransport`-сценарии
планируются для постепенного перевода без потери покрытия. Никаких новых
разрешений реальным калибровочным командам этот пакет не даёт.

## R3.5.56 — четыре группы UART-тестов, единый инкремент

Заменены 15 `FakeTransport`-тестов: состояния выходов, `*IDN?`/серийный номер,
диагностический dispatch и некорректные числовые ответы. Все новые тесты
используют `Keithley2231A` и тестовый PTY. Production-драйвер не меняется;
сервисный/калибровочный доступ не расширяется. Остальные сценарии FakeTransport
сохраняются до их поштучной миграции без потери покрытия.

## R3.5.58 — sequence and tracking-control regressions (software only)

Migrate four historical tests to six strict virtual-UART tests invoking the
production `Keithley2231A`: ordered independent channel ON/OFF, per-channel
programmable timer write/readback, Tracking mode readback and explicit CH3
confirmation, Tracking timer pair with OFF-only constraint, delayed global OFF
acknowledgement, and two distinct fault outcomes (coherent wrong reply leads
to fault and verified OFF; missing UART reply leads to unknown output state
without unsafe retries). The peer exposes only digital SCPI readbacks, no
independent instrument control logic or electrical simulation. Keep the old
voltage-coupling and physical Series/Parallel scenarios until a truthful
bounded test can replace them. No real-hardware testing is implied.

## R3.5.59 — Tracking voltage readbacks via production-driver virtual UART

The historical FakeTransport tracking-voltage test is migrated to three
production-driver/PTY tests for ratios 1:1 and 1:2: pre-I/O rejection of
out-of-cap, zero, nonfinite, unresolvable, and wrong-channel setpoints;
accepted CH1/CH2 SCPI writes with explicit, **per-channel scripted** VOLT?
responses; and linked-channel readback disagreement causing a fault with
confirmed OFF. No automatic linked-channel voltage simulation was introduced:
scripted replies are protocol fixtures, **not** measured coupling or a
substitute for H08. No change to production SCPI or physical qualifications.

## R3.5.60 — bounded Series/Parallel aggregate SCPI regressions (offline)

Three historical FakeTransport tests move to production-driver POSIX UART:
Series aggregate-limit mismatch trips before arming, Parallel 4 A current
setpoint is programmed only after the mode command, and CH3 limit drift
trips in both modes. A negative fixture-validation test is also included.
The peer chooses **literal scripted digital readbacks** when a topology mode
is selected; it never computes branch current, coupled voltage, or aggregate
limit from independent settings. No electrical proof, physical wiring check,
firmware behavior simulation, new SCPI permission, or H09/H10 status change.

## R3.5.61 — batched production-UART driver regression (offline only)

A single software-only increment replaces 22 remaining `FakeTransport` test
methods with 23 production-`Keithley2231A` / POSIX virtual-UART tests across
`test_combined_workflow_uart.py` and `test_output_measurement_uart.py`.
Scope: explicit Series/Parallel operator gating, aggregate CH1+CH2 setpoints,
CH3 limits, safe mode exit confirmation, digital output/readback disagreement,
verified global OFF + individual OFF fallback, profile and setpoint limits,
measured overvoltage/overcurrent, CH3 CV/CC status evidence, read-to-clear-free
measurement, acquisition and selective `record=False` safety.
The UART peer returns fixed digital text and never implements electrical
combining, channel current sharing, voltage coupling, or real OFF confirmation.
The hardware H09/H10 and CH3 firmware caveats remain unchanged. Other
`FakeTransport` unit tests not yet migrated must retain their original
behavior until individually justified. No changes to `src/` or hardware tests.

## R3.5.62 — batched offline driver guard migration and fixture ownership

- Replace remaining `tests/test_driver.py` FakeTransport-backed test methods
  with 11 `Keithley2231A` POSIX virtual-UART cases, including safety around
  absent replies and invalid host transport types. Explicitly distinguish
  coherent verified shutdown from uncertain-link no-retry semantics.
- Move the existing qualification-only FakeTransport implementation unchanged
  into `tests/support/qualification_transport.py` and update all direct
  consumers, without introducing compatibility imports, SCPI execution
  alternatives, or duplicating stage logic.
- Existing qualification runner tests using a synthetic transport are retained
  as **offline runner tests**, not converted to electrical evidence. No H09/H10
  requalification or live output permission is implied by this patch.
- Gate: full software suite + `compileall`, staged patch SHA match, and no
  production source modifications.

## R3.5.63 — pyserial-specific close assertion fix and large UART batch (offline)

- Address **observed R3.5.62 regression** from the user's 2026-10-29 run:
  `SerialTransport` has no `.closed` property, although PTYTransport does.
  Use the shared transport contract: after `close()`, further `query()` must
  fail and must not emit SCPI bytes. No production `closed` shim is added.
- Migrate six standalone inherited-Series/Parallel topology and status tests
  to the real `Keithley2231A` dispatcher through a POSIX UART peer, using
  fixed readbacks for aggregate-register and CV/CC evidence.
- Consolidate the remaining monitor lifecycle and fault-handling tests onto
  the existing monitor UART suite (interval validation, stop/join, close,
  unarmed/faulted refusal, Parallel CH3 drift, in-flight lock timeout).
- Delete `tests/test_r3_topology_status.py` and
  `tests/test_profile_integrity_monitor.py` after verifying replacement.
- Eliminate the accidentally retained duplicate `FakeTransport` in
  `tests/test_driver.py`; preserve the single qualification-runner fixture.
- Run the full software suite and a separate target check on the user's
  pyserial environment. **Hardware execution is not authorized by this step**.

## R3.5.64 — finish application-level power-only/sweep UART migration (offline)

- Replace `tests/test_profiles_sweep_bench.py` with combined pure-profile unit
  checks and production-driver virtual-UART cases in
  `tests/offline/test_profiles_sweep_uart.py`.
- Assert forward and reverse sequencing, timed steps, restricted APIs, rated
  limits, readback uncertainty versus coherent OFF, guarded voltage sweeps,
  CSV/observer results and validation-before-SCPI behavior.
- Keep historical qualification-runner transport tests and their one existing
  `tests/support/qualification_transport.py` fixture: these are not a
  duplicate production SCPI dispatcher or physical qualification.
- Gates: full offline tests, compilation, stage path/SHA reproducibility,
  and separate pyserial-equipped verification on the user environment.
- Next: move to R4 SCPI work rather than extending R3.5 migration indefinitely.
  R4 changes affecting actual output power need separate authorization.

## R4.4-B — bounded OFF-only display query stage (software prepared, HW NOT_RUN)

- New explicit TTY-gated `tools/r4_4_display_qualify.py` and a constrained
  `tests/hardware/r4_4_display_stage.py` with one `DISP?` via the real driver.
- No profile, outputs, setpoints or topology changes; only REM/LOC and temporary
  channel selection for OFF checks. Query errors/timeouts never retry or
  attempt an unverified shutdown.
- Virtual-UART tests verify exact wire, readback validation, ON refusal,
  error-queue polling, no later SCPI after uncertain replies, and dry-run.
- **Do not declare hardware-qualified until real evidence has been reviewed.**
  Scalar measurement queries need a separate low-power R4.4-C gated phase;
  see `docs/R4_4_HARDWARE_PLAN.ru.md`.

## R4.4-B — physical DISP? evidence reviewed (2026-10-29)

- **HW REVIEWED PASS**, scoped to a single `DISP?=1` reply on physical
  Keithley 2231A-30-3 firmware `1.04-1.04`, with CH1/2/3 twice verified OFF.
- Audited 254 JSONL records: 57 queries, 10 writes, 33 zero SCPI errors,
  no power/setpoint/topology writes. Operator physically confirmed final OFF.
- A standalone fail-closed offline auditor and its regression tests preserve
  reproducibility without including local evidence or silently editing it.
- **R4.4-C measurement query hardware status stays NOT_RUN.** The next
  controlled step is a separately reviewed powered CH1 procedure with KEL103
  and DMM7510, not a continuation of the OFF-only test session.

## R4.4-C — bounded scalar + external metrology preparation (software-only)

- One independent low-power CH1 point, PSU/KEL103/DMM7510 operator-gated.
- Configuration from validated bench TOML; exact fixed 3.30 V / 0.150 A / CR40Ω / 0.60 W.
- Shared production driver, strict SCPI allowlist, no extra SCPI gateway.
- DMM7510 10 V DC mode, independent load OFF control, one CH1 ON authorization,
  readback-confirmed PSU/LOAD shutdown, physical final OFF gate.
- Compare native `MEAS:POW?` with source U×I and KEL power; observe FETC
  with finite/range checks but *without claiming freshness or simultaneity*.
- Test UART faults, power overrange, wrong topology, operator refusal and
  non-atomic readings offline before operator authorizes powered execution.
- **HW NOT_RUN / NOT_REVIEWED** until manual powered trace is independently audited.


### R4.4-C failed hardware trial — 2026-10-29

The first real powered CH1 scalar attempt is **HW ABORTED**, not HW PASS: DMM7510 returned 11.61243 V on configured 10 V DCV range, while Keithley/KEL103 both measured approximately 3.30 V. The runner correctly stopped and confirmed OFF on all PSU channels and KEL INPUT. Do not re-run on power until DMM wiring/measurement is separately investigated. Full evidence audit: `docs/reviews/R4_4_C_SCALAR_ABORT_2026-10-29.md`. The follow-up fix adds diagnostic detail and immediate cross-meter checking; it is **software-only** and does not change the HW qualification result.


### R4.4-C one-point hardware review, 2026-10-29

**HW REVIEWED PASS, bounded:** one Independent CH1 3.30 V / 0.150 A limit /
KEL CR 40 Ω point, with independently consistent PSU, KEL103 and DMM7510
measurements. Real `MEAS:POW?=0.267863 W`, `FETC:VOLT?=3.2996 V`,
`FETC:CURR?=0.0811749 A`, `FETC:POW?=0.267907 W`. Buffer freshness and
synchronized sampling are **not** demonstrated. No new higher-power or topology
qualification. Review: `docs/reviews/R4_4_C_SCALAR_HW_REVIEW_2026-10-29.md`;
raw evidence SHA-256 `138942d72bc95c3bacba15ed776372d7207b48333fef7ee4b41f4f76790b340c`.
Corrected future `psu_identity` event redaction without touching raw evidence.


## R4.5-A — digital source observation, software only

- Added read-only symbolic `INST?` and Independent-profile `VOLT:STEP?` /
  `CURR:STEP?` to the existing production driver. All have virtual-UART
  positive and fail-closed negative coverage; no other executor/framework.
- No new SCPI write, output ON/OFF, trigger, STEP change or storage action.
  Profiling/integrity and uncertain-link restrictions remain effective.
- **SIM TESTED / HW NOT_RUN**: hardware command syntax and replies must be
  confirmed under a distinct, OFF-only, operator-gated protocol. The vendor
  `VOLT:STEP?` return-type entry is inconsistent; do not assume real firmware
  returns a numeric value merely because the offline UART does.
- Next: document and separately approve an OFF-only hardware plan for the
  three new queries; leave dangerous step/UP/DOWN/trigger/APPLy commands gated.

### R4.5-B hardware qualification prepared (SIM ONLY)

Added a default-dry-run, two-gate OFF-only stage: `INST?` without profile,
then optional independent OFF-profile CH1 `VOLT:STEP?`/`CURR:STEP?` after
a separate operator decision. No ON command is allowlisted. Hardware reply
semantics and JSONL independent review remain outstanding.

### R4.5-B INST terminal review (2026-10-29)

Коммит `a1af3d5` и 675 тестов OK; реальное `INST? -> CH1`, OFF три раза по каждому каналу; исходный JSONL отсутствует в приложениях. Отдельный офлайн-аудитор добавлен, STEP квалификация остаётся неиспытанной.

### R4.5-B independent original JSONL review — 2026-10-29

**HW REVIEWED PASS (bounded):** both original OFF-only traces audited.
`INST?=CH1` on both runs; on the separately approved OFF-profile run,
`VOLT:STEP?=0.001 V` and `CURR:STEP?=0.001 A` on CH1, each queried exactly once.
All 33 STEP-run output readbacks OFF; all 232 error polls zero; profile presets
read back at 3.30 V/0.150 A CH1 and 1.00 V/0.100 A CH2/CH3, remaining programmed
but OFF. No power ON or STEP writes were sent. Evidence digests and limits:
`docs/reviews/R4_5_B_INST_STEP_HW_REVIEW_2026-10-29.md`.
Next work is software-only risk-based SCPI coverage; no automatic promotion of
write/UP/DOWN/trigger functions or powered modes.

## R4.5-C — typed, bounded STEP writes and UP/DOWN (software-only)

- Implemented production `configure_source_step`/`step_source` for independent
  all-OFF sessions. One explicit internal dispatcher authorization per exact
  source mutation; diagnostic bypass and service restrictions remain intact.
- Strict readback of selected channel, configured step, previous/final digital
  preset, normal SCPI error poll after writes, profile re-verification and
  millivolt-aware expected-setpoint integrity. Normal ON is blocked after
  configuring STEP; fresh profile/session required to re-arm standard use.
- Virtual UART models register responses, **not** measured analog behavior.
- **SIM TESTED; STEP write, UP/DOWN HW NOT_RUN; NOT EXPERIMENT READY.**
- R4.5-D OFF-only hardware; R4.5-E measured CV/CC under load; R4.5-F
  useful V–I experiment with independent U/I/P readings, all separately gated.
- Mandatory scope and acceptance matrix: `docs/R4_5_FUNCTIONAL_QUALIFICATION.ru.md`.

## R4.5-D — OFF-only STEP write + UP/DOWN qualification prepared

- One-case gated hardware runner, independent fixed profile, strict channel,
  kind, step and direction-scoped SCPI allowlist; no output enable or load.
- Real digital readbacks required after STEP write and every UP/DOWN;
  bounded shutdown and mandatory physical OFF, fail-closed on uncertain UART.
- Initially 24 CH1/2/3 × VOLT/CURR × increment cases planned, later reduced to 18 after real 1mV voltage STEP refusal;
  boundary and combined experiments need additional evidence.
- Virtual UART and independent patch regression only; **HW NOT_RUN**.
  R4.5-E CV/CC analog qualification and R4.5-F I–V sweep remain separate.
  See `docs/R4_5_D_STEP_MUTATION_OFF_PLAN.ru.md`.

### R4.5-D first OFF-only physical STEP mutation — 2026-10-30

- **HW REVIEWED PASS (one bounded case only):** CH1 `VOLT:STEP 0.010`, then
  two `VOLT:UP` and two `VOLT:DOWN`; digital `VOLT?`: 3.30, 3.31,
  3.32, 3.31, 3.30 V. Keithley 2231A-30-3 firmware 1.04-1.04.
- Independently audited original 4449-record JSONL: 1002 query/reply pairs,
  216 writes, 611 zero-error polls, 78 OFF readbacks, one STEP write and
  exactly four ordered directional commands. Operator final OFF confirmed.
- Evidence hash and limitations: `docs/reviews/R4_5_D_CH1_VOLT_10MV_HW_REVIEW_2026-10-30.md`.
  Automated replay audit: `tools/audit_r4_5_step_mutation_evidence.py`.
- **Remaining cases in the subsequently revised 18-case matrix, analog measurement under load, dynamic CV/CC,
  V-I experiments and boundary combinations NOT VERIFIED**. Next case:
  CH1 VOLT 0.001 OFF-only, independent manual execution and evidence review.

### R4.5-D voltage STEP refusal (2026-10-30)

CH1 `VOLT:STEP 0.010` digital OFF-only HW REVIEWED PASS;
`VOLT:STEP 0.001` returned SCPI 120 (Parameter overflowed), aborted safely.
Voltage STEP writes now require 10 mV grid (datasheet setting resolution),
current STEP writes 1 mA grid; no powered STEP or CV/CC readiness is claimed.
See `docs/reviews/R4_5_D_CH1_VOLT_1MV_REJECTION_2026-10-30.md`.

### R4.5-D second accepted OFF-only mutation — CH1 CURR 1 mA

**HW REVIEWED PASS (bounded)**: CH1 `CURR:STEP 0.001`, followed by
UP/UP/DOWN/DOWN with digital `CURR?` 0.100→0.101→0.102→0.101→0.100 A;
Keithley 1.04-1.04. Original 4449-event file SHA
`b35a1dc15665d629753f76cf6b388cc72200f5c6117b938446fc731a396517ea`;
1002 complete query/reply pairs, 216 writes, 611 zero error polls,
78 CH1/2/3 OFF confirmations and final physical OFF gate.
**2/18 digital OFF-only matrix cases complete**; remaining 16 NOT_RUN.
R4.5-E powered CV/CC and R4.5-F V–I curves remain NOT_RUN.
See `docs/reviews/R4_5_D_CH1_CURR_1MA_HW_REVIEW_2026-10-30.md`.

R4.5-D: третий аппаратный OFF-only случай CH1 CURR STEP `0.005` —
**HW REVIEWED PASS (digital only)**; 3/18 штатных комбинаций. Трасса
`*IDN?` строго сверяется с идентификацией и outcome при двух наблюдавшихся
значениях AUX (`1.04-1.04` / `1.04-0.01`). См.
`docs/reviews/R4_5_D_CH1_CURR_5MA_HW_REVIEW_2026-10-30.md`.
Следующий CH1 CURR `0.010`, затем остальные каналы; R4.5-E/F не закрыты.

### R4.5-D batch acceleration, 2026-10-30

- Verified CH1 `CURR:STEP=10mA` digital OFF-only evidence: SHA-256 `e047b8591258fd0f4f7bb8bcea4aa8517f10eddb8a28a57ee7dac047ef447f5a` (4 known successful OFF-only cases total).
- One-session, independently gated **nine-case current matrix** is implemented as `tools/r4_5_step_batch_qualify.py`; optional voltage/all batches, default DRY RUN, and independent original-JSONL auditor. Software coverage is not hardware qualification.
- **After current batch review, prioritize powered CV/CC STEP with KEL103 load + DMM7510**, not another round of 1-case-at-a-time OFF-only requests. See `docs/R4_5_D_BATCH_EXIT_AND_POWERED_GATE.ru.md`.
- Aux `1.04 ↔ 0.01`: record/observe as known metadata anomaly; await manufacturer reply. Never suppress output or SCPI error diagnostics.

### R4.5-D complete current batch independent hardware review (2026-10-30)

**HW REVIEWED PASS — 9/9 current digital OFF-only cases** (CH1/CH2/CH3 × 1/5/10 mA). Original 36342-event hardware log and SHA `16e7aadd755b360fd1a5ce4d71dbb041da0b668f08e3ff4e97fa0dc195046052`, 8268 replies, 4969 zero-error polls, 639 OFF readbacks; all four UP/DOWN sequences per case restored the original setpoint. Software test 801 OK on operator machine, baseline commit `ee121f4`. Independently reviewed and enhanced trace-payload audit: `docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md`. **No KEL/DMM analog proof.** Remaining OFF-only voltage cases do not block transition to R4.5-E. Next: `docs/R4_5_E_POWERED_STEP_ENTRY.ru.md`; powered STEP still forbidden until separately qualified.

### R4.5-E-A — guarded powered STEP software entry (2026-10-30)

- Production `powered_step_qualification` context: **SIM TESTED only**.
  Only CH1 Independent, 3.50 V/0.150 A/0.60 W maximum profile envelope,
  one authorized output-enable, one approved voltage/CV or current/CC direction,
  mode/power readback before and after, automatic known-link OFF on exit.
- Outside this context, STEP remains OFF-only. Auxiliary KEL103/DMM7510
  readings are **not** covered by virtual UART tests; no physical powered
  STEP was run. A separately reviewed R4.5-E powered stage is required.
- `R4.5-D` current digital OFF-only 9/9 is HW REVIEWED PASS; R4.5-E and
  R4.5-F analog experiments remain NOT_RUN / NOT EXPERIMENT READY.

### R4.5-E-B: one-point powered STEP hardware gate (prepared)

- Separate CH1-only CV (10 mV) / CC (5 mA) operator-gated sessions using
  production guarded STEP, existing KEL103 and DMM7510 adapters.
- Mandatory pre/post **independent analog** readings, commanded absolute
  setpoint, physical delta, PSU MEAS:POW and power/current/voltage caps.
- UART/negative tests before any physical run; after software qualification,
  CV is the first distinct hardware review, CC is a later separately gated run.
- Hardware status: NOT_RUN until original evidence independently reviewed.

R4.5-E-B powered qualification: the first real **CH1 CV 10mV** low-power
point is HW REVIEWED PASS (2026-10-30, KEL103 CR40Ω, DMM7510
+10.626mV). Independent auditor and raw evidence SHA are in
`docs/reviews/R4_5_E_B_CH1_CV_10MV_POWERED_HW_REVIEW_2026-10-30.md`.
**CH1 CC 5mA** is the next separately operator-gated run, NOT_RUN as of this
review; analog dynamics and broader functionality remain open.

### R4.5-E-C — CH1 five-point powered CV hardware review (2026-10-30)

**HW REVIEWED PASS (bounded):** Keithley CH1 CV 3.30 V, KEL103 CR 40 Ω,
DMM7510 FRONT; one authorized ON, physical `VOLT:UP/UP/DOWN/DOWN`, with
five measured DMM voltages 3.296577 → 3.307226 → 3.317040 → 3.307257
→ 3.296999 V. The full independent SCPI, power and OFF review is in
`docs/reviews/R4_5_E_C_CH1_CV_FIVE_POINT_HW_REVIEW_2026-10-30.md`.
The prior one-point CV and one-point CC trials were reviewed separately.
**Five-point CC is NOT_RUN** pending its own operator-approved test and
original-evidence audit. The CV result does not prove timing transients,
absolute calibration, arbitrary V–I sweep or additional H09/H10 load points.
