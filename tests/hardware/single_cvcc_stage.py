"""R3.5.6 CH1-only Independent CV/CC qualification core (offline-tested).

Not exposed through the hardware CLI until a separate operator-gate/transport
integration and pre-energization review. All PSU operations use the core driver.
"""
from __future__ import annotations

import math

from bench_config import H03SingleCVCC
from channel_stage import H03Scope
from common import StageAdapter, operator_gate, require_outputs
from powered_display import comparison_table
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError


class SingleCVCCScope(H03Scope):
    """Narrow the legacy H03 SCPI envelope to one approved CH1-only sequence."""
    MAX_COMMANDS = 1400
    MAX_SECONDS = 240

    def __init__(self, transport, evidence, point: H03SingleCVCC):
        point.validate()
        super().__init__(transport, evidence, start_channel=1)
        self.point = point
        self.enabled_by_gate = False
        self._writes = {'SYST:REM', 'SYST:LOC', 'OUTP 0',
                        'OUTP:SER 0', 'OUTP:PARALLEL 0', 'OUTP:TRAC 0',
                        'OUTP:TIM:STAT 0', 'CHAN:OUTP 0',
                        *(f'INST:NSEL {ch}' for ch in (1, 2, 3)),
                        *(f'VOLT:LIM {v:.2f}' for v in point.voltage_cap_v),
                        'VOLT:LIM:STAT 1', 'CURR 0', 'VOLT 0',
                        *(f'CURR {v:.3f}' for v in (*point.initial_current_a,
                                                     point.cv_current_limit_a,
                                                     point.cc_current_limit_a)),
                        *(f'VOLT {v:.2f}' for v in (*point.initial_voltage_v,
                                                     point.cv_voltage_v,
                                                     point.cc_voltage_v))}

    def _validate(self, command, query):
        if query:
            # Keep the established H03 read-only diagnostic set, but do not
            # inherit its broad, hard-coded write setpoints.
            StageAdapter._validate(self, command, True)
            return
        if command == 'CHAN:OUTP 1':
            if self.selected != 1 or self.authorized_channel != 1 or not self.enabled_by_gate:
                raise SafetyError('CH1 output ON requires a current point-specific operator gate')
        elif command not in self._writes:
            raise SafetyError(f'Single CV/CC command outside configured point: {command}')
        if command.startswith(('VOLT ', 'CURR ', 'VOLT:LIM ')) and self.selected != 1:
            # During profile apply, CH2/CH3 may receive ONLY their safe
            # initial settings and caps; no powered point may use these channels.
            idx = self.selected - 1
            allowed = {'CURR 0', 'VOLT 0', f'VOLT:LIM {self.point.voltage_cap_v[idx]:.2f}',
                       f'CURR {self.point.initial_current_a[idx]:.3f}',
                       f'VOLT {self.point.initial_voltage_v[idx]:.2f}'}
            if command not in allowed:
                raise SafetyError('CH2/CH3 powered point or unexpected setpoint forbidden')


def _profile(point):
    return Profile('H03 single CH1 CV CC configuration', tuple(
        ChannelProfile(point.voltage_cap_v[ch], point.current_cap_a[ch],
                       point.initial_voltage_v[ch], point.initial_current_a[ch],
                       expected_regulation='auto') for ch in range(3)),
        wiring='independent', lock_front_panel=False, verify_every_s=3600.0)


def run_single_cvcc(transport, evidence, load, point: H03SingleCVCC):
    """Execute two explicitly gated CH1 points; return status, never auto-retry.

    Caller MUST separately confirm bench connections and unloaded wiring before
    opening either transport. This function does not authorize CLI execution.
    """
    point.validate()
    if load is None or not hasattr(load, 'input_state'):
        raise ValueError('verified KEL103 load object is required')
    if not getattr(load, 'transport', '').startswith('USB'):
        # A simulated load may set transport to 'USB serial (simulated)'.
        raise ValueError('single CV/CC requires the explicitly reviewed KEL103 USB link')
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: SingleCVCCScope(channel, evidence, point))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', 'CH1 CV/CC did not complete'
    started_profile = False
    try:
        load.identify()
        if load.input_state() != 'OFF':
            raise SafetyError('KEL103 INPUT must be OFF at the initial gate')
        # Inspect the queue at the entry boundary. Subsequent transfers are
        # already followed by automatic SYST:ERR? in StageAdapter.
        original = dev.diagnostic_query('SYST:ERR?')
        if int(original.split(',', 1)[0].strip()) != 0:
            raise SafetyError(f'pre-existing PSU error queue: {original}')
        # Bootstrap the driver's identity/Remote session before the first
        # explicit output-state scan; querying OUTP:SER? is read-only.
        if int(dev.diagnostic_query('OUTP:SER?')) != 0:
            raise SafetyError('Series mode active before initial OFF inspection')
        require_outputs(dev)
        evidence.add('initial_off_confirmed', channels=[1, 2, 3], load='OFF')
        started_profile = True
        dev.apply_profile(_profile(point), confirmed_wiring='independent')
        require_outputs(dev)
        evidence.add('single_cvcc_profile', channel=1, config_point=vars(point),
                     max_power_w=point.max_power_w)
        for mode, voltage, current, resistance in (
            ('CV', point.cv_voltage_v, point.cv_current_limit_a, point.cv_resistance_ohm),
            ('CC', point.cc_voltage_v, point.cc_current_limit_a, point.cc_resistance_ohm),
        ):
            # Keep the load disconnected from the PSU electronically while
            # applying setpoints and changing CR.
            require_outputs(dev)
            if load.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT not OFF between CV/CC points')
            dev.set_channel(1, voltage=voltage, current=current)
            require_outputs(dev)
            connect = (f'CONFIRM H03-SINGLE CH1 {mode} CR={resistance:g} '
                       'WIRED=CH1_ONLY PSU=OFF LOAD=OFF')
            operator_gate(connect, 'Check KEL103 polarity + to CH1+, − to CH1−; '
                          'CH2/CH3 terminals free; PSU all OFF, KEL103 INPUT OFF.', io)
            evidence.add('operator_gate', token=connect, mode=mode,
                         wiring='CH1 only; CH2/CH3 disconnected')
            load.configure_cr(resistance)
            load.set_input(True)
            if load.input_state() != 'ON':
                raise SafetyError('KEL103 INPUT ON did not verify')
            energize = f'CONFIRM H03-SINGLE CH1 {mode} ENABLE PSU=CH1_ONLY LOAD=CR{resistance:g}'
            operator_gate(energize, 'KEL103 INPUT ON verified; approve one low-power '
                          'CH1 output ON. CH2/CH3 remain OFF.', io)
            evidence.add('operator_gate', token=energize, mode=mode,
                         authorized_output='CH1 only')
            io.authorized_channel, io.enabled_by_gate = 1, True
            try:
                dev.output(1, True)
            finally:
                io.enabled_by_gate = False
            require_outputs(dev, {1: True, 2: False, 3: False})
            if load.input_state() != 'ON':
                raise SafetyError('KEL103 INPUT changed after PSU enable')
            sample = dev.read_channel(1)
            observed = load.measurements()
            if not sample.enabled or sample.regulation != mode:
                raise SafetyError(f'CH1 regulation mismatch: expected {mode}, got {sample.regulation}')
            if any(not math.isfinite(float(value)) or float(value) < 0 for value in observed.values()):
                raise SafetyError('KEL103 returned invalid measurement')
            expected_voltage = voltage if mode == 'CV' else current * resistance
            expected_current = voltage / resistance if mode == 'CV' else current
            if (abs(observed['kel103_voltage_v'] - expected_voltage) > 0.20 or
                    abs(observed['kel103_current_a'] - expected_current) > 0.04):
                raise SafetyError(f'{mode} measured point outside reviewed tolerance')
            if (abs(observed['kel103_voltage_v'] - sample.voltage_v) > 0.06 or
                    abs(observed['kel103_current_a'] - sample.current_a) > 0.03):
                raise SafetyError('PSU/KEL103 measurement disagreement')
            if (sample.power_w > point.max_power_w or
                    observed['kel103_power_w'] > point.max_power_w or
                    abs(observed['kel103_power_w'] - observed['kel103_voltage_v'] *
                        observed['kel103_current_a']) > 0.31):
                raise SafetyError('CV/CC measured power outside approved envelope')
            evidence.add('single_cvcc_measurement', mode=mode, channel=1,
                         voltage_set_v=voltage, current_set_a=current,
                         resistance_ohm=resistance, regulation=sample.regulation,
                         psu_voltage_v=sample.voltage_v, psu_current_a=sample.current_a,
                         psu_power_w=sample.power_w, **observed)
            display = f'CONFIRM H03-SINGLE CH1 {mode} DISPLAY=VERIFIED KEL=CR INPUT=ON'
            view = comparison_table(1, mode, psu_v=sample.voltage_v,
                                    psu_i=sample.current_a, psu_p=sample.power_w,
                                    kel_v=observed['kel103_voltage_v'],
                                    kel_i=observed['kel103_current_a'],
                                    kel_p=observed['kel103_power_w'])
            operator_gate(display, view, io, powered_timeout_s=120)
            evidence.add('operator_display_confirmation', token=display, mode=mode)
            dev.output(1, False)
            io.authorized_channel = None
            require_outputs(dev)
            load.set_input(False)
            if load.input_state() != 'OFF':
                raise SafetyError('KEL103 INPUT OFF did not verify')
            off_gate = f'CONFIRM H03-SINGLE CH1 {mode} PSU=OFF LOAD=OFF'
            operator_gate(off_gate, 'Physically verify CH1/CH2/CH3 OFF and KEL INPUT OFF.', io)
            evidence.add('operator_gate', token=off_gate, mode=mode,
                         psu='OFF', load='OFF')
        dev.emergency_off()
        require_outputs(dev)
        dev.diagnostic_write('INST:NSEL 1')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'CH1 CV/CC both verified; all PSU outputs and load INPUT OFF'
    except BaseException as exc:
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc),
                     psu_link_live=io.live, load_link_live=getattr(load, 'live', False))
        outcome = 'ABORTED' if not io.live or not getattr(load, 'live', False) else 'FAIL'
        reason = f'{type(exc).__name__}: {exc}; physical OFF verification required'
        # Refuse any automatic mutation before a validated OFF-state gate.
        # Once a profile mutation has begun, attempt OFF only on links which
        # are still individually trustworthy. Never retry an uncertain link.
        if not started_profile:
            outcome = 'ABORTED'
            reason = 'Before profile authorization: no automatic writes; physically verify OFF'
        else:
            uncertain = not io.live or not getattr(load, 'live', False)
            if io.live:
                io.cleanup = True
                try:
                    dev.emergency_off()
                    require_outputs(dev)
                    dev.diagnostic_write('SYST:LOC')
                except BaseException as cleanup_exc:
                    uncertain = True
                    evidence.add('cleanup_error', device='psu', exception_type=type(cleanup_exc).__name__)
            if getattr(load, 'live', False):
                try:
                    load.set_input(False)
                    if load.input_state() != 'OFF':
                        raise SafetyError('KEL103 INPUT OFF not verified')
                except BaseException as cleanup_exc:
                    uncertain = True
                    evidence.add('cleanup_error', device='load', exception_type=type(cleanup_exc).__name__)
            if uncertain:
                outcome, reason = 'ABORTED', ('UNKNOWN OUTPUT STATE for one or both devices; '
                                              'physical emergency stop required; no reconnect')
    finally:
        for connection in (transport, load):
            try: connection.close()
            except BaseException:
                outcome, reason = 'ABORTED', 'closing connection failed; verify physically'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
    return outcome
