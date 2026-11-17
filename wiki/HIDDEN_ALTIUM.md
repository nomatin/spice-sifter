# Скрытый экземпляр Altium

Техническое описание изолированного запуска Altium Designer в Spice Sifter 0.6. Команды и общие параметры см. в [руководстве пользователя](User-Guide.md), порядок пакетной интеграции — в [руководстве для агентов](AGENT_INTEGRATION.md).

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

## Как это устроено

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

## Требования и ограничения

- Altium Designer **24.4.x**, Windows 10/11, интерактивный сеанс пользователя.
- Нужен свободный лицензионный слот: рабочий и скрытый Altium работают
  одновременно. Если лицензия разрешает только один экземпляр, появится
  лицензионный диалог, и сеанс завершится с `HIDDEN_BLOCKING_DIALOG` — диалоги
  не обходятся.
- Создание Desktop в заблокированном или отключённом RDP-сеансе не проверялось;
  `doctor` возвращает `HIDDEN_DESKTOP_UNAVAILABLE` с диагностикой.
- Скрытый экземпляр использует тот же профиль `%APPDATA%`/`%LOCALAPPDATA%`;
  Installed Libraries и файлы библиотек не изменяются.

## Диагностика и восстановление

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

## Что Spice Sifter никогда не делает с рабочим Altium

Не закрывает, не перезапускает, не завершает по имени процесса, не отправляет
ему скрипты, не вызывает `SwitchDesktop`, не сохраняет пользовательские
документы и не изменяет Installed Libraries.
