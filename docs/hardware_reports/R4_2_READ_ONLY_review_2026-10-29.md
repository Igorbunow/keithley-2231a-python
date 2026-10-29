# R4.2 Read-only hardware qualification — reviewed 2026-10-29

**Decision:** **HW REVIEWED PASS — READ_ONLY scope only** (single Keithley 2231A-30-3,
firmware `1.04-1.04`). **R4.2-B EVENT consumption remains HW NOT_RUN.**

## Reviewed source evidence

- Operator terminal: `terminal(20261029-101248).log`; SHA256 `6be07214e71a1ee895a1566e7871532a233d8736b486ce3e8ab86479b0d1227e`.
- Captured wire/evidence: `events(20261029-101312).jsonl`; SHA256 `68759878e4c5081620a3df99111cd4e9db1464e886029062b43b8c37d36594e0`.
- Full raw evidence is retained by the operator, **not** embedded in the installable package.
- Staged source candidate SHA256 (git diff --cached --binary):
  `f7473903e521dcc3f33e2df45e901b6413a2affd193ea0a4a852cbde8f2e7c08`.
- Evidence `start.software.source_sha256`: `2c68c7413b1177eeb6879bd82edc6a7551211b669f9c4050a88b15e8a6d546a2`
  (independently reproduced against R3.5.38 source tree). Git HEAD in operator log:
  `52ca75b411476e6f9e8b26738ea06171265b4fe6`, `dirty=true`, because
  R3.5.33–R3.5.38 were staged and not yet committed. Source digest does not
  individually bind the `tools/r4_status_qualify.py` entrypoint (fixed in the
  subsequent evidence-only change).
- Bench-config SHA256 (from evidence):
  `858887cd453c4e8ef7f0473686c578af03868f478cd73afdc09b7e66d4912bb8`.
  Initial BENCH/ALL_OFF approvals are visible in **terminal** only, final
  operator approval in both terminal and JSONL. Subsequent runs also store
  pre-open approval markers in JSONL.

## Independent observations (do not infer extra capabilities)

| Check | Recorded result |
|---|---|
| JSONL validity and temporal order | 349 parseable JSON records; monotonic clock and UTC monotonically ordered; no duplicate JSON object keys |
| Wire trace coherence | 81 query operations, 81 query replies, 10 write operations; 172 driver trace records |
| Error queue | Exactly 45 `SYST:ERR?`, all `0, "No error"`; one after each prior wire operation except final `SYST:LOC` |
| Device identity | Two identical `*IDN?`: Keithley 2231A-30-3, firmware `1.04-1.04` (serial redacted) |
| Outputs | `CHAN:OUTP? = 0` for CH1, CH2, CH3 in two independent readback sweeps (six readings) |
| Topology | `OUTP:SER? = 0` (three samples), `OUTP:PARALLEL? = 0`, `OUTP:TRAC? = 0` |
| Module info | Raw `SYST:MODU?` = `6302  , 1.04` (different from manual's illustrative example, not used as device identity) |
| Standard enable masks | `*ESE?`, `*SRE?`, `STAT:QUES:ENAB?`, `STAT:OPER:ENAB?`: each exactly once, each 0 |
| Nested enable masks | All eight documented `STAT:QUES/OPER:INST[:ISUM1..3]:ENAB?`: exactly once, each 0 |
| Unsafe/mutating SCPI | None. Only `SYST:REM`, eight `INST:NSEL` selection writes, `SYST:LOC`; no output/setpoint/status-clear writes |
| Event reads | None. No `*ESR?`, `:EVEN?`, `*CLS`, or `STAT:PRES` |
| Wire end | `SYST:LOC` sent; final operator confirmation `CH1=OFF CH2=OFF CH3=OFF` |
| Outcome | `PASS`, `phase=READ_ONLY`, `link_known=true`, `physical_off_confirmed=true` |

Each `query`/`read` record and `driver_trace` represents the same on-wire
query, **not a duplicate command**. The first `OUTP:SER?` is logged as a
high-level query before the driver's injected `SYST:REM`; actual driver_trace
correctly shows `SYST:REM`, error poll, then `OUTP:SER?`. `INST:NSEL 1`
writes are repeated intentionally at the boundaries of OFF-verification
sweeps; there is **no** automatic ON/OFF.

## Nonblocking evidence caveats and boundaries

1. The original R3.5.38 results record used key `source_sha256` for a digest
   of `repr(results)`, **not a source-code hash**. This was independently
   reproduced. The key is corrected to `results_sha256` with canonical JSON
   for **future** evidence, without changing this immutable historical file.
2. Pre-open operator gates were not duplicated into the initial JSONL; they
   are present in the terminal capture. Future runs record a retrospective
   pre-open approval marker in JSONL without asserting its timestamp is the
   approval instant.
3. The last `INST:NSEL 1` selection write has a successful SCPI error poll,
   but no additional `INST:NSEL?` response **after** it. The initial selected
   channel and status-phase channel-preservation check were confirmed as 1.
   No stronger claim about the final selection readback is made.
4. The final `SYST:LOC` has no SCPI error poll by intentional scope design.
   Output OFF after LOCAL is confirmed by the operator, not an additional
   post-LOC SCPI read. This is not an independent electrical voltage/current
   measurement; no meter/current readings were acquired.
5. The zero masks confirm **reads and decoding**, not enable-mask behavior,
   queued event behavior, interrupts/SRQ, persistence, or the event-consuming
   R4.2-B path. Manual §5 contains an illustrative `SYST:MODU?` sample rather
   than a mandatory module identifier value.

**Action:** Retain read-only HW PASS in this bounded scope; commit code + this
review. R4.2-B requires a fresh operator-approved read-to-clear run and separate
review before `HW VERIFIED` can be recorded for those commands. No additional
multi-instrument coordinator or experiment is implied.
