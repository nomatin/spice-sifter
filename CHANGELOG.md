# История изменений

## 0.6 — Reliable Execution & Report Contract

- Добавлен `schema_version: "1.0"` во все отчёты.
- Добавлена единая коллекция `results[]` для Standalone SPICE, DbLib Component, Temperature Testing, Parameter Sweep, Automatic Measurements и частично выполненных/ошибочных испытаний.
- Добавлен раздел `coverage` с инвариантами `expected = completed + missing`, `completed = passed + failed + measured + errors`, `missing = not_run`.
- Планировщик ожидаемых результатов учитывает `checks`, `measurements`, `temperatures_c`, `sweep.parameters`, фильтры `when` и `temperature_c`. Семейные sweep-измерения дают один результат на температурную группу и имеют `scope: "sweep"` + `run_ids`.
- Потерянный или невыполненный результат даёт `ERROR / REPORT_INCOMPLETE`, дубликат `result_id` — диагностическую ошибку. Ложный `PASS` исключён.
- Добавлены статусы `MEASURED` и `NOT_RUN`; при отсутствии допусков возвращается `validation_status: "NO_ASSERTIONS"`.
- `pending-run.txt` заменён на атомарный структурированный JSON-маркер (run_id, run_dir, PID и время создания Altium, runner_pid, started_at, timeout_at, state). Старый формат с путём поддерживается.
- Добавлено безопасное самовосстановление: маркер удаляется только при доказанном завершении запуска или процесса Altium; активная/неопределённая симуляция не прерывается. Возраст маркера не является основанием для удаления.
- Добавлены команды `python spice_sifter.py doctor [--json]` и `python spice_sifter.py recover`; прежний CLI не изменён.
- Добавлена эталонная интеграция пакетного агента `batch_agent.py`: один preflight, остановка серии при системной ошибке, `NOT_RUN` для незапущенных испытаний.
- Устранены гонки чтения `collect.tmp`/`status.txt`: временная блокировка файла и `SAVE_PENDING` с готовым `collect.tmp` больше не приводят к ложному тайм-ауту.
- Добавлены `schemas/report.schema.json`, `report_model.py`, `runtime_state.py`, автотесты контракта, pending-run, doctor/recover и batch preflight.
- Документация: [REPORT_CONTRACT.md](wiki/REPORT_CONTRACT.md), [AGENT_INTEGRATION.md](wiki/AGENT_INTEGRATION.md), обновлены README, User-Guide, DEVELOPMENT и Home.

Проверка на Altium Designer 24.4.1.13: 174 автотеста, Standalone 7/7, Parameter Sweep 11/11, Temperature 9/9, Automatic Measurements 7/7, AC 11/11, DbLib 13/13, multipin DbLib — все доступные кейсы OK (BJT ожидаемо `BLOCKED`). 65 реальных отчётов v1.0 прошли JSON Schema и проверку инвариантов coverage.

## Невыпущенные изменения

- Добавлен режим **DbLib Component**: компонент из установленной DbLib автоматически размещается на подготовленном SchDoc, подключается Wire/Net Label, компилируется, Altium формирует NSX, MixedSim создаёт SDF, а результат проходит через существующие Validator/Measurements/Graph Export.
- Добавлены `dblib_runner.py`, ресурсы `dblib_resources/` (шаблон проекта и DelphiScript), примеры `examples/dblib_*`, автономные тесты и реальная приёмка `tests/acceptance_dblib.py`.
- Очистка схемы использует журнал созданных объектов и выполняется при PASS/FAIL/ERROR; документы Altium не закрываются. Рабочие файлы остаются в `temp/dblib_runs/<run_id>/`.
- Ужесточена проверка DbLib: `component.library` принимает только полный абсолютный путь к существующему `.DbLib`; поиск по имени удалён.
- Очистка считается успешной только после фактического удаления объектов и успешного сохранения SchDoc; ошибки дают `STATUS=ERROR` и не маскируют исходную причину.
- Дублирующиеся графики SDF сравниваются полностью по X/Y, единицам и комплексным значениям; различающиеся данные дают `SDF_DUPLICATE_CONFLICT`.
- Приёмочные входы и отчёты перенесены в `temp/dblib_acceptance/<acceptance_id>/`; путь к библиотеке задаётся через `SPICE_SIFTER_DBLIB_PATH`.
- Добавлена лицензия GNU GPL-3.0-only: файл `LICENSE`, SPDX-заголовки в собственных Python-файлах и раздел «License / Лицензия» в README.
- Исправлен расчёт RMS: вместо трапеций по квадратам отсчётов используется точное аналитическое интегрирование квадрата кусочно-линейно интерполированного сигнала. Добавлены математические тесты RMS.
- DbLib Component расширен на многовыводные компоненты: `component.spice` + `component.pins` + `{{DUT}}`, динамическое чтение выводов, произвольные designators и `null` для явно неподключённых выводов.
- Добавлен минимальный генератор SchDoc из `spice` для V/I/R/C/L и одна директива `.OP`/`.AC`/`.TRAN`; `dblib_resources/template_empty` и `dblib_resources/dblib_fixture.pas`.
- Добавлены проверки: `COMPONENT_NOT_FOUND`, `COMPONENT_NO_PINS`, `COMPONENT_PIN_NOT_FOUND`, `COMPONENT_PIN_AMBIGUOUS`, `COMPONENT_PIN_UNASSIGNED`, `COMPONENT_PIN_CONNECTION_FAILED`, `COMPONENT_NETLIST_MISMATCH`, `COMPONENT_SIM_MODEL_MISSING`, `COMPONENT_SIM_PORT_MAP_INVALID`, `MULTIPART_UNSUPPORTED`.
- Pin Mapping проверяется по реальному Sim Port Map и порядку узлов в Altium NSX, а не по порядку перечисления выводов.
- Добавлена multi-pin приёмка `tests/acceptance_dblib_multipin.py` (3-выводный MOSFET, 6-выводная ИС, null/shared/bad/missing pin, unsupported spice). BJT — `BLOCKED`: в библиотеке нет BJT.
- Ограничения: `.DC` в проектом режиме отклоняется как `FIXTURE_ANALYSIS_UNSUPPORTED`; AC impedance требует токовых probe; многосекционные УГО отклоняются.

## Переименование проекта

- Проект переименован в Spice Sifter: `dbur_test.py` → `spice_sifter.py`, `DBUR_ALTIUM` → `SPICE_SIFTER_ALTIUM`, `%TEMP%/DBUR-Test` → `%TEMP%/SpiceSifter`.
- Функциональность версии 0.5 не изменена; обновлены документация, примеры и тесты.

## 0.5 — Automatic Measurements

- Добавлен Measurement Engine над полными данными SDF, без повторного моделирования ради каждого измерения.
- Добавлены реальные оконные операции, фронты, задержка, установление, AC-срез, минимум импеданса, эквивалентные C/L/ESR.
- Добавлены sweep_crossing для VGS(th), статический rds_on, when/temperature_c и раздельные температурные семейства.
- Допуски используют прежний Validator; результаты различают MEASURED/PASS/FAIL/ERROR и поддерживают диагностические markers.
- Добавлены примеры, математические тесты, реальные SDF и приёмочный сценарий. Подробности — [Wiki: измерения](wiki/MEASUREMENT_VERIFICATION.md).

Проверка: 79 автотестов, 7 новых реальных измерительных сценариев и 38 прежних сценариев регрессии на Altium Designer 24.4.1.13.

## 0.4 — Parameter Sweep

- Добавлены values, Decimal start/stop/step и декартово произведение параметров с температурами.
- Добавлены безопасные числовые плейсхолдеры {{NAME}}, фильтры when и лимит --max-runs (по умолчанию 100).
- Добавлены метаданные parameters/run_id, независимые проверки и экспорт графиков каждой комбинации.
- После тайм-аута или потери отзывчивого Altium дальнейшие комбинации не запускаются; результаты сохраняются.
- Подтверждён самостоятельный OP с .PLOT OP; добавлены analysis=op, пример и реальный SDF.
- Автоматическое закрытие документов не добавлялось.

Проверка: 58 автотестов, 11 приёмочных сценариев Parameter Sweep и 27 прежних сценариев регрессии. Подробности — в [SWEEP_VERIFICATION.md](wiki/SWEEP_VERIFICATION.md).

## 0.3 — Temperature Testing

- Добавлены одиночная `.TEMP` и последовательные независимые серии `temperatures_c`.
- Применение температуры подтверждено в MixedSim 24.4 через `.OPTIONS TEMP`; входная `.TEMP` преобразуется в рабочем NSX.
- Добавлены привязка проверок к температуре, фильтры графиков, run_id и агрегированные runs/summary.
- Разделены успешность моделирования и состояние проверок; разрешён сбор температурных графиков без проверок.
- Ошибки одной температуры сохраняются отдельно; исходная диагностика не заменяется общим сообщением.
- Добавлены примеры, реальные температурные SDF, 12 новых автономных тестов и приёмочный сценарий температурных испытаний.

Подтверждены 43 автономных теста и 9 реальных температурных сценариев на AD24.4.1.13. Подробности — в [TEMPERATURE_VERIFICATION.md](wiki/TEMPERATURE_VERIFICATION.md).

## 0.2 — AC Analysis & Graph Export

- Добавлены AC DEC/OCT/LIN и чтение комплексных SDF из Altium 24.4.
- Добавлены проверки Re(Z), Im(Z), |Z|, явное направление тока и порог min_current_a.
- Добавлены выбор точной частоты и интерполяция комплексных V/I без экстраполяции.
- Добавлен output.graphs для сигналов Transient/DC/AC и импеданса: полные массивы или выборка с сохранением крайних точек.
- Добавлены обычные DC-проверки, AC-примеры, реальный SDF-образец, автоматические тесты и приёмочный сценарий.
- Сохранены CLI, старый формат проверок, статусы и коды завершения.

Проверка: 31 автономный тест, 11 реальных AC-сценариев и 7 реальных сценариев регрессии на Altium Designer 24.4.1.13. Подробности — в [AC_VERIFICATION.md](wiki/AC_VERIFICATION.md).

## 0.1 — MVP

- Реализованы подготовка NSX, временный DelphiScript, запуск MixedSim и собственный SDF-парсер.
- Добавлены измерения first/last/min/max, JSON-отчёт, тайм-аут, изоляция запусков и проверка открытого Altium.
- Добавлены примеры делителя и диода с внешней моделью.
