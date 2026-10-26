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
            'SYST:ERR?', '*IDN?', *OFF_QUERIES)


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
    normalized = status.strip()
    if normalized.startswith('**') and normalized.endswith('**'):
        normalized = normalized[2:-2].strip()
    else:
        normalized = re.sub(r'^\*\*(PASS)\*\*', r'\1', normalized)
    return re.fullmatch(r'PASS(?:\s+\(.*\))?', normalized) is not None


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
    """Append timestamped events, redacting only the identity response's serial field."""
    def __init__(self, path: Path, test_id: str):
        self.path, self.test_id = path, test_id
        self.firmware = None
        self.firmware_observations = []
        path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse collisions; a new run never overwrites earlier evidence.
        with path.open('x', encoding='utf-8'):
            pass

    def sanitize(self, value):
        if isinstance(value, dict): return {k: self.sanitize(v) for k, v in value.items()}
        if isinstance(value, list): return [self.sanitize(v) for v in value]
        return value

    def add(self, event: str, **fields):
        if (fields.get('command') == '*IDN?' and 'response' in fields
                and not event.startswith('kel103_')):
            parts = fields['response'].split(',')
            if len(parts) == 4:
                if not event.startswith('dmm7510_'):
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


def open_meter(host, evidence):
    """Construct the explicit raw-socket DMM adapter only after the H08 gate."""
    from dmm7510 import DMM7510
    from keithley2231a import TcpTransport
    return DMM7510(TcpTransport(host, 5025, timeout=5.0, terminator=b'\n'), evidence)


def verify_serial_adapter(port, adapter, available_ports=None):
    """Require the operator-confirmed USB VID:PID to match the selected port."""
    match = re.search(r'(?<![0-9A-Fa-f])([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})(?![0-9A-Fa-f])', adapter)
    if match is None:
        raise ValueError('Adapter confirmation must include USB VID:PID, for example 10C4:EA60')
    if available_ports is None:
        from serial.tools import list_ports
        available_ports = list_ports.comports()
    selected = Path(port).resolve()
    for candidate in available_ports:
        if Path(candidate.device).resolve() != selected:
            continue
        actual = (candidate.vid, candidate.pid)
        expected = (int(match.group(1), 16), int(match.group(2), 16))
        if actual == expected:
            return True
        actual_text = ('unknown' if None in actual else f'{actual[0]:04X}:{actual[1]:04X}')
        raise ValueError(f'USB adapter {port} is {actual_text}, does not match confirmed '
                         f'{expected[0]:04X}:{expected[1]:04X}')
    raise ValueError(f'USB adapter port {port} is not present in serial-port inventory')


def preflight(test_id, port, load_host='192.168.22.106', load_port=18190, start_channel=1,
              load_transport='udp', load_serial_port='/dev/ttyACM0', diagnose_status=False,
              load_connected=False, recall=False, meter_host='192.168.22.112'):
    """Print a human-readable card; never contact hardware."""
    if test_id == 'H06' and recall:
        from h06_recall import preflight as recall_preflight
        print('[H06 Output Recall] Отдельная подгруппа ручных сетевых циклов')
        recall_preflight(port)
        return
    row = stages()[test_id]
    print(f'[{test_id}] {row[0]}\nТопология/нагрузка: {row[1]}\nКритерий: {row[2]}')
    if test_id == 'H08':
        from h08_stage import preflight as h08_preflight
        h08_preflight(port, meter_host)
        return
    if test_id == 'H07':
        from h07_stage import approval_token
        print(f'Keithley: {port}, USB–TTL 9600 8N1; KEL103 USB '
              f'{load_serial_port}, 115200 8N1. Один владелец каждого порта.\n'
              'На входе CH1/CH2/CH3 OFF, KEL103 INPUT OFF и физически отключена.\n'
              'Независимые каналы; осциллограф отключён; перемычек, общей шины и DUT нет.\n'
              'Output Recall=Off проверить на панели. Сетевой выключатель доступен.\n'
              'После отдельного CONNECT gate: CH1+ → KEL103+, CH1− → KEL103−.\n'
              'Короткие провода; клеммы CH2/CH3 свободны; CR 40 Ω задаст runner.\n'
              'Уставки CH1/2/3: 4.00/1.50/2.00 V, 0.150/0.200/0.300 A;\n'
              'V-cap 4.00/4.05/4.10 V, software I-cap 0.300/0.400/0.500 A.\n'
              'Только CH1 ON: ожидается CV около 4.00 V / 0.100 A / 0.400 W.\n'
              'При текущих уставках CH1 ≤4.00 V / 0.150 A / 0.600 W; '
              'границы профиля CH1 ≤4.00 V / 0.300 A / 1.200 W.\n'
              'Запись 180 s, интервал 2 s; CH2/CH3 контролируются и остаются OFF.\n'
              'После 10 сканов пауза 12 s без разрыва связи; max_gap=10 s; '
              'один пропуск на канал исключается из Ah/Wh.\n'
              'CSV пересчитывается независимо: min/max/mean/population σ для U/I/P, '
              'Δt, трапеции Ah/Wh по monotonic_s; reset статистики и reset_energy.\n'
              'Допуски арифметики: статистика 1e-10, Ah 2e-9, Wh 1e-8, время 2e-6 s.\n'
              'Сверка KEL103: ΔV≤0.06 V, ΔI≤0.03 A, P≈VI с Δ≤0.31 W, '
              'как в H03; это контроль согласованности, не калибровка.\n'
              'SCPI: REM/LOC, OUTP 0, независимый режим, OFF таймеров, '
              'VOLT/CURR/VOLT:LIM профиля, CHAN:OUTP 1 только CH1, '
              'поканальные readbacks и SYST:ERR?; без RWLOCK, *CLS и reset.\n'
              'Бюджет 16000 команд / 300 s без времени ожидания оператора.\n'
              'При ошибке STOP, попытка OFF по живой связи, без reconnect; '
              'при неопределённости выключить Keithley сетевым выключателем и INPUT нагрузки.\n'
              'После записи runner выключит CH1 и INPUT, проверит OFF и запросит DISCONNECT.\n'
              'НЕ ВКЛЮЧАТЬ ВЫХОДЫ до разрешения H07. Токен:\n'
              f'{approval_token(load_serial_port)}')
        return
    if test_id == 'H02':
        from profile_stage import (H02_CHANNELS, H02_DEBUG_TOKEN, H02_PANEL_TOKEN,
                                   TOKEN as token, H02Scope)
        profile_map = '; '.join(
            f'CH{ch}: V={values[2]:.2f} V / I={values[3]:.3f} A, '
            f'V-cap={values[0]:.2f} V / software I-cap={values[1]:.3f} A'
            for ch, values in H02_CHANNELS.items())
        print(f'Порт: {port}; USB–TTL UART 9600 8N1, один владелец.\n'
              'CH1/CH2/CH3 OFF, клеммы свободны; без DUT и объединяющих перемычек.\n'
              'Нагрузка отсоединена от всех каналов, INPUT OFF; режим/значение не задавать.\n'
              'Output Recall=Off проверить на панели; доступен сетевой выключатель.\n'
              'Перед изменениями снимок V/I, лимита/state, выхода и таймера всех каналов.\n'
              f'Разные значения по каналам (CH1→CH3): {profile_map}.\n'
              'VOLT:LIM не является независимой OVP; отдельного hardware I-cap нет.\n'
              'Все выходы остаются OFF; если ошибочно включить после профиля, уставки '
              'равны 1.00/1.50/2.00 V, токи 0.100/0.200/0.300 A.\n'
              'Профильные верхние границы: 0.60/1.20/2.00 W на канал, 3.80 W суммарно.\n'
              'До применения/при отказе защиты возможны паспортные 30/30/5 V, 3 A, 195 W.\n'
              'Изменения: REM/LOC, OUTP 0, независимый режим, выбор CH, отключение таймеров,\n'
              'VOLT/CURR 0, затем по CH1/2/3 VOLT:LIM 2/3/4 V, CURR 0.100/0.200/0.300 A, '
              'VOLT 1.00/1.50/2.00 V. Без RWLOCK.\n'
              'После каждой записи и запроса, кроме LOC и самого ERR?, проверяется SYST:ERR?; без *CLS.\n'
              'Проверки: точные V/I (±0.0049 V / ±0.00049 A), V-cap ±0.0049 V, state=1,\n'
              'таймеры OFF, режим независимый, ON-биты всех CH=0, invalid V/I без SCPI.\n'
              'Обязательные проверки защиты: панельные Max Volt Set CH1/2/3=2/3/4 V, '
              'попытки 2.01/3.01/4.01 V; runner проверяет поканальный clamp.\n'
              'После panel edit профиль разоружается и повторно применяется штатной\n'
              'последовательностью. Обычный API отклоняет превышение каждого V/I cap до SCPI.\n'
              'Отдельный gate включает debug_bypass_profile только для CH1/2/3 VOLT 2.01/3.01/4.01;\n'
              'ожидаются 120,"Parameter overflowed" и сохранение исходных V-set 1.00/1.50/2.00 V.\n'
              'VOLT:LIM — потолок уставки, не независимая OVP.\n'
              'До профиля ON через API должен быть отвергнут без SCPI. ON после профиля не вызывается.\n'
              'Читаются внутренние U/I; точность и CV/CC здесь не квалифицируются.\n'
              'Один проход до 180 s. Первая ошибка останавливает настройки; на живой связи\n'
              'попытка OUTP 0 и проверка OFF. При отказе I/O — никаких следующих команд/повторов.\n'
              'Ручной останов: сетевой выключатель Keithley; проверить физический OFF.\n'
              'Итоговые настройки не откатываются, CH1 выбран, управление локальное.\n'
              f'Первый допуск H02: {token}\n'
              f'Panel gate: {H02_PANEL_TOKEN}\nDebug gate: {H02_DEBUG_TOKEN}\n'
              'SCPI command scope (VOLT 2.01/3.01/4.01 разрешены только по одному разу '
              'на соответствующем канале после debug gate; sequences are owned by the driver):')
        for command in sorted(H02Scope.READS | H02Scope.WRITES): print('  ' + command)
        print('Failure path, known live link only: OUTP 0; SYST:ERR?; per-channel OFF; SYST:ERR?; SYST:LOC.\n'
              'Внутри apply_profile/verify_profile возможна более ранняя попытка OUTP 0; общий адаптер ограничивает команды и проверяет ошибки.')
        return
    if test_id == 'H04P':
        from powered_profile_stage import H04P_MAXV_TOKEN, approval_token
        token = approval_token(load_transport, load_port, load_serial_port)
        load_connection = (f'KEL103 UDP {load_host}:{load_port}' if load_transport == 'udp'
                           else f'KEL103 USB {load_serial_port}, 115200 8N1')
        print(f'Keithley: {port}, USB–TTL 9600 8N1; {load_connection}. '
              'Independent CH1–CH3, без перемычек и DUT; Output Recall=Off; сетевой выключатель доступен.\n'
              'Начало: все выходы OFF, KEL103 INPUT OFF и нагрузка отсоединена. Подключается '
              'только один канал за раз, + к + и − к −. Runner задаёт CR и читает нагрузку.\n'
              'Профиль каждого канала: V-cap=4.50 V, I-cap=0.500 A; включается только '
              'испытываемый выход. Максимум 2.25 W; токовые точки при VSET=3.00 V — до 1.50 W. '
              'Паспортные unrestricted пределы проверяются только в OFF-only H04.\n'
              '21 точка: CV при 3.30 V, индивидуальной уставке CH1=2.20 V / CH2=3.30 V / '
              'CH3=4.40 V и граничных 4.50 V (CR 47/56 Ω); CC при 0.100/0.250/0.500 A '
              '(CR 22/11/5 Ω); затем фиксированный power_only preset. '
              'API-запросы 4.51 V / 0.501 A отклоняются до SCPI.\n'
              'До подключения нагрузки: Max Volt Set=4.50 V на всех каналах, все OFF. '
              'Сначала диагностический VOLT 4.51 без обхода должен дать soft-отказ без SCPI. '
              'Затем только на время пробы включается debug_bypass_profile: та же команда '
              'идёт через общий драйвер и должна вернуть ошибку прибора 120, сохранить '
              '3.30 V и после чтения ошибки оставить чистую очередь. Флаг сбрасывается в finally. '
              'Оператор проверяет Protection Settings → Max Volt Set On=4.50 V и пробует '
              'VSET=4.51 V на каждом выходе: панель должна ограничить его до 4.50 V. '
              'Runner перечитывает результат и возвращает 3.30 V до подключения нагрузки.\n'
              f'Подтверждение панели: {H04P_MAXV_TOKEN}\n'
              'На каждой powered-точке: INPUT OFF перед CR, проверка CR, INPUT ON, '
              'только выбранный PSU ON, запись PSU/KEL, затем PSU OFF, проверка всех OFF, '
              'KEL INPUT OFF. При ошибке — останов без повторных запросов. Общий адаптер '
              'проверяет очередь после записей и запросов, кроме LOC/ERR?. '
              'Предел: 5000 команд / 900 s плюс резерв на OFF; время ожидания оператора исключено.\n'
              f'Разрешение этапа: {token}\n'
              'Паузы на проводку: CONNECT CH1; MOVE CH1→CH2; MOVE CH2→CH3; DISCONNECT CH3.\n'
              'Финальная проверка: CONFIRM H04P CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        return
    if test_id == 'H05':
        from h05_stage import H05_TOKEN as token
        print(f'Keithley: {port}, USB–TTL 9600 8N1; KEL103 USB /dev/ttyACM0, 115200 8N1; '
              'Tektronix TDS 3064B at 192.168.22.105. Independent CH1–CH3, '
              'Output Recall=Off, all outputs OFF at start.\n'
              'Проводка, подтверждённая оператором: отрицательные клеммы CH1/CH2/CH3 '
              'объединены в общий возврат; все три земляных зажима осциллографа подключены '
              'только к этой точке; щупы CH1/CH2/CH3 ×10 — наконечник каждого к своей '
              'положительной клемме; CH4 свободен. Это временно соединяет минусы каналов '
              'между собой и с защитной землёй осциллографа, поэтому гальваническая '
              'изоляция в H05 не проверяется.\n'
              'KEL103: физически подключён только поперёк CH1, режим CR, 22 Ω; INPUT '
              'остаётся OFF до финального разрешения. CH2 и CH3 без нагрузки.\n'
              'Профиль: power_only, независимые каналы, каждый V=1.00 V / I=0.100 A, '
              'caps 1.00 V / 0.100 A. Фиксированная последовательность: включение '
              'CH1→CH2→CH3 с задержками перед CH2 и CH3 по 1 s; отключение '
              'CH3→CH2→CH1 с такими же минимумами. Ожидаемая мощность не выше 0.10 W '
              'на канал, 0.30 W суммарно; через CR22 на CH1 ожидаются около 45 mA.\n'
              'Осциллограф: один захват 10 kpts, Sample, 2 s/div, CH1 rising trigger '
              '0.5 V, позиция 10%; записать CH1–CH3 одновременно. Существующие probe '
              'factor=10 только прочитать и проверить, не менять. Начало фронта '
              'определять по 10% при включении и 90% при выключении; событие принимать '
              'только после подтверждения противоположного уровня. Это отсекает '
              'дрожание CH2 около 0.5 V на открытом выходе. Все пересечения 0.5 V '
              'сохранять отдельно для диагностики; интервалы брать с общей оси времени.\n'
              'Сценарий: после identity и исходной очереди ошибок выполнить SYST:REM '
              'до чтения статусов каналов; подтвердить все исходные OFF и профиль, проверить запреты '
              'set_channel/timer/arbitrary sequence без передачи SCPI, настроить KEL CR22, '
              'включить INPUT, запустить одиночный захват, выполнить power_on() и сразу '
              'power_off(), затем выключить INPUT и проверить все три PSU OFF и очередь '
              'ошибок. При любой ошибке — останов, попытка OFF только при живой связи; '
              'при потере связи проверить OFF вручную.\n'
              'НЕ ВКЛЮЧАТЬ ВЫХОДЫ до ввода точного подтверждения powered gate. '
              'Это подтверждение проводки и настройки стенда, а не разрешение включить '
              'выходы. Для H05 POWERED потребуется отдельное подтверждение ниже.\n'
              f'Разрешение на один powered проход: {token}\n'
              'Финальная ручная проверка: CONFIRM H05 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        return
    if test_id == 'H06':
        from h06_stage import approval_token
        token = approval_token(load_serial_port)
        print(f'Keithley: {port}, USB–TTL 9600 8N1; KEL103 USB {load_serial_port}, '
              '115200 8N1; независимый режим, без перемычек и DUT. Все выходы и INPUT OFF.\n'
              'Схема: KEL103 CR 22 Ω подключается только к одному каналу за раз; '
              'начать с CH1, затем переместить CH1→CH2→CH3, в конце снять с CH3. '
              'Полярность + к + и − к −; остальные выходные клеммы свободны.\n'
              'Разные уставки: CH1 1.00 V / 0.100 A; CH2 1.50 V / 0.200 A; '
              'CH3 2.00 V / 0.300 A. Пределы профиля: V=2/3/4 V, '
              'I=0.300/0.400/0.500 A; максимальная мощность тестовой нагрузки <0.19 W.\n'
              'На каждом канале: установить timer=3.0 s при OFF; включить только '
              'этот канал и нагрузку; записать время до автоматического OFF и сверить '
              'измерения Keithley/KEL103. Затем отключить таймер, повторно включить '
              'канал на 0.5 s, подтвердить устойчивое ON и штатно выключить его.\n'
              'Перед каждой перестановкой: все PSU outputs OFF, KEL INPUT OFF; '
              'runner проверяет это программно. На панели подтвердить Output Recall=Off.\n'
              'Ручной сетевой цикл Output Recall в этом прогоне НЕ выполняется: '
              'для него потребуется отдельное подтверждение, физическое отключение нагрузки '
              'и отдельный запуск после обзора отчёта.\n'
              'НЕ ВКЛЮЧАТЬ выходы до ввода точного разрешения H06 timer pass ниже.\n'
              'При ошибке/таймауте остановить этап; не повторять SCPI после потери связи. '
              'Runner попытается выключить все выходы и таймеры только пока связь жива.\n'
              f'Разрешение на H06 timer pass: {token}\n'
              'Паузы для нагрузки: CONNECT CH1; MOVE CH1->CH2; MOVE CH2->CH3; '
              'DISCONNECT CH3.\n'
              'Финальная проверка: CONFIRM H06 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
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
              'канала, все OFF, затем переключит PSU в Local. Сначала в Protection Settings → '
              'Max Volt Set включите On, поочерёдно выберите CH1/CH2/CH3 и установите '
              '2.00/3.00/4.00 V; убедитесь на экране, что значения не одинаковы. Затем отдельно '
              'задайте V-set: выбрать CH1 → V-set → 2.01 → Enter; CH2 → V-set → 3.01 → Enter; '
              'CH3 → V-set → 4.01 → Enter. Перед каждым вводом проверяйте выбранный канал. '
              'Ожидается ограничение до 2.00/3.00/4.00 V, не сохранение прежней '
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
        from channel_stage import approval_token, H03Scope
        token = approval_token(start_channel, load_transport, load_host,
                               load_port, load_serial_port, diagnose_status, load_connected)
        selected_scope = sorted(H03Scope.READS | H03Scope.WRITES)
        digest = hashlib.sha256('\n'.join(selected_scope).encode()).hexdigest()
        load_connection = (f'KEL103 UDP {load_host}:{load_port}' if load_transport == 'udp'
                           else f'KEL103 USB {load_serial_port}, 115200 8N1')
        from channel_stage import PROFILE_TOKEN
        profile_values = PROFILE_TOKEN
        print(f'Порт: {port}; USB–TTL UART 9600 8N1, один владелец. Независимые CH1–CH3; '
              f'без DUT/перемычек; {load_connection}; выбран вручную, без failover; '
              'один канал включён за раз.\n'
              f'Продолжение с CH{start_channel}; предыдущие точки должны быть в приложенном evidence.\n'
              f'Разные исходные уставки/пределы: {profile_values}.\n'
              'Перед каждой точкой при PSU/Input OFF runner задаёт V=4.00 V; '
              'CV использует I-cap канала 0.300/0.400/0.500 A.\n'
              'CV: CR 40.0 Ω, ожидается ~4.00 V / 0.100 A / 0.400 W.\n'
              'CC: V=4.00 V, I=0.250 A, CR 12.0 Ω; ожидается ~3.00 V / 0.250 A / 0.750 W.\n'
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
              'подтверждают режим CH3, библиотека возвращает тот же режим и OPER содержит ON, '
              'отсутствующие CV/CC-биты OPER записываются как оговорка. Если библиотечное состояние '
              'не подтверждает режим, точка получает PARTIAL.\n'
              f'Разрешённые Keithley SCPI-команды SHA256: {digest}; команд: {len(selected_scope)}.\n'
              f'Первый допуск до открытия портов: {token}\n'
              'Дальнейшие подтверждения подключения, CR и перестановки запрашиваются отдельно внутри runner.\n'
              'Финальное физическое подтверждение: CONFIRM H03 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        if load_connected:
            print('Recovery CH1 с уже подключённой нагрузкой: INPUT KEL103 должен быть OFF; '
                  'перед профилем проверит все выходы OFF. Убедитесь, что KEL103 '
                  'подключён только к CH1 с правильной полярностью; CH2/CH3 свободны.')
        if diagnose_status:
            print('ДИАГНОСТИКА CH3: реальные TX/RX-байты status-запросов; короткие и полные формы; '
                  'ранний снимок и снимок после подтверждения дисплея, не ранее 5 s после ON. '
                  'Затем повтор с выбранным CH1 (он остаётся OFF) и возврат выбора CH3. '
                  'Считываются все три condition-регистра и ENAB CH3; маски не изменяются. '
                  'Event-регистры, *CLS, *RST и калибровка не используются. '
                  'CV/CC, CR и пределы прежние. Диагностические данные требуют review; '
                  'отсутствие mode-битов OPER записывается как status caveat, не скрывается.')
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
          'Мутации: SYST:REM / SYST:LOC и выбор CH1–CH3 для чтения всех настроек; '
          'исходный выбор канала восстанавливается. Уставки и выходы не изменяются. '
          'SYST:ERR? после команд/запросов извлекает записи ошибок и сохраняет их в журнале.\n'
          'Все ERR? должны вернуть 0; любой ненулевой код сохраняется и останавливает этап. Без *CLS.\n'
          'Нет OUTP 1/0, RWLOCK, *CLS, *RST или изменения уставок.\n'
          'V/I, лимиты, таймеры и ON/OFF перечитываются отдельно на CH1/CH2/CH3.\n'
          'Ожидание: корректный IDN, повторяемый OFF в бите 3 каждого ISUM, корректные типы ответов.\n'
          'Стоп: при любой ошибке прекратить SCPI; вручную проверить OFF.\n'
          'Физический останов: сетевой выключатель Keithley; не полагаться на SCPI.\n'
          'Предел: один проход, до 120 s, без повторного подключения.\n'
          f'Ввод перед открытием порта: {TOKEN}\nSCPI plan:')
    for command in H01_PLAN: print('  ' + command)
    print('Затем общий channel_snapshot() для CH1/CH2/CH3; возврат исходного выбора и SYST:LOC.')


def h01(transport, evidence):
    """One passive electrical probe. No OFF on exit: H01 permits no output writes.

    An exception makes the link uncertain: no further SCPI is attempted. A live
    semantic failure may restore local control. The operator checks physical OFF.
    """
    from common import StageAdapter, channel_snapshot, require_outputs
    from keithley2231a import InstrumentError, Keithley2231A

    class H01Scope(StageAdapter):
        READS = frozenset(c for c in H01_PLAN if c.endswith('?'))
        WRITES = frozenset(('SYST:REM', 'SYST:LOC', 'INST:NSEL 1', 'INST:NSEL 2', 'INST:NSEL 3'))
        MAX_SECONDS = 120

    dev = Keithley2231A(transport, debug=True,
                         debug_trace=lambda record: evidence.add('driver_trace', trace=record),
                         debug_adapter=lambda channel: H01Scope(channel, evidence))
    outcome, reason, live = 'ABORTED', 'incomplete', True
    start = time.monotonic()
    initial_selection = None
    try:
        for command in H01_PLAN:
            if time.monotonic() - start > 120:
                raise TimeoutError('stage duration exceeded')
            try:
                answer = dev.diagnostic_query(command) if command.endswith('?') else None
                if answer is None: dev.diagnostic_write(command)
            except InstrumentError:
                raise
            except BaseException as exc:
                live = False
                # Do not serialize exception messages: an undecodable IDN reply
                # might contain a serial number not yet known to the redactor.
                evidence.add('io_error', command=command,
                             exception_type=type(exc).__name__,
                             cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
                raise
            if answer is None: continue
            if command == '*IDN?':
                pass  # The driver validates identity; revision is not an identity key.
            elif command == 'SYST:ERR?':
                code, message = answer.split(',', 1)
                code = int(code.strip())
                if code != 0 or not message.strip():
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
                if command == 'INST:NSEL?': initial_selection = int(value)
        require_outputs(dev)
        for channel in (1, 2, 3):
            values = channel_snapshot(dev, channel, measured=True)
            if values['CHAN:OUTP?']:
                raise ValueError(f'CH{channel} unexpectedly ON')
            evidence.add('channel_snapshot', channel=channel, phase='initial', values=values)
        outcome, reason = 'PASS', 'query checks passed; physical OFF confirmation still required'
    except (ValueError, InstrumentError) as exc:
        outcome, reason = 'FAIL', str(exc)
    except BaseException:
        outcome, reason = 'ABORTED', 'I/O failure or interruption; UNKNOWN OUTPUT STATE; no automatic reconnect'
    finally:
        try:
            if live:
                if initial_selection is not None:
                    dev.diagnostic_write(f'INST:NSEL {initial_selection}')
                dev.diagnostic_write('SYST:LOC')
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
    current_source = software_metadata()['source_sha256']
    events = []
    for evidence_path in paths:
        records = [json.loads(line) for line in Path(evidence_path).read_text(encoding='utf-8').splitlines()]
        if not any(r.get('event') == 'metadata' and r.get('source_sha256') == current_source for r in records):
            raise ValueError('Prior H03 evidence does not match current source; repeat all affected points')
        if not any(r.get('event') == 'outcome' and r.get('physical_off_confirmed') is True for r in records):
            raise ValueError('prior H03 run lacks final operator OFF confirmation')
        events.extend(records)
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
    print(f'H03 checkpoint accepted: reviewed {len(expected)} prior points; prior log retained.')


def main(argv=None):
    """Dry run is default; each prepared hardware group has its own operator gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'preflight', 'run', 'report', 'abort'))
    parser.add_argument('test_id', nargs='?', choices=tuple(stages()))
    parser.add_argument('--port', default='/dev/ttyUSB1')
    parser.add_argument('--meter-host', default='192.168.22.112', help='H08 DMM7510 raw TCP port 5025')
    parser.add_argument('--load-host', default='192.168.22.106')
    parser.add_argument('--load-port', type=int, default=18190)
    parser.add_argument('--load-transport', choices=('udp', 'serial'), default='udp',
                        help='explicit KEL103 connection; never auto-switches on failure')
    parser.add_argument('--load-serial-port', default='/dev/ttyACM0')
    parser.add_argument('--start-channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--load-connected', action='store_true',
                        help='H03 CH1 recovery only: load remains wired, INPUT OFF; verify all PSU outputs OFF first')
    parser.add_argument('--diagnose-status', action='store_true',
                        help='H03 CH3 only: wire trace, delayed and channel-selection status snapshots')
    parser.add_argument('--recall', action='store_true',
                        help='H06 only: separately gated Output Recall mains cycles, all terminals empty')
    parser.add_argument('--prior-evidence', type=Path, action='append',
                        help='H03 evidence log; repeat once per completed-channel log when resuming')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    if args.recall and args.test_id != 'H06':
        parser.error('--recall requires H06')
    if args.load_connected and (args.test_id != 'H03' or args.start_channel != 1):
        parser.error('--load-connected requires H03 start channel 1')
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
              args.start_channel, args.load_transport, args.load_serial_port,
              args.diagnose_status, args.load_connected, args.recall, args.meter_host)
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
    if args.test_id not in ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07', 'H08'):
        print('BLOCKED: выполнение этой группы ещё не подготовлено; порт не открыт.')
        return 2
    if args.test_id in ('H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07') and not stage_passed(stages()['H01'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H01; порт не открыт.')
        return 2
    if args.test_id in ('H03', 'H04', 'H04P', 'H05', 'H06', 'H07') and not stage_passed(stages()['H02'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H02; порт не открыт.')
        return 2
    if args.test_id in ('H04', 'H04P', 'H05', 'H06', 'H07') and not stage_passed(stages()['H03'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H03; порт не открыт.')
        return 2
    if args.test_id in ('H05', 'H06', 'H07') and not stage_passed(stages()['H04P'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H04P; порт не открыт.')
        return 2
    if args.test_id in ('H06', 'H07') and not stage_passed(stages()['H05'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H05; порт не открыт.')
        return 2
    if args.test_id == 'H07' and not stage_passed(stages()['H06'][-1]):
        print('BLOCKED: требуется рассмотренный PASS H06; порт не открыт.')
        return 2
    token, final_token, runner = TOKEN, FINAL_TOKEN, h01
    if args.test_id == 'H08':
        required = ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07')
        if any(not stage_passed(stages()[stage][-1]) for stage in required):
            print('BLOCKED: H08 requires reviewed PASS H01–H07 including H04P; ports not opened.')
            return 2
        from h08_stage import approval_token, FINAL_TOKEN as final_token, h08
        token, runner = approval_token(args.meter_host), h08
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
    if args.test_id == 'H05':
        from h05_stage import H05_TOKEN as token, h05
        runner = h05
        final_token = 'CONFIRM H05 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
        if not stage_passed(stages()['H04P'][-1]):
            print('BLOCKED: требуется рассмотренный PASS H04P; порты не открыты.')
            return 2
        if args.load_transport != 'serial' or args.load_serial_port != '/dev/ttyACM0':
            print('BLOCKED: H05 uses the explicitly preflighted KEL103 USB port /dev/ttyACM0.')
            return 2
    if args.test_id == 'H06':
        from h06_stage import FINAL_TOKEN as final_token, approval_token, h06
        if args.recall:
            from h06_recall import TOKEN as token, recall as runner
        else:
            token, runner = approval_token(args.load_serial_port), h06
        if not args.recall and (args.load_transport != 'serial' or
                                args.load_serial_port != '/dev/ttyACM0'):
            print('BLOCKED: H06 uses the reviewed KEL103 USB port /dev/ttyACM0.')
            return 2
    if args.test_id == 'H03':
        from channel_stage import approval_token, FINAL_TOKEN as final_token, h03
        token = approval_token(args.start_channel, args.load_transport,
                               args.load_host, args.load_port, args.load_serial_port,
                               args.diagnose_status, args.load_connected)
        runner = h03
    if args.test_id == 'H07':
        from h07_stage import FINAL_TOKEN as final_token, approval_token, h07
        token, runner = approval_token(args.load_serial_port), h07
        if args.load_transport != 'serial':
            print('BLOCKED: H07 requires the reviewed KEL103 USB connection; ports not opened.')
            return 2
    if not sys.stdin.isatty():
        print('BLOCKED: требуется интерактивный терминал; пакетный ввод запрещён.')
        return 2
    try:
        adapter = input('Проверенный USB–TTL адаптер (модель, USB VID:PID; без серийного номера): ').strip()
        if not adapter:
            print('BLOCKED: не указан проверенный адаптер.')
            return 2
        try:
            verify_serial_adapter(args.port, adapter)
        except (ImportError, OSError, ValueError) as exc:
            print(f'BLOCKED: {exc}; последовательный порт не открыт.')
            return 2
        while input(f'Подтверждение:\n{token}\n> ').strip() != token:
            print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
    except (EOFError, KeyboardInterrupt):
        print('ABORTED до открытия порта.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_{args.test_id}/events.jsonl', args.test_id)
    evidence.add('metadata', **software_metadata(), port=args.port, baud=9600, framing='8N1',
                 adapter=adapter,
                 topology=('tracking CH1/CH2; DMM on one channel at a time; no jumpers; CH3 disconnected'
                           if args.test_id == 'H08' else
                           'independent; CH1/CH2/CH3 negative terminals tied to scope common'
                           if args.test_id == 'H05' else 'independent; no jumpers; no DUT'),
                 subgroup='output_recall' if args.recall else None,
                 load=('disconnected; KEL103 INPUT OFF; scope disconnected; no jumpers'
                       if args.recall else
                       f'KORAD KEL103; UDP port {args.load_port}; input OFF'
                       if args.test_id in ('H03', 'H04P') and args.load_transport == 'udp' else
                       f'KORAD KEL103; USB serial {args.load_serial_port}; '
                       '115200 8N1; USB link previously HW OBSERVED on H03; input OFF'
                       if args.test_id in ('H03', 'H04P', 'H05', 'H06', 'H07')
                       else 'disconnected; input OFF'),
                 proposed_caps=('H08 V-cap 2/3/4 V, I-cap 0.300/0.400/0.500 A; '
                                'I-set 0.100/0.200/0.300 A; CH1 1..1.4 V, CH2 1..2.8 V, CH3 0.5 V'
                                if args.test_id == 'H08' else
                                'unchanged; initial unknown' if args.test_id == 'H01' else
                                'each CH: V-cap 4.00 V, software I-cap 0.500 A; '
                                'initial 4.00 V / 0.500 A' if args.test_id == 'H03' else
                                'H04: unrestricted caps 30/30/5 V, 3 A; setpoints 0 V / 0.100 A; '
                                'per-channel manual caps 2/3/4 V and 0.2/0.3/0.4 A; '
                                'then power_only caps 4 V / 0.500 A, setpoints 1.00 V / 0.100 A'
                                if args.test_id == 'H04' else
                                'H04P powered: control/power_only V-cap 4.50 V, I-cap 0.500 A; '
                                'one CH at a time; low-power CV/CC; KEL103 CR 5/12/22/47/56 Ω'
                                if args.test_id == 'H04P' else
                                'H05 power_only: each CH 1.00 V / 0.100 A, caps 1.00 V / 0.100 A; '
                                'CH1→CH2→CH3 and reverse OFF with 1 s delays; KEL103 CR22 on CH1'
                                if args.test_id == 'H05' else
                                'H06 Recall: CH1/2/3 1.00/1.50/2.00 V and 0.100/0.200/0.300 A; '
                                'V-cap 2/3/4 V, software I-cap 0.300/0.400/0.500 A; '
                                'timers OFF; no load; four manually gated mains cycles'
                                if args.recall else
                                'H06 timer test: CH1/2/3 1.00/1.50/2.00 V and 0.100/0.200/0.300 A; '
                                '3.0 s timer, KEL103 CR22; Output Recall cycle separately gated'
                                if args.test_id == 'H06' else
                                'H07 CH1/2/3 4.00/1.50/2.00 V and 0.150/0.200/0.300 A; '
                                'V-cap 4.00/4.05/4.10 V; I-cap 0.300/0.400/0.500 A; '
                                'only CH1 ON, CR40; 180 s acquisition with one 12 s sampling pause'
                                if args.test_id == 'H07' else
                                'each CH: V-cap 4.00 V, software I-cap 0.500 A; initial 1.00 V / 0.100 A'),
                 independent_meter=({'model': 'DMM7510', 'host': args.meter_host, 'port': 5025,
                                     'function': 'DCV 10 V, NPLC 1, front terminals'}
                                    if args.test_id == 'H08' else None))
    evidence.add('approval', token=token)
    if args.diagnose_status:
        evidence.add('diagnostic_scope', channel=3, wire_trace='status commands only',
                     late_sample_min_s=5.0, selections=(3, 1),
                     condition_registers='all channels; short and full forms on CH3',
                     enable_masks='read only; CH3')
    if args.test_id == 'H03' and args.start_channel > 1:
        evidence.add('resume_from', start_channel=args.start_channel,
                     prior_evidence=[path.name for path in args.prior_evidence])
    load, meter, transport = None, None, None
    try:
        if args.test_id == 'H08': meter = open_meter(args.meter_host, evidence)
        if args.test_id in ('H03', 'H04P', 'H05', 'H06', 'H07') and not args.recall:
            from channel_stage import KEL103Serial, KEL103UDP
            load = (KEL103UDP(args.load_host, args.load_port, evidence=evidence)
                    if args.load_transport == 'udp' else
                    KEL103Serial(args.load_serial_port, evidence=evidence))
        transport = open_transport(args.port)
    except Exception:
        for instrument in (load, meter, transport):
            if instrument is not None:
                try: instrument.close()
                except Exception: pass
        evidence.add('outcome', outcome='ABORTED', stop_reason='port open failed; verify OFF manually')
        print(f'ABORTED: порт не открыт; проверить OFF вручную. Evidence: {evidence.path}')
        return 2
    def reopen_recall_transport():
        """Open once after a completed operator cycle; never scan or auto-retry."""
        verify_serial_adapter(args.port, adapter)
        return open_transport(args.port)

    outcome = (runner(transport, evidence, meter) if args.test_id == 'H08' else
               runner(transport, evidence, reopen_recall_transport) if args.recall else
               runner(transport, evidence, load, args.start_channel,
                      diagnose_status=args.diagnose_status,
                      load_connected=args.load_connected)
               if args.test_id == 'H03' else
               runner(transport, evidence, load) if args.test_id in ('H04P', 'H05', 'H06', 'H07') else
               runner(transport, evidence))
    physical_off = False
    try:
        while input(f'После ручной проверки введите:\n{final_token}\n> ').strip() != final_token:
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
