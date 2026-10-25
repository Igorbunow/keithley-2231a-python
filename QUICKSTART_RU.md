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
