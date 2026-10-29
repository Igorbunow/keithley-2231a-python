# R4.5-B — independent original-evidence review of INST and STEP OFF runs, 2026-10-29

**Decision: HW REVIEWED PASS — bounded query readbacks only.** On one physical
Keithley 2231A-30-3, firmware `1.04-1.04`, `INST? = CH1` was observed in two
OFF-only runs, and `VOLT:STEP? = 0.001` V and `CURR:STEP? = 0.001` A were
observed once on CH1 **after separately authorized changes to OFF presets**.
This does not qualify STEP writes, stepping effects, powered operation, other
channels, latency bounds, persistence, or actual voltage/current outputs.

## Primary sources / provenance

- Operator terminal `terminal(20261029-230828).log`: 684 unittest cases OK,
  `compileall` completed, incremental SHA-256 matched, commit `a29dc25` made.
  Source file SHA-256: `a35d781701e072269aae7af7d454400d2e887f461ab34d2593c5c82cd3d491a7`.
- Original INST evidence (`events(9).jsonl`), SHA-256
  `b52054a4ba0b3737a87a95c70884c2ef9ce6a45b6da036dfe7dbd1068e26edec`,
  334 records, source commit `a1af3d51a9b1ecc69c5a5d72385eb702e1030f33`.
  On-operator path `reports/hardware/2026-10-29T225311.519185Z_R45_SOURCE_INST_OFF/events.jsonl`.
- Original STEP evidence (`events(10).jsonl`), SHA-256
  `52ee167157d8c015d826db9f5b9608f64957a1203269be5b45d008e462b238e6`,
  1,679 records, source commit `a29dc253aefc8a44320aac699a2b057dd209b36c`.
  On-operator path `reports/hardware/2026-10-29T230732.081056Z_R45_SOURCE_STEPS_OFF/events.jsonl`.
- Both source reports have bench configuration SHA-256
  `e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720`.
- Both carry runner SHA-256
  `9e7192520e439e32393ba1d65c39dbf34b5a3421b8d0d7f73e14c26ee433604b`
  and stage SHA-256
  `ae24b64ec41986a4cfaef52986a3b83d5970d8c6d3e95d0c8468f0b101724df2`.
  These match the checked source, byte for byte. `dirty=true` was retained;
  it does not prove modified tracked production source.
- Original JSONL and local bench configuration stay with operator; no raw
  serial number or private configuration are committed.

## SCPI and safety findings

| Check | INST run | STEP run |
|---|---:|---:|
| JSONL records | 334 | 1,679 |
| SCPI queries / matching replies | 75 / 75 | 371 / 371 |
| SCPI writes | 14 | 94 |
| `SYST:ERR?` replies | 44 zero | 232 zero |
| `CHAN:OUTP?=0` | 9, three full sweeps | 33, eleven full sweeps |
| `INST?` | `CH1`, once | `CH1`, once |
| `VOLT:STEP?` / `CURR:STEP?` | NOT_RUN | `0.001 V` / `0.001 A`, once each |
| Final physical OFF gate | confirmed | confirmed |

- All recorded low-level trace queries have a matching reply. Driver trace
  agrees with higher-level query/read/write evidence. No unexpected error
  events; no unapproved SCPI commands. Every operation except the final
  `SYST:LOC` is followed by one error-queue query, which consumes the queue.
- INST run has only REM, channel-selection and LOC writes. STEP run has the
  same initial check, a **separate PROFILE approval**, then explicit `OUTP 0`,
  disabled combined modes and safe CH1 3.30 V/0.150 A and CH2/3 1.00 V/0.100 A
  OFF-channel presets. Each channel's readbacks match the configured values.
- In the STEP run, `OUTP 0` occurred twice: before profile updates and after
  *both* STEP readbacks. Last six output queries are OFF, in two CH1/2/3
  sweeps. None of the traces contains `CHAN:OUTP 1`, `OUTP 1`, STEP writes,
  UP/DOWN, trigger, reset or non-zero SCPI error.
- Source, model and firmware stable. No UART unanswered read or SCPI retry.
  STEP run recorded two manual gates (PROFILE and FINAL) in addition to
  retrospective BENCH/INITIAL approval evidence. The physical confirmation
  is a human observation, not independently sampled electrical OFF.

## Evidence limits / important residual risks

1. Reading a STEP register while outputs are OFF does **not** test applying
   that increment to an energized output, accuracy, persistence, trigger
   sequencing, or behavior across other channels/firmware. Vendor's `Returns
   None` ambiguity for `VOLT:STEP?` is resolved **for one physical 1.04-1.04
   observation only**: the reply was numeric `0.001`.
2. Digital channel setpoints remain programmed to the approved OFF profile
   after the STEP run. The test does not restore prior user settings; this is
   an intentional, operator-approved side effect, and must not be silently
   described as an entirely nonmutating read-only session.
3. `SYST:LOC` is the final write, with no subsequent readback or error poll.
   Individual channel OFF states were sampled sequentially, not continuously
   monitored. No DMM7510/KEL103 electrical measurements were made in these
   runs. No powered operation can be inferred.
4. The first BENCH and INITIAL keystrokes predate the evidence file's start;
   the recorded `pre_open_approvals` is a retrospective marker. `dirty=true`
   must not be normalized to a clean-tree claim.
5. This review is based on the provided terminal and JSONL, not an
   independently instrumented oscilloscope or isolated hardware observer.

## Reproduction

```bash
python3 tools/audit_r4_5_source_evidence.py \
  reports/hardware/2026-10-29T225311.519185Z_R45_SOURCE_INST_OFF/events.jsonl \
  --expected-sha256 b52054a4ba0b3737a87a95c70884c2ef9ce6a45b6da036dfe7dbd1068e26edec
python3 tools/audit_r4_5_source_evidence.py \
  reports/hardware/2026-10-29T230732.081056Z_R45_SOURCE_STEPS_OFF/events.jsonl \
  --expected-sha256 52ee167157d8c015d826db9f5b9608f64957a1203269be5b45d008e462b238e6
```

Both must print `REVIEWABLE_PASS`. A negative or missing review is a blocker;
software alone does not authorize further hardware operations.
