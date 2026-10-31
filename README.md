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

**R3.5.54 armed-profile SCPI fault/shutdown UART tests:** historical
in-memory error/shutdown tests are migrated to `Keithley2231A` over strict
PTY. The tests first call real `apply_profile()` (physically simulated but
sent through the full driver) and `output(1, True)` using minimal allowlisted
register readbacks. Faults at `SYST:ERR?` verify the actual `OUTP 0` and
per-channel confirmation/fallback behavior. A lost OFF readback and malformed
error both forbid further commands on the uncertain link. This is **software
fault injection only**, not proof a physical PSU will obey OFF.

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
The example-only `Acquisition.sample()` already polls all three independent channels sequentially,
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

`voltage_sweep`, `coordinated_voltage_sweep`, and `Acquisition` are
**application examples under `examples/`**, not exported by the installed
`keithley2231a` library. Import them from a repository checkout (not a wheel).
The library exposes typed device capabilities and guarded SCPI primitives;
the future upper layer owns scheduling, cross-instrument routing and CSV.


```python
from examples.iv_sweep import voltage_sweep

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

## R3.5.55 — несколько offline-этапов в одной поставке

Публичные `*PSC?` и `*OPC?`, а также контроль целостности активного
профиля проверяются **через основной драйвер и виртуальный UART**. Удалены
устаревшие дублирующие тесты с `FakeTransport`; тесты границ аппаратных
runner сохранены, поскольку выполняют отдельную защитную функцию.
Проверяются CRLF, отсутствие повторов, реакция на плохие ответы/тайм-ауты,
дрейф четырёх параметров профиля и запрет аварийного OFF после потери связи.
Это программные проверки, **не новая аппаратная квалификация**.

## R3.5.56 — four bundled virtual-UART test migrations

Fifteen older `FakeTransport` tests are replaced by 15 tests sending actual
`Keithley2231A` commands over POSIX virtual UART: output-state inspection,
physical identity/serial and observational Aux revision, diagnostic dispatch,
and malformed numeric/status replies. Only test code and documentation change;
no hardware access, new driver execution layers or service permissions.
See [SCPI coverage](docs/SCPI_COVERAGE.md#r3556--four-production-driver-uart-migrations-software-only).

## R3.5.57 — safety/topology gates tested over virtual UART

Seven historical `FakeTransport` test methods were replaced by eight
production-driver/PTY tests: explicit wiring confirmation before any SCPI,
all-topology cleanup on an injected profile-integrity fault, selected-channel
alias detection, inherited timer disable, fixed and changed setpoint integrity,
and unauthorized output activation. The fixture allows combined-mode **writes
only in the injected-fault scenario**, without pretending to simulate an actual
Series/Parallel electrical connection. Historical physical H09/H10 results are
unchanged. These tests never connect to or energize real equipment.

## R3.5.58 — independent sequencing and Tracking control through virtual UART

Four remaining `FakeTransport` test methods were replaced by six real-driver
PTY tests for independent channel sequencing and timer readback, guarded
Tracking global ON/OFF (including CH3), paired timer validation and OFF-only
configuration, delayed global OFF readback, a valid-but-wrong timer reply,
and an uncertain UART timeout that prohibits further OFF commands. The UART
fixture only reflects explicitly written *digital* SCPI registers and scripted
responses; it does not model linked-voltage regulation, actual timer expiry,
physical connections or real power. Hardware H05/H06/H08/H09/H10 evidence
and hardware authorization remain unchanged. No production SCPI or safety
logic was modified.

## R3.5.59 — scripted Tracking readback software qualification

Tracking voltage changes now have three POSIX-PTY regression tests using the
production `Keithley2231A` driver. They check software ratio calculation,
pre-command safety gates, separate CH1/CH2 register queries, and a confirmed
OFF response after a mismatched linked readback. The test peer supplies
**explicit per-channel `VOLT?` values**, never computes a coupled channel's
voltage from the other's write. Electrical Tracking 1:1/1:2 remains qualified
only to the extent established by historical **hardware H08** measurements;
this patch neither changes the driver nor makes any new hardware claim.

## R3.5.60 — bounded Series/Parallel aggregate SCPI regressions (offline)

Three historical FakeTransport tests move to production-driver POSIX UART:
Series aggregate-limit mismatch trips before arming, Parallel 4 A current
setpoint is programmed only after the mode command, and CH3 limit drift
trips in both modes. A negative fixture-validation test is also included.
The peer chooses **literal scripted digital readbacks** when a topology mode
is selected; it never computes branch current, coupled voltage, or aggregate
limit from independent settings. No electrical proof, physical wiring check,
firmware behavior simulation, new SCPI permission, or H09/H10 status change.

### R3.5.61: larger offline UART regression batch

The combined-mode, output-safety, and measurement/regulation driver tests
were migrated as one software-only patch: 22 older `FakeTransport` tests
replaced by 23 narrower checks using the *production* driver on POSIX PTY.
The canned SCPI replies verify software wire sequencing and fail-stop logic;
these tests cannot validate physical Series/Parallel wiring, power delivery,
output shutdown, or electrical CV/CC operation. Original hardware evidence
and its limits are not reclassified. `tests/test_driver.py` keeps unrelated
legacy mocks until their separate cases have appropriate real-driver coverage.

## R3.5.62 — remaining driver guards, isolated qualification fixture (offline)

A batch of 11 production-driver/PTY checks replaces the remaining direct
`FakeTransport` protocols in `tests/test_driver.py` (including acquisition
clock boundaries). Explicit diagnostic bypass, serial binding, wrong channel
types, non-string transport replies, `KeyboardInterrupt`, lost versus coherent
readbacks, and verified versus uncertain shutdown are tested over the real
`Keithley2231A` dispatcher. Invalid non-string/interrupt transport behavior
uses boundary injection **after** the real wire query rather than a second SCPI
interpreter. No changes to the production package, electrical qualifications,
or permissions for live tests.

The **unchanged** pre-existing stateful `FakeTransport` is now owned by
`tests/support/qualification_transport.py` rather than imported from
`tests/test_driver.py`. It remains an offline fixture for qualification-runner
logic, topology/status unit scenarios, and evidence tests; its state changes
are *not* physical-device verification. All historical consumers import this
single fixture directly, with no compatibility forwarding shim.

## R3.5.63 — transport-close regression fix and topology/monitor UART consolidation

The **user's pyserial-equipped R3.5.62 run failed** in
`test_close_verifies_off_then_closes_connection`: the test asserted
`wire.closed`, a property of the fallback `PTYTransport`, not the production
`SerialTransport`. The test now checks the common **public behavior** instead:
`close()` prevents subsequent transport queries without sending another SCPI
command. The driver/transport APIs are unchanged. In the previous environment
without pyserial, this difference was hidden; both branches must be exercised
in CI or on the pyserial-equipped bench machine, without enabling real outputs.

Six topology/status scenarios and the remaining profile-monitor lifecycle
checks have been consolidated into production `Keithley2231A` / virtual-UART
regressions. Removed the duplicate stateful `FakeTransport` definition from
`tests/test_driver.py`: the only remaining qualification-stage fixture is in
`tests/support/qualification_transport.py`, which is not a physical hardware
model. The virtual peer returns fixed digital SCPI readbacks, does not simulate
combined electrical behavior, and cannot supersede H09/H10 evidence. No
changes to production code, physical topology or live-test authorization.

## R3.5.64 — power-only sequencing and guarded sweep UART regression (offline)

The remaining application-level `tests/test_profiles_sweep_bench.py` transport
mock has been retired. Its profile-data validation cases stay pure unit tests;
its driver-facing cases now run against the **production `Keithley2231A`** using
the existing virtual UART in `tests/offline/test_profiles_sweep_uart.py`.
Coverage includes rated `unrestricted` bounds, `power_only` access gates,
two- and three-channel forward/reverse power sequencing and host delays,
coherent versus lost output readback, voltage-sweep CSV/observer data, invalid
sweep preflight and safe OFF on completion or observer failure. No independent
SCPI emulator, new production safety path or hardware-qualification claim.

Remaining `FakeTransport` uses in qualification-runner/stage tests are retained
intentionally: they exercise runner and evidence mechanisms, not physical
Keithley operation. The virtual UART only returns digital SCPI responses;
it cannot establish actual powered Series/Parallel or H09/H10 results.


## R4.4-A — direct scalar and display queries (SIM TESTED; HW NOT_RUN)

New read-only API methods (single instrument, no unreviewed mutations):

```python
# After normal Keithley2231A connection and validation:
print(psu.display_enabled())         # DISP? (front-panel display, not power state)
psu.apply_profile(profile, confirmed_wiring="independent")
watts = psu.measure_power(1)          # MEAS:POW? (new native power measurement)
volts = psu.fetch_measurement(1, "voltage")  # FETC:VOLT? (existing/current reading)
amps = psu.fetch_measurement(1, "current")   # FETC:CURR?
watts_cached = psu.fetch_measurement(1, "power")  # FETC:POW?
```

For clarity, `read_channel().power_w` **still computes the product of two
sequentially measured U and I**; do not interpret it as an atomic instrument
power measurement. `FETC` freshness is not established, and requests on
unarmed/faulted sessions or with unknown scalar kinds are rejected. Each scalar
request rechecks the active profile (including topology and output integrity)
before sending its single measurement command, so the extra API cannot bypass
prior protection. Errors or loss of synchronization remain fail-closed. No
hardware verification of these five query forms is claimed; their behavior
must be qualified on the actual connected instrument before relying on it in
an application or replacing established measurement workflows.

No additional privileged service route or independent SCPI implementation is
introduced. See `docs/SCPI_COVERAGE.md` for the per-family evidence state.

## R4.4-B — manual display-only OFF qualification (hardware NOT_RUN)

After R4.4-A software regression, the next separate stage validates only
`display_enabled()` / `DISP?` on a real PSU, with physically empty terminals
and all outputs OFF. It cannot energize the PSU and never sends `OUTP 0`.

```bash
python3 tools/r4_4_display_qualify.py --config config/bench.local.toml
# After reviewing DRY RUN, physical wiring and operator confirmations:
python3 tools/r4_4_display_qualify.py --config config/bench.local.toml --execute
```

The `--execute` form is manual, gated, and not part of CI. Its result must be
reviewed together with the JSONL evidence. It does **not** verify native
`MEAS:POW?` or `FETC:*`: their own low-power, profile-bound test is separate.
See `docs/R4_4_HARDWARE_PLAN.ru.md` for permissions and abort behavior.


### R4.4-B hardware evidence review (2026-10-29)

One OFF-only physical `DISP?=1` observation on firmware `1.04-1.04` is
**HW REVIEWED PASS** after inspection of the full JSONL/SCPI trace, including
both CH1–CH3 OFF sweeps and 33 zero error-queue responses. This says nothing
about real `MEAS:POW?` or `FETC:*` operation, which remains **HW NOT_RUN**.
See `docs/reviews/R4_4_B_DISPLAY_HW_REVIEW_2026-10-29.md` and run the
offline-only `tools/audit_r4_4_display_evidence.py` against the original
unchanged evidence file for reproducibility.

### R4.4-C — scalar measurement hardware preparation (HW NOT_RUN)

The restricted `tools/r4_4_scalar_qualify.py` is DRY RUN by default.
Its powered mode is separately gated and must not be executed until the new
hardware plan and software patch are reviewed. It performs one fixed CH1
low-power measurement with KEL103 CR40Ω and external DMM7510 voltage,
using the production driver, then requires independent OFF readbacks and
operator confirmation. The exact four-field `[qualification.r44_scalar]`
block must be added to local bench TOML; sample values and strict ceilings
are in `docs/R4_4_HARDWARE_PLAN.ru.md`. The FETC results are observational:
freshness and simultaneity are explicitly **unverified**. The runner's
`PASS` does not itself mark these commands hardware qualified.


### R4.5-A — selected channel and step preset inspection (software only)

`Keithley2231A.selected_channel_name()` reads `INST?` (CH1/CH2/CH3) without
applying a profile or selecting a different channel. `source_step(ch, kind)`
reads `VOLT:STEP?` or `CURR:STEP?` under an armed Independent profile, checking
profile integrity before selecting its channel. Both operations are **query
only**, with strict reply parsing; invalid replies/transport faults block
further commands on the uncertain session. `STEP` writes, `VOLT/CURR:UP/DOWN`,
triggered setpoints and triggers remain blocked. The virtual UART supplies
canned digital responses only. All three queries are **SIM TESTED / HW NOT_RUN**;
`VOLT:STEP?` firmware response is particularly unconfirmed because its vendor
reference documents query syntax but lists `Returns None`. See
`docs/R4_5_QUERY_ONLY_PLAN.ru.md` before planning any operator-approved trial.

### R4.5-B: OFF-only qualification preparation

For the guarded, two-phase source observation runner and explicit manual gates,
see `docs/R4_5_B_OFF_HARDWARE_PLAN.ru.md`. `tools/r4_5_source_qualify.py`
is dry-run by default; no hardware is authorized by software tests.

### R4.5-B hardware-readback status

Two independently reviewed OFF-only JSONL runs on firmware `1.04-1.04`
confirm `INST?=CH1` and, after a second operator gate and programmed OFF
presets, `VOLT:STEP?=0.001 V` / `CURR:STEP?=0.001 A` on CH1. This proves
**readback on one OFF configuration only**, not STEP writes or powered behavior.
Both originals are re-auditable offline with
`tools/audit_r4_5_source_evidence.py`; SHA-256 and limits are in
`docs/reviews/R4_5_B_INST_STEP_HW_REVIEW_2026-10-29.md`.

### R4.5-C — controlled source STEP API, OFF-only until hardware qualification

`Keithley2231A.configure_source_step(channel, 'voltage'|'current', increment)`
configures one bounded 10mV voltage / 1mA current digital step and verifies it by SCPI
readback. `Keithley2231A.step_source(channel, 'voltage'|'current', 'up'|'down')`
changes a digital preset by a previously validated step. These are guarded
production-driver operations, **not arbitrary diagnostic writes**. They require
an armed Independent control profile, *all outputs OFF*, no unexpected profile
change, target within channel caps, and exact digital readback. Every write
polls one SCPI error (consuming the queue). A session that configures STEP
cannot subsequently turn the outputs ON. Apply a clean new profile for ordinary
power operations; do not treat this as a substitute for physical precautions.

**SIM TESTED; two bounded OFF-only digital STEP cases HW REVIEWED PASS; NOT EXPERIMENT READY.** A virtual
UART validates command ordering, digital register bookkeeping and fail-stop,
not whether the physical PSU changes voltage/current by 1 mV/1 mA. Before
using STEP for I–V curves, CV/CC control or experiments, complete the bounded
operator-approved R4.5-D (OFF) and R4.5-E (live CV/CC with external metrology)
procedures and independently review their JSONL. Functional qualification
matrix: `docs/R4_5_FUNCTIONAL_QUALIFICATION.ru.md`.

### R4.5-D — OFF-only STEP mutation bench qualification (in progress)

`tools/r4_5_step_mutation_qualify.py` is **DRY RUN** by default. One exact
channel/kind/increment per operator-approved hardware run, separate BENCH,
INITIAL, PROFILE, MUTATION, FINAL gates, no output-enable commands, no load.
The production STEP API performs one step-register write followed by exactly
UP/UP/DOWN/DOWN, digital readbacks and OFF checks, returning to the initial
preset. A distinct profile sets CH1 3.30V/.100A and CH2/CH3 1.00V/.050A,
leaving safe room for both UP actions. `VOLT?` must confirm the approved 10mV-or-larger changes;
firmware rounding or unsupported commands **ABORT**, never widen tolerances.
See `docs/R4_5_D_STEP_MUTATION_OFF_PLAN.ru.md` for the revised 18-case matrix and
operator checks. **Two OFF-only digital cases reviewed, 16 outstanding; not EXPERIMENT READY.**

### R4.5-D voltage STEP refusal (2026-10-30)

CH1 `VOLT:STEP 0.010` digital OFF-only HW REVIEWED PASS;
`VOLT:STEP 0.001` returned SCPI 120 (Parameter overflowed), aborted safely.
Voltage STEP writes now require 10 mV grid (datasheet setting resolution),
current STEP writes 1 mA grid; no powered STEP or CV/CC readiness is claimed.
See `docs/reviews/R4_5_D_CH1_VOLT_1MV_REJECTION_2026-10-30.md`.

### R4.5-D CH1 current 1mA OFF-only (2026-10-30)

CH1 `CURR:STEP 0.001` was accepted by real firmware 1.04-1.04. Two
`CURR:UP` and two `CURR:DOWN` commands gave digital `CURR?` readbacks
0.100→0.101→0.102→0.101→0.100 A. Original JSONL was independently
audited: 1002 query/reply pairs, 216 writes, 611 zero SCPI error polls,
78 OFF readbacks, operator physical OFF confirmed. Bounded **HW REVIEWED
PASS for this CH1 digital OFF-only case**, not a measured 1mA analog change.
The approved 18-case OFF-only matrix has **2/18** reviewed (the other is
CH1 voltage 10mV). Next: CH1 current 5mA; loaded CV/CC still NOT_RUN.
See `docs/reviews/R4_5_D_CH1_CURR_1MA_HW_REVIEW_2026-10-30.md`.

R4.5-D CH1/5mA OFF-only цифровая квалификация: независимый аппаратный review
`docs/reviews/R4_5_D_CH1_CURR_5MA_HW_REVIEW_2026-10-30.md`.
Аудитор строго сверяет оба наблюдавшихся `*IDN?` (AUX `1.04-1.04` / `1.04-0.01`)
с остальной трассой. Это не физическая проверка тока и не разрешение на ВАХ.

### Batched OFF-only STEP qualification

`tools/r4_5_step_batch_qualify.py` offers one reviewed digital matrix per explicitly approved session (`--matrix current`, nine CH1–CH3 current STEP cases). Default is **dry run**; `--execute` requires physical empty outputs, interactive operator gates and live hardware. `tools/audit_r4_5_step_batch_evidence.py` independently audits the original JSONL. This is *not* powered step verification and does not justify output ON after STEP; see `docs/R4_5_D_BATCH_EXIT_AND_POWERED_GATE.ru.md`.

### R4.5-D current STEP batch — hardware result

The real current OFF-only batch CH1–CH3 × 1/5/10 mA has been independently reviewed as **9/9 digital HW REVIEWED PASS**, with 4,969 zero SCPI error polls and 639 OFF readbacks. See `docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md`. Real powered current step accuracy **NOT_RUN**. The next milestone is the explicitly gated KEL103+DMM7510 powered CV/CC/STEP experiment; see `docs/R4_5_E_POWERED_STEP_ENTRY.ru.md`.

**R4.5-E-A (software-only, 2026-10-30):** the guarded qualification-only
`powered_step_qualification` context enables one bounded CH1 CV/voltage or
CC/current STEP under an explicitly approved external-load stage. It is **not**
a released/general powered STEP API, and no physical loaded STEP has yet been
qualified. Ordinary STEP methods remain OFF-only outside this context.
See `docs/R4_5_E_POWERED_STEP_ENTRY.ru.md`; do not run the context directly
on physical equipment before an independently reviewed powered runner exists.

R4.5-E-B adds an **operator-gated, dry-run-by-default** low-power STEP
qualification runner (`tools/r4_5_powered_step_qualify.py`). It uses existing
KEL103/DMM7510 test adapters and the production one-shot safety window.
It is not a general powered STEP permission or a completed hardware test.
See `docs/R4_5_E_POWERED_STEP_ENTRY.ru.md` before connecting instruments.

R4.5-E-B: first real low-power CH1 CV powered STEP has **HW REVIEWED PASS**
for one steady-state +10mV point with KEL103 and independent DMM7510
(DMM delta +10.626mV); see
`docs/reviews/R4_5_E_B_CH1_CV_10MV_POWERED_HW_REVIEW_2026-10-30.md`.
Powered CC +5mA remains HW NOT_RUN; do not interpret this as transient
accuracy or full IV-curve qualification.

### R4.5-E-C — independently reviewed five-point powered CV series (2026-10-30)

The CH1 CV five-point `UP/UP/DOWN/DOWN` series with KEL103 CR 40 Ω and
DMM7510 FRONT DCV is **HW REVIEWED PASS** for steady-state measurements only.
The original JSONL, immutable SHA-256, measured reversibility and restrictions
are documented in
[`docs/reviews/R4_5_E_C_CH1_CV_FIVE_POINT_HW_REVIEW_2026-10-30.md`](docs/reviews/R4_5_E_C_CH1_CV_FIVE_POINT_HW_REVIEW_2026-10-30.md).
The earlier R4.5-E-B one-point CC test was also separately reviewed PASS;
any older `CC NOT_RUN` statement above describes its historical review date.
The **five-point CC series remains NOT_RUN**. No transient-response,
calibration or unrestricted I–V automation claims follow from this CV review.

## Static capabilities (R5-A)

Instrument-native and driver-exposed features are separate axes. Read-only
metadata needs no connected instrument and does not authorize switching an output:

```python
from keithley2231a import Keithley2231A
caps = Keithley2231A.capabilities()
print(caps.channel(3).rated_voltage_v)  # 5.0
print(caps.feature("native_iv_sweep").instrument_native)  # False
print(caps.to_dict()["readback"]["readings_atomic"])  # False
```

See `docs/R5_A_STATIC_DEVICE_CAPABILITIES.ru.md` for schema, accuracy
conditions and bounded qualification caveats.

### R5-C: scoped static capability evidence

`Keithley2231A.capabilities()` is offline model metadata (`schema_version=2`).
It distinguishes reviewed STEP register queries, OFF-only digital steps,
and narrowly qualified CH1 CV/CC powered steps. No capability metadata grants
permission to control outputs or implies firmware-independent behavior.
See `docs/R5_C_STEP_SCOPED_CAPABILITIES.ru.md`.

### R5-D: channel-neutral STEP qualification candidate

`powered_step_qualification(ch, ...)` now uses the selected channel for
all three independent outputs, retaining the low-power envelope, one-channel
ON invariant, exact STEP checks, emergency OFF and no-retry behavior. The
ordinary `step_source()` method remains OFF-only outside this bounded window.
**This is software/virtual-UART validation, NOT CH2/CH3 powered hardware
qualification or general-purpose powered STEP permission.** Actual CH2/CH3
CV/CC with KEL103 and DMM7510 must be independently reviewed per-channel,
in separate operator-approved sessions. See
`docs/R5_D_ALL_CHANNEL_STEP_QUALIFICATION_PLAN.ru.md`.

**R5-D2 STEP all-channel powered qualification candidate (software-only):**
`tools/r5_d2_step_powered_qualify.py --channel 2 --mode cv` is a dry-run by
 default. One future, separately operator-approved powered test may exercise
one CH1/CH2/CH3 channel in CV or CC with external KEL103/DMM7510 readings;
exactly four native UP/UP/DOWN/DOWN moves, independent verified OFF and no
retries. `tools/audit_r5_d2_step_powered.py` reviews original evidence offline.
**CH2/CH3 powered STEP remains HW NOT_RUN** until independent hardware review.
See `docs/R5_D2_POWERED_STEP_CH123_HARDWARE_CANDIDATE.ru.md`.


**R5-D3 evidence update (2026-10-31):** native voltage and current STEP have
bounded low-power CV/CC hardware observations on **all three channels**,
including independent KEL103/DMM7510 measurements and verified final OFF.
This does not authorize arbitrary powered stepping or prove calibrated current,
full-range/transient behavior or trigger synchronization. See
`docs/reviews/R5_D3_ALL_CHANNEL_STEP_HW_REVIEW_2026-10-31.md`.

### R5-E1: read-only native trigger preset registers

`device.read_trigger_presets(ch)` reads `VOLT:TRIG?` and `CURR:TRIG?` for
CH1/CH2/CH3 in an independent control profile with all outputs OFF. The
returned immutable `TriggerPresetReadback` is a pair of **sequential digital
registers** (not active outputs or simultaneous measurements). The path
currently has **virtual-UART-only** evidence. `*TRG`, writing trigger
setpoints and `INST:COUP` remain blocked; no hardware synchronization or
active trigger behavior is qualified. See
`docs/R5_E1_TRIGGER_PRESET_READBACK.ru.md`.

### Native trigger registers (R5-E2/R5-E3)

`read_trigger_presets(ch)` now has bounded OFF-only real-hardware readback
evidence for CH1/CH2/CH3. `stage_trigger_presets_off(ch, voltage_v, current_a,
confirmed_off_only=True)` is a software-only guarded trigger-register write
probe; it locks output-enable in the current session. A new session does not
prove physical trigger registers were reset. `*TRG` and `INST:COUP` remain
blocked; no simultaneous switching or loaded trigger behavior is claimed.
See `docs/R5_E3_TRIGGER_OFF_STAGING.ru.md`.

### R5-E4 — OFF-only trigger-write qualification candidate

`tools/r5_e4_trigger_write_qualify.py --channel {1,2,3}` is dry-run by
 default. Once separately approved for hardware, each interactive case writes
bounded `VOLT:TRIG` and `CURR:TRIG`, reads back, restores original trigger
registers with readbacks and leaves all outputs OFF. It never sends `*TRG`,
`INST:COUP` or output ON. Abort may leave partial native presets: inspect
manually, never auto-retry an uncertain UART. The independent JSONL auditor is
`tools/audit_r5_e4_trigger_write.py`. **R5-E4 hardware NOT_RUN**, not powered
trigger qualification. See `docs/R5_E4_TRIGGER_OFF_WRITE_RESTORE_QUALIFICATION.ru.md`.

### R5-E5 — Reviewed OFF-only trigger register staging

Original R5-E4 hardware JSONL independently verifies CH1/CH2/CH3
`VOLT:TRIG`/`CURR:TRIG` write/read/restore while all outputs OFF. See
`docs/reviews/R5_E4_TRIGGER_WRITE_HW_REVIEW_2026-10-31.md` and
`tools/audit_r5_e5_trigger_write_matrix.py` for pinned SHA-256 cross-run
verification. This does not qualify `*TRG`, `INST:COUP`, powered switching,
or synchronization. The *offline-only* `tools/plan_r5_e5_trigger_event.py`
models all three channels as potentially affected by a bus trigger and
performs no device I/O.
