# User Guide

## Requirements and Limitations

- Supported analyses are `.TRAN`, `.DC`, `.OP`, and `.AC` (DEC/OCT/LIN). Complex AC data are required for impedance; `.OP` requires explicit `.PLOT OP` to save selected signals. Unknown SDF formats and AC magnitude-only data without complex components are rejected.
- In default `current` mode, Altium must already be running, licensed, responsive, and free of blocking dialogs; otherwise `ALTIUM_NOT_RUNNING` or another readiness error may occur. `hidden` launches a separate instance and does not require an already-open Altium, but needs a license slot.
- Independent concurrent Spice Sifter runs are restricted by an interprocess lock. Avoid manual simulations during `current` tests. `hidden` uses a dedicated instance.
- Cyrillic model paths require working Windows short filenames; otherwise use ASCII paths. Recursive relative `.include` references and Cyrillic directives inside `spice` have not been separately verified.
- Full MixedSim panel text is not exported; available diagnostic files are preserved on errors.
- On a `current` timeout, Spice Sifter does not terminate the user's Altium. A structured `pending-run.txt` marker blocks unsafe follow-up runs until safe recovery can be proved. The hidden instance lifecycle is managed separately; see [Hidden Altium](HIDDEN_ALTIUM.md).

## Quick Start

1. For `current`, open Altium Designer and close any blocking dialogs. For `hidden`, Altium need not be open.
2. Open a terminal in the project directory.
3. Run a test:

```powershell
python spice_sifter.py examples/divider.json
python spice_sifter.py examples/diode.json --keep-files
```

The Altium executable path is chosen from `--altium`, then `SPICE_SIFTER_ALTIUM`, or the standard `Program Files/Altium/AD24/X2.EXE` path for version 24.4.

```powershell
python spice_sifter.py examples/divider.json --altium "C:\Program Files\Altium\AD24\X2.EXE" --timeout 120 --verbose
```

| Option | Description |
|---|---|
| `--altium PATH` | Path to selected X2.EXE |
| `--altium-instance {current,hidden}` | Use an open Altium (`current`, default) or start a dedicated hidden instance |
| `--ready-timeout SECONDS` | Hidden startup readiness timeout; default 120 s |
| `--timeout SECONDS` | Timeout for each test script; default 120 s |
| `--keep-files` | Keep temporary files and include artifact paths in report |
| `--verbose` | Diagnostic output on stderr |
| `--max-runs N` | Maximum sweep runs; default 100 |

### Results and Coverage

Every report contains `schema_version: "1.0"`, `results[]`, and `coverage`; `results[]` is the normalized source of measurements. Historical `checks[]`, `measurements[]`, and `runs[]` remain for compatibility.

- `PASS`: limits met; `FAIL`: limits violated.
- `MEASURED`: value obtained without limits, not an assertion of datasheet compliance.
- `ERROR`: reliable result unavailable; `NOT_RUN`: operation never executed.

PASS/FAIL/MEASURED values must be finite numbers. Missing/null/NaN/Infinity/string/boolean values become ERROR (`RESULT_VALUE_INVALID` or `RESULT_VALUE_MISSING`) and the overall status is recomputed.

`coverage.expected` counts planned check/measurement instances, not simulation runs. Invariants: `expected = completed + missing`, `completed = passed + failed + measured + errors`, and `missing = not_run`. Missing results mean `ERROR / REPORT_INCOMPLETE`; never fill them with PASS or zero. Without assertions, `validation_status: "NO_ASSERTIONS"` applies. See [Report Contract](REPORT_CONTRACT.md).

### Doctor and Recovery

```powershell
python spice_sifter.py doctor
python spice_sifter.py doctor --json
python spice_sifter.py recover
python spice_sifter.py recover --confirm-legacy-stopped
```

`doctor` performs a non-simulating readiness check of X2.EXE, version, process, responsiveness, lock, pending-run, temporary folder, and DbLib resources. Possible statuses: READY, BUSY, RECOVERY_REQUIRED, ERROR. In current mode, READY requires version 24.4.x and `responding = true`. Unknown versions, unknown responsiveness, or ambiguous instances do not count as READY.

`recover` removes a pending marker only after verifying completion or process exit. For legacy path-only markers, all X2.EXE processes must be verifiably absent. `--confirm-legacy-stopped` does not bypass this condition. Active simulations are never interrupted; marker age alone never permits deletion. See [Agent Integration](AGENT_INTEGRATION.md).

### Hidden Altium Instance

```powershell
# One test: start and stop a dedicated hidden Altium
python spice_sifter.py examples/dblib_resistor_r0009.json --altium-instance hidden

# Hidden-mode readiness check (Altium need not be running)
python spice_sifter.py doctor --altium-instance hidden --json

# Batch: one dedicated instance, fresh on the next invocation
python batch_agent.py temp/inputs/a.json temp/inputs/b.json `
  --altium-instance hidden --timeout 180 --ready-timeout 120
```

Hidden mode can coexist with the user's working Altium without closing, restarting, or sending scripts to it. A dedicated instance runs on `SpiceSifterHidden-<id>` and is managed via a Job Object. All `-R` invocations, including `collect.pas`, target that instance. Altium 24.4.x, an interactive session, and an available license slot are required. An unresolved session is diagnosed by doctor; recover only removes proven stale records. See [Hidden Altium](HIDDEN_ALTIUM.md) for details and error codes.

### DbLib Component Mode

A top-level `component` object instead of standalone `spice` selects a component from a database library, places it in a prepared SchDoc, and tests it with MixedSim.

```json
{
  "name": "dblib_resistor_r0009",
  "component": {"library": "C:\\AltiumLibraries\\MY_LIB\\MY_LIB.DbLib", "page": "Resistors", "id": "R0009"},
  "fixture": "resistor_dc",
  "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.06, "max": -0.94}]
}
```

- `library` must be the full absolute path to an existing `.DbLib`; relative/name-only paths and automatic library discovery are not supported (`LIBRARY_PATH_INVALID`). Replace the sample path with your local library.
- `page` is the Excel worksheet; its DbLib `$` suffix is added automatically.
- `id` is the original ID column value, not a Designator or MPN.
- For legacy two-pin tests, `fixture` is required: `resistor_dc` (1 A, OP), `diode_forward` (−10 mA), or `diode_reverse` (+10 mA). Reverse testing with forward limits intentionally produces FAIL.

Multi-pin components specify `spice` and `pins` instead of `fixture`:

```json
{
  "name": "dblib_mosfet_vt0001",
  "component": {
    "library": "C:\\AltiumLibraries\\MY_LIB\\MY_LIB.DbLib",
    "table": "Transistors",
    "id": "VT0001",
    "designator": "Q1",
    "pins": {"1": "GATE", "2": "GND", "3": "DRAIN"}
  },
  "spice": "* 2N7002 DC test\nVDD VDD 0 DC 5\nVG GATE 0 DC 3\nR1 VDD DRAIN 1k\n{{DUT}}\n.OP\n.END\n",
  "checks": [{"analysis": "op", "signal": "v(drain)", "measurement": "first", "min": 0, "max": 1}]
}
```

- `table` is an alias of `page`; `$` is added automatically.
- `pins` maps schematic pin designators to nets. `null` explicitly means unconnected; omitting a pin yields `COMPONENT_PIN_UNASSIGNED`. All actual pins must be accounted for.
- `spice` permits fixture V/I/R/C/L elements, exactly one `{{DUT}}`, and one `.OP`, `.AC`, or `.TRAN` directive. Supported `.OPTIONS` keys: METHOD, SPICEREFNODE, DVCC, DVDD. Unknown elements/directives yield `FIXTURE_*` before simulation.
- Pin count comes from the loaded component; list order does not imply numbering. `component.designator` sets the DUT designator in NSX.

The runner verifies SIM Implementation, Sim Port Map, component source/table/ID, pin coverage, net connectivity, NSX node order derived from the actual Sim Port Map, and freshness of the run's SDF. Checks, measurements, and graphs share the SDF parser, Validator, and Measurement Engine. DbLib sweeps and temperature series are not supported.

Project-mode `.DC` is rejected as `FIXTURE_ANALYSIS_UNSUPPORTED`; AC impedance needs current probes. Multipart symbols yield `MULTIPART_UNSUPPORTED`. BJT acceptance was `BLOCKED` in the reference library because no BJT was available.

DUTs, wires, and net labels created during a run are removed using an object journal, even on failure. Tabs and documents remain open. Success requires removing all created objects, preserving permanent fixture objects, and saving the cleaned SchDoc with `DoFileSave()`. Cleanup errors produce `ERROR / SCHEMATIC_CLEANUP_FAILED` and preserve the original simulation error in diagnostics. Work files remain in `temp/dblib_runs/<run_id>/` (report.json, status.txt, NSX, SDF); `dblib_resources/template` remains unchanged.

Duplicate charts are compared across analysis, signals, units, data types, sample counts, and complete real/complex X/Y arrays. Identical duplicates are merged; conflicting ones yield `ERROR / SDF_DUPLICATE_CONFLICT`.

Additional report fields include `component`, `run_id`, `stages`, `artifacts`, and `error`. Common codes: `LIBRARY_PATH_INVALID`, `LIBRARY_NOT_FOUND`, `PAGE_NOT_FOUND`, `COMPONENT_NOT_FOUND`, `COMPONENT_NO_PINS`, `COMPONENT_PIN_NOT_FOUND`, `COMPONENT_PIN_AMBIGUOUS`, `COMPONENT_PIN_UNASSIGNED`, `COMPONENT_PIN_CONNECTION_FAILED`, `COMPONENT_NETLIST_MISMATCH`, `COMPONENT_SIM_MODEL_MISSING`, `COMPONENT_SIM_PORT_MAP_INVALID`, `MULTIPART_UNSUPPORTED`, `FIXTURE_ELEMENT_UNSUPPORTED`, `FIXTURE_ANALYSIS_UNSUPPORTED`, `MISSING_SIMULATION_MODEL`, `SIM_PORT_MAP_MISSING`, `CONNECTIVITY_FAILED`, `NSX_NOT_GENERATED`, `NSX_INVALID`, `SDF_NOT_SAVED`, `SDF_DUPLICATE_CONFLICT`, and `SCHEMATIC_CLEANUP_FAILED`.

### Test JSON Format

```json
{
  "name": "voltage_divider",
  "spice": "* Divider\nV1 in 0 DC 5\nR1 in out 10k\nR2 out 0 10k\n.TRAN 1u 1m\n.PLOT TRAN V(out)\n.END",
  "checks": [
    {"signal": "v(out)", "measurement": "last", "min": 2.49, "max": 2.51}
  ]
}
```

`name`, a complete `spice` text (header on first line), and a nonempty `checks` array are required for basic standalone tests. Supported check reductions are `first`, `last`, `min`, and `max`. Limits are inclusive finite values; signal names are case-insensitive; checks run independently and errors take precedence over FAIL.

For external models, add `"models": ["models/diode.lib"]`. Paths resolve relative to the input JSON and are included via `.include`. See `examples/diode.json`.

### AC and Impedance

Run the example tests:

```powershell
python spice_sifter.py examples/ac_resistor.json
python spice_sifter.py examples/ac_capacitor.json
python spice_sifter.py examples/ac_inductor.json
python spice_sifter.py examples/ac_rc.json
```

Supply `AC 1` for the source, an `.AC` directive, and `.PLOT AC` for voltage and current. The original SPICE frequency grid is preserved. The examples use a zero-volt `VSENSE` source to measure current; the SDF name is `i(vsense)` (case-insensitive).

Example AC check:

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

`component` selects `real`, `imag`, or `magnitude`. `Z = V / (current_sign * I)` in ohms. `current_sign` is required (1 or −1), never inferred from expected results. Optional `min_current_a` defaults to 1e−15 A.

`frequency_sampling` defaults to `interpolate`: V and I are separately interpolated as complex values, linearly in hertz, **before** division. `exact` requires a source-grid point with 1e−10 relative comparison tolerance. No extrapolation. Interpolation is approximate and requires a sufficiently dense grid around resonances. V/I grids must match; ambiguous Charts are rejected.

Regular DC checks use `analysis: "dc"` with signal, measurement, min, and max. Checks without `analysis` continue to mean transient.

### Graph Arrays

Add an optional `output` section:

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

See `examples/ac_graph_full.json` and `examples/ac_graph_limited.json`. Without `max_points`, all points are returned; otherwise the value must be an integer of at least 2. Decimation preserves endpoints and uniformly samples intermediate indices, potentially missing narrow resonances. Numeric checks always use full-resolution data.

For individual signals use `type: "signal"`, `signal: "v(out)"`, optional `analysis` (tran/dc/op/ac), and `components`. Real signals support only `real`; complex AC supports `real`, `imag`, and `magnitude`. The default component is real. Graph names must be unique.

Report `graphs` contain `x`, selected component arrays, `x_unit`, `y_unit`, `points_total`, and `points_returned`. Arrays are synchronized, unrounded SDF numerical data. Graphs are returned even on FAIL, omitted entirely when not requested, and never rendered as images.

### Temperature Testing

A single temperature is provided using `.TEMP 25` in SPICE (negative/fractional values supported). For a series, add `"temperatures_c": [-40, 25, 85]` at top level. In series mode, the source SPICE must not already contain `.TEMP` or `.OPTIONS TEMP`.

```powershell
python spice_sifter.py examples/temperature_single.json
python spice_sifter.py examples/temperature_series.json --keep-files
python spice_sifter.py examples/temperature_ac.json
python spice_sifter.py examples/temperature_graphs.json
```

**MixedSim 24.4 behavior:** direct `.TEMP` in standalone NSX did not change the tested diode. The verified alternative is `.OPTIONS TEMP=<value>`. Spice Sifter accepts `.TEMP` as input and converts it only in the working NSX, reporting `temperature_method: "OPTIONS TEMP"`; original tests/models remain untouched.

Temperatures are finite degrees Celsius values above −273.15 °C. Empty/duplicate lists, multiple active .TEMP directives, multiple values on one directive, nested .TEMP, and conflicts with .OPTIONS TEMP are rejected before simulation. Comments are not active directives. TNOM and local model temperature settings are unchanged. Equal results at different temperatures do not necessarily indicate an error.

A check's `temperature_c` restricts it to that temperature; otherwise it runs for all temperatures. This also applies to AC impedance checks. Unknown referenced temperatures cause INPUT_INVALID. Graphs reside inside `runs[i].graphs`, carry `temperature_c`, and can be filtered with a graph-level `temperatures_c`. Each graph has its own `max_points`.

Single `.TEMP` maintains the legacy response structure with an added `temperature_c`; a JSON temperature list always generates `runs` and `summary`. The overall ERROR takes precedence over FAIL. Other temperatures' results survive a failure. Every temperature has its own run_id, NSX, SDF, and timeout.

`simulation_status` indicates whether simulation data were read successfully, `checks_count` counts performed assertions, and `checks_status` can be PASS/FAIL/ERROR/NOT_RUN/NOT_CONFIGURED. Empty `checks: []` are allowed for temperature tests; PASS alone does not mean datasheet compliance.

Invalid inputs return `INPUT_INVALID` with `temperature_code`: TEMP_INVALID, TEMP_CONFLICT, TEMP_CHECK_NOT_FOUND, TEMP_GRAPH_NOT_FOUND. Runtime errors retain code/message and use TEMP_RUN_FAILED or TEMP_RESULT_MISSING. TEMP_UNSUPPORTED and TEMP_NOT_APPLIED are reserved for confirmed problems, not merely matching numeric values. Without temperature options, legacy JSON is unchanged. OP requires `analysis: "op"` and `.PLOT OP V(out)`; see `examples/sweep_op.json`. OP graph X is dimensionless (`x_unit: "1"`).

### Parameter Sweep

Define `sweep.parameters` and numeric `{{NAME}}` placeholders in the main SPICE text:

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

Run examples:

```powershell
python spice_sifter.py examples/sweep_linear.json
python spice_sifter.py examples/sweep_divider.json --keep-files
python spice_sifter.py examples/sweep_temperature.json
python spice_sifter.py examples/sweep_ac.json
python spice_sifter.py examples/sweep_transient.json
python spice_sifter.py examples/sweep_op.json
python spice_sifter.py examples/sweep_dc.json
```

Each parameter uses either `values` or `start`/`stop`/`step`. For example `{"start":0,"stop":5,"step":0.5}` creates 11 values. Negative values and decreasing grids are supported. Step must be nonzero and point toward the stop; stop is included only if on the grid. Decimal arithmetic avoids accumulated float stepping error.

Names are case-sensitive ASCII identifiers beginning with a letter/underscore, followed by letters/digits/underscores. Values must be finite numbers, not strings or booleans. Empty/duplicate grids, unused parameters, and unresolved placeholders are rejected. External models are not modified. Temperatures use `temperatures_c` or a constant `.TEMP`, not implicit placeholders.

The Cartesian product iterates the first parameter slower than the last; temperatures form the outermost loop. Total combinations, including temperatures, are checked **before** launching Altium. Default maximum is 100; explicitly raise it with `--max-runs 200`. Exceeding the limit yields INPUT_INVALID without partial simulation.

Check and graph `when` filters select combinations, such as `{"VIN":5}`. Filter names and values are validated before simulation; float-near matches require a unique grid candidate. `temperature_c` for checks and `temperatures_c` for graphs still apply alongside `when`.

With sweep, the response always has `runs` and `summary`, even for one combination. Runs include run_id, parameters, checks_count, simulation_status, checks_status, and optional temperature. Graphs are inside `runs[i].graphs` with corresponding context. Combinations without applicable checks, including empty checks, are allowed; NOT_CONFIGURED does not imply compliance.

Ordinary per-run failures allow following runs. TIMEOUT, BUSY, lost Altium, or unknown process state stop remaining combinations. Completed runs stay available; `summary.completed` counts recorded runs, `summary.not_run` counts omitted runs, and `summary.total` remains the planned total. ERROR takes precedence over FAIL; timeout applies per combination.

Input `sweep_code` values include SWEEP_INVALID, SWEEP_EMPTY, SWEEP_DUPLICATE_VALUE, SWEEP_INVALID_RANGE, SWEEP_PARAMETER_UNUSED, SWEEP_PLACEHOLDER_MISSING, SWEEP_TOO_MANY_RUNS, and SWEEP_CHECK_INVALID. Runtime failures preserve original messages and may use SWEEP_RUN_FAILED or SWEEP_RESULT_MISSING. NSX/SDF tabs are not automatically closed. Native Altium sweep, .STEP, zip, random, and parallel simulations are not used.

### Automatic Measurements

Optional `measurements` extracts characteristics from existing SDF without additional simulations for each measurement:

```json
{
  "measurements": [
    {"id": "rise", "type": "rise_time", "signal": "v(out)", "low": 0, "high": 5, "limits": {"min": 0.00218, "max": 0.00222}, "markers": true}
  ]
}
```

This is part of the main test JSON, not a separate input. IDs are unique; limits are optional; `checks: []` is allowed with measurements. MEASURED means no limits, PASS/FAIL applies limits, and ERROR indicates calculation failure. Existing checks/runs/summary are preserved.

```powershell
python spice_sifter.py examples/measure_transient.json
python spice_sifter.py examples/measure_cutoff.json
python spice_sifter.py examples/measure_resonance.json
python spice_sifter.py examples/measure_mosfet.json --keep-files
```

Available types: at, crossing, mean, rms, peak_to_peak/ripple, rise_time, fall_time, delay, settling_time, cutoff_3db, resonant_frequency, capacitance_series, inductance_series, esr, sweep_crossing, and rds_on. `when` and `temperature_c` are supported; different temperatures never mix. Full SDF arrays are processed before `max_points`. Optional markers provide diagnostic coordinates, not images.

Top-level `measurements` results identify `run_id` or `scope=sweep`/`run_ids`. A failure in one measurement does not erase others or rerun simulation. Without limits, a measurement never asserts datasheet compliance. Without measurements, the prior response format remains. See [Measurements](MEASUREMENTS.md) and [Measurement Verification](MEASUREMENT_VERIFICATION.md).

### Results and Errors

stdout contains exactly one compact JSON. Exit codes: PASS=0, FAIL=1, ERROR=2. Diagnostics go to stderr. With preserved files, the report includes `artifacts` and `diagnostics`.

Working files are under `%TEMP%/SpiceSifter/<run_id>`. By default, successful temporary runs are cleaned after reading; execution errors preserve artifacts. If the script never starts before timeout, return SCRIPT_NOT_EXECUTED; if it started but did not finish, return TIMEOUT.

If a following call reports BUSY after a timeout, run `doctor --json` and follow the documented safe `recover` procedure. **Never manually delete an active or uncertain pending-run marker.**

### Verifying the Installation

Standalone tests:

```powershell
python -m unittest discover -s tests -v
```

Live acceptance with Altium running:

```powershell
python tests/acceptance.py
python tests/acceptance_ac.py
python tests/acceptance_temperature.py
python tests/acceptance_sweep.py
python tests/acceptance_measurements.py
```

AC error codes include AC_CHART_NOT_FOUND, AC_WAVE_NOT_FOUND, AC_COMPLEX_NOT_AVAILABLE, AC_FREQUENCY_OUT_OF_RANGE, AC_FREQUENCY_GRID_MISMATCH, IMPEDANCE_DIVISION_BY_ZERO, NON_FINITE_RESULT, and GRAPH_EXPORT_ERROR. Ambiguous/unknown formats produce UNSUPPORTED_FORMAT. NaN and Infinity are never serialized as JSON numbers.
