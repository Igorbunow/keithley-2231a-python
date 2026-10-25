# Observation — USB–UART and `*IDN?` (not full hardware qualification)

- **Date:** 2026-10-25 (reported by project owner)
- **Model:** Keithley 2231A-30-3
- **Firmware:** 1.04-1.04
- **Connection:** USB–TTL UART adapter; Linux `/dev/ttyUSB1`, 9600, 8N1, no hardware/software flow control
- **Physical evidence (user reported):** Python/pySerial successfully wrote `SYSTem:REMote\r\n` (15 bytes), then `*IDN?\r\n` (7 bytes); the supply responded with a correctly formatted identity message. Serial number deliberately redacted.
- **Observed identity:** `Keithley instruments, 2231A-30-3, [REDACTED], 1.04-1.04`
- **Outcome:** `HW OBSERVED` for initial UART/remote/identity; **NOT** a test of any output, voltage limit, current limit, regulator state, measurement accuracy, network bridge or power sequence.
- **What is missing for HW VERIFIED:** independent repeatable run, detailed wiring/logical-voltage record, observed output/response with a signed report and code revision.
