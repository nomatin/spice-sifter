# DBUR Test 0.2

## Описание

Консольная утилита для автоматической проверки SPICE-моделей через Altium MixedSim: Transient, DC, комплексный импеданс AC и экспорт числовых массивов графиков. Принимает JSON с испытательной схемой и условиями, выполняет моделирование, читает SDF собственным парсером и возвращает один JSON-отчёт.

Требования: Windows 10/11, Python 3.11+, установленный и запущенный Altium Designer 24.4. Дополнительные Python-пакеты и проекты Altium не требуются. Пользовательские документы не изменяются.

## Ограничения

- Поддерживаются `.TRAN`, `.DC` и `.AC` (DEC/OCT/LIN). Импеданс рассчитывается для комплексных данных AC; `.OP` не поддерживается. Неизвестный формат SDF и амплитудные AC-данные без комплексных составляющих отклоняются.
- Altium должен быть открыт, лицензирован и свободен от блокирующих диалогов. Утилита не запускает закрытый Altium: возвращает `ALTIUM_NOT_RUNNING`. Наличие процесса не гарантирует его отзывчивость.
- Одновременные запуски DBUR запрещены. Не запускайте другую симуляцию вручную во время проверки.
- Кириллические пути моделей требуют доступных коротких имён Windows; иначе используйте путь ASCII. Вложенные относительные `.include` и кириллические директивы внутри `spice` отдельно не проверены.
- Полный текст сообщений панели MixedSim не экспортируется; при ошибках сохраняются доступные диагностические файлы.
- При тайм-ауте Altium не завершается. Незавершённый запуск блокирует следующие проверки до завершения или ручной проверки состояния.

## Инструкция

1. Откройте Altium Designer и закройте блокирующие диалоги.
2. В терминале перейдите в каталог проекта.
3. Выполните тест:

```powershell
python dbur_test.py examples/divider.json
python dbur_test.py examples/diode.json --keep-files
```

Путь к Altium определяется из `--altium`, затем `DBUR_ALTIUM`. Без них проверяется стандартный `Program Files/Altium/AD24/X2.EXE` с версией 24.4.

```powershell
python dbur_test.py examples/divider.json --altium "C:\Program Files\Altium\AD24\X2.EXE" --timeout 120 --verbose
```

Параметры:

| Параметр | Назначение |
|---|---|
| `--altium PATH` | Путь к выбранному X2.EXE |
| `--timeout SECONDS` | Ограничение ожидания скрипта, по умолчанию 120 секунд |
| `--keep-files` | Сохранить временные файлы и добавить их путь в отчёт |
| `--verbose` | Вывести диагностику в stderr |

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
python dbur_test.py examples/ac_resistor.json
python dbur_test.py examples/ac_capacitor.json
python dbur_test.py examples/ac_inductor.json
python dbur_test.py examples/ac_rc.json
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

Для одного сигнала используйте `type: "signal"`, `signal: "v(out)"`, необязательный `analysis` (`tran`, `dc`, `ac`) и `components`. У действительных сигналов допустим только `real`; у AC — `real`, `imag`, `magnitude`. Если `components` не задан, возвращается `real`. Имена графиков должны быть уникальными.

В `graphs` отчёта возвращаются `x`, выбранные массивы компонентов, `x_unit`, `y_unit`, `points_total`, `points_returned`. Все массивы синхронизированы и содержат числовые данные SDF без округления. Графики возвращаются и при FAIL. Если `output.graphs` отсутствует или пуст, поля `graphs` нет. Изображения не создаются.

### Результат и ошибки

stdout содержит только один компактный JSON. Статусы и коды завершения: `PASS` — 0, `FAIL` — 1, `ERROR` — 2. Диагностика направляется в stderr. При сохранении артефактов отчёт содержит `artifacts` и `diagnostics`.

Рабочие файлы находятся в `%TEMP%/DBUR-Test/<run_id>`. По умолчанию после чтения результата они удаляются; при ошибке выполнения сохраняются. Если скрипт не начал работу до истечения времени, возвращается `SCRIPT_NOT_EXECUTED`; если начал, но не завершился — `TIMEOUT`.

Если следующий запуск возвращает `BUSY` после тайм-аута, дождитесь завершения предыдущего. Если он так и не завершился, вручную убедитесь, что симуляция в Altium больше не выполняется, затем удалите `%TEMP%/DBUR-Test/pending-run.txt`.

### Проверка установки

Автономные тесты:

```powershell
python -m unittest discover -s tests -v
```

Приёмочные проверки с открытым Altium:

```powershell
python tests/acceptance.py
python tests/acceptance_ac.py
```

AC-ошибки: `AC_CHART_NOT_FOUND`, `AC_WAVE_NOT_FOUND`, `AC_COMPLEX_NOT_AVAILABLE`, `AC_FREQUENCY_OUT_OF_RANGE`, `AC_FREQUENCY_GRID_MISMATCH`, `IMPEDANCE_DIVISION_BY_ZERO`, `NON_FINITE_RESULT`, `GRAPH_EXPORT_ERROR`. Неоднозначный или неизвестный формат — `UNSUPPORTED_FORMAT`. NaN и Infinity не выводятся как JSON-числа.

Сведения о реализации и результатах испытаний вынесены в [DEVELOPMENT.md](DEVELOPMENT.md), [VERIFICATION.md](VERIFICATION.md) и [AC_VERIFICATION.md](AC_VERIFICATION.md).
