"""Strict OFF-only channel/firmware correlation diagnostic for 2231A.

This is a separate H01 variant. It cannot energize or configure any output.
Only the selected channel and REM/LOC are allowed writes. No retries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sys

from keithley2231a import Keithley2231A
from common import StageAdapter
from connection_gate import connection_card, connection_token, verify_serial_adapter
from equipment.factory import open_psu

# Repeated same-channel reads distinguish simple time drift from selection effects.
# Returning to CH1 after CH3 tests whether the observed version is reversible.
SELECTION_PLAN = (1, 1, 2, 2, 3, 3, 1, 3, 2, 1)
FINAL_TOKEN = 'CONFIRM H01-AUX CH1=OFF CH2=OFF CH3=OFF LOAD=DISCONNECTED'
WIRING_TOKEN = ('CONFIRM H01-AUX ALL_OFF LOAD=DISCONNECTED '
                'NO_JUMPERS NO_DUT RECALL=OFF')


class AuxRevisionScope(StageAdapter):
    READS = frozenset({'*IDN?', 'SYST:ERR?', 'INST:NSEL?', 'CHAN:OUTP?',
                       'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:STAT:ALL?',
                       *(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))})
    WRITES = frozenset({'SYST:REM', 'SYST:LOC',
                        'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'})
    MAX_SECONDS = 120
    MAX_COMMANDS = 800


def preflight(bench):
    print('[H01-AUX / OFF-ONLY] Сопоставление версии Aux с выбором канала')
    print(connection_card(bench, 'H01', ('psu',), aux_revision_diagnostic=True))
    print('Проверьте CH1/CH2/CH3 OFF на панели; клеммы свободны, нагрузка отсоединена; '
          'нет перемычек/DUT; Output Recall OFF. Возможен ручной сетевой останов.')
    print('Читаются: *IDN?, INST:NSEL?, CHAN:OUTP?, OUTP:SER?, OUTP:PARALLEL?, '
          'OUTP:STAT:ALL?, STAT:OPER:INST:ISUM1/2/3:COND?, SYST:ERR?. '
          'Записи только SYST:REM, INST:NSEL 1/2/3, SYST:LOC.')
    print('Полный CHAN:OUTP? sweep до и после наблюдений; внутри серии проверяются '
          'глобальный OFF, текущий CHAN:OUTP? и три ISUM ON-бита без переключений. '
          'Уставки/режимы/таймеры/выходы НЕ изменяются; нет OUTP, CHAN:OUTP, *CLS, *RST, SYST:MODU?.')
    print('Порядок выборов: ' + ' -> '.join(f'CH{c}' for c in SELECTION_PLAN))
    print('Повторы на одном канале позволяют отличать изменение версии по времени от смены канала.')
    print(f'Токен исходной схемы: {WIRING_TOKEN}')
    print(f'Финальное подтверждение: {FINAL_TOKEN}')
    print('DRY RUN: порты закрыты, SCPI не отправлены.')


def run_sequence(transport, evidence):
    """Return PASS or ABORTED/FAIL. No I/O after first failed read/validation.

    Do not call Keithley2231A.close(): it issues emergency OUTP 0, forbidden
    for this observational stage. Close the underlying transport directly.
    """
    scopes = []
    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda trace: evidence.add('driver_trace', trace=trace),
                         debug_adapter=lambda raw: scopes.append(AuxRevisionScope(raw, evidence)) or scopes[-1])
    initial = None
    outcome = 'ABORTED'
    reason = 'Not completed'
    try:
        # A read-only driver path may select channels to verify their actual
        # OFF readbacks, restoring the prior selected channel afterward.
        states = dev.output_states()
        if states != {1: False, 2: False, 3: False}:
            raise ValueError(f'Initial output state not all OFF: {states}')
        if dev.diagnostic_query('OUTP:SER?').strip() != '0':
            raise ValueError('Series mode active; not an independent-channel diagnostic')
        if dev.diagnostic_query('OUTP:PARALLEL?').strip() != '0':
            raise ValueError('Parallel mode active; not an independent-channel diagnostic')
        initial_raw = dev.diagnostic_query('INST:NSEL?').strip()
        if initial_raw not in ('1', '2', '3'):
            raise ValueError('Unknown initial selected channel')
        initial = int(initial_raw)
        for index, channel in enumerate(SELECTION_PLAN):
            # Avoid a full selecting sweep before each IDN: that would itself
            # visit CH3 and confound the channel-selection hypothesis. Instead
            # check the independent ON-bit summaries without selecting others.
            dev.diagnostic_write(f'INST:NSEL {channel}')
            actual = dev.diagnostic_query('INST:NSEL?').strip()
            if actual != str(channel):
                raise ValueError(f'Channel selection mismatch at sample {index}: {actual!r}')
            if dev.diagnostic_query('CHAN:OUTP?').strip() != '0':
                raise ValueError(f'CH{channel} output ON during sample {index}')
            if dev.diagnostic_query('OUTP:STAT:ALL?').strip() != '0':
                raise ValueError(f'Global output ON during sample {index}')
            for other in (1, 2, 3):
                mask_raw = dev.diagnostic_query(f'STAT:OPER:INST:ISUM{other}:COND?').strip()
                if not re.fullmatch(r'[0-9]{1,5}', mask_raw) or int(mask_raw) > 65535:
                    raise ValueError(f'Invalid channel status at sample {index}: CH{other}')
                if int(mask_raw) & 8:
                    raise ValueError(f'CH{other} ON bit active at sample {index}')
            raw = dev.diagnostic_query('*IDN?')
            revision = raw.split(',', 3)[3].strip()  # identity() validates the complete reply
            evidence.add('aux_channel_observation', index=index, selected_channel=channel,
                         revision=revision, output_off_verified=True)
            print(f'AUX SAMPLE {index + 1:02d}: CH{channel} Main/Aux={revision}')
        if dev.output_states() != {1: False, 2: False, 3: False}:
            raise ValueError('Final output readback not all OFF')
        if initial is not None:
            dev.diagnostic_write(f'INST:NSEL {initial}')
            if dev.diagnostic_query('INST:NSEL?').strip() != str(initial):
                raise ValueError('Original channel selection was not restored')
        dev.diagnostic_write('SYST:LOC')
        outcome, reason = 'PASS', 'OFF-only sequence complete; physical OFF confirmation required'
    except (ValueError, TypeError) as exc:
        # A semantic failure can coexist with a live connection. Do not send
        # *any* extra wire command on the failure path: leave OFF to operator.
        outcome, reason = 'FAIL', type(exc).__name__
    except BaseException as exc:
        outcome, reason = 'ABORTED', f'{type(exc).__name__}; no retry, physical OFF required'
    finally:
        try:
            transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'Transport close failure; physical OFF required'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware_observations=list(evidence.firmware_observations))
    return outcome


def run_cli(bench):
    """Independent interactive connection/wiring gates, before opening any port."""
    from qualification import Evidence, ROOT, report, software_metadata
    if not sys.stdin.isatty():
        print('BLOCKED: local interactive terminal required; no ports opened.')
        return 2
    bench_gate = connection_token(bench, 'H01', aux_revision_diagnostic=True)
    print(connection_card(bench, 'H01', ('psu',), aux_revision_diagnostic=True))
    try:
        verify_serial_adapter(bench.psu)
        if input(f'Проверьте USB-подключение и подтвердите:\n{bench_gate}\n> ').strip() != bench_gate:
            print('BLOCKED: bench gate mismatch; no ports opened.')
            return 2
        if input(f'Физически проверьте все выходы OFF и свободные клеммы:\n{WIRING_TOKEN}\n> ').strip() != WIRING_TOKEN:
            print('BLOCKED: wiring gate mismatch; no ports opened.')
            return 2
    except (EOFError, KeyboardInterrupt, OSError, ImportError, ValueError) as exc:
        print(f'BLOCKED: preflight or gate failed ({type(exc).__name__}); no ports opened.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_H01_AUX_REVISION/events.jsonl',
                        'H01_AUX_REVISION')
    evidence.add('metadata', **software_metadata(), topology='OFF-only, independent, no jumpers',
                 subgroup='aux_revision_channel_selection')
    evidence.add('bench_configuration', sha256=bench.sha256, source=bench.source.name,
                 devices={'psu': {'model': bench.psu.model, 'transport': bench.psu.transport}})
    evidence.add('bench_connection_confirmation', sha256=bench.sha256,
                 token=bench_gate, devices=['psu'], serial_os_verified=['psu'])
    evidence.add('approval', token=WIRING_TOKEN, authorization='OFF-only; no output enable')
    try:
        transport = open_psu(bench)
    except BaseException as exc:
        evidence.add('open_error', exception_type=type(exc).__name__)
        evidence.add('outcome', outcome='ABORTED', physical_off_confirmed=False)
        print('ABORTED: transport open failed; check physical OFF.')
        return 2
    outcome = run_sequence(transport, evidence)
    physical = False
    try:
        physical = input(f'Проверьте физический OFF всех каналов:\n{FINAL_TOKEN}\n> ').strip() == FINAL_TOKEN
    except (EOFError, KeyboardInterrupt):
        pass
    if outcome == 'PASS' and not physical:
        outcome = 'PARTIAL'
    evidence.add('outcome', outcome=outcome, physical_off_confirmed=physical,
                 stop_reason='OFF-only AUX diagnostic; review before subsequent hardware stage')
    report_path = ROOT / f'docs/hardware_reports/{now}_H01_AUX_REVISION_qualification.md'
    from contextlib import redirect_stdout
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open('x', encoding='utf-8') as output, redirect_stdout(output):
        report(evidence.path)
    print(f'{outcome}; Evidence: {evidence.path}\nПротокол: {report_path}\n'
          'STOP: analyse findings; do not start another hardware stage automatically.')
    return 0 if outcome == 'PASS' else 2
