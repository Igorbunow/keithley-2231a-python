# R4.4-C independent review — one low-power CH1 scalar point (2026-10-29)

**Decision: HW REVIEWED PASS for exactly one Independent CH1 point** on a real
Keithley 2231A-30-3, firmware `1.04-1.04`, with KEL103 and DMM7510 connected in
parallel. `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?`, and `FETC:POW?` returned
finite, consistent values. **FETC freshness, buffer age, synchronization and
transient behavior remain UNVERIFIED**. No Series/Parallel/Tracking inference.

## Original evidence, not checked into Git

- Terminal `terminal(20261029-212857).log`, SHA-256:
  `6c30ec340542a5194bb3682ed0b92b965383252586fec904e116634c5875605d`.
- JSONL `events(8).jsonl`, SHA-256:
  `138942d72bc95c3bacba15ed776372d7207b48333fef7ee4b41f4f76790b340c`.
- Evidence runner path: `reports/hardware/2026-10-29T212655.545648Z_R44_SCALAR_CH1/events.jsonl`.
  Source timestamps 2026-10-29 21:27:03.328912 UTC through 21:28:30.994957 UTC;
  the final ~57 s is operator confirmation, **not** a serial timeout.
- Recorded Git commit: `752470f067dd678303c2f6e273c04c4f221807df`;
  `dirty=true` (cannot infer uncommitted tracked-code changes from this flag).
- Bench configuration digest: `e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720`.
- Runner SHA-256: `740af1e54f18cdb7f6910a17838464cab72197ef076d8256877ee8123df48b56`.
  Stage SHA-256: `b8c3cd2fc3f44656b33d0d1083410a25f5f91edc446342ef1b8906b2987fce14`.
  Both reproduced from the review baseline after R4.4-C ABORT DIAG GUARDS.
- The preceding unsuccessful attempt's cause (disconnected DMM lead) was
  confirmed by the operator; that earlier **ABORTED** record remains separate
  and is not reclassified as a success.

**Privacy warning:** the historical JSONL's high-level `psu_identity` event
contains an unredacted instrument serial because its identity record omitted
`command='*IDN?'`. The low-level identity read and traces were redacted. The
original evidence must remain private: do not commit or distribute raw JSONL.
The accompanying review patch corrects **future** emission; it does not rewrite
or silently alter this evidence's hash.

## Independently checked on-wire sequence

| Evidence property | Review |
|---|---|
| 2476 JSONL records | Valid JSON; no duplicate keys; ordered UTC and monotonic timestamps |
| Keithley operations | 534 queries / 534 corresponding replies, 125 writes; 1193 low-level trace entries (534 query + 534 reply + 125 write) |
| Error queue | 329 `SYST:ERR?` polls, all `0, "No error"`; after every on-wire operation except final `SYST:LOC` |
| Startup nesting | The first logical `OUTP:SER?` surrounds automatic `SYST:REM` and its own error poll: not a duplicate operation |
| External instruments | DMM7510 20 query/reply pairs, 7 setup writes; KEL103 15 query/reply pairs, 4 writes |
| Identity | PSU firmware `1.04-1.04`; DMM firmware `1.7.18a` |
| Initial state | CH1–CH3 OFF, Series=Parallel=Tracking=0, KEL INPUT OFF, CR 40 Ω |
| Profile | CH1 3.30 V / 0.150 A limit / 3.50 V limit enabled; CH2/CH3 OFF despite stored 1.00 V presets |
| Power gate | Operator confirmed before one `CHAN:OUTP 1` on selected CH1; KEL INPUT ON immediately before PSU enable |
| Powered interval | One ON at 21:27:24.122586 UTC; one shutdown `OUTP 0` at 21:27:32.827036 UTC; CH2/CH3 remained OFF |
| SCPI scalar reads | Exactly one each of `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?`, `FETC:POW?`, each followed by `SYST:ERR?=0` |
| Final state | Two OFF readbacks per CH1, CH2 and CH3 after global `OUTP 0`; KEL `:INP OFF` followed by two `OFF` readbacks; final physical OFF gate confirmed |
| Local transition | `SYST:LOC` sent after verified OFF; no subsequent SCPI readback (intentional limitation) |
| Unsafe commands | No repeat ON, `*RST` to PSU, event-latch read, calibration, topology ON, reconnect or unexpected SCPI |

The DMM's `*RST` was to the **DMM only**. Its FRONT terminal selector was
queried **before** that reset, not independently re-queried afterward; the
actual DCV function, 10 V range and NPLC=1 were read back afterward. Physical
FRONT connection and correct live voltage are supported by the operator gate
and cross-instrument agreement, not an additional post-reset `:ROUT:TERM?`.

## Live measurements: one bounded, sequential observation

| Quantity | Observed |
|---|---:|
| PSU `MEAS:VOLT?` | 3.297710 V |
| PSU `MEAS:CURR?` | 0.0811548 A |
| PSU derived U × I | 0.267624995508 W |
| KEL103 voltage, current, power | 3.296900 V; 0.0823000 A; 0.271300 W |
| DMM7510 `:READ?` | 3.296494 V |
| PSU `MEAS:POW?` | **0.267863 W** |
| PSU `FETC:VOLT?` | **3.299600 V** |
| PSU `FETC:CURR?` | **0.0811749 A** |
| PSU `FETC:POW?` | **0.267907 W** |

- DMM–PSU voltage: −1.216 mV; DMM–KEL: −0.406 mV.
- KEL–PSU current: +1.1452 mA.
- `MEAS:POW?` minus PSU U × I: +0.000238004492 W (~0.089%).
- KEL power minus `MEAS:POW?`: +0.003437 W (~1.28%).
- FETC minus live reference: +1.890 mV, +0.0201 mA, +0.000044 W
  (referencing the sequential PSU V/I and `MEAS:POW?`, respectively).
- All voltage, current and power values remain below the preapproved
  3.55 V / 0.165 A / 0.60 W bounds and within cross-instrument tolerances.

The individual measurements were made at different times; the PSU-to-FETC
sequence took several seconds with guarded profile checks in between. The
agreement is **not proof of fresh FETC buffers**, simultaneous sampling,
transient response, calibration accuracy, or general electrical uncertainty.
No additional operating points were exercised.

## Disposition and follow-up

The source stage's `outcome=PASS` was explicitly `HW NOT_REVIEWED` at collection.
This independent audit changes the scoped documentation assessment to
**HW REVIEWED PASS** for one stable low-power Independent CH1 point and the
four specific query replies above. The review is supported by local tool
`tools/audit_r4_4_scalar_evidence.py` and the original operator-gated record,
not an independent physical re-run. Leave untouched both raw success/abort logs.

Next: commit the review/evidence-auditor and identity-redaction regression, then
continue R4 software work. Any higher-power, other-channel, topology, or
freshness experiment requires a new dedicated hardware gate.
