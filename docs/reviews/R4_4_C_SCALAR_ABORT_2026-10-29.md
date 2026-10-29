# R4.4-C CH1 scalar qualification — aborted-hardware evidence review (2026-10-29)

**Disposition: `HW ABORTED` / `NOT REVIEWED PASS`. Do not treat the new
`MEAS:POW?` and `FETC:*` queries as HW tested. Do not automatically repeat the
powered stage until the physical DMM path has been diagnosed.**

## Raw, operator-controlled evidence

- Terminal `terminal(20261029-210025).log`; stage executed after 636 program
  tests reported OK and patch SHA256 `5df0778ffa4e72972596212662657d4bb31306d0801d63bb2b31fead29248e9d`
  was matched; commit `0ff8e3a` created.
- Original `events(7).jsonl`: **1474 records**, SHA256
  `65e83d3820cc7251058c34c21deb3a91be576adcf337fd478daaca1f1bb5177a`.
  Provenance in the journal cites source HEAD `0ff8e3a25f564c54bad2a3d55821af8f88165f23`,
  `dirty=true`; that flag is preserved, not interpreted as a clean tree.
- Bench configuration SHA256:
  `e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720`.
  The private configuration and unredacted instrument identities are excluded.
- UTC and monotonic event timestamps strictly increase. Journal duration
  approximately 127.227 s, including two long operator-confirmation intervals.

## Exact failure and independent readings

| Observation | Source / reply | Finding |
|---|---|---|
| CH1 programmed | `VOLT?=3.3`, `CURR?=0.15` | CV voltage setpoint and current limit; `CURR?` **not** measured output current |
| CH1 live | `MEAS:VOLT?=3.29797`, `MEAS:CURR?=0.0811329` | ~0.26757 W derived from non-simultaneous samples |
| CH1 regulation | `STAT:OPER:INST:ISUM1:COND?=9`, `STAT:QUES:INST:ISUM1:COND?=1` | Both indicate CV, not CC, per driver bit interpretation |
| KEL103 | `3.2959V`, `0.0821A`, `0.2705W`, `CR=40.000OHM` | Consistent with CH1: U differs by 2.07 mV, I by 0.9671 mA |
| DMM7510 | `:READ? = 1.161243E+01` = **11.61243 V** | Invalid against 10 V configured range and ~3.3 V physical working point |
| DMM error queue | `:SYST:ERR? = 0,"No error;0;0 0"` | No DMM SCPI error reported; does not certify voltage correct |
| Stage | `ValueError: Invalid/overrange DMM7510 voltage` | Abort correctly prevented further power/FETC queries |

DMM model `DMM7510`, firmware `1.7.18a`, SCPI language; `:ROUT:TERM?=FRON`
was queried **before** `*RST`. After reset, `:SENS:FUNC?=VOLT:DC`,
`:SENS:VOLT:RANG?=10`, and `:SENS:VOLT:NPLC?=1` were verified.
The stage did **not** re-query FRONT terminals after `*RST`; this is a
verification gap, not evidence that the terminals actually changed. With
`:SENS:VOLT:INP AUTO` on 10 V the DMM input is high impedance, so an
open/floating/misplaced lead is a plausible hypothesis, not a proven cause.
Reversed HI/LO alone would not normally explain a **positive** 11.6 V
reading across an otherwise 3.3 V output.

The reading was a **finite SCPI numeric** and arrived ~23 ms after `:READ?`:
not a timeout, string-decoding failure, proof of DMM calibration failure,
or proof the PSU supplied 11.6 V. Source/load agreement strongly suggests the
output was near 3.3 V at the time of their sequential observations, but the
physical DMM wiring/measurement must be inspected independently.

## Wire and safety invariants

- PSU: 306 queries and 306 responses, 80 writes; 692 corresponding trace
  events. There were 193 `SYST:ERR?` polls, **all returned zero**. Note:
  high-level query/read order is nested by injected error polls; it is not a
  second on-wire query or data loss.
- DMM: 20 queries / 20 replies, 7 writes. KEL: 15 queries / 15 replies,
  4 writes. No detected missing DMM/KEL reply.
- `CHAN:OUTP 1` was sent **once** for CH1 and verified with `CHAN:OUTP?=1`.
  All CH2/CH3 `CHAN:OUTP?` responses were zero throughout this run.
  CH2/CH3 preset VOLT/CURR writes occurred while their outputs were OFF.
- KEL `:INP ON` occurred before CH1 ON, and the load readback was `ON`.
  Its measured voltage, current and power agree with the PSU.
- Abort was logged immediately after the DMM voltage read. There were
  **no** `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?` or `FETC:POW?` transmissions.
- Cleanup started ~44 ms after the stage error: `OUTP 0` exactly once
  during cleanup, followed by two full three-channel sets of OFF readbacks.
  All six responses were `0`. KEL `:INP OFF` then confirmed by two `:INP?=OFF`.
- Terminal records the operator's final physical OFF acknowledgment.
  It remains operator evidence, not an independent voltage measurement.
- No `SYST:LOC` in failure path (intentional), no reconnection/retry,
  no `*CLS`, PSU reset, topology ON, extra CH1 ON or out-of-scope triggering.

## Follow-up before any powered repeat

Keep PSU CH1/CH2/CH3 OFF and KEL input OFF. First inspect the **actual**
DMM FRONT HI/LO lead pair and whether both probes contact CH1+ / CH1-;
check the DMM panel reading and post-reset terminal/range settings.
With meter probes **disconnected from all supplies**, a separately verified
short across only its own HI/LO probe ends should read approximately zero;
do not short the PSU. Verify the DMM against a known, independent low-voltage
reference (for example a 1.5 V cell). Only after the discrepancy is
understood and the physical connection is independently confirmed should an
operator consider another separately authorized low-power trial.

This incident does **not** justify relaxing DMM limits, increasing the
range to 100 V to suppress failure, omitting the DMM, or marking scalar/FETC
as HW VERIFIED. The accompanying software-only follow-up improves numeric
error diagnostics and aborts immediately if a finite **within-range** DMM
reading disagrees with the PSU and KEL, before any scalar/FETC traffic.
