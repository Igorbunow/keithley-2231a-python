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

from bench_config import load_bench, activate_bench, current_bench, EXAMPLE, bench_summary
from connection_gate import (connection_card, connection_token, required_devices,
                             verify_serial_adapter)
from equipment.factory import open_psu, open_load, open_meter as equipment_open_meter

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
# StageAdapter performs one SYST:ERR? after every command except ERR? and LOC.
# Avoid a second, explicit read immediately after an already-checked query.
H01_PLAN = ('SYST:REM', '*IDN?', *OFF_QUERIES, 'SYST:VERS?', *STATE_QUERIES,
            '*IDN?', *OFF_QUERIES)


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
    # Matrix cells may append Markdown evidence links after the status. Parse
    # the status token itself; never let a later historical PASS override FAIL.
    normalized = status.strip()
    if normalized.startswith('**'):
        closing = normalized.rfind('**')
        if closing > 1:
            normalized = normalized[2:closing].strip()
    else:
        normalized = re.sub(r'^\*\*(PASS)\*\*', r'\1', normalized)
    return re.fullmatch(r'PASS(?:\s+\(.*\))?', normalized) is not None


def software_metadata():
    """Identify the checked-out implementation, including uncommitted sources."""
    def git(*args):
        try:
            return subprocess.check_output(['git', '-c', f'safe.directory={ROOT}', *args],
                                           cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            return 'unversioned'
    digest = hashlib.sha256()
    for pattern in ('src/keithley2231a/*.py', 'tools/hw_qualify.py', 'tests/hardware/*.py',
                    'tests/hardware/equipment/*.py', 'config/bench.example.toml'):
        for path in sorted(ROOT.glob(pattern)):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    return {'sha': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
            'source_sha256': digest.hexdigest(), 'library_version': '0.2.1.dev0',
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
        revision_change = None
        if (fields.get('command') == '*IDN?' and 'response' in fields
                and not event.startswith('kel103_')):
            parts = fields['response'].split(',')
            if len(parts) == 4:
                # Only PSU-owned identity events may update PSU firmware metadata.
                # Other *IDN? responses (scope/DMM) still need serial redaction,
                # but must NEVER be interpreted as a Keithley 2231A revision.
                psu_identity = (event in ('read', 'psu_identity')
                                and parts[0].strip().casefold() in ('keithley', 'keithley instruments')
                                and parts[1].strip().casefold() == '2231a-30-3')
                if psu_identity:
                    observed = parts[3].strip()
                    if self.firmware is not None and observed != self.firmware:
                        revision_change = (self.firmware, observed)
                    self.firmware = observed
                    if observed not in self.firmware_observations:
                        self.firmware_observations.append(observed)
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
        if revision_change is not None:
            self.add('firmware_revision_drift', before=revision_change[0],
                     after=revision_change[1],
                     classification='OBSERVED_ONLY: firmware metadata, not identity',
                     note='Do not suppress manufacturer/model/serial or SCPI safety checks')
        return record


def open_transport(port):
    """Open the PSU using the validated active bench; never scan or fallback."""
    bench = current_bench()
    if port != bench.psu.port:
        raise ValueError('PSU port differs from the active bench configuration')
    return open_psu(bench)


def open_meter(host, evidence):
    """Open the configured meter through the only shared factory."""
    bench = current_bench()
    if host != bench.meter.host:
        raise ValueError('meter host differs from the active bench configuration')
    return equipment_open_meter(bench, evidence)



def preflight(test_id, port=None, load_host=None, load_port=None, start_channel=1,
              load_transport=None, load_serial_port=None, diagnose_status=False,
              load_connected=False, recall=False, meter_host=None, powered=False,
              high_current=False, profile_only=False, off_only=False):
    """Print a human-readable card; never contact hardware."""
    bench = current_bench()
    if port is None: port = bench.psu.port
    if load_host is None: load_host = bench.load.host
    if load_port is None: load_port = bench.load.network_port
    if load_transport is None: load_transport = bench.load.transport
    if load_serial_port is None: load_serial_port = bench.load.port
    if meter_host is None: meter_host = bench.meter.host
    if test_id == 'H06' and recall:
        from h06_recall import preflight as recall_preflight
        print('[H06 Output Recall] Отдельная подгруппа ручных сетевых циклов')
        recall_preflight(port)
        return
    print(bench_summary(bench))
    row = stages()[test_id]
    if test_id == 'H10':
        if not (powered or high_current or off_only):
            print('[H10 FULL] OFF diagnostic → low-power CV/CC → high-current CV3A/CC4A.')
            print('Одна CLI-команда, три независимых допуска и три отдельных evidence; '
                  'каждый этап заканчивается подтверждённым OFF. Между этапами — '
                  'физическое переподключение ТОЛЬКО при OFF, после отдельного подтверждения оператора.')
            print('OFF-диагностика штатно возвращает PARTIAL; FULL PASS допустим '
                  'только после её успешного завершения и двух powered PASS.')
            for title, opts in (('OFF diagnostic', {'off_only': True}),
                                ('low-current CV/CC', {'powered': True}),
                                ('high-current CV3A/CC4A', {'high_current': True})):
                print(f'\n--- H10 FULL / {title} ---')
                preflight(test_id, port, load_host, load_port, start_channel,
                          load_transport, load_serial_port, diagnose_status,
                          load_connected, recall, meter_host,
                          powered=opts.get('powered', False),
                          high_current=opts.get('high_current', False),
                          off_only=opts.get('off_only', False))
            return
        if powered or high_current:
            from h10_powered_stage import preflight as h10_preflight
            print('[H10-High-Current] Parallel — отдельные gated-точки CV ~3 A / CC 4 A'
                  if high_current else
                  '[H10-Powered] Parallel — low-power CV/CC с внешними измерителями')
            h10_preflight(port, meter_host, load_serial_port, load_transport,
                          high_current=high_current)
            return
        from combined_off_stage import preflight as combined_preflight
        combined_preflight(port, test_id)
        return
    if test_id == 'H09':
        if powered:
            if load_transport != 'serial':
                print('H09 powered preflight requires --load-transport serial; no hardware accessed.')
                return
            print('[H09-Powered] Series — CV/CC с внешней нагрузкой и DMM7510')
            from h09_powered_stage import preflight as h09_preflight
            h09_preflight(port, args_meter_host=meter_host,
                          load_transport=load_transport,
                          load_serial_port=load_serial_port)
        else:
            print('[H09] Series — OFF-подгруппа')
            from combined_off_stage import preflight as h09_preflight
            h09_preflight(port)
        return
    if not (test_id == 'H02' and profile_only):
        print(f'[{test_id}] {row[0]}\nТопология/нагрузка: {row[1]}\nКритерий: {row[2]}')
    if test_id == 'O01':
        from o01_stage import preflight as o01_preflight
        o01_preflight(port, meter_host)
        return
    if test_id == 'H08':
        from h08_stage import preflight as h08_preflight
        h08_preflight(port, meter_host)
        return
    if test_id == 'H07':
        from h07_stage import approval_token
        print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config); KEL103 USB '
              f'{load_serial_port}, 8N1 (скорость из bench config). Один владелец каждого порта.\n'
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
              'Сверка KEL103: ΔV≤0.06 V, ΔI≤0.03 A, три проверки P с Δ≤0.025 W '
              '(U×I каждого прибора и прямое сравнение); не калибровка.\n'
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
    if test_id == 'H12':
        from h12_stage import preflight as h12_preflight
        h12_preflight(port)
        return
    if test_id == 'H11':
        from h11_stage import preflight as h11_preflight
        h11_preflight(port, load_serial_port, load_transport)
        return
    if test_id == 'H02' and profile_only:
        from profile_stage import H02_CHANNELS, PROFILE_ONLY_TOKEN
        print('R3.5.2: OFF-only Independent profile verification (NOT full H02).')
        print(f'Keithley: {port}; параметры из bench config. Все CH OFF; нагрузка INPUT OFF '
              'и физически отсоединена; клеммы свободны, нет перемычек, DUT и общей шины. '
              'Output Recall=Off; аварийный сетевой выключатель доступен.')
        for ch, (v_cap, i_cap, v_set, i_set) in H02_CHANNELS.items():
            print(f'CH{ch}: V-set={v_set:.2f} V, I-set={i_set:.3f} A; '
                  f'V-cap={v_cap:.2f} V, software I-cap={i_cap:.3f} A.')
        print('Применение того же guarded_profile() и тех же проверок readback через '
              'основной драйвер. Выполняются OUTP 0, смена/подтверждение Independent, '
              'программирование U/I и ограничений, проверка всех OFF; без OUTP 1, '
              'без пользовательского panel-edit, debug bypass и ожидаемых SCPI ошибок. '
              'Уставки сохраняются после проверки, но выходы остаются OFF. '
              'Нормальная остановка требует подтверждённого OFF; при неуверенной связи '
              'состояние UNKNOWN, физический останов, без reconnect.\n'
              f'Подтверждение схемы: {PROFILE_ONLY_TOKEN}\n'
              'Это частичная переквалификация R3.5.2, не замена полного H02.')
        return
    if test_id == 'H02':
        from profile_stage import (H02_CHANNELS, H02_DEBUG_TOKEN, H02_PANEL_TOKEN,
                                   TOKEN as token, H02Scope)
        profile_map = '; '.join(
            f'CH{ch}: V={values[2]:.2f} V / I={values[3]:.3f} A, '
            f'V-cap={values[0]:.2f} V / software I-cap={values[1]:.3f} A'
            for ch, values in H02_CHANNELS.items())
        print(f'Порт: {port}; USB–TTL UART 8N1 (скорость из bench config), один владелец.\n'
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
                           else f'KEL103 USB {load_serial_port}, 8N1 (скорость из bench config)')
        print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config); {load_connection}. '
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
              'Предел: 8000 команд / 900 s плюс резерв 128 команд на OFF; время ожидания оператора исключено.\n'
              f'Разрешение этапа: {token}\n'
              'Паузы на проводку: CONNECT CH1; MOVE CH1→CH2; MOVE CH2→CH3; DISCONNECT CH3.\n'
              'Финальная проверка: CONFIRM H04P CH1=OFF CH2=OFF CH3=OFF LOAD=OFF')
        return
    if test_id == 'H05':
        from h05_stage import approval_token
        token = approval_token()
        print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config); KEL103 USB {load_serial_port}, 8N1 (скорость из bench config); '
              f'Tektronix TDS 3064B at {current_bench().scope.host}. Independent CH1–CH3, '
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
        print(f'Keithley: {port}, USB–TTL 8N1 (скорость из bench config); KEL103 USB {load_serial_port}, '
              '8N1 (скорость из bench config); независимый режим, без перемычек и DUT. Все выходы и INPUT OFF.\n'
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
        print(f'Порт: {port}; USB–TTL 8N1 (скорость из bench config), один владелец. Независимые CH1–CH3, '
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
                           else f'KEL103 USB {load_serial_port}, 8N1 (скорость из bench config)')
        from channel_stage import PROFILE_TOKEN
        profile_values = PROFILE_TOKEN
        print(f'Порт: {port}; USB–TTL UART 8N1 (скорость из bench config), один владелец. Независимые CH1–CH3; '
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
    print(f'Порт: {port}; USB–TTL UART, 8N1 (скорость из bench config); один владелец.\n'
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
        READS = frozenset(c for c in H01_PLAN if c.endswith('?')) | {'SYST:ERR?'}
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
            # output_states() has just restored the original selected channel.
            # Do not issue an immediately repeated INST:NSEL 1 when it is already
            # selected; channel_snapshot still validates the live INST:NSEL?.
            values = channel_snapshot(dev, channel, measured=True,
                                      select=not (channel == 1 and initial_selection == 1))
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


H10_FULL_TOKEN = ('CONFIRM H10 FULL SEQUENTIAL OFF_THEN_POWERED_THEN_HIGH_CURRENT '
                  'SEPARATE_GATES NO_AUTOMATIC_REWIRING STOP_ON_FAILURE')
H10_FULL_GROUPS = (
    ('off', '--off-only', 'parallel_off_diagnostic', 'PARTIAL'),
    ('powered', '--powered', 'parallel_powered_cv_cc', 'PASS'),
    ('high_current', '--high-current', 'parallel_high_current_cv_cc', 'PASS'),
)


def _h10_verify_subrun(path, bench_sha, source_sha, subgroup, expected):
    """Fail closed on absent, foreign, or incomplete freshly-generated evidence."""
    if not isinstance(path, Path):
        raise ValueError('H10 full subrun did not return an evidence path')
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    def single(kind):
        records = [event for event in events if event.get('event') == kind]
        if len(records) != 1:
            raise ValueError(f'H10 full requires one {kind}: observed {len(records)}')
        return records[0]
    metadata = single('metadata')
    bench = single('bench_configuration')
    query = single('query_outcome')
    outcome = single('outcome')
    if metadata.get('test_id') != 'H10' or metadata.get('subgroup') != subgroup:
        raise ValueError('H10 full subrun subgroup/identity mismatch')
    if (metadata.get('source_sha256') != source_sha or
            bench.get('sha256') != bench_sha):
        raise ValueError('H10 full subrun source/config drift')
    if (query.get('outcome') != expected or outcome.get('outcome') != expected or
            outcome.get('physical_off_confirmed') is not True):
        raise ValueError('H10 full subrun status or operator OFF confirmation is incomplete')
    if any(event.get('event') in ('stage_error', 'io_error') for event in events):
        raise ValueError('H10 full subrun contains a recorded error')
    writes = [(index, event['command']) for index, event in enumerate(events)
              if event.get('event') == 'write']
    if subgroup == 'parallel_off_diagnostic':
        if any(cmd in ('OUTP 1', 'CHAN:OUTP 1') for _, cmd in writes):
            raise ValueError('H10 OFF diagnostic contains an output enable')
        if not all(any(cmd == required for _, cmd in writes)
                   for required in ('OUTP:PARALLEL 1', 'OUTP:PARALLEL 0')):
            raise ValueError('H10 OFF diagnostic omitted a parallel-mode transition')
        if not any(event.get('phase') == 'independent_after_restoration'
                   for event in events):
            raise ValueError('H10 OFF diagnostic lacks final Independent snapshot')
    else:
        if not any(event.get('event') == 'topology_restore' and
                   event.get('state') == 'VERIFIED_INDEPENDENT' for event in events):
            raise ValueError('H10 powered subrun has no verified Independent restore')
        if not any(event.get('event') == 'electrical_shutdown' and
                   event.get('state') == 'VERIFIED_OFF' and
                   {'Keithley', 'KEL103'} <= set(event.get('devices', ()))
                   for event in events):
            raise ValueError('H10 powered subrun has no verified PSU/KEL electrical shutdown')
        if not any(event.get('event') == 'operator_gate' and
                   event.get('action') == 'disconnect_parallel_wiring' for event in events):
            raise ValueError('H10 powered subrun has no confirmed physical jumper disconnection')
        on = [index for index, cmd in writes if cmd == 'OUTP 1']
        if (len(on) != 2 or not any(on[0] < index < on[1] for index, cmd in writes
                                    if cmd == 'OUTP 0')):
            raise ValueError('H10 powered subrun lacks two distinct ON points and an intermediate OFF')
        points = ('CV', 'CC') if subgroup == 'parallel_powered_cv_cc' else ('CV3A', 'CC4A')
        samples_by_point = []
        for point in points:
            samples = [index for index, event in enumerate(events)
                       if event.get('event') == 'parallel_measurement_sample'
                       and event.get('point') == point]
            reviews = [index for index, event in enumerate(events)
                       if event.get('event') == 'operator_display_review'
                       and event.get('point') == point and event.get('outcome') == 'confirmed']
            if len(samples) != 4 or len(reviews) != 1:
                raise ValueError(f'H10 powered subrun incomplete at {point}')
            mode = 'CV' if point.startswith('CV') else 'CC'
            if ([(events[index].get('sample')) for index in samples] != [1, 2, 3, 4] or
                    any(events[index].get('psu_regulation') != mode for index in samples)):
                raise ValueError(f'H10 powered subrun at {point} has missing sample indexes or ambiguous CV/CC')
            samples_by_point.append((samples, reviews))
        if not (on[0] < samples_by_point[0][0][0] <= samples_by_point[0][0][-1] <
                samples_by_point[0][1][0] < on[1] < samples_by_point[1][0][0] <=
                samples_by_point[1][0][-1] < samples_by_point[1][1][0]):
            raise ValueError('H10 powered subrun measurements/display reviews are out of order')
    return {'subgroup': subgroup, 'outcome': expected, 'evidence': str(path),
            'firmware': query.get('firmware')}


def _h10_full_sequence(args, bench):
    """Three bounded fresh subruns; no shared transport, no automatic power reruns."""
    print('[H10 FULL] OFF diagnostic → low-power CV/CC → high-current CV3A/CC4A.')
    print('Каждый подпроход включает собственную проверку USB, отдельный токен схемы, '
          'подтверждение OFF и закрытие соединений. Силовые кабели устанавливаются '
          'оператором заново ТОЛЬКО после OFF; без автоматического переподключения.')
    if args.command == 'preflight' or not args.execute:
        for title, flag, _, _ in H10_FULL_GROUPS:
            print(f'\n=== H10 FULL PREVIEW: {title} ===')
            main([args.command, 'H10', flag, '--config', str(args.config), '--dry-run']
                 if args.command == 'run' else
                 [args.command, 'H10', flag, '--config', str(args.config)])
        print('DRY RUN H10 FULL: никакие приборы не открывались; '
              'для аппаратного прогона требуется --execute и новые подтверждения.')
        return 0
    if bench.load.transport != 'serial':
        print('BLOCKED: H10 FULL requires KEL103 serial; ports not opened.')
        return 2
    prerequisites = ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05',
                     'H06', 'H07', 'H08', 'H09')
    if any(not stage_passed(stages()[stage][-1]) for stage in prerequisites):
        print('BLOCKED: H10 FULL requires reviewed prerequisites H01–H09, including H04P; ports not opened.')
        return 2
    if not sys.stdin.isatty():
        print('BLOCKED: H10 FULL requires an interactive terminal; ports not opened.')
        return 2
    try:
        if input(f'Подтвердите план полного H10 (не разрешает включение):\n'
                 f'{H10_FULL_TOKEN}\n> ').strip() != H10_FULL_TOKEN:
            print('BLOCKED: H10 FULL plan was not confirmed; ports not opened.')
            return 2
    except (KeyboardInterrupt, EOFError):
        print('ABORTED: H10 FULL plan not confirmed; ports not opened.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    summary = Evidence(ROOT / f'reports/hardware/{now}_H10_FULL/events.jsonl', 'H10')
    source_sha = software_metadata()['source_sha256']
    summary.add('full_plan', subgroup='parallel_full_sequence',
                config_sha256=bench.sha256, source_sha256=source_sha,
                stages=[name for name, _, _, _ in H10_FULL_GROUPS],
                gating='fresh approval and final OFF for every separate hardware subrun')
    completed = []
    result = 'ABORTED'
    reason = 'H10 FULL interrupted'
    try:
        for name, flag, subgroup, expected in H10_FULL_GROUPS:
            print(f'\n=== H10 FULL STEP {len(completed)+1}/3: {name} ===')
            if name != 'off':
                print('Предыдущий этап завершён и его evidence проверен. '
                      'Следующая схема подключается оператором при PSU ALL OFF и KEL OFF; '
                      'новое подключение НЕ считается разрешённым автоматически.')
            summary.add('full_step_start', step=name)
            subrun = {}
            code = main(['run', 'H10', flag, '--config', str(args.config), '--execute'],
                        _h10_full_off_proof=(completed[0]['evidence'] if completed else None),
                        _subrun_result=subrun)
            if code != 0:
                raise ValueError(f'H10 {name} exited {code}; next stage prohibited')
            verified = _h10_verify_subrun(subrun.get('evidence'), bench.sha256,
                                          source_sha, subgroup, expected)
            completed.append(verified)
            summary.add('full_step_complete', step=name, **verified)
        result, reason = 'PASS', 'H10 OFF, low-current CV/CC and high-current CV3A/CC4A all completed with separate gates and final OFF'
    except BaseException as exc:
        reason = f'STOP after {len(completed)}/3 subruns: {type(exc).__name__}: {exc}'
        result = 'ABORTED' if isinstance(exc, (KeyboardInterrupt, EOFError,
                                                OSError, TimeoutError, InterruptedError)) else 'FAIL'
        print(f'{result}: {reason}; НЕ запускать следующий этап, проверить PSU/KEL OFF вручную.')
        summary.add('full_step_error', error_type=type(exc).__name__, message=str(exc),
                    completed=len(completed))
    summary.add('outcome', subgroup='parallel_full_sequence', outcome=result,
                stop_reason=reason, completed=completed,
                review='separate raw evidence requires manual review; no automatic HW VERIFIED promotion')
    report_path = ROOT / f'docs/hardware_reports/{now}_H10_FULL_qualification.md'
    with report_path.open('x', encoding='utf-8') as stream, contextlib.redirect_stdout(stream):
        report(summary.path)
    print(f'H10 FULL {result}; completed {len(completed)}/3; Evidence: {summary.path}\n'
          f'Summary: {report_path}\n'
          'Каждый этап также имеет собственный протокол. Нового аппаратного прогона без review не выполнять.')
    return 0 if result == 'PASS' else 2


def main(argv=None, *, _h10_full_off_proof=None, _subrun_result=None):
    """Dry run is default; each prepared hardware group has its own operator gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'inventory', 'preflight', 'run', 'report', 'abort'))
    parser.add_argument('test_id', nargs='?', choices=tuple(stages()))
    parser.add_argument('--config', type=Path, default=EXAMPLE,
                        help='single TOML bench configuration (no instrument endpoints as CLI flags)')
    parser.add_argument('--start-channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--load-connected', action='store_true',
                        help='H03 CH1 recovery only: load remains wired, INPUT OFF; verify all PSU outputs OFF first')
    parser.add_argument('--diagnose-status', action='store_true',
                        help='H03 CH3 only: wire trace, delayed and channel-selection status snapshots')
    parser.add_argument('--recall', action='store_true',
                        help='H06 only: separately gated Output Recall mains cycles, all terminals empty')
    parser.add_argument('--powered', action='store_true',
                        help='H09/H10 only: separately gated low-power combined-mode qualification')
    parser.add_argument('--high-current', action='store_true',
                        help='H10 only: separately gated high-current Parallel CV ~3 A and CC 4 A points')
    parser.add_argument('--off-only', action='store_true',
                        help='H10 only: isolated, non-powered Parallel OFF diagnostic (PARTIAL expected); default H10 runs ALL subgroups')
    parser.add_argument('--single-channel-cvcc', action='store_true',
                        help='H03 CH1-only CV/CC; gated live runner or dry-run (full H03 unchanged)')
    parser.add_argument('--aux-revision-diagnostic', action='store_true',
                        help='H01 only: separate OFF-only, selected-channel *IDN? correlation')
    parser.add_argument('--allow-single-cvcc-on', action='store_true',
                        help='H03 CH1 ONLY: additional explicit powered-run opt-in; no effect on full H03')
    parser.add_argument('--allow-o01-on', action='store_true',
                        help='O01 ONLY: explicitly authorize gated CH1 no-load DMM voltage pilot')
    parser.add_argument('--profile-only', action='store_true',
                        help='H02 only: restricted OFF-only Independent profile readback, no panel/debug probes')
    parser.add_argument('--prior-evidence', type=Path, action='append',
                        help='H03 evidence log; repeat once per completed-channel log when resuming')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--execute', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args(argv)
    try:
        bench = load_bench(args.config)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f'invalid bench configuration: {exc}')
    activate_bench(bench)
    args.port = bench.psu.port
    args.meter_host = bench.meter.host
    args.load_host = bench.load.host
    args.load_port = bench.load.network_port
    args.load_transport = bench.load.transport
    args.load_serial_port = bench.load.port
    if args.aux_revision_diagnostic and (args.test_id != 'H01' or
                                          args.start_channel != 1 or args.single_channel_cvcc or
                                          args.allow_single_cvcc_on or args.profile_only or
                                          args.recall or args.powered or args.high_current or
                                          args.diagnose_status or args.load_connected or
                                          args.prior_evidence):
        parser.error('--aux-revision-diagnostic is an exclusive H01 OFF-only variant')
    if args.single_channel_cvcc and (args.test_id != 'H03' or args.start_channel != 1 or
                                     args.profile_only or args.recall or args.powered or
                                     args.high_current or args.diagnose_status or args.load_connected or
                                     args.prior_evidence):
        parser.error('--single-channel-cvcc only supports H03 CH1 with no other stage variants')
    if args.allow_single_cvcc_on and (not args.single_channel_cvcc or
                                      args.command != 'run' or not args.execute):
        parser.error('--allow-single-cvcc-on requires run H03 --single-channel-cvcc --execute')
    if args.allow_o01_on and (args.test_id != 'O01' or args.command != 'run' or not args.execute):
        parser.error('--allow-o01-on requires run O01 --execute')
    if args.test_id == 'O01' and (args.start_channel != 1 or args.recall or args.powered or
                                   args.high_current or args.off_only or args.load_connected or
                                   args.diagnose_status or args.single_channel_cvcc or
                                   args.profile_only or args.prior_evidence):
        parser.error('O01 only supports the bounded CH1 DMM voltage pilot; no variants')
    if args.single_channel_cvcc and bench.h03_single_cvcc is None:
        parser.error('H03 single CV/CC requires [qualification.h03_single_cvcc] in --config TOML')
    if args.profile_only and (args.test_id != 'H02' or args.recall or args.powered or args.high_current):
        parser.error('--profile-only is restricted to H02')
    if args.recall and args.test_id != 'H06':
        parser.error('--recall requires H06')
    if args.powered and args.test_id not in ('H09', 'H10'):
        parser.error('--powered requires H09 or H10')
    if args.high_current and args.test_id != 'H10':
        parser.error('--high-current requires H10')
    if args.high_current and args.powered:
        parser.error('--high-current and --powered are separate H10 runs')
    if args.off_only and (args.test_id != 'H10' or args.powered or args.high_current or
                          args.command not in ('run', 'preflight')):
        parser.error('--off-only is an exclusive H10 run/preflight variant')
    if args.load_connected and (args.test_id != 'H03' or args.start_channel != 1):
        parser.error('--load-connected requires H03 start channel 1')
    if args.diagnose_status and (args.test_id != 'H03' or args.start_channel != 3):
        parser.error('--diagnose-status requires H03 --start-channel 3')
    # The example bench is documentation/test data, not an approved live setup.
    # Interactive hardware runs must explicitly use a real local bench file.
    if args.command == 'run' and args.execute and bench.source == EXAMPLE.resolve():
        print('BLOCKED: для аппаратного запуска нужен --config config/bench.local.toml; '
              'bench.example.toml не является подтверждённой настройкой стенда.')
        return 2
    if args.command == 'inventory':
        if args.test_id is not None:
            parser.error('inventory does not accept a stage ID')
        if args.evidence is not None and not args.execute:
            parser.error('--evidence for inventory requires --execute')
        print(bench_summary(bench))
        if not args.execute:
            print('DRY RUN: no device was contacted. Use inventory --execute to query '
                  'all four instruments without changing their settings.')
            return 0
        from equipment.inventory import run_inventory, print_inventory
        # Use an independent evidence directory for every run. Never overwrite.
        if args.evidence is None:
            import uuid
            path = (ROOT / 'reports/local/inventory' /
                    f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}' /
                    'inventory.jsonl')
        else:
            path = args.evidence
        try:
            evidence = Evidence(path, 'INVENTORY')
        except OSError as exc:
            parser.error(f'cannot create inventory evidence: {exc}')
        evidence.add('bench_configuration', sha256=bench.sha256,
                     devices={name: {'model': getattr(bench, name).model,
                                     'transport': getattr(bench, name).transport}
                              for name in ('psu', 'load', 'meter', 'scope')})
        print('Read-only queries only: no output enable/disable, setpoints, reset, '
              'automatic reconnect or configuration changes.')
        results = run_inventory(bench, evidence)
        print_inventory(results, path)
        return 0 if all(item.status == 'PASS' for item in results) else 2
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
    if (args.test_id == 'H10' and args.command in ('run', 'preflight') and
            not (args.off_only or args.powered or args.high_current)):
        return _h10_full_sequence(args, bench)
    if args.aux_revision_diagnostic:
        from aux_revision_stage import preflight as aux_preflight, run_cli as aux_run
        if args.command in ('preflight', 'run'):
            aux_preflight(bench)
            if args.command == 'run' and args.execute:
                return aux_run(bench)
            return 0
    if args.single_channel_cvcc:
        from single_cvcc_preflight import describe
        from single_cvcc_cli import run_h03_single_cli
        print(describe(bench, execution_supported=True))
        if args.command == 'run' and args.execute:
            if not args.allow_single_cvcc_on:
                print('BLOCKED: explicitly add --allow-single-cvcc-on after review; no ports opened.')
                return 2
            # Distinct function, independent evidence namespace and two gates.
            # Never flow through the original 3-channel H03 dispatcher below.
            return run_h03_single_cli(bench)
        if args.command in ('run', 'preflight'):
            print(connection_card(bench, 'H03', required_devices('H03'),
                                  single_channel_cvcc=True))
            print('DRY RUN: порты не открыты; SCPI не отправлены; H03 full не запускается.')
            return 0
    if args.command == 'preflight' or not args.execute:
        print(connection_card(bench, args.test_id,
                          required_devices(args.test_id, recall=args.recall,
                                           powered=args.powered, high_current=args.high_current),
                          recall=args.recall, powered=args.powered,
                          high_current=args.high_current, profile_only=args.profile_only))
    preflight(args.test_id, args.port, args.load_host, args.load_port,
              args.start_channel, args.load_transport, args.load_serial_port,
              args.diagnose_status, args.load_connected, args.recall, args.meter_host,
              args.powered, args.high_current, args.profile_only, args.off_only)
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
    if args.test_id == 'O01' and not args.allow_o01_on:
        print('BLOCKED: O01 live run requires --allow-o01-on; ports not opened.')
        return 2
    if args.test_id not in ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11', 'H12', 'O01'):
        print('BLOCKED: выполнение этой группы ещё не подготовлено; порт не открыт.')
        return 2
    if args.test_id in ('H09', 'H10') and (args.powered or args.high_current) and args.load_transport != 'serial':
        print(f'BLOCKED: {args.test_id} powered requires an explicitly reviewed KEL103 USB link; '
              'no ports opened.')
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
    if args.test_id in ('H09', 'H10'):
        required = ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07', 'H08')
        if any(not stage_passed(stages()[stage][-1]) for stage in required):
            print(f'BLOCKED: {args.test_id} requires reviewed PASS H01–H08; port not opened.')
            return 2
        if args.test_id == 'H10':
            if not stage_passed(stages()['H09'][-1]):
                print('BLOCKED: H10 requires reviewed PASS H09; port not opened.')
                return 2
            if (args.powered or args.high_current) and _h10_full_off_proof is None and not stages()['H10'][-1].startswith('**PARTIAL (HW OBSERVED'):
                print('BLOCKED: H10 powered requires the reviewed OFF diagnostic; port not opened.')
                return 2
        if args.high_current:
            from h10_powered_stage import (FINAL_TOKEN as final_token,
                                           high_current_approval_token, h10_high_current)
            token = high_current_approval_token(args.meter_host, args.load_serial_port)
            runner = h10_high_current
        elif args.powered:
            if args.test_id == 'H09':
                from h09_powered_stage import (FINAL_TOKEN as final_token,
                                               approval_token, h09_powered)
                token = approval_token(args.meter_host, args.load_serial_port)
                runner = h09_powered
            else:
                from h10_powered_stage import (FINAL_TOKEN as final_token,
                                               approval_token, h10_powered)
                token = approval_token(args.meter_host, args.load_serial_port)
                runner = h10_powered
        else:
            from functools import partial
            from combined_off_stage import tokens, run_combined_off
            token, final_token = tokens(args.test_id)
            runner = partial(run_combined_off, test_id=args.test_id)
    if args.test_id == 'H08':
        required = ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07')
        if any(not stage_passed(stages()[stage][-1]) for stage in required):
            print('BLOCKED: H08 requires reviewed PASS H01–H07 including H04P; ports not opened.')
            return 2
        from h08_stage import approval_token, FINAL_TOKEN as final_token, h08
        token, runner = approval_token(args.meter_host), h08
    if args.test_id == 'H02':
        from functools import partial
        from profile_stage import (TOKEN as h02_token, PROFILE_ONLY_TOKEN,
                                   FINAL_TOKEN as final_token, h02)
        token = PROFILE_ONLY_TOKEN if args.profile_only else h02_token
        runner = partial(h02, profile_only=args.profile_only)
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
        from h05_stage import approval_token, h05
        token = approval_token()
        runner = h05
        final_token = 'CONFIRM H05 CH1=OFF CH2=OFF CH3=OFF LOAD=OFF'
        if not stage_passed(stages()['H04P'][-1]):
            print('BLOCKED: требуется рассмотренный PASS H04P; порты не открыты.')
            return 2
        if args.load_transport != 'serial':
            print('BLOCKED: H05 requires a configured KEL103 serial connection.')
            return 2
    if args.test_id == 'H06':
        from h06_stage import FINAL_TOKEN as final_token, approval_token, h06
        if args.recall:
            from h06_recall import TOKEN as token, recall as runner
        else:
            token, runner = approval_token(args.load_serial_port), h06
        if not args.recall and (args.load_transport != 'serial'):
            print('BLOCKED: H06 requires a configured KEL103 serial connection.')
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
    if args.test_id == 'H11':
        from h11_stage import FINAL_TOKEN as final_token, approval_token, h11
        if args.load_transport != 'serial':
            print('BLOCKED: H11 requires the configured KEL103 USB connection; ports not opened.')
            return 2
        required = ('H01', 'H02', 'H03', 'H04', 'H04P', 'H05', 'H06', 'H07', 'H08', 'H09')
        if any(not stage_passed(stages()[stage][-1]) for stage in required):
            print('BLOCKED: H11 requires reviewed PASS H01–H09; ports not opened.')
            return 2
        if not stages()['H10'][-1].startswith('**PARTIAL (HW OBSERVED'):
            print('BLOCKED: H11 requires reviewed H10 result; ports not opened.')
            return 2
        token, runner = approval_token(args.load_serial_port), h11
    if args.test_id == 'H12':
        from h12_stage import TOKEN as token, FINAL_TOKEN as final_token, h12
        if not stage_passed(stages()['H11'][-1]):
            print('BLOCKED: H12 requires reviewed PASS H11; port not opened.')
            return 2
        runner = h12
    if args.test_id == 'O01':
        from o01_stage import FINAL_TOKEN as final_token, approval_token, o01
        if any(not stage_passed(stages()[stage][-1]) for stage in ('H08', 'H11', 'H12')):
            print('BLOCKED: O01 requires reviewed PASS H08, H11, H12; ports not opened.')
            return 2
        token, runner = approval_token(args.meter_host), o01
    # One bench gate for ALL stages. Never ask the operator to retype a
    # port, serial speed or VID:PID: the validated TOML is authoritative.
    devices = required_devices(args.test_id, recall=args.recall,
                               powered=args.powered, high_current=args.high_current)
    print(connection_card(bench, args.test_id, devices, recall=args.recall,
                          powered=args.powered, high_current=args.high_current,
                          profile_only=args.profile_only))
    if not sys.stdin.isatty():
        print('BLOCKED: требуется интерактивный терминал; пакетный ввод запрещён.')
        return 2
    bench_token = connection_token(bench, args.test_id, recall=args.recall,
                                   powered=args.powered, high_current=args.high_current,
                                   profile_only=args.profile_only)
    try:
        # USB discovery is read-only; network identity is verified on opening
        # through existing equipment adapters, never assumed from the prompt.
        try:
            for name in devices:
                instrument = getattr(bench, name)
                if instrument.transport == 'serial':
                    verify_serial_adapter(instrument)
                    print(f'USB {name}: {instrument.port} VID:PID match PASS')
        except (ImportError, OSError, ValueError, TypeError) as exc:
            print(f'BLOCKED: {exc}; соединения не открывались.')
            return 2
        if input(f'Подтвердите подключения согласно конфигурации:\n{bench_token}\n> ').strip() != bench_token:
            print('BLOCKED: соединения не подтверждены; порты не открывались.')
            return 2
        while input(f'Подтверждение схемы и условий этапа:\n{token}\n> ').strip() != token:
            print('Подтверждение не распознано; ожидаю повторный ввод. Для остановки — Ctrl-C.')
    except (EOFError, KeyboardInterrupt):
        print('ABORTED до открытия соединений.')
        return 2
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
    evidence = Evidence(ROOT / f'reports/hardware/{now}_{args.test_id}/events.jsonl', args.test_id)
    evidence.add('metadata', **software_metadata(), port=args.port, baud=bench.psu.baudrate, framing='8N1',
                 adapter=f'{bench.psu.usb_vid}:{bench.psu.usb_pid} (OS verified)',
                 topology=('external Parallel: CH1+↔CH2+ and CH1−↔CH2−; KEL103 and DMM7510 across pair; CH3 open'
                           if args.test_id == 'H10' and (args.powered or args.high_current) else
                           'external Series: jumper CH1− to CH2+; aggregate load and DMM across CH1+/CH2−; CH3 open'
                           if args.test_id == 'H09' and args.powered else
                           'tracking CH1/CH2; DMM on one channel at a time; no jumpers; CH3 disconnected'
                           if args.test_id == 'H08' else
                           'independent; CH1/CH2/CH3 negative terminals tied to scope common'
                           if args.test_id == 'H05' else 'independent; no jumpers; no DUT'),
                 subgroup=('independent_profile_only' if args.profile_only else
                           'parallel_high_current_cv_cc' if args.high_current else
                           ('series_powered_cv_cc' if args.test_id == 'H09' else
                            'parallel_powered_cv_cc') if args.powered else
                           'series_off_diagnostic' if args.test_id == 'H09' else
                           'parallel_off_diagnostic' if args.test_id == 'H10' else
                           'output_recall' if args.recall else None),
                 load=(f'KORAD KEL103; USB serial {args.load_serial_port}; {bench.load.baudrate} 8N1; CR 1.00/0.50 Ω; '
                       'input OFF between the two high-current points'
                       if args.test_id == 'H10' and args.high_current else
                       f'KORAD KEL103; USB serial {args.load_serial_port}; {bench.load.baudrate} 8N1; CR 40/12/2000/1600 Ω; '
                       'input OFF between points'
                       if args.test_id == 'H09' and args.powered else
                       f'KORAD KEL103; USB serial {args.load_serial_port}; {bench.load.baudrate} 8N1; CR 30/5 Ω; '
                       'input OFF between points'
                       if args.test_id == 'H10' and args.powered else
                       'disconnected; KEL103 INPUT OFF; scope disconnected; no jumpers'
                       if args.recall else
                       f'KORAD KEL103; UDP port {args.load_port}; input OFF'
                       if args.test_id in ('H03', 'H04P') and args.load_transport == 'udp' else
                       f'KORAD KEL103; USB serial {args.load_serial_port}; '
                       '8N1 (скорость из bench config); USB link previously HW OBSERVED on H03; input OFF'
                       if args.test_id in ('H03', 'H04P', 'H05', 'H06', 'H07')
                       else 'disconnected; input OFF'),
                 proposed_caps=('H09 powered LOW: Series total soft cap 4.00 V, current cap 0.300 A; '
                                'CV 2.00 V/0.100 A CR40; CC 4.00 V/0.250 A CR12; '
                                'HIGH: total soft cap 40.00 V, current cap 0.025 A; '
                                'CV 40.00 V/0.025 A CR2000; CC 40.00 V/0.024 A CR1600; '
                                'CH3=0 V/open; load <=1 W; software-only combined voltage cap'
                                if args.test_id == 'H09' and args.powered else
                                'H10 high-current: Parallel soft caps 4.00 V / 4.500 A aggregate; '
                                'CV 3.00 V / 4.000 A CR1.00 (~3 A) then CC 3.00 V / 4.000 A CR0.50; CH3=0 V/open; <=10 W each'
                                if args.test_id == 'H10' and args.high_current else
                                'H10 powered: Parallel soft caps 4.00 V / 0.500 A aggregate; '
                                'CV 3.00 V / 0.100 A CR30; CC 3.00 V / 0.300 A CR5; CH3=0 V/open; <=1 W'
                                if args.test_id == 'H10' and args.powered else
                                f'{args.test_id} OFF only: V-set 1/1.5/2 V, I-set 0.100/0.200/0.300 A; '
                                'V-cap 2/3/4 V, software I-cap 0.300/0.400/0.500 A; ON forbidden'
                                if args.test_id in ('H09', 'H10') else
                                'H08 V-cap 2/3/4 V, I-cap 0.300/0.400/0.500 A; '
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
                                'one CH at a time; low-power CV/CC; KEL103 CR 5/11/22/47/56 Ω'
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
                                'H12 OFF only: V-set 1/1.5/2 V, I-set 0.100/0.200/0.300 A; '
                                'V-cap 2/3/4 V; software I-cap 0.300/0.400/0.500 A'
                                if args.test_id == 'H12' else
                                'O01 independent CH1 only, no load: 0.50/1.00/1.50 V, I-set 0.040 A; '
                                'DMM7510 FRONT DCV10 NPLC1; CH2/CH3 OFF; caps 2/3/4 V; '
                                'max lag 2 s/window 5 s/PSU–DMM delta 30 mV'
                                if args.test_id == 'O01' else
                                'each CH: V-cap 4.00 V, software I-cap 0.500 A; initial 1.00 V / 0.100 A'),
                 independent_meter=({'model': 'DMM7510', 'host': args.meter_host, 'port': bench.meter.network_port,
                                     'function': ('DCV 100 V, NPLC 1, front terminals'
                                                  if args.test_id == 'H09' and args.powered
                                                  else 'DCV 10 V, NPLC 1, front terminals')}
                                    if args.test_id in ('H08', 'O01') or (args.test_id in ('H09', 'H10') and (args.powered or args.high_current))
                                    else None))
    evidence.add('bench_configuration', sha256=bench.sha256, source=bench.source.name,
                 devices={name: {'model': getattr(bench, name).model,
                                 'transport': getattr(bench, name).transport}
                          for name in ('psu', 'load', 'meter', 'scope')})
    evidence.add('bench_connection_confirmation', token=bench_token, sha256=bench.sha256,
                 devices=list(devices), serial_os_verified=[name for name in devices
                     if getattr(bench, name).transport == 'serial'])
    evidence.add('approval', token=token, variant='profile_only' if args.profile_only else 'full')
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
        if args.test_id in ('H08', 'O01') or (args.test_id in ('H09', 'H10') and (args.powered or args.high_current)):
            meter = open_meter(args.meter_host, evidence)
        if (args.test_id in ('H03', 'H04P', 'H05', 'H06', 'H07', 'H11') or
                (args.test_id in ('H09', 'H10') and (args.powered or args.high_current))) and not args.recall:
            load = open_load(bench, evidence)
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
        verify_serial_adapter(bench.psu)
        return open_transport(args.port)

    outcome = (runner(transport, evidence, meter) if args.test_id in ('H08', 'O01') else
               runner(transport, evidence, load, meter) if args.test_id in ('H09', 'H10') and (args.powered or args.high_current) else
               runner(transport, evidence) if args.test_id in ('H09', 'H10') else
               runner(transport, evidence, reopen_recall_transport) if args.recall else
               runner(transport, evidence, load, args.start_channel,
                      diagnose_status=args.diagnose_status,
                      load_connected=args.load_connected)
               if args.test_id == 'H03' else
               runner(transport, evidence, load) if args.test_id in ('H04P', 'H05', 'H06', 'H07', 'H11') else
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
    report_stage = f'{args.test_id}_PROFILE_ONLY' if args.profile_only else args.test_id
    report_path = ROOT / f'docs/hardware_reports/{now}_{report_stage}_qualification.md'
    with report_path.open('x', encoding='utf-8') as stream, contextlib.redirect_stdout(stream):
        report(evidence.path)
    if _subrun_result is not None:
        _subrun_result.update(outcome=outcome, physical_off_confirmed=physical_off,
                              evidence=evidence.path, report=report_path)
    if args.test_id == 'H10' and args.off_only and outcome == 'PARTIAL':
        print('NOTE: --off-only H10 is an isolated OFF diagnostic; PARTIAL is expected. For complete H10 omit all subgroup flags.')
    print(f'{outcome}; Evidence: {evidence.path}\n'
          f'Протокол для review: {report_path}\n'
          'STOP: перед следующей группой требуется разбор отчёта и отдельное разрешение.\n'
          f'Предлагаемое сообщение коммита после review: test(hw): record {args.test_id} qualification')
    # An isolated, deliberately partial OFF diagnostic is a *completed* command,
    # not a complete H10 qualification. Its evidence still says PARTIAL.
    return 0 if (outcome == 'PASS' or (args.test_id == 'H10' and args.off_only and
                                       outcome == 'PARTIAL' and physical_off)) else 2
