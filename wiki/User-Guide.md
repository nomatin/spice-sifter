# Руководство пользователя

## Ограничения

- Поддерживаются `.TRAN`, `.DC`, `.OP` и `.AC` (DEC/OCT/LIN). Импеданс рассчитывается для комплексных данных AC; `.OP` требует явной `.PLOT OP` для сохранения выбранных сигналов. Неизвестный формат SDF и амплитудные AC-данные без комплексных составляющих отклоняются.
- Altium должен быть открыт, лицензирован и свободен от блокирующих диалогов. Утилита не запускает закрытый Altium: возвращает `ALTIUM_NOT_RUNNING`. Наличие процесса не гарантирует его отзывчивость.
- Одновременные запуски Spice Sifter запрещены. Не запускайте другую симуляцию вручную во время проверки.
- Кириллические пути моделей требуют доступных коротких имён Windows; иначе используйте путь ASCII. Вложенные относительные `.include` и кириллические директивы внутри `spice` отдельно не проверены.
- Полный текст сообщений панели MixedSim не экспортируется; при ошибках сохраняются доступные диагностические файлы.
- При тайм-ауте Altium не завершается. Незавершённый запуск блокирует следующие проверки до завершения или ручной проверки состояния.

## Инструкция

1. Откройте Altium Designer и закройте блокирующие диалоги.
2. В терминале перейдите в каталог проекта.
3. Выполните тест:

```powershell
python spice_sifter.py examples/divider.json
python spice_sifter.py examples/diode.json --keep-files
```

Путь к Altium определяется из `--altium`, затем `SPICE_SIFTER_ALTIUM`. Без них проверяется стандартный `Program Files/Altium/AD24/X2.EXE` с версией 24.4.

```powershell
python spice_sifter.py examples/divider.json --altium "C:\Program Files\Altium\AD24\X2.EXE" --timeout 120 --verbose
```

Параметры:

| Параметр | Назначение |
|---|---|
| `--altium PATH` | Путь к выбранному X2.EXE |
| `--timeout SECONDS` | Ограничение ожидания скрипта, по умолчанию 120 секунд |
| `--keep-files` | Сохранить временные файлы и добавить их путь в отчёт |
| `--verbose` | Вывести диагностику в stderr |
| `--max-runs N` | Лимит запусков Parameter Sweep, по умолчанию 100 |

### Формат теста

```json
{
  "name": "voltage_divider",
  "spice": "* Divider\nV1 in 0 DC 5\nR1 in out 10k\nR2 out 0 10k\n.TRAN 1u 1m\n.PLOT TRAN V(out)\n.END",
  "checks": [
    {"signal": "v(out)", "measurement": "last", "min": 2.49, "max": 2.51}
  ]
}
```

Обязательны `name`, полный текст `spice` с заголовком в первой строке и непустой массив `checks`. Методы измерения: `first`, `last`, `min`, `max`. Границы включительные и конечные, сигналы сопоставляются без учёта регистра. Все проверки выполняются независимо; ошибка имеет приоритет над FAIL.

Для внешних моделей добавьте `"models": ["models/diode.lib"]`. Пути разрешаются относительно JSON и подключаются через `.include`. Пример — `examples/diode.json`.

### AC и импеданс

Примеры:

```powershell
python spice_sifter.py examples/ac_resistor.json
python spice_sifter.py examples/ac_capacitor.json
python spice_sifter.py examples/ac_inductor.json
python spice_sifter.py examples/ac_rc.json
```

В `spice` задайте источник с `AC 1`, директиву `.AC` и `.PLOT AC` для напряжения и тока. Частотная сетка берётся из исходного SPICE без изменений. В примерах ток измеряется нулевым источником `VSENSE`; его имя в SDF — `i(vsense)` (регистр не важен).

AC-проверка:

```json
{
  "analysis": "ac",
  "quantity": "impedance",
  "voltage": "v(dut)",
  "current": "i(VSENSE)",
  "current_sign": 1,
  "frequency_hz": 1000,
  "frequency_sampling": "interpolate",
  "component": "imag",
  "min": -160,
  "max": -158
}
```

`component` выбирается из `real`, `imag`, `magnitude`. Формула: `Z = V / (current_sign * I)`, единица — Ом. `current_sign` обязателен и равен 1 или -1; программа не подбирает знак по ожиданиям. Порог модуля тока задаётся необязательным `min_current_a` (по умолчанию `1e-15` А).

`frequency_sampling` по умолчанию `interpolate`: отдельно линейно интерполируются комплексные V и I по частоте в герцах, затем вычисляется отношение. `exact` требует точку исходной сетки (относительный допуск сравнения `1e-10`). Экстраполяции нет. Интерполяция приближённая; вблизи резонансов нужна достаточно плотная частотная сетка. Сетки V/I должны совпадать. При нескольких подходящих Chart выбор отклоняется как неоднозначный.

Для обычных проверок DC укажите `analysis: "dc"`, сохранив `signal`, `measurement`, `min`, `max`. Проверки без `analysis` по-прежнему относятся к Transient.

### Массивы графиков

Добавьте необязательный раздел в тест:

```json
{
  "output": {
    "graphs": [
      {
        "name": "z_dut",
        "type": "impedance",
        "voltage": "v(dut)",
        "current": "i(VSENSE)",
        "current_sign": 1,
        "components": ["real", "imag", "magnitude"],
        "max_points": 100
      }
    ]
  }
}
```

Полные примеры — `examples/ac_graph_full.json` и `examples/ac_graph_limited.json`. Без `max_points` возвращаются все точки; значение должно быть целым числом не меньше 2. При сокращении сохраняются первая и последняя точки и равномерно выбираются промежуточные индексы. Такая выборка может пропускать узкие пики и резонансы. На числовые проверки сокращение не влияет.

Для одного сигнала используйте `type: "signal"`, `signal: "v(out)"`, необязательный `analysis` (`tran`, `dc`, `op`, `ac`) и `components`. У действительных сигналов допустим только `real`; у AC — `real`, `imag`, `magnitude`. Если `components` не задан, возвращается `real`. Имена графиков должны быть уникальными.

В `graphs` отчёта возвращаются `x`, выбранные массивы компонентов, `x_unit`, `y_unit`, `points_total`, `points_returned`. Все массивы синхронизированы и содержат числовые данные SDF без округления. Графики возвращаются и при FAIL. Если `output.graphs` отсутствует или пуст, поля `graphs` нет. Изображения не создаются.

### Температурные испытания

Одиночная температура задаётся в SPICE директивой `.TEMP 25` (регистр не важен; разрешены отрицательные и дробные значения). Для серии добавьте верхнеуровневое поле `"temperatures_c": [-40, 25, 85]`. В серии исходный SPICE не должен содержать `.TEMP` или `.OPTIONS TEMP`.

```powershell
python spice_sifter.py examples/temperature_single.json
python spice_sifter.py examples/temperature_series.json --keep-files
python spice_sifter.py examples/temperature_ac.json
python spice_sifter.py examples/temperature_graphs.json
```

**Особенность MixedSim 24.4:** в самостоятельном NSX прямая `.TEMP` не изменила характеристики проверенного диода. Подтверждённая альтернатива — `.OPTIONS TEMP=<значение>`. Spice Sifter принимает `.TEMP` во входе и преобразует её в `.OPTIONS TEMP` только в рабочем NSX; в отчёте указывает `temperature_method: "OPTIONS TEMP"`. Исходные тесты и модели не изменяются.

Температуры задаются в °C, должны быть конечными и выше −273,15 °C. Пустые списки, дубликаты, несколько активных `.TEMP`, несколько значений на директиву, вложенная `.TEMP` и конфликт с `.OPTIONS TEMP` отклоняются до запуска. Комментарии не считаются активными директивами. `TNOM` и локальные температурные параметры моделей не изменяются. Одинаковые результаты при разных температурах сами по себе не являются ошибкой: модель может не иметь температурной зависимости.

У проверки можно указать `temperature_c`: она применяется только к этой температуре. Без поля проверка выполняется во всех запусках серии. Это действует для обычных измерений и AC-импеданса. Ссылка на отсутствующую температуру — `INPUT_INVALID`.

Графики группируются **внутри соответствующих элементов `runs`** и содержат `temperature_c`. Необязательный фильтр графика `"temperatures_c": [-40, 85]` исключит +25 °C; без фильтра график возвращается для всей серии. `max_points` действует отдельно на каждый график. Без `output.graphs` массивов нет.

Одиночная `.TEMP` сохраняет прежнюю структуру ответа, добавляя `temperature_c`. JSON-массив температур, даже из одного элемента, возвращает `runs` в исходном порядке и `summary` с числами total/passed/failed/errors. Общий ERROR имеет приоритет над FAIL. Результаты остальных температур сохраняются при ошибке; после тайм-аута небезопасные запуски блокируются существующей защитой BUSY.

Каждая температура имеет отдельные `run_id`, NSX и SDF и собственное ограничение `--timeout`. `simulation_status` показывает успешность чтения результатов моделирования, `checks_count` — число выполненных проверок, `checks_status` — PASS/FAIL/ERROR, NOT_RUN или NOT_CONFIGURED. У температурных тестов допускается `checks: []`: PASS такого запуска подтверждает завершение моделирования, а не соответствие электрическим допускам.

Ошибки температурного входа имеют `code: "INPUT_INVALID"` и уточнение `temperature_code`: TEMP_INVALID, TEMP_CONFLICT, TEMP_CHECK_NOT_FOUND или TEMP_GRAPH_NOT_FOUND. Ошибки запуска сохраняют исходный code/message и температурную категорию TEMP_RUN_FAILED или TEMP_RESULT_MISSING. TEMP_UNSUPPORTED и TEMP_NOT_APPLIED зарезервированы для подтверждённых случаев; совпадение числовых результатов не используется как основание для них.

Без `.TEMP` и `temperatures_c` температурные поля не добавляются; предыдущие JSON-тесты работают как раньше. Для `.OP` указывайте `analysis: "op"` и `.PLOT OP V(out)` в SPICE; см. `examples/sweep_op.json`. У OP-графика ось X безразмерная (`x_unit: "1"`).

### Parameter Sweep

Добавьте `sweep.parameters` и числовые плейсхолдеры `{{NAME}}` в основной SPICE-текст:

```json
{
  "name": "divider_sweep",
  "spice": "* Divider\nV1 in 0 DC {{VIN}}\nR1 in out {{RLOAD}}\nR2 out 0 1k\n.TRAN 1u 1m\n.PLOT TRAN V(out)\n.END",
  "sweep": {
    "parameters": {
      "VIN": {"values": [2, 5, 10]},
      "RLOAD": {"values": [1000, 2000]}
    }
  },
  "checks": [
    {"signal": "v(out)", "measurement": "last", "when": {"VIN": 5, "RLOAD": 1000}, "min": 2.49, "max": 2.51}
  ]
}
```

```powershell
python spice_sifter.py examples/sweep_linear.json
python spice_sifter.py examples/sweep_divider.json --keep-files
python spice_sifter.py examples/sweep_temperature.json
python spice_sifter.py examples/sweep_ac.json
python spice_sifter.py examples/sweep_transient.json
python spice_sifter.py examples/sweep_op.json
python spice_sifter.py examples/sweep_dc.json
```

Для параметра задаётся либо `values`, либо `start`, `stop`, `step`. Например, `{"start": 0, "stop": 5, "step": 0.5}` даёт 11 значений. Допускаются отрицательные значения и отрицательный шаг. Шаг не равен нулю и направлен к stop; конечная точка включается только при попадании на сетку. Сетка строится через Decimal без накопления ошибок сложения float.

Имена чувствительны к регистру: латинская буква или подчёркивание, затем буквы, цифры и подчёркивания. Значения — только конечные числа, не строки и не bool. Пустые сетки, дубликаты, неиспользуемые параметры и неразрешённые плейсхолдеры отклоняются. Внешние модели не редактируются. Для температуры используйте прежние `temperatures_c` или постоянную `.TEMP`; температурное поле не превращается автоматически в плейсхолдер.

Перебирается декартово произведение: первый параметр меняется медленнее последнего. При совместном `temperatures_c` температура образует внешний цикл. До Altium проверяется полное число запусков с учётом температур; максимум по умолчанию — 100. Увеличить осознанно: `--max-runs 200`. При превышении возвращается `INPUT_INVALID` с рассчитанным числом, без частичного моделирования.

У проверки и запроса графика необязательное `when` ограничивает комбинации. Например, `"when": {"VIN": 5}` выбирает все значения RLOAD при VIN=5. Все ключи и значения фильтра проверяются до моделирования; близкие представления float допускаются только при однозначном совпадении с сеткой. `temperature_c` в проверках и `temperatures_c` в графиках продолжают действовать одновременно с `when`.

При наличии sweep ответ всегда содержит `runs` и `summary`, даже для одной комбинации. Каждый запуск имеет `run_id`, `parameters`, `checks_count`, `simulation_status`, `checks_status`; температура добавляется только при явном задании. Графики находятся в `runs[i].graphs` и содержат те же run_id/parameters/temperature_c. Параметры без применимых проверок разрешены, включая `checks: []`: статус NOT_CONFIGURED не подтверждает соответствие допускам.

После обычной ошибки моделирования перебор продолжается. После тайм-аута, BUSY, потери Altium или невозможности проверить его состояние новые комбинации не запускаются. Уже полученные результаты остаются в runs, summary дополнительно содержит completed (число записей runs) и not_run; total остаётся полным плановым числом. ERROR имеет приоритет над FAIL. Ограничение времени применяется отдельно к каждой комбинации.

Ошибки входа: `code: "INPUT_INVALID"` с `sweep_code`: SWEEP_INVALID, SWEEP_EMPTY, SWEEP_DUPLICATE_VALUE, SWEEP_INVALID_RANGE, SWEEP_PARAMETER_UNUSED, SWEEP_PLACEHOLDER_MISSING, SWEEP_TOO_MANY_RUNS или SWEEP_CHECK_INVALID. Ошибки выполнения сохраняют исходные code/message и уточняются как SWEEP_RUN_FAILED либо SWEEP_RESULT_MISSING.

NSX и SDF автоматически **не закрываются**; вкладки Altium остаются открытыми. Нативный Parameter Sweep, `.STEP`, zip, случайные и параллельные испытания не используются.

### Автоматические измерения

Массив `measurements` извлекает характеристики из SDF без повторного моделирования ради каждого измерения:

```json
{
  "measurements": [
    {"id": "rise", "type": "rise_time", "signal": "v(out)", "low": 0, "high": 5, "limits": {"min": 0.00218, "max": 0.00222}, "markers": true}
  ]
}
```

Это раздел основного JSON, а не отдельный входной файл. id уникален, limits необязателен; checks: [] допустим при наличии измерений. Статусы измерений — MEASURED без допусков, PASS/FAIL с допусками, ERROR при невозможности расчёта. Общий статус учитывает измерения; существующие checks/runs/summary не переписываются.

```powershell
python spice_sifter.py examples/measure_transient.json
python spice_sifter.py examples/measure_cutoff.json
python spice_sifter.py examples/measure_resonance.json
python spice_sifter.py examples/measure_mosfet.json --keep-files
```

Доступны at, crossing, mean, rms, peak_to_peak/ripple, rise_time, fall_time, delay, settling_time, cutoff_3db, resonant_frequency, capacitance_series, inductance_series, esr, sweep_crossing и rds_on. Работают when и temperature_c; семейства разных температур не смешиваются. Расчёты используют полные массивы до max_points. При запросе markers возвращаются диагностические координаты, без изображений.

Результаты находятся в верхнеуровневом measurements с run_id или scope=sweep/run_ids. Ошибка одного измерения не удаляет остальные результаты и не вызывает повторной симуляции. Отсутствие limits не подтверждает соответствие datasheet. Без measurements прежний формат ответа сохраняется.

[Полный формат, алгоритмы и ограничения](MEASUREMENTS.md) · [Результаты проверки](MEASUREMENT_VERIFICATION.md).

### Результат и ошибки

stdout содержит только один компактный JSON. Статусы и коды завершения: `PASS` — 0, `FAIL` — 1, `ERROR` — 2. Диагностика направляется в stderr. При сохранении артефактов отчёт содержит `artifacts` и `diagnostics`.

Рабочие файлы находятся в `%TEMP%/SpiceSifter/<run_id>`. По умолчанию после чтения результата они удаляются; при ошибке выполнения сохраняются. Если скрипт не начал работу до истечения времени, возвращается `SCRIPT_NOT_EXECUTED`; если начал, но не завершился — `TIMEOUT`.

Если следующий запуск возвращает `BUSY` после тайм-аута, дождитесь завершения предыдущего. Если он так и не завершился, вручную убедитесь, что симуляция в Altium больше не выполняется, затем удалите `%TEMP%/SpiceSifter/pending-run.txt`.

### Проверка установки

Автономные тесты:

```powershell
python -m unittest discover -s tests -v
```

Приёмочные проверки с открытым Altium:

```powershell
python tests/acceptance.py
python tests/acceptance_ac.py
python tests/acceptance_temperature.py
python tests/acceptance_sweep.py
python tests/acceptance_measurements.py
```

AC-ошибки: `AC_CHART_NOT_FOUND`, `AC_WAVE_NOT_FOUND`, `AC_COMPLEX_NOT_AVAILABLE`, `AC_FREQUENCY_OUT_OF_RANGE`, `AC_FREQUENCY_GRID_MISMATCH`, `IMPEDANCE_DIVISION_BY_ZERO`, `NON_FINITE_RESULT`, `GRAPH_EXPORT_ERROR`. Неоднозначный или неизвестный формат — `UNSUPPORTED_FORMAT`. NaN и Infinity не выводятся как JSON-числа.
