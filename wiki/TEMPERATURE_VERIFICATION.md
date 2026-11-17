# Spice Sifter 0.3 — Temperature Testing

Historical 0.3 report. Parameter Sweep and OP support were added in 0.4; see [SWEEP_VERIFICATION.md](SWEEP_VERIFICATION.md).

## Environment and Temperature Handling

Windows, Python 3.14.5, Altium Designer 24.4.1.13. Verified October 30, 2026 using standalone NSX files through the existing DelphiScript runner, without Altium projects, Dashboard Temperature Sweep, or input emulation.

Using `.TEMP -40`, `.TEMP 25`, or `.TEMP 85` directly produced the same 0.6550993901096803 V for the test diode. Replacing those directives with `.OPTIONS TEMP=<value>` produced the expected temperature dependence. Therefore, the 24.4 backend converts external `.TEMP` directives or JSON temperature series to `.OPTIONS TEMP` in the working NSX. The report explicitly records `temperature_method=OPTIONS TEMP`. Original JSON, models, and TNOM are unchanged.

This is an observation for the tested version and model, not a rule that identical values from arbitrary models indicate failure. `TEMP_NOT_APPLIED` is not emitted solely because values match.

## Electrical Verification

Diode `D(IS=1e-14 N=1)`, 1 mA current, default TNOM 27 °C:

| Temperature, °C | Voltage, V |
|---|---|
| −40 | 0.7718678688455873 |
| +25 | 0.658645844084736 |
| +85 | 0.5508387135188227 |

Example limits use the diode equation with temperature-dependent Is: Is(T)=Is(Tnom)·(T/Tnom)^3·exp[−Eg/k·(1/T−1/Tnom)], V=kT·ln(1+I/Is(T)), Eg=1.11 eV. A ±3 mV tolerance covers physical-constant differences and simulator numerical behavior. All three checks passed.

AC resistor `R=1 kΩ`, `TC1=0.0039`, fixed `TNOM=25 °C`:

| Temperature, °C | Re(Z), Ω | Im(Z), Ω | Magnitude, Ω |
|---|---|---|---|
| −40 | 746.5 | 0 | 746.5 |
| +25 | 1000 | 0 | 1000 |
| +85 | 1234 | 0 | 1234 |

All components were tested at 1 kHz. Results agree with R(T)=1000·[1+0.0039·(T−25)]. Voltage and current are taken from the same SDF at a given temperature.

## Test Coverage

Temperature acceptance: **9/9** scenarios. Standalone tests: **43/43**, including actual diode SDFs at three temperatures. Coverage includes single and series runs, AC, full/filtered graphs, unchanged divider behavior, per-temperature ERROR/FAIL, and collecting graphs without checks.

Standalone tests cover temperature syntax, fractional values, comments, absolute-zero boundaries, NaN/Infinity, duplicates, conflicts with OPTIONS TEMP, embedded directives, unchanged TNOM, missing check/graph references, run ordering, continuation after a runner error, FAIL/ERROR statuses, graph filtering, and the absence of checks.

Live acceptance: `python tests/acceptance_temperature.py`. Its JSON report, `tests/temperature_acceptance_report.json`, remains local and is excluded from Git. Every run is checked for a unique `run_id`, corresponding OPTIONS TEMP in NSX, and links between temperature graphs and runs.

For the live "single temperature error" scenario, a missing signal is requested only at +25 °C: all temperature simulations complete, the +25 °C check reports ERROR, and subsequent results remain available. A separate standalone test mocks a runner failure and verifies that the original error message is retained and subsequent runs proceed. Altium was not intentionally hung.

## Regression

The original `tests/acceptance.py` passed **7/7**, and `tests/acceptance_ac.py` passed **11/11**, without changes. Transient/DC, previous AC models, interpolation, full/limited graphs, no graphs when not requested, FAIL with graphs, and errors were verified. Both suites exited with code 0.

## Limitations

- `.OP` was not supported in v0.2 and is not claimed for v0.3; existing Transient/DC/AC modes work.
- Global operating temperature neither overrides local model values nor creates temperature dependence inside macromodels.
- `.OPTIONS TEMP` was confirmed on AD24.4.1.13; other versions were not tested.
- After a timeout, `pending-run.txt` blocks unsafe subsequent runs. The user's process is not terminated.
- Temperature graphs appear under `runs[i].graphs` and include `temperature_c`; graphs from different temperatures are not merged.
- Input errors return `INPUT_INVALID` with `temperature_code`; runner errors retain their original code and message.
