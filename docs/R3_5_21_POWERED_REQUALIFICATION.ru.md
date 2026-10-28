# R3.5.21 — preparation for powered H09/H10 requalification

## Scope and release gate

This increment is **offline-only**. It does not authorize a physical run. The
R3.5.20 patch must be separately verified and committed before R3.5.21 is
applied. Do not mix the two staged candidates or commit hardware evidence.

The existing powered runners were already exercised on an older revision.
However, OFF-only results from 28 October do not constitute powered
requalification of the current revision.

R3.5.21 updates the powered H09/H10 exception/finalization paths to reuse
`ShutdownEvidence`, `release_psu_and_load` and a common finalizer. The PSU and
load have independent OFF attempts and readbacks. A KEL103 adapter originally
constructed with the raw Evidence object is rebound to shutdown-only evidence
for the bounded cleanup: otherwise a full disk could prevent USB INPUT OFF.
A readback mismatch, uncertain connection, or incomplete evidence is never PASS.
No automatic reconnection and no SCPI retries after link uncertainty.

## Mandatory order, once the patch and wiring have been approved

1. **H09 Series low-power first.** Physically inspect wiring before enabling
   any outputs. Keithley CH1− ↔ CH2+; KEL103 and DMM7510 across CH1+ / CH2−.
   CH3 open. The existing `H09 --powered` runner performs CV at ~2 V with
   CR40, then CC at ~3 V with CR12. Its readbacks and display-confirmation
   gates must succeed. Each transition requires PSU and load OFF.
2. **H09 Series high-voltage second.** Before changing to the 40 V profile,
   the same runner now requires a **separate operator authorization** with
   confirmed PSU ALL OFF, KEL103 INPUT OFF, DMM7510 FRONT DCV100, and the
   physical Series jumper still in place. Only then perform CV at ~40 V with
   CR2000 and CC with 40 V setpoint / CR1600, verifying actual voltage,
   current, power, and the panel's CC indication independently. The 40 V
   software cap is **not independent hardware OVP**.
3. **H10 Parallel low-power.** Only after H09 evidence has been reviewed.
   Parallel jumper pair: CH1+ ↔ CH2+ AND CH1− ↔ CH2−, no short across polarity.
   KEL103 and DMM7510 across the parallel pair, CH3 open. Run bounded CV/CC
   with CR30 and CR5, checking all three instruments and both readbacks OFF.
4. **H10 Parallel high-current.** A **different, separately approved run**
   (`--high-current` instead of `--powered`), only after reviewing the low
   powered H10 evidence. Use suitable independently verified AWG10 leads and
   connections. The existing controlled point is ~2 V / 4 A / ~8 W under
   CR0.5; measured load power must be ≤10 W. This checks **aggregate** current,
   not the individual CH1/CH2 sharing. No 5 A point is authorized by this patch.

For all steps: no rewiring while outputs are live, no oscilloscope/common
bus or unexpected return path, no powered rerun after a fault without review.
Confirm Output Recall OFF and accessible mains OFF. An operator confirmation
is not electrical proof of the jumper topology; check it physically.

After all three electrical instruments reach OFF, the powered runner requires
manual removal of the supply/load/meter wiring and the jumpers before restoring
Independent. The CLI additionally requests its final physical OFF token.

## Offline preview (does not touch instruments)

```bash
python3 tools/hw_qualify.py preflight H09 --powered --config config/bench.local.toml
python3 tools/hw_qualify.py run H09 --powered --config config/bench.local.toml --dry-run
python3 tools/hw_qualify.py preflight H10 --powered --config config/bench.local.toml
python3 tools/hw_qualify.py run H10 --powered --config config/bench.local.toml --dry-run
python3 tools/hw_qualify.py preflight H10 --high-current --config config/bench.local.toml
python3 tools/hw_qualify.py run H10 --high-current --config config/bench.local.toml --dry-run
```

None of the `--execute` variants are authorized by this document or this patch.
Request an individual live-run authorization after the offline patch, the
bench config, wiring diagram, instrument protections and fault-injection report
have been independently reviewed.

## Acceptance and evidence

Track three separate powered cases: H09 `--powered`, H10 `--powered`, H10
`--high-current`. Record per case: config SHA, exact operator gates, SCPI
sequence and checked queue, selected/combined topology, independent DMM7510
voltage, KEL103 voltage/current/power, Keithley measured values and status
raw masks, CH3 open and zero, maximum observed power, physical OFF and jumper
removal, and the evidence location. The historical reports remain valid as
observations of earlier code only. Do not promote any H09/H10 to full current
revision qualification until each corresponding powered case is reviewed.

## R3.5.22 — bounded operator display confirmations

The H09/H10 powered display gates now impose an explicit 120-second deadline
for each point while the PSU is energized. Failure to enter the exact
confirmation in time causes the existing runner to abort and use the shared
fail-closed PSU/KEL103 cleanup. OFF-only wiring and high-voltage transition
approvals are unchanged and may be reviewed without energizing outputs.
No new setpoints, SCPI commands, or wiring schemes are added.
