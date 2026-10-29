# R3.5.31 / O01 — ограниченный аппаратный пилот

**Статус SIM TESTED / HW NOT_RUN до фактического прогона. R3.5.31 разрешает
отдельно подтверждённый аппаратный запуск только с `--execute --allow-o01-on`.**
Это ограниченный аппаратный сценарий, а не массовая автоматизация или аппаратная синхронизация.

## Подключения и ограничения

- Keithley 2231A-30-3 (USB TTL), DMM7510 (LAN TCP SCPI, FRONT DCV 10 V,
  NPLC=1, autozero ON). Только приборы из `bench.local.toml`; никаких fallback.
- На входе независимая топология, CH1/CH2/CH3 OFF, таймеры OFF,
  Output Recall OFF, доступен физический сетевой выключатель.
  KEL103 физически отсоединена и INPUT OFF; осциллограф, DUT,
  перемычки и межканальная общая шина отключены.
- **До конфигурации DMM и его `*RST` щупы не подключены.** В отдельном
  CONNECT gate при всех выходах OFF: DMM FRONT INPUT HI → CH1+,
  FRONT INPUT LO → CH1−. Нельзя использовать current, SENSE разъёмы.
- Подготовка профиля: CH1 0.50 V / 0.040 A (max 2.00 V / 0.100 A);
  CH2 1.50 V / 0.200 A (max 3.00 V / 0.200 A);
  CH3 2.00 V / 0.300 A (max 4.00 V / 0.300 A). Только CH1 получает ON.
- Проверка DMM при OFF: |U| ≤0.1 V. Затем три последовательные точки
  CH1: 0.50/1.00/1.50 V, пауза 0.5 s, ограничение тока 0.040 A,
  требуемый CV, measured I≤0.030 A и P≤0.050 W.
- Host-clock timestamp (после Keithley, перед и после DMM): start lag≤2 s,
  overall window≤5 s. Разность PSU–DMM ≤0.030 V; строгий контроль CSV.
  **Не аппаратная синхронизация**: задержки АЦП приборов не измерены.
- На ошибку DMM / несогласование / сбой журнала: `voltage_sweep()` вызывает
  emergency OFF в finally, затем StageAdapter проверяет финальный OFF без
  повторного обращения к неисправной связи. DMM — пассивный вольтметр.
- После подтверждённого OFF оператор отключает оба провода DMM при
  выключенном источнике; отдельный финальный токен для физического OFF.

## Offline и аппаратные команды

```bash
python3 tools/hw_qualify.py preflight O01 --config config/bench.local.toml
python3 tools/hw_qualify.py run O01 --config config/bench.local.toml --dry-run
PYTHONPATH=src:tests:tests/hardware python3 -m unittest discover -s tests -v
```

Для аппаратного испытания после проверки физической схемы и доступности
сетевого выключателя:

```bash
python3 tools/hw_qualify.py run O01 --config config/bench.local.toml --execute --allow-o01-on
```

Без `--allow-o01-on` команда `--execute` возвращает BLOCKED до USB discovery.
Сохрани `reports/hardware/<UTC>_O01/events.jsonl`, `o01_sweep.csv`,
консольный журнал и итоговый Markdown. Любое `FAIL`/`ABORTED`/`PARTIAL`
требует разбора и физической проверки OFF, без автоматического повтора.
Даже `PASS` остаётся **HW OBSERVED / REVIEW REQUIRED** до независимого аудита.
