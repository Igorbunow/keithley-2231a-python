# R4.4-B DISP? — independent hardware evidence review (2026-10-29)

**Verdict: HW REVIEWED PASS — precisely one OFF-only `DISP? = 1` observation.**

- Instrument: Keithley 2231A-30-3, observed firmware `1.04-1.04` in both `*IDN?` replies. Serial number is redacted.
- Recorded software HEAD: `0ebaa40d591f3cd7f8dd1f0e794b86eb33bbd577`.
- Configuration SHA-256: `858887cd453c4e8ef7f0473686c578af03868f478cd73afdc09b7e66d4912bb8`.
- Original evidence path (operator workstation): `reports/hardware/2026-10-29T195616.072725Z_R44_DISPLAY_OFF/events.jsonl`.
- Evidence SHA-256 (raw, 254 JSONL records): `12036c737d721df51e4ea8157d80ad517640063f9bbe9b7d67b705eaef0434bd`.
- Reproduced runner SHA-256: `fa267658c662749fcd1aac5d5294189fc354ebaddbd4d39c7a881e825021c003`.
- Reproduced stage SHA-256: `34b959d5c7403d82ea650fdc19a722c5449c72523f94607b29b0b1cc593c0c46`.
- Operator confirmation: bench identity, starting empty terminals and physical OFF, then final physical OFF after LOCAL. These are operator attestations, not an independent electrical measurement.

## SCPI evidence audit

- 57 queries, 57 replies, 10 writes, 33 `SYST:ERR?` replies — all `0, "No error"`.
- Only writes: `SYST:REM`, `INST:NSEL 1/2/3` and final `SYST:LOC`. No `OUTP`, voltage/current setpoint, topology write, trigger, reset or event-latch read.
- CH1, CH2, CH3 `CHAN:OUTP?` each returned `0` **before and after** `DISP?`.
- `OUTP:SER?=0`, `OUTP:PARALLEL?=0`, `OUTP:TRAC?=0`; selected channel checks consistent with the explicit `INST:NSEL` writes.
- One `DISP?` reply `1`, mapped to `display_enabled=True`; a powered output is **not** implied.
- Two consistent `*IDN?` readbacks; no firmware drift, malformed reply, timeout, unexpected SCPI command or error-queue result.
- `SYST:LOC` has no readback by protocol design; only delivery to the transport and subsequent operator OFF confirmation are recorded. Do not present return to LOCAL as independently read back.
- The first outer `query: OUTP:SER?` evidence event precedes the trace for the driver's implicit `SYST:REM`. The **actual driver trace** shows `SYST:REM` before the actual `OUTP:SER?` transmission; the outer event logs intent prior to opening the remote session. This is an evidence layering artifact, not an inverted on-wire order.

## Remaining caveats

The recorded `software.dirty=true` is **not** a clean tracked-code assertion: `software_metadata()` computes it using `git status --porcelain`, which also counts existing untracked hardware reports. Verified runner/stage hashes and the recorded `source_sha256` provide stronger provenance, but the reviewed evidence does not independently prove the absence of other working-tree edits.

No DMM7510, KEL103 or TDS3064B measurement was taken in this phase. Earlier values printed by the *software regression tests* must not be represented as measurements from R4.4-B. No new scalar `MEAS:POW?`/`FETC:*` command was physically executed.

This review qualifies the **single observed ON state of the front-panel display**, not display writes, the `DISP?=0` firmware case, power-output safety over all conditions, measurement performance or additional firmware versions. The runner's `PASS` alone is not the review; the independent examination of its original evidence is the basis for this status.

Re-run the static audit on the unchanged evidence file (no instrument access):

```bash
python3 tools/audit_r4_4_display_evidence.py \
  reports/hardware/2026-10-29T195616.072725Z_R44_DISPLAY_OFF/events.jsonl \
  --expected-sha256 12036c737d721df51e4ea8157d80ad517640063f9bbe9b7d67b705eaef0434bd
```

The audit tool returns `REVIEWABLE_PASS`, **not** an automatic HW qualification: it cannot independently verify what the operator saw or whether the underlying USB device behaved beyond the logged replies.

## R4.4-C handoff (not yet authorized)

Next, prepare a separate, bounded **low-power Independent CH1** measurement qualification with real KEL103 and DMM7510, on a previously qualified H03/H04P electrical point. Before any output ON: verify physical wiring and independent topology, configured identities, all outputs OFF, KEL103 INPUT OFF, explicit voltage/current/power caps, measured/verified CR, profile verification and exact operator authorization. Use a strict SCPI allowlist and do not introduce any alternate production driver.

Measure and log `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?`, `FETC:POW?`, sequential source U×I and independently observed KEL103 U/I/P and DMM7510 voltage, with **predeclared tolerances** and timestamps. Cached `FETC` values may be stale and are not guaranteed simultaneous. Ensure both PSU outputs and KEL input are positively verified OFF on exit; on any uncertain link or failed shutdown, report UNKNOWN and demand manual OFF. Only after offline tests and explicit review of a new hardware plan may a powered `--execute` be offered.
