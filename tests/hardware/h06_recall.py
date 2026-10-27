"""Manually gated Output Recall qualification using fresh driver sessions.

@file h06_recall.py
@author Project contributors
@date 2026-10-26
@version 0.2.0
@brief Observe startup outputs before applying any profile or shutdown command.
@details Recall/Save Last settings and mains switching are operator actions.
         No load, scope, jumpers, automatic reconnect or undocumented SCPI is used.
"""
from __future__ import annotations

import re

from common import StageAdapter, channel_snapshot, operator_gate, require_outputs
from h06_stage import CHANNELS, H06Scope, _profile
from keithley2231a import Keithley2231A, SafetyError

CASES = (('OFF', '111'), ('ON', '101'), ('ON', '010'), ('ON', '000'))
TOKEN = ('CONFIRM H06 RECALL INDEPENDENT ALL_OFF LOAD=DISCONNECTED '
         'SCOPE=DISCONNECTED NO_JUMPERS RECALL=OFF SAVE_LAST=ON '
         'CH1=V1.00_I0.100 CH2=V1.50_I0.200 CH3=V2.00_I0.300')
RESTORE_TOKEN = 'CONFIRM H06 RECALL RESTORED RECALL=OFF SAVE_LAST=ON ALL_OFF'


class RecallScope(StageAdapter):
    """Permit bounded setup and preserve boot observations before any reconfiguration."""
    _QUERY = H06Scope._QUERY
    _WRITE = H06Scope._WRITE

    def __init__(self, transport, evidence, observing=False):
        super().__init__(transport, evidence)
        self.observing = observing
        self.allowed_on = set()

    def _validate(self, command, query):
        super()._validate(command, query)
        if not query:
            if command.startswith('OUTP:TIM:') and command != 'OUTP:TIM:STAT 0':
                raise SafetyError('Recall checks never arm output timers')
            if command == 'CHAN:OUTP 1' and (
                    self.observing or self.selected not in self.allowed_on):
                raise SafetyError('Output ON is outside the confirmed Recall pattern')
            if self.observing and not (command in ('SYST:REM', 'SYST:LOC', 'OUTP 0',
                                                   'CHAN:OUTP 0') or
                                       re.fullmatch(r'INST:NSEL [123]', command)):
                raise SafetyError('Record startup state before changing configuration')


def preflight(port):
    """Describe the separate Recall subgroup without opening a port."""
    print(f'Output Recall H06: Keithley {port}, 8N1 (скорость из bench config); все выходные клеммы свободны.\n'
          'KEL103 физически отключена, INPUT OFF; режим нагрузки не используется. '
          'Осциллограф, общая шина и перемычки сняты. Independent, все выходы OFF.\n'
          'User Settings: Save Last ON; Output Recall будет вручную OFF/ON по точкам.\n'
          'Уставки: CH1=1.00 V/0.100 A, CH2=1.50 V/0.200 A, CH3=2.00 V/0.300 A; '
          'V-cap=2/3/4 V, software I-cap=0.300/0.400/0.500 A, все таймеры OFF.\n'
          'Четыре сетевых цикла, CH1/CH2/CH3: Recall OFF 111→000; '
          'Recall ON 101→101; Recall ON 010→010; Recall ON 000→000.\n'
          'До каждого цикла отдельный gate разрешает включение пустых выходов. '
          'После проверки исходного состояния UART закрывается без команды OFF. '
          'Затем оператор выключает/включает питание Keithley и сообщает состояние панели.\n'
          'Новая сессия открывается только после подтверждённого сетевого цикла, '
          'с проверкой VID:PID и привязкой к исходному serial. Никаких auto-retry.\n'
          'После старта сначала читаются выходы, уставки, лимиты и измерения; '
          'профиль до наблюдения не применяется. Затем все выходы выключаются.\n'
          'В конце вручную восстановить Output Recall OFF, Save Last ON; подтвердить OFF.\n'
          f'Начальное разрешение:\n{TOKEN}')


def _device(transport, evidence, serial=None, observing=False):
    """Bind each explicitly opened session to the same instrument identity."""
    return Keithley2231A(
        transport, expected_serial=serial, debug=True,
        debug_trace=lambda record: evidence.add('driver_trace', trace=record),
        debug_adapter=lambda channel: RecallScope(channel, evidence, observing))


def _states(mask):
    return {ch: mask[ch - 1] == '1' for ch in (1, 2, 3)}


def _snapshot(dev):
    """Use driver diagnostics without arming a profile or writing output settings."""
    return {str(ch): channel_snapshot(dev, ch, measured=True) for ch in (1, 2, 3)}


def _cycle_gate(case, expected, adapter):
    """Accept any syntactically valid actual panel mask, including a failing result."""
    prefix = f'CONFIRM H06 RECALL CASE={case} POWER_CYCLED PANEL='
    print('UART закрыт без OFF. Выключите питание Keithley, дождитесь погасания дисплея, '
          'включите питание и дождитесь завершения самотеста. Не нажимайте Output On/Off.\n'
          'Провода остаются отключёнными. Запишите фактическое ON/OFF каждого канала '
          '(CH1/CH2/CH3, 1=ON, 0=OFF). При отличии замените PANEL на фактические биты.\n'
          f'Ожидаемое подтверждение:\n{prefix}{expected}')
    while True:
        answer = adapter.operator_input('> ').strip()
        if re.fullmatch(re.escape(prefix) + r'[01]{3}', answer):
            return answer[-3:]
        print('Подтверждение не распознано; ожидаю фактическую маску. Для остановки — Ctrl-C.')


def recall(transport, evidence, reopen):
    """Run four explicit power cycles, preserving evidence before shutdown.

    ``reopen`` is called once per operator-confirmed cycle and must recheck the
    selected USB adapter. It must never retry or discover another serial port.
    """
    dev = _device(transport, evidence)
    matched = False
    outcome, reason = 'ABORTED', 'incomplete Output Recall check'
    try:
        identity = dev.identity()
        matched = True
        serial = identity.split(',')[2].strip()
        if not serial:
            raise SafetyError('Recall cycles require a nonempty instrument serial binding')
        require_outputs(dev)
        for mode in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?'):
            if dev._bool(dev.diagnostic_query(mode)):
                raise SafetyError('Recall qualification requires Independent mode')

        for mode, before in CASES:
            case = f'{mode}_{before}'
            expected = before if mode == 'ON' else '000'
            io = dev.debug_transport
            io.observing, io.allowed_on = False, set()
            dev.apply_profile(_profile(), confirmed_wiring='independent')
            require_outputs(dev)
            dev.diagnostic_write('SYST:LOC')
            token = (f'CONFIRM H06 RECALL CASE={case} RECALL={mode} '
                     'SAVE_LAST=ON ALL_OFF CYCLE=AUTHORIZED')
            operator_gate(token,
                          f'Все выходы OFF, клеммы свободны, KEL103 отключена/INPUT OFF; '
                          f'режим нагрузки не используется. На панели Menu → User Settings '
                          f'установите Output Recall={mode}, Save Last=ON. '
                          f'Точка {case} разрешает включить каналы по маске {before} '
                          'и затем выполнить один сетевой цикл. Пока питание не переключайте.', io)
            evidence.add('recall_menu_gate', case=case, recall=mode, save_last='ON',
                         source='operator panel confirmation', token=token)
            io.allowed_on = {ch for ch, enabled in _states(before).items() if enabled}
            for ch in sorted(io.allowed_on): dev.output(ch, True)
            require_outputs(dev, _states(before))
            evidence.add('recall_before_cycle', case=case, outputs=dev.output_states(),
                         channels=_snapshot(dev))
            dev.diagnostic_write('SYST:LOC')
            # Driver.close() intentionally sends OFF. Close only the already
            # verified UART here so the authorized pre-cycle mask is preserved.
            closing = transport
            transport, dev, matched = None, None, False
            closing.close()
            evidence.add('recall_transport_closed', case=case, outputs_preserved=before)
            panel = _cycle_gate(case, expected, io)
            evidence.add('recall_cycle_gate', case=case, panel=panel,
                         power_cycle='operator confirmed')

            transport = reopen()
            dev = _device(transport, evidence, serial, observing=True)
            dev.identity()
            matched = True
            actual = dev.output_states()
            boot = _snapshot(dev)
            topology = {cmd: dev.diagnostic_query(cmd)
                        for cmd in ('OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?')}
            evidence.add('recall_boot_observation', case=case, panel=panel,
                         expected=_states(expected), actual=actual, channels=boot,
                         topology=topology, profile_applied=False)
            dev.emergency_off()
            require_outputs(dev)
            if actual != _states(expected) or actual != _states(panel):
                raise SafetyError(f'{case} startup state disagrees with expectation or panel: '
                                  f'actual={actual}, panel={panel}, expected={expected}')
            if any(dev._bool(value) for value in topology.values()):
                raise SafetyError(f'{case} restored unexpected output topology')
            for ch, values in boot.items():
                vcap, _, voltage, current = CHANNELS[int(ch)]
                for command, wanted in (('VOLT?', voltage), ('CURR?', current),
                                        ('VOLT:LIM?', vcap)):
                    if abs(values[command] - wanted) > 0.0001:
                        raise SafetyError(f'{case} CH{ch} Save Last mismatch: {command}')
                if values['OUTP:TIM:STAT?']:
                    raise SafetyError(f'{case} CH{ch} timer unexpectedly enabled at startup')
            evidence.add('recall_case_result', case=case, outcome='PASS')

        dev.diagnostic_write('SYST:LOC')
        operator_gate(RESTORE_TOKEN,
                      'Все выходы программно OFF. В User Settings восстановите '
                      'Output Recall=OFF и Save Last=ON. Нагрузка и осциллограф '
                      'остаются физически отключёнными.', dev.debug_transport)
        require_outputs(dev)
        evidence.add('recall_restored', recall='OFF', save_last='ON',
                     source='operator panel confirmation', outputs='OFF')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'four Output Recall cycles passed; restored Recall OFF'
    except BaseException as exc:
        reason = f'Output Recall stopped: {type(exc).__name__}: {exc}'
        outcome = ('ABORTED' if isinstance(exc, (KeyboardInterrupt, InterruptedError, EOFError))
                   else 'FAIL')
        evidence.add('stage_error', exception_type=type(exc).__name__, message=str(exc))
        print(reason)
        if transport is None or dev is None or not matched or not dev.debug_transport.live:
            outcome = 'ABORTED'
            print('Связь/identity не подтверждены: автоматических повторов нет. '
                  'Выключите Keithley вручную, INPUT нагрузки OFF; проверьте выходы.')
        else:
            try:
                dev.debug_transport.cleanup = True
                dev.emergency_off()
                require_outputs(dev)
                dev.diagnostic_write('SYST:LOC')
            except BaseException as off_error:
                outcome = 'ABORTED'
                evidence.add('cleanup_error', exception_type=type(off_error).__name__,
                             message=str(off_error))
    finally:
        if transport is not None:
            try: transport.close()
            except BaseException:
                outcome, reason = 'ABORTED', 'transport close failed; manual OFF required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     scope='Output Recall power-cycle; no load',
                     verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
    return outcome
