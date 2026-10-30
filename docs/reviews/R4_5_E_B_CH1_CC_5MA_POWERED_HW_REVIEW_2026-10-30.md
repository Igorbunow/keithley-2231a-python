# R4.5-E-B — CH1 CC, powered `CURR:UP 0.005` — аппаратный review

**Review verdict: `HW REVIEWED PASS` for exactly one steady-state CC point under load.**
Do not extrapolate to analog calibration, transient response, multiple STEP commands, or other channels.

## Raw evidence and immutable provenance

- Original file: `reports/hardware/2026-10-30T102746.572121Z_R45E_CH1_cc_ONE_STEP/events.jsonl`
- SHA-256: `2c417b48f86922784bf2bb5e94ba68877fbaed161ad37f69377ab4e8a924c01f`
- 3,941 JSONL records; `start` on 2026-10-30 10:27:56 UTC; `outcome=PASS`.
- Source commit in evidence: `3e1584818a22173701994ec93ad1b28eaaf4f41f`; `dirty=true` (not independently resolved).
- Config SHA-256: `e4c580a71c6931d31b47fec86d3de270047a633818835c9b3f53490b92e9d720`.
- Runner SHA-256: `a16d16af3f6c99ebf3bc8dae4cbab1c3f4275aabc1df442cee5911ec2db6acef`.
- Stage SHA-256: `4ad4a03ac27300517ffa859ef5fa8d21bd9b7a5e8ec204393bc7fe38823cb92e`.
- Keithley `*IDN?`: `2231A-30-3`, firmware `1.04-1.04` observed twice; known alternative Aux `1.04-0.01` is unrelated to this case.
- DMM7510 firmware `1.7.18a`, SCPI, front DCV range 10 V, NPLC 1; KEL103 CR 11 Ω.

## Independent measurements — before and after

| Observation | Before | After | Delta |
| --- | ---: | ---: | ---: |
| CH1 current setpoint | 0.100 A | 0.105 A | +0.005 A |
| Keithley `MEAS:CURR?` | 0.0996704 A | 0.104679 A | +0.0050086 A |
| KEL103 current | 0.1001 A | 0.1052 A | +0.0051 A |
| Keithley `MEAS:VOLT?` | 1.10993 V | 1.16595 V | +0.05602 V |
| DMM7510 DCV | 1.105249 V | 1.161415 V | +0.056166 V |
| KEL103 voltage | 1.1048 V | 1.1599 V | +0.0551 V |
| Keithley `MEAS:POW?` | 0.110655 W | 0.122055 W | +0.011400 W |
| KEL103 power | 0.1105 W | 0.1220 W | +0.0115 W |

- KEL103 effective resistance `U/I`: 11.037 Ω before; 11.026 Ω after. DMM/KEL-current equivalent: 11.041 Ω before; 11.040 Ω after; compatible with CR 11 Ω.
- DMM change 56.166 mV versus `11 Ω × 5.1 mA = 56.1 mV`, mismatch 0.066 mV.
- Keithley/KEL current discrepancy: 0.430 mA and 0.521 mA (not zero; stable-scale discrepancy, not calibrated here).
- Keithley/DMM voltage discrepancy: 4.681 mV and 4.535 mV; DMM/KEL voltage discrepancy: 0.449 mV and 1.515 mV. These are *observations*, not assignments of fault to an instrument or cable.
- PSU power `MEAS:POW?` versus PSU `U×I`: +0.0278 mW before, +0.0045 mW after. KEL power versus KEL `U×I`: −0.0905 mW before, −0.0215 mW after.
- Independent snapshot timestamps have `PSU < KEL < DMM < PSU power` ordering, not synchronous triggering. First PSU sample **4.28 s after the logged `CURR:UP` write**. This is not a measurement of electrical settling time.

## Command and safety audit

- 863 paired PSU SCPI queries/replies, 186 physical SCPI writes, 525 **zero-code** `SYST:ERR?` results; no nonzero error, missing response, or conflicting stage-vs-wire reply.
- Exactly one `CURR:STEP 0.005` before power ON and one `CURR:UP` while CH1 ON. `CURR?` after UP = 0.105 A. No auto-retry, `CURR:DOWN`, global `OUTP 1`, or additional powered case.
- KEL103 configured `CR 11 Ω`, input ON before `CHAN:OUTP 1`; CH2 and CH3 independently read OFF throughout.
- Keithley `STAT:OPER:INST:ISUM1:COND? = 10` and `STAT:QUES:INST:ISUM1:COND? = 2` before and after: consistent CC, not inferred from setpoint alone.
- DMM7510 configured via SCPI and `*RST`; it remains in configured DCV at end. KEL103 input is OFF at end but CR configuration is not restored. New runs must revalidate both instruments.
- Shutdown: `OUTP 0`, repeated all-channel OFF reads; independent KEL103 `:INP OFF` verified twice; then `SYST:LOC`, final manual OFF confirmation. 58 zero output readbacks across the run (13 CH1 ON readbacks are expected).
- No continuous voltage/current capture, time-domain settling proof, calibration claim, or electrical verification of cable topology from software alone.
- Physical FINAL confirmation arrived about **5 min 4 s after `SYST:LOC`**, not immediately; digital OFF and KEL OFF had already been read back. This interval is recorded, not silently omitted.
- Initial BENCH/INITIAL approvals are evidenced by a pre-open summary rather than independent timestamped live instrument observations. `dirty=true` prevents claiming an entirely clean source tree.

## Verification and next stage

Verify this original JSONL with the common auditor followed by the CC-specific physics auditor:

```bash
python3 tools/audit_r4_5_powered_step_cc_review.py \
  reports/hardware/2026-10-30T102746.572121Z_R45E_CH1_cc_ONE_STEP/events.jsonl \
  --expected-sha256 2c417b48f86922784bf2bb5e94ba68877fbaed161ad37f69377ab4e8a924c01f
```

**R4.5-E-B CV and CC each have a separate real powered STEP qualification.** This does *not* certify unrestricted powered STEP, the entire power range, or fast automatic IV curves. Next: a bounded, separately operator-authorized small-point sweep with external measurements and controlled fail-closed shutdown; no more OFF-only matrix is necessary.
