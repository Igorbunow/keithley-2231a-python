"""Shared OFF-only investigation before Series/Parallel qualification.

@file combined_off_stage.py
@author Project contributors
@date 2026-10-27
@version 0.2.0
@brief Record combined-mode digital semantics without energizing terminals.
@details Uses the shared driver diagnostic path and stage command policy.
         Successful investigation is PARTIAL, never a powered PASS.
"""
from __future__ import annotations

import re

from common import StageAdapter, channel_snapshot, require_outputs, selected_settings
from keithley2231a import ChannelProfile, Keithley2231A, Profile, SafetyError

def tokens(test_id):
    """Return distinct operator gates for the selected combined mode."""
    if test_id not in ('H09', 'H10'):
        raise ValueError('Combined OFF diagnostics support H09/H10 only')
    return (f'CONFIRM {test_id} OFF_DIAGNOSTIC INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
            'DMM=DISCONNECTED SCOPE=DISCONNECTED NO_JUMPERS RECALL=OFF',
            f'CONFIRM {test_id} CH1=OFF CH2=OFF CH3=OFF LOAD=OFF NO_JUMPERS')


def preflight(port, test_id='H09'):
    """Print the OFF-only scope without opening any instrument connection."""
    token, _ = tokens(test_id)
    mode = 'SER' if test_id == 'H09' else 'PARALLEL'
    print(f'Keithley {port}, CP2102 10C4:EA60, USB–TTL 8N1 (скорость из bench config).\n'
          f'{test_id}: только OFF-диагностика {mode}, БЕЗ силовой сборки.\n'
          'Все CH1/CH2/CH3 OFF; клеммы свободны; KEL103 INPUT OFF и отключена.\n'
          'DMM и осциллограф отключены; общей шины и перемычек нет; Output Recall=Off.\n'
          'CH1/2/3 V-set=1.00/1.50/2.00 V; I-set=0.100/0.200/0.300 A;\n'
          'V-cap=2.00/3.00/4.00 V; software I-cap=0.300/0.400/0.500 A.\n'
          f'Снимок исходных настроек; независимый профиль; OUTP:{mode} 1 →\n'
          f'снимок всех каналов → OUTP:{mode} 0 → снимок; восстановление профиля.\n'
          'В объединённом режиме уставки не менять. ON запрещён политикой сценария.\n'
          'Разрешены REM/LOC, OUTP 0, отключение таймеров и других combined modes,\n'
          'профильные VOLT/CURR/VOLT:LIM и queries. После каждой команды SYST:ERR?.\n'
          'Без *CLS/reset/автоповтора. Бюджет 2000 команд/120 s.\n'
          'Штатная мощность 0 W; при ошибочном ON уставки дают до 0.10/0.30/0.60 W;\n'
          'профильные границы 0.60/1.20/2.00 W. При ошибке STOP, попытка OFF только\n'
          'по живой связи; вручную проверить OFF, при неопределённости сетевой OFF.\n'
          'Результат максимум PARTIAL: силовой тест требует отдельного gate.\n'
          f'Токен:\n{token}')


class CombinedOffScope(StageAdapter):
    """Permit bounded OFF configuration only, including one explicitly selected combined mode toggle."""
    MAX_SECONDS = 120
    _QUERY = re.compile(
        r'^(?:\*IDN\?|SYST:ERR\?|INST:NSEL\?|CHAN:OUTP\?|OUTP:STAT:ALL\?|'
        r'OUTP:(?:SER|PARALLEL|TRAC)\?|OUTP:TIM:(?:STAT|DEL)\?|'
        r'STAT:OPER:INST:ISUM[123]:COND\?|'
        r'VOLT\?|CURR\?|VOLT:LIM\?|VOLT:LIM:STAT\?|MEAS:(?:VOLT|CURR)\?)$')
    def __init__(self, transport, evidence, mode='SER'):
        if mode not in ('SER', 'PARALLEL'):
            raise ValueError('Unsupported combined mode')
        super().__init__(transport, evidence)
        other = 'PARALLEL' if mode == 'SER' else 'SER'
        self._WRITE = re.compile(
            rf'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:{mode} [01]|OUTP:(?:{other}|TRAC) 0|'
            r'INST:NSEL [123]|CHAN:OUTP 0|OUTP:TIM:STAT 0|'
            r'VOLT (?:0|1\.00|1\.50|2\.00)|CURR (?:0|0\.100|0\.200|0\.300)|'
            r'VOLT:LIM (?:2\.00|3\.00|4\.00)|VOLT:LIM:STAT 1)$')
        self._CLEANUP_WRITE = re.compile(
            rf'^(?:SYST:(?:REM|LOC)|OUTP 0|OUTP:{mode} 0|INST:NSEL [123]|CHAN:OUTP 0)$')
        self._CLEANUP_QUERY = self._QUERY


def _snapshot(dev, evidence, phase, combined, mode):
    """Record raw digital semantics; do not guess combined setpoint conversion."""
    if not combined: require_outputs(dev)
    modes = {name: dev.diagnostic_query(f'OUTP:{name}?').strip()
             for name in ('SER', 'PARALLEL', 'TRAC')}
    if modes != {name: str(int(combined and name == mode)) for name in ('SER', 'PARALLEL', 'TRAC')}:
        raise SafetyError(f'Unexpected mode flags: {modes}')
    channels = {}
    for ch in (1, 2, 3):
        if combined:
            dev.diagnostic_write(f'INST:NSEL {ch}')
            channels[str(ch)] = selected_settings(dev, measured=True)
        else:
            channels[str(ch)] = channel_snapshot(dev, ch, measured=True)
        if channels[str(ch)]['CHAN:OUTP?'] != 0:
            raise SafetyError('Selected output is unexpectedly ON during OFF diagnostic')
    if not combined: require_outputs(dev)
    evidence.add(f'{mode.lower()}_off_snapshot', phase=phase, modes=modes, channels=channels,
                 selection_context='keys are requested selections; INST:NSEL? identifies actual register context',
                 individual_output_states_verified=not combined)
    return channels


def run_combined_off(transport, evidence, test_id='H09'):
    """Investigate OFF-only semantics; powered qualification remains pending."""
    tokens(test_id)
    mode = 'SER' if test_id == 'H09' else 'PARALLEL'
    dev = Keithley2231A(transport, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: CombinedOffScope(channel, evidence, mode))
    io = dev.debug_transport
    outcome, reason = 'ABORTED', f'incomplete {test_id} OFF diagnostic'
    try:
        dev.identity()
        baseline = dev.diagnostic_query('SYST:ERR?')
        if int(baseline.split(',', 1)[0]):
            raise SafetyError(f'Pre-existing PSU error: {baseline}')
        require_outputs(dev)
        for flag in ('SER', 'PARALLEL', 'TRAC'):
            if dev.diagnostic_query(f'OUTP:{flag}?').strip() != '0':
                raise SafetyError(f'{test_id} OFF diagnostic requires independent mode')
        evidence.add('initial_settings', channels={
            str(ch): channel_snapshot(dev, ch) for ch in (1, 2, 3)})
        safe_profile = Profile(f'{test_id} OFF diagnostic restore', (
            ChannelProfile(2, .3, 1, .1), ChannelProfile(3, .4, 1.5, .2),
            ChannelProfile(4, .5, 2, .3)), lock_front_panel=False)
        dev.apply_profile(safe_profile, confirmed_wiring='independent')
        before = _snapshot(dev, evidence, 'independent_before', False, mode)
        dev.emergency_off()
        # Only OFF diagnostics bypass the active profile's independent mode guard.
        dev.debug_bypass_profile = True
        try:
            dev.diagnostic_write(f'OUTP:{mode} 1')
            _snapshot(dev, evidence, f'{mode.lower()}_off', True, mode)
            dev.diagnostic_write(f'OUTP:{mode} 0')
        finally:
            dev.debug_bypass_profile = False
        after_mode_off = _snapshot(dev, evidence, 'independent_after_mode_off', False, mode)
        keys = ('VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?', 'OUTP:TIM:STAT?')
        changes = {ch: {key: {'before': before[ch][key], 'after_mode_off': after_mode_off[ch][key]}
                        for key in keys if before[ch][key] != after_mode_off[ch][key]}
                   for ch in before}
        evidence.add(f'{mode.lower()}_mode_effect', changed_settings=changes,
                     limit_register_interpretation='combined mode only; no individual limit claim')
        if any(changes.values()):
            # Combined-mode firmware may rewrite individual settings on exit.
            # Restore the bounded profile before returning control to the operator.
            dev.emergency_off()
            dev.apply_profile(safe_profile, confirmed_wiring='independent')
            evidence.add('independent_profile_restored', expected={
                str(ch): {'VOLT?': spec.initial_voltage, 'CURR?': spec.initial_current,
                          'VOLT:LIM?': spec.max_voltage, 'VOLT:LIM:STAT?': 1.0}
                for ch, spec in enumerate(safe_profile.channels, 1)})
        after = _snapshot(dev, evidence, 'independent_after_restoration', False, mode)
        if any(abs(after[ch][key] - before[ch][key]) > (.0049 if key in ('VOLT?', 'VOLT:LIM?')
                                                         else .00049 if key == 'CURR?' else 0)
               for ch in before for key in keys):
            raise SafetyError('Independent safe profile did not restore exactly')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PARTIAL', (
            f'{mode} OFF diagnostic complete; raw register semantics recorded; '
            'independent safety profile restored; powered scope NOT_RUN')
    except BaseException as exc:
        outcome = ('ABORTED' if not io.live or isinstance(exc, (
            OSError, TimeoutError, KeyboardInterrupt, InterruptedError)) else 'FAIL')
        reason = f'{test_id} OFF diagnostic stopped: {type(exc).__name__}: {exc}'
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc), live_link=io.live)
        if io.live:
            try:
                io.cleanup = True
                dev.debug_bypass_profile = True
                # Global OFF is safe even when channel selection is aliased.
                # Restore independent addressing before confirming individual OFF.
                dev.diagnostic_write('OUTP 0')
                dev.diagnostic_write(f'OUTP:{mode} 0')
                dev.debug_bypass_profile = False
                dev.emergency_off()
                require_outputs(dev)
                dev.diagnostic_write('SYST:LOC')
            except BaseException:
                outcome, reason = 'ABORTED', 'shutdown unconfirmed; UNKNOWN OUTPUT STATE'
    finally:
        try: transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'close failed; manual OFF check required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware, verification='HW UNVERIFIED',
                     scope='OFF diagnostic only; no external wiring or powered measurements')
    return outcome
