# R4.1 — SCPI coverage inventory (baseline, no hardware I/O)

**Статус:** R4.1 начат, таблица первичной инвентаризации, **не** заявление о полном покрытии/аппаратной проверке.

**Источник:** Keithley Instruments, *Model 2231A-30-3 Triple-channel DC Power Supply Reference Manual*,
публикация 077100401/September 2014, **§5, стр. 5-2–5-28**, по подразделам из официального оглавления.
[Официальный PDF (Tektronix)](https://download.tek.com/manual/077100401_Referencemanual.pdf) ·
[Страница производителя](https://www.tek.com/en/tektronix-and-keithley-dc-power-supplies/keithley-2220-2230-2231-series-manual-0).

Метод: строка описывает **семейство** команды документации, а не каждую форму,
алиас, query/write или аргумент. Для `W` проверен буквальный маршрут в
`src/keithley2231a/device.py` (включая вызываемые им методы), но статус **не
подтверждает** поддержку всех синтаксических вариантов руководства. Неоднозначные
варианты будут уточнены в R4.1; отсутствующее в прямом on-wire маршруте нельзя
назвать покрытым только по существованию generic debug API.

Классы: `W` — обнаружен прямой рабочий on-wire маршрут драйвера; `U` — маршрут
не обнаружен, требуется оценка необходимости; `R` — отдельная оценка риска до
реализации/испытания; `X` — сервисная/калибровочная команда, **исключена из
публичного API** (учтена без посылки). `W` ≠ `HW VERIFIED`. For a paired write/query family, `W` can mean **query-only**: the table must explicitly mark the write leg unsupported; never infer permission to write from `W`. Входящие в общий
`_write/_query` диагностические команды не означают автоматическое разрешение
выходов.

## Первичная матрица (семейства справочника)

| Раздел §5 | Команда / семейство | Класс | Привязка / решение |
| --- | --- | :---: | --- |
| Common 5-2…5-6 | `*CLS` | **U** | Очистка статуса; опасность потери диагностик |
| Common 5-2…5-6 | `*ESE / *ESE?` | **W** | R4.2: `status_enable_mask("standard_event")` reads `*ESE?` only; writing `*ESE` stays unsupported |
| Common 5-2…5-6 | `*ESR?` | **W** | R4.2-B: `consume_status_event("standard_event", acknowledge_clear=True)`; destructive read, OFF-only, SIM TESTED |
| Common 5-2…5-6 | `*IDN?` | **W** | `Keithley2231A.identity()` |
| Common 5-2…5-6 | `*OPC / *OPC?` | **U** | Completion/query |
| Common 5-2…5-6 | `*RST` | **R** | Сброс конфигурации может изменить условия безопасности |
| Common 5-2…5-6 | `*SRE / *SRE?` | **W** | R4.2: `status_enable_mask("service_request")` reads `*SRE?` only; writing `*SRE` stays unsupported |
| Common 5-2…5-6 | `*STB?` | **W** | `status_snapshot()` |
| Common 5-2…5-6 | `*TRG` | **R** | Триггер может менять выходные уставки |
| Common 5-2…5-6 | `*SAV` | **R** | Запись внутренней памяти |
| Common 5-2…5-6 | `*RCL` | **R** | Восстановление внутренних уставок |
| Common 5-2…5-6 | `*TST?` | **R** | Самотестирование; непроверенные побочные эффекты |
| Common 5-2…5-6 | `*WAI` | **U** | Синхронизация очереди |
| Common 5-2…5-6 | `*PSC / *PSC?` | **R** | Настройка power-on status |
| APPLy 5-6…5-7 | `[SOUR:]APPLy / ?` | **R** | Многоканальная настройка с побочными эффектами |
| APPLy 5-6…5-7 | `APPLy:VOLTage / ?` | **R** | Массовое изменение уставок |
| APPLy 5-6…5-7 | `APPLy:CURRent / ?` | **R** | Массовое изменение ограничений тока |
| CALibration 5-7…5-9 | `CAL:SECure[:STATe]` | **X** | Сервисная/калибровочная операция |
| CALibration 5-7…5-9 | `CAL:VOLT:LEVel` | **X** | Калибровочные точки |
| CALibration 5-7…5-9 | `CAL:VOLT[:DATA]` | **X** | Запись калибровки |
| CALibration 5-7…5-9 | `CAL:CURR:LEVel` | **X** | Калибровочные точки |
| CALibration 5-7…5-9 | `CAL:CURR[:DATA]` | **X** | Запись калибровки |
| CALibration 5-7…5-9 | `CAL:SAVe` | **X** | Постоянное сохранение калибровки |
| CALibration 5-7…5-9 | `CAL:INITial` | **X** | Инициализация калибровки |
| CALibration 5-7…5-9 | `CAL:STRing` | **X** | Калибровочные данные |
| DISPlay 5-9…5-10 | `DISP[:WIND][:STATe] / ?` | **U** | Управление экраном |
| DISPlay 5-9…5-10 | `DISP[:WIND]:TEXT[:DATA]` | **U** | Пользовательский текст |
| DISPlay 5-9…5-10 | `DISP[:WIND]:TEXT:CLEar` | **U** | Очистка текста |
| INSTrument 5-10…5-12 | `INST[:SELect] / ?` | **U** | Выбор по имени; core применяет NSEL |
| INSTrument 5-10…5-12 | `INST:NSELect / ?` | **W** | Селектор канала + проверка |
| INSTrument 5-10…5-12 | `INST:COMBine:SERies` | **U** | Отдельная альтернатива OUTP:SER |
| INSTrument 5-10…5-12 | `INST:COMBine:PARAllel` | **U** | Отдельная альтернатива OUTP:PARALLEL |
| INSTrument 5-10…5-12 | `INST:COMBine:TRACk` | **U** | Отдельная альтернатива OUTP:TRAC |
| INSTrument 5-10…5-12 | `INST:COMBine:OFF` | **U** | Проверить топологические эффекты |
| INSTrument 5-10…5-12 | `INST:COUPle[:TRIGger] / ?` | **R** | Связь триггеров/каналов |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:VOLT[:DC]?` | **W** | `read_channel()` вызывает `MEAS:VOLT?` |
| MEASure 5-12…5-13 | `FETCh[:VOLT][:DC]?` | **U** | Отдельное чтение сохранённого результата |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:CURR[:DC]?` | **W** | `read_channel()` вызывает `MEAS:CURR?` |
| MEASure 5-12…5-13 | `FETCh:CURR[:DC]?` | **U** | Нет прямого вызова core |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:POW[:DC]?` | **U** | Core вычисляет V×I, нет on-wire MEAS:POW |
| MEASure 5-12…5-13 | `FETCh:POW[:DC]?` | **U** | Нет прямого вызова core |
| OUTPut 5-14…5-16 | `OUTP[:STATe][:ALL] / ?` | **W** | `OUTP 0`, guarded global output, readback |
| OUTPut 5-14…5-16 | `CHAN:OUTP[:STATe] / ?` | **W** | Один канал; при ON нужны профиль и gate |
| OUTPut 5-14…5-16 | `OUTP:ENABle / ?` | **U** | EnableChannels semantics ещё не сопоставлены |
| OUTPut 5-14…5-16 | `OUTP:TIMer[:STATe] / ?` | **W** | Проверка таймера |
| OUTPut 5-14…5-16 | `OUTP:TIMer:DELay / ?` | **W** | Установка задержки + readback |
| OUTPut 5-14…5-16 | `OUTP:TRACk[:STATe] / ?` | **W** | Tracking, профиль |
| OUTPut 5-14…5-16 | `OUTP:SERies[:STATe] / ?` | **W** | Series, физические перемычки отдельно |
| OUTPut 5-14…5-16 | `OUTP:PARallel[:STATe] / ?` | **W** | Parallel, физические перемычки отдельно |
| SYSTem 5-17…5-18 | `SYST:ERRor?` | **W** | Очередь ошибок / статус после операций |
| SYSTem 5-17…5-18 | `SYST:VERSion?` | **W** | `status_snapshot()` |
| SYSTem 5-17…5-18 | `SYST:REMote` | **W** | Remote |
| SYSTem 5-17…5-18 | `SYST:LOCal` | **W** | Local при закрытии |
| SYSTem 5-17…5-18 | `SYST:RWLock` | **W** | Блокировка панели, не safety interlock |
| SYSTem 5-17…5-18 | `SYST:MODUle?` | **W** | R4.2: `module_info()` via `SYST:MODU?`; module text ≠ identity/serial check; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| SYSTem 5-17…5-18 | `SYST:BEEPer` | **W** | `beep()` |
| STATus 5-19…5-23 | `STAT:QUES[:EVENt]?` | **W** | R4.2-B: `consume_status_event("questionable", acknowledge_clear=True)`; read-to-clear, OFF-only, SIM TESTED |
| STATus 5-19…5-23 | `STAT:QUES:ENABle / ?` | **W** | R4.2: `status_enable_mask("questionable")` queries `...:ENAB?` only; writes unsupported |
| STATus 5-19…5-23 | `STAT:QUES:CONDition?` | **W** | `status_snapshot()` |
| STATus 5-19…5-23 | `STAT:PRESet` | **U** | Сброс status config |
| STATus 5-19…5-23 | `STAT:QUES:INST[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:QUES:INST:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("questionable")`: read-only; writes unsupported; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("questionable", channel=1..3)`; queries only; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>:COND?` | **W** | `read_channel()` mode decoding |
| STATus 5-19…5-23 | `STAT:OPER[:EVENt]?` | **W** | R4.2-B: `consume_status_event("operation", acknowledge_clear=True)`; potentially destructive event read, OFF-only, SIM TESTED |
| STATus 5-19…5-23 | `STAT:OPER:COND?` | **W** | `status_snapshot()` |
| STATus 5-19…5-23 | `STAT:OPER:ENABle / ?` | **W** | R4.2: `status_enable_mask("operation")` queries `...:ENAB?` only; writes unsupported |
| STATus 5-19…5-23 | `STAT:OPER:INST[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:OPER:INST:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("operation")`: read-only; writes unsupported; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:OPER:INST:ISUM<x>[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:OPER:INST:ISUM<x>:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("operation", channel=1..3)`; queries only; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:OPER:INST:ISUM<x>:COND?` | **W** | `read_channel()` mode decoding; CH3 caveat |
| SOURce 5-24…5-28 | `[SOUR:]CHAN:OUTP / ?` | **W** | Эквивалент `CHAN:OUTP` |
| SOURce 5-24…5-28 | `[SOUR:]CURRent[:LEV] / ?` | **W** | Уставка и readback через `set_channel()` |
| SOURce 5-24…5-28 | `CURRent:UP` | **U** | Инкремент уставки |
| SOURce 5-24…5-28 | `CURRent:DOWN` | **U** | Декремент уставки |
| SOURce 5-24…5-28 | `CURRent:STEP / ?` | **R** | Состояние шагов требует охранных границ |
| SOURce 5-24…5-28 | `CURRent:TRIGgered / ?` | **R** | Триггер может менять ограничение тока |
| SOURce 5-24…5-28 | `[SOUR:]VOLTage[:LEV] / ?` | **W** | Уставка и readback через `set_channel()` |
| SOURce 5-24…5-28 | `VOLTage:UP` | **U** | Инкремент уставки |
| SOURce 5-24…5-28 | `VOLTage:DOWN` | **U** | Декремент уставки |
| SOURce 5-24…5-28 | `VOLTage:STEP / ?` | **R** | Состояние шагов требует охранных границ |
| SOURce 5-24…5-28 | `VOLTage:TRIGgered / ?` | **R** | Триггер меняет уставку |
| SOURce 5-24…5-28 | `VOLTage:LIMit / ?` | **W** | Профиль ограничений + readback |
| SOURce 5-24…5-28 | `VOLTage:LIMit:STATe / ?` | **W** | Защита профиля + readback |

## Первые выводы R4.1

- Основные безопасно ограничиваемые группы уже реализованы: идентификация,
  выбор канала, уставки V/I, лимиты V, считывание V/I, outputs, timers,
  топологии и часть status; физические эффекты подтверждены **только** там,
  где имеются конкретные протоколы H01–H12/O01.
- Приоритет **первого технического инкремента R4.2**: отдельно изучить
  read-only `SYST:MODUle?`, статусные события (`*ESR?`, `STAT:*:EVENt?`) и
  требуемые для их корректной работы маски. Event-регистры **read-to-clear**;
  не вызывать их неявно, чтобы не уничтожить чужую диагностику.
- `*RST`, `*RCL`, `APPLy`, `*TRG`, `VOLT/CURR:TRIGgered`, combined mode
  и изменение состояния output требуют явного API-контракта и отдельных gates.
- CALibration/service — **X**, без debug-bypass и аппаратных проб.
- Таблица требует следующего шага: проверить синтаксис/побочные эффекты
  каждой формы на конкретной странице §5, сопоставить с тестом и
  ограничением firmware 1.04-1.04/1.04-0.01. До этого `U` — не баг.
- Два PSU, MCU bridge, PyVISA, общий bench coordinator и O02-B/C **не
  участвуют** в R4.

## Границы и источники кода

- `src/keithley2231a/device.py`: реальные `_write/_query`, `set_channel`,
  `read_channel`, `timer`, `apply_profile`, `status_snapshot`, `beep`.
- `src/keithley2231a/transport.py`: передача строк и fail-closed link.
- `tests/offline/test_scpi_sequence_audit.py` + `tests/test_driver.py`: симуляция,
  не дополнительная аппаратная функциональность.
- `docs/VERIFICATION.md` и аппаратная матрица: статусы реально проведённых
  квалификаций; не переносить `PASS` на непротестированные команды.

## R4.2-A — недеструктивное чтение (SIM TESTED, HW REVIEWED PASS: READ_ONLY 2026-10-29)

- `Keithley2231A.module_info()` посылает **только `SYST:MODU?`**, возвращая
  непустой текст (пример справочника: `2231,1.04`). Содержимое не используется
  для проверки модели/серийного номера или нормализации Aux revision.
- `Keithley2231A.status_enable_mask(name)` читает одну из четырёх масок
  `*ESE?`, `*SRE?`, `STAT:QUES:ENAB?`, `STAT:OPER:ENAB?`; результат `0..255`.
  Неизвестные имена отклоняются до SCPI. Методы могут сначала вызвать
  `*IDN?` и `SYST:REM` при инициализации сессии; выходы не включаются.
- **Никаких** `*ESR?`, `STAT:*:EVEN?` (читают и сбрасывают latch), `*CLS`,
  `STAT:PRES`, настройки enable-масок, `*RST` или изменения уставок.
- Новые методы не читают очередь ошибок неявно, не выполняют повторов,
  наследуют fail-closed поведение драйвера при некорректном ответе/потере I/O.
- В ограниченном OFF-only аппаратном прогоне 2026-10-29 подтверждены реальные
  ответы и точные маршруты чтения; это не проверка изменения масок и событий.

Официальный источник: 077100401 §5, страницы 5-2–5-4, 5-18–5-22.

## R4.2-B — explicit event consumption (SIM TESTED, HW NOT_RUN)

- `consume_status_event(register, *, acknowledge_clear=True)` supports **only**
  `standard_event` → `*ESR?`, `questionable` → `STAT:QUES:EVEN?`, and
  `operation` → `STAT:OPER:EVEN?`. These are **not** nondestructive status
  snapshots: a query may consume/clear latched events, and even an invalid or
  lost reply does not allow the application to reconstruct those events.
- Requires a fresh, unarmed, profile-free session and checks **all three**
  output states through the shared guarded driver immediately before issuing
  a single event query. If any ON/UNKNOWN, **no event query**. An output ON
  condition is a refusal, not automatic OFF. Every call requires the exact
  `acknowledge_clear=True` keyword; no scheduled/background polling.
- May enter Remote and temporarily select each channel to check OFF, restoring
  the original selection when communication is valid. The OFF test is sequential:
  it cannot ensure an external controller does not subsequently change outputs.
  It is a software restriction, **not a physical interlock**.
- The normal `status()` and `status_enable_mask()` never call these queries.
  No `*CLS`, `STAT:PRES`, configuration writes, scope/bench orchestration, or
  retries are added. Additional per-channel and nested events remain **U**.
- On invalid 8-bit reply / transport failure, the session latches UNKNOWN
  without retrying. On a normally parsed event value, no implicit error poll
  or second event query consumes other evidence.
- Vendor reference: 077100401, §5, pages 5-3, 5-19, 5-21–5-22.
  `*ESR?` and `STAT:QUES[:EVENt]?` have documented clear-on-query behavior;
  all event queries are treated as potentially destructive pending firmware-
  specific validation. **No HW test authorized by this patch.**

## R4.2-C — nested status enable masks (SIM TESTED, HW REVIEWED PASS: READ_ONLY 2026-10-29)

- `status_instrument_enable_mask(register, *, channel=None)` reads *only* the
  `STAT:QUES:INST:ENAB?` / `STAT:OPER:INST:ENAB?` enable masks. With
  `channel=1`, `2` or `3`, it instead reads the corresponding
  `...:INST:ISUM<n>:ENAB?` mask. All documented replies are 8-bit decimal
  unsigned integers (`0..255`). The supplied channel is **embedded** in the
  command only after strict validation; `INST:NSEL` is not sent and the
  device's selected output channel is not altered.
- Only the two exact group names and three real integer channel indices are
  accepted; `True`, strings, floats and command injection are rejected before
  any I/O. Method never reads `:EVEN?`/`*ESR?`, writes enable masks, calls
  `*CLS`/`STAT:PRES`, or toggles outputs; it is valid while profile/outputs
  are on because it is observational.
- On malformed/lost response the existing dispatcher latches the session
  `UNKNOWN`, sends no retry, and subsequent calls are refused. A synthetic
  transport verifies exact SCPI strings; аппаратная READ_ONLY-проверка
  2026-10-29 подтверждена для нулевых масок и текущего устройства.
- Vendor reference: 077100401 §5, printed pp. 5-20, 5-21, 5-23. Two
  corresponding *event* families remain unimplemented (`U`) because reads
  may be destructive. Neither the older status snapshot nor explicit event
  consumption semantics change in this increment.

## R4.2 hardware gate (R3.5.38)

**Read-only A/C HW REVIEWED PASS** (2026-10-29, exact tested firmware/scope).
All 12 enable-mask queries and `SYST:MODU?` were observed on one real PSU;
zero values are valid readbacks, **not** interrupt-behavior validation.
Evidence review: [`hardware_reports/R4_2_READ_ONLY_review_2026-10-29.md`](hardware_reports/R4_2_READ_ONLY_review_2026-10-29.md).
**Event B HW NOT_RUN.** Hardware qualification protocol for A/C masks and B destructive events is
[`R4_2_HARDWARE.ru.md`](R4_2_HARDWARE.ru.md). It consists of two manually
gated, separate real-PSU runs. Step A must be reviewed before step B. No
programmatic PASS or simulation claim upgrades SCPI families to HW VERIFIED.
