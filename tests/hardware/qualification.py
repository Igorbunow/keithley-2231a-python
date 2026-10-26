"""Manually gated qualification, excluded from automatic hardware discovery.

@file qualification.py
@author Project contributors
@date 2026-10-25
@version 0.2.0
@brief Gated H01/H02/H03 and a bounded registry of subsequent qualification stages.
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
STATE_QUERIES = ('INST:NSEL?', 'OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARALLEL?',
                 'OUTP:TRAC?', 'VOLT?', 'CURR?', 'VOLT:LIM?', 'VOLT:LIM:STAT?',
                 'CHAN:OUTP?', 'OUTP:TIM:STAT?', 'OUTP:TIM:DEL?',
                 'MEAS:VOLT?', 'MEAS:CURR?', '*STB?',
                 'STAT:QUES:COND?', 'STAT:OPER:COND?')
# SYST:REM is required by manual section 5-2. LOC restores panel control.
# No profile, reset, setpoint, topology, timer or output mutation is allowed.
H01_PLAN = ('SYST:REM', '*IDN?', 'SYST:ERR?', *OFF_QUERIES, 'SYST:VERS?', *STATE_QUERIES,
            'SYST:ERR?', '*IDN?', *OFF_QUERIES, 'SYST:LOC')
UNITS = {'VOLT?': 'V', 'VOLT:LIM?': 'V', 'CURR?': 'A',
         'MEAS:VOLT?': 'V', 'MEAS:CURR?': 'A', 'OUTP:TIM:DEL?': 's'}


def stages():
    """Read the exact authoritative matrix, keeping a single stage registry."""
    result = {}
    for line in MATRIX.read_text(encoding='utf-8').splitlines():
        match = re.match(r'\| \*\*([HO]\d\dP?)\*\* \|', line)
        if match:
            result[match[1]] = [cell.strip() for cell in line.strip('|').split('|')][1:]
    return result


def stage_passed(status: str) -> bool:
    """Accept a reviewed PASS with an explanatory suffix, never a prefix match."""
    return re.fullmatch(r'\*{0,2}PASS\*{0,2}(?:\s+\(.*\))?',
                        status.strip()) is not None


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
        self.firmware_observations = []
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
        if (fields.get('command') == '*IDN?' and 'response' in fields
                and not event.startswith('kel103_')):
            parts = fields['response'].split(',')
            if len(parts) == 4:
                serial = parts[2].strip()
                if serial: self.secrets.add(serial)
                self.firmware = parts[3].strip()
                if self.firmware not in self.firmware_observations:
                    self.firmware_observations.append(self.firmware)
                parts[2] = ' [REDACTED]'
                fields['response'] = ','.join(parts)
            else:
                fields['response'] = '[REDACTED MALFORMED IDN]'
        record = self.sanitize({'utc': datetime.now(timezone.utc).isoformat(),
                                'monotonic_s': time.monotonic(), 'test_id': self.test_id,
                                'event': event, **fields})
        if event in ('outcome', 'query_outcome'):
            record['firmware_observations'] = list(self.firmware_observations)
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n')
        return record


def open_transport(port):
    """Only call after the specific interactive operator gate."""
    from keithley2231a import SerialTransport
    return SerialTransport(port, baudrate=9600, timeout=2.0)


def preflight(test_id, port, load_host='192.168.22.106', load_port=18190, start_channel=1,
              load_transport='udp', load_serial_port='/dev/ttyACM0', diagnose_status=False):
    """Print a human-readable card; never contact hardware."""
    row = stages()[test_id]
    print(f'[{test_id}] {row[0]}\nТопология/нагрузка: {row[1]}\nКритерий: {row[2]}')
    if test_id == 'H02':
        from profile_stage import TOKEN as token, command_plan
        print(f'Порт: {port}; USB–TTL UART 9600 8N1, один владелец.\n'
              'CH1/CH2/CH3 OFF, клеммы свободны; без DUT и объединяющих перемычек.\n'
              'Нагрузка отсоединена от всех каналов, INPUT OFF; режим/значение не задавать.\n'
              'Output Recall=Off проверить на панели; доступен сетевой выключатель.\n'
              'Перед изменениями снимок V/I, лимита/state, выхода и таймера всех каналов.\n'
              'Профиль: каждый CH 1.00 V / 0.100 A; V-cap 4.00 V, software I-cap 0.500 A.\n'
              'VOLT:LIM не является независимой OVP; отдельного hardware I-cap нет.\n'
              'Все выходы остаются OFF; при ошибочном ON после профиля ожидается до 1 V / 0.1 A.\n'
              'В пределах профиля до 4 V / 0.5 A, 2 W на канал, 6 W суммарно.\n'
              'До применения/при отказе защиты возможны паспортные 30/30/5 V, 3 A, 195 W.\n'
              'Изменения: REM/LOC, OUTP 0, независимый режим, выбор CH, отключение таймеров,\n'
              'VOLT/CURR 0, VOLT:LIM 4.00/state=1, CURR 0.100, VOLT 1.00. Без RWLOCK.\n'
              'После каждой записи, кроме LOC, SYST:ERR? должен вернуть 0; без *CLS.\n'
              'Проверки: точные V/I (±0.0049 V / ±0.00049 A), V-cap ±0.0049 V, state=1,\n'
              'таймеры OFF, режим независимый, ON-биты всех CH=0, invalid V/I без SCPI.\n'
              'До профиля ON через API должен быть отвергнут без SCPI. ON после профиля не вызывается.\n'
              'Читаются внутренние U/I; точность и CV/CC здесь не квалифицируются.\n'
              'Один проход до 180 s. Первая ошибка останавливает настройки; на живой связи\n'
              'попытка OUTP 0 и проверка OFF. При отказе I/O — никаких следующих команд/повторов.\n'
              'Ручной останов: сетевой выключатель Keithley; проверить физический OFF.\n'
              'Итоговые настройки не откатываются, CH1 выбран, управление локальное.\n'
              f'Подтверждение только H02: {token}\nSCPI plan (success path):')
        for command in command_plan(): print('  ' + command)
        print('Failure path, known live link only: OUTP 0; SYST:ERR?; per-channel OFF; SYST:ERR?; SYST:LOC.\n'
              'Внутри apply_profile/verify_profile возможна более ранняя попытка OUTP 0; план запрещает иные отклонения.')
        return
    if test_id == 'H04P':
        from powered_profile_stage import H04P_MAXV_TOKEN, approval_token
        token = approval_token(load_transport, load_port, load_serial_port)
        load_connection = (f'KEL103 UDP {load_host}:{load_port}' if load_transport == 'udp'
                           else f'KEL103 USB {load_serial_port}, 115200 8N1')
        print(f'Keithley: {port}, USB–TTL 9600 8N1; {load_connection}. '
              'Independent CH1–CH3, no jumpers/DUT. Output Recall=Off; network switch accessible.\n'
              'Start with every output OFF and KEL103 INPUT OFF/disconnected. Runner controls '
              'KEL103 CR and readings. Operator pauses only to connect/move/disconnect its leads; '
              'KEL polarity must be correct and only one channel may be connected.\n'
              'Powered profile: per channel V-cap=4.50 V, software I-cap=0.500 A; one PSU '
              'output at a time. Absolute profile envelope is 2.25 W/channel; measured current '
              'tests use 3.00 V setpoint and stay at or below 1.50 W. No unrestricted profile '
              'is energized; its rated caps remain covered by OFF-only H04.\n'
              'Twenty-one automated readings: per channel CV at 3.30 V, its assigned value '
              '(CH1 2.20 V, CH2 3.30 V, CH3 4.40 V), and exact 4.50 V cap; CC at 0.100/0.250/0.500 A '
              'with CR 22/12/5 Ω; then one fixed power_only preset. CV uses CR 47/56 Ω. '
              'Out-of-profile 4.51 V and 0.501 A are local API rejections with no SCPI.\n'
              'Before connecting load: runner sets Max V=4.50 V on all channels and displays '
              'readbacks; operator sets Protection Settings → Max Volt Set On at 4.50 V on '
              'CH1/CH2/CH3, '
              'tries V-set=4.51 V on CH1/CH2/CH3 with outputs OFF, and confirms each clamps '
              'to 4.50 V. Runner reads back, restores 3.30 V, then library attempts 4.51 V and '
              'must reject before SCPI. '
              f'Manual max-voltage confirmation: {H04P_MAXV_TOKEN}\n'
              'Every point: KEL INPUT OFF before setup, load readback verified, KEL INPUT ON, '
              'only selected PSU channel ON, PSU and KEL readings/status recorded, PSU OFF, '
              'all-channel OFF confirmed, KEL INPUT OFF. Any error/measurement/mode mismatch '
              'stops immediately; no retry. Writes are allow-listed and checked with SYST:ERR? '
              'except LOC. Budget 5000 commands / 900 s with a reserved OFF/verification tail.\n'
              f'Initial authorization: {token}\n'
              'Manual gates: CONNECT CH1; MOVE CH1→CH2; MOVE CH2→CH3; DISCONNECT CH3.\n'
              f'Final OFF confirmation: CONFIRM H04P CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        return
    if test_id == 'H04':
        from profile_stage import H04_MAXV_TOKEN, H04_TOKEN as token
        print(f'Порт: {port}; USB–TTL 9600 8N1, один владелец. Независимые CH1–CH3, '
              'все выходы OFF; клеммы свободны, перемычек/DUT нет. KEL103 отключена, INPUT OFF.\n'
              'На панели проверьте Output Recall=Off; сетевой выключатель доступен.\n'
              'Сценарий: применить unrestricted() при уставках 0.00 V / 0.100 A, '
              'прочитать caps CH1/2=30 V, CH3=5 V, проверить, что все выходы OFF; '
              'попытка заменить активный профиль должна быть отклонена без SCPI.\n'
              'Затем H04 задаст отдельный control-профиль для Max Volt Set: CH1 V=1.00 V / '
              'I=0.100 A / V-cap=2.00 V / software I-cap=0.200 A; CH2=2.00/0.200/3.00/0.300; '
              'CH3=3.00/0.300/4.00/0.400 (V/A/V/A). Runner прочитает уставки и VOLT:LIM каждого '
              'канала, все OFF, затем переключит PSU в Local. На панели задайте Max Volt Set On '
              'по каналам 2.00/3.00/4.00 V. Введите уставку на 0.01 V выше каждого предела '
              '(2.01/3.01/4.01); ожидается ограничение до 2.00/3.00/4.00 V, не сохранение прежней '
              'уставки. После ручной проверки runner независимо прочитает все setpoints, проверит '
              'ошибки и OFF, восстановит 1/2/3 V и проверит до SCPI, что API отвергает voltage '
              '2.01/3.01/4.01 V и current 0.21/0.31/0.41 A при профилях 0.20/0.30/0.40 A. '
              f'Токен ручной проверки: {H04_MAXV_TOKEN}.\n'
              'При всех OFF runner проверит два внешних изменения: '
              'VOLT:LIM 5.00 V на CH1 (в пределах паспортного лимита, выше cap профиля) и '
              'CURR 0.101 A на CH3 (в пределах cap, но точная уставка изменилась); '
              'verify_profile должен обнаружить оба изменения и разоружить профиль.\n'
              'Ни одна операция не включает выход. Профиль unrestricted временно задаёт caps '
              '30/30/5 V и по 3 A, но уставка тока 0.100 A; при случайном ON уставка V=0.00 V. '
              'Затем power_only: предел профиля до 4.00 V / 0.500 A (2 W/CH; 6 W total), '
              'уставка 1.00 V / 0.100 A. LOAD отключена.\n'
              'Разрешённые writes: REM/LOC, OUTP 0, сброс независимого режима, OFF таймеров, '
              'VOLT/CURR/VOLT:LIM значения профилей и единственные тамперы VOLT:LIM 5.00 / CURR 0.101. '
              'Запрос SYST:ERR? после каждой записи, кроме LOC; ненулевой код останавливает этап. '
              'Запрещены все команды включения, RWLOCK, сброс и автоматический retry.\n'
              'Один проход, бюджет 1000 команд / 180 s. При ошибке — останов и только OFF '
              'при живой связи; при потере связи проверить OFF вручную. Уставки после теста '
              'не откатываются автоматически.\n'
              f'Требуется H01/H02/H03 reviewed PASS. Допуск только H04: {token}\n'
              f'Итоговая ручная проверка: {"CONFIRM H04 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF"}')
        return
    if test_id == 'H03':
        from channel_stage import approval_token, command_plan
        token = approval_token(start_channel, load_transport, load_host,
                               load_port, load_serial_port, diagnose_status)
        selected_plan = command_plan(start_channel, diagnose_status)
        digest = hashlib.sha256('\n'.join(selected_plan).encode()).hexdigest()
        load_connection = (f'KEL103 UDP {load_host}:{load_port}' if load_transport == 'udp'
                           else f'KEL103 USB {load_serial_port}, 115200 8N1')
        print(f'Порт: {port}; USB–TTL UART 9600 8N1, один владелец. Независимые CH1–CH3; '
              f'без DUT/перемычек; {load_connection}; выбран вручную, без failover; '
              'один канал включён за раз.\n'
              f'Продолжение с CH{start_channel}; предыдущие точки должны быть в приложенном evidence.\n'
              'Уставки профиля: каждый CH V=4.00 V, I=0.500 A; cap 4.00 V / 0.500 A.\n'
              'CV: CR 40.0 Ω, ожидается ~4.00 V / 0.100 A / 0.400 W.\n'
              'CC: CH I=0.250 A, CR 12.0 Ω, ожидается ~3.00 V / 0.250 A / 0.750 W.\n'
              'На каждую точку: при PSU OFF оператор подключает KEL103 только к целевому CH; '
              f'runner проверяет INPUT OFF, задаёт CR по {load_transport.upper()}, '
              'включает INPUT и считывает U/I/P. '
              'После измерения Keithley OFF, все каналы проверяются OFF, runner выключает '
              'KEL INPUT и проверяет OFF.\n'
              'Паузы: подключение CH1, перестановки CH1→CH2 и CH2→CH3, дисплейная сверка.\n'
              'Остановить при любом коде SYST:ERR?, расхождении questionable status/панели, измерений, OFF или связи. '
              'При I/O-ошибке UNKNOWN OUTPUT STATE, не повторять команды.\n'
              'KEL103: *IDN?, :INP?, :FUNC?, :RES?, :MEAS:VOLT?, :MEAS:CURR?, '
              ':MEAS:POW?; разрешённые writes: :FUNC CR, :RES 12/40OHM, :INP ON/OFF.\n'
              'Keithley MEAS:POW? записывается в evidence. На каждой точке отдельно читаются '
              'operation и questionable condition-регистры канала. Если questionable status и панель '
              'подтверждают режим, но operation status не подтверждает, точка получает PARTIAL, '
              'оба прибора выключаются и разрешается запросить следующий режим после OFF gate.\n'
              f'Точный Keithley SCPI-plan SHA256: {digest}; команд: {len(selected_plan)}.\n'
              f'Первый допуск до открытия портов: {token}\n'
              'Дальнейшие подтверждения подключения, CR и перестановки запрашиваются отдельно внутри runner.\n'
              'Финальное физическое подтверждение: CONFIRM H03 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        if diagnose_status:
            print('ДИАГНОСТИКА CH3: реальные TX/RX-байты status-запросов; короткие и полные формы; '
                  'ранний снимок и снимок после подтверждения дисплея, не ранее 5 s после ON. '
                  'Затем повтор с выбранным CH1 (он остаётся OFF) и возврат выбора CH3. '
                  'Считываются все три condition-регистра и ENAB CH3; маски не изменяются. '
                  'Event-регистры, *CLS, *RST и калибровка не используются. '
                  'CV/CC, CR и пределы прежние. Диагностические данные требуют review; '
                  'несоответствие operation status не скрывается и не переводится в PASS.')
            print('Подключение: KEL103 +/− только к CH3 +/−; CH1/CH2 свободны, без перемычек. '
                  'До запуска все выходы и INPUT OFF, Output Recall=Off проверить на панели. '
                  'Тестовый максимум CH3: 4.00 V / 0.500 A / 2 W; CC: лимит 0.250 A. '
                  'При отказе ограничений паспортный максимум CH3 5 V / 3 A / 15 W. '
                  'Ручной останов: сетевой выключатель Keithley, затем INPUT OFF и проверка всех выходов. '
                  'Не переставлять провода во время диагностики.')
        return
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
          'Мутации: только SYST:REM / SYST:LOC (режим управления); два SYST:ERR? извлекают записи ошибок.\n'
          'Первый ERR? фиксирует исходную очередь: допускается только 0 или ранее сообщённая 170.\n'
          'Исходная 170 сохраняется в evidence; финальный ERR? обязан вернуть 0. Без *CLS.\n'
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
            except BaseException as exc:
                live = False
                # Do not serialize exception messages: an undecodable IDN reply
                # might contain a serial number not yet known to the redactor.
                evidence.add('io_error', command=command,
                             exception_type=type(exc).__name__,
                             cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
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
                code = int(code.strip())
                initial = command not in replies
                if initial and code == 170 and message.strip():
                    evidence.add('preexisting_error', code=code, message=message,
                                 source='operator-reported previous H01 failure; consumed and preserved')
                elif code != 0 or not message.strip():
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
                if command in ('OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?',
                               'VOLT:LIM:STAT?', 'CHAN:OUTP?', 'OUTP:TIM:STAT?') and value not in (0, 1):
                    raise ValueError('invalid boolean response')
                if command in ('OUTP:STAT:ALL?', 'OUTP:SER?', 'OUTP:PARALLEL?', 'OUTP:TRAC?', 'CHAN:OUTP?') and value:
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
    test_id = events[0].get('test_id', 'hardware') if events else 'hardware'
    print(f'# {test_id} evidence / Протокол {test_id}\n\nReview required; no automatic HW VERIFIED promotion.\n')
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


def validate_h03_checkpoint(path, start_channel):
    """Require reviewed PASS evidence and final OFF proof across prior run logs."""
    paths = path if isinstance(path, (list, tuple)) else [path]
    events = [json.loads(line)
              for evidence_path in paths
              for line in Path(evidence_path).read_text(encoding='utf-8').splitlines()]
    if not events or any(event.get('test_id') != 'H03' for event in events):
        raise ValueError('prior evidence is not an H03 event log')
    passed = {(event.get('channel'), event.get('mode'))
              for event in events if event.get('event') == 'measurement_review'
              and event.get('review_outcome') == 'PASS'}
    expected = {(channel, mode) for channel in range(1, start_channel)
                for mode in ('CV', 'CC')}
    if not expected <= passed:
        missing = ', '.join(f'CH{ch} {mode}' for ch, mode in sorted(expected - passed))
        raise ValueError(f'prior H03 points lack reviewed PASS: {missing}')
    if not any(event.get('event') == 'outcome' and event.get('physical_off_confirmed') is True
               for event in events):
        raise ValueError('prior H03 run lacks final operator OFF confirmation')
    print(f'H03 checkpoint accepted: reviewed {len(expected)} prior points; prior log retained.')


def main(argv=None):
    """Dry run is default; each prepared hardware group has its own operator gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'preflight', 'run', 'report', 'abort'))
    parser.add_argument('test_id', nargs='?', choices=tuple(stages()))
    parser.add_argument('--port', default='/dev/ttyUSB1')
    parser.add_argument('--load-host', default='192.168.22.106')
    parser.add_argument('--load-port', type=int, default=18190)
    parser.add_argument('--load-transport', choices=('udp', 'serial'), default='udp',
                        help='explicit KEL103 connection; never auto-switches on failure')
    parser.add_argument('--load-serial-port', default='/dev/ttyACM0')
    parser.add_argument('--start-channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--diagnose-status', action='store_true',
                        help='H03 CH3 only: wire trace, delayed and channel-selection status snapshots')
    parser.add_argument('--prior-evidence', type=Path, action='append',
                        help='H03 evidence log; repeat once per completed-channel log when resuming')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.diagnose_status and (args.test_id != 'H03' or args.start_channel != 3):
        parser.error('--diagnose-status requires H03 --start-channel 3')
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
    preflight(args.test_id, args.port, args.load_host, args.load_port,
              args.start_channel, args.load_transport, args.load_serial_port, args.diagnose_status)
    if args.test_id == 'H03' and args.start_channel > 1:
        if args.prior_evidence is None:
            parser.error('--prior-evidence is required when resuming H03; repeat for each prior channel log')
        try:
            validate_h03_checkpoint(args.prior_evidence, args.start_channel)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
    if args.command == 'preflight' or not args.execute:
        print('DRY RUN: порт не открыт, SCPI не отправлены.')
        return 0
    if args.test_id not in ('H01', 'H02', 'H03', 'H04', 'H04P'):
        print('BLOCKED: выполнение этой группы ещё не подготовлено; порт не открыт.')
        return 2
    if args.test_id in ('H02', 'H03', 'H04', 'H04P') and not stage_passed(stages()['H01'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H01; порт не открыт.')
        return 2
    if args.test_id in ('H03', 'H04', 'H04P') and not stage_passed(stages()['H02'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H02; порт не открыт.')
        return 2
    if args.test_id in ('H04', 'H04P') and not stage_passed(stages()['H03'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H03; порт не открыт.')
        return 2
    token, final_token, runner = TOKEN, FINAL_TOKEN, h01
    if args.test_id == 'H02':
        from profile_stage import TOKEN as token, FINAL_TOKEN as final_token, h02
        runner = h02
    if args.test_id == 'H04':
        from profile_stage import H04_TOKEN as token, H04_FINAL_TOKEN as final_token, h04
        runner = h04
    if args.test_id == 'H04P':
        from powered_profile_stage import (H04P_FINAL_TOKEN as final_token,
                                           approval_token, h04_powered)
        token = approval_token(args.load_transport, args.load_port, args.load_serial_port)
        runner = h04_powered
        if not stage_passed(stages()['H04'][-1]):
            print('BLOCKED: требуется рассмотренный PASS H04; порт не открыт.')
            return 2
    if args.test_id == 'H03':
        from channel_stage import approval_token, FINAL_TOKEN as final_token, h03
        token = approval_token(args.start_channel, args.load_transport,
                               args.load_host, args.load_port, args.load_serial_port,
                               args.diagnose_status)
        runner = h03
    if not sys.stdin.isatty():
        print('BLOCKED: требуется интерактивный терминал; пакетный ввод запрещён.')
        return 2
    try:
        adapter = input('Проверенный USB–TTL адаптер (модель, USB VID:PID; без серийного номера): ').strip()
        if not adapter:
            print('BLOCKED: не указан проверенный адаптер.')
            return 2
        while input(f'Подтверждение: {token}\n> ').strip() != token:
            print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
    except (EOFError, KeyboardInterrupt):
        print('ABORTED до открытия порта.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_{args.test_id}/events.jsonl', args.test_id)
    evidence.add('metadata', **software_metadata(), port=args.port, baud=9600, framing='8N1',
                 adapter=adapter,
                 topology='independent; no jumpers; no DUT',
                 load=(f'KORAD KEL103; UDP port {args.load_port}; input OFF'
                       if args.test_id in ('H03', 'H04P') and args.load_transport == 'udp' else
                       f'KORAD KEL103; USB serial {args.load_serial_port}; '
                       '115200 8N1 (community-derived; physical verification pending); input OFF'
                       if args.test_id in ('H03', 'H04P')
                       else 'disconnected; input OFF'),
                 proposed_caps=('unchanged; initial unknown' if args.test_id == 'H01' else
                                'each CH: V-cap 4.00 V, software I-cap 0.500 A; '
                                'initial 4.00 V / 0.500 A' if args.test_id == 'H03' else
                                'H04: unrestricted caps 30/30/5 V, 3 A; setpoints 0 V / 0.100 A; '
                                'per-channel manual caps 2/3/4 V and 0.2/0.3/0.4 A; '
                                'then power_only caps 4 V / 0.500 A, setpoints 1.00 V / 0.100 A'
                                if args.test_id == 'H04' else
                                'H04P powered: control/power_only V-cap 4.50 V, I-cap 0.500 A; '
                                'one CH at a time; low-power CV/CC; KEL103 CR 5/12/22/47/56 Ω'
                                if args.test_id == 'H04P' else
                                'each CH: V-cap 4.00 V, software I-cap 0.500 A; initial 1.00 V / 0.100 A'),
                 independent_meter=None)
    evidence.add('approval', token=token)
    if args.diagnose_status:
        evidence.add('diagnostic_scope', channel=3, wire_trace='status commands only',
                     late_sample_min_s=5.0, selections=(3, 1),
                     condition_registers='all channels; short and full forms on CH3',
                     enable_masks='read only; CH3')
    if args.test_id == 'H03' and args.start_channel > 1:
        evidence.add('resume_from', start_channel=args.start_channel,
                     prior_evidence=[path.name for path in args.prior_evidence])
    load = None
    try:
        if args.test_id in ('H03', 'H04P'):
            from channel_stage import KEL103Serial, KEL103UDP
            load = (KEL103UDP(args.load_host, args.load_port, evidence=evidence)
                    if args.load_transport == 'udp' else
                    KEL103Serial(args.load_serial_port, evidence=evidence))
        transport = open_transport(args.port)
        if args.diagnose_status:
            from channel_stage import StatusWireTrace
            # Qualification-only byte observer; public transport API is unchanged.
            transport._serial = StatusWireTrace(transport._serial, evidence)
    except Exception:
        if load is not None:
            try: load.close()
            except Exception: pass
        evidence.add('outcome', outcome='ABORTED', stop_reason='port open failed; verify OFF manually')
        print(f'ABORTED: порт не открыт; проверить OFF вручную. Evidence: {evidence.path}')
        return 2
    outcome = (runner(transport, evidence, load, args.start_channel,
                      diagnose_status=args.diagnose_status)
               if args.test_id == 'H03' else
               runner(transport, evidence, load) if args.test_id == 'H04P' else
               runner(transport, evidence))
    physical_off = False
    try:
        while input(f'После ручной проверки введите {final_token}: ').strip() != final_token:
            print('Подтверждение OFF не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
        physical_off = True
    except (EOFError, KeyboardInterrupt): pass
    if not physical_off and outcome == 'PASS': outcome = 'PARTIAL'
    evidence.add('outcome', outcome=outcome, physical_off_confirmed=physical_off,
                 stop_reason=f'STOP after {args.test_id}; next stage requires separate operator approval')
    report_path = ROOT / f'docs/hardware_reports/{now}_{args.test_id}_qualification.md'
    with report_path.open('x', encoding='utf-8') as stream, contextlib.redirect_stdout(stream):
        report(evidence.path)
    print(f'{outcome}; Evidence: {evidence.path}\n'
          f'Протокол для review: {report_path}\n'
          'STOP: перед следующей группой требуется разбор отчёта и отдельное разрешение.\n'
          f'Предлагаемое сообщение коммита после review: test(hw): record {args.test_id} qualification')
    return 0 if outcome == 'PASS' else 2
