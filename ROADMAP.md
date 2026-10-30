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

- Channel API (CH1/CH2/CH3) *delegating* to common guards, not copying SCPI.
- Accurate device capability metadata and observed-vs-simulated distinctions.
- Typed profile validation, versioned serialization, documented ownership and
  connection/close/unknown-link semantics.

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
- 24 approved CH1/2/3 × VOLT/CURR × increment cases planned, not yet executed;
  boundary and combined experiments need additional evidence.
- Virtual UART and independent patch regression only; **HW NOT_RUN**.
  R4.5-E CV/CC analog qualification and R4.5-F I–V sweep remain separate.
  See `docs/R4_5_D_STEP_MUTATION_OFF_PLAN.ru.md`.
