"""SCPI driver. Every operation goes through an active immutable safety profile.

NOTE: The physical Series/Parallel interconnection is OUTSIDE the PSU.
The API does not guarantee a fail-safe output after a cable/PC/network failure.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import threading
import time
from typing import Iterable
from .profile import Profile, LimitsError, ProfileError
from .transport import Transport, TransportError

class InstrumentError(RuntimeError):
    pass
class SafetyError(InstrumentError):
    pass

@dataclass(frozen=True)
class Reading:
    """Sequential channel readback, not an atomic instrument snapshot.

    Firmware 1.04-1.04 has been observed to omit CH3 OPER CV/CC bits.
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
    def __init__(self, transport: Transport):
        self._io = transport
        self._lock = threading.RLock()
        self._profile: Profile | None = None
        self._armed = False
        self._faulted = False
        self._last_verify = 0.0
        self._idn = None
        self._sequencing = False
        self._expected_setpoints = {}
        self._expected_outputs = {ch: False for ch in (1, 2, 3)}

    @property
    def instrument_id(self) -> str | None: return self._idn
    @property
    def profile(self) -> Profile | None: return self._profile
    @property
    def armed(self) -> bool: return self._armed
    @property
    def faulted(self) -> bool: return self._faulted

    def _write(self, command: str):
        try: self._io.write(command)
        except Exception:
            self._faulted, self._armed = True, False
            raise

    def _query(self, command: str) -> str:
        try: return self._io.query(command)
        except Exception:
            self._faulted, self._armed = True, False
            raise
    def _num(self, command: str) -> float:
        s = self._query(command)
        try:
            val = float(s)
            if not math.isfinite(val): raise ValueError("not finite")
            return val
        except ValueError as exc:
            raise InstrumentError(f"Unexpected response to {command}: {s!r}") from exc

    @staticmethod
    def _bool(value: str) -> bool:
        x = value.strip().upper()
        if x in ('1', 'ON'): return True
        if x in ('0', 'OFF'): return False
        raise InstrumentError(f"Invalid boolean response: {value!r}")

    def identity(self) -> str:
        with self._lock:
            ans = self._query('*IDN?')
            parts = [part.strip() for part in ans.split(',')]
            if len(parts) != 4 or parts[0].lower() != 'keithley instruments' or parts[1].upper() != '2231A-30-3' or not parts[3]:
                self._faulted, self._armed = True, False
                raise InstrumentError('Not a valid Keithley 2231A-30-3 identity')
            if self._idn is not None and ans != self._idn:
                self._faulted, self._armed = True, False
                raise InstrumentError('Instrument identity changed during connection')
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

    def _trip(self, why: str):
        self._faulted = True
        self._armed = False
        try: self._off_confirmed()
        except Exception: pass
        raise SafetyError(f'{why}; emergency output-off attempted (not guaranteed)')

    def _output_states(self) -> dict[int, bool]:
        """Read per-channel ON bits; global status has ambiguous mixed-state semantics."""
        states = {}
        for ch in (1, 2, 3):
            mask = self._num(f'STAT:OPER:INST:ISUM{ch}:COND?')
            if mask != int(mask) or not 0 <= mask <= 255:
                raise InstrumentError('Invalid operation condition register')
            states[ch] = bool(int(mask) & 8)
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
            self._armed = False
            try:
                # A previous run may have returned the instrument to Local.
                # Re-enter Remote before sending shutdown SCPI commands.
                self._write('SYST:REM')
                if not self._off_confirmed():
                    raise SafetyError('A channel remains ON after OUTP 0')
                self._expected_outputs = {ch: False for ch in (1, 2, 3)}
            except BaseException:
                self._faulted = True
                raise

    def apply_profile(self, profile: Profile, *, confirmed_wiring: str | None = None) -> None:
        """Arms profile with all outputs OFF. For series/parallel physical wires must
        be manually confirmed EVERY time, e.g. confirmed_wiring='series'.
        Always replaces an existing profile only after reconfiguring from outputs OFF.
        """
        if not isinstance(profile, Profile): raise ProfileError('expected Profile')
        if profile.wiring in ('series', 'parallel') and confirmed_wiring != profile.wiring:
            raise SafetyError('Confirm physically installed CH1/CH2 wiring explicitly')
        with self._lock:
            if self._armed:
                raise SafetyError('Active profile immutable. Call emergency_off() before replacing it')
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
                if not self._off_confirmed():
                    self._trip('Output could not be switched OFF')
                # When changing wiring, remove physical jumpers while outputs OFF.
                self._write('OUTP:SER 0')
                self._write('OUTP:PARALLEL 0')
                self._write('OUTP:TRAC 0')
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
                elif profile.wiring == 'parallel': self._write('OUTP:PARALLEL 1')
                elif profile.wiring == 'tracking': self._write('OUTP:TRAC 1')
                self._profile = profile
                self._check_wiring_mode()
                if profile.lock_front_panel:
                    self.remote(lock_panel=True)
                if any(self._output_states().values()):
                    raise SafetyError('Unexpected output ON while applying profile')
                self._last_verify = time.monotonic()
                self._armed = True  # Outputs remain OFF until explicitly enabled.
            except BaseException:
                self._profile = None
                self._armed = False
                self._faulted = True
                try: self._write('OUTP 0')
                except Exception: pass
                raise

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

    def verify_profile(self):
        with self._lock:
            self._require_ready()
            try:
                self._check_wiring_mode()
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

    def set_channel(self, ch: int, *, voltage: float | None = None,
                    current: float | None = None):
        with self._lock:
            self._require_ready(); self._check_channel(ch)
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: voltage/current setpoints are frozen')
            if self._profile.wiring != 'independent':
                raise SafetyError('CH1/CH2 combined or tracked: setpoints frozen; reapply profile with outputs OFF')
            spec = self._profile.channels[ch - 1]
            for val, cap, label, step in ((voltage, spec.max_voltage, 'voltage', .01),
                                          (current, spec.max_current, 'current', .001)):
                if val is not None:
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val):
                        raise LimitsError(f'{label}: must be finite number')
                    if val < 0 or val > cap or abs(val/step - round(val/step)) > 1e-6:
                        raise LimitsError(f'CH{ch}: {label}={val} exceeds limit {cap} or resolution {step}')
            # Verify hardware controls before writing; protects against external adjustments.
            self.verify_profile()
            try:
                self._select(ch)
                # Current first, then voltage; both are bounded by profile.
                if current is not None: self._write(f'CURR {current:.3f}')
                if voltage is not None: self._write(f'VOLT {voltage:.2f}')
                old_v, old_i = self._expected_setpoints[ch]
                expected = (old_v if voltage is None else voltage,
                            old_i if current is None else current)
                self._check_setpoints(ch, spec, expected)
                self._select(ch)
                if voltage is not None and abs(self._num('VOLT?') - voltage) > .011:
                    raise SafetyError('Voltage set command was not applied')
                if current is not None and abs(self._num('CURR?') - current) > .002:
                    raise SafetyError('Current set command was not applied')
                self._expected_setpoints[ch] = expected
            except Exception as exc:
                self._trip(f'Setting CH{ch} failed: {exc}')

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
            if enabled and self._profile.wiring in ('series', 'parallel'):
                raise SafetyError('Combined setpoint semantics require H09/H10 qualification before enabling')
            if enabled and not confirm_all_channels_safe:
                raise SafetyError('Global OUTP 1 may turn on CH3. Confirm ALL connected loads.')
            if enabled: self.verify_profile()
            try:
                self._write(f'OUTP {1 if enabled else 0}')
                if self._bool(self._query('OUTP:STAT:ALL?')) != bool(enabled):
                    raise SafetyError('Global output state mismatch')
                if any(state != enabled for state in self._output_states().values()):
                    raise SafetyError('Per-channel output state mismatch')
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

    def timer(self, channel: int, seconds: float | None):
        """Configure channel's PSU OFF timer; triggering depends on PSU output activation."""
        with self._lock:
            self._require_ready(); self._check_channel(channel)
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: timer settings are frozen')
            if seconds is not None and (not math.isfinite(seconds) or not 0.1 <= seconds <= 99999.9):
                raise ValueError('timer range 0.1..99999.9 seconds')
            if self._profile.wiring == 'tracking' and channel in (1,2):
                raise SafetyError('Tracking channel timer is coupled; do not configure separately')
            try:
                self.verify_profile()
                self._select(channel)
                if seconds is None:
                    self._write('OUTP:TIM:STAT 0')
                else:
                    self._write(f'OUTP:TIM:DEL {seconds:.1f}')
                    if abs(self._num('OUTP:TIM:DEL?') - seconds) > .11:
                        raise SafetyError('timer delay readback mismatch')
                    self._write('OUTP:TIM:STAT 1')
                if self._bool(self._query('OUTP:TIM:STAT?')) != (seconds is not None):
                    raise SafetyError('timer state readback mismatch')
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
            if (not with_regulation and self._profile.channels[ch-1].expected_regulation != 'auto'):
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
                    operation_mask = int(self._num(f'STAT:OPER:INST:ISUM{ch}:COND?'))
                    questionable_mask = int(self._num(f'STAT:QUES:INST:ISUM{ch}:COND?'))

                    def decode_regulation(mask: int) -> str | None:
                        cv, cc = bool(mask & 1), bool(mask & 2)
                        if cv and cc:
                            return 'UNKNOWN'
                        if not cv and not cc:
                            return None
                        return 'CV' if cv else 'CC'

                    operation_mode = decode_regulation(operation_mask)
                    questionable_mode = decode_regulation(questionable_mask)
                    if ('UNKNOWN' in (operation_mode, questionable_mode) or
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
                spec = self._profile.channels[ch - 1]
                combined = self._profile.wiring in ('series', 'parallel') and ch == 1
                if self._profile.wiring in ('series', 'parallel') and ch == 2:
                    raise SafetyError('CH2 metering is replaced by combined CH1+CH2; query CH1')
                if combined:
                    other = self._profile.channels[1]
                    # In combine mode manual says CH1 metering is total V (series)
                    # or total I (parallel), NOT a separate channel measurement.
                    v_cap = spec.max_voltage + other.max_voltage if self._profile.wiring == 'series' else min(spec.max_voltage, other.max_voltage)
                    i_cap = min(spec.max_current, other.max_current) if self._profile.wiring == 'series' else spec.max_current + other.max_current
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
                               voltage_set_v=None if combined else voltage_set,
                               current_limit_set_a=None if combined else current_set,
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
                        'status_byte': int(self._num('*STB?')),
                        'questionable_condition': int(self._num('STAT:QUES:COND?')),
                        'operation_condition': int(self._num('STAT:OPER:COND?'))}
            except Exception as exc:
                self._trip(f'Status query failed: {exc}')

    def error(self) -> str:
        with self._lock: return self._query('SYST:ERR?')

    def beep(self):
        with self._lock:
            self._require_ready()
            if self._profile.access == 'power_only':
                raise SafetyError('power_only: only output switching is mutable')
            self._write('SYST:BEEP')

    def close(self, *, return_local: bool = True):
        """Attempts OFF even if profile faulted; exceptions on lost link propagate."""
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
