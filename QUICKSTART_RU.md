# Быстрый старт (русский)

Актуальная полная инструкция версии **0.2.0**: [docs/README.ru.md](docs/README.ru.md).
Архитектура: [docs/ARCHITECTURE.ru.md](docs/ARCHITECTURE.ru.md).
Матрица аппаратных испытаний: [docs/VERIFICATION.ru.md](docs/VERIFICATION.ru.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
python examples/probe.py --port /dev/ttyUSB1
```

Первые команды безопасно выполняют только чтение. Не включайте реальную нагрузку, пока функции настройки и управления на вашем устройстве и прошивке не будут проверены на стенде.
**Series/Parallel требуют внешних соединений выходов CH1/CH2: программно перемычки внутри прибора не включаются.**


## Единая конфигурация испытательного стенда

Всю конфигурацию подключения Keithley 2231A, KEL103, DMM7510 и TDS3064B
сохраняем в одном локальном TOML-файле. Параметры соединений нельзя изменять
отдельными флагами каждого аппаратного теста.

```bash
cp config/bench.example.toml config/bench.local.toml
# Проверить адреса, физическую проводку и характеристики адаптеров.
python3 tools/hw_qualify.py preflight H01 --config config/bench.local.toml
# Только после проверки карточки подключения:
python3 tools/hw_qualify.py run H01 --config config/bench.local.toml --execute
```

`preflight` не открывает порты и не посылает SCPI. При `--execute` остаются
обязательные подтверждения оператора. Исторические успешные H01–H12
не являются автоматическим аппаратным PASS для нового исходного кода R1.

Подробности: [R1_INFRASTRUCTURE.ru.md](docs/R1_INFRASTRUCTURE.ru.md).
