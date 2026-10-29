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
   R4.2-B event-consuming queries: HW NOT_RUN until separate approval and review.
3. **R4.3 risky gaps:** review trigger/apply/step/save/recall/display commands
   separately. Never expose arbitrary write access or bypass existing profile
   and output gates. CALibration/service commands are **inventory only**.
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
