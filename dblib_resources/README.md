<!-- SPDX-FileCopyrightText: 2026 nomatin -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# DbLib test resources

- `template/` — preconfigured Altium Designer 24.4 project for legacy two-pin `fixture` tests (`PrjPcb`, `PrjPcbStructure`, `SchDoc`, `Simulation/Test.simcfg`). Contains current source `I1`, an `.OP` profile, and a blank schematic. A copy is created for each run; the original template is never modified in place.
- `template_empty/` — the same project with a completely empty SchDoc for `component.spice` mode. The generator creates the V/I/R/C/L fixture elements.
- `dblib_experiment.pas` — DelphiScript for legacy two-pin `fixture` tests.
- `dblib_fixture.pas` — DelphiScript for `component.spice`: dynamically reads DUT pins, places fixture elements, places Net Labels at electrical hot spots, validates connectivity/NSX, and removes created objects.

These files contain no Altium source code. A licensed installation of Altium Designer 24.4 is required. The template was created with that software and is distributed as a Spice Sifter test resource under GPL-3.0-only.
