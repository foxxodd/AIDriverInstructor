# TripCompiler

Единый Python 3.10-проект для послепоездочного анализа двух типов данных:

- `obd` — длинный CSV, экспортированный из Car Scanner (`OBD-II + GPS`);
- `wrc` — JSONL, записанный из официальной UDP-телеметрии EA Sports WRC на ПК.

Весь проект находится в корне репозитория. Отдельных WRC- и OBD-компиляторов нет:
источник является обязательным аргументом одной команды `tripcompiler compile`.

## Структура

```text
src/tripcompiler/
  cli.py          единый CLI
  compiler.py     диспетчер источников и общий формат результатов
  obd.py          адаптер Car Scanner CSV
  capture.py      запись WRC UDP
  schema.py       декодирование настраиваемого пакета WRC
  analysis.py     общая нормализация WRC и детекторы событий
tests/
docs/
drive_logs/       исходные записи, не изменяются и не коммитятся
compiled_trips/   результаты, не коммитятся
```

## Установка

```powershell
cd C:\projects\AIDriverInstructor
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## Компиляция OBD

```powershell
tripcompiler compile obd "drive_logs\2026-07-29 16-27-48.csv" `
  --output "compiled_trips\obd_2026-07-29"
```

OBD-адаптер:

- читает CSV с разделителем `;` и колонками `SECONDS`, `PID`, `VALUE`, `UNITS`,
  `LATITUDE`, `LONGTITUDE`;
- поддерживает UTF-8/UTF-8 BOM и CP1251;
- преобразует GPS в локальные метры;
- нормализует скорость, RPM, акселератор, тормоз, ускорения и скорости колёс;
- сохраняет каталог PID и распознанные исходные динамические сигналы;
- никогда не изменяет исходный CSV.

## Настройка и запись WRC

После первого запуска игра создаёт каталог
`%USERPROFILE%\Documents\My Games\WRC\telemetry`.

1. Скопировать
   `src\tripcompiler\config\wrc_ai_instructor.json` в
   `%USERPROFILE%\Documents\My Games\WRC\telemetry\udp\wrc_ai_instructor.json`.
2. Добавить в массив `udp.packets` файла игры `config.json`:

```json
{
  "bEnabled": true,
  "frequencyHz": 60,
  "ip": "127.0.0.1",
  "packet": "session_update",
  "port": 20779,
  "structure": "wrc_ai_instructor"
}
```

3. Проверить соответствие схемы текущей версии игры:

```powershell
tripcompiler validate-wrc
```

4. Запустить запись до входа на спецучасток и остановить Ctrl+C после финиша:

```powershell
tripcompiler record-wrc
```

По умолчанию запись создаётся в `drive_logs/wrc/<дата_время>/`.

5. Скомпилировать её тем же TripCompiler:

```powershell
tripcompiler compile wrc "drive_logs\wrc\20260816_120000\telemetry.jsonl" `
  --output "compiled_trips\wrc_20260816_120000"
```

## Общий формат результатов

Для обоих источников создаются:

- `telemetry.csv` — единая нормализованная схема;
- `events.json` — интервалы обнаруженных событий;
- `summary.json` — метрики поездки и качество данных;
- `report.html` — автономный отчёт.
- `script_ai.json` — общая траектория для последующей адаптации к BeamNG ScriptAI;
- `road_centerline.json` — локальная ось маршрута в метрах.

Для OBD дополнительно создаются:

- `pid_catalog.csv` — список всех PID, частота и сопоставление с общей схемой;
- `vehicle_dynamics_raw.csv` — распознанные исходные измерения без перезаписи CSV.

Общие детекторы отмечают резкое торможение/ускорение, высокое боковое ускорение,
ручник на скорости, большой угол скольжения, пробуксовку и одновременный газ с тормозом.
Пороговые значения являются начальными и требуют отдельной калибровки для реальной дороги
и раллийного симулятора.

## Проверки качества

```powershell
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m pytest
```

Минимальное покрытие — 75%. Те же проверки запускаются GitHub Actions на Python 3.10.
Подробности формата времени, координат и обработки потерь находятся в
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
