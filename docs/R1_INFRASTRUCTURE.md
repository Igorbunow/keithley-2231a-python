# R1 — Hardware qualification infrastructure

**Status: implementation and offline regression complete; HW requalification pending.**

## Boundaries

- `src/keithley2231a/`: standalone Keithley library; never imports test equipment.
- `tests/hardware/bench_config.py`: validated single TOML configuration loader and its fingerprint.
- `tests/hardware/equipment/`: KEL103, DMM7510, TDS3064B; qualification-specific minimal adapters.
- `tests/hardware/equipment/factory.py`: sole instrument construction path.
- `tests/hardware/*_stage.py`: reviewed test points, wiring gates, specific invariants; no independent I/O clients.
- `tests/hardware/qualification.py`: CLI and supervised evidence runner.

## Configuration

Copy `config/bench.example.toml` to `config/bench.local.toml`.
Edit only the latter. Both serial/UDP KEL103 endpoints are declared in the same
load section; **transport must be selected explicitly** (`serial` or `udp`).
There is no automatic fallback. PSU VID:PID confirmation must match the
configured VID:PID **and** the enumerated USB adapter.

```bash
python tools/hw_qualify.py preflight H01 --config config/bench.local.toml
python tools/hw_qualify.py run H01 --config config/bench.local.toml --execute
```

`list`, `preflight` and unexecuted `run` do not connect to hardware.
Approved test values remain fixed by each stage. Changing such values
requires code review and a new qualification; they are intentionally not
an unguarded runtime override in TOML. Effective connection settings
and TOML SHA-256 are stored in the test evidence.

## De-duplication / no legacy

Removed `tests/hardware/dmm7510.py`, inline TDS3064B and inline KEL103
classes. Removed all four stage-specific KEL103 CR methods. Common
`configure_cr()` enforces INPUT OFF, mode readback and resistor readback.
`_KEL103Core` handles allowlist, failure invalidation, evidence, and
measurements uniformly for USB serial and UDP. Old CLI connection flags are removed.

## Qualification limits

Offline regression: 130 test cases discovered, 128 executed successfully,
2 skipped because `pyserial` could not be installed in the offline container.
Hardware I/O was not performed. Previous H01–H12 records remain
historical evidence of the earlier source revision, not a PASS for this patch.

Following this refactor, prioritize a concise read-only H01 plus tests of
changed adapters: H03 KEL103, H05 TDS3064B, H08 DMM7510. Re-run H09/H10
only where impacted by shared KEL103/DMM changes and safety review. All
other hardware test evidence remains archived. No bench rewiring without
explicit operator gates.

The provisional `BenchCoordinator` and its tests were removed; the future
lab-automation layer belongs in another project.
