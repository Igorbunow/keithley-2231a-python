# R5-E7-HW — Independent neutral `*TRG` OFF-only review (2026-10-31)

## Verdict and boundaries

**REVIEWABLE_PASS_NEUTRAL_OFF_ONLY_NOT_ACTUATION.** One operator-authorized hardware event on a Keithley 2231A-30-3, empty output terminals, independent profile, CH1/CH2/CH3 OFF. Original JSONL was independently audited using `tools/audit_r5_e7_neutral_trigger.py`, followed by an independent command/response/timing inspection. Do **not** infer triggered setpoint switching, coupling behavior, analog voltage/current, synchronization, response time or powered behavior.

The full original file is **not** included in Git. Its pinned identity is `events(20261031-112541).jsonl`, SHA-256 `0c3dd7335dc15a304f65bd14fa604d11ef07c24accc225a93b2bed4c0aa71116` (14,352 records, 3,263 queries, 645 writes, 1,978 `SYST:ERR?`). An independent audit can be rerun with `python3 tools/audit_r5_e8_neutral_hw_evidence.py /absolute/path/events.jsonl`.

### Evidence

- The event was recorded at **2026-10-31 11:24:25.746959 UTC**, within a 114.535 s qualification run. Exactly one physical `*TRG` write; no `TRIG`, `INST:COUP`, `OUTP 1`, `CHAN:OUTP 1`, powered load or external metrology.
- The trigger presets before the run were **0.000 V / 0.000 A** on each channel. They were neutralized to CH1 **3.300 V / 0.150 A**, CH2 and CH3 **1.000 V / 0.100 A**; the six original values were subsequently restored and read back as zeros.
- All 240 `CHAN:OUTP?` readbacks were `0` (OFF). All `OUTP:SER?`, `OUTP:PARALLEL?`, `OUTP:TRAC?`, `OUTP:TIM:STAT?` readbacks were `0`. Voltage limit states remained enabled and active V/I settings remained unchanged.
- All 1,978 `SYST:ERR?` replies indicated zero; 3,263 queries had corresponding responses in the independent trace. No mismatched selection, nonfinite values, UART timeout or unrecognized write was found. Two `*IDN?` reads reported `1.04-1.04`, with no firmware revision drift.
- BENCH and INITIAL operator gates preceded device opening; PROFILE, EVENT and FINAL gates were recorded in order, with a final physical OFF confirmation.
- Executable source SHA-256 `bb6896a7662c00ffefc36f5c3dce2a97d725e078e244fc711ca2c05fa021a0da`, runner SHA-256 `da1c0e90157fdd258f35997d998a1724516f45a972088d70810a89eb0b1c6204`, stage SHA-256 `5cbf4b3c94e217bd746d911037b0647cd54820113431fdd07e19f7397f973b08`. Git HEAD at execution was `2a11929`; evidence metadata says `dirty=true`. File hashes matched the locally reconstructed R5-E7 implementation, but the reason for the dirty flag cannot be determined from the original JSONL alone.

### Nonblocking observations / remaining risks

1. There are **50 adjacent `SYST:ERR?` pairs in the query/write call sequence** (one pair is separated on the actual trace by the deferred `OUTP:SER?` reply). The observed duplication is consistent with automatic per-operation error checking in the stage adapter plus explicit `check_errors()` in production trigger-register methods. Both checks returned zero. Do not remove production-driver error checks merely to optimize this qualification runner. A subsequent software-only task may reduce redundant diagnostics without weakening safety.
2. The 1,978 error queue queries and 602 channel selection writes make this one-shot OFF-only qualification relatively verbose; this is a latency/trace-volume observation, not proof of a command-order fault. Observed query replies were complete. A 94 ms worst-case query latency was observed, without a timeout.
3. No external DMM7510, KEL103 or oscilloscope measurements were performed because the output terminals were intentionally **empty**. Digital OFF readbacks and operator confirmation cannot certify absence of a transient between samples.
4. The `*TRG` event was deliberately neutral. It cannot reveal which subset `INST:COUP` would select, whether a non-neutral event is accepted, whether the outputs change, or whether simultaneous actuation is supported.
5. The active independent OFF profile overwrote inactive ordinary U/I setpoints. Trigger presets were restored; ordinary pre-run setpoints were not an explicit part of restoration. This is documented behavior, not a new fault.
6. The complete terminal transcript was not uploaded separately for this event; operator gates and wire commands were cross-checked from original JSONL. Do not claim terminal output was independently reviewed.

### Scope and next phase

Promote **only** `neutral_trigger_bus_off_candidate` to `bounded_hw`, strictly for the reviewed one-shot neutral event under empty-terminal OFF conditions. Keep `hardware_triggered_setpoint` and `trigger_channel_coupling` unqualified and not generally callable. Next: R5-E9 software-only grammar and safety exploration for `INST:COUP` / non-neutral trigger transitions, without hardware execution; require a new operator gate and independent evidence for any subsequent physical stage. Historical H09/H10 loaded Series/Parallel endpoints remain separate open qualifications.
