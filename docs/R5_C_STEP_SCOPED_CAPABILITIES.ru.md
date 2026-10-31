# R5-C — доказательные области применимости capabilities и аппаратный STEP

**Статус:** программный рефакторинг статических данных; аппаратные команды и алгоритмы SCPI не изменялись. Снимок рассчитан на R5-A + R5-B.

## Назначение

После аудита R5-B общий признак `source_step_query=simulated_only` оказался слишком грубым: зарегистрированы отдельные реальный CH1 `VOLT:STEP?` и CH1/CH2/CH3 `CURR:STEP?` при выключенных выходах, а также независимо проверенные низкомощные CH1 CV и CC изменения STEP под нагрузкой. **Это не означает** универсальной готовности произвольных STEP-операций на всех каналах.

В `capabilities.py` теперь отдельно перечислены:

| Признак | Маршрут драйвера | Подтверждённое из истории |
| --- | --- | --- |
| `source_step_query` | `source_step` | CH1 напряжение, CH1/CH2/CH3 ток: цифровые readback OFF |
| `source_step_off_configuration` | `configure_source_step` | Ограниченная цифровая запись и readback OFF |
| `source_step_off_mutation` | `step_source` | UP/DOWN и проверка цифровой уставки при OFF |
| `source_step_powered_qualification` | `powered_step_qualification` + `step_source` | Только CH1, только ограниченные низкомощные CV и CC; не общецелевой powered API |
| `hardware_triggered_setpoint` | Нет | Документировано `VOLT/CURR:TRIG`, `*TRG`; нет аппаратной квалификации |
| `trigger_channel_coupling` | Нет | Документировано `INST:COUP`; не доказана синхронность |
| `bulk_apply_setpoints` | Нет | Документировано `APPLy`; атомарность не доказана |
| `enable_channels_function` | Нет | Документировано `OUTP:ENAB`; опасная семантика меню, не заменять `output()` |

`HardwareEvidence` — неизменяемый типизированный указатель на **исторически ограниченную** проверку с полями `channels`, `operation`, `output_state`, `regulation`, `observation`, `reference`. Метаданные обновлены до `schema_version=2`, поскольку `to_dict()` добавляет `features[*].hardware_evidence`. В JSON поля динамического состояния прибора отсутствуют. Пустой список означает, что детальная история для функции здесь не закодирована, **а не** что все другие исторические испытания отсутствуют.

### Граница безопасности

- Сведения о нативной поддержке, наличии метода и реальных испытаниях — **три независимые характеристики**. `hardware_evidence` не даёт разрешения на вызов, не устанавливает профиль, не подтверждает реальную топологию и не обходит interlock.
- CH1 powered CV/CC были независимо проверены в узкой области и на двух разных нагрузочных профилях. Нельзя переносить их квалификацию на CH2/CH3, высокую мощность, произвольные последовательности, фронты или тайминги.
- `source_step_off_*` не свидетельствуют о физическом изменении U/I на выключенном выходе; это цифровая проверка состояния уставок.
- Библиотека по-прежнему не посылает `*TRG`, `VOLT:TRIG`, `CURR:TRIG`, `INST:COUP`, `APPLy` или `OUTP:ENAB` по обычному API.
- Нет реального определения возможностей при подключении, проверки firmware версии, новых аппаратных статусов или метрологических утверждений.

### Источники доказательств

`docs/reviews/R4_5_D_CH1_VOLT_10MV_HW_REVIEW_2026-10-30.md`, `docs/reviews/R4_5_D_CURRENT_BATCH_HW_REVIEW_2026-10-30.md`, `docs/reviews/R4_5_E_C_CH1_CV_FIVE_POINT_HW_REVIEW_2026-10-30.md`, `docs/reviews/R4_5_E_C_CH1_CC_FIVE_POINT_HW_REVIEW_2026-10-30.md`. Сопоставление семейств SCPI — `docs/R5_B_SCPI_GAP_AUDIT.ru.md`.

### Пример для будущего верхнего слоя

```python
from keithley2231a import get_capabilities

caps = get_capabilities()  # No instrument I/O.
assert caps.schema_version == 2
trigger = caps.feature('hardware_triggered_setpoint')
assert trigger.instrument_native and not trigger.driver_exposed
for scope in caps.feature('source_step_powered_qualification').hardware_evidence:
    print(scope.channels, scope.regulation, scope.reference)
```

Данный код выводит **документированные свидетельства**, но не выбирает автоматически разрешённую силовую процедуру. Логику оркестрации различных блоков питания, внешних DMM и будущего gateway необходимо держать на верхнем слое.

**Следующий этап:** R5-D — при необходимости отдельно подготовить сначала виртуальный UART и OFF-only readback для trigger регистров; никакого powered trigger без отдельного разрешения. Независимо от R5-D продолжать эргономику CH1/CH2/CH3, управление владением соединением и R6 packaging.
