# Roadmap

[English](ROADMAP.md) | [Русский](docs/ROADMAP.ru.md)

All planned items are subject to hardware compatibility and safety review. A feature is not `HW VERIFIED` merely because its implementation lands in source.

## v0.2.0 — GitHub-ready preview (this package)

- Repository naming, MIT, bilingual README/architecture, ignore rules, third-party notices, GitHub Actions CPU-only tests.
- Rated-range and operator-only profiles; reverse power shutdown sequence.
- DC I–V sweep with optional meter observer; generic host-side multi-PSU coordinator.
- Hardware qualification matrix and operator-run instructions.

## Next: v0.3 — physical qualification on firmware 1.04-1.04

- Read-only probe, output-off-first apply/profile integrity, voltage/current set/readback.
- Per-channel output, CV/CC bits, output timer, fail/timeout behavior.
- Physical Series/Parallel (external wiring), Tracking, combined measurements.
- Time-series measurements, current/energy accuracy and disconnected-client tests.
- Publish redacted reports for specific firmware/hardware/revision combinations.

## Next: v0.4 — experimental programmable measurements

- Generic stepped **multi-dimensional test plan** for multiple voltage/current setpoints, conditional stop and external DMM/oscilloscope callbacks.
- External instrument driver interface and capability discovery; first adapters can be PyVISA-backed, independent of the PSU core.
- Optional stable power-only kiosk/CLI mode without exposing controller internals.
- Optional results storage with data provenance, instrument identity (redacted) and uncertainty notes.

## Next: v0.5 — network MCU bridge and synchronization

- MCU transparent UART–TCP bridge with single-client lease, timeouts, safety/health telemetry, firmware protocol version and isolated local operation.
- Distinguish network disconnect from instrument electrical OFF; prototype independent hardware cutoff if unattended use requires it.
- Explicit trigger/clock interfaces, device-specific `*TRG`/external trigger adapters where applicable, timestamp accuracy budgets, oscilloscope-confirmed switching.
- Generic supply-driver protocol to integrate other vendor instruments and synchronize multi-source experiments at declared (non-assumed) precision.

## Future

- More SCPI commands: safe status/event handling, configured triggered setpoints, display, saved-state management (with strict risk controls), capability negotiation for firmware variants.
- Richer IV analysis (multi-channel transistor families, sensor curves, diode thermal drift) and plots with unit metadata.
- Automated release packaging and a compatibility table for hardware-tested firmware.
