# R4.2-B EVENT hardware qualification — independently reviewed 2026-10-29

**Decision:** **HW REVIEWED PASS (bounded EVENT scope)** for one physical Keithley
2231A-30-3, firmware `1.04-1.04`, on the **committed** R3.5.39 source
(`9bd7232`). R4.2-A/C were independently reviewed in the preceding READ_ONLY
run. This review does **not** verify that events are actually cleared upon read;
that semantic is specified by the vendor, but no second destructive query was
performed (deliberate one-shot protocol).

## Source evidence and integrity

- Terminal: `terminal(20261029-102744).log`, SHA256
  `0b5c0ce714a8a75610437369b8d2fc06d34f40caf733711f7226c4ebc0a3dfed`.
- Wire/evidence: `events(20261029-102807).jsonl`, SHA256
  `5bd6df02572156dc85644c23ee44c082f99e4b6b9f765b5fafbf209c85887b3b`.
- Bench configuration SHA256:
  `858887cd453c4e8ef7f0473686c578af03868f478cd73afdc09b7e66d4912bb8`.
- JSONL entrypoint SHA256 (`tools/r4_status_qualify.py`):
  `3ca86368a6a3bfb91cbabbaefdfddd25052d2f917c4e2e808a6bb6fc91299950`.
- JSONL stage SHA256 (`tests/hardware/r4_status_stage.py`):
  `c53fedcbb79895123bc41946c272a96075afc3ce98283a573c374c615e76280f`.
- JSONL's reported Git HEAD is `9bd7232b65cdbb6f78be0ead504a4885173a6a98`,
  matching the new operator commit. `dirty=true` is reported; terminal `git
  status --short` showed numerous unrelated *untracked* historical hardware
  reports. These were not staged by this test, and the two R4 runner hashes
  independently match the committed source files. Do not interpret the `dirty`
  flag as proof of code drift or of a clean tree.
- Historical source evidence is retained by the operator. Neither raw trace,
  serial number nor private bench TOML belongs in the installable distribution.

## Independent JSONL/SCPI audit

| Property | Observed |
|---|---|
| JSONL and chronology | 727 valid unique-key JSON objects; UTC and monotonic timestamps in order; 14.562 s total |
| SCPI low-level trace | 360 driver-trace records: **167 queries + 167 replies + 26 writes**; exactly paired; 193 on-wire commands in order |
| SCPI error queue | Exactly **96** `SYST:ERR?` replies, all `0, "No error"`; one after every other wire operation except final `SYST:LOC` |
| Identity | Two agreeing `*IDN?` replies, `Keithley instruments, 2231A-30-3, [REDACTED], 1.04-1.04` |
| Topology | Series, Parallel and Tracking read back `0` |
| Physical output readback | Eighteen `CHAN:OUTP? = 0` replies: **six** sequential CH1→CH2→CH3 OFF sweeps |
| Module/status masks | `SYST:MODU? = 6302  , 1.04`; four standard and eight nested enable masks each read once, all `0` |
| `*ESR?` | **128**, set **bit 7 PON** (power-on/reset event); no command/execution/device/query-error bits |
| `STAT:QUES:EVEN?` | `0` |
| `STAT:OPER:EVEN?` | `0` |
| Operator gates | BENCH and INITIAL before equipment open, separate EVENT confirmation before first event query, FINAL physical OFF after `SYST:LOC` |
| Prohibited commands | **None**: no `OUTP` write, `CHAN:OUTP` write, `VOLT`, `CURR`, `*RST`, `*CLS`, `STAT:PRES`, event re-read or enable-mask write |
| Results integrity | `results_sha256=23bf7fd9cb8d44486bfd2924639f3e13c70934c97e489166bfbacce564f17e1c` independently equals canonical `json.dumps(values,sort_keys=True,separators=(',',':'),ensure_ascii=True)` SHA256 |
| End state | One `SYST:LOC` write, final manual CH1/2/3 OFF acknowledgement, `outcome=PASS`, `phase=EVENT`, `link_known=true` |

All three event reads followed the separate operator EVENT approval. Each had
its **own immediately preceding three-channel OFF sweep** and successful
post-read `SYST:ERR?`. This is six OFF sweeps total: initial,
post-mask, three per-event, and final. The events were read in the
planned order exactly once; there are no retries or missing replies.
The two identical `*IDN?` values indicate no observed device substitution.
Query/read/driver_trace records refer to the **same** transactions, not three
separate commands. The first user-level `OUTP:SER?` can precede the injected
remote command in the high-level log, but the on-wire trace correctly begins
with `SYST:REM` followed by the error query.

## Register interpretation and limits of proof

- Vendor publication **077100401**, §5, printed pp. **5-2–5-3** and status
  model: Standard Event bit 7 `PON = 128` denotes an event from power-on/reset.
  Seeing it here is **not** evidence that a reset occurred during this 14.56 s
  test; the previous read-only run intentionally did not consume `*ESR?`.
  `*ESE? = 0` means this event was not enabled for ESB/SRQ reporting; no
  contradiction with a nonzero event latch or a zero `SYST:ERR?` queue.
- `STAT:QUES:EVEN? = 0` and `STAT:OPER:EVEN? = 0` are valid one-shot responses,
  not a proof of nonworking status logic. No stimulus was generated and no
  before/after clearing check was performed. **Read-to-clear behavior is
  documented but not experimentally demonstrated** by this protocol.
- Repeated read-only enable-mask values establish command acceptance and
  decoding, **not** configurable mask writes, SRQ/interrupt behavior, stored
  event persistence, or immunity to external changes.
- Each OFF check is **sequential**, not an electrical interlock or independent
  DMM verification. No voltage/current/power was measured; no DUT or load was
  attached. A separate controller could theoretically change output state
  between checks, contrary to the physical gate.
- The final `INST:NSEL 1` restore was not followed by an independent
  `INST:NSEL?` confirmation; the subsequent `SYST:ERR?` was zero. Final
  `SYST:LOC` intentionally had no error poll, and post-LOC OFF was confirmed
  by the operator rather than an on-wire measurement.
- The evidence was acquired with the committed source; status metadata
  `dirty=true` reflects observed untracked reports and merits retention,
  not silent normalization.

**Outcome:** qualify R4.2-B one-shot event query/response on real hardware in
this exact bounded OFF-only scope. Do not repeat event reads to claim a stronger
clear-on-query proof without a *new* explicit, separately reviewed test plan.
Move to R4.3 manual/risk disposition before implementing any additional
SCPI writes. Multi-instrument orchestration stays excluded.
