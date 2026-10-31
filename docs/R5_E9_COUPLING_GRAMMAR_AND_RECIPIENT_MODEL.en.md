# R5-E9 — Coupling grammar review and conservative recipient model (software only)

## Source and provenance

- Baseline repository: `934ef47` (R5-E8), 2026-10-31.
- Authoritative vendor source: Keithley Instruments, *Model 2231A-30-3 Triple-channel DC Power Supply Reference Manual*, document **077100401 / September 2014**, section **5-12** (PDF page 48): <https://download.tek.com/manual/077100401_Referencemanual.pdf>.
- Prior physical evidence: `docs/reviews/R5_E7_NEUTRAL_TRIGGER_HW_REVIEW_2026-10-31.md`; one neutral, global OFF-only `*TRG`, reviewed without analog measurements. The previous neutral test **does not characterize coupling**.

## What the manual actually establishes

The manual says `INSTrument:COUPle[:TRIGger]` determines which channels respond to a trigger, and documents both a setter and a query. However, its syntax shows an argument even for the query, specifies the arguments as `CH1|CH2|CH3`, gives a multi-channel example with a dot separator (`Inst:coup ch1.ch2`), and lists the return value as `None`. Those statements do **not** define an unambiguous query response, group grammar, default coupling state, or per-channel independence. The example alone is not sufficient to justify sending a guessed command.

**Decision:** `INST:COUP` writes and reads remain blocked, including the debug-bypass path. Do not assume `INST:NSEL` changes the recipient group of global `*TRG`, that a coupling list is a simple bitmask, or that `INST:COUP` is equivalent to output Tracking. Do not infer channel recipients from unchanged values after a neutral event.

## Offline safety model

`tools/plan_r5_e9_trigger_recipients.py` uses the existing R5-E5 `ChannelBounds` and `plan_offline()` as the single source of the three-channel voltage/current/profile-envelope validation. It additionally refuses off-grid ordinary or trigger preset values, and refuses a wholly neutral candidate when asked to analyze non-neutral behavior.

The output lists all **eight** possible subsets of CH1/CH2/CH3, including an empty subset. Each row is an explicitly hypothetical *digital* ordinary-setpoint outcome under a simple model where channels in the subset adopt their trigger presets, and others do not. No row asserts that the hardware implements this model, that these groups can be configured, or that a specific SCPI reply would be observed. The empty subset is retained because `*TRG` can be accepted without a qualified observable setpoint change.

All three channel limits are mandatory, even if the intended experiment changes CH1 alone. The R5-E5 aggregate product of voltage/current caps is only a **planning envelope**. It is not a measured load, an independent interlock, or evidence of physical output behavior.

No UART, TCP, hardware runners, `*TRG` dispatch, `INST:COUP` queries/writes, output enables, or source-driver safety rules were changed in R5-E9. The model imports no transport layer, and **must never authorize hardware execution**. Tests assert malformed profiles, missing/duplicate channels, nonfinite inputs, wrong grids and all recipient subsets.

## Constraints for a possible future R5-E10 qualification

No hardware experiment is authorized by this increment. Before writing or approving a real non-neutral OFF-only runner, review a separate procedure that includes all of the following:

1. Re-establish independent profile and physical empty-terminal/OFF gates; every CH is a potential recipient. Independently confirm the real instrument and its operator-facing settings. Treat `INST:COUP` as UNKNOWN, not as a selector.
2. Save and independently read back **both original ordinary V/I setpoints and all six original trigger presets** before any mutation. E7 restored the trigger registers but did **not** restore prior ordinary setpoints; a new case must define a separate bounded restoration strategy.
3. Compare ordinary and trigger registers per channel before/after one event; verify actual OFF on all three, error queue, readback, consistency of provenance/trace and physically confirmed OFF. Never equate setpoint readback with measured analog output.
4. On an uncertain response, partial write, error, timeout or failed restoration, abort without replay, automatic reconnect or blind cleanup. An `OUTP 0` write is insufficient confirmation on an uncertain link.
5. Capture the original unmodified JSONL, its SHA-256, source/runner hashes, firmware and bench-config identity; independently audit all commands, write/read ordering, all channels and human operator gates. No automatic second attempt.
6. Seek explicit new operator authorization for **that particular hardware event**. R5-E9's offline results alone are not a permission to implement or execute it.

## Verification status

- Coupling grammar: `DOCUMENTED_AMBIGUOUS`.
- Coupling configuration and recipients: `UNKNOWN_NOT_QUALIFIED`.
- R5-E9 possible-recipient outcomes: `SOFTWARE_ONLY_HYPOTHESES`.
- Physical non-neutral OFF digital actuation: `NOT_RUN`.
- Powered triggered CV/CC, timing and synchronization: `NOT_QUALIFIED`.
- H09 loaded Series / H10 loaded Parallel: `OPEN`, separately authorized.
