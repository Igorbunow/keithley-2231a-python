# Hardware qualification matrix / Матрица аппаратной квалификации

**Current source:** H01 PASS (read-only; HW OBSERVED). H02 PASS (OFF-only profile, panel clamp, API soft guard, gated instrument rejection; HW OBSERVED), report dated 2026-10-26. H03 PASS for the tested scope on the current runner: six connected-load CV/CC points, display confirmation and final OFF; CH3 OPER status caveat retained. H04/H04P PASS for reviewed scopes on the current runner. H05 PASS (HW OBSERVED) on the current runner with six TDS edges, live PSU/KEL103 readings, clean error queue and final physical OFF; [current report](2026-10-26T190417.647970Z_H05_qualification.md). H06 per-channel timer and re-enable subgroup PASS (HW OBSERVED): timer expiries 3.051/3.063/3.056 s and final physical OFF; Output Recall PASS for OFF `111→000`, ON `101→101`, `010→010`, `000→000`; all 970 error-queue responses were clean and final physical OFF confirmed. The first attempt aborted before energizing due runner defects; corrected source passed 95/95 offline. See [H06 results](2026-10-26T195235.744227Z_H06_qualification.md) and [first aborted attempt](2026-10-26T193545.109045Z_H06_qualification.md). Prior stage results below remain historical for their recorded source. H07 PASS (HW OBSERVED): 180 s acquisition, 44 samples/channel; one intentional >10 s gap/channel excluded from Ah/Wh; CSV statistics and integrals independently recomputed; both reset modes passed; 2,035 clean error-queue replies; final physical OFF confirmed. The first 120 s attempt stopped safely at 28/30 samples. [passing report](2026-10-26T205248.277386Z_H07_qualification.md) · [first attempt](2026-10-26T204422.181415Z_H07_qualification.md). H08 PASS (HW OBSERVED, bounded tracking scope): 12 DMM readings, six 1:1/1:2 ratios, four shorter-timer expiries, four OFF-only zero diagnostics, 3,404 clean PSU error replies and final physical OFF; [report](2026-10-26T213656.668407Z_H08_qualification.md). H09 OFF diagnostics are PARTIAL (HW OBSERVED): Series aliases CH2 to CH1 and changes independent CH1/CH2 settings; runner restored the safe profile and verified OFF. Powered Series is **PASS (HW OBSERVED)** for CV2/CC4/CV40/CC40 using KEL103 and DMM7510; the two CC mode readbacks were UNKNOWN because OPER and QUES bits conflict, while panel displays confirmed CC. [OFF replay](2026-10-26T220642.185652Z_H09_qualification.md) · [powered PASS](2026-10-27T070018.826475Z_H09_qualification.md) · [preflight](../H09_PREFLIGHT.ru.md). H10–H12/O01/O02 remain unqualified. Current offline suite: **111/111 PASS** (`PYTHONPATH=src:tests/hardware python3 -m unittest discover -s tests -v`).



Подгруппа `H06 --recall` прошла **SIM TESTED** и **PASS (HW OBSERVED)**:
Recall OFF `111→000`, Recall ON `101→101`, `010→010`, `000→000`; Save Last ON,
пустые клеммы, четыре ручных сетевых цикла. Новая serial-bound сессия записывала
boot readback до любых OFF/профильных команд. Все 970 ответов очереди ошибок чистые;
Recall OFF восстановлен, финальный физический OFF подтверждён.
[Процедура](../H06_OUTPUT_RECALL.ru.md) · [протокол](2026-10-26T201550.519080Z_H06_qualification.md).

Исходная матрица из CODEX_HARDWARE_ROADMAP.ru.md, 2026-10-25. H01 повторно пройден read-only; H02 прошёл полный OFF-only сценарий профиля и защиты с разными значениями по каналам (HW OBSERVED; [отчёт](2026-10-26T164414.981773Z_H02_qualification.md)). Первая попытка panel check остановилась из-за недостаточно точной инструкции; после её исправления повтор прошёл. H03 прошёл текущий сценарий из шести connected-load CV/CC точек с оговоркой OPER CH3; текущий runner сообщает PASS. H04/H04P PASS для review-объёма. H05 прошёл текущую аппаратную последовательность с TDS/KEL103, чистой очередью ошибок и финальным OFF; детали и оговорка по определению фронта приведены ниже.

An earlier H03 CH3 resume aborted before a measurement: KEL103 `*IDN?` timed out, and the
cleanup sequence read `-200, "Execution error"` after its first `OUTP 0`. The response
does not identify its originating command. Library emergency shutdown now enters Remote
before OFF. Offline regression is SIM TESTED 56/56; physical replay remains pending fresh
operator authorization. See [attempt report](2026-10-25T233455.502881Z_H03_qualification.md).

Outcome and feature verification level are independent. Existing UART/SYST:REM/*IDN? observation on firmware 1.04-1.04 is HW OBSERVED, never a formal H01 PASS. Only reviewed physical evidence may promote a feature to HW VERIFIED.

| ID / порядок | Группа и покрытие | Соединение и нагрузка | Критерий успеха | Outcome / Результат |
| --- | --- | --- | --- | --- |
| **H01** | Пассивный SCPI-preflight, идентификация, чтение статусных и mode-регистров, парсер ошибок | Все три OUT OFF, вход нагрузки OFF; без физических перемычек | Ответы воспроизводимы; нет таймаутов/сдвигов ответов; `*IDN?` корректен | **PASS (HW OBSERVED; read-only, all 3 channels; operator confirmed OFF; [2026-10-26 report](2026-10-26T145345.082772Z_H01_qualification.md))** |
| **H02** | Безнагрузочная установка профиля с уникальными CH значениями: V-set 1/1.5/2 V, I-set 0.1/0.2/0.3 A, V-cap 2/3/4 V, software I-cap 0.3/0.4/0.5 A; panel clamp 2.01/3.01/4.01→2/3/4 V; normal API soft reject до SCPI; gated `VOLT 2.01/3.01/4.01` probes с ожиданием error 120 | Независимые каналы, нагрузка отключена, все OFF; уникальная карта CH и отдельный debug gate | Панель clamps по каналу; API не отправляет превышение индивидуального V/I cap; debug запрос отвергается прибором, прежние V-set 1/1.5/2 V сохраняются, ошибка прочитана и OFF подтверждён | **PASS (HW OBSERVED; [corrected report](2026-10-26T164414.981773Z_H02_qualification.md); [first incomplete attempt](2026-10-26T163157.369595Z_H02_qualification.md))**
| **H03** | **Все 3 канала × CV и CC**, индивидуальный ON/OFF, измерение U/I/P и статус CV/CC | Нагрузка последовательно CH1→CH2→CH3, CR 40 Ω / CR 12 Ω; KEL103 USB serial; паузы на перемещение и дисплеи. Разные исходные значения и пределы каналов | CV/CC соответствуют индикации и независимому измерению; остальные каналы OFF | **PASS (HW OBSERVED; текущий runner; 6/6 точек; 697 ответов `SYST:ERR?` — `0, "No error"`; финальный OFF подтверждён; caveat CH3 OPER=`8`, QUES=`1/2`; CH2 CC повторён после исправления подключения нагрузки; [отчёт](2026-10-26T174052.170220Z_H03_qualification.md))** |
| **H04** | OFF-only профильные ограничения: `unrestricted()`, tamper, per-channel Max Volt Set 2/3/4 V, varied setpoints 1/2/3 V and current 0.1/0.2/0.3 A; software current caps 0.2/0.3/0.4 A; API boundary rejection | Независимая схема, нагрузка отключена, все OFF | Панель clamps requests 2.01/3.01/4.01 V to per-channel maxima; API rejects above each V/I cap before SCPI; tamper detected | **PASS (HW OBSERVED; corrected panel sequence; all outputs OFF; [current report](2026-10-26T182004.069364Z_H04_qualification.md); [earlier failed attempt](2026-10-26T180828.280308Z_H04_qualification.md))** |
| **H04P** | **Powered H04 substage**: перед нагрузкой Max Volt Set 4.50 V на панели, попытка 4.51 V; direct remote `VOLT 4.51` на каждом канале при выходах OFF; API over-cap rejection; далее низкомощные CV/CC границы; 3.30 V на каждом CH, CH1=2.20 V, CH2=3.30 V, CH3=4.40 V; current limits 0.100/0.250/0.500 A; `control` и `power_only` | Сначала OFF и нагрузка отключена для проверки Max Volt; затем KEL103 CR 47/56 Ω для CV и 22/11/5 Ω для CC; один подключённый CH за раз | Панель ограничила 4.51 V до 4.50 V; direct SCPI probes вернули ожидаемую ошибку 120, сохранили 3.30 V и затем чистую очередь; API отклонил 4.51 V/0.501 A до SCPI; все 21 powered CV/CC V/I/P измерения согласовались с KEL103 в допуске; соседи оставались OFF; все output/input OFF после каждой точки | **PASS (HW OBSERVED; current shared-path runner; 21/21 measurements; 2,833 clean `SYST:ERR?`, 3 expected error-120 probes; final physical OFF confirmed; CH3 OPER=`8`, QUES=`1/2`; [current report](2026-10-26T182637.334275Z_H04P_qualification.md); [raw log](../reports/hardware/2026-10-26T182637.334275Z_H04P/events.jsonl))** |
| **H05** | `power_only`: неизменяемая фиксация уставок, запрет `set_channel`/`timer`/произвольной последовательности и прямого `output`; CH1→CH2→CH3, затем CH3→CH2→CH1, задержки 1 s | Independent; KEL103 CR 22 Ω only across CH1, INPUT normally OFF; CH2/CH3 open. CH1–CH3 negative terminals and all x10 scope grounds at one common return. This ties outputs and earth; channel isolation is not tested. | One simultaneous 10 kpoint capture; six qualified edge onsets in the required order; 10% rising / 90% falling, qualified by opposite rail; each programmed 1 s minimum delay measured ≥0.95 s; all readbacks and final PSU/KEL103 OFF; no SCPI from rejected APIs. 0.5 V crossings retained as diagnostics. | **PASS (HW OBSERVED; firmware field 1.04-1.04; 6/6 edges; measured intervals 2.551/2.491/4.200/1.189/1.140 s; KEL103 0.9976 V / 0.0452 A / 0.0450 W; 296 clean `SYST:ERR?`; physical OFF confirmed; [current report](2026-10-26T190417.647970Z_H05_qualification.md); [raw log](../reports/hardware/2026-10-26T190417.647970Z_H05/events.jsonl))** |
| **H06** | Таймеры CH1/CH2/CH3, отключение по таймеру, повторное включение, влияние Output Recall | Таймеры: Independent; KEL103 CR 22 Ω по одному CH; разные низкие V/I. Recall: клеммы свободны, KEL103 физически отключена | H06 runner SIM TESTED. Timer 3.0 s, отключение в 2.5–4.0 s, повторное включение с timer OFF на 0.5 s. Recall: 4 ручных цикла при OFF/ON масках | **PASS (HW OBSERVED; таймеры CH1/CH2/CH3 = 3.051/3.063/3.056 s. Recall OFF `111→000`; ON `101→101`, `010→010`, `000→000`; Save Last ON сохранил V/I/max-V уставки. 970 чистых ответов очереди ошибок; Recall OFF восстановлен, финальный OFF подтверждён. [Recall-протокол](2026-10-26T201550.519080Z_H06_qualification.md); [таймерный протокол](2026-10-26T195235.744227Z_H06_qualification.md); [первая остановленная попытка](2026-10-26T193545.109045Z_H06_qualification.md))** |
| **H07** | Мониторинг: CSV, Δt, раздельные статистики U/I/P, min/max/mean/σ, Ah/Wh, reset, пропущенные измерения | Один канал + CR 40 Ω, остальных OFF; запись 2–5 минут | Числа воспроизводятся из CSV с указанной допуском, интегралы по фактическим временным меткам; измерения не нарушают профиль; [карточка H07](../H07_PREFLIGHT.ru.md), runner SIM TESTED with command latency | **PASS (HW OBSERVED; corrected 180 s run: 44 samples/channel, one intentional gap/channel, independent CSV recomputation, statistics-only and full resets passed; 2,035 clean `SYST:ERR?`; final OFF confirmed. [report](2026-10-26T205248.277386Z_H07_qualification.md); [first stopped attempt](2026-10-26T204422.181415Z_H07_qualification.md))** |
| **H08** | Tracking CH1/CH2 (1:1 и 1:2), чтение и изменение связанной уставки, запреты несовместимых API | **Без внешних перемычек**; один канал под нагрузкой либо оба холостой ход, один DMM поочерёдно на CH1/CH2 | 12 DMM readings; six ratios passed; four timer trials switched both channels OFF according to shorter 3 s delay; zero diagnostics OFF-only; final physical OFF | **PASS (HW OBSERVED; bounded scope). 3,404 clean PSU error responses; timer OFF observed 3.345–3.854 s after host output write; DMM after OFF 0.069–0.095 V. Calibration not qualified. [successful report](2026-10-26T213656.668407Z_H08_qualification.md); [first aborted attempt](2026-10-26T213208.734572Z_H08_qualification.md)** |
| **H09** | **Series**: CV/CC, суммарные U/I и `read_channel(1)` | Внешняя серия CH1− ↔ CH2+; CV2/CR40, CC4/CR12, CV40/CR2000 и CC40/CR1600 аппаратно прошли с подтверждением дисплея | Четыре значения PSU/KEL103/DMM7510 и 632 чистых ответа `SYST:ERR?` приведены в актуальной матрице. На обеих CC-точках `read_channel(1).regulation=UNKNOWN`: OPER mask 9 против QUES mask 2; дисплей подтвердил CC. Финальный физический OFF, нагрузка и измеритель отключены. Combined VOLT:LIM отключён; aggregate soft-cap не является аппаратной OVP. | **PASS (HW OBSERVED, с оговоркой статуса CC; [OFF replay](2026-10-26T220642.185652Z_H09_qualification.md) · [powered PASS](2026-10-27T070018.826475Z_H09_qualification.md) · [preflight](../H09_PREFLIGHT.ru.md))** |
| **H10** | **Parallel**: режим/индикация, суммарный I, U, поведение лимитов, CH3 | OFF-диагностика: без нагрузки/перемычек; силовая подгруппа ожидает исправления API | OFF: `OUTP:PARALLEL 1` алиасит CH2 к CH1; при выходе изменились CH2 setpoints и отключились оба V-limit enable; независимый профиль восстановлен; 431 чистый error read; оператор подтвердил финальный OFF. Powered Parallel не запускался. | **PARTIAL (HW OBSERVED; OFF только)** · [протокол](2026-10-27T074024.558437Z_H10_qualification.md) · [preflight](../H10_PREFLIGHT.ru.md) |
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
unproven. At this earlier diagnostic snapshot H03 remained PARTIAL and H04 was NOT_RUN. All outputs/load were read OFF and final
physical OFF was confirmed. No further library workaround was introduced.

CH3 workaround API addendum: the existing QUES-based decoder is retained, with a
new `Reading.regulation_source` identifying the supporting register(s). Additional
regressions passed in the operator-approved full offline run: **66/66**, exit 0,
0.289 s (SIM TESTED). At that offline snapshot there was no additional SCPI or hardware
run. Usage and limits:
[EN](../../README.md#ch3-status-workaround-and-limitations) /
[RU](../README.ru.md#workaround-статусов-ch3-и-ограничения).


## H03 reviewed disposition update — 2026-10-26

H03 is **PASS for the qualified functional scope**: CH1/CH2/CH3 each completed CV and CC, with panel confirmations, KEL103 comparisons and final OFF checks. CH3 ON/OFF, measured V/I, setpoints and CV/CC readback work; the library gets CH3 CV/CC from QUES when OPER contains only its ON bit. The raw diagnostic runner recorded PARTIAL because it strictly compared the redundant OPER mode bits against the manual. That runner result is preserved in its event report; this reviewed matrix classifies the functional stage PASS with an explicit caveat: firmware 1.04-1.04 CH3 OPER stays 8 in CV and CC. No calibrated DMM was used, so metrology accuracy is not claimed. H04 is **PASS for OFF-only scope**; H04P is **PASS for bounded powered low-power profile scope (HW OBSERVED)** with the same CH3 OPER caveat. No calibrated DMM was used and unrestricted rated limits were not energized. See [current H04 evidence](2026-10-26T182004.069364Z_H04_qualification.md) and [current H04P evidence](2026-10-26T182637.334275Z_H04P_qualification.md).

H10 OFF diagnostics (2026-10-27): PARTIAL (HW OBSERVED). Parallel aliases CH2
to CH1 and rewrites CH1/CH2 settings on exit, disabling both voltage limit
enables. The independent profile was restored and operator-confirmed OFF. Powered
Parallel remains NOT_RUN pending an explicit guarded profile/API. See [report](2026-10-27T074024.558437Z_H10_qualification.md).
