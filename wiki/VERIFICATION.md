# Spice Sifter 0.1 Verification — Historical Report

For more recent results, see [TEMPERATURE_VERIFICATION.md](TEMPERATURE_VERIFICATION.md). The limitations and results below apply to MVP 1.

**Date:** October 30, 2026. **Environment:** Windows, Altium Designer 24.4.1.13, Python 3.14.5.

Tested workflow: already-running Altium, one input JSON, one `spice_sifter.py` command, automated MixedSim simulation, SDF export, a custom binary parser, and numerical validation. No Altium project, component placement, input emulation, or external SPICE engine was required. The running-Altium check was introduced after the original requirement and superseded the initial cold-start capability.

## Verified Results

| Scenario | Result |
|---|---|
| 5 V / 10 kΩ / 10 kΩ divider | PASS, 2.5 V |
| Incorrect 3.0–3.2 V tolerance | FAIL, exit code 1 |
| Missing signal | ERROR / SIGNAL_NOT_FOUND, exit code 2 |
| JSON without `spice` | ERROR / INPUT_INVALID before launch |
| Repeated run | PASS, separate `run_id` and SDF |
| Missing model | ERROR / SIM_NOT_RUN with artifacts |
| External model with Cyrillic characters and spaces in path | PASS, 0.6928681429973097 V |
| Altium not running | Unit test: ALTIUM_NOT_RUNNING; simulator not invoked |
| Unresponsive script | Unit test: bounded timeout, then BUSY; process not terminated |

Actual reports from seven runs: `tests/acceptance_report.json`. All seven acceptance scenarios passed; unit tests: **18/18**. A real divider SDF from the target version is included. The parser was additionally checked with a local real SDF containing two Charts and 457 Waves; its exact source version was unknown, so it was not used to establish 24.4 compatibility.

## Version Limitations

- Only `.TRAN` and real-valued signals were supported. `.OP` was not claimed without separate acceptance testing. Complex data were explicitly rejected, though the SDFWave architecture allowed later extensions.
- Full MixedSim panel messages were not exported. Available diagnostics included the command, source netlist, generated scripts, status, and artifact list. This is a known diagnostic limitation, not proof that MixedSim produced no errors.
- Tests for an absent Altium process and an unresponsive script used mocks for process invocation; the user's Altium was neither closed nor deliberately hung.
- Cyrillic model paths were tested using Windows short names. If short names are disabled, ASCII paths are required. Recursive relative includes inside models were not claimed.
- After a timeout, an unresolved run blocks subsequent runs. If no status appears, Altium must be inspected manually before deleting `pending-run.txt`.

New Altium APIs are not considered functional without tests. Successful reports do not hide the listed limitations.

This is the historical MVP 1 report. AC results and the 0.2 regression are documented in [AC_VERIFICATION.md](AC_VERIFICATION.md).
