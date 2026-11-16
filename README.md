# Spice Sifter 0.6

[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)

Консольная утилита для автоматической проверки SPICE-моделей через Altium MixedSim. Принимает JSON с испытательной схемой или компонентом DbLib, выполняет моделирование и возвращает JSON-отчёт.

Поддерживает Transient, DC/OP, комплексный импеданс AC, температурные серии, Parameter Sweep, экспорт числовых массивов графиков и автоматическое извлечение характеристик.

Начиная с 0.6 каждый отчёт содержит `schema_version: "1.0"`, единую коллекцию `results[]` и раздел `coverage`. Это основной источник измерений для автоматических агентов; исторические `checks[]`, `measurements[]` и `runs[]` сохранены для обратной совместимости. Подробности — [контракт отчёта](wiki/REPORT_CONTRACT.md).

## Ветки

- `master` — стабильная версия для пользователей и интеграции с библиотекой Altium.
- `dev` — активная разработка, новые функции и исправления до проверки. Изменения переносятся в `master` только по отдельному согласованному решению.

## Два режима

- **Standalone SPICE** — входной JSON содержит `spice`; проверяется готовая SPICE-схема.
- **DbLib Component** — входной JSON содержит `component`; компонент автоматически размещается на подготовленном SchDoc, Altium генерирует NSX, MixedSim выполняет симуляцию, а результат проходит через тот же SDF-парсер, Validator и Measurement Engine.

Пример DbLib-запуска (`R0009`, резистор 1 Ом):

```json
{
  "name": "dblib_resistor_r0009",
  "component": {"library": "C:\\AltiumLibraries\\MY_LIB\\MY_LIB.DbLib", "page": "Резисторы", "id": "R0009"},
  "fixture": "resistor_dc",
  "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.06, "max": -0.94}]
}
```

```powershell
python spice_sifter.py examples/dblib_resistor_r0009.json
```

`component.library` должен быть **полным абсолютным путём** к существующему файлу `.DbLib`; путь в примере демонстрационный, замените его на локальный. Автоматического поиска библиотеки по имени нет. Для DbLib-режима доступны `fixture`: `resistor_dc`, `diode_forward`, `diode_reverse`. Рабочие файлы каждого запуска остаются в `temp/dblib_runs/<run_id>/`; исходный шаблон не изменяется. Ошибки очистки или сохранения очищенного SchDoc переводят результат в `ERROR`.

### Многовыводные компоненты

Вместо `fixture` можно задать `spice` и `pins`. DUT загружается только из DbLib; из `spice` размещаются элементы оснастки V/I/R/C/L, а `{{DUT}}` заменяется реальным компонентом. `pins` сопоставляет **designator вывода УГО** с именем сети; `null` означает явно неподключённый вывод.

```json
{
  "name": "dblib_mosfet_vt0001",
  "component": {
    "library": "C:\\AltiumLibraries\\MY_LIB\\MY_LIB.DbLib",
    "table": "Транзисторы",
    "id": "VT0001",
    "designator": "Q1",
    "pins": {"1": "GATE", "2": "GND", "3": "DRAIN"}
  },
  "spice": "* 2N7002 DC test\nVDD VDD 0 DC 5\nVG GATE 0 DC 3\nR1 VDD DRAIN 1k\n{{DUT}}\n.OP\n.END\n",
  "checks": [{"analysis": "op", "signal": "v(drain)", "measurement": "first", "min": 0, "max": 1}]
}
```

Поддерживаются `V`, `I`, `R`, `C`, `L`, `{{DUT}}`, а также одна директива `.OP`, `.AC` или `.TRAN`; `.OPTIONS` — только `METHOD`, `SPICEREFNODE`, `DVCC`, `DVDD`. Неизвестные элементы и директивы отклоняются с `FIXTURE_*` ошибкой до моделирования. Количество выводов определяется из загруженного компонента, а не из JSON. `component.designator` задаёт designator DUT в NSX.

Ограничения текущего этапа: `.DC` в проектом режиме Altium не выполняется и отклоняется как `FIXTURE_ANALYSIS_UNSUPPORTED`; AC-проверки импеданса требуют токовых probe и в этой оснастке пока недоступны. BJT-приёмка отмечена `BLOCKED`: в `MY_LIB` нет BJT.

## Требования

Windows 10/11, Python 3.11+ и установленный Altium Designer 24.4. Дополнительные Python-пакеты и проекты Altium не требуются.

## Быстрый запуск

Откройте Altium Designer, закройте блокирующие диалоги и выполните из каталога проекта:

```powershell
python spice_sifter.py examples/divider.json
```

При необходимости укажите путь к Altium и сохраните рабочие файлы:

```powershell
python spice_sifter.py examples/diode.json --altium "C:\Program Files\Altium\AD24\X2.EXE" --keep-files
```

Результат выводится в stdout как JSON: `PASS` (код завершения 0), `FAIL` (1) или `ERROR` (2). Диагностика направляется в stderr.

Altium должен быть запущен и доступен. Одновременные проверки и ручная симуляция во время работы утилиты не поддерживаются. Пользовательские документы не изменяются.

## Результаты и coverage

Отчёт всегда содержит `schema_version`, `results[]` и `coverage`:

```json
{
  "schema_version": "1.0",
  "status": "PASS",
  "validation_status": "ASSERTIONS",
  "coverage": {
    "status": "OK",
    "expected": 14,
    "completed": 14,
    "missing": 0,
    "passed": 9,
    "failed": 1,
    "measured": 4,
    "errors": 0,
    "not_run": 0
  },
  "results": [
    {
      "result_id": "run_001:check:0",
      "run_id": "run_001",
      "kind": "check",
      "id": 0,
      "status": "PASS",
      "value": 2.5,
      "unit": null,
      "limits": {"min": 2.4, "max": 2.6},
      "error": null
    }
  ]
}
```

Статусы: `PASS`, `FAIL`, `MEASURED`, `ERROR`, `NOT_RUN`. `MEASURED` не подтверждает соответствие datasheet, `NOT_RUN` не является `PASS`. Для `PASS`, `FAIL` и `MEASURED` значение `value` обязано быть конечным числом; `null`, `NaN`, `Infinity`, строка или boolean переводят результат в `ERROR` с кодом `RESULT_VALUE_INVALID`/`RESULT_VALUE_MISSING`. Итоговый статус: `ERROR > FAIL > PASS`. Потерянный или невыполненный результат даёт `ERROR / REPORT_INCOMPLETE`, а не ложный `PASS`. Если проверок по допускам нет, указывается `validation_status: "NO_ASSERTIONS"`.

## doctor и recover

Проверка готовности среды без запуска моделирования:

```powershell
python spice_sifter.py doctor
python spice_sifter.py doctor --json
```

Статусы: `READY`, `BUSY`, `RECOVERY_REQUIRED`, `ERROR`. Проверяются X2.EXE, версия Altium, запущенный процесс, отзывчивость, межпроцессная блокировка, `pending-run`, временная директория и ресурсы шаблонов DbLib. `READY` требует версию `24.4.x` и подтверждённую отзывчивость (`responding = true`); неизвестная версия, `responding = null` и несколько неоднозначных экземпляров не дают `READY`.

Безопасное восстановление после неудачного запуска:

```powershell
python spice_sifter.py recover
python spice_sifter.py recover --confirm-legacy-stopped
```

`recover` удаляет маркер только если предыдущий запуск доказанно завершён (финальный статус) или процесс Altium завершился / PID был переиспользован. Для старого маркера с одним путём проверяется наличие всех процессов `X2.EXE`; очистка разрешается только при достоверном отсутствии Altium. `--confirm-legacy-stopped` не обходит эту проверку. Активная симуляция не прерывается, маркер не удаляется. Возраст маркера сам по себе не является основанием для удаления.

## Пакетные испытания

Перед серией агент должен один раз вызвать `doctor --json`. Если среда не готова, ни один тест не запускается, а вся серия помечается `NOT_RUN`. При системной ошибке в середине серии (`BUSY`, `TIMEOUT`, `SCRIPT_NOT_EXECUTED`, `ALTIUM_NOT_RUNNING`, `ALTIUM_STATE_UNKNOWN`) оставшиеся тесты не запускаются, уже завершённые отчёты сохраняются. Перед принятием отдельного результата агрегатор проверяет `schema_version`, `results[]`, `coverage`, уникальность `result_id`, счётчики и отсутствие `ERROR`/`NOT_RUN` при заявленном `PASS`; неполный отчёт даёт `REPORT_INCOMPLETE`, а не `PASS`. Эталонная реализация — [`batch_agent.py`](batch_agent.py); описание — [интеграция с агентами](wiki/AGENT_INTEGRATION.md).

## Скрытый экземпляр Altium

По умолчанию Spice Sifter работает с уже открытым Altium Designer
(`--altium-instance current`). Режим `hidden` запускает **собственный** Altium на
отдельном невидимом Win32 Desktop: пользовательский экземпляр не перезапускается,
не получает скрипты и не меняет открытые документы. `SwitchDesktop`,
`SendInput`, `taskkill /IM X2.EXE` и глобальное завершение процессов не
используются.

```powershell
# Одиночный тест: Spice Sifter сам запускает и завершает скрытый Altium
python spice_sifter.py examples/dblib_resistor_r0009.json --altium-instance hidden

# Пачка: один скрытый экземпляр на всю серию, свежий при каждом вызове
python batch_agent.py tests_list\a.json tests_list\b.json `
  --altium-instance hidden --timeout 180 --ready-timeout 120
```

`--ready-timeout` (по умолчанию 120 с) ограничивает только запуск и проверку
готовности Altium; `--timeout` остаётся таймаутом одного испытания. Холодный
старт Altium 24.4 занимает 30–45 с, поэтому значения ниже 90 с не рекомендуются.

### Как это устроено

- Создаётся Desktop `SpiceSifterHidden-<id>` (`CreateDesktopW`), Altium
  запускается с `STARTUPINFO.lpDesktop` на нём.
- Экземпляр идентифицируется парой **PID + время создания**; перед каждым
  скриптом и перед завершением идентичность перепроверяется.
- Все процессы Spice Sifter входят в собственный Job Object с
  `KILL_ON_JOB_CLOSE`; завершается только он. Штатный `WM_CLOSE` не
  используется: Altium 24.4 отвечает диалогом `Confirm Save Locally`.
- Создание и настройка Job Object проверяются. Основной процесс и каждый
  `-R`-launcher создаются приостановленными (`CREATE_SUSPENDED`), добавляются в
  Job Object и только затем возобновляются. Если присоединение не удалось,
  запуск завершается с `HIDDEN_LAUNCH_FAILED`, а созданный процесс немедленно
  уничтожается; неуправляемый Altium не может стать `READY`.
- Одиночный `--altium-instance hidden` перед запуском проверяет предыдущий
  скрытый сеанс под общей блокировкой: `ACTIVE`/`UNKNOWN`/`CORRUPTED`
  запрещают старт, доказанно `STALE`-запись удаляется.
- Batch-блокировка принадлежит конкретному потоку: вложенный вызов из того же
  потока разрешён, из другого — возвращает `BUSY`. Межпроцессная блокировка
  `altium.lock` сохраняется.
- Ошибка завершения делает итоговый статус теста `ERROR` (`HIDDEN_STOP_FAILED`
  или более точный код), уже полученные результаты сохраняются, а
  `hidden-session.json` не удаляется, пока отсутствие управляемого процесса не
  подтверждено. `doctor`/`recover` обрабатывают такое состояние.
- Каждая команда `X2.EXE -RScriptingSystem:RunScriptFile(...)`, включая
  дополнительный `collect.pas`, создаётся на скрытом Desktop. Это и есть
  адресация: Altium исполняет `-R` тем экземпляром, чей Desktop указан в
  `STARTUPINFO`.
- Готовность подтверждается не окном, а контрольным DelphiScript: его документ
  появляется только на скрытом Desktop и принадлежит PID скрытого экземпляра.
- После пачки экземпляр завершается; следующий вызов создаёт новый. Постоянного
  демона и общего экземпляра между пачками нет.

### Требования и ограничения

- Altium Designer **24.4.x**, Windows 10/11, интерактивный сеанс пользователя.
- Нужен свободный лицензионный слот: рабочий и скрытый Altium работают
  одновременно. Если лицензия разрешает только один экземпляр, появится
  лицензионный диалог, и сеанс завершится с `HIDDEN_BLOCKING_DIALOG` — диалоги
  не обходятся.
- Создание Desktop в заблокированном или отключённом RDP-сеансе не проверялось;
  `doctor` возвращает `HIDDEN_DESKTOP_UNAVAILABLE` с диагностикой.
- Скрытый экземпляр использует тот же профиль `%APPDATA%`/`%LOCALAPPDATA%`;
  Installed Libraries и файлы библиотек не изменяются.

### Диагностика и восстановление

```powershell
python spice_sifter.py doctor --altium-instance hidden --json
python spice_sifter.py recover
```

`doctor` в hidden-режиме не требует запущенного Altium и не запускает его:
проверяются версия, возможность создать скрытый Desktop, временная директория,
блокировка, `pending-run`, записанный скрытый сеанс и его PID. Поля
`hidden_desktop`, `hidden_instance`, `hidden`, `license_two_instances_allowed`
добавлены к прежнему JSON без удаления существующих.

`recover` никогда не завершает процессы. Запись скрытого сеанса удаляется только
если PID **и** время создания доказывают, что процесс завершился; активный или
непроверяемый сеанс даёт `REFUSED`/`RECOVERY_REQUIRED`. При аварийном выходе
контроллера Job Object с `KILL_ON_JOB_CLOSE` завершает только собственный
Altium. Состояние сеанса хранится в
`%TEMP%\SpiceSifter\hidden-session.json`, журнал — в
`%TEMP%\SpiceSifter\hidden-sessions.log.jsonl`.

Коды ошибок hidden-режима: `HIDDEN_DESKTOP_UNAVAILABLE`,
`HIDDEN_LAUNCH_FAILED`, `HIDDEN_READY_TIMEOUT`, `HIDDEN_BLOCKING_DIALOG`,
`HIDDEN_PROCESS_LOST`, `HIDDEN_SCRIPT_NOT_ROUTED`, `HIDDEN_SCRIPT_TIMEOUT`,
`HIDDEN_STATE_AMBIGUOUS`, `HIDDEN_STOP_FAILED`. Они не маскируются общим
`SIM_NOT_RUN` и останавливают оставшиеся тесты пачки с `NOT_RUN`.

### Что Spice Sifter никогда не делает с рабочим Altium

Не закрывает, не перезапускает, не завершает по имени процесса, не отправляет
ему скрипты, не вызывает `SwitchDesktop`, не сохраняет пользовательские
документы и не изменяет Installed Libraries.


## Документация

- [Wiki — руководство и подробная документация](wiki/Home.md)
- [Контракт JSON-отчёта 1.0](wiki/REPORT_CONTRACT.md)
- [Интеграция с пакетными агентами](wiki/AGENT_INTEGRATION.md)
- [Автоматические измерения](wiki/MEASUREMENTS.md)
- [Готовые примеры тестов](examples/)
- [История изменений](CHANGELOG.md)

Тестовые скрипты находятся в `tests/`; все генерируемые файлы испытаний — в `temp/` (исключён из Git).

## License / Лицензия

Spice Sifter распространяется на условиях **GNU General Public License v3.0 only** (`GPL-3.0-only`).

- Разрешены использование, модификация и коммерческое распространение при соблюдении условий лицензии.
- При распространении модифицированной версии необходимо соблюдать GPL, включая требование предоставлять соответствующий исходный код.
- Частные и внутренние изменения не требуют обязательной публичной публикации.
- Полный текст лицензии находится в файле [LICENSE](LICENSE).
