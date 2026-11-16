# Hidden Altium — proof of concept

Проверка возможности запускать второй, независимый экземпляр Altium Designer
на отдельном **скрытом Win32 Desktop** и адресно выполнять в нём DelphiScript и
MixedSim, не затрагивая рабочий экземпляр Altium.

Итог эксперимента: **все проверки PASS** на Altium Designer 24.4.1.13
(Windows 11 Pro, один пользовательский сеанс). Подробный отчёт —
[`REPORT.md`](REPORT.md).

> Это изолированный PoC. Он не импортируется основным кодом Spice Sifter и не
> меняет существующий механизм генерации схем, симуляции, блокировок и отчётов.

## Требования

- Windows 10/11, Altium Designer 24.4.x.
- Python 3.11+ (проверено на 3.14.5), только стандартная библиотека + `ctypes`.
- Запуск из интерактивного сеанса пользователя (тот же, где работает Altium).
- Для этапа 3/4 — существующая `.DbLib` и пример JSON с компонентом
  (по умолчанию `examples/dblib_resistor_r0009.json` и
  `C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib`).

## Состав

| Файл | Назначение |
|---|---|
| `hidden_session.py` | Win32-примитивы: `CreateDesktopW`, `CreateProcessW` + `STARTUPINFO.lpDesktop`, `EnumDesktopWindows`, `GetWindowThreadProcessId`, PID + время создания, Job Object |
| `support.py` | журнал доказательств (`evidence.jsonl`), снимки процессов `X2.EXE`, снимки профиля Altium, DelphiScript-литералы |
| `instances.py` | эталонный («рабочий») и скрытый экземпляры, ожидание готовности, отправка `-R`-скрипта, стабильный снимок окон |
| `spice_flow.py` | production-поток DbLib → NSX → MixedSim → SDF, но с запуском в выбранном экземпляре |
| `stage0_plumbing.py` | контроль Win32-plumbing на тестовом GUI-процессе без Altium |
| `stage1_launch.py` | два экземпляра `X2.EXE`, окна только на скрытом Desktop |
| `stage2_script.py` | адресная доставка DelphiScript: маршрут с рабочего и со скрытого Desktop |
| `stage3_spice.py` | реальный SPICE-тест DbLib в скрытом Altium |
| `stage4_lifecycle.py` | 3 × `START → READY → SCRIPT → SIMULATION → STOP` |
| `probe_hidden_altium.py` | диагностика одиночного скрытого запуска |
| `probe_shutdown.py` | диагностика штатного завершения |
| `probe_profile.py` | измерение изменений в `%APPDATA%`/`%LOCALAPPDATA%` Altium |
| `artifacts/` | машинные артефакты прогонов (в git не попадают) |

## Воспроизведение

Все команды выполняются из каталога `experiments/hidden_altium`.

```powershell
# 0. Проверка самих Win32-механизмов (Altium не нужен)
python stage0_plumbing.py

# 1. Скрытый Desktop + второй X2.EXE
python stage1_launch.py --timeout 240

# 2. Адресный DelphiScript (сначала рабочий Desktop, затем скрытый)
python stage2_script.py --timeout 180

# 3. Реальный DbLib SPICE-тест в скрытом экземпляре
python stage3_spice.py `
  --library "C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib" `
  --example "..\..\examples\dblib_resistor_r0009.json" `
  --timeout 300

# 4. Жизненный цикл, три цикла
python stage4_lifecycle.py --cycles 3 --timeout 300

# Диагностика (необязательно)
python probe_hidden_altium.py --seconds 120
python probe_shutdown.py --wait 8
python probe_profile.py --timeout 180
```

### Что происходит с экземплярами Altium

- Если Altium уже запущен, скрипт берёт его как **эталонный** и только читает
  его PID/время создания/окна. Такой экземпляр никогда не завершается
  экспериментом.
- Если Altium не запущен, эксперимент запускает эталонный экземпляр на обычном
  рабочем столе (он виден пользователю) и завершает его в конце — это
  собственный процесс эксперимента.
- Скрытый экземпляр всегда создаётся экспериментом и живёт в Job Object с
  `KILL_ON_JOB_CLOSE`.
- `--no-start-reference` запрещает запуск эталонного экземпляра: тогда требуется
  уже работающий Altium.

Скрытый Desktop создаётся в `winsta0` с уникальным именем
`SpiceSifterHidden-<id>`; `SwitchDesktop` не вызывается. В конце работы Desktop
закрывается, процессы эксперимента завершаются.

## Результаты

Каждый прогон пишет `artifacts/<stage>_<timestamp>/`:

- `evidence.jsonl` — пошаговый журнал (PID, время создания, окна, маршрут
  скрипта, NSX/SDF, диалоги, причины завершения);
- `result.json` — итоговые проверки;
- `altium_run/` или `cycleN/` — копии `status.txt`, `experiment.pas`, NSX, SDF.

Код возврата `0` — все проверки PASS, `1` — есть FAIL.

## Ограничения PoC

- Штатное закрытие Altium 24.4 через `WM_CLOSE` упирается в модальный диалог
  `Confirm Save Locally for (N) Modified Documents`; эксперимент фиксирует это и
  завершает **только свой** процесс через Job Object после повторной проверки
  PID + времени создания.
- Каждый скрытый экземпляр занимает лицензионный слот; на этой машине две
  одновременные сессии Altium были разрешены.
- `-R` маршрутизируется по Desktop: скрипт исполняет тот экземпляр, чей Desktop
  указан в `STARTUPINFO.lpDesktop`. Это и есть механизм адресации.
- Эксперимент не использует production-lock и production-marker; его нельзя
  запускать одновременно с обычным прогоном Spice Sifter.
- Пользовательский профиль не изменяется по настройкам, но Altium дописывает
  служебные логи в `%LOCALAPPDATA%\Altium` (см. `probe_profile.py`).
