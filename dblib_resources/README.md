<!-- SPDX-FileCopyrightText: 2026 nomatin -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# DbLib test resources

- `template/` — подготовленный проект Altium Designer 24.4 для старых двухвыводных `fixture`-тестов (`PrjPcb`, `PrjPcbStructure`, `SchDoc`, `Simulation/Test.simcfg`). Содержит источник тока `I1`, профиль `.OP` и пустую схему. Проект копируется для каждого запуска и никогда не изменяется на месте.
- `template_empty/` — тот же проект с полностью пустой SchDoc для режима `component.spice`. Оснастка V/I/R/C/L создаётся генератором.
- `dblib_experiment.pas` — DelphiScript для старых двухвыводных `fixture`-тестов.
- `dblib_fixture.pas` — DelphiScript для `component.spice`: динамически читает выводы DUT, размещает элементы оснастки, ставит Net Label на электрические hotspot, проверяет цепи/NSX и удаляет созданные объекты.

Эти файлы не содержат исходного кода Altium. Для работы требуется лицензированный Altium Designer 24.4; шаблон создан в нём и распространяется как тестовый ресурс Spice Sifter на условиях GPL-3.0-only.
