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
обычного API**. Некоторые отдельно разрешённые некалибровочные сервисные
операции проходят через основной драйвер при `service_mode=True` и
явном разрешении в сервисном профиле; калибровочные записи остаются
заблокированными для реального UART (см. `R4_3_SERVICE_MODE.ru.md`). `W` ≠ `HW VERIFIED`. For a paired write/query family, `W` can mean **query-only**: the table must explicitly mark the write leg unsupported; never infer permission to write from `W`. Входящие в общий
`_write/_query` диагностические команды не означают автоматическое разрешение
выходов.

## Первичная матрица (семейства справочника)

| Раздел §5 | Команда / семейство | Класс | Привязка / решение |
| --- | --- | :---: | --- |
| Common 5-2…5-6 | `*CLS` | **R** | Очистка диагностик; только явно авторизованный service_mode, не обычный API |
| Common 5-2…5-6 | `*ESE / *ESE?` | **W** | R4.2: `status_enable_mask("standard_event")` reads `*ESE?` only; writing `*ESE` stays unsupported |
| Common 5-2…5-6 | `*ESR?` | **W** | R4.2-B: `consume_status_event("standard_event", acknowledge_clear=True)`; destructive read, OFF-only, HW REVIEWED PASS EVENT 2026-10-29 (one-shot; clear effect not independently measured) |
| Common 5-2…5-6 | `*IDN?` | **W** | `Keithley2231A.identity()` |
| Common 5-2…5-6 | `*OPC / *OPC?` | **W** | R3.5.43 `operation_complete()` sends only `*OPC?` with idle/profile-free guard, strict `1`, **HW REVIEWED PASS** (one idle OFF-only `1` in 0.207 s, 2026-10-29); `*OPC` write unsupported; no guaranteed latency or event-latch preservation |
| Common 5-2…5-6 | `*RST` | **R** | Сброс конфигурации может изменить условия безопасности |
| Common 5-2…5-6 | `*SRE / *SRE?` | **W** | R4.2: `status_enable_mask("service_request")` reads `*SRE?` only; writing `*SRE` stays unsupported |
| Common 5-2…5-6 | `*STB?` | **W** | `status_snapshot()` |
| Common 5-2…5-6 | `*TRG` | **R** | Триггер может менять выходные уставки |
| Common 5-2…5-6 | `*SAV` | **R** | Запись внутренней памяти |
| Common 5-2…5-6 | `*RCL` | **R** | Восстановление внутренних уставок |
| Common 5-2…5-6 | `*TST?` | **R** | Самотестирование; непроверенные побочные эффекты |
| Common 5-2…5-6 | `*WAI` | **R** | Write-only wait barrier; DEFER: possible instrument queue stall and no reliable readback |
| Common 5-2…5-6 | `*PSC / *PSC?` | **W** | R3.5.41 `power_on_status_clear_state()` queries `*PSC?` only (HW REVIEWED PASS 2026-10-29: one real `0` read, OFF-only); `*PSC` write and power-on effects remain untested/blocked |
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
| DISPlay 5-9…5-10 | `DISP[:WIND][:STATe] / ?` | **W** | R4.4-A `display_enabled()` query-only `DISP?`; R4.4-B **HW REVIEWED PASS** (2026-10-29; one `DISP?=1`, OFF-only, firmware `1.04-1.04`); writes unsupported; see `docs/reviews/R4_4_B_DISPLAY_HW_REVIEW_2026-10-29.md` |
| DISPlay 5-9…5-10 | `DISP[:WIND]:TEXT[:DATA]` | **U** | Пользовательский текст |
| DISPlay 5-9…5-10 | `DISP[:WIND]:TEXT:CLEar` | **U** | Очистка текста |
| INSTrument 5-10…5-12 | `INST[:SELect] / ?` | **W** | R4.5-A: `selected_channel_name()` reads `INST?` only; CH1/CH2/CH3; symbolic write is unsupported, **SIM TESTED / HW NOT_RUN** |
| INSTrument 5-10…5-12 | `INST:NSELect / ?` | **W** | Селектор канала + проверка |
| INSTrument 5-10…5-12 | `INST:COMBine:SERies` | **U** | Отдельная альтернатива OUTP:SER |
| INSTrument 5-10…5-12 | `INST:COMBine:PARAllel` | **U** | Отдельная альтернатива OUTP:PARALLEL |
| INSTrument 5-10…5-12 | `INST:COMBine:TRACk` | **U** | Отдельная альтернатива OUTP:TRAC |
| INSTrument 5-10…5-12 | `INST:COMBine:OFF` | **U** | Проверить топологические эффекты |
| INSTrument 5-10…5-12 | `INST:COUPle[:TRIGger] / ?` | **R** | Связь триггеров/каналов |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:VOLT[:DC]?` | **W** | `read_channel()` вызывает `MEAS:VOLT?` |
| MEASure 5-12…5-13 | `FETCh[:VOLT][:DC]?` | **W** | R4.4-A: `fetch_measurement(ch, "voltage")`, query-only `FETC:VOLT?`; R4.4-C HW REVIEWED PASS (one Independent CH1 low-power value 3.2996 V; freshness UNVERIFIED) |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:CURR[:DC]?` | **W** | `read_channel()` вызывает `MEAS:CURR?` |
| MEASure 5-12…5-13 | `FETCh:CURR[:DC]?` | **W** | R4.4-A: `fetch_measurement(ch, "current")`, query-only `FETC:CURR?`; R4.4-C HW REVIEWED PASS (one CH1 low-power value 0.0811749 A; freshness UNVERIFIED) |
| MEASure 5-12…5-13 | `MEAS[:SCAL]:POW[:DC]?` | **W** | R4.4-A: `measure_power(ch)` performs `MEAS:POW?`; `Reading.power_w` remains U×I; R4.4-C HW REVIEWED PASS (one CH1 point 0.267863 W) |
| MEASure 5-12…5-13 | `FETCh:POW[:DC]?` | **W** | R4.4-A: `fetch_measurement(ch, "power")`, query-only `FETC:POW?`; R4.4-C HW REVIEWED PASS (one CH1 low-power value 0.267907 W; freshness UNVERIFIED) |
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
| STATus 5-19…5-23 | `STAT:QUES[:EVENt]?` | **W** | R4.2-B: `consume_status_event("questionable", acknowledge_clear=True)`; read-to-clear, OFF-only, HW REVIEWED PASS EVENT 2026-10-29 (one-shot; actual clearing not independently demonstrated) |
| STATus 5-19…5-23 | `STAT:QUES:ENABle / ?` | **W** | R4.2: `status_enable_mask("questionable")` queries `...:ENAB?` only; writes unsupported |
| STATus 5-19…5-23 | `STAT:QUES:CONDition?` | **W** | `status_snapshot()` |
| STATus 5-19…5-23 | `STAT:PRESet` | **U** | Сброс status config |
| STATus 5-19…5-23 | `STAT:QUES:INST[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:QUES:INST:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("questionable")`: read-only; writes unsupported; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>[:EVENt]?` | **U** | Event/read-to-clear |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>:ENABle / ?` | **W** | R4.2-C `status_instrument_enable_mask("questionable", channel=1..3)`; queries only; HW REVIEWED PASS READ_ONLY 2026-10-29 |
| STATus 5-19…5-23 | `STAT:QUES:INST:ISUM<x>:COND?` | **W** | `read_channel()` mode decoding |
| STATus 5-19…5-23 | `STAT:OPER[:EVENt]?` | **W** | R4.2-B: `consume_status_event("operation", acknowledge_clear=True)`; potentially destructive event read, OFF-only, HW REVIEWED PASS EVENT 2026-10-29 (one-shot) |
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
| SOURce 5-24…5-28 | `CURRent:STEP / ?` | **W** | R4.5-A: `source_step(ch, "current")` reads `CURR:STEP?` only in armed Independent; writes/UP/DOWN blocked; **SIM TESTED / HW NOT_RUN** |
| SOURce 5-24…5-28 | `CURRent:TRIGgered / ?` | **R** | Триггер может менять ограничение тока |
| SOURce 5-24…5-28 | `[SOUR:]VOLTage[:LEV] / ?` | **W** | Уставка и readback через `set_channel()` |
| SOURce 5-24…5-28 | `VOLTage:UP` | **U** | Инкремент уставки |
| SOURce 5-24…5-28 | `VOLTage:DOWN` | **U** | Декремент уставки |
| SOURce 5-24…5-28 | `VOLTage:STEP / ?` | **W** | R4.5-A: `source_step(ch, "voltage")` reads `VOLT:STEP?` only in armed Independent; writes/UP/DOWN blocked; response format not yet HW confirmed |
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

## R4.2-B — explicit event consumption (SIM TESTED, HW REVIEWED PASS: EVENT 2026-10-29; clear effect not independently measured)

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
  all event queries remain treated as potentially destructive.
  **One-shot event queries HW REVIEWED PASS** on 2026-10-29:
  `*ESR?=128` (PON), QUES=0, OPER=0. Clear-after-read was not tested with a
  second read. See [EVENT review](hardware_reports/R4_2_EVENT_review_2026-10-29.md).

### R3.5.52 — event query virtual-UART regression (NO new HW qualification)

The R4.2-B `consume_status_event()` offline tests now use the **production
`Keithley2231A` through the normal SCPI dispatcher** and a POSIX virtual UART.
A minimal peer returns independent clear-on-read fixture values for `*ESR?`,
`STAT:QUES:EVEN?` and `STAT:OPER:EVEN?`. This checks exact transmitted bytes,
OFF-preflight selection/restore, explicit acknowledgement and exactly one
read-to-clear query per call. An invalid/lost event reply is modeled as
**already consumed**, then the driver must forbid every further command;
a failed preflight must not consume any event. The test peer is only a UART
response fixture, not a parallel instrument driver or evidence of an actual
firmware latch clearing. Previously reviewed R4.2 event hardware evidence
contains the same preflight sequence modulo explicit `SYST:ERR?` polls made by
the separate hardware qualification runner. The public event API itself does
not drain the error queue after the destructive read.

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
**Event B HW REVIEWED PASS** (2026-10-29, one-shot reads; no independent
clearing proof): [review](hardware_reports/R4_2_EVENT_review_2026-10-29.md).
Hardware qualification protocol for A/C masks and B event reads is
[`R4_2_HARDWARE.ru.md`](R4_2_HARDWARE.ru.md). It consists of two manually
gated, separate real-PSU runs. Step A must be reviewed before step B. No
programmatic PASS or simulation claim upgrades SCPI families to HW VERIFIED.
Next: [R4.3-A risk disposition](R4_3_RISK_REGISTER.md), without new live writes.

## R3.5.49: SERVICE (virtual UART, NOT HARDWARE QUALIFIED)

`Keithley2231A.service_operation()` с `service_mode=True` и отдельным
`ServiceProfile` использует единственный общий SCPI dispatcher. Через POSIX
псевдо-UART проверены точные байты `*CLS`, `*WAI`, `*RST`, опрос ошибок и OFF;
три дублирующих fake-layer удалены. Всё это **только программные тесты**,
не успешная квалификация данных команд на физическом источнике.
Калибровка, `*TST?`, запись `*PSC` и разблокировка пароля по-прежнему
запрещены для отправки до уточнения документации.

## R3.5.50 — virtual UART wire regression for R4.3 PSC/OPC

In `tests/offline/test_r4_3_status_virtual_uart.py`, the existing R4.3 hardware
functions `run_psc` and `run_opc` invoke the production driver through a POSIX
virtual serial port. Both full successful scripts must match the independently
specified 75-command trace byte for byte: 65 queries, 10 writes, 37
`SYST:ERR?` polls, one target query, six OFF readbacks, no output writes.
Fault injection covers missing replies, malformed responses, non-OFF channels,
changed ESE masks and SCPI error after the target query. Unknown commands are
rejected by the peer instead of receiving a fabricated success reply.

These are **software UART tests**; the previously reviewed physical R4.3 PASS
claims are unchanged. Other legacy FakeTransport tests are not claimed to be
migrated in this increment. No actual CAL/service SCPI is sent to hardware.

The expected 75-command sequence was independently compared against the
low-level `driver_trace` command order in the previously reviewed real
`R43PSC` (2026-10-29T11:12 UTC) and `R43OPC` (2026-10-29T11:51 UTC)
evidence: **75/75 positions match for both**. The higher-level scope `query`
records may precede an automatically injected `SYST:REM`; therefore those
records must not be mistaken for raw UART transmission order.

## R3.5.51 — migrated R4.2-A/C status enable tests (software PTY)

`tests/offline/test_r4_2_status_reads.py` and
`tests/offline/test_r4_2_nested_enable.py` no longer inherit fake drivers.
The production `Keithley2231A` API goes through `SerialTransport` (when
installed) or the test-only POSIX PTY transport, with a peer in
`tests/support/virtual_uart.py` returning allowlisted readback fixtures.
Verified literal command order and CRLF wire bytes for `SYST:MODU?`, `*ESE?`,
`*SRE?`, `STAT:QUES:ENAB?`, `STAT:OPER:ENAB?`, and eight
`STAT:{QUES,OPER}:INST[:ISUM1/2/3]:ENAB?` queries. Invalid register/channel
inputs emit zero SCPI. Malformed or lost replies latch fault and prevent
retry; neither events (`*ESR?`, `*:EVEN?`) nor clears, output writes or
channel-selection commands are emitted by mask tests. The armed-flag test
injects the in-memory state only; it is not a full physical profile exercise.
No production driver changes or new physical hardware evidence. These status
mask commands retain their previous bounded hardware review claims; no new
`HW PASS` is implied by this software test refactor.

### R3.5.53 — error-path regression on virtual UART (software only)

`*IDN?`, `INST:NSEL?`, `CHAN:OUTP?` and `SYST:ERR?` negative responses
now exercise the production `Keithley2231A` dispatcher over a PTY rather
than selected historical `FakeTransport`-based tests. Exact CRLF, no retry,
no automatic shutdown on an uncertain link, no channel restore after malformed
selection/output responses, and no extra `SYST:ERR?` after an invalid output
reply are asserted. These are controlled peer injections, not physical
instrument observations. No new SCPI support or safety qualification is implied.

### R3.5.54 — armed SCPI faults and OFF confirmation (software PTY only)

The production `Keithley2231A` driver applies an Independent profile via
actual SCPI calls to a strict virtual UART, then enables CH1 with the normal
`output(1, True)` API. Injections at `SYST:ERR?`, `CHAN:OUTP?` and shutdown
acknowledgements check the exact wire order: one error poll, one global OFF,
three-channel readback, optional single-channel OFF fallback, and no further
SCPI after a link-uncertain reply. The peer implements minimal readback
registers, not another SCPI driver or independent execution workflow.
These tests do **not** establish hardware OFF or new calibration support.

### R3.5.55 — bundled virtual-UART regression (software only)

One combined increment retires redundant `FakeTransport` tests of the *public*
`power_on_status_clear_state()` and `operation_complete()` methods and
replaces them with wire-level `Keithley2231A`/PTY tests. The separate hardware
runner whitelist/evidence contract tests stay in place because their safety
scopes are different; the full runner wire scripts are already tested over PTY.

The same bundle migrates the implicit-monitor, four profile-drift and lost
monitor-reply cases to `apply_profile()` plus the real profile monitor over
virtual UART; no second driver/state machine is introduced. Hardware OFF is
**not** inferred from virtual readback. The physical Keithley is never opened.

The test-only peer verifies CRLF and exact command counts; missing replies
latch an uncertain link. A serial driver's `readline()` cannot *atomically*
prove that no second delayed reply line will arrive; this is a transport
limitation, not an unqualified guarantee of reply framing. Neither offline
PASS nor existing bounded physical reviews are expanded by this work.


## R3.5.56 — four production-driver UART migrations (software only)

Fifteen original `FakeTransport` regression tests have been **replaced**, not
retained as duplicated execution paths. The new test module
`tests/offline/test_r4_3_identity_output_numeric_uart.py` exercises the actual
`Keithley2231A` through the test-only POSIX pseudo-terminal:

- `output_states()` without arming, with a live CH2, after a real
  `apply_profile()`, with selection restoration and with lost readback;
- `*IDN?` physical identity/serial binding, identity switch between requests,
  and observed Main/Aux revision variation without rewriting identity;
- guarded diagnostics: `diagnostic_query()` remote setup, debug/normal
  profile command equivalence and no raw adapter bypass;
- malformed measurements and status masks (fractional, out-of-range,
  oversized, NaN) and invalid `*STB?` on an applied profile.

The virtual peer accepts a *minimal, explicit* condition-register and
measurement readback subset in `profile_registers=True` mode. It records
actual UART lines with CRLF; malformed/absent responses block follow-up SCPI,
with **no speculative OFF attempt on an uncertain transport**. The tests
never claim physical output OFF, certified firmware behavior or new service
rights. Both production device.py and transport.py remain unchanged. The
legacy FakeTransport is retained for topology, tracking, sweep and other
currently non-migrated test cases; no compatibility layer is introduced.

## R4.4-A — Scalar measurement + display query-only bundle (SIM TESTED, HW NOT_RUN)

Based on Reference Manual 077100401 §5, pages **5-10**, **5-13–5-14**,
`display_enabled()` reads only `DISP?` with strict 0/1 or ON/OFF parsing.
This is **front-panel visibility**, not proof that any power output is OFF.
It may be used without an applied profile; faulted or uncertain-link sessions
cannot be reused. Nothing writes display text or visibility.

`measure_power(ch)` initiates one `MEAS:POW?`; `fetch_measurement(ch, kind)`
queries one `FETC:VOLT?` / `FETC:CURR?` / `FETC:POW?` for an existing/current
instrument value. Both require an armed profile, validate the request before
SCPI, run the existing full profile-integrity check, and select the channel
using the same guarded dispatcher. For Series/Parallel CH2 is not an independent
logical output and is rejected. No implicit power-on/off, status-event reading,
error-queue consumption, retries, or trigger commands. This software check
**does not** establish actual firmware replies, accuracy, age of FETC data, or
simultaneity with `read_channel()`; real-device testing requires an independent
operator-approved protocol. `Reading.power_w` remains sequential V×I, not
replaced with or silently corrected by the new direct instrument power query.

POSIX virtual-UART tests exercise exact CRLF bytes, command allowlisting,
read-only responses, invalid requests before I/O, profile drift before a scalar
query, strict malformed reply terminal faults, lost responses without unsafe
post-fault commands, and display reading before profile installation. The peer
provides canned replies only, **not an electrical model or measurement source**.

## R4.4-B — hardware DISP? OFF-only reviewed (one-query HW REVIEWED PASS)

`tools/r4_4_display_qualify.py` provides an explicit dry-run and a separately
operator-gated `--execute` for **one** `DISP?` query. A strict stage adapter
restricts the wire to identity, selected-channel/OFF/topology checks, one
`DISP?`, and post-query `SYST:ERR?` error-queue consumption. Only
`SYST:REM`, `INST:NSEL 1/2/3` and `SYST:LOC` session writes are allowed.
The stage refuses any ON output, combined topology, bad readback, malformed
boolean or UART loss before further commands; it never sends `OUTP 0`.
Software simulation checks stage policy; one real OFF-only `DISP?=1`
observation has now been independently reviewed against a complete 254-record
JSONL evidence trace. See `docs/reviews/R4_4_B_DISPLAY_HW_REVIEW_2026-10-29.md`.
A display query is **not** proof of output-off state. The
other four R4.4-A scalar queries remain SIM TESTED / HW NOT_RUN and await a
separate low-power approved phase. See `docs/R4_4_HARDWARE_PLAN.ru.md`.

## R4.4-C — scalar low-power hardware procedure prepared, HW NOT_RUN

`tools/r4_4_scalar_qualify.py` and `tests/hardware/r4_4_scalar_stage.py`
prepare one operator-authorized Independent CH1/CR40Ω/DCV10V test of the
already SIM-tested `MEAS:POW?`, `FETC:VOLT?`, `FETC:CURR?`, `FETC:POW?`.
14 offline UART tests cover allowed SCPI, explicit power gate, comparison
of PSU/KEL/DMM, invalid values, link failures, conditional independent
shutdown and absence of new commands after uncertain link. No real
measurement has been made. **All four commands remain HW NOT_RUN**; this
is not a transfer of previous H03/H04P powered qualification.


#### R4.4-C actual low-power CH1 attempt (2026-10-29)

**HW ABORTED**: during a powered trial, `DMM7510 :READ?=11.61243 V` on a 10 V range disagreed with independent PSU/KEL103 readings (~3.3 V). The guarded runner stopped and confirmed PSU/KEL OFF; **`MEAS:POW?` and all `FETC:*?` were never transmitted**. Accordingly all four new scalar queries remain `HW NOT_RUN` despite passing software regression. Evidence SHA256 and safety audit are documented in `docs/reviews/R4_4_C_SCALAR_ABORT_2026-10-29.md`.

## R4.5-A — source observability without stepping (SIM TESTED, HW NOT_RUN)

- `selected_channel_name()` sends one `INST?` and returns **only** the documented
  symbolic channel string `CH1`, `CH2` or `CH3`; no channel-select write from
  this method. The session may authenticate and enter Remote before the query.
- `source_step(ch, kind)` accepts only real integer channel 1–3, and exact kind
  `voltage`/`current`; it requires an armed Independent profile and checks its
  integrity before selecting the channel to observe one `VOLT:STEP?` or
  `CURR:STEP?` numeric preset. Bounded numbers are **digital observations**,
  not active limits, measured outputs, or authorization to use those steps.
- Explicit exception in the SCPI policy permits **only** `VOLT:STEP?` and
  `CURR:STEP?`. Any `STEP` write, `UP`, `DOWN`, trigger, `OUTP:ENAB` write,
  `APPLy` or reset remains blocked; no generic raw executor is introduced.
- Invalid/missing reply and lost transport latch UNKNOWN / no retry; no
  unconfirmed automatic OFF is sent. A profile-integrity drift uses the
  existing safety response. The virtual peer scripts 0.01 V / 0.001 A; it
  does not model real stepping, a cache, or firmware behavior.
- The vendor documents `INST?` -> CHn (5-11), `CURR:STEP?` numerical current
  increment (5-25), `VOLT:STEP?` query syntax (5-27), but oddly lists
  `Returns None` for voltage step; **real hardware reply format remains unverified**.
  Do not perform powered hardware testing based on these software tests.

#### R4.5-B hardware evidence preparation

`INST?`, `VOLT:STEP?`, `CURR:STEP?` remain **SIM TESTED / HW NOT_RUN**.
`tools/r4_5_source_qualify.py` now provides separately gated OFF-only
`INST?` and optional OFF-profile CH1 STEP queries; runner presence and virtual
UART pass **do not** constitute hardware verification. The VOLT:STEP? reply
remains firmware-dependent/unknown until original on-wire JSONL review.

## R4.5-B: первое аппаратное наблюдение INST? (29.10.2026)

Терминальный протокол подтверждает один `INST? -> CH1` при `INST:NSEL? -> 1`, всех трёх выходах OFF. **TERMINAL REVIEWED PASS; JSONL PENDING**. Независимая трасса не приложена, `VOLT:STEP?`/`CURR:STEP?` остаются `HW NOT_RUN`.

### R4.5-B original evidence: bounded hardware readback (2026-10-29)

`INST?` **HW REVIEWED PASS** on selected CH1 while OFF (two real replies,
`CH1`). `VOLT:STEP?` **HW REVIEWED PASS** for one CH1 OFF-profile numeric
reply `0.001 V`; `CURR:STEP?` **HW REVIEWED PASS** for one CH1 OFF-profile
numeric reply `0.001 A`. Firmware `1.04-1.04`; no output enabled. The STEP
run **modified OFF-channel presets by authorization**; this was not a
profile-free or fully nonmutating session. The vendor `Returns None` wording
for `VOLT:STEP?` does not describe this one observed numeric response.
No STEP writes/UP/DOWN, no powered operation, no other channels or firmware
can be inferred. See `docs/reviews/R4_5_B_INST_STEP_HW_REVIEW_2026-10-29.md`.

### R4.5-C SOURCE step mutation (simulation-only, not hardware qualified)

- `VOLT:STEP <value>`, `CURR:STEP <value>`: **SIM TESTED / HW NOT_RUN**, typed
  `configure_source_step`, Independent control profile, all three outputs OFF,
  increment multiples of 0.001, bounded and read back with `VOLT/CURR:STEP?`.
- `VOLT:UP`, `VOLT:DOWN`, `CURR:UP`, `CURR:DOWN`: **SIM TESTED / HW NOT_RUN**,
  typed `step_source`, same-session registered step; exact 1mV/1mA digital
  target tracking, strict preflight, bounds, check-errors and post-verification.
- All new mutations blocked when any output ON, under power-only, Tracking,
  Series/Parallel, on an uncertain link, or from raw/debug mode. Installing a
  source step also blocks later ON commands on the same session.
- `VOLT:STEP?=0.001 V`, `CURR:STEP?=0.001 A` from R4.5-B **remain bounded
  HW REVIEWED PASS for CH1 read-only**, not validation of writing or stepping.
- Do not infer physical voltage/current increment from digital UART fixture.
  Real precision, rounding, edge behavior and switching transients need R4.5-D/E.
  See `docs/R4_5_FUNCTIONAL_QUALIFICATION.ru.md`.

### R4.5-D: bounded STEP mutation qualification prepared (NO hardware evidence)

`VOLT:STEP`, `CURR:STEP`, `VOLT:UP/DOWN`, `CURR:UP/DOWN` have a new
operator-gated *OFF-only* physical qualification runner. The production
methods are the same as R4.5-C; the runner adds a second wire-scoped allowlist,
fixed Independent settings and independent physical OFF confirmations.
**SIM TESTED / HW NOT_RUN / NOT EXPERIMENT READY.** A single eventual hardware
PASS will establish only that one channel/kind/increment case, never all
24 matrix cases or analog/energized resolution. See
`docs/R4_5_D_STEP_MUTATION_OFF_PLAN.ru.md`.

### R4.5-D first genuine STEP mutation — narrow hardware review

- `VOLT:STEP 0.010` + `VOLT:UP` ×2 + `VOLT:DOWN` ×2:
  **HW REVIEWED PASS only CH1, all outputs OFF, no load**, 1.04-1.04 firmware.
  Digital readback 3.30→3.31→3.32→3.31→3.30 V; original JSONL reviewed
  with 611 zero-error polls and 78 OFF readbacks. Independent evidence audit:
  `docs/reviews/R4_5_D_CH1_VOLT_10MV_HW_REVIEW_2026-10-30.md`.
- No hardware qualification yet for CH1 1 mV, alternate steps/channels,
  `CURR:STEP` writes, voltage/current mutation under power, CV/CC, V–I
  experiments, analog resolution or fault-induced recovery on the bench.

### R4.5-D voltage STEP refusal (2026-10-30)

CH1 `VOLT:STEP 0.010` digital OFF-only HW REVIEWED PASS;
`VOLT:STEP 0.001` returned SCPI 120 (Parameter overflowed), aborted safely.
Voltage STEP writes now require 10 mV grid (datasheet setting resolution),
current STEP writes 1 mA grid; no powered STEP or CV/CC readiness is claimed.
See `docs/reviews/R4_5_D_CH1_VOLT_1MV_REJECTION_2026-10-30.md`.

### R4.5-D CH1 current 1 mA OFF-only independent review (2026-10-30)

`CURR:STEP 0.001` returned successful `SYST:ERR?=0`, then
`CURR:STEP?=0.001`; `CURR:UP` ×2 and `CURR:DOWN` ×2 produced
`CURR?` values 0.101, 0.102, 0.101 and 0.100 A. All 78 CHAN:OUTP?
readbacks OFF; 611 zero SCPI error polls. Original 4449-record
JSONL independently audited. Scope **HW REVIEWED PASS for CH1 OFF digital
mutation only** — no current measured, no CC, no powered behavior.
See `docs/reviews/R4_5_D_CH1_CURR_1MA_HW_REVIEW_2026-10-30.md`.
`VOLT:STEP` 1/5mV are NOT in the approved write matrix: do not
confuse the readable 1mV register value with accepted write precision.

R4.5-D аппаратный review: CH1 `CURR:STEP 0.005` и `CURR:UP/DOWN`
(2×UP, 2×DOWN) **HW REVIEWED PASS (OFF-only digital, one case)**.
SCPI error=0, выходы OFF, итоговая уставка 0.100 А.
Другие шаги, комбинации и аналоговый CC не подтверждены этим случаем;
статус `EXPERIMENT READY` не присваивается.

#### R4.5-D batch testing

A single OFF-only driver session now supports a physically gated matrix of up to 18 digital `VOLT/CURR:STEP`, `UP/DOWN` permutations with verified readbacks. Software: `tools/r4_5_step_batch_qualify.py`, `tests/hardware/r4_5_step_batch_stage.py`; original-evidence auditor: `tools/audit_r4_5_step_batch_evidence.py`. **Batch hardware NOT_RUN; powered STEP NOT_RUN.** No new production SCPI command exposure or authorization to energize outputs.

### R4.5-D current batch: hardware scope update

`CURR:STEP` + `CURR:UP/DOWN` **HW REVIEWED PASS for all nine OFF-only digital combinations** (CH1/CH2/CH3 × 0.001/0.005/0.010 A), exactly restored readbacks, error queue zero and outputs OFF. `VOLT:STEP 0.010` on CH1 previously independently confirmed; `VOLT:STEP 0.001` rejected by real SCPI 120. Do not label powered/analog STEP as qualified. Evidence and hash: `docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md`.
