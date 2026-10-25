# Changelog

## 0.2.0 — 2026-10-25 (preview)

- Repository prepared for MIT publication with English/Russian documentation, architecture, verification matrix, ignore rules and simulator CI.
- Added `Profile.unrestricted()` (full rated range, not raw access).
- Added immutable `PowerStep`, `Profile.access='power_only'`, default/fixed `power_on()` and reverse `power_off()`.
- Added host-timed `voltage_sweep()` I–V acquisition, optional external meter observer and CSV.
- Added `BenchCoordinator`, `BenchStep`, `BenchAction`, and extensible meter protocol for several PSUs and meters.
- Added simulator regression tests; 30 tests in the local test suite at release preparation time.
- Hardware status remains preliminary: initial UART/remote/IDN `HW OBSERVED`, output-control functions `HW UNVERIFIED`.

## 0.1.0 — 2026-10-25 (previous prototype)

- Initial serial/TCP SCPI transport, protected device API, combined modes, logging and statistics.
- 20 tests using a fake instrument model; no full physical hardware qualification.
