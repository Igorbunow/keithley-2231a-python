# Hardware qualification matrix / Матрица аппаратной квалификации

Исходная матрица из CODEX_HARDWARE_ROADMAP.ru.md, 2026-10-25. Current result: H01 PASS after an aborted first attempt; H02 PASS after a failed first attempt; H03 PARTIAL (CH1/CH2 CV/CC reviewed PASS; CH3 CV/CC behavior, library mode decoding, display and electrical comparison observed PASS; operation-status interpretation unresolved); H04–H12/O01/O02 NOT_RUN.

An earlier H03 CH3 resume aborted before a measurement: KEL103 `*IDN?` timed out, and the
cleanup sequence read `-200, "Execution error"` after its first `OUTP 0`. The response
does not identify its originating command. Library emergency shutdown now enters Remote
before OFF. Offline regression is SIM TESTED 56/56; physical replay remains pending fresh
operator authorization. See [attempt report](2026-10-25T233455.502881Z_H03_qualification.md).

Outcome and feature verification level are independent. Existing UART/SYST:REM/*IDN? observation on firmware 1.04-1.04 is HW OBSERVED, never a formal H01 PASS. Only reviewed physical evidence may promote a feature to HW VERIFIED.

| ID / порядок | Группа и покрытие | Соединение и нагрузка | Критерий успеха | Outcome / Результат |
| --- | --- | --- | --- | --- |
| **H01** | Пассивный SCPI-preflight, идентификация, чтение статусных и mode-регистров, парсер ошибок | Все три OUT OFF, вход нагрузки OFF; без физических перемычек | Ответы воспроизводимы; нет таймаутов/сдвигов ответов; `*IDN?` корректен | PASS |
| **H02** | Безнагрузочная установка защищённого профиля: все 3 канала, `VOLT:LIM`/state, readback, отказ на недопустимых V/I, неактивированный ON | Независимые каналы, нагрузка отсоединена | После применения все OFF, уставки/лимиты соответствуют профилю, отказы не отправлены в SCPI | PASS (second attempt; first FAIL) |
| **H03** | **Все 3 канала × CV и CC**, индивидуальный ON/OFF, измерение U/I/P и статус CV/CC | Нагрузка последовательно CH1→CH2→CH3, CR 40 Ω / CR 12 Ω; KEL103 по явно выбранному UDP/18190 или USB serial; паузы на проводку и дисплеи | CV и CC соответствуют индикации и независимому измерению; другие каналы остаются OFF | PARTIAL (CH1/CH2 CV/CC reviewed PASS; CH3 CV/CC function, display and electrical comparison observed PASS; OPER3 device response discrepancy confirmed; library CV/CC physically replayed; USB control HW OBSERVED) |
| **H04** | Профильные ограничения: защищённый, `unrestricted()` в маломощном сценарии; внешняя подмена уставки/лимита при OFF; аварийная реакция/запрет смены профиля | Независимая схема, все OFF для tamper | Нет программного выхода за cap; активный профиль неизменяем; обнаруживается смена **точных** уставок `power_only` | NOT_RUN |
| **H05** | `power_only`: предварительная фиксация уставок, запрет `set_channel`/`timer`/произвольной последовательности, CH1→CH2→CH3 и обратное выключение с паузами | Независимая схема, нагрузка на одном CH, остальные без нагрузки; независимый DMM/осциллограф желателен | Реальные статусы и порядок правильны; тайминги проверены с указанной погрешностью; если нет физического измерения всех фронтов — лишь PARTIAL | NOT_RUN |
| **H06** | Таймеры CH1/CH2/CH3, отключение по таймеру, повторное включение, влияние `Output Recall` | Независимые каналы; нагрузка поочерёдно или холостой ход с измерением | Выход отключается в установленном окне; не происходит непрошеного автозапуска. Проверка recall после AC power-cycle — отдельное ручное разрешение | NOT_RUN |
| **H07** | Мониторинг: CSV, Δt, раздельные статистики U/I/P, min/max/mean/σ, Ah/Wh, reset, пропущенные измерения | Один канал + CR 40 Ω, остальных OFF; запись 2–5 минут | Числа воспроизводятся из CSV с указанной допуском, интегралы по фактическим временным меткам; измерения не нарушают профиль | NOT_RUN |
| **H08** | Tracking CH1/CH2 (1:1 и 1:2), чтение и изменение связанной уставки, запреты несовместимых API | **Без внешних перемычек**; один канал под нагрузкой либо оба холостой ход, контролировать оба внешним DMM | Правильные отношения и status, безопасное поведение при 0 В/таймере; если API не умеет менять tracking — сначала доработать | NOT_RUN |
| **H09** | **Series**: режим/индикация, суммарное U, I, раздельные лимиты и интерпретация `read_channel(1)` | **ВНЕШНЯЯ серия** CH1− ↔ CH2+, нагрузка CH1+ → CH2−; как на Fig. 9 производителя, CH3 свободен | DMM/нагрузка видят суммарное U и корректный ток; нет ошибочного суммирования/удвоения и опасного включения CH3 | NOT_RUN |
| **H10** | **Parallel**: режим/индикация, суммарный I, U, поведение лимитов, CH3 | **ВНЕШНЯЯ параллель** CH1+ ↔ CH2+, CH1− ↔ CH2−, нагрузка на общих клеммах; как Fig. 10 производителя | Суммарный ток согласуется с нагрузкой; распределение токов не обязано быть 50/50; отсутствует недопустимый встречный ток | NOT_RUN |
| **H11** | Низкоэнергетическая I–V развертка с CR-нагрузкой (без настоящего диода), останов по условию/ошибке; CSV | После снятия объединяющих перемычек: независимый CH1, CR 40 Ω | Монотонные шаги, ограничение тока, валидные точки/CSV, гарантирована *попытка* OFF в `finally`; физический OFF проверяется отдельно | NOT_RUN |
| **H12** | Негативные сценарии и финальная проверка: неверные каналы/NaN/профиль, несовпадение readback, отказ I/O (эмулятор), ручное отключение, восстановление соединения | В основном offline; **реальное** извлечение USB при включённом выходе — только отдельное подтверждение и ручной контроль | Нет silent pass или автоматического повторного включения; явная сигнализация о неопределённом состоянии; очищены таймеры/режимы, все выходы OFF | NOT_RUN |
| **O01** | Координатор + внешний DMM: timestamp/ошибка наблюдателя, CSV, задержка между приборами | Только если DMM реально подключён и есть драйвер; иначе SIM TESTED / BLOCKED | Валидные показания и времена. Не называть аппаратно синхронным | NOT_RUN |
| **O02** | Два физических PSU / TCP-мост / внешняя аппаратная синхронизация | Только при наличии второго PSU, MCU-моста/триггеров; сейчас нет подтверждения наличия | Отдельный план после подготовки оборудования; иначе `NOT_RUN`/`PLANNED`, не притягивать к общему PASS | NOT_RUN |

H02 profile scenario and [operator card](../H02_PREFLIGHT.ru.md) are prepared. Offline
regression after the identity guard is 48/48 SIM TESTED. Physical attempt report:
[H02 evidence](2026-10-25T213651.354322Z_H02_qualification.md). It failed because the
firmware field changed between two `*IDN?` replies; the error queue stayed at zero,
the profile setpoints/caps were written, and channel OFF bits read 0/0/0. The operator
later confirmed OFF, load OFF/disconnected, and 1.00 V / 0.100 A setpoints on all
channels. The driver guard is fixed and passed offline. Corrected H02 rerun passed:
both IDN replies were firmware `1.04-0.01`, all error replies were zero, profile
readbacks passed, OFF bits were 0/0/0 and physical OFF was confirmed. See [corrected
H02 PASS](2026-10-25T214134.648837Z_H02_qualification.md). H04–H12/O01/O02 remain
NOT_RUN. H03 attempt 1 ran CH1 CV/CC with long, thin leads and stopped on a voltage
comparison mismatch. The voltage differences divided by load current imply 0.324 Ω and
0.325 Ω, suggesting lead/contact drop. After fitting short 10 AWG leads, attempt 2
produced reviewed PASS on CH1 CV and CC: Keithley/KEL103 were 3.9988/3.998 V at
0.0986824/0.099 A for CV, and 3.01538/3.008 V at 0.249777/0.250 A for CC. Status mask 10 and the operator-confirmed CC panel agreed. All OFF was confirmed;
both reviewed points are PASS. See the H03 [event log](../../reports/hardware/2026-10-25T222742.371680Z_H03/events.jsonl)
and [review report](2026-10-25T222742.371680Z_H03_qualification.md).

KEL103 read-only UDP/18190 preflight returned V3.30, INPUT OFF, CR and zero measurements.
H03 resumed at CH2: CV returned Keithley 3.9931 V / 0.0986671 A / 0.394513 W (status 9)
and KEL103 3.9924 V / 0.0999 A / 0.3988 W; CC returned 3.00772 V / 0.249603 A /
0.750575 W (status 10) and KEL103 3.0037 V / 0.2503 A / 0.7518 W. The operator
confirmed both displays and OFF after both points. Earlier CH3 CV returned operation
status 8 (ON without CV/CC bits), repeatedly; error queue remained zero. A later USB
attempt measured Keithley 3.99493 V / 0.100668 A / 0.402329 W and KEL103 4.0003 V /
0.0999 A / 0.3996 W, with operation status 8 and questionable status 1 (CV); all error
queue replies were zero. The operator retrospectively confirmed panel CV / KEL CR /
INPUT ON, then confirmed all OFF after shutdown. Display confirmation arrived too late
for the live gate; run remains ABORTED and point is not passed. The official manual
assigns CV/CC bits 0/1 to both channel status summaries and ON bit 3 to operation summary,
so operation status conflicts with the manual, questionable status, panel and measurements.
The 5 V CH3 rating was respected with 4 V / 0.5 A. USB KEL CR setup/readback, INPUT
control and powered telemetry are HW OBSERVED only. See [latest H03 report](2026-10-25T235757.702842Z_H03_qualification.md)
and [earlier CH3 report](2026-10-25T231834.474816Z_H03_qualification.md).

The corrected retry accepted the exact wiring and live display gates, configured KEL103
CR 40 Ω / INPUT ON, and measured Keithley 3.99493 V / 0.0989667 A / 0.395529 W and
KEL103 4.0003 V / 0.0999 A / 0.3996 W. Electrical comparison and live panel confirmation passed; all error replies were zero.
The runner stopped at its status guard (`UNKNOWN`, `OPER=8`, `QUES=1`); this is not an
SCPI error and does not indicate that CV failed to operate. Status interpretation/source
remains unresolved. All channels and load were shut down and confirmed OFF. See [physical evidence](2026-10-26T000805.061853Z_H03_qualification.md).

The prior gated USB run completed CH3 CV and CC with live PSU/KEL103 display confirmations.
CV: Keithley 3.99576 V / 0.0998173 A / 0.399012 W (SCPI power); KEL103 4.0003 V / 0.0999 A /
0.3996 W; `OPER=8`, `QUES=1`. CC: Keithley 2.98781 V / 0.249532 A / 0.745347 W (SCPI power);
KEL103 2.9840 V / 0.2487 A / 0.7421 W; `OPER=8`, `QUES=2`. Both comparisons passed H03
tolerances and all 192 error-queue reads were `0, "No error"`. The operation status omits
the documented CV/CC bits in both modes while the questionable status, panel and electrical
measurements agree. The corrected library now returns CV and CC from questionable status on
physical CH3 replay, while operation status remains inconsistent with the documented mode bits.
CH3 mode behavior and library mode decoding are physically observed in both modes; H03 remains
PARTIAL on operation-status qualification.
All channels and KEL103 INPUT were read OFF and final physical OFF was confirmed. See
[latest report](2026-10-26T003341.714219Z_H03_qualification.md) and [raw event log](../../reports/hardware/2026-10-26T003341.714219Z_H03/events.jsonl).

H01 attempt: [2026-10-26 local / 2026-10-25 UTC report](2026-10-25T210502.973378Z_H01_preflight.md).
Stopped on OUTP:PARA? after an I/O exception. Last channel ON bits were 0/0/0;
post-run physical OFF awaits operator confirmation. No output writes or retry.

H01 retest: [PASS on corrected query](2026-10-25T211550.545588Z_H01_preflight.md),
firmware 1.04-1.04, error queue 0 before/after, repeated OFF bits 0/0/0 and operator-confirmed PSU OFF.
The previous ABORTED attempt remains part of the history. The final confirmation
supersedes the pending PSU-OFF status above; load state is documented by the initial gate.

H01 readback addendum: [CH2/CH3 independent readback](2026-10-25T212311.676405Z_H01_CH2_CH3_readback.md).
Both channels returned `VOLT?=1`, `CURR?=0.1`, output OFF and timer OFF. All three
OFF bits remained 0; `SYST:ERR?` returned `0, "No error"` before and after. CH2/CH3
`VOLT:LIM?` returned 30.1 V / 5.1 V respectively with limit state 0 (observed,
not active protection). PSU meter readings are uncalibrated and have no independent DMM comparison.

H03 attempt 1 stopped on the CH1 voltage mismatch. After replacing the leads, attempt 2
measured CH1 CV/CC within criteria; the panel showed the tested CC mode. The CH2
continuation then passed CV/CC with display confirmations and all-OFF checks. CH3 CV
returned operation status 8 without CV/CC bits despite 3.99326 V / 0.0998173 A; all
error-queue replies were zero. CH3 uses a distinct 5 V rating (CH1/2: 30 V), while this
test capped it at 4 V / 0.5 A. A diagnostic runner update and offline regression are
prepared but unrun. See the [latest physical trace](../../reports/hardware/2026-10-25T231834.474816Z_H03/events.jsonl)
and [review report](2026-10-25T231834.474816Z_H03_qualification.md).


Latest corrected CH3 replay (`2026-10-26T003341.714219Z`): `read_channel()` reported library
regulation CV/CC from questionable masks 1/2 while operation masks remained 8. Keithley CV was
3.99576 V / 0.0998173 A / 0.399012 W (SCPI power); CC was 2.98781 V / 0.249532 A / 0.745347 W.
KEL103 readings were 4.0003 V / 0.0999 A / 0.3996 W and 2.9840 V / 0.2487 A / 0.7421 W.
All 192 error-queue replies were `0, "No error"`; physical final OFF was confirmed. Outcome
remains PARTIAL solely because operation-status mode qualification did not match.


CH3 diagnosis addendum: direct OFF-only UART observation agrees for short/full forms; operation masks 0/0/0, questionable masks 16/0/0. All six per-channel OP/QUES enable masks read 0. Two OFF probes returned 33+12 clear error-queue responses. The ON authorization guard was fixed before transmission; new diagnostic flow is SIM TESTED (64/64 offline), powered diagnosis NOT_RUN. H03 remains PARTIAL; no firmware root cause is asserted. See [OFF evidence](2026-10-26T004315.108014Z_H03_status_off.md).


Powered CH3 status diagnosis completed: [review and wire evidence](2026-10-26T005733.649705Z_H03_qualification.md).
CV (3.99826 V / 0.0998173 A) and CC (2.98198 V / 0.249532 A) agreed with KEL103 and
operator panels. All 274 error-queue responses were clear. Actual UART OPER3 response
was `38 0a` in both modes with short/full forms, including 83 s CV / 48 s CC observations
and CH1 selected. QUES3=1/2 and library CV/CC were correct. ENAB masks were not modified.
The anomaly is at the device response boundary; firmware versus manual cause remains
unproven. H03 remains PARTIAL and H04 NOT_RUN. All outputs/load were read OFF and final
physical OFF was confirmed. No further library workaround was introduced.

CH3 workaround API addendum: the existing QUES-based decoder is retained, with a
new `Reading.regulation_source` identifying the supporting register(s). Additional
regressions passed in the operator-approved full offline run: **66/66**, exit 0,
0.289 s (SIM TESTED). No additional SCPI or hardware run, no change to historical
evidence, H03 PARTIAL and H04 NOT_RUN. Usage and limits:
[EN](../../README.md#ch3-status-workaround-and-limitations) /
[RU](../README.ru.md#workaround-статусов-ch3-и-ограничения).
