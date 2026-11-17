# Spice Sifter

[![License: GPL-3.0-only](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](LICENSE)

**Automated SPICE model testing for Altium Designer.**

Spice Sifter is a Python CLI tool that automates SPICE simulations using Altium Designer MixedSim. It validates simulation results against expected limits and generates structured JSON reports.

Designed for electronic component library verification, automated testing, and integration with development agents.

## Features

- **Standalone SPICE testing** — simulate circuits defined in JSON test cases.
- **DbLib component testing** — load and validate components directly from Altium database libraries.
- **Simulation analysis** — transient, operating point, DC, and AC analysis.
- **Advanced measurements** — impedance, RMS, timing, frequency response, and other electrical characteristics.
- **Parameter and temperature sweeps** — test circuits under different operating conditions.
- **Machine-readable reports** — structured results, validation statuses, and test coverage.
- **Altium isolation** — optionally use a dedicated hidden Altium instance without interfering with the working session.

Feature availability and limitations vary by testing mode.

## Requirements

Windows 10/11, Python 3.11+, and Altium Designer 24.4.x.

No additional Python packages or external SPICE engines are required.

## Quick Start

With Altium Designer running:

```powershell
python spice_sifter.py examples/divider.json
```

Check the environment before testing:

```powershell
python spice_sifter.py doctor
```

Results are returned as JSON through stdout, with exit codes `0` (PASS), `1` (FAIL), or `2` (ERROR).

For isolated simulations, batch execution, and DbLib testing, see the [User Guide](wiki/User-Guide.md).

## Documentation

**[Documentation Home](wiki/Home.md)** — Complete documentation and navigation.

**[User Guide](wiki/User-Guide.md)** — Configuration, test formats, supported analyses, and usage examples.

**[JSON Report Contract](wiki/REPORT_CONTRACT.md)** — Report schema, results, statuses, and coverage.

**[Agent Integration](wiki/AGENT_INTEGRATION.md)** — Automated testing workflows, preflight, and recovery.

**[Hidden Altium Instance](wiki/HIDDEN_ALTIUM.md)** — Isolated Altium session behavior, limitations, and diagnostics.

**[Development](wiki/DEVELOPMENT.md)** — Architecture, testing, and development guidelines.

**[Changelog](CHANGELOG.md)** — Version history and changes.

## License

Licensed under [GNU GPL v3.0 only](LICENSE).
