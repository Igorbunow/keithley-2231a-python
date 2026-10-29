# Architecture — keithley-2231a-python

[English](ARCHITECTURE.md) | [Русский](docs/ARCHITECTURE.ru.md) · [Main README](README.md)

## Goals and boundaries

The library provides a **guarded, testable Python API for a single Keithley 2231A-30-3** without embedding a general-purpose multi-instrument automation framework. The design separates transport, SCPI protocol, safety semantics and measurement processing. Do **not** assume that profile enforcement, serial communication or the front-panel lock is a hardware safety function.

### Layer diagram

```text
              User code / CLI / single-source sweep
                             |
          Keithley2231A / voltage_sweep / Acquisition
                             |
                 immutable Profile + PowerStep
                             |
                guarded SCPI command path
                             |
               Transport (UART / raw TCP)
                             |
                  Keithley 2231A-30-3
                             |
                   wiring / DUT

  Qualification only (tests/hardware/, not installed library):
     bench.local.toml -> equipment factory -> H01-H12 stages
                         |                     |
                 Keithley public API   KEL103 / DMM7510 / TDS3064
```

**Important:** The former `src/keithley2231a/dual_offline.py` has been **moved**, not retained as an alias. It is now the quarantined **experimental draft** `experiments/o02_dual_psu/dual_offline.py` (simulation only), excluded from packaging and forbidden as a dependency of the Keithley API. O02-B/C are frozen. Multiple-instrument control and hardware synchronization belong to a future separate project. Sequential measurements and host timestamps are not synchronized hardware capture.

## Module map

| Location | Responsibility | Boundaries |
| --- | --- | --- |
| `experiments/o02_dual_psu/dual_offline.py` | **EXPERIMENTAL DRAFT O02-A**, synthetic dual-PSU state machine | Outside `src/`, not packaged; never used by the Keithley API or HW qualification; not part of R4–R6 |
| `transport.py` | One-line SCPI UART and raw TCP transactions; explicit timeouts | No device-level safety, no VISA or telnet protocol |
| `profile.py` | Immutable `Profile`, `ChannelProfile`, `PowerStep`, JSON validation, `unrestricted()` | Nameplate values always bound V/I; no file-based arbitrary SCPI injection |
| `device.py` | `Keithley2231A`, SCPI mapping, outputs, timers, monitoring interlocks, profile verification | All mutating guarded commands require an armed profile |
| `monitor.py` | `Acquisition`, per-channel stats, CSV, temporal energy integration | Samples are sequential, not synchronized |
| `sweep.py` | Voltage stepping, settled readings, meter callbacks, CSV and final OFF | No battery discharge, negative current, precision SMU or hardware triggering |
| `tests/hardware/bench_config.py` | One validated TOML configuration for all equipment | No SCPI commands or direct instrument access |
| `tests/hardware/equipment/` | Test-only KEL103, DMM7510 and TDS3064B adapters | Not part of the published Keithley package |
| `examples/` | Read-only probe and opt-in lab recipes | Outputs only with explicit operator action |
| `tests/` | Fake SCPI emulator, CPU-only tests | Passing tests do **not** prove physical hardware behaviour |
| `docs/VERIFICATION.md` | Evidence-based qualification tracking | No fake hardware green status |

## Profile and state machine

```text
Created (unarmed)
    |  apply_profile(): write GLOBAL OFF; check OFF; clear combined mode
    |  set rated/profiled V/I limits, read back, select mode, optional RWLOCK
    v
Armed / outputs OFF ---- set_channel() ----> Armed / outputs OFF
    |                                  (control access only)
    |  power_on()/output()                    | output ON
    v                                         v
Armed / outputs ON ---- periodic check / verify_profile() ----> Armed
    | fault: mismatch, unexpected CC/CV, timeout, etc.
    v
Faulted (attempt global OFF, command delivery NOT guaranteed)
    |
    +-- emergency_off() / reapply after resolving fault --> Armed / OFF
```

- `apply_profile()` is intentionally disruptive: the device is taken OFF **before** any configuration. It never automatically enables outputs; user needs to check wiring and explicitly power on.
- A `Profile` cannot be swapped while armed; the device must first be disarmed using `emergency_off()`.
- `Profile.unrestricted()` means **full rated range**, not unrestricted SCPI or bypass of physical limits.
- `access='power_only'`: channel setpoints, channel configuration and topology are frozen after arming; `set_channel`, `timer`, arbitrary `sequence` and changes that release front-panel lock are rejected. `power_on` traverses the fixed list; `power_off` reverses it. Direct per-channel `output` is unavailable when a sequence is configured.
- `verify_profile()` checks software-known limits, `VOLT:LIM:STAT?`, cap readback, setpoints and series/parallel/tracking control state. It is periodic, **not instantaneous** protection. External controller, front panel and firmware can still intervene between checks.
- `emergency_off()` sends `OUTP 0`. Lost link means no guarantee it arrives; design physical interlocks outside this stack.
- For Series/Parallel, the library *does not detect* physical jumper presence. The `confirmed_wiring='series'/'parallel'` acknowledgment is an operator declaration, not electrical validation. Any future hardware topology sensor belongs in an external bench adapter.
- For CV/CC, profiles specify expected regulation, NOT a mode selection command. The supply switches regulator based on load automatically.

## Instrument ownership and synchronization

**Each physical instrument must have exactly one command owner.** A transparent MCU TCP bridge should accept only one control client, otherwise independent SCPI commands can interleave and change the selected channel. `Keithley2231A` uses a reentrant lock around device operations, but two independent processes or two library instances are not mutually locked. Device-side writer ownership and lease expiry belong to the Ethernet bridge.

Coordinating multiple power supplies or DMMs, distributing triggers and synchronizing clocks are out of scope for this library. A future independent automation project should define `TriggerCapable`, clock domain declarations, bounded trigger latency and per-instrument capability/evidence contracts. Hardware qualification stages use test-only adapters, not a general bench automation framework.

Do **not** silently call `*TRG` in a generic coordinator: it can alter configured outputs and must be part of a qualified, profile-checked action.

## DC curves and combined-channel experiments

`voltage_sweep` requires other PSU channels OFF, uses one source channel to apply a DC step and then reads `MEAS:VOLT?` and `MEAS:CURR?`. External DMM readings can be attached through an observer callback (CSV columns are keyed by meter reading names). This enables diode I–V, resistor verification and transistor curves with additional bias sources, but all wiring and current limiting remain the experiment author's responsibility. The supply cannot sink current; its one-milliamp readback resolution is inadequate for microamp diode leakage curves.

For multi-supply experiments, use independent instrument libraries and a future standalone coordinator. This library intentionally does not ship a multi-instrument coordinator.

## Transport faults and future MCU bridge

`TcpTransport` is raw SCPI line forwarding, with connection timeout and a bounded line buffer. A remote MCU must ensure UART line framing, back-pressure, single client, timeout handling, network authentication/isolation, and fail-safe output behavior. A TCP disconnect alone cannot guarantee OFF; a robust design needs an **out-of-band hardware watchdog/interlock** and safe reset/power-recovery defaults. Do not mix bridge responsibilities into the Keithley instrument driver.

## Verification and release discipline

Hardware evidence has three axes: **software version/commit**, **instrument model/firmware**, and **scenario conditions**. Hardware qualification is not inherited automatically by changed code or different firmware. Each row of [`docs/VERIFICATION.md`](docs/VERIFICATION.md) points to a reproducible procedure/report (see `docs/hardware_reports/`). CI covers simulator tests only. A release should not claim `HW VERIFIED` beyond specifically observed operations.

## Roadmap authority

[`ROADMAP.md`](ROADMAP.md) is the **only current roadmap**. `docs/ROADMAP.ru.md` was deleted as an obsolete sketch. Next: R4 SCPI inventory and review, then R5 channel API/capabilities and R6 release hardening. Prototypes under `experiments/` do not define the roadmap.

## Known tradeoffs / future work

- `SerialTransport` uses request-response line protocols and `readline` timing. No streaming high-rate metrology.
- `Acquisition` computes W = V·I from sequential reads and trapezoidal Ah/Wh, not calibrated instantaneous power and battery discharge capacity.
- Hardware current cap is the commanded `CURR` limit (no separate guaranteed programmable OCP trip exposed here).
- Multi-channel combined control has firmware-specific semantics; verify it before relying on Series/Parallel or Tracking on a DUT.
- Future work: hardware-trigger adapters, registered generic instrument drivers, triggered source setpoints, more complete SCPI status/error handling, interface ownership/leases, deterministic power-cut hardware, and an optional UI/CLI.

## One SCPI execution path

Normal methods and opt-in diagnostics call the same `_write`/`_query` dispatcher.
An optional qualification policy adapter wraps the private command channel; the
channel refuses sends made outside driver dispatch. `_execute` owns actual
transport I/O, Remote/Local state, trace redaction and uncertain-link latching.
The policy adapter's automatic error reads also use `_execute`.

`debug=False` is the default. Enabling debug permits raw reply inspection but keeps
profile guards on diagnostic voltage/current changes. `debug_bypass_profile` must
be explicitly enabled for deliberate configuration/tamper tests; it never changes
normal API guards. UART status-byte tracing belongs to the transport and is enabled
by the driver only in debug mode, replacing external wrappers of `_serial`.

All qualification scopes inherit `StageAdapter` in `tests/hardware/common.py`.
They define command limits, permitted values and operator-authorized channels;
they do not implement SCPI initialization or copy driver command sequences.
Common output-state, snapshot and operator-gate checks are reused across stages.


## R1 — Qualification infrastructure boundaries

The shipping `src/keithley2231a/` package contains no test-equipment
drivers. Hardware stages only use the public Keithley driver. All physical
connections originate in a single validated `config/bench.local.toml` via
`bench_config.py` and `equipment/factory.py`. KEL103 transport codecs do
not duplicate the higher-level device policy: `_KEL103Core` implements one
command/error/evidence/readback path for serial and UDP. The e*Scope and
DMM7510 adapters live in `tests/hardware/equipment` and can later be
moved into independent instrument projects. There are no old transport aliases.

The stage-specific approved *test points* and allow-listed PSU SCPI
operations remain part of the corresponding qualification stage; they
are safety-reviewed scenario definitions, not transport configuration.
They cannot be overridden at runtime without updating the stage and
its qualification evidence.


The former vendor-specific `BenchCoordinator` was removed as out of scope.
Multi-instrument orchestration will be developed as an independent project
after its component device drivers have matured.
