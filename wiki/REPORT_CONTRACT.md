# Контракт JSON-отчёта Spice Sifter 1.0

Начиная с версии 0.6 каждый отчёт Spice Sifter содержит стабильную нормализованную коллекцию `results[]`. Это основной источник измерений для внешних агентов. Исторические `checks[]`, `measurements[]` и `runs[]` сохранены для обратной совместимости.

Машиночитаемая схема: [`schemas/report.schema.json`](../schemas/report.schema.json).

## Верхний уровень

```json
{
  "schema_version": "1.0",
  "status": "PASS",
  "validation_status": "ASSERTIONS",
  "test": "voltage_divider",
  "results": [],
  "coverage": {},
  "checks": [],
  "measurements": [],
  "runs": [],
  "error": null
}
```

`schema_version` описывает контракт отчёта, а не версию Spice Sifter.

## results[]

Каждый элемент описывает один обязательный экземпляр проверки или измерения:

```json
{
  "result_id": "run_001:check:0",
  "run_id": "run_001",
  "kind": "check",
  "id": 0,
  "status": "PASS",
  "value": 2.5,
  "unit": "V",
  "limits": {"min": 2.4, "max": 2.6},
  "parameters": {"VIN": 5},
  "temperature_c": 25,
  "error": null
}
```

Обязательные поля:

| Поле | Назначение |
|---|---|
| `result_id` | Уникальный идентификатор результата в отчёте |
| `run_id` | Запуск или `null` для семейного sweep-измерения |
| `kind` | `check` или `measurement` |
| `id` | Идентификатор исходной проверки/измерения; для проверки без `id` — индекс исходного массива |
| `status` | `PASS`, `FAIL`, `MEASURED`, `ERROR` или `NOT_RUN` |
| `value` | Число или `null` |
| `unit` | Единица или `null` |
| `error` | `{code, message}` или `null` |

Необязательные поля: `limits`, `parameters`, `temperature_c`, `frequency_hz`, `signal`, `component`, `scope`, `run_ids`, `markers`.

Для измерения, объединяющего семейство запусков Parameter Sweep, задаётся `scope: "sweep"`, `run_id: null` и `run_ids`. Промежуточные точки sweep не становятся отдельными результатами.

## Статусы

| Статус | Значение |
|---|---|
| `PASS` | Допуски заданы и пройдены |
| `FAIL` | Допуски заданы и нарушены |
| `MEASURED` | Значение получено, допуски не заданы; это **не** подтверждение datasheet |
| `ERROR` | Достоверный результат получить нельзя |
| `NOT_RUN` | Проверка/измерение не выполнялись; это **не** `PASS` |

Для `PASS`, `FAIL` и `MEASURED` поле `value` обязано быть конечным числом:

| Исходное значение | Результат |
|---|---|
| поле отсутствует | `ERROR / RESULT_VALUE_MISSING` |
| `null` | `ERROR / RESULT_VALUE_MISSING` |
| `NaN`, `Infinity`, `-Infinity` | `ERROR / RESULT_VALUE_INVALID` |
| строка или boolean | `ERROR / RESULT_VALUE_INVALID` |
| конечное число, включая `0` | статус сохраняется, значение не меняется |

Пример недопустимого значения:

```json
{
  "status": "ERROR",
  "value": null,
  "error": {
    "code": "RESULT_VALUE_INVALID",
    "message": "Result has no valid finite numeric value"
  }
}
```

`NaN` и `Infinity` никогда не попадают в финальный JSON. Для `ERROR` и `NOT_RUN` значение `null` допустимо. Итоговый статус отчёта пересчитывается после этой проверки.

## coverage

```json
{
  "coverage": {
    "status": "OK",
    "expected": 14,
    "completed": 13,
    "missing": 1,
    "passed": 9,
    "failed": 1,
    "measured": 3,
    "errors": 0,
    "not_run": 1
  }
}
```

`expected` — число запланированных экземпляров проверок и измерений, а не число SPICE-запусков. Одна проверка на пяти температурах создаёт пять ожидаемых результатов.

Инварианты:

```text
expected  = completed + missing
completed = passed + failed + measured + errors
missing   = not_run
len(results[]) = expected        # если планирование успешно
```

Если план вычислить невозможно, возвращается `coverage.status = "UNKNOWN"`, `expected = null`, а диагностика находится в `coverage.diagnostics`.

## Итоговый статус

Приоритет: `ERROR > FAIL > PASS`.

`ERROR`, если:

- любой обязательный результат имеет `ERROR` или `NOT_RUN`;
- покрытие неполное;
- есть дубликаты `result_id` или незапланированные результаты;
- произошла ошибка запуска/моделирования;
- невозможно достоверно сформировать отчёт.

`FAIL`, если все обязательные результаты обработаны, ошибок нет, но хотя бы одна проверка не прошла допуски.

`PASS`, если все запланированные действия завершены, ошибок и нарушений допусков нет.

Если проверок по допускам нет:

```json
{"validation_status": "NO_ASSERTIONS"}
```

Успешная симуляция без проверок может иметь `status: "PASS"`, но не является подтверждённым соответствием характеристик.

## Примеры

### Обычный PASS

```json
{
  "schema_version": "1.0",
  "status": "PASS",
  "validation_status": "ASSERTIONS",
  "coverage": {"status": "OK", "expected": 1, "completed": 1, "missing": 0,
               "passed": 1, "failed": 0, "measured": 0, "errors": 0, "not_run": 0},
  "results": [{"result_id": "run_001:check:0", "run_id": "run_001", "kind": "check",
               "id": 0, "status": "PASS", "value": 2.5, "unit": null,
               "limits": {"min": 2.4, "max": 2.6}, "error": null}],
  "error": null
}
```

### FAIL по допускам

```json
{
  "status": "FAIL",
  "coverage": {"status": "OK", "expected": 1, "completed": 1, "missing": 0,
               "passed": 0, "failed": 1, "measured": 0, "errors": 0, "not_run": 0},
  "results": [{"result_id": "run_001:check:0", "status": "FAIL",
               "value": 3.1, "limits": {"min": 2.4, "max": 2.6}, "error": null}]
}
```

### ERROR из-за потерянного результата

```json
{
  "status": "ERROR",
  "error": {"code": "REPORT_INCOMPLETE", "message": "Expected 14 results, collected 13"},
  "coverage": {"status": "OK", "expected": 14, "completed": 13, "missing": 1,
               "passed": 9, "failed": 1, "measured": 3, "errors": 0, "not_run": 1}
}
```

Потерянный результат присутствует в `results[]` со статусом `NOT_RUN`. Подстановка `PASS`, `0` или последнего значения запрещена.

### Дубликат result_id

```json
{
  "status": "ERROR",
  "error": {"code": "REPORT_DUPLICATE_RESULT", "message": "Duplicate result_id values: ..."},
  "coverage": {"diagnostics": [{"code": "REPORT_DUPLICATE_RESULT"}]}
}
```

### Семейное sweep-измерение

```json
{
  "result_id": "sweep:vgs_threshold:2",
  "run_id": null,
  "kind": "measurement",
  "id": "vgs_threshold",
  "scope": "sweep",
  "run_ids": ["run_005", "run_006", "run_007", "run_008"],
  "status": "MEASURED",
  "value": 2.15,
  "unit": "V",
  "error": null
}
```

## Где выполняется нормализация

Единственный нормализатор — `report_model.finalize_report()`. Он вызывается после `finish_measurements()` и агрегации Temperature/Sweep, поэтому одно измерение не попадает в `results[]` дважды. Отдельных нормализаторов для Standalone, Temperature, Sweep и DbLib нет.

## Совместимость

- `checks[]`, `measurements[]`, `runs[]` сохраняются.
- Входные JSON прежних версий поддерживаются.
- Коды завершения: `PASS=0`, `FAIL=1`, `ERROR=2`.
- Новые агенты должны читать `results[]` и `coverage`; старые могут продолжать читать исторические массивы.
