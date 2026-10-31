<!-- SPDX-FileCopyrightText: 2026 nomatin -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# DbLib test resources

- `template/` — подготовленный проект Altium Designer 24.4 (`PrjPcb`, `PrjPcbStructure`, `SchDoc`, `Simulation/Test.simcfg`). Содержит только испытательную оснастку Spice Sifter: источник тока `I1`, профиль `.OP` и пустую схему. Проект копируется для каждого запуска и никогда не изменяется на месте.
- `dblib_experiment.pas` — DelphiScript, который размещает компонент DbLib, создаёт соединения, компилирует SchDoc, запускает MixedSim и удаляет созданные объекты.

Эти файлы не содержат исходного кода Altium. Для работы требуется лицензированный Altium Designer 24.4; шаблон создан в нём и распространяется как тестовый ресурс Spice Sifter на условиях GPL-3.0-only.
