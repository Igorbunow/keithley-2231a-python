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
   R3.5.45 starts a **separate service-profile, typed OFFLINE-ONLY
   plan compiler** for dangerous and calibration commands, with no runtime
   transport and no live service execution. The ordinary driver even blocks
   service SCPI in diagnostic bypass. R3.5.46 adds a **fake-only service
   state machine** with one-use sequence acknowledgements, independent critical
   approval, calibration point completion guards and injected-fault stop; it
   has no live transport. See `docs/R4_3_SERVICE_MODE.ru.md`.
4. **R4.4 verification:** trace auditor, firmware caveats, documented exclusions,
   targeted operator-approved hardware checks only if indispensable.

Completion: every vendor SCPI **family** has an explicit disposition, including
unsupported/unsafe commands. Completeness never means blindly executing them.

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

### R4.3-F — сервисный in-memory gateway (R3.5.47)

Сверка профиля/плана/автомата и отдельного фиктивного SCPI-контура с
`FAKE_ERROR_POLL`, ограниченным числом шагов и отказами send/reply/error/output:
**OFFLINE SIMULATED ONLY**, никакой связи с Keithley, никаких live-команд
калибровки. Реальный сервисный gateway остаётся отдельным будущим этапом
после проверки документации, внешней авторизации и метрологической методики.

### R4.3-G / R3.5.48 — offline service authority model

The offline service session now has fake normal/service exclusivity, a unique
per-action permit bound to full profile/serial/plan/synthetic-fixture hashes,
explicit action approval and terminal fail-closed behavior on changes or faults.
Only a FakeBench is accepted; `HW NOT_RUN` and **real service execution remains
blocked**. Remaining before any live gateway: real exclusive device ownership,
trustworthy operator authorization, independent evidence, exact vendor service
procedure and external calibrated measurement hardware.
