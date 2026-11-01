# Интеграция с пакетными агентами

Spice Sifter остаётся одиночным CLI-инструментом. Пакетный агент (например, агент, добавляющий компоненты в библиотеку) обязан выполнить **одну общую проверку готовности** перед серией испытаний. Готовый эталонный модуль — [`batch_agent.py`](../batch_agent.py).

## Preflight

```powershell
python spice_sifter.py doctor --json
```

Пример ответа:

```json
{
  "schema_version": "1.0",
  "status": "READY",
  "altium": {
    "path": "C:\\Program Files\\Altium\\AD24\\X2.EXE",
    "exists": true,
    "running": true,
    "responding": true,
    "version": "24.4.1.13"
  },
  "lock": {"available": true},
  "pending_run": null,
  "temporary_directory": {"path": "C:\\Users\\...\\Temp\\SpiceSifter", "writable": true},
  "dblib_resources": {"ready": true, "missing": []},
  "ready": true,
  "diagnostics": [],
  "suggested_action": null
}
```

Статусы:

- `READY` — можно запускать серию. Требуются Altium 24.4.x и подтверждённая отзывчивость (`responding = true`).
- `BUSY` — Altium не отвечает, занят другой запуск или активна предыдущая симуляция.
- `RECOVERY_REQUIRED` — есть завершённый/устаревший/неопределённый `pending-run`; сначала выполните `recover`.
- `ERROR` — нет X2.EXE, Altium не запущен, версия неизвестна или не поддерживается, `responding = null`, обнаружено несколько неоднозначных экземпляров, недоступна временная директория или ресурсы DbLib.

Агент должен прекратить **всю** серию, если `ready != true`:

```text
Agent
  ↓
doctor --json
  ↓
READY?
  ├── YES → выполнять тесты
  └── NO  → пометить все испытания NOT_RUN и остановиться
```

`doctor` не запускает электрическое моделирование. Он проверяет только доступные признаки: наличие файла, версию, процесс, отзывчивость, блокировку, маркер и временную директорию. Если блокирующий диалог Altium нельзя обнаружить надёжно, `doctor` не заявляет, что его отсутствие проверено.

## Эталонный пакетный запуск

```python
from batch_agent import run_batch

result = run_batch(
    [
        {"name": "R0009", "path": "temp/inputs/r0009.json"},
        {"name": "R0020", "path": "temp/inputs/r0020.json"},
    ],
    altium=r"C:\Program Files\Altium\AD24\X2.EXE",
)
```

При недоступном Altium:

```json
{
  "schema_version": "1.0",
  "status": "ERROR",
  "error": {
    "code": "ALTIUM_BLOCKED_BY_PENDING_RUN",
    "message": "Previous run is unresolved"
  },
  "summary": {"planned_runs": 12, "completed_runs": 0, "not_run": 12},
  "runs": [{"index": 0, "name": "R0009", "status": "NOT_RUN", "error": {"code": "ALTIUM_BLOCKED_BY_PENDING_RUN"}}]
}
```

Ни один тест не запускается, MixedSim не вызывается, ложные измерения не создаются.

## Проверка отчёта перед принятием

`batch_agent.run_batch()` не доверяет полю `status`. Перед принятием результата он проверяет:

- `schema_version == "1.0"`;
- наличие `results[]` и `coverage`;
- `coverage.status == "OK"`;
- `coverage.expected == coverage.completed`, `missing == 0`, `not_run == 0`;
- `len(results) == coverage.expected`;
- уникальность `result_id`;
- отсутствие `ERROR`/`NOT_RUN` при заявленном `PASS`;
- соответствие счётчиков `coverage` фактическим статусам `results[]`;
- конечное числовое `value` для `PASS`/`FAIL`/`MEASURED`.

Неполный отчёт не может дать `PASS`:

```json
{
  "status": "ERROR",
  "error": {
    "code": "REPORT_INCOMPLETE",
    "message": "Expected 14 results, collected 5"
  },
  "summary": {"planned_runs": 1, "completed_runs": 0, "not_run": 0, "errors": 1},
  "runs": [{
    "status": "ERROR",
    "error": {
      "code": "REPORT_INCOMPLETE",
      "diagnostics": {"report": {"status": "PASS", "results": []}}
    }
  }]
}
```

Структурные нарушения и несовместимая версия дают `REPORT_CONTRACT_INVALID`. Оригинальный отчёт сохраняется в `error.diagnostics.report`. Автоматическое заполнение пропущенных значений не выполняется.

## Ошибки в середине серии

Если первый или последующий тест вернул один из системных кодов:

- `BUSY`
- `TIMEOUT`
- `SCRIPT_NOT_EXECUTED`
- `ALTIUM_NOT_RUNNING`
- `ALTIUM_STATE_UNKNOWN`
- `ALTIUM_PENDING_RUN`

агент останавливает оставшуюся серию. Уже завершённые отчёты сохраняются, остальные помечаются `NOT_RUN`. `batch_agent.run_batch()` делает это автоматически.

Каждый отдельный запуск Spice Sifter всё равно берёт собственную межпроцессную блокировку: состояние Altium может измениться после preflight.

## Диагностика зависшего запуска

Вместо короткого `BUSY` возвращается структурированная ошибка:

```json
{
  "status": "ERROR",
  "error": {
    "code": "ALTIUM_PENDING_RUN",
    "message": "Previous simulation has not finished",
    "diagnostics": {
      "run_id": "a38f52",
      "run_dir": "C:\\Temp\\SpiceSifter\\a38f52",
      "altium_pid": 5678,
      "age_seconds": 120.5,
      "started_at": "2026-11-01T12:05:00Z",
      "timeout_at": "2026-11-01T12:07:00Z",
      "status_files": {
        "run_dir_exists": true,
        "started": true,
        "status_file": false,
        "status": null,
        "code": null,
        "status_source": null
      },
      "reason": "process_matches",
      "recovery_possible": false
    },
    "suggested_action": "Run: python spice_sifter.py doctor --json"
  }
}
```

## Восстановление

```powershell
python spice_sifter.py recover
python spice_sifter.py recover --confirm-legacy-stopped
```

| Состояние маркера | Действие |
|---|---|
| Предыдущий запуск завершён (`STATUS=OK/ERROR`, в том числе в `collect.tmp`/`status.tmp`) | Маркер удаляется |
| Процесс Altium завершился или PID переиспользован | Устаревший маркер удаляется |
| Старый маркер с путём, директория отсутствует | Устаревший маркер удаляется |
| Старый маркер с путём, директория есть, финального статуса нет, процессы `X2.EXE` отсутствуют | Маркер удаляется после проверки всех процессов |
| Старый маркер с путём, Altium запущен или состояние процессов неизвестно | Отказ, маркер сохраняется |
| Запуск ещё выполняется | `REFUSED`, маркер сохраняется |
| Состояние неопределённо | `UNKNOWN`, маркер сохраняется, выдаются инструкции |
| Маркер повреждён | `UNKNOWN`, диагностика, маркер сохраняется |

`--confirm-legacy-stopped` — явное подтверждение для старого маркера. Оно **не обходит** проверку процессов: если `X2.EXE` запущен или его состояние нельзя установить, восстановление отклоняется.

Успешное восстановление:

```json
{
  "schema_version": "1.0",
  "status": "RECOVERED",
  "run_id": "a38f52",
  "state": "STALE",
  "reason": "Previous Altium process has exited",
  "ready": true,
  "removed": true
}
```

Отказ при активной симуляции:

```json
{
  "schema_version": "1.0",
  "status": "REFUSED",
  "run_id": "a38f52",
  "state": "ACTIVE",
  "reason": "Previous Altium process is still running; the marker was kept",
  "ready": false,
  "suggested_action": "Wait for Altium to finish, then run recover again"
}
```

Неопределённое состояние (Altium жив, таймаут истёк, финального статуса нет):

```json
{
  "status": "UNKNOWN",
  "ready": false,
  "suggested_action": "Close or restart Altium, verify that no simulation is running, then run: python spice_sifter.py recover"
}
```

Возраст маркера сам по себе не является основанием для удаления. Небезопасного `recover --force` нет.

## Рекомендуемый порядок

1. `python spice_sifter.py doctor --json`
2. Если `RECOVERY_REQUIRED` — `python spice_sifter.py recover`, затем снова `doctor --json`.
3. Запустить серию.
4. При системной ошибке остановить серию, сохранить завершённые отчёты, остальные пометить `NOT_RUN`.
5. Читать `results[]` и `coverage`, а не вычислять ожидаемое число измерений по косвенным признакам.

Контракт отчёта: [REPORT_CONTRACT.md](REPORT_CONTRACT.md).
