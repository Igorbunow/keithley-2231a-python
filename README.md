# keithley-2231a-python

[**English**](README.md) | [**Русский**](docs/README.ru.md)

**Python control library for the Keithley 2231A-30-3 three-channel DC power supply.**
Provides SCPI over USB–TTL UART, optional transparent TCP–UART bridges, immutable safety profiles, per-channel sequencing, measurement logs/statistics, DC voltage sweeps (including I–V curves).

> **Experimental software / hardware qualification in progress.** A successful simulator/CI run does not validate an output command on a real instrument. The operator confirmed that the front-panel Main/Aux display always matched `*IDN?` for the selected channel. A controlled OFF-only diagnostic observed `1.04-1.04` on CH1/CH2 and `1.04-0.01` on CH3. This does not establish which internal firmware module supplies the Aux field. Firmware is reported and recorded, not hard-coded as an API compatibility gate. See [verification matrix](docs/VERIFICATION.md) before powering a DUT. This is NOT a safety-rated protection system.

**Version:** 0.2.1.dev0 · **Python:** 3.10+ · **License:** [MIT](LICENSE) · **Manufacturer affiliation:** none (independent community project).

**Archived O02-A experimental draft (R3.5.33/R3.5.34), outside the installed package in `experiments/o02_dual_psu/`.** `python3 tools/o02_offline.py --evidence /tmp/o02_a_nominal.jsonl` simulates two independent synthetic PSU sessions, separate identity-bound approvals and fail-closed independent OFF; fault-injection scenarios never access SCPI, serial or TCP. A simulation PASS is **not** evidence of two physical devices or simultaneous sampling; O02 hardware remains NOT_RUN. [O02-A details (RU)](docs/O02_A_SIMULATION.ru.md).

## Why this library?

- **Three independent channels:** CH1/CH2: 0–30 V, 0–3 A; CH3: 0–5 V, 0–3 A. 195 W nameplate combined output rating.
- **Series / parallel / tracking:** API support, with explicit physical-wiring confirmation for Series/Parallel. **SCPI does NOT insert external series/parallel jumpers or switch the output terminals internally.** Connections must be installed and inspected **while outputs are off**. The 2231A combined modes also change metering semantics; read the vendor manual.
- **Conservative profiles:** per-channel V/I bounds, hardware voltage limit setting, output-off-first profile arming, optional CV/CC expectation and explicit opt-in integrity polling.
- **Full-rated-range profile:** `Profile.unrestricted()` allows all **rated** V/I settings, without extra user-specific reduced caps; it is **not** a raw SCPI bypass or permission to exceed instrument ratings.
- **Power-only profile:** output levels and topology are frozen; operators may switch power on/off. Fixed `power_sequence` enables channels in order and disables in reverse. No arbitrary channel toggles when a sequence is specified.
- **Measurement and automation:** per-channel CSV logs, min/max/mean/population σ, Ah/Wh integration; guarded voltage sweeps with optional DMM callback; optional external readings via an observer callback.
- **Replaceable transports:** UART `SerialTransport('/dev/ttyUSB1')`, future MCU bridge `TcpTransport('psu-lab.local', 5025)`; TCP is a transparent UART tunnel, **not native Ethernet, VISA or VXI-11**.

## Service commands — guarded real driver, UART tests (R3.5.49)

**Dangerous service commands are not ordinary API operations. Do not use without
an actual maintenance requirement and a qualified engineer.** The only service
path is `Keithley2231A.service_operation(...)` using the common guarded driver
SCPI dispatcher. A separate `ServiceProfile` file with exact model/serial and
explicit per-operation rights, profile-hash-bound approval and an additional
execution approval are required. Critical operations require a third approval.
The ordinary `diagnostic_write()` and `debug_bypass_profile` can NEVER access
service commands. Strict OFF-only/Independent preflight and identity checks run
before any service command. A fault stops the sequence without retry.

Tests exercise the **actual driver through a pseudo-terminal (virtual UART)**;
this validates driver command bytes/ordering/fault logic, NOT undocumented
physical firmware behavior. The former synthetic service simulator, offline
gateway and offline authority layers were removed. The `ServicePlanner` retained
inside driver policy only validates typed operation arguments and builds the
allowlisted wire sequence; it is NOT a separate execution engine.

**R3.5.50 virtual-UART status qualification:** the unchanged R4.3 PSC/OPC
hardware-stage code is exercised through the real `Keithley2231A` dispatcher
and a POSIX pseudo-terminal. Tests independently assert the complete 75-command
wire trace (including 37 `SYST:ERR?` polls), both output-OFF sweeps, exact
CRLF bytes, and fail-stop after malformed replies, timeouts, SCPI errors,
non-OFF outputs and status-enable drift. The UART peer now rejects unknown
commands rather than answering `1`. On hosts with `pyserial`, the tests use
production `SerialTransport`; the test-only PTY transport is a fallback.
Other historical fake-transport tests have not yet been migrated; do not
conflate these virtual-wire results with new physical qualification.

**R3.5.51 R4.2 status-mask UART migration:** the existing R4.2-A/C tests
now use the real `Keithley2231A` API over a strictly allowlisted POSIX
virtual UART instead of two `FakeTransport` subclasses. All four top-level
and eight nested enable-mask reads, `SYST:MODU?`, invalid inputs, missing or
malformed replies, and no-event-consumption wire rules are exercised. Exact
CRLF bytes and command order are checked, with `SerialTransport` selected
when `pyserial` is available. No production driver or hardware runner change.
The remaining historical fake-backed tests still need selective migration.

**R3.5.53 malformed-reply/error-queue UART migration:** the existing driver
regressions for invalid `CHAN:OUTP?`, `INST:NSEL?`, `*IDN?` and nonzero
`SYST:ERR?` have been moved from `test_driver.FakeTransport` to the **production
`Keithley2231A` over POSIX virtual UART**. The peer records exact ASCII + CRLF,
proves fail-stop without accidental restore, and preserves uncertain output
state on malformed/lost answers. Additional negative tests cover malformed and
missing SCPI error replies. This change does not modify the production driver,
validate real firmware, or authorize service commands on equipment.

**R3.5.52 R4.2 event-query UART migration:** all 12 R4.2-B event-consumption
regression tests now send SCPI through the actual `Keithley2231A` and a
strict POSIX virtual UART, replacing the remaining event-test `FakeTransport`
subclass. The UART peer models three independent read-to-clear event latches:
`*ESR?`, `STAT:QUES:EVEN?`, `STAT:OPER:EVEN?`. The test confirms exact CRLF
and preflight order, one explicit destructive query, channel restoration,
no automatic error-queue polls or extra latch reads, and fail-stop on an ON
channel, invalid response or lost answer. A simulated latch clear does **not**
prove that physical firmware cleared it; the 2026-10-29 hardware event run
remains the only bounded physical qualification. No driver or hardware stage
was modified.

**Safety hold:** calibration commands, unverified `*TST?`/`*PSC` write syntax,
and calibration-password handling remain blocked on the driver wire path until
their vendor procedure is fully reviewed. No service hardware test is authorized.
See [service and calibration guide](docs/R4_3_SERVICE_MODE.ru.md).

## Hardware and safety first

1. Rear DB9 is **TTL-level UART**, NOT ±RS-232. Use a correctly configured and wired USB–TTL adapter; never connect a conventional RS-232 dongle directly. The instrument serial protocol uses 9600 baud, 8N1.
2. The PSU output is **two-terminal only**, without remote Kelvin sensing. Measure voltage at DUT terminals with a separate DMM when lead drop matters.
3. **Series/Parallel require external power wiring.** Software can only configure the instrument's combined control/measurement modes. Confirm terminal polarity, load, cable current capability and the vendor wiring diagram before arming.
4. Power supply CV/CC is selected dynamically based on load and V/I setpoints; `expected_regulation='cv'`/`'cc'` means **monitor and trip on mismatch**, not force an internal regulator mode.
5. Front-panel locks, periodic polling, hardware `VOLT:LIM`, and SCPI OFF are **not independent electrical protection**. In particular, when serial/TCP communication fails, OFF may not reach the PSU. Use external interlocks, fuses, supervision and appropriate protective circuits for unattended or risky tests.
6. Check the instrument **Output Recall = Off** setting after a power-cycle; otherwise it may restore powered outputs without the Python controller.
7. **The PSU cannot sink current.** Do not use it as a battery electronic load, directly connect a charged battery, or infer battery discharge capacity from PSU-delivered Ah. Sweep scripts must use safe current limits and appropriate DUT series resistors.
8. Never use test scripts on valuable loads until their SCPI commands and transitions have been qualified on dummy loads.

## Instrument identity and firmware

The driver validates manufacturer and model but does not pin the firmware revision.
The firmware field is retained in the raw `instrument_id` response and qualification
evidence. For benches with multiple identical supplies, bind a driver instance to the
expected serial number; a mismatch is rejected during identity checking before profile
settings are written:

```python
psu = Keithley2231A(SerialTransport('/dev/ttyUSB1'), expected_serial='12345678')
print(psu.identity())  # raw *IDN? response, including the firmware revision
print(psu.instrument_key)  # stable manufacturer/model/serial tuple
```

Omit `expected_serial` when serial binding is not needed. Firmware behavior remains
qualification-specific: record the version shown in the instrument menu and the raw
`*IDN?` revision in every hardware report; a new revision still needs hardware review.

**Observed on one 2231A-30-3 (13 qualification runs, October 2026):** the same
physical supply returned `1.04-1.04` and `1.04-0.01` from `*IDN?`, including
within individual runs. Main stayed `1.04`; the manufacturer, model, serial,
SCPI communication and validated output behavior did not change. The operator
confirmed that the panel and SCPI readings were identical for the selected channel. The previously reported panel `Aux 1.01` was a transcription error, **not** a device observation.
The observed revision change is **diagnostic-only**, not a profile or safety
fault. Preserve the literal response and log the transition. A separate error
in identity, transport, setpoints, status or protection **must still fail**.

The later H01_AUX_REVISION controlled OFF-only test (10 samples) confirmed a
**reproducible channel-selection dependency on this physical unit**: both
CH1 and CH2 reported `Aux 1.04`, while CH3 reported `Aux 0.01`; returning
to CH1/CH2 restored `Aux 1.04`. The operator confirmed the panel always
matched SCPI. This does not prove that firmware is actually changing or
that all 2231A units behave alike. No vendor erratum or independent field
report of this exact alternation has been identified. See
`docs/R3_5_11_AUX_CHANNEL_OFF_DIAGNOSTIC.ru.md` for the controlled evidence.

[Keithley/Tektronix official reference manual (PDF)](https://download.tek.com/manual/077100401_Referencemanual.pdf) · [Upstream product documentation](https://www.tek.com/en/tektronix-and-keithley-dc-power-supplies/keithley-2220-2230-2231-series-manual-0)

## Shared session and diagnostic mode

`output_states()` reads all three ON states before or after a profile is applied.
The driver validates identity and enters Remote when necessary. Queries do not
configure setpoints or energize outputs. Ordinary API and diagnostics share one
command dispatcher, Remote/Local tracking, locking and fault handling.

Output Recall and Save Last are front-panel settings; the reference manual lists
no SCPI access to them. The driver cannot enforce Recall Off during instrument
startup. To inspect restored outputs, call `output_states()` before
`apply_profile()`, which deliberately switches all outputs OFF. The gated
[H06 Output Recall procedure](docs/H06_OUTPUT_RECALL.ru.md) tests this using fresh
serial-bound sessions and records startup state before shutdown.

Diagnostics are disabled by default. Use `debug=True` to enable
`diagnostic_query()`, `diagnostic_write()` and optional `debug_trace=callback`.
Trace records redact the identity serial field. Optional `debug_wire_trace=callback`
records actual UART bytes for status commands only; identity bytes are excluded.

Without an override, diagnostic `VOLT`/`CURR` writes use `set_channel()` and its
profile guards. Raw configuration requires the additional explicit flag
`debug_bypass_profile=True`. This flag affects diagnostic writes only: normal
`set_channel()` still enforces software caps. A test can therefore prove software
rejection first, then test the instrument limit through the same dispatcher.

```python
# Existing debug-enabled instance, validated profile and all outputs OFF.
psu.diagnostic_write('INST:NSEL 1')
# A request above the profile cap is rejected before sending SCPI.
psu.debug_bypass_profile = True
try:
    psu.diagnostic_write('VOLT 4.51')  # Only in an approved 4.50 V limit test.
    reply = psu.diagnostic_query('SYST:ERR?')  # Preserve the actual rejection.
finally:
    psu.debug_bypass_profile = False
```

The override intentionally bypasses software profile restrictions; it does not
bypass a failed link, session ownership or a qualification adapter's command scope.
It is never enabled automatically. A wire failure, including an interrupted query,
latches the connection as uncertain; neither API reconnects or retries it.
`debug_adapter` is a qualification policy factory around the same command engine;
sending directly through that adapter is refused outside driver dispatch.

SCPI error handling (R2.3): `check_errors()` consumes **one** `SYST:ERR?`
entry. A valid nonzero error disarms the active profile and, if it was armed,
attempts a verified all-output OFF. It reports `output OFF confirmed` only after
readback; a failed confirmation is reported as `UNKNOWN OUTPUT STATE`.
Malformed error replies may indicate a desynchronized stream: the link is
latched uncertain, with **no further commands or retries** on that connection.
Without an armed profile, error checking does not change output settings.
This behavior is SIM TESTED; real-hardware requalification is pending.

H01–H05 use this mechanism and shared checks in `tests/hardware/common.py`.
Historical hardware reports apply to their recorded source versions; the changed
path requires H01/H02/H03/H04/H04P replay before H05 proceeds.

## R3.2 — Profile integrity monitoring (software-only; SIM TESTED)

`verify_profile()` is a synchronous integrity check. `Profile.verify_every_s`
(default 5 seconds) is **not an autonomous timer**: it controls opportunistic
checks when `read_channel()` is called, and supplies the default interval for
an explicitly started monitor. Without `start_profile_monitor()`, there is no
independent periodic check while the application is idle.

```python
# With a validated, armed profile and operator-approved wiring:
psu.start_profile_monitor()  # opt-in; uses profile.verify_every_s
try:
    # Application operations; these share the SCPI lock with the monitor.
    ...
finally:
    psu.stop_profile_monitor()  # request stop and join; no I/O

print(psu.profile_monitor_running, psu.profile_monitor_error)
# Any monitor-detected fault also latches psu.faulted and disarms the profile.
```

`start_profile_monitor(interval_s=...)` accepts an explicit interval of at least
0.1 seconds and runs an initial `verify_profile()` synchronously before starting
a daemon worker. The worker waits for the interval **after each completed check**;
slow SCPI traffic or other operations can delay it. There is no guaranteed
polling deadline, no automatic reconnect, and no recovery/re-arm after a fault.
All monitor checks use the existing verification, guarded SCPI dispatcher, and
fault/shutdown path. `profile_monitor_error` contains the monitor's failure
message (if any); `profile_monitor_running` reports whether an opt-in worker is
currently active. Stopping is explicit; `emergency_off()` also requests a stop,
and `close()` joins the worker before closing the transport. A failed join raises
instead of claiming the worker stopped. Stop/join does **not** establish that
outputs are OFF. Before applying a new profile, stop/join the old monitor.

On a valid link, a mismatch triggers an attempted output shutdown and readback.
After an uncertain transport reply, **no** further SCPI commands are sent on
that connection, and output state must be regarded as **UNKNOWN**. Software
monitoring is no substitute for independent OVP/OCP, an external interlock, or
physical shutdown when communications fail. Monitor behavior on this revision
has **not** been verified on physical hardware.

## R3.3 — Truthful output state after malformed replies (software-only; SIM TESTED)

`output_states()` returns `{1: bool, 2: bool, 3: bool}` **only when the
relevant state readbacks succeeded**. A boolean `False` means a confirmed OFF
reply; it never stands for an unreadable or unknown channel. A failed read
raises instead of returning a partial map or silently assigning `False`.

A malformed SCPI numeric, boolean, integer register/channel-selection, or
non-text/framing reply, or an unexpected instrument identity, latches the
connection **uncertain**, disarms the profile, and prohibits further
SCPI commands. This also prevents the former attempt to restore a selected channel after an invalid state response. A communication
exception/interrupted exchange uses the same no-retry rule. An ordinary,
well-formed setpoint mismatch is a semantic fault and may still use the one
shared best-effort shutdown/readback procedure while the link is known-good.

**If `UNKNOWN OUTPUT STATE` is reported:** assume the supply may still be
energized. Do **not** repeat `emergency_off()` on the failed connection and do
not assume that `close()` switches outputs off. If safe and available, use the
physical front-panel output control; otherwise isolate the load with an
independent disconnect/interlock. Confirm OFF independently (for example with
an appropriate meter), inspect wiring, and reconnect deliberately with a new
transport/session before re-arming. A software test cannot establish that a
physical load has been isolated. The new parser/fault cases are SIM TESTED;
no R3.3 hardware qualification has been performed.

## R3.4 — Topology and regulation evidence (SIM TESTED)

Before clearing an *inherited* Parallel mode, every profile application route
(Independent/Tracking, Series and Parallel) now queries the instrument's actual
`OUTP:PARALLEL?` state, sends `OUTP 0` when necessary and requires an OFF reply
from `OUTP:STAT:ALL?` **before** `OUTP:PARALLEL 0`. This also applies to a fresh
Python session with no remembered previous profile. If OFF is not confirmed,
configuration stops; no channel setpoints or topology-disable commands follow.
The existing profile-failure handler may attempt best-effort global OFF, but its
write alone is *not* verified shutdown. This is a software command-order guard;
it cannot verify physical jumper wiring or guarantee OFF after communication loss.

The [Keithley reference manual](https://download.tek.com/manual/077100401_Referencemanual.pdf)
(§3, Combine Off, p. 3-8) requires removal of physical interconnections when
leaving a combined setup; the operator's `confirmed_wiring` declaration still
requires a real inspection before any command sequence. The manual's status
model (§5, pp. 5-29–5-30) identifies OPER bit 2 as `CAL`, not a regulation
state. When this bit is set for an enabled channel, the reader preserves raw
registers and returns `regulation='UNKNOWN'` / `regulation_source=None`, even
if CV/CC bits otherwise agree. Conflicting OPER/QUES modes also remain UNKNOWN.

`VOLT:LIM:STAT?` is an instrument register, **not proof of independent
hardware OVP**. Prior H09/H10 evidence reports combined-mode limitations, but
this R3.4 change has no new physical measurements: Series/Parallel voltage
caps remain explicitly *software enforced*, and the previous firmware-specific
observations are not extended to other firmware or untested operating points.
The R3.4 tests are fully offline; target hardware re-qualification remains R3.5.

## CH3 status workaround and limitations

CH3 remains available through the normal profile, `set_channel(3, ...)`,
`output(3, ...)` and `read_channel(3)` APIs. In the full H03 hardware trace from 2026-10-28 (SCPI Main 1.04 /
Aux 1.04), its operation condition reports **8** in both CV and CC: the ON bit
is present but the documented mode bits are absent. Its questionable condition
correctly reports **1 (CV)** or **2 (CC)**. The library accepts an unambiguous mode
from either condition register when the other has no mode bits. No opt-in or
channel-specific fabricated status is needed.

```python
# Existing armed profile; this call does not enable the output.
reading = psu.read_channel(3)
print(reading.enabled, reading.regulation, reading.regulation_source)
print(reading.operation_status_mask, reading.questionable_status_mask)
# Observed ON/CV case: True, 'CV', 'questionable'; raw masks 8, 1.
```

`Reading.regulation_source` is `'operation'`, `'questionable'`, or `'both'` for a
resolved ON mode; it is `None` for OFF, UNKNOWN or skipped regulation checks.
`enabled` and all profile ON/OFF integrity checks use the documented
`CHAN:OUTP?` query for each selected channel; the driver restores the live channel
selection afterward. An H03 shared-path replay showed `CHAN:OUTP?=1` while the
operation condition returned `0` during a CC point, so the operation ON bit is not
used as the sole output-state source. Successful writes and a clear error queue
alone are never treated as proof of output state or regulation.
Both condition queries must succeed; communication failures still trip. Conflicting
or ambiguous modes remain UNKNOWN; a required `expected_regulation='cv'/'cc'`
therefore still trips on mismatch or UNKNOWN. CV/CC is determined by load and V/I
setpoints, not forced by that profile setting. Raw masks remain unchanged.

Limits: CH3 is rated **0–5 V, 0–3 A**, constrained further by the active profile.
Physical evidence covers independent CV at 4 V / 0.500 A with CR 40 ohms and CC at
4 V / 0.250 A with CR 12 ohms, plus individual ON/OFF and setpoint readbacks.
This does not qualify the full rated range, CH3 timers or all later stages.
There is no redundant OPER confirmation of CH3's CV/CC on this firmware.
The source field, direct output-state readback and H03 missing-status handling are
**SIM TESTED** (88/88 offline tests passed); the current implementation still
requires a full physical replay. If both status registers omit CV/CC, the reading
remains `UNKNOWN`; the library does not synthesize a mode from measurements.

The [reference manual](https://download.tek.com/manual/077100401_Referencemanual.pdf)
sections 5-20/21 and the status model describe group event/summary registers, not a
single response containing all three channels' complete live CV/CC states. Use
per-channel `...ISUM1/2/3:COND?` queries; group event reads are not a substitute.
`Acquisition.sample()` already polls all three independent channels sequentially,
with separate timestamps; it is not an atomic snapshot. No new group command is
assumed. See the [physical diagnosis](docs/hardware_reports/2026-10-26T005733.649705Z_H03_qualification.md)
and [verification matrix](docs/VERIFICATION.md). H03's manual-conformance result
remains PARTIAL while manufacturer clarification is pending.

### Guarded tracking voltage changes

After applying a `Profile(wiring='tracking', ...)`, use
`dev.set_tracking_voltage(1, 1.20)` or select channel 2 with the same method.
The ratio comes from the profile's initial CH1/CH2 voltages. Both resulting
setpoints must fit their voltage caps and 0.01 V resolution; current limits stay
fixed. Both channels and unchanged CH3 are read back after each change. A mismatch
trips the driver and attempts all-OFF. Zero is refused because the manual specifies
that it suppresses tracking. Change the ratio by applying a new profile with
outputs OFF. Use `dev.tracking_timers(3.0, 5.0)` to configure both tracking timers
while all outputs are OFF; `None` disables a timer. The manual specifies that the
shorter active duration controls the pair. Individual timer/sequence APIs remain
unavailable for tracked CH1/CH2. `combined_outputs()` uses global ON, including
CH3: its wiring must also be safe. These tracking APIs are **SIM TESTED and HW OBSERVED within H08’s bounded scope**; the external DMM calibration and physical edge timing were not qualified.

### External Series operation

Series uses a dedicated `SeriesProfile`; install the external CH1− to CH2+ jumper
with outputs OFF and confirm it when applying the profile. The pair has one combined
voltage/current setpoint and is read through `read_channel(1)` with label `CH1+CH2`;
`read_channel(2)` is rejected because firmware aliases its selection to CH1 in the
observed mode. `combined_outputs(True, confirm_all_channels_safe=True)` uses global
ON and may also energize CH3, so leave CH3 open or otherwise make it safe. In the
observed firmware, Series `VOLT:LIM:STAT?` is disabled; the profile enforces its
aggregate voltage cap in software and does not claim hardware OVP. H09 powered
Series is HW OBSERVED for the reported CV/CC points; CC regulation readback has
an explicit firmware status-bit caveat in the qualification matrix.

### External Parallel operation

Parallel uses a dedicated `ParallelProfile`; with outputs OFF, connect CH1+ to
CH2+ and CH1− to CH2−, then confirm `confirmed_wiring='parallel'`. The pair is
addressed through `read_channel(1)` as `CH1+CH2`; independent CH2 reads and
setpoint changes are rejected. `max_current` is the aggregate limit (up to 6 A),
and it is written only after Parallel mode is selected while outputs remain OFF.
On the observed firmware, Parallel disables the pair's hardware voltage-limit
enable. `max_voltage` is therefore enforced by the library as a software cap,
not hardware OVP. `combined_outputs(True, confirm_all_channels_safe=True)` is a
global ON and also energizes CH3. H10 OFF, low-current CV/CC, and
high-current CV (~3 A)/CC (4 A) have been HW OBSERVED in separately gated runs;
these do not establish individual channel current sharing or hardware OVP.

H10 CLI dispatch (R3.5.26): without a subgroup flag, `preflight H10` or
`run H10 --dry-run` previews all three stages; `run H10 --execute` runs
OFF diagnostic → low-current CV/CC → high-current CV3A/CC4A sequentially.
Each stage has its own fresh USB/bench, wiring, display, and final-OFF gates;
physical reconfiguration is manual and only at verified OFF. The isolated
`--off-only` command completes successfully with `PARTIAL` *scope* evidence;
`--powered` and `--high-current` select the other isolated stages. The full
command requires all three new stage records, otherwise it stops and never
runs later powered stages. This command includes 4 A and MUST NOT be treated
as an automatic, unattended bench run. No qualification is promoted without
manual review of the generated evidence.

## Installation and offline tests

```bash
git clone https://github.com/<YOUR-ACCOUNT>/keithley-2231a-python.git
cd keithley-2231a-python
python3 -m venv .venv
source .venv/bin/activate                  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
```

`git clone` URL is a placeholder until **you** create the repository. There is currently no claimed PyPI release. Plotting is optional: `python -m pip install -e '.[plot]'`.

### First connection: read-only

```bash
python examples/probe.py --port /dev/ttyUSB1
```

The probe does not enable outputs or set V/I. The actual instrument communication result already obtained by the project owner is `*IDN? -> Keithley instruments, 2231A-30-3, ..., 1.04-1.04` using a TTL-USB adapter. **Nothing else is yet hardware-qualified.**

## Examples

### Guarded operation (output stays OFF during profile setup)

```python
from keithley2231a import Keithley2231A, SerialTransport, Profile

with Keithley2231A(SerialTransport('/dev/ttyUSB1')) as psu:
    psu.apply_profile(Profile.from_json('examples/safe_3ch.json'),
                      confirmed_wiring='independent')
    psu.verify_profile()
    psu.set_channel(1, voltage=1.20, current=0.10)
    # Explicit human check of load/wiring required before psu.output(1, True)
```

**Mandatory physical topology confirmation:** before calling `apply_profile()`
for Independent **or Tracking**, verify that the CH1/CH2 terminals have no
external Series/Parallel jumper wires, and pass `confirmed_wiring='independent'`.
For Series/Parallel use the matching `confirmed_wiring='series'` or `'parallel'`
only **after** manually inspecting the respective external connections with
all PSU outputs OFF. Tokens are operator attestations; software cannot sense
whether the jumper wires are actually present. Wrong or absent confirmation
is rejected before any SCPI I/O. No topology change occurs automatically in
physical wiring.

`apply_profile` **immediately switches ALL outputs OFF** and rewrites configuration. Don't run while powering a device.

### Full rated range; no reduced application limits

```python
profile = Profile.unrestricted()       # CH1/2 ≤30 V/3 A, CH3 ≤5 V/3 A
# psu.apply_profile(profile, confirmed_wiring='independent')           # intentionally OFF-first
# psu.set_channel(1, voltage=12.0, current=1.0)
```

### Operator profile (power only)

```python
from keithley2231a import Profile, ChannelProfile, PowerStep

profile = Profile(
    name='digital-board', access='power_only', wiring='independent',
    lock_front_panel=True,
    channels=(
        ChannelProfile(3.3, .15, initial_voltage=3.3, initial_current=.10),
        ChannelProfile(5.0, .25, initial_voltage=5.0, initial_current=.20),
        ChannelProfile(3.3, .10, initial_voltage=0.0, initial_current=.05),
    ),
    power_sequence=(PowerStep(1, 0.0), PowerStep(2, 0.5)),
)
# psu.apply_profile(profile, confirmed_wiring='independent')  # all outputs OFF
# psu.power_on()              # CH1, wait 0.5 s, CH2
# psu.power_off()             # CH2, wait 0.5 s, CH1
# set_channel(), timer(), arbitrary sequence() are forbidden while armed
```

If the power sequence is empty, `power_on()/power_off()` use CH1→CH2→CH3 / reverse order, and individual `output(channel, enabled)` remains permitted. CH3 is not enabled in the explicit two-step example.

### DC I–V characteristics and external meters

```python
from keithley2231a import voltage_sweep

# After an appropriate guarded profile has been armed; with a safe resistive DUT:
# points = voltage_sweep(psu, 1, [0.0, 0.1, 0.2],
#                        current_limit_a=0.020, settle_s=0.3,
#                        observer=lambda reading: {'dmm_v': external_dmm.read_voltage()},
#                        csv_path='runs/iv.csv')
```

Uncomment only after validating the test circuit and the external meter API. The source is stepped in DC voltage; PSU current and voltage are acquired sequentially. **It is not an SMU or high-speed curve tracer.** `voltage_sweep()` requires other channels OFF and attempts global OFF in `finally`, even after successful completion. For powered bias sources, use a separately designed multi-instrument orchestrator instead. For diodes include a **series resistor** and measure DUT voltage directly with a DMM or estimate `Vdiode ≈ Vpsu − I·R` (lead drops ignored).

### Future multi-instrument automation

Orchestration of multiple independent sources, DMMs and oscilloscopes will live
in a separate repository built on each instrument's own driver. The present
Keithley package intentionally does **not** provide a `BenchCoordinator` or
require vendor-specific external-instrument packages.

### UART vs future Ethernet bridge

```python
from keithley2231a import SerialTransport, TcpTransport
serial_io = SerialTransport('/dev/ttyUSB1', timeout=3.0)
serial_io.close()
# A future MCU with a transparent single-client bridge:
# network_io = TcpTransport('psu-lab.local', 5025, timeout=3.0)
```

The MCU needs correct framing, single-writer ownership, TCP half-open detection and an **independent** failsafe if physical protection is required. Do **not** expose raw SCPI TCP directly to the Internet. Reconnecting does not imply outputs are OFF.

## Documentation

| English | Русский |
| --- | --- |
| [Architecture](ARCHITECTURE.md) | [Архитектура](docs/ARCHITECTURE.ru.md) |
| [Configuration & recipes](docs/RECIPES.md) | [Профили и примеры](docs/RECIPES.ru.md) |
| [Hardware verification matrix](docs/VERIFICATION.md) | [Матрица аппаратной проверки](docs/VERIFICATION.ru.md) |
| [Hardware test procedure](docs/HARDWARE_TESTS.md) | [Протокол испытаний](docs/HARDWARE_TESTS.ru.md) |
| [Contributing](CONTRIBUTING.md) | [Участие в разработке](docs/CONTRIBUTING.ru.md) |
| [Roadmap](ROADMAP.md) | [Roadmap — актуальный единый план](ROADMAP.md) |

## Status and roadmap

- **HW OBSERVED:** serial UART link, `SYST:REMote`, `*IDN?` on firmware 1.04-1.04; repeatable formal verification is still needed.
- **SIM TESTED:** protected profile operations, host sequencing, serial/TCP protocol models, statistics, sweep, multi-instrument coordination (30 simulator tests in current source tree).
- **HW UNVERIFIED:** changing setpoints, outputs, voltage limits, timers, CV/CC status bit mappings, and series/parallel/tracking behaviours on real hardware.
- **PLANNED:** hardware-trigger coordination, MCU network bridge firmware, true concurrently timestamped multi-meter acquisitions, generic vendor driver protocol, more complete safe SCPI coverage, and additional instrument integration tests.

Do not merge hardware verification claims based solely on CI green status. Hardware reports go under [`docs/hardware_reports/`](docs/hardware_reports/README.md).

## Licensing, trademarks and contributions

Original code and documentation are MIT-licensed; see [LICENSE](LICENSE). Third-party dependencies retain their own terms; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Keithley and Tektronix are trademarks of their respective owners. **This is an independent, unofficial library**, not approved, supported or warranted by either manufacturer.

Contributions and hardware test results welcome: [CONTRIBUTING.md](CONTRIBUTING.md).


## Qualification bench (single configuration source)

Hardware qualification is **not** run by the library's normal unit tests. Its one
configuration file defines PSU, load, multimeter and oscilloscope connections.
There are no separate CLI flags for individual serial ports or instrument hosts.

```bash
cp config/bench.example.toml config/bench.local.toml
# Edit and physically verify the connections in config/bench.local.toml
python tools/hw_qualify.py preflight H01 --config config/bench.local.toml
# Only after an operator reviews the card and wiring:
python tools/hw_qualify.py run H01 --config config/bench.local.toml --execute
```

`bench.local.toml` is ignored by Git. The runner prints the effective settings and
records a configuration SHA-256 in evidence. `preflight` never opens instruments.
Connecting, energizing outputs or moving leads always requires the operator gates.
Historical H01–H12 evidence refers to its original implementation revision and
**does not automatically verify this refactored revision**.

See [R1 infrastructure notes](docs/R1_INFRASTRUCTURE.md) and its
[Russian translation](docs/R1_INFRASTRUCTURE.ru.md).

### Shared hardware connection gate (R3.5)

Every interactive hardware qualification run derives its instrument endpoints and USB IDs solely from `config/bench.local.toml`. The shared connection gate prints all participating instrument settings, validates serial VID:PID against OS discovery without opening a port, and asks the operator to confirm the configuration SHA-256 once before the stage-specific wiring/power gate. No manual retyping of serial adapters or endpoints. Network identity is checked by the existing instrument adapters after opening. This does **not** replace operator physical wiring approval or final OFF verification. See `docs/R3_BENCH_CONNECTION_GATE.ru.md`.

### R3.5.2 targeted OFF-only Independent requalification

The hardware qualification runner supports `H02 --profile-only` as a separately
approved, bounded no-load check of the existing protected Independent profile.
It reuses the H02 driver/scopes and skips the full H02 front-panel and debug
limit probes. The limited report is labeled `H02_PROFILE_ONLY` and must not be
used as evidence of full H02 qualification. See
[`docs/R3_5_PROFILE_ONLY_REQUALIFICATION.ru.md`](docs/R3_5_PROFILE_ONLY_REQUALIFICATION.ru.md)
for safety conditions and the non-I/O preflight command.

### R3.5 SCPI sequence and Aux revision audit

The qualification stage adapter retains one `SYST:ERR?` check after each
command/query, but H01 and H02_PROFILE_ONLY no longer issue redundant adjacent
error-queue polls. Malformed diagnostic replies now fail closed without any
further I/O, and observed Main/Aux revision changes are explicitly recorded
in evidence as observations only (never as an identity bypass). See
[`docs/R3_5_SCPI_SEQUENCE_AND_FIRMWARE_AUDIT.ru.md`](docs/R3_5_SCPI_SEQUENCE_AND_FIRMWARE_AUDIT.ru.md).

R3.5.5 added validated `[qualification.h03_single_cvcc]` and a separate CH1-only planning card. The original R3.5.5 stage intentionally blocked live execution. See `docs/R3_5_5_H03_SINGLE_CHANNEL_CONFIG.ru.md`.

R3.5.6 adds an offline-tested CH1-only CV/CC runner core in
`tests/hardware/single_cvcc_stage.py` with strict CH1 output scope, a
config-derived point and simulated success/failure wire tests. **CLI live
execution remains blocked** until a separate reviewed integration. See
`docs/R3_5_6_H03_SINGLE_RUNNER_OFFLINE.ru.md`.

R3.5.7 adds a separate interactive CLI route, `--single-channel-cvcc --execute --allow-single-cvcc-on`, with config-bound connection/wiring gates, two separate CH1-ON approvals and strictly reviewed evidence. The legacy three-channel H03 remains unchanged. Do not energize hardware until the updated patch and offline regression suite have been reviewed. See [`docs/R3_5_7_H03_SINGLE_CLI.ru.md`](docs/R3_5_7_H03_SINGLE_CLI.ru.md).
- [R3.5.8 Powered H03 SCPI & display audit](docs/R3_5_8_POWERED_H03_AUDIT.ru.md) — readable Keithley/KEL U/I/P, bounded live confirmation, separate full-H03 ON gates.

### OFF-only Aux/channel selection diagnostic (R3.5.11)

A separate, physically gated H01 variant can correlate `*IDN?` Main/Aux revision with CH1/CH2/CH3 while all outputs remain OFF. Read-only preflight: `python3 tools/hw_qualify.py preflight H01 --aux-revision-diagnostic --config config/bench.local.toml`. See `docs/R3_5_11_AUX_CHANNEL_OFF_DIAGNOSTIC.ru.md`; no routine auto-execution.

### H04P independent shutdown fault injection (R3.5.13)

See `docs/R3_5_13_H04P_INDEPENDENT_SHUTDOWN.ru.md` for the offline-only H04P exception-path guard: PSU and KEL103 shutdown attempts are independent and never retry an uncertain link. Hardware execution requires a separate operator go-ahead.

### H04P independent power consistency and strict trace audit (R3.5.14)

H04P now validates finite U/I/P and CR values, compares Keithley SCPI power
with Keithley U×I and KEL103 power with KEL103 U×I, and directly compares both
instruments' power readings (0.025 W qualification tolerance; 0.06 V / 0.03 A
remain unchanged). See `docs/R3_5_14_H04P_POWER_CONSISTENCY_AUDIT.ru.md`.
Offline independent verification: `python3 tools/audit_hw_scpi.py
reports/hardware/<timestamp>_H04P/events.jsonl`. Historical H04P evidence
with a 0.31 W acceptance field is reported `PASS_WITH_REVIEW_NOTES`, even
when every measurement passes today's tighter independent check. No automatic
hardware approval or re-energizing follows from an offline PASS.

### R3.5.15 H05 guard refinement

For the fixed H05 1 V power_only run, the runner now checks KEL103 and Keithley live U/I/P, demands positive KEL103 OFF readback, and verifies a real 0-to-1 V excursion rather than accepting small relative-only scope edges. Evidence logging and scope-open failures cannot suppress PSU/load shutdown attempts. The physical common-return/oscilloscope-ground wiring and all setpoints are unchanged. See `docs/H05_PREFLIGHT.ru.md`.


### R3.5.29 — O01 host-timed DMM observer (offline-only)

The experimental `TimedVoltageObserver` / `coordinated_voltage_sweep` reuse the
existing guarded sweep and emergency OFF. They record source-to-DMM host-clock
intervals and validate CSV, but **do not synchronize instrument conversions**
and have no approved hardware-run CLI. [O01 scope and safety (RU)](docs/O01_COORDINATOR_OFFLINE.ru.md).


### R3.5.30 — O01 stage-preparation, NO hardware permission

The bounded `tests/hardware/o01_stage.py` pilot reuses the existing O01 coordinator,
DMM7510 SCPI adapter and shared PSU safety dispatcher. `preflight O01` / `run O01`
without `--execute` now prints the wiring, scope and operator confirmation plan.
**`run O01 --execute` refuses before any USB/LAN enumeration or port open** until
the pilot's independently reviewed offline trace and physical procedure are approved.
See [O01 stage card](docs/O01_STAGE_PREFLIGHT.ru.md). No O01 hardware PASS is claimed.


O01: ограниченный интерактивный аппаратный пилот с независимым DMM7510:
`python3 tools/hw_qualify.py preflight O01 --config config/bench.local.toml`,
после проверки схемы `python3 tools/hw_qualify.py run O01 --config config/bench.local.toml --execute --allow-o01-on`.
Без `--allow-o01-on` порты не открываются. См. `docs/O01_STAGE_PREFLIGHT.ru.md`.

### R4.2-A non-destructive SCPI diagnostics (SIM TESTED; HW REVIEWED PASS: READ_ONLY)

`device.module_info()` reads `SYST:MODU?` as metadata only; it does not
replace `device.identity()` or enforce a firmware/Aux revision.
`device.status_enable_mask(name)` accepts `standard_event`,
`service_request`, `questionable`, or `operation`, and reads the corresponding
**enable mask**, 0–255. Neither method reads or clears an EVENT latch or the
error queue, and neither method enables a power output. The session may first
identify the PSU and enter Remote. The commands are covered by offline tests and independently reviewed on
a real PSU in the limited read-only 2026-10-29 run. See `docs/SCPI_COVERAGE.md`.

### R4.2-B: explicit event-latch consumption (HW REVIEWED PASS: bounded EVENT)

`psu.consume_status_event("standard_event", acknowledge_clear=True)` reads
`*ESR?`. The same method accepts `"questionable"` and `"operation"` for the
corresponding `STAT:...:EVEN?` queries. **These are destructive diagnostics**:
reading may clear accumulated event bits, so the method requires explicit
confirmation every time. The session must be unarmed, without a profile, and
all three channels must be reported OFF immediately before the read. Unlike
`psu.status_enable_mask(...)` or `psu.status()`, this call is **not** suitable
for routine polling. A failed response cannot be retried to recover its events.
The OFF preflight itself is sequential and does not prove an external
controller cannot energize the outputs afterward. HW status: **REVIEWED PASS**
for the one-shot OFF-only 2026-10-29 run:
`*ESR? = 128` (PON bit), `STAT:QUES:EVEN? = 0`,
`STAT:OPER:EVEN? = 0`; 96 clean error-queue polls. No second event read
was made, so *actual latch clearing is not independently measured*.
See [the independent EVENT review](docs/hardware_reports/R4_2_EVENT_review_2026-10-29.md).

### R4.2-C: nested STATUS enable masks (HW REVIEWED PASS: READ_ONLY)

`Keithley2231A.status_instrument_enable_mask("questionable", channel=2)`
reads one documented nested enable mask (0..255). The register can instead
be `"operation"`; omitting the channel reads the instrument-level mask.
It does **not** consume event latches, modify status configuration, select
channels, enable outputs, or change a live profile. The output is diagnostic
only; R4.2-C READ_ONLY paths are **HW REVIEWED PASS (2026-10-29)** on one Keithley;
this does not qualify event-latch consumption. Invalid register/channel
values fail before I/O; malformed/lost replies fault the current session.

### R4.2 physical SCPI verification

The R4.2-A/B/C status API has **bounded HW REVIEWED PASS** from separate
READ_ONLY and EVENT runs on 2026-10-29. This does not prove status-mask
configurability or event-latch clear-after-read behavior. An independent
OFF-only hardware gate, with a separate explicit opt-in for destructive event
reads, is described in
[`docs/R4_2_HARDWARE.ru.md`](docs/R4_2_HARDWARE.ru.md).
Default `python3 tools/r4_status_qualify.py --config config/bench.local.toml` is
**dry run**. It must never be launched with `--execute` unattended.
Next R4.3 work requires [a fresh command-level risk review](docs/R4_3_RISK_REGISTER.md),
not another unapproved live SCPI experiment.

### R4.3-B: query-only `*PSC?` (bounded hardware review PASS)

`psu.power_on_status_clear_state()` reads the 0/1 flag used for power-on
behavior of status enable registers (ESER/SRER). It **does not** reset
the PSU, clear any event register, write `*PSC`, or change outputs.
The 0/1 value is observational; do not interpret it as Output Recall.
First verify offline, then run the separately gated OFF-only physical protocol
`tools/r4_3_psc_qualify.py` as documented in `docs/R4_3_PSC_HARDWARE.ru.md`.
Hardware status: **HW REVIEWED PASS** (2026-10-29) for the one-shot, OFF-only
`*PSC? = 0` read and unchanged zero ESER/SRER masks. See the
[independent review](docs/hardware_reports/R4_3_PSC_review_2026-10-29.md).
This does **not** qualify the `*PSC` write, power-on clearing, power cycling,
nonzero mask retention, SRQ, or any output-state mutation.


### R4.3-C: idle completion `*OPC?` (bounded HW REVIEWED PASS)

`psu.operation_complete()` returns `True` only after one successful `*OPC?=1`
query in a profile-free/unarmed session. **It can block until pending operations
complete**. The hardware-only runner `tools/r4_3_opc_qualify.py` enforces a
serial read timeout <=3s and two physical OFF sweeps. Do not interpret this as
a live profile watchdog, an event-register proof, or permission to issue `*WAI`
or `*OPC` writes; see `docs/R4_3_OPC_HARDWARE.ru.md`.
Hardware **HW REVIEWED PASS**, limited to one idle `*OPC?=1` response
observed in 0.207 s on 2026-10-29. No pending-work, status-latch,
execution-time bound or side-effect preservation claim. See the
[independent review](docs/hardware_reports/R4_3_OPC_review_2026-10-29.md).
