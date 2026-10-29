"""SCPI driver. Every operation goes through an active immutable safety profile.

NOTE: The physical Series/Parallel interconnection is OUTSIDE the PSU.
The API does not guarantee a fail-safe output after a cable/PC/network failure.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import re
import threading
import time
from typing import Callable, Iterable
from .profile import Profile, SeriesProfile, ParallelProfile, ChannelProfile, LimitsError, ProfileError
from .transport import Transport, TransportError

class InstrumentError(RuntimeError):
    pass
class SafetyError(InstrumentError):
    pass


class _CommandChannel:
    """Transport seam for diagnostic policy/recording adapters, owned by one driver."""
    def __init__(self, owner): self._owner = owner
    def write(self, command): self._owner._execute(command, query=False)
    def query(self, command): return self._owner._execute(command, query=True)
    def close(self): self._owner._raw.close()

@dataclass(frozen=True)
class Reading:
    """Sequential channel readback, not an atomic instrument snapshot.

    On the tested 2231A, CH3 OPER may omit CV/CC bits (H03 evidence).
    An unambiguous QUES condition mode remains usable when OPER has no mode.
    ``regulation_source`` identifies the supporting register(s): operation,
    questionable, both, or None for OFF, UNKNOWN or skipped regulation checks.
    Raw masks are retained; missing bits are never synthesized. Conflicting or
    ambiguous mode bits produce UNKNOWN. No-error replies cannot establish mode.
    See README.md, "CH3 status workaround and limitations" for hardware scope.
    """
    channel: int
    timestamp_utc: str
    monotonic_s: float
    voltage_v: float
    current_a: float
    power_w: float
    regulation: str    # CV, CC, OFF, UNKNOWN
    enabled: bool
    label: str  # CH1, CH2, CH3, or CH1+CH2 for series/parallel
    voltage_set_v: float | None = None
    current_limit_set_a: float | None = None
    operation_status_mask: int | None = None
    questionable_status_mask: int | None = None
    regulation_source: str | None = None

class Keithley2231A:
    _OUTPUT_OFF_TIMEOUT_S = 1.0
    _OUTPUT_POLL_INTERVAL_S = 0.05

    def __init__(self, transport: Transport, *, expected_serial: str | None = None,
                 debug: bool = False, debug_bypass_profile: bool = False,
                 debug_trace: Callable[[dict], None] | None = None,
                 debug_wire_trace: Callable[[dict], None] | None = None,
                 debug_adapter: Callable[[Transport], Transport] | None = None):
        if type(debug) is not bool or type(debug_bypass_profile) is not bool:
            raise TypeError('debug flags must be bool')
        if not debug and (debug_bypass_profile or debug_adapter is not None or
                          debug_trace is not None or debug_wire_trace is not None):
            raise ValueError('diagnostic options require debug=True')
        for option in (debug_trace, debug_wire_trace, debug_adapter):
            if option is not None and not callable(option):
                raise TypeError('diagnostic callbacks/adapters must be callable')
        status_observer = getattr(transport, 'set_debug_status_trace', None)
        if debug_wire_trace is not None and status_observer is None:
            raise ValueError('Transport does not support status-byte tracing')
        if status_observer is not None: status_observer(debug_wire_trace)
        if expected_serial is not None:
            if not isinstance(expected_serial, str):
                raise TypeError('expected_serial must be a string or None')
            expected_serial = expected_serial.strip()
            if not expected_serial:
                raise ValueError('expected_serial must not be empty')
        self._raw = transport
        self._debug = debug
        self._debug_bypass_profile = debug_bypass_profile
        self._debug_trace = debug_trace
        self._dispatch_depth = 0
        self._remote_mode = False
        self._link_uncertain = False
        self._selected_channel = None
        self._expected_serial = expected_serial
        self._lock = threading.RLock()
        # Separate lifecycle lock: stop() must not wait on a stalled SCPI lock.
        self._monitor_control_lock = threading.Lock()
        self._profile: Profile | SeriesProfile | ParallelProfile | None = None
        self._armed = False
        self._faulted = False
        self._last_verify = 0.0
        self._profile_monitor_thread: threading.Thread | None = None
        self._profile_monitor_stop: threading.Event | None = None
        self._profile_monitor_error: str | None = None
        self._idn = None
        self._instrument_key = None
        self._sequencing = False
        self._expected_setpoints = {}
        self._expected_outputs = {ch: False for ch in (1, 2, 3)}
        self._parallel_limit_enabled: bool | None = None
        channel = _CommandChannel(self)
        self._io = debug_adapter(channel) if debug_adapter is not None else channel

    def _latch_fault(self, *, link_uncertain: bool = False) -> None:
        """Latch faults consistently; an uncertain link requires a NEW transport.

        An uncertain wire exchange forbids *all* further commands on this
        connection, including an unverified automatic output-off attempt.
        """
        self._faulted, self._armed = True, False
        if self._profile_monitor_stop is not None:
            self._profile_monitor_stop.set()
        if link_uncertain:
            self._link_uncertain = True

    @property
    def debug_transport(self) -> Transport:
        """Expose diagnostic adapter policy state; sending outside dispatch is refused."""
        if not self._debug: raise SafetyError('Diagnostic mode is disabled')
        return self._io

    @property
    def debug_bypass_profile(self) -> bool:
        return self._debug_bypass_profile

    @debug_bypass_profile.setter
    def debug_bypass_profile(self, enabled: bool):
        if type(enabled) is not bool: raise TypeError('debug_bypass_profile must be bool')
        with self._lock:
            if not self._debug: raise SafetyError('Diagnostic mode is disabled')
            self._debug_bypass_profile = enabled

    @staticmethod
    def _command(command: str, *, query: bool):
        if (not isinstance(command, str) or not command or
                any(c in command for c in ('\r', '\n', ';')) or
                not command.isascii() or command.endswith('?') != query):
            raise ValueError('one ASCII SCPI query or write per call')

    def _trace(self, event: str, command: str, **fields):
        if not self._debug: return
        if command == '*IDN?' and 'response' in fields:
            parts = fields['response'].split(',')
            if len(parts) == 4:
                parts[2] = ' [REDACTED]'
                fields['response'] = ','.join(parts)
            else:
                fields['response'] = '[REDACTED INVALID IDENTITY]'
        record = dict(event=event, command=command, monotonic_s=time.monotonic(),
                      profile_bypass=self._debug_bypass_profile, **fields)
        logging.getLogger(__name__).debug('SCPI %s', record)
        if self._debug_trace is not None: self._debug_trace(record)

    def _ensure_remote(self):
        if self._idn is None:
            if self._faulted:
                raise SafetyError('Resolve identity fault before further operations')
            self.identity()
        if not self._remote_mode:
            if self._faulted:
                raise SafetyError('Faulted session cannot automatically re-enter Remote')
            self.remote(lock_panel=False)

    @staticmethod
    def _session_command(command: str) -> str:
        """Recognize documented long/short session commands without rewriting traces."""
        upper = command.upper()
        match = re.fullmatch(r'SYST(?:EM)?:(REM(?:OTE)?|LOC(?:AL)?|RWL(?:OCK)?|ERR(?:OR)?\?)', upper)
        if match:
            keyword = match[1]
            return 'SYST:' + ('RWLOCK' if keyword.startswith('RWL') else
                              'ERR?' if keyword.startswith('ERR') else keyword[:3])
        match = re.fullmatch(r'INST(?:RUMENT)?:NSEL(?:ECT)? ([123])', upper)
        if match: return f'INST:NSEL {match[1]}'
        return upper

    @staticmethod
    def _service_only_command(command: str) -> bool:
        """Never let normal or debug-bypass dispatch reach service SCPI.

        This is a logical API boundary, not a security sandbox against code
        accessing the underlying physical transport directly. Service preview
        plans deliberately have no live dispatch implementation.
        """
        cmd = command.strip().upper()
        if re.match(r'^CAL[A-Z]*:', cmd):
            return True
        if re.match(r'^(?:SOUR(?:CE)?:)?APPL(?:Y)?(?:\b|:)', cmd):
            return True
        if re.match(r'^(?:STAT(?:US)?:PRES|TRIG(?:GER)?(?:\b|:))', cmd):
            return True
        if re.match(r'^\*(?:CLS|RST|WAI|TRG|RCL|SAV|TST)(?:\b|\?)', cmd):
            return True
        if re.match(r'^\*(?:PSC|OPC)(?!\?)\b', cmd):
            return True
        if re.match(r'^(?:VOLT(?:AGE)?|CURR(?:ENT)?):(?:TRIG|STEP|UP|DOWN)', cmd):
            return True
        return False

    def _execute(self, command: str, *, query: bool):
        """Single wire path for normal API, diagnostics and adapter error checks."""
        with self._lock:
            if not self._dispatch_depth:
                raise SafetyError('Use the driver API; direct adapter sends are forbidden')
            self._command(command, query=query)
            if self._service_only_command(command):
                raise SafetyError('Service-only SCPI is blocked by the ordinary driver'
                                  ' (including debug bypass); offline plan only')
            if self._link_uncertain:
                raise TransportError('UNKNOWN OUTPUT STATE: link uncertain; no commands or automatic reconnect')
            session_command = self._session_command(command)
            if session_command not in ('*IDN?', 'SYST:ERR?', 'SYST:REM', 'SYST:RWLOCK', 'SYST:LOC'):
                self._ensure_remote()
            self._trace('query' if query else 'write', command)
            try:
                answer = self._raw.query(command) if query else self._raw.write(command)
            except BaseException as exc:
                self._latch_fault(link_uncertain=True)
                self._trace('io_error', command, exception_type=type(exc).__name__)
                raise
            if not query:
                if session_command in ('SYST:REM', 'SYST:RWLOCK'): self._remote_mode = True
                elif session_command == 'SYST:LOC': self._remote_mode = False
                elif session_command.startswith('INST:NSEL '):
                    self._selected_channel = int(session_command[-1])
            else:
                # Even custom transports must not hand a malformed line to a
                # decoder/trace callback and then permit another wire command.
                if (not isinstance(answer, str) or not answer.isascii() or
                        any(char in answer for char in ('\r', '\n', '\x00'))):
                    self._latch_fault(link_uncertain=True)
                    raise InstrumentError(
                        f'Malformed SCPI reply to {command}; UNKNOWN OUTPUT STATE '
                        '(link uncertain; no retry)')
                self._trace('reply', command, response=answer)
            return answer

    def diagnostic_query(self, command: str) -> str:
        """Read a raw SCPI reply through the normal session; debug must be enabled."""
        with self._lock:
            if not self._debug: raise SafetyError('Diagnostic mode is disabled')
            self._command(command, query=True)
            return self.identity() if command.upper() == '*IDN?' else self._query(command)

    def diagnostic_write(self, command: str):
        """Use normal profile guards unless debug_bypass_profile is explicitly set.

        The bypass permits deliberate negative tests, not normal operation. It does
        not bypass transport failures, session ownership or adapter command gates.
        """
        with self._lock:
            if not self._debug: raise SafetyError('Diagnostic mode is disabled')
            self._command(command, query=False)
            if self._debug_bypass_profile:
                self._write(command)
                return
            command = self._session_command(command)
            match = re.fullmatch(r'(VOLT|CURR) ([+-]?(?:\d+(?:\.\d*)?|\.\d+))', command)
            if match:
                if self._selected_channel is None:
                    raise SafetyError('Select a channel through the driver first')
                key = 'voltage' if match[1] == 'VOLT' else 'current'
                self.set_channel(self._selected_channel, **{key: float(match[2])})
            elif re.fullmatch(r'INST:NSEL [123]', command):
                self._select(int(command[-1]))
            elif command == 'SYST:REM':
                self.remote(lock_panel=False)
            elif command == 'SYST:RWLOCK':
                self.remote(lock_panel=True)
            elif command == 'SYST:LOC':
                if self._armed and self._profile is not None and (
                        self._profile.lock_front_panel or self._profile.access == 'power_only'):
                    raise SafetyError('Disarm protected profile before returning to Local')
                self._write(command)
            elif command == 'OUTP 0':
                self.emergency_off()
            else:
                raise SafetyError('Raw configuration requires debug_bypass_profile=True')

    @property
    def instrument_id(self) -> str | None: return self._idn
    @property
    def instrument_key(self) -> tuple[str, str, str] | None: return self._instrument_key
    @property
    def profile(self) -> Profile | None: return self._profile
    @property
    def armed(self) -> bool: return self._armed
    @property
    def faulted(self) -> bool: return self._faulted

    @property
    def profile_monitor_running(self) -> bool:
        """True while the opt-in worker is alive, including while stopping."""
        with self._monitor_control_lock:
            thread = self._profile_monitor_thread
            return bool(thread is not None and thread.is_alive())

    @property
    def profile_monitor_error(self) -> str | None:
        """Last monitor-detected failure; does not include external API faults."""
        with self._lock:
            return self._profile_monitor_error

    def _require_monitor_stopped(self) -> None:
        thread = self._profile_monitor_thread
        if thread is not None and thread.is_alive():
            raise SafetyError('Stop and join the profile monitor before replacing a profile')

    def start_profile_monitor(self, *, interval_s: float | None = None) -> None:
        """Explicitly start best-effort periodic integrity polling.

        Checks are serialized with all other driver SCPI calls. A failed check
        latches a fault and uses verify_profile()'s existing shutdown path;
        this is NOT an independent hardware watchdog. The interval is a minimum
        delay between completed checks, not a guaranteed detection deadline.
        """
        with self._monitor_control_lock, self._lock:
            self._require_ready()
            self._require_monitor_stopped()
            interval = self._profile.verify_every_s if interval_s is None else interval_s
            if (isinstance(interval, bool) or not isinstance(interval, (int, float)) or
                    not math.isfinite(interval) or interval < 0.1):
                raise ValueError('profile monitor interval_s must be >= 0.1 seconds')
            # Verify before reporting that monitoring is active; do not let an
            # already-invalid profile wait through one polling interval.
            self.verify_profile()
            stop = threading.Event()
            worker = threading.Thread(target=self._profile_monitor_loop,
                                      args=(stop, float(interval)),
                                      name='Keithley2231A-profile-monitor', daemon=True)
            self._profile_monitor_stop = stop
            self._profile_monitor_error = None
            self._profile_monitor_thread = worker
            worker.start()

    def _profile_monitor_loop(self, stop: threading.Event, interval_s: float) -> None:
        try:
            while not stop.wait(interval_s):
                try:
                    with self._lock:
                        if stop.is_set():
                            break
                        self.verify_profile()
                except Exception as exc:
                    with self._lock:
                        self._profile_monitor_error = f'{type(exc).__name__}: {exc}'
                    break
        finally:
            stop.set()

    def stop_profile_monitor(self, *, timeout_s: float = 5.0) -> None:
        """Request monitor shutdown and join without holding the SCPI lock.

        A blocked transport query cannot be killed by Python. If the monitor
        cannot stop by the timeout, raise; do not report it as stopped or close
        the transport while a worker may still be using it.
        """
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or
                not math.isfinite(timeout_s) or timeout_s < 0):
            raise ValueError('timeout_s must be a finite non-negative number')
        # Do not acquire the SCPI lock here: its owner may be stuck in I/O.
        # Signal before joining so a stalled worker can be diagnosed by timeout.
        with self._monitor_control_lock:
            thread = self._profile_monitor_thread
            stop = self._profile_monitor_stop
            if stop is not None:
                stop.set()
        if thread is not None and thread.is_alive():
            if thread is threading.current_thread():
                raise SafetyError('Monitor thread cannot join itself')
            thread.join(timeout_s)
            if thread.is_alive():
                raise SafetyError('Profile monitor did not stop; output state not established')

    def _dispatch_scpi(self, command: str, *, query: bool):
        """Single guarded adapter dispatch for writes and queries.

        Neither SCPI commands nor transport reads are automatically retried.
        Adapter exceptions latch a fault; wire exceptions additionally latch
        the uncertain-link state in _execute().
        """
        with self._lock:
            self._dispatch_depth += 1
            try:
                return self._io.query(command) if query else self._io.write(command)
            except BaseException:
                self._latch_fault()
                raise
            finally:
                self._dispatch_depth -= 1

    def _write(self, command: str):
        self._dispatch_scpi(command, query=False)

    def _query(self, command: str) -> str:
        return self._dispatch_scpi(command, query=True)

    def _malformed_value(self, description: str) -> None:
        """A consumed, unparseable reply makes further exchange unsafe."""
        self._latch_fault(link_uncertain=True)
        raise InstrumentError(f'{description}; UNKNOWN OUTPUT STATE '
                              '(link uncertain; no retry)')

    def _num(self, command: str) -> float:
        s = self._query(command)
        try:
            val = float(s)
            if not math.isfinite(val): raise ValueError('not finite')
        except (TypeError, ValueError, OverflowError):
            self._malformed_value(f'Unexpected response to {command}: {s!r}')
        return val

    def _uint(self, command: str, *, maximum: int = 65535) -> int:
        """Read bounded SCPI unsigned integers without floating-point conversion.

        Integer register replies are not measurements: parsing via float can
        round non-integral text (e.g. 1.0000000000000001) into a valid bit.
        Refuse malformed/oversized values as an uncertain link, without retry
        or any subsequent command on that transport.
        """
        raw = self._query(command)
        if (not isinstance(raw, str) or
                re.fullmatch(r'[0-9]{1,5}', raw.strip()) is None):
            self._malformed_value(f'Invalid unsigned integer response to {command}: {raw!r}')
        value = int(raw.strip())
        if value > maximum:
            self._malformed_value(f'Out-of-range unsigned integer response to {command}: {raw!r}')
        return value

    def _bool(self, value: str) -> bool:
        if isinstance(value, str):
            x = value.strip().upper()
            if x in ('1', 'ON'): return True
            if x in ('0', 'OFF'): return False
        self._malformed_value(f'Invalid boolean response: {value!r}')

    def identity(self) -> str:
        """Return the latest raw *IDN? reply, preserving Main/Aux text verbatim.

        On a qualified 2231A-30-3, the Aux field has changed between 1.04
        and 0.01 while manufacturer, model and serial stayed constant. The
        front panel has also shown a different Aux revision. Such revision
        drift is a non-blocking *observation*, not proof of a new instrument
        or of a firmware update. Selection-dependent display behavior is a
        hypothesis, not a vendor-documented property. Do not normalize 0.01
        to 1.01 or suppress independent I/O, status or identity failures.
        """
        with self._lock:
            ans = self._query('*IDN?')
            parts = [part.strip() for part in ans.split(',')]
            if len(parts) != 4 or parts[0].lower() != 'keithley instruments' or parts[1].upper() != '2231A-30-3' or not parts[3]:
                self._latch_fault(link_uncertain=True)
                raise InstrumentError('Not a valid Keithley 2231A-30-3 identity')
            key = (parts[0].lower(), parts[1].upper(), parts[2])
            if self._expected_serial is not None and parts[2] != self._expected_serial:
                self._latch_fault(link_uncertain=True)
                raise InstrumentError('Instrument serial number mismatch')
            if self._instrument_key is not None and key != self._instrument_key:
                self._latch_fault(link_uncertain=True)
                raise InstrumentError('Instrument identity changed during connection')
            self._instrument_key = key
            self._idn = ans
            return ans

    def remote(self, *, lock_panel: bool = False):
        with self._lock:
            if self._armed and self._profile is not None:
                if self._profile.access == 'power_only':
                    raise SafetyError('power_only: remote configuration frozen')
                if self._profile.lock_front_panel and not lock_panel:
                    raise SafetyError('Cannot release front panel while protected profile is armed')
            self._write('SYST:RWLOCK' if lock_panel else 'SYST:REM')

    @staticmethod
    def _check_channel(ch: int):
        if type(ch) is not int or ch not in (1, 2, 3):
            raise ValueError('channel must be 1, 2 or 3')

    def _select(self, ch: int):
        self._check_channel(ch)
        self._write(f'INST:NSEL {ch}')

    def _require_ready(self):
        if self._profile is None or not self._armed or self._faulted:
            raise SafetyError('No active validated profile or instrument faulted')

    def _try_confirm_fault_shutdown(self) -> bool:
        """One shutdown-and-readback route; never reuse an uncertain link."""
        if self._link_uncertain:
            return False
        try:
            if self._off_confirmed():
                self._expected_outputs = {ch: False for ch in (1, 2, 3)}
                return True
        except Exception:
            pass
        return False

    def _trip(self, why: str):
        """Latch a semantic safety fault and report shutdown evidence accurately."""
        self._latch_fault()
        if self._link_uncertain:
            raise SafetyError(f'{why}; UNKNOWN OUTPUT STATE (link uncertain; no retry). '
                              'Use independent front-panel or external shutdown and '
                              'verify outputs physically before a new session')
        if self._try_confirm_fault_shutdown():
            raise SafetyError(f'{why}; output OFF confirmed by readback')
        raise SafetyError(f'{why}; UNKNOWN OUTPUT STATE (shutdown unconfirmed)')

    def output_states(self) -> dict[int, bool]:
        """Read all channel ON states without applying a profile or changing outputs.

        The shared session validates identity and enters Remote when necessary.
        An armed profile retains its current remote/panel-lock configuration.
        Read-only checks remain possible after semantic faults on a live session;
        uncertain I/O prohibits further commands and never reconnects or retries.
        """
        with self._lock:
            if self._link_uncertain:
                raise SafetyError('UNKNOWN OUTPUT STATE: link uncertain; no further operations')
            try:
                return self._output_states()
            except Exception as exc:
                self._latch_fault()
                if self._link_uncertain:
                    raise SafetyError(
                        f'UNKNOWN OUTPUT STATE ({exc}; link uncertain; no retry). '
                        'Use independent front-panel or external shutdown, '
                        'verify the outputs physically, then open a new session') from exc
                raise

    def _output_states(self) -> dict[int, bool]:
        """Read each selected-channel ON state and restore the prior selection.

        The operation status register's ON bit is not reliable on observed
        firmware while a channel regulates in CC. CHAN:OUTP? is the documented
        output-state query for the currently selected channel.
        """
        if isinstance(self._profile, (SeriesProfile, ParallelProfile)):
            enabled = self._bool(self._query('OUTP:STAT:ALL?'))
            return {ch: enabled for ch in (1, 2, 3)}
        if self._profile is None and self._bool(self._query('OUTP:SER?')):
            enabled = self._bool(self._query('OUTP:STAT:ALL?'))
            return {ch: enabled for ch in (1, 2, 3)}
        selected = self._uint('INST:NSEL?')
        if selected not in (1, 2, 3):
            self._malformed_value(f'Invalid selected-channel response: {selected}')
        states = {}
        try:
            for ch in (1, 2, 3):
                self._select(ch)
                actual = self._num('INST:NSEL?')
                if actual != ch:
                    raise SafetyError(f'Cannot read CH{ch} output state: selected channel is {actual:g}')
                states[ch] = self._bool(self._query('CHAN:OUTP?'))
        finally:
            if not self._link_uncertain:
                self._select(selected)
        return states

    def _off_confirmed(self) -> bool:
        """Request global OFF, then explicitly disable any channel still ON."""
        self._write('OUTP 0')
        states = self._output_states()
        if any(states.values()):
            for ch, enabled in states.items():
                if enabled:
                    self._select(ch)
                    self._write('CHAN:OUTP 0')
            states = self._output_states()
        return not any(states.values())

    def emergency_off(self) -> None:
        """Available even when faulted; command delivery not guaranteed if link is dead."""
        with self._lock:
            if self._profile_monitor_stop is not None:
                self._profile_monitor_stop.set()
            self._armed = False
            try:
                # A previous run may have returned the instrument to Local.
                # Re-enter Remote before sending shutdown SCPI commands.
                self._write('SYST:REM')
                if not self._off_confirmed():
                    raise SafetyError('A channel remains ON after OUTP 0')
                self._expected_outputs = {ch: False for ch in (1, 2, 3)}
            except BaseException:
                self._latch_fault()
                raise

    def _exit_parallel_if_active(self) -> None:
        """Require verified global OFF before clearing an inherited Parallel mode.

        A new driver session may find Parallel active even without an in-memory
        profile. Query the instrument, not the previous Python profile. This
        guard runs before any channel-specific setup in every topology path.
        It does not establish that external jumper wires have been removed.
        """
        if not self._bool(self._query('OUTP:PARALLEL?')):
            return
        self._write('OUTP 0')
        if self._bool(self._query('OUTP:STAT:ALL?')):
            raise SafetyError('Parallel output could not be switched OFF before mode change')
        self._write('OUTP:PARALLEL 0')

    def _disable_combined_modes(self) -> None:
        """Clear all combination modes in the instrument's established order.

        Callers must already have confirmed that outputs are OFF. This does
        not remove or verify any *physical* Series/Parallel connections.
        """
        self._write('OUTP:SER 0')
        self._write('OUTP:PARALLEL 0')
        self._write('OUTP:TRAC 0')

    def _abort_profile_application(self) -> None:
        """Common best-effort cleanup after any profile-application failure.

        An OUTP 0 write alone cannot prove outputs are OFF. In particular,
        an uncertain link blocks any follow-up I/O; never report confirmed OFF.
        Preserve the original exception raised by the failed application.
        """
        self._profile = None
        self._latch_fault()
        try:
            self._write('OUTP 0')
        except Exception:
            pass

    def apply_profile(self, profile: Profile, *, confirmed_wiring: str | None = None) -> None:
        """Arm a profile only after operator confirmation of physical topology.

        ``confirmed_wiring='independent'`` means CH1/CH2 have NO external
        Series or Parallel jumpers (also required for Tracking). This is a
        *human assertion*, not a measurement of the actual cable arrangement.
        Combined profiles require their respective explicit wiring token.
        All confirmations are checked BEFORE SCPI I/O; profiles are applied
        with outputs OFF and never turn outputs on automatically.
        """
        if isinstance(profile, SeriesProfile):
            self.apply_series_profile(profile, confirmed_wiring=confirmed_wiring)
            return
        if isinstance(profile, ParallelProfile):
            self.apply_parallel_profile(profile, confirmed_wiring=confirmed_wiring)
            return
        if not isinstance(profile, Profile): raise ProfileError('expected Profile, SeriesProfile or ParallelProfile')
        if profile.wiring not in ('independent', 'tracking'):
            raise SafetyError('Use a topology-specific profile for Series/Parallel wiring')
        if confirmed_wiring != 'independent':
            raise SafetyError('Before applying Independent/Tracking, confirm CH1/CH2 '
                              'have NO external Series/Parallel jumpers: '
                              "confirmed_wiring='independent'")
        with self._lock:
            if self._armed:
                raise SafetyError('Active profile immutable. Call emergency_off() before replacing it')
            self._require_monitor_stopped()
            self._armed = False
            self._faulted = False
            self._profile = None
            self._expected_setpoints = {
                ch: (spec.initial_voltage, spec.initial_current)
                for ch, spec in enumerate(profile.channels, 1)}
            self._expected_outputs = {ch: False for ch in (1, 2, 3)}
            try:
                self.remote(lock_panel=False)
                self.identity()
                self._exit_parallel_if_active()
                if not self._off_confirmed():
                    self._trip('Output could not be switched OFF')
                # Operator must have confirmed the physical Series/Parallel
                # jumpers were removed BEFORE this configuration began.
                self._disable_combined_modes()
                for ch, spec in enumerate(profile.channels, 1):
                    self._select(ch)
                    self._write('OUTP:TIM:STAT 0')
                    if self._bool(self._query('OUTP:TIM:STAT?')):
                        raise SafetyError(f'CH{ch}: inherited timer remains enabled')
                    self._write('VOLT 0')
                    self._write('CURR 0')
                    self._write(f'VOLT:LIM {spec.max_voltage:.2f}')
                    self._write('VOLT:LIM:STAT 1')
                    if not self._bool(self._query('VOLT:LIM:STAT?')):
                        raise SafetyError(f'CH{ch}: hardware limit inactive')
                    got = self._num('VOLT:LIM?')
                    if abs(got - spec.max_voltage) > .0049:
                        raise SafetyError(f'CH{ch}: hardware V limit mismatch {got}')
                    self._write(f'CURR {spec.initial_current:.3f}')
                    self._write(f'VOLT {spec.initial_voltage:.2f}')
                    self._check_setpoints(ch, spec)
                if profile.wiring == 'series': self._write('OUTP:SER 1')
                elif profile.wiring == 'tracking': self._write('OUTP:TRAC 1')
                self._profile = profile
                self._check_wiring_mode()
                if profile.lock_front_panel:
                    self.remote(lock_panel=True)
                if any(self._output_states().values()):
                    raise SafetyError('Unexpected output ON while applying profile')
                self._last_verify = time.monotonic()
                self._armed = True  # Outputs remain OFF until explicitly enabled.
                if profile.wiring == 'series':
                    # Changing combine mode can change the exposed limit/register
                    # context. Never arm on checks made before the transition.
                    self.verify_profile()
            except BaseException:
                self._abort_profile_application()
                raise

    def apply_series_profile(self, profile: SeriesProfile, *,
                             confirmed_wiring: str | None = None) -> None:
        """Configure an external CH1−/CH2+ series pair with a combined soft cap.

        Series mode replaces separate CH1/CH2 meter contexts and firmware has
        been observed to disable the combined voltage-limit register. The API
        therefore enforces ``max_voltage`` in software and never claims an
        active hardware voltage limiter. CH1 setpoint is the total voltage;
        current is shared by the series pair. Global ON also enables CH3.
        """
        if not isinstance(profile, SeriesProfile):
            raise ProfileError('expected SeriesProfile')
        if confirmed_wiring != 'series':
            raise SafetyError('Confirm physical CH1− to CH2+ series wiring explicitly')
        with self._lock:
            if self._armed:
                raise SafetyError('Active profile immutable. Call emergency_off() before replacing it')
            self._require_monitor_stopped()
            self._armed = False
            self._faulted = False
            self._profile = None
            self._expected_setpoints = {
                1: (profile.initial_voltage, profile.initial_current),
                3: (profile.channel3.initial_voltage, profile.channel3.initial_current),
            }
            self._expected_outputs = {ch: False for ch in (1, 2, 3)}
            try:
                self.remote(lock_panel=False)
                self.identity()
                self._exit_parallel_if_active()
                if not self._off_confirmed():
                    self._trip('Output could not be switched OFF')
                self._disable_combined_modes()

                for ch, voltage_limit in ((1, profile.channel_voltage_limits[0]),
                                          (2, profile.channel_voltage_limits[1])):
                    self._select(ch)
                    self._write('OUTP:TIM:STAT 0')
                    if self._bool(self._query('OUTP:TIM:STAT?')):
                        raise SafetyError(f'CH{ch}: inherited timer remains enabled')
                    self._write('VOLT 0')
                    self._write(f'VOLT:LIM {voltage_limit:.2f}')
                    self._write('VOLT:LIM:STAT 1')
                    if (not self._bool(self._query('VOLT:LIM:STAT?')) or
                            abs(self._num('VOLT:LIM?') - voltage_limit) > .0049):
                        raise SafetyError(f'CH{ch}: independent voltage limit setup failed')
                    self._write(f'CURR {profile.initial_current:.3f}')

                ch3 = profile.channel3
                self._select(3)
                self._write('OUTP:TIM:STAT 0')
                if self._bool(self._query('OUTP:TIM:STAT?')):
                    raise SafetyError('CH3: inherited timer remains enabled')
                self._write('VOLT 0')
                self._write(f'VOLT:LIM {ch3.max_voltage:.2f}')
                self._write('VOLT:LIM:STAT 1')
                if (not self._bool(self._query('VOLT:LIM:STAT?')) or
                        abs(self._num('VOLT:LIM?') - ch3.max_voltage) > .0049):
                    raise SafetyError('CH3: hardware voltage limit setup failed')
                self._write(f'CURR {ch3.initial_current:.3f}')
                self._write(f'VOLT {ch3.initial_voltage:.2f}')
                self._check_setpoints(3, ch3)

                self._profile = profile
                self._select(1)
                self._write('OUTP:SER 1')
                self._check_wiring_mode()
                self._write(f'CURR {profile.initial_current:.3f}')
                self._write(f'VOLT {profile.initial_voltage:.2f}')
                self._check_combined_setpoints((profile.initial_voltage, profile.initial_current))
                if abs(self._num('VOLT:LIM?') - sum(profile.channel_voltage_limits)) > .0049:
                    raise SafetyError('Series voltage-limit register does not match channel-limit sum')
                if any(self._output_states().values()):
                    raise SafetyError('Unexpected output ON while applying SeriesProfile')
                if profile.lock_front_panel:
                    self.remote(lock_panel=True)
                self._last_verify = time.monotonic()
                self._armed = True
                self.verify_profile()
            except BaseException:
                self._abort_profile_application()
                raise

    def apply_parallel_profile(self, profile: ParallelProfile, *,
                               confirmed_wiring: str | None = None) -> None:
        """Arm an external CH1/CH2 pair under aggregate current/software V caps."""
        if not isinstance(profile, ParallelProfile):
            raise ProfileError('expected ParallelProfile')
        if confirmed_wiring != 'parallel':
            raise SafetyError('Confirm physical CH1+/CH2+ and CH1−/CH2− wiring explicitly')
        with self._lock:
            if self._armed:
                raise SafetyError('Active profile immutable. Call emergency_off() before replacing it')
            self._require_monitor_stopped()
            self._armed, self._faulted, self._profile = False, False, None
            self._expected_setpoints = {
                1: (profile.initial_voltage, profile.initial_current),
                3: (profile.channel3.initial_voltage, profile.channel3.initial_current),
            }
            self._expected_outputs = {ch: False for ch in (1, 2, 3)}
            try:
                self.remote(lock_panel=False)
                self.identity()
                # Check the actual instrument mode, including a previous
                # process/session with no matching Python profile.
                self._exit_parallel_if_active()
                if not self._off_confirmed():
                    self._trip('Output could not be switched OFF')
                self._disable_combined_modes()
                for ch in (1, 2):
                    self._select(ch)
                    self._write('OUTP:TIM:STAT 0')
                    if self._bool(self._query('OUTP:TIM:STAT?')):
                        raise SafetyError(f'CH{ch}: inherited timer remains enabled')
                    self._write('VOLT 0')
                    self._write('CURR 0')
                    self._write(f'VOLT:LIM {profile.max_voltage:.2f}')
                    self._write('VOLT:LIM:STAT 1')
                    if (not self._bool(self._query('VOLT:LIM:STAT?')) or
                            abs(self._num('VOLT:LIM?') - profile.max_voltage) > .0049):
                        raise SafetyError(f'CH{ch}: independent voltage limit setup failed')
                    # Configure the pair below its per-channel 3 A ceiling.
                    # The aggregate current setpoint is applied only after the
                    # instrument has entered Parallel mode.
                    self._write(f'VOLT {profile.initial_voltage:.2f}')
                ch3 = profile.channel3
                self._select(3)
                self._write('OUTP:TIM:STAT 0')
                if self._bool(self._query('OUTP:TIM:STAT?')):
                    raise SafetyError('CH3: inherited timer remains enabled')
                self._write('VOLT 0')
                self._write('CURR 0')
                self._write(f'VOLT:LIM {ch3.max_voltage:.2f}')
                self._write('VOLT:LIM:STAT 1')
                if (not self._bool(self._query('VOLT:LIM:STAT?')) or
                        abs(self._num('VOLT:LIM?') - ch3.max_voltage) > .0049):
                    raise SafetyError('CH3 hardware voltage limit setup failed')
                self._write(f'CURR {ch3.initial_current:.3f}')
                self._write(f'VOLT {ch3.initial_voltage:.2f}')
                self._check_setpoints(3, ch3)
                self._profile = profile
                self._select(1)
                self._write('OUTP:PARALLEL 1')
                self._check_wiring_mode()
                self._write(f'CURR {profile.initial_current:.3f}')
                self._write(f'VOLT {profile.initial_voltage:.2f}')
                self._check_combined_setpoints((profile.initial_voltage, profile.initial_current))
                self._parallel_limit_enabled = self._bool(self._query('VOLT:LIM:STAT?'))
                if (self._parallel_limit_enabled and
                        abs(self._num('VOLT:LIM?') - profile.max_voltage) > .0049):
                    raise SafetyError('Parallel hardware voltage limit does not match profile')
                if any(self._output_states().values()):
                    raise SafetyError('Unexpected output ON while applying ParallelProfile')
                if profile.lock_front_panel:
                    self.remote(lock_panel=True)
                self._last_verify = time.monotonic()
                self._armed = True
                self.verify_profile()
            except BaseException:
                self._abort_profile_application()
                raise

    def _check_combined_setpoints(self, expected=None):
        """Validate CH1 aggregate setpoints for Series or Parallel via one path.

        The instrument presents CH1 as the aggregate output in both modes.
        Preserve the independently verified SCPI command order and tolerances.
        """
        profile = self._profile
        if not isinstance(profile, (SeriesProfile, ParallelProfile)):
            raise SafetyError('Combined profile is not active')
        self._select(1)
        voltage, current = self._num('VOLT?'), self._num('CURR?')
        wanted = expected if expected is not None else self._expected_setpoints[1]
        if (voltage < 0 or voltage > profile.max_voltage + .011 or
                current < 0 or current > profile.max_current + .002 or
                abs(voltage - wanted[0]) > .0049 or abs(current - wanted[1]) > .00049):
            raise SafetyError(f'{profile.wiring.capitalize()} setpoint mismatch: '
                              f'V={voltage}, I={current}, expected={wanted}')

    def _check_setpoints(self, ch: int, spec, expected=None):
        self._select(ch)
        v = self._num('VOLT?'); i = self._num('CURR?')
        if v < -.011 or v > spec.max_voltage + .011 or i < -.002 or i > spec.max_current + .002:
            raise SafetyError(f'CH{ch}: readback setpoint exceeds profile: V={v} I={i}')
        wanted_v, wanted_i = expected if expected is not None else self._expected_setpoints[ch]
        # These are digital setpoint readbacks, not analog measurements. Less
        # than half a programming step catches even a one-step external edit.
        if abs(v - wanted_v) > .0049 or abs(i - wanted_i) > .00049:
            raise SafetyError(f'CH{ch}: authorized setpoints changed: V={v} I={i}')

    def _check_wiring_mode(self):
        p = self._profile
        if p is None: raise SafetyError('profile not loaded')
        flags = {'series': self._bool(self._query('OUTP:SER?')),
                 'parallel': self._bool(self._query('OUTP:PARALLEL?')),
                 'tracking': self._bool(self._query('OUTP:TRAC?'))}
        active = {k for k, v in flags.items() if v}
        wanted = set() if p.wiring == 'independent' else {p.wiring}
        if active != wanted:
            raise SafetyError(f'expected wiring {wanted}, actual instrument mode {active}')

    def _verify_combined_ch3(self) -> None:
        """One CH3 integrity path shared by Series and Parallel profiles."""
        self._select(3)
        spec = self._profile.channel3
        if (not self._bool(self._query('VOLT:LIM:STAT?')) or
                abs(self._num('VOLT:LIM?') - spec.max_voltage) > .0049):
            raise SafetyError('CH3 hardware voltage limit changed')
        self._check_setpoints(3, spec)

    def _verify_combined_outputs(self) -> None:
        """One global output-state check for the two combined topologies."""
        states = self._output_states()
        if states != self._expected_outputs:
            raise SafetyError(f'{self._profile.wiring.capitalize()} global output state mismatch: {states}')

    def verify_profile(self):
        with self._lock:
            self._require_ready()
            try:
                self._check_wiring_mode()
                if isinstance(self._profile, ParallelProfile):
                    self._verify_combined_outputs()
                    self._check_combined_setpoints()
                    self._select(1)
                    limit_enabled = self._bool(self._query('VOLT:LIM:STAT?'))
                    if limit_enabled != self._parallel_limit_enabled:
                        raise SafetyError('Parallel hardware voltage-limit state changed')
                    if (limit_enabled and
                            abs(self._num('VOLT:LIM?') - self._profile.max_voltage) > .0049):
                        raise SafetyError('Parallel hardware voltage limit changed')
                    self._verify_combined_ch3()
                    self._last_verify = time.monotonic()
                    return
                if isinstance(self._profile, SeriesProfile):
                    self._verify_combined_outputs()
                    self._check_combined_setpoints()
                    if abs(self._num('VOLT:LIM?') - sum(self._profile.channel_voltage_limits)) > .0049:
                        raise SafetyError('Series voltage-limit register changed')
                    self._verify_combined_ch3()
                    self._last_verify = time.monotonic()
                    return
                for ch, enabled in self._output_states().items():
                    if enabled and not self._expected_outputs[ch]:
                        raise SafetyError(f'CH{ch}: unexpected output ON')
                for ch, spec in enumerate(self._profile.channels, 1):
                    self._select(ch)
                    if not self._bool(self._query('VOLT:LIM:STAT?')):
                        raise SafetyError(f'CH{ch}: hardware V limit disabled')
                    if abs(self._num('VOLT:LIM?') - spec.max_voltage) > .0049:
                        raise SafetyError(f'CH{ch}: hardware V limit changed')
                    self._check_setpoints(ch, spec)
                self._last_verify = time.monotonic()
            except Exception as exc:
                self._trip(f'Profile integrity check failed: {exc}')

    @staticmethod
    def _validate_setpoint_request(ch: int, voltage: float | None, current: float | None,
                                   voltage_cap: float, current_cap: float) -> None:
        """Reject invalid or out-of-profile setpoints before any SCPI I/O."""
        for value, cap, label, step in (
                (voltage, voltage_cap, 'voltage', .01),
                (current, current_cap, 'current', .001)):
            if value is None:
                continue
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or value < 0 or value > cap
                    or abs(value / step - round(value / step)) > 1e-6):
                raise LimitsError(f'CH{ch}: {label}={value} exceeds limit {cap} '
                                  f'or resolution {step}')

    def set_channel(self, ch: int, *, voltage: float | None = None,
                    current: float | None = None):
        """One guarded programming path for independent and combined outputs.

        CH1 represents the aggregate output in Series/Parallel; CH3 is always
        an individual channel. In Tracking, use set_tracking_voltage().
        """
        with self._lock:
            self._require_ready()
            self._check_channel(ch)
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: voltage/current setpoints are frozen')
            combined = isinstance(self._profile, (SeriesProfile, ParallelProfile))
            if combined:
                if ch == 2:
                    raise SafetyError(f'{self._profile.wiring.capitalize()} CH2 has no independent '
                                      'setpoint; change combined output through CH1')
                spec = self._profile.channel3 if ch == 3 else None
                voltage_cap = self._profile.max_voltage if ch == 1 else spec.max_voltage
                current_cap = self._profile.max_current if ch == 1 else spec.max_current
            else:
                if self._profile.wiring != 'independent':
                    raise SafetyError('Use set_tracking_voltage() for tracking voltage; '
                                      'other combined setpoint changes require a fresh profile at OFF')
                spec = self._profile.channels[ch - 1]
                voltage_cap, current_cap = spec.max_voltage, spec.max_current

            self._validate_setpoint_request(ch, voltage, current, voltage_cap, current_cap)
            # An external front-panel edit must be detected before any setpoint write.
            self.verify_profile()
            try:
                self._select(ch)
                # Current first, then voltage; preserve the qualified SCPI order.
                if current is not None:
                    self._write(f'CURR {current:.3f}')
                if voltage is not None:
                    self._write(f'VOLT {voltage:.2f}')
                old_v, old_i = self._expected_setpoints[ch]
                expected = (old_v if voltage is None else voltage,
                            old_i if current is None else current)
                if combined and ch == 1:
                    self._check_combined_setpoints(expected)
                else:
                    self._check_setpoints(ch, spec, expected)
                if not combined:
                    # Preserve the independent-channel post-write confirmation.
                    self._select(ch)
                    if voltage is not None and abs(self._num('VOLT?') - voltage) > .011:
                        raise SafetyError('Voltage set command was not applied')
                    if current is not None and abs(self._num('CURR?') - current) > .002:
                        raise SafetyError('Current set command was not applied')
                self._expected_setpoints[ch] = expected
            except Exception as exc:
                self._trip(f'Setting CH{ch} failed: {exc}')

    def set_tracking_voltage(self, ch: int, voltage: float) -> None:
        """Adjust CH1 or CH2, preserving the profile's initial voltage ratio.

        Both resulting setpoints must fit their caps and 10 mV resolution.
        Zero is rejected because it suppresses tracking (manual section 3-7).
        Current limits remain fixed. Reapply a profile with outputs OFF to change
        the ratio. Physical tracking behavior requires H08 hardware qualification.
        """
        with self._lock:
            self._require_ready()
            if self._profile.wiring != 'tracking' or type(ch) is not int or ch not in (1, 2):
                raise SafetyError('Tracking voltage requires CH1/CH2 in tracking mode')
            if (not isinstance(voltage, (int, float)) or isinstance(voltage, bool)
                    or not math.isfinite(voltage) or voltage <= 0):
                raise LimitsError('Tracking voltage must be finite and greater than zero')
            scale = voltage / self._profile.channels[ch - 1].initial_voltage
            expected = {}
            for channel in (1, 2):
                spec = self._profile.channels[channel - 1]
                value = spec.initial_voltage * scale
                if (not math.isfinite(value) or value > spec.max_voltage + 1e-9
                        or value < .01 or abs(value * 100 - round(value * 100)) > 1e-6):
                    raise LimitsError(f'CH{channel}: linked voltage exceeds cap or 0.01 V resolution')
                expected[channel] = (round(value, 2), self._expected_setpoints[channel][1])
            self.verify_profile()
            try:
                self._select(ch)
                self._write(f'VOLT {voltage:.2f}')
                for channel in (1, 2):
                    self._check_setpoints(channel, self._profile.channels[channel - 1],
                                          expected[channel])
                self._check_setpoints(3, self._profile.channels[2])
                self._check_wiring_mode()
                self._expected_setpoints.update(expected)
            except Exception as exc:
                self._trip(f'Tracking voltage operation failed: {exc}')

    def output(self, ch: int, enabled: bool) -> None:
        if type(enabled) is not bool: raise ValueError('enabled must be bool')
        with self._lock:
            self._require_ready(); self._check_channel(ch)
            if self._profile.access == 'power_only' and self._profile.power_sequence and not self._sequencing:
                raise SafetyError('power_only: use power_on()/power_off() for fixed sequence')
            if self._profile.wiring != 'independent':
                raise SafetyError('Use combined_outputs() for series/parallel/tracking')
            if enabled: self.verify_profile()
            try:
                self._select(ch)
                self._write(f'CHAN:OUTP {1 if enabled else 0}')
                if self._bool(self._query('CHAN:OUTP?')) != bool(enabled):
                    raise SafetyError(f'CH{ch} output state mismatch')
                self._expected_outputs[ch] = enabled
            except Exception as exc:
                self._trip(f'CH{ch} output operation failed: {exc}')

    def combined_outputs(self, enabled: bool, *, confirm_all_channels_safe: bool = False):
        """Global output command. Could enable CH3: explicit confirmation required.
        Intended for operation only when CH3 wiring & configured setpoint are safe.
        """
        if type(enabled) is not bool: raise ValueError('enabled must be bool')
        with self._lock:
            self._require_ready()
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: combined output control is unavailable')
            if self._profile.wiring == 'independent':
                raise SafetyError('Use output(channel, on/off) for independent wiring')
            if enabled and not confirm_all_channels_safe:
                raise SafetyError('Global OUTP 1 may turn on CH3. Confirm ALL connected loads.')
            if enabled: self.verify_profile()
            try:
                self._write(f'OUTP {1 if enabled else 0}')
                deadline = time.monotonic() + (0.0 if enabled else self._OUTPUT_OFF_TIMEOUT_S)
                while True:
                    global_state = self._bool(self._query('OUTP:STAT:ALL?'))
                    if global_state == enabled:
                        break
                    if enabled or time.monotonic() >= deadline:
                        raise SafetyError('Global output state mismatch')
                    time.sleep(min(self._OUTPUT_POLL_INTERVAL_S,
                                   max(0.0, deadline - time.monotonic())))
                while True:
                    states = self._output_states()
                    if all(state == enabled for state in states.values()):
                        break
                    if enabled or time.monotonic() >= deadline:
                        raise SafetyError('Per-channel output state mismatch')
                    time.sleep(min(self._OUTPUT_POLL_INTERVAL_S,
                                   max(0.0, deadline - time.monotonic())))
                self._expected_outputs = {ch: enabled for ch in (1, 2, 3)}
            except Exception as exc:
                self._trip(f'Global output operation failed: {exc}')

    def sequence(self, steps: Iterable[tuple[float, int, bool]]):
        """Independent channels only: (delay_since_previous_step_s, channel, on).
        Delays are best-effort host timings, NOT precision hardware synchronization.
        """
        self._require_ready()
        if self._profile.access == 'power_only':
            raise SafetyError('power_only: arbitrary sequence forbidden; use power_on/power_off')
        if self._profile.wiring != 'independent': raise SafetyError('Sequence not valid for combined mode')
        plan = list(steps)
        for delay, channel, enabled in plan:
            self._check_channel(channel)
            if not math.isfinite(delay) or delay < 0: raise ValueError('invalid delay')
            if not isinstance(enabled, bool): raise ValueError('enabled must be bool')
        try:
            for delay, channel, enabled in plan:
                time.sleep(delay)
                self.output(channel, enabled)
        except BaseException:
            try: self.emergency_off()
            except Exception: pass
            raise

    def power_on(self) -> None:
        """Energize a profile-defined sequence; independent mode only.

        With no explicit sequence, CH1/CH2/CH3 are enabled in that order,
        without delays. ALWAYS verify loads and external cabling first.
        """
        self._power_cycle(True)

    def power_off(self) -> None:
        """Switch outputs OFF in reverse sequence. If faulted, try global OFF."""
        if not self.armed or self.faulted:
            self.emergency_off()
            return
        self._power_cycle(False)

    def _power_cycle(self, enabled: bool):
        from .profile import PowerStep
        with self._lock:
            self._require_ready()
            p = self._profile
            if p.wiring != 'independent':
                raise SafetyError('profiled power sequencing requires independent wiring')
            steps = p.power_sequence or tuple(PowerStep(ch, 0.0) for ch in (1, 2, 3))
            # Readback guards run before and during output changes.
            self.verify_profile()
            self._sequencing = True
            try:
                if enabled:
                    for step in steps:
                        time.sleep(step.delay_s)
                        self.output(step.channel, True)
                else:
                    for step in reversed(steps):
                        self.output(step.channel, False)
                        # Delay that originally preceded this step now follows it.
                        time.sleep(step.delay_s)
            except BaseException:
                try: self.emergency_off()
                except Exception: pass
                raise
            finally:
                self._sequencing = False

    @staticmethod
    def _validate_timer(seconds):
        if seconds is not None and (
                not isinstance(seconds, (int, float)) or isinstance(seconds, bool)
                or not math.isfinite(seconds) or not 0.1 <= seconds <= 99999.9
                or abs(seconds * 10 - round(seconds * 10)) > 1e-6):
            raise ValueError('timer range 0.1..99999.9 seconds, resolution 0.1 s')

    def _configure_timer(self, channel, seconds):
        """Shared timer transaction; caller holds the lock and validates policy."""
        self._select(channel)
        if seconds is None:
            self._write('OUTP:TIM:STAT 0')
        else:
            self._write(f'OUTP:TIM:DEL {seconds:.1f}')
            if abs(self._num('OUTP:TIM:DEL?') - seconds) > .049:
                raise SafetyError('timer delay readback mismatch')
            self._write('OUTP:TIM:STAT 1')
        if self._bool(self._query('OUTP:TIM:STAT?')) != (seconds is not None):
            raise SafetyError('timer state readback mismatch')

    def tracking_timers(self, ch1_seconds: float | None, ch2_seconds: float | None):
        """Configure both tracking timers at OFF; None disables that timer.

        With both enabled the instrument uses the shorter duration (manual 3-7).
        Configuring the pair is sequential, not atomic; failure trips all OFF.
        Actual expiry behavior remains subject to hardware qualification.
        """
        with self._lock:
            self._require_ready()
            if self._profile.wiring != 'tracking':
                raise SafetyError('tracking_timers requires a tracking profile')
            for seconds in (ch1_seconds, ch2_seconds): self._validate_timer(seconds)
            self.verify_profile()
            if any(self._output_states().values()):
                raise SafetyError('Configure tracking timers only with all outputs OFF')
            try:
                for channel, seconds in ((1, ch1_seconds), (2, ch2_seconds)):
                    self._configure_timer(channel, seconds)
            except Exception as exc: self._trip(f'Tracking timer configuration failed: {exc}')

    def timer(self, channel: int, seconds: float | None):
        """Configure channel's PSU OFF timer; triggering depends on PSU output activation."""
        with self._lock:
            self._require_ready(); self._check_channel(channel)
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: timer settings are frozen')
            self._validate_timer(seconds)
            if self._profile.wiring == 'tracking' and channel in (1,2):
                raise SafetyError('Use tracking_timers() at OFF to configure the coupled timer pair')
            try:
                self.verify_profile()
                self._configure_timer(channel, seconds)
            except Exception as exc: self._trip(f'Timer configuration failed: {exc}')

    def read_channel(self, ch: int, *, with_regulation: bool = True) -> Reading:
        """Read actual output, measurements, setpoints and regulation evidence.

        ON/OFF comes from CHAN:OUTP?, not from the presence of CV/CC bits.
        Missing OPER mode bits alone do not disable CH3 use: QUES may supply
        its mode. Both status queries must succeed; I/O failures still trip.
        """
        with self._lock:
            self._require_ready(); self._check_channel(ch)
            if self._profile.wiring in ('series', 'parallel') and ch == 2:
                raise SafetyError('Combined CH2 metering is replaced by CH1+CH2; request channel 1')
            if isinstance(self._profile, SeriesProfile):
                spec = (self._profile.channel3 if ch == 3 else
                        ChannelProfile(self._profile.max_voltage, self._profile.max_current,
                                       self._profile.initial_voltage, self._profile.initial_current))
            elif isinstance(self._profile, ParallelProfile):
                spec = (self._profile.channel3 if ch == 3 else
                        ChannelProfile(self._profile.max_voltage, self._profile.max_current,
                                       self._profile.initial_voltage, self._profile.initial_current,
                                       self._profile.expected_regulation))
            else:
                spec = self._profile.channels[ch - 1]
            if (not with_regulation and spec.expected_regulation != 'auto'):
                raise SafetyError('Regulation checks required by profile cannot be disabled')
            try:
                if self._profile.verify_every_s and time.monotonic() - self._last_verify >= self._profile.verify_every_s:
                    self.verify_profile()
                self._select(ch)
                enabled = self._bool(self._query('CHAN:OUTP?'))
                volts = self._num('MEAS:VOLT?')
                amps = self._num('MEAS:CURR?')
                voltage_set = self._num('VOLT?')
                current_set = self._num('CURR?')
                if volts < -.05 or amps < -.03:
                    raise SafetyError(f'Unexpected negative measurement CH{ch}: {volts}, {amps}')
                mode = 'UNKNOWN'
                operation_mask = None
                questionable_mask = None
                regulation_source = None
                if with_regulation:
                    operation_mask = self._uint(f'STAT:OPER:INST:ISUM{ch}:COND?', maximum=255)
                    questionable_mask = self._uint(f'STAT:QUES:INST:ISUM{ch}:COND?', maximum=255)

                    def decode_regulation(mask: int) -> str | None:
                        cv, cc = bool(mask & 1), bool(mask & 2)
                        if cv and cc:
                            return 'UNKNOWN'
                        if not cv and not cc:
                            return None
                        return 'CV' if cv else 'CC'

                    operation_mode = decode_regulation(operation_mask)
                    questionable_mode = decode_regulation(questionable_mask)
                    # OPER bit 2 is CAL (calibration in progress) in the
                    # reference status model. Never certify CV/CC from a
                    # calibrating channel, even if its other bits agree.
                    if (operation_mask & 4 or
                            'UNKNOWN' in (operation_mode, questionable_mode) or
                            (operation_mode is not None and questionable_mode is not None
                             and operation_mode != questionable_mode)):
                        mode = 'UNKNOWN'
                    else:
                        mode = operation_mode or questionable_mode or 'UNKNOWN'
                        if mode != 'UNKNOWN':
                            regulation_source = ('both' if operation_mode and questionable_mode
                                                 else 'operation' if operation_mode
                                                 else 'questionable')
                if not enabled:
                    mode = 'OFF'
                    regulation_source = None
                combined = isinstance(self._profile, (SeriesProfile, ParallelProfile)) and ch == 1
                if isinstance(self._profile, Profile):
                    spec = self._profile.channels[ch - 1]
                if self._profile.wiring in ('series', 'parallel') and ch == 2:
                    raise SafetyError('CH2 metering is replaced by combined CH1+CH2; query CH1')
                if combined:
                    v_cap = self._profile.max_voltage
                    i_cap = self._profile.max_current
                else:
                    v_cap, i_cap = spec.max_voltage, spec.max_current
                if enabled and spec.trip_on_measurement:
                    # This guard is a SLOW secondary alarm, not an OVP/OCP substitute.
                    v_allow = v_cap + (.025 + .0006 * v_cap)
                    i_allow = i_cap + (.012 + .002 * i_cap)
                    if volts > v_allow or amps > i_allow:
                        raise SafetyError(f'CH{ch} measured V/I exceed profile: {volts}, {amps}')
                if enabled and spec.expected_regulation != 'auto' and mode != spec.expected_regulation.upper():
                    raise SafetyError(f'CH{ch} unexpected regulation: {mode}; expected {spec.expected_regulation}')
                return Reading(channel=ch,
                               timestamp_utc=datetime.now(timezone.utc).isoformat(),
                               monotonic_s=time.monotonic(), voltage_v=volts,
                               current_a=amps, power_w=volts * amps,
                               regulation=mode, enabled=enabled,
                               label="CH1+CH2" if combined else f"CH{ch}",
                               voltage_set_v=voltage_set,
                               current_limit_set_a=current_set,
                               operation_status_mask=operation_mask,
                               questionable_status_mask=questionable_mask,
                               regulation_source=regulation_source)
            except Exception as exc:
                self._trip(f'Measurement failed: {exc}')

    def status(self) -> dict:
        with self._lock:
            self._require_ready()
            try:
                return {'identity': self._idn, 'scpi_version': self._query('SYST:VERS?'),
                        'status_byte': self._uint('*STB?', maximum=255),
                        'questionable_condition': self._uint('STAT:QUES:COND?'),
                        'operation_condition': self._uint('STAT:OPER:COND?')}
            except Exception as exc:
                self._trip(f'Status query failed: {exc}')

    def module_info(self) -> str:
        """Read the instrument's SYST:MODU? text without changing settings.

        The documented reply resembles ``2231,1.04``, but its firmware text
        is observational only: it is NOT an identity or serial-number guard.
        No event register or SCPI error queue is consumed. As with all public
        queries, the driver may first identify the PSU and enter Remote.
        """
        with self._lock:
            if self._faulted:
                raise SafetyError('Faulted session cannot query module info')
            response = self._query('SYST:MODU?')
            if (not response.strip() or len(response) > 128 or
                    any(ord(char) < 32 or ord(char) == 127 for char in response)):
                self._malformed_value(f'Invalid SYST:MODU? reply: {response!r}')
            return response.strip()

    def status_enable_mask(self, register: str) -> int:
        """Read one ENABLE MASK (0..255), never a read-to-clear EVENT latch.

        Accepted register names: ``standard_event``, ``service_request``,
        ``questionable`` and ``operation``. This method does NOT call *ESR?,
        STAT:*:EVEN?, *CLS, nor write status configuration. The register name
        is selected from an exact whitelist, never interpolated into SCPI.
        """
        queries = {
            'standard_event': '*ESE?',
            'service_request': '*SRE?',
            'questionable': 'STAT:QUES:ENAB?',
            'operation': 'STAT:OPER:ENAB?',
        }
        if type(register) is not str or register not in queries:
            raise ValueError('Unsupported status enable register')
        with self._lock:
            if self._faulted:
                raise SafetyError('Faulted session cannot query status enable masks')
            return self._uint(queries[register], maximum=255)

    def status_instrument_enable_mask(self, register: str, *, channel: int | None = None) -> int:
        """Read a nested STATUS INSTrument enable mask without clearing events.

        ``register`` is exactly ``questionable`` or ``operation``. With no
        channel, read the instrument-level enable mask; with channel=1, 2, 3,
        read that channel's ISUMmary enable mask. The channel is an index in
        the documented SCPI path, NOT a request to select a live channel.

        No SET, event (read-to-clear), status preset, output or channel-select
        command is issued. Values are bounded to the documented 8-bit range.
        This is an observation of one Keithley, not a multi-device coordinator.
        """
        if type(register) is not str or register not in ('questionable', 'operation'):
            raise ValueError('Unsupported instrument status enable register')
        if channel is not None and (type(channel) is not int or channel not in (1, 2, 3)):
            raise ValueError('Instrument status channel must be exactly 1, 2, or 3')
        group = {'questionable': 'STAT:QUES:INST',
                 'operation': 'STAT:OPER:INST'}[register]
        command = (f'{group}:ENAB?' if channel is None else
                   f'{group}:ISUM{channel}:ENAB?')
        with self._lock:
            if self._faulted or self._link_uncertain:
                raise SafetyError('Faulted/uncertain session cannot query instrument masks')
            return self._uint(command, maximum=255)

    def power_on_status_clear_state(self) -> int:
        """Observe the *PSC? flag (0 or 1) without changing the instrument.

        This is a power-on STATUS-ENABLE policy observation, **not** a power
        output state or an instruction to clear latches. The vendor documents
        *PSC? as a query but does not specify a reliable default value for
        all firmware revisions. Do not infer an output/recall policy from it.

        This method never sends *PSC, *ESR?, *CLS, *RST or STAT:PRES. A bad
        reply latches an uncertain session and forbids another command.
        """
        with self._lock:
            if self._faulted or self._link_uncertain:
                raise SafetyError('Faulted/uncertain session cannot query *PSC?')
            return self._uint('*PSC?', maximum=1)

    def operation_complete(self) -> bool:
        """One bounded-by-transport ``*OPC?`` completion query, expecting ``1``.

        Intended ONLY for an idle, unarmed, profile-free session. The query
        may WAIT for outstanding instrument operations and so is NOT a
        passive status snapshot. It does not authorize *OPC (write), *WAI,
        triggering, reset, or status-event consumption. The caller supplies
        a finite transport read timeout; a lost reply is terminal and no
        retry or automatic OFF may be sent on the uncertain link.

        This does not prove event-register preservation on all firmware.
        """
        with self._lock:
            if self._faulted or self._link_uncertain:
                raise SafetyError('Faulted/uncertain session cannot query *OPC?')
            if (self._armed or self._profile is not None or
                    self._sequencing or (self._profile_monitor_thread is not None and
                    self._profile_monitor_thread.is_alive())):
                raise SafetyError('*OPC? requires idle, profile-free, unarmed session')
            response = self._query('*OPC?')
            if re.fullmatch(r' *1 *', response) is None:
                self._malformed_value('Unexpected *OPC? completion response')
            return True

    def consume_status_event(self, register: str, *, acknowledge_clear: bool) -> int:
        """Explicitly consume one 8-bit event latch; may clear its contents.

        This is NOT a status snapshot. The caller must expressly acknowledge
        loss of the accumulated events on every invocation. Only a fresh,
        unarmed session is accepted; all three physical outputs must report OFF
        immediately before the destructive query. Validation and the OFF
        preflight happen before the event query. Neither successful reads nor
        failures trigger an automatic second event read or an event clear.

        The OFF check is sequential, not a physical interlock or an atomic
        guarantee against external controllers changing the output afterward.
        Event values cannot be reconstructed after a malformed/lost reply.
        """
        queries = {
            'standard_event': '*ESR?',
            'questionable': 'STAT:QUES:EVEN?',
            'operation': 'STAT:OPER:EVEN?',
        }
        if type(register) is not str or register not in queries:
            raise ValueError('Unsupported status event register')
        if type(acknowledge_clear) is not bool or acknowledge_clear is not True:
            raise ValueError('Explicit acknowledge_clear=True required to consume events')
        with self._lock:
            if self._faulted or self._link_uncertain:
                raise SafetyError('Faulted/uncertain session cannot consume events')
            if self._armed or self._profile is not None:
                raise SafetyError('Event consumption requires an unarmed, profile-free session')
            try:
                states = self._output_states()
            except BaseException:
                self._latch_fault()
                raise
            if set(states) != {1, 2, 3} or any(type(v) is not bool for v in states.values()):
                self._malformed_value('Invalid physical OFF preflight result')
            if any(states.values()):
                raise SafetyError('Cannot consume events: all three outputs must report OFF')
            return self._uint(queries[register], maximum=255)

    def error(self) -> str:
        with self._lock: return self._query('SYST:ERR?')

    def check_errors(self) -> str:
        """Consume exactly one SCPI error; fail closed on unexpected replies.

        A well-formed nonzero SCPI error while armed causes one verified
        shutdown attempt. A malformed reply can indicate lost framing; no
        follow-up command is sent on that connection. Never claim OFF unless
        the instrument has acknowledged and verified it.
        """
        with self._lock:
            response = self.error()
            try:
                code, message = response.split(',', 1)
                code = int(code.strip())
                if not message.strip():
                    raise ValueError('missing message')
            except (AttributeError, ValueError, TypeError):
                self._latch_fault(link_uncertain=True)
                raise InstrumentError(
                    f'Malformed SCPI error response: {response!r}; '
                    'UNKNOWN OUTPUT STATE (link uncertain)')
            if code != 0:
                was_armed = self._armed
                self._latch_fault()
                if was_armed:
                    if self._try_confirm_fault_shutdown():
                        raise InstrumentError(
                            f'Instrument error: {response}; output OFF confirmed by readback')
                    raise InstrumentError(
                        f'Instrument error: {response}; UNKNOWN OUTPUT STATE '
                        '(shutdown unconfirmed)')
                raise InstrumentError(
                    f'Instrument error: {response}; output state not checked '
                    '(no active profile)')
            return response

    def beep(self):
        with self._lock:
            self._require_ready()
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: only output switching is mutable')
            self._write('SYST:BEEP')

    def close(self, *, return_local: bool = True):
        """Stop the monitor, then attempt OFF; lost-link exceptions propagate."""
        self.stop_profile_monitor()
        with self._lock:
            try: self.emergency_off()
            finally:
                self._armed = False
                try:
                    if return_local: self._write('SYST:LOC')
                finally: self._io.close()

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb):
        self.close()
