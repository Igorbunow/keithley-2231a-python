# R5-E10 — Non-neutral OFF-only digital trigger: protocol review gate

**Status: SOFTWARE DESIGN / SYNTHETIC TESTS ONLY. No hardware operator
authorization; no `*TRG` sent; no instrument I/O.** Base: operator-confirmed
`e879136` (R5-E9, 1362 tests OK). The accompanying offline module is not a
hardware runner or an independent original-JSONL auditor.

## What R5-E7 and R5-E9 do and do not establish

- R5-E7-HW independently reviewed a **single global neutral** `*TRG` while
  every output was OFF. That neither tests a non-neutral transition nor
  qualifies an analog signal, recipients, coupling, timing or synchronization.
- R5-E9 showed that vendor `INST:COUP` syntax/response is ambiguous; no
  fabricated coupling query or guessed grouping syntax may be used. Assume
  CH1/CH2/CH3 are all potential recipients of the **one global** bus event.
- R5-E10 models synthetic values and drafts a protocol, without adding a live
  SCPI implementation, second dispatcher, permission bypass, or new test
  equipment adapter. The tests do not establish actual digital transitions.

## Proposed protocol — for separate technical and operator review only

The *future* implementation must obtain fresh, specific permission after its
own source review, tests and dry-run. This document does not authorize wiring,
output ON, or any bus trigger. Original instrument firmware and identity must
be observed and attached to evidence; firmware AUX revision anomalies are not
normalized away.

1. **Preflight / ownership.** Operator physically confirms bare PSU output
   terminals, no jumpers, no DUT, no KEL103/DMM loads or other external voltage;
   Independent topology and all three physical outputs OFF. A sole controller
   owns the serial link. There is no independent hardware watchdog just because
   a host-side timeout is configured. Abort on any ambiguous output state.
2. **Original baseline BEFORE any mutation.** Read and preserve twelve original
   registers — CH1/CH2/CH3 each `VOLT?`, `CURR?`, `VOLT:TRIG?`, `CURR:TRIG?`
   — plus individual OFF readbacks. Each reading is sequential, NOT an atomic
   snapshot. A future runner must preserve original ordinary as well as original
   trigger registers, and verify stored source/evidence hashes. Refuse the case
   if original values cannot be read/verified or restored within a separately
   approved bounded envelope. Never silently use a default value.
3. **Separate restoration-plan approval.** Specify exactly which register
   writes would restore the original ordinary and triggered presets, their
   order, range, profiles, OFF preconditions, post-write readbacks and failure
   action. A previous R5-E7 test restored original TRIG values, **not** the
   user's ordinary setpoints. A future implementation must not assume this
   cleanup is provided by R5-E7. Any failed preflight aborts before a trigger.
4. **Prepare bounded OFF-only settings.** Preserve every output OFF. Stage
   CH1/CH2/CH3 ordinary and triggered values within each channel's independently
   validated envelope via the production driver only, then independently verify
   all **twelve staged registers** and three OFF states. R5-E9's example
   describes bounds, **not** an automatically authorized instrument setup.
   All three potential recipients must have bounded values, even if only one
   channel is intentionally non-neutral.
5. **One specific event authorization.** The operator separately approves this
   **one** prospective global non-neutral trigger after seeing all twelve
   staged readbacks and confirming three physical OFF outputs. No batch by
   channel, no retry, no `INST:COUP` mutation, no guessed query, no output ON.
6. **If separately approved later, event characterization.** Capture the raw
   on-wire command sequence and exactly one event. After a *trustworthy*
   response, examine all twelve post-event registers and three OFF states,
   SCPI error queue and firmware identity. Compare results without claiming
   any specific channel recipient from equal/neutral values. Ordinary setpoint
   changes alone are not measured voltage/current at an output terminal.
7. **Restore and independently verify.** Only on a confirmed healthy link and
   under the separately approved restoration procedure, restore the twelve
   ORIGINAL register values and read back all twelve plus three OFF states.
   Get physical final OFF confirmation. A mismatch, error queue entry, malformed
   reply, timeout, uncertain link or output anomaly is an ABORT; no automatic
   replay, reconnect, speculative restore, or claimed physical shutdown. The
   operator physically checks/isolates the PSU after uncertainty.
8. **Evidence review, not an auto-PASS.** Preserve unmodified original JSONL,
   raw bytes, SHA-256, software/runner hash, bench-config hash, full terminal
   log and all initial/event/final operator gates. A separate evidence auditor
   must verify SCPI wire order, mutation count, zero forbidden commands,
   snapshots, OFF confirmation, restoration and hidden diagnostics. An auditor
   may at most report *bounded non-neutral OFF digital evidence*, NEVER
   powered switching, timing, synchronization or coupling qualification.

## Software interface and exclusions

`python3 tools/plan_r5_e10_off_digital_protocol.py` prints only a JSON
**design checklist**. It accepts no hardware connection, `--execute`, run or
serial-port option; it opens no device. The four named synthetic capture sets
represent original, staged, post-event and restored values, each including
four registers and an OFF readback for all three channels.

`assess_synthetic_digital_trace()` validates only user-created/mock values:
exactly one **injected** event, certain simulated link/error queue, per-channel
bounds and 10 mV/1 mA grids, all four sets of three OFF readbacks, staged
setpoint integrity, absence of TRIG-register changes in the synthetic
post-event model, final exact restoration of **all twelve** original fields,
and whether ordinary post-event values fit one or more of R5-E9's eight
hypothetical recipient sets. Multiple compatible subsets stay ambiguous.
Inconsistency raises `ProtocolError` (STOP); success is explicitly labeled
`SYNTHETIC_CONSISTENCY_ONLY_NOT_HW_REVIEW`, with `hardware_permission=false`.

This synthetic checker is **not** suitable for imported hardware evidence:
flag values do not prove link health or physical OFF, and it does not inspect
SCPI wire events, timing or SHA-256. A future live stage needs a separate
original-evidence auditor and new manual operator gate. Until then the
following remain **NOT_RUN**: R5-E10 non-neutral event, powered triggering,
`INST:COUP` behavior and physical load qualifications H09/H10.
