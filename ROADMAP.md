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
