"""Manually gated qualification, excluded from automatic hardware discovery.

@file qualification.py
@author Project contributors
@date 2026-10-25
@version 0.2.0
@brief Read-only H01 and a bounded registry of subsequent qualification stages.
@details No import or dry run opens a port. Powered stages remain guarded until
         their preceding evidence and operator-specific load details are reviewed.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / 'docs/hardware_reports/qualification_matrix_2026-10-25.md'
TOKEN = 'CONFIRM H01 INDEPENDENT ALL_OFF LOAD=DISCONNECTED'
FINAL_TOKEN = 'CONFIRM H01 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
# Read the selected channel only: no INST:NSEL mutation in a read-only preflight.
OFF_QUERIES = tuple(f'STAT:OPER:INST:ISUM{ch}:COND?' for ch in (1, 2, 3))
STATE_QUERIES = ('INST:NSEL?', 'OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARA?',
                 'OUTP:TRAC?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                 'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?',
                 'MEAS:VOLT?', 'MEAS:CURR?', '*STB?',
                 'STAT:QUES:COND?', 'STAT:OPER:COND?')
# SYST:REM is required by manual section 5-2. LOC restores panel control.
# No profile, reset, setpoint, topology, timer or output mutation is allowed.
H01_PLAN = ('SYST:REM', '*IDN?', *OFF_QUERIES, 'SYST:VERS?', *STATE_QUERIES,
            'SYST:ERR?', '*IDN?', *OFF_QUERIES, 'SYST:LOC')
UNITS = {'VOLT?': 'V', 'VOLT:LIM?': 'V', 'CURR?': 'A',
         'MEAS:VOLT?': 'V', 'MEAS:CURR?': 'A', 'OUTP:TIM:DEL?': 's'}


def stages():
    """Read the exact authoritative matrix, keeping a single stage registry."""
    result = {}
    for line in MATRIX.read_text(encoding='utf-8').splitlines():
        match = re.match(r'\| \*\*([HO]\d\d)\*\* \|', line)
        if match:
            result[match[1]] = [cell.strip() for cell in line.strip('|').split('|')][1:]
    return result


def software_metadata():
    """Identify the checked-out implementation, including uncommitted sources."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    digest = hashlib.sha256()
    for pattern in ('src/keithley2231a/*.py', 'tools/hw_qualify.py', 'tests/hardware/*.py'):
        for path in sorted(ROOT.glob(pattern)):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return {'sha': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
            'source_sha256': digest.hexdigest(), 'library_version': '0.2.0',
            'python': platform.python_version(), 'os': platform.platform()}


class Evidence:
    """Append timestamped events, redacting serial numbers before persistence."""
    def __init__(self, path: Path, test_id: str):
        self.path, self.test_id = path, test_id
        self.secrets = set()
        self.firmware = None
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse collisions; a new run never overwrites earlier evidence.
        with path.open('x', encoding='utf-8'):
            pass

    def sanitize(self, value):
        if isinstance(value, dict): return {k: self.sanitize(v) for k, v in value.items()}
        if isinstance(value, list): return [self.sanitize(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets: value = value.replace(secret, '[REDACTED]')
        return value

    def add(self, event: str, **fields):
        if fields.get('command') == '*IDN?' and 'response' in fields:
            parts = fields['response'].split(',')
            if len(parts) == 4:
                serial = parts[2].strip()
                if serial: self.secrets.add(serial)
                self.firmware = parts[3].strip()
                parts[2] = ' [REDACTED]'
                fields['response'] = ','.join(parts)
            else:
                fields['response'] = '[REDACTED MALFORMED IDN]'
        record = self.sanitize({'utc': datetime.now(timezone.utc).isoformat(),
                                'monotonic_s': time.monotonic(), 'test_id': self.test_id,
                                'event': event, **fields})
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        return record


def open_transport(port):
    """Only call after the specific interactive operator gate."""
    from keithley2231a import SerialTransport
    return SerialTransport(port, baudrate=9600, timeout=2.0)


def preflight(test_id, port):
    """Print a human-readable card; never contact hardware."""
    row = stages()[test_id]
    print(f'[{test_id}] {row[0]}\nТопология/нагрузка: {row[1]}\nКритерий: {row[2]}')
    if test_id != 'H01':
        print('BLOCKED для выполнения: сначала разбор H01, паспорт нагрузки и отдельная полная карточка.\n'
              'SCPI-последовательность ещё не разрешена; порт не открывается.')
        return
    print(f'Порт: {port}; USB–TTL UART, 9600 8N1; один владелец.\n'
          'ВСЕ выходы CH1/CH2/CH3 OFF; нагрузка INPUT OFF и отсоединена.\n'
          'Без перемычек и DUT. Режим нагрузки не задавать; вход остаётся OFF.\n'
          'Проверить Output Recall=Off на панели и соответствие порта TTL-адаптеру.\n'
          'PSU V/I/caps не изменяются, исходные значения неизвестны: сначала снимок.\n'
          'Ожидается отсутствие питания нагрузки. Паспортный возможный максимум при ошибке\n'
          'состояния: CH1/2 30 V, CH3 5 V, 3 A каждый, суммарно 195 W.\n'
          'Мутации: только SYST:REM / SYST:LOC (режим управления); SYST:ERR? извлекает одну ошибку.\n'
          'Нет OUTP 1/0, INST:NSEL, RWLOCK, *CLS, *RST или изменения уставок.\n'
          'Чтение V/I относится только к выбранному каналу; ON-биты читаются для всех трёх.\n'
          'Ожидание: корректный IDN, повторяемый OFF в бите 3 каждого ISUM, корректные типы ответов.\n'
          'Стоп: при любой ошибке прекратить SCPI; вручную проверить OFF.\n'
          'Физический останов: сетевой выключатель Keithley; не полагаться на SCPI.\n'
          'Предел: один проход, до 120 s, без повторного подключения.\n'
          f'Ввод перед открытием порта: {TOKEN}\nSCPI plan:')
    for command in H01_PLAN: print('  ' + command)


def h01(transport, evidence):
    """One passive electrical probe. No OFF on exit: H01 permits no output writes.

    An exception makes the link uncertain: no further SCPI is attempted. A live
    semantic failure may restore local control. The operator checks physical OFF.
    """
    outcome, reason, live = 'ABORTED', 'incomplete', True
    start = time.monotonic()
    replies = {}
    try:
        for command in H01_PLAN:
            if time.monotonic() - start > 120:
                raise TimeoutError('stage duration exceeded')
            evidence.add('query' if command.endswith('?') else 'write', command=command)
            try:
                answer = transport.query(command) if command.endswith('?') else None
                if answer is None: transport.write(command)
            except BaseException:
                live = False
                raise
            if answer is None: continue
            record = evidence.add('read', command=command, response=answer, units=UNITS.get(command))
            print(f'{command}: {record["response"]}')
            if command == '*IDN?':
                parts = [part.strip() for part in answer.split(',')]
                if len(parts) != 4 or parts[0].lower() != 'keithley instruments' or parts[1].upper() != '2231A-30-3' or not parts[3]:
                    raise ValueError('unexpected instrument identity')
                if command in replies and answer != replies[command]:
                    raise ValueError('identity changed between queries')
            elif command == 'SYST:ERR?':
                code, message = answer.split(',', 1)
                if int(code.strip()) != 0 or not message.strip():
                    raise ValueError('instrument error queue contains an error')
            else:
                import math
                value = float(answer)
                if not math.isfinite(value): raise ValueError('nonfinite numeric response')
                if command in OFF_QUERIES or command in ('*STB?', 'STAT:QUES:COND?', 'STAT:OPER:COND?'):
                    if value != int(value) or not 0 <= value <= 65535:
                        raise ValueError('invalid status register')
                if command in OFF_QUERIES and (int(value) & 8):
                    raise ValueError('a channel reports ON')
                if command in ('OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARA?', 'OUTP:TRAC?',
                               'VOLT:LIM:STAT?', 'CHAN:OUTP?', 'OUTP:TIM:STAT?') and value not in (0, 1):
                    raise ValueError('invalid boolean response')
                if command in ('OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARA?', 'OUTP:TRAC?', 'CHAN:OUTP?') and value:
                    raise ValueError('expected independent mode and all outputs OFF')
                if command == 'INST:NSEL?' and value not in (1, 2, 3):
                    raise ValueError('invalid selected channel')
            replies[command] = answer
        outcome, reason = 'PASS', 'query checks passed; physical OFF confirmation still required'
    except ValueError as exc:
        outcome, reason = 'FAIL', str(exc)
    except BaseException:
        outcome, reason = 'ABORTED', 'I/O failure or interruption; UNKNOWN OUTPUT STATE; no automatic reconnect'
    finally:
        try:
            if outcome != 'PASS' and live:
                evidence.add('write', command='SYST:LOC')
                transport.write('SYST:LOC')
        except BaseException:
            outcome, reason = 'ABORTED', 'local control restore failed; UNKNOWN OUTPUT STATE'
        try: transport.close()
        except BaseException:
            outcome, reason = 'ABORTED', 'transport close failed; check physical OFF'
        evidence.add('query_outcome', outcome=outcome, stop_reason=reason,
                     firmware=evidence.firmware, verification='HW OBSERVED' if outcome == 'PASS' else 'HW UNVERIFIED')
        print(f'{outcome}: {reason}\nПроверьте физически CH1/CH2/CH3 OFF и вход нагрузки OFF.')
    return outcome


def report(path):
    """Render sanitized evidence to stdout for operator review; no promotion."""
    if path is None:
        print('Нет выбранного журнала. Укажите --evidence PATH; аппаратные группы NOT_RUN.')
        return
    events = [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()]
    # Reapply IDN redaction even to externally supplied evidence.
    print('# H01 evidence / Протокол H01\n\nReview required; no automatic HW VERIFIED promotion.\n')
    print('| UTC | Monotonic s | Operation | SCPI | Actual response | Unit |\n'
          '| --- | --- | --- | --- | --- | --- |')
    for event in events:
        if event.get('command') == '*IDN?' and 'response' in event:
            parts = event['response'].split(',')
            event['response'] = ','.join(parts[:2] + [' [REDACTED]'] + parts[3:]) if len(parts) == 4 else '[REDACTED]'
        if event['event'] in ('write', 'read'):
            values = [event.get(key, '') for key in ('utc', 'monotonic_s', 'event', 'command', 'response', 'units')]
            print('| ' + ' | '.join(str(value).replace('|', '\\|').replace('\n', ' ') for value in values) + ' |')
    print('\n## Conditions, approvals and result\n')
    for event in events:
        if event['event'] not in ('write', 'read', 'query'):
            print('```json\n' + json.dumps(event, ensure_ascii=False, indent=2) + '\n```\n')


def main(argv=None):
    """Dry run is the default; only H01 can pass an execution gate at this stage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'preflight', 'run', 'report', 'abort'))
    parser.add_argument('test_id', nargs='?', choices=tuple(stages()))
    parser.add_argument('--port', default='/dev/ttyUSB1')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'list':
        for key, row in stages().items(): print(f'{key} {row[-1]} | {row[0]}')
        return 0
    if args.command == 'report':
        if args.execute: parser.error('report does not access hardware')
        report(args.evidence)
        return 0
    if args.command == 'abort':
        print('STOP: вручную выключите Keithley сетевым выключателем,\n'
              'вход нагрузки OFF; проверьте все выходы. Порт не открывается; OFF не предполагается.\n'
              'Этот CLI однопроходный; другой работающий процесс остановите Ctrl-C. Не переподключаться автоматически.')
        return 0
    if args.test_id is None: parser.error('preflight/run requires one test ID')
    preflight(args.test_id, args.port)
    if args.command == 'preflight' or not args.execute:
        print('DRY RUN: порт не открыт, SCPI не отправлены.')
        return 0
    if args.test_id != 'H01':
        print('BLOCKED: выполнение этой группы ещё не подготовлено; порт не открыт.')
        return 2
    if not sys.stdin.isatty():
        print('BLOCKED: требуется интерактивный терминал; пакетный ввод запрещён.')
        return 2
    try:
        adapter = input('Проверенный USB–TTL адаптер (модель, USB VID:PID; без серийного номера): ').strip()
        if not adapter:
            print('BLOCKED: не указан проверенный адаптер.')
            return 2
        if input('Подтверждение: ').strip() != TOKEN:
            print('BLOCKED: нет точного разрешения H01; порт не открыт.')
            return 2
    except (EOFError, KeyboardInterrupt):
        print('ABORTED до открытия порта.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_H01/events.jsonl', 'H01')
    evidence.add('metadata', **software_metadata(), port=args.port, baud=9600, framing='8N1',
                 adapter=adapter, topology='independent; no jumpers; no DUT', load='disconnected; input OFF',
                 proposed_caps='unchanged; initial unknown', independent_meter=None)
    evidence.add('approval', token=TOKEN)
    try:
        transport = open_transport(args.port)
    except Exception:
        evidence.add('outcome', outcome='ABORTED', stop_reason='port open failed; verify OFF manually')
        print(f'ABORTED: порт не открыт; проверить OFF вручную. Evidence: {evidence.path}')
        return 2
    outcome = h01(transport, evidence)
    physical_off = False
    try: physical_off = input(f'После ручной проверки введите {FINAL_TOKEN}: ').strip() == FINAL_TOKEN
    except (EOFError, KeyboardInterrupt): pass
    if not physical_off and outcome == 'PASS': outcome = 'PARTIAL'
    evidence.add('outcome', outcome=outcome, physical_off_confirmed=physical_off,
                 stop_reason='STOP after H01; next stage requires separate operator approval')
    report_path = ROOT / f'docs/hardware_reports/{now}_H01_preflight.md'
    with report_path.open('x', encoding='utf-8') as stream, contextlib.redirect_stdout(stream):
        report(evidence.path)
    print(f'{outcome}; Evidence: {evidence.path}\n'
          f'Протокол для review: {report_path}\n'
          'STOP: перед следующей группой требуется разбор отчёта и отдельное разрешение.\n'
          'Предлагаемое сообщение коммита после успешного review: test(hw): record H01 read-only preflight')
    return 0 if outcome == 'PASS' else 2
