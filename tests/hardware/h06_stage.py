"""Per-channel output-timer qualification for the Keithley 2231A-30-3.

@file h06_stage.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Exercise timer expiry and safe re-enable using the common driver path.
@details Output Recall power-cycle behavior is a separate, manually authorized test.
"""
from __future__ import annotations

import math
import re
import time

from common import StageAdapter, operator_gate, require_outputs
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError

TIMER_S = 3.0
CHANNELS = {
    1: (2.0, 0.300, 1.00, 0.100),
    2: (3.0, 0.400, 1.50, 0.200),
    3: (4.0, 0.500, 2.00, 0.300),
}
FINAL_TOKEN = 'CONFIRM H06 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'


def approval_token(load_serial_port='/dev/ttyACM0'):
    """Bind the one powered H06 pass to the explicit electronic-load USB port."""
    return (f'CONFIRM H06 INDEPENDENT ALL_OFF LOAD=KEL103 DISCONNECTED '
            f'USB={load_serial_port} BAUD=115200 '
            'CH1=V1.00_I0.100 CH2=V1.50_I0.200 CH3=V2.00_I0.300 '
            'TIMER=3.0S CR=22OHM RECALL=OFF')


class H06Scope(StageAdapter):
    """Allow only H06 profile setup, per-channel timer and authorized output operations."""
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:(?:OPER|QUES):INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|'
        r'MEAS:(?:VOLT|CURR|POW)\?)$')
    _WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:(?:SER|PARALLEL|TRAC) 0|'
        r'INST:NSEL [123]|CHAN:OUTP [01]|OUTP:TIM:STAT [01]|'
        r'OUTP:TIM:DEL 3\.0|VOLT (?:0|1\.00|1\.50|2\.00)|'
        r'CURR (?:0|0\.100|0\.200|0\.300)|'
        r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')
    MAX_COMMANDS = 2400
    MAX_SECONDS = 300
    _CLEANUP_QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|'
        r'STAT:OPER:INST:ISUM[123]:COND\?|OUTP:TIM:(?:STAT|DEL)\?)$')
    _CLEANUP_WRITE = re.compile(
        r'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:TIM:STAT 0|INST:NSEL [123]|CHAN:OUTP 0)$')

    def _validate(self, command, query):
        super()._validate(command, query)
        if command == 'CHAN:OUTP 1' and (
                self.authorized_channel != self.selected or
                any(value for channel, value in self.outputs.items()
                    if channel != self.selected)):
            raise SafetyError('H06 can enable only the explicitly gated channel')


def _profile():
    return Profile('H06 output timer qualification', tuple(
        ChannelProfile(*CHANNELS[ch]) for ch in (1, 2, 3)),
        wiring='independent', lock_front_panel=False,
        verify_every_s=3600.0, access='control')


def _check_load(load, channel, source_voltage_v, enabled):
    load.target_channel = channel
    values = load.measurements()
    voltage = values['kel103_voltage_v']
    current = values['kel103_current_a']
    power = values['kel103_power_w']
    if not all(math.isfinite(value) and value >= 0 for value in (voltage, current, power)):
        raise SafetyError('KEL103 returned an invalid measurement')
    if enabled:
        expected_current = source_voltage_v / 22.0
        if (abs(voltage - source_voltage_v) > 0.06 or
                abs(current - expected_current) > 0.03 or
                abs(power - voltage * current) > 0.31):
            raise SafetyError(f'CH{channel} Keithley/KEL103 readings disagree')
        if current < 0.01:
            raise SafetyError(f'CH{channel} load current is too low while enabled')
    elif voltage > 0.10 or current > 0.01 or power > 0.01:
        raise SafetyError(f'CH{channel} KEL103 still measures output after timer expiry')
    return values


def _wait_timer_off(dev, channel, started, evidence):
    deadline = started + TIMER_S + 1.0
    while time.monotonic() <= deadline:
        dev.diagnostic_write(f'INST:NSEL {channel}')
        if not dev._bool(dev.diagnostic_query('CHAN:OUTP?')):
            elapsed = time.monotonic() - started
            if elapsed < TIMER_S - 0.50:
                raise SafetyError(f'CH{channel} timer expired early at {elapsed:.3f} s')
            evidence.add('timer_expiry', channel=channel, configured_s=TIMER_S,
                         elapsed_s=elapsed, output_state='OFF')
            return elapsed
        time.sleep(0.10)
    raise TimeoutError(f'CH{channel} output timer did not turn the output OFF')


def h06(transport, evidence, load):
    """Run the bounded timer check on each independent channel; no mains cycle."""
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H06Scope(channel, evidence))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'incomplete H06 timer pass'
    try:
        identity = dev.identity()
        if '2231A-30-3' not in identity:
            raise SafetyError('unexpected Keithley identity')
        if 'KEL103' not in load.identify().upper() or load.input_state() != 'OFF':
            raise SafetyError('KEL103 identity/input preflight failed')
        baseline = dev.diagnostic_query('SYST:ERR?')
        code, message = baseline.split(',', 1)
        evidence.add('instrument_error_queue_baseline', response=baseline,
                     clean=int(code.strip()) == 0 and bool(message.strip()))
        if int(code.strip()) != 0 or not message.strip():
            raise SafetyError(f'pre-existing Keithley error: {baseline}')
        initial = require_outputs(dev)
        if any(initial.values()):
            raise SafetyError(f'outputs must be OFF before H06: {initial}')
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(mode)):
                raise SafetyError('H06 requires independent output mode')
        evidence.add('output_recall_preflight', state='OFF',
                     source='operator panel confirmation', power_cycle_test='NOT_RUN')
        dev.apply_profile(_profile())
        dev.verify_profile()
        evidence.add('profile', name='H06 output timer qualification',
                     channels={str(ch): {'voltage_v': CHANNELS[ch][2],
                                         'current_a': CHANNELS[ch][3],
                                         'voltage_cap_v': CHANNELS[ch][0],
                                         'current_cap_a': CHANNELS[ch][1]}
                               for ch in CHANNELS},
                     timer_s=TIMER_S, load='KEL103 CR 22 ohm')

        for channel in (1, 2, 3):
            if channel == 1:
                token = 'CONFIRM H06 CONNECT CH1 ALL_OFF LOAD=OFF'
                prompt = ('Keithley CH1–CH3 OFF; KEL103 INPUT OFF. Подключите KEL103 '
                          'только к CH1 (+ к +, − к −); CH2/CH3 и остальные клеммы свободны.')
            else:
                previous = channel - 1
                token = f'CONFIRM H06 MOVE CH{previous}->CH{channel} ALL_OFF LOAD=OFF'
                prompt = (f'Keithley CH1–CH3 OFF; KEL103 INPUT OFF. Отключите KEL103 '
                          f'от CH{previous}, проверьте отсутствие напряжения и подключите '
                          f'только к CH{channel} (+ к +, − к −).')
            operator_gate(token, prompt, io)
            evidence.add('operator_gate', token=token, channel=channel,
                         action='connect' if channel == 1 else 'move',
                         load='KEL103 CR 22 ohm', outputs='OFF')
            load.configure_h04p_cr(22.0)
            dev.timer(channel, TIMER_S)
            if load.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT must be OFF before output timer test')
            load.set_input(True)
            io.authorized_channel = channel
            dev.output(channel, True)
            started = time.monotonic()
            require_outputs(dev, {ch: ch == channel for ch in (1, 2, 3)})
            source = dev.read_channel(channel)
            if not source.enabled:
                raise SafetyError(f'CH{channel} failed to turn ON')
            live_load = _check_load(load, channel, source.voltage_v, source.enabled)
            evidence.add('timer_live_measurement', channel=channel,
                         keithley_voltage_v=source.voltage_v,
                         keithley_current_a=source.current_a,
                         keithley_power_w=source.power_w, **live_load)
            elapsed = _wait_timer_off(dev, channel, started, evidence)
            require_outputs(dev)
            load_off_measurement = _check_load(load, channel, source.voltage_v, False)
            evidence.add('timer_off_measurement', channel=channel, elapsed_s=elapsed,
                         output_state='OFF', **load_off_measurement)

            dev.timer(channel, None)
            io.authorized_channel = channel
            dev.output(channel, True)
            require_outputs(dev, {ch: ch == channel for ch in (1, 2, 3)})
            time.sleep(0.50)
            require_outputs(dev, {ch: ch == channel for ch in (1, 2, 3)})
            reenabled = dev.read_channel(channel)
            _check_load(load, channel, reenabled.voltage_v, reenabled.enabled)
            evidence.add('timer_reenable', channel=channel, timer='OFF',
                         output_state='ON', stable_s=0.50,
                         voltage_v=reenabled.voltage_v, current_a=reenabled.current_a)
            dev.output(channel, False)
            require_outputs(dev)
            load.set_input(False)
            dev.timer(channel, None)

        token = 'CONFIRM H06 DISCONNECT CH3 ALL_OFF LOAD=OFF'
        operator_gate(token, 'Убедитесь, что все выходы Keithley и INPUT KEL103 OFF; '
                      'отсоедините нагрузку от CH3, проверьте отсутствие напряжения.', io)
        evidence.add('operator_gate', token=token, action='disconnect', channel=3,
                     outputs='OFF', load_input='OFF')
        if any(require_outputs(dev).values()) or load.input_state() != 'OFF':
            raise SafetyError('final OFF readback failed')
        timer_states = {}
        for channel in (1, 2, 3):
            dev.diagnostic_write(f'INST:NSEL {channel}')
            timer_states[channel] = dev._bool(dev.diagnostic_query('OUTP:TIM:STAT?'))
        if any(timer_states.values()):
            raise SafetyError(f'timer state remained enabled after H06: {timer_states}')
        evidence.add('final_timer_state', states=timer_states, outcome='PASS')
        dev.diagnostic_write('SYST:LOC')
        outcome = 'PARTIAL'
        reason = ('all per-channel timer expiry and re-enable checks passed; '
                  'Output Recall AC power-cycle remains separately gated')
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or isinstance(
            exc, (TimeoutError, KeyboardInterrupt, InterruptedError)) else 'FAIL')
        reason = f'H06 stopped: {type(exc).__name__}: {exc}; verify outputs and load OFF'
        evidence.add('stage_error', exception_type=type(exc).__name__,
                     message=str(exc), live_link=io.live)
        if io.live:
            try:
                # Keep emergency traffic inside the adapter's shutdown allow-list.
                io.cleanup = True
                dev.emergency_off()
                require_outputs(dev)
                if getattr(load, 'live', True):
                    load.set_input(False)
                # The public timer API requires an armed profile. After emergency_off
                # disarms it, use the explicit debug bypass through the same driver
                # dispatch; H06Scope still restricts this to timer OFF and channel select.
                dev.debug_bypass_profile = True
                for channel in (1, 2, 3):
                    dev.diagnostic_write(f'INST:NSEL {channel}')
                    dev.diagnostic_write('OUTP:TIM:STAT 0')
                dev.debug_bypass_profile = False
                dev.diagnostic_write('SYST:LOC')
            except BaseException:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT/LOAD/TIMER STATE'
                evidence.add('cleanup_error', outcome='UNKNOWN')
    finally:
        try: transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'Keithley transport close failed; manual OFF required'
        try: load.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'KEL103 transport close failed; manual INPUT OFF required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware,
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED',
                     scope='timers-only; Output Recall power-cycle not run')
    return outcome
