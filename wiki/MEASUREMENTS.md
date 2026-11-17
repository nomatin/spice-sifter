# Automatic Measurements — Formats and Algorithms (v0.5)

## Input and Output

The optional `measurements` array contains predefined measurement types; Python expressions in JSON are **never executed**. Each request specifies a unique `id`, `type`, and type-specific fields. `analysis` supports `tran`, `dc`, `op`, and `ac`. Real-waveform measurements default to transient, `rds_on` and `sweep_crossing` to OP, and AC measurement types automatically select AC.

Common fields: `limits` with inclusive finite `min`/`max`, `when`, `temperature_c`, `window: {start, stop}`, and `markers: true`. Without limits, a value is measured but not validated against datasheet specifications. The existing Validator evaluates limits.

Top-level `measurements` results include value, unit, status, id, type, run/parameter/temperature context, limits, and optional markers. A measurement without limits is `MEASURED`; one with limits is `PASS` or `FAIL`; computation failures are `ERROR`. Overall ERROR takes precedence over FAIL. Existing `checks`, `runs`, and `summary` retain their legacy meanings; measurements additionally affect the overall status. When no measurements are requested, the field is omitted.

An empty `checks: []` is accepted when measurements exist. Invalid inputs are detected before Altium when possible. A single measurement error neither aborts independent measurements nor discards graphs or reruns simulation. Measurements use full-resolution SDF before optional graph reduction with `max_points`.

## Real-valued Waveforms

| type | Additional fields | Calculation |
|---|---|---|
| `at` | signal, x | Linearly interpolated value |
| `crossing` | signal, level, edge, occurrence | Crossing coordinate |
| `mean` | signal | Integral divided by interval duration |
| `rms` | signal | Exact integral of squared piecewise-linear waveform, divided by interval, then square root |
| `peak_to_peak` | signal | max−min |
| `ripple` | signal, window | Peak-to-peak in a specified settled window |
| `rise_time` | signal, low, high; from_percent=10, to_percent=90 | Rise duration |
| `fall_time` | signal, low, high; from_percent=90, to_percent=10 | Fall duration |
| `delay` | trigger, target, occurrence | Time between events |
| `settling_time` | signal, target_value, start, stop; tolerance_abs or tolerance_rel | Settling time from start |

`window` restricts the X range (seconds for transient). Boundaries are interpolated, never extrapolated. Mean integrates using trapezoids on actual X coordinates. RMS integrates the *square* of the piecewise-linear interpolant analytically, rather than trapezoid-integrating squared samples. At least two distinct samples are required except for `at` and a single-point OP ratio.

The X axis must increase. Duplicate X with equal Y is deduplicated; duplicate X with conflicting Y yields `INVALID_AXIS`. The resulting non-decreasing axis must provide enough samples. NaN and Infinity are rejected. For DC, the X unit comes from the independent wave, or must be supplied as `x_unit` if ambiguous.

### RMS

Between adjacent SDF samples, the signal is assumed linear. The exact integral over `[x_i, x_(i+1)]` is:

`I_i = (x_(i+1) − x_i)(y_i² + y_i·y_(i+1) + y_(i+1)²)/3`

The result is `sqrt(ΣI_i / (x_N − x_0))`. Calculations use the full SDF, `math.fsum`, and O(N) complexity. Window edges use interpolation, without extrapolation. This is exact for the *interpolated* waveform but cannot recover fast changes absent from sampled SDF; reduce the maximum `.TRAN` timestep for better physical accuracy.

### Crossings and Edges

`edge` is `rising`, `falling`, or `either`; `occurrence` is `first` (default) or `last`. Crossings are linearly interpolated. An exact threshold hit is counted once. Touching a threshold and returning to the same side is not a crossing. One-sided events at window boundaries follow the adjacent segment's direction. A nonzero-duration threshold plateau without a unique crossing yields `AMBIGUOUS_CROSSING`.

Rise/fall require `low < high`. Percentages are relative to the low-to-high span; rise follows increasing values and fall decreasing values. Both crossings must belong to the *same monotonic edge*; the beginning of one pulse cannot be paired with the end of another. Restrict `window` for noisy or complex signals.

Delay example:

```json
{
  "id": "delay",
  "type": "delay",
  "trigger": {"signal": "v(in)", "level": 2.5, "edge": "rising"},
  "target": {"signal": "v(out)", "level": 2.5, "edge": "falling"},
  "occurrence": "first",
  "markers": true
}
```

First/last selects a trigger event; the target must be the first matching event no earlier than the trigger and strictly before the next trigger. A target only appearing in the next pulse is an error. The final trigger can use the end of available data; set a window when needed.

Settling requires **exactly one** tolerance: `tolerance_abs` in signal units or `tolerance_rel` as a fraction of `abs(target_value)` (0.01 means 1%). Use absolute tolerance for a zero target. Find the final re-entry into the band after the last excursion. At least two subsequent in-band samples must confirm it. Any new excursion or failure to settle before `stop` means no result; if the entire interval is within the band, settling time is zero.

## AC

| type | Fields | Unit |
|---|---|---|
| `cutoff_3db` | input, output, reference_frequency_hz, direction=high/low | Hz |
| `resonant_frequency` | voltage, current, current_sign; method=minimum_impedance; frequency_min_hz/frequency_max_hz | Hz |
| `capacitance_series` | voltage, current, current_sign, frequency_hz | F |
| `inductance_series` | voltage, current, current_sign, frequency_hz | H |
| `esr` | voltage, current, current_sign, frequency_hz | Ohm |

All calculations use matching complex frequency grids from the same run/analysis. Impedance uses v0.2 `frequency_sampling` (`exact`/`interpolate`) and `min_current_a` (default 1e-15 A). `current_sign` must explicitly be 1 or −1; the tool never adjusts sign to fit expected results.

Cutoff uses `H=Vout/Vin`, threshold `abs(H_ref)/sqrt(2)`, and an explicit `reference_frequency_hz`. For `high`, choose the nearest falling crossing above the reference; for `low`, the nearest rising crossing below it. Interpolate in dB; use log(f) for logarithmic grids and f for linear grids. `frequency_axis=auto` detects uniform linear/log spacing from at least three points. For irregular or two-point grids, set `frequency_axis=linear` or `log` explicitly. No implicit peak-based reference is used.

Resonance requires one unique strict interior minimum of `abs(Z)` within the requested window. Endpoints do not count; multiple minima are errors. The result is the actual sampled grid frequency (no fitting). Minimum impedance is not a universal resonance definition for arbitrary networks.

`Cs=−1/(2πf·Im(Z))` requires negative reactance; `Ls=Im(Z)/(2πf)` requires positive reactance. `abs(Im(Z))` must exceed `min_reactance_ohm` (default 1e−12 Ω). `esr` returns `Re(Z)`. These are condition-dependent equivalent series values, not internal model extraction or automatic datasheet validation.

## Parameter Sweep and MOSFET

```json
{
  "id": "vgs_threshold",
  "type": "sweep_crossing",
  "parameter": "VGS",
  "parameter_unit": "V",
  "signal": "i(VSENSE)",
  "analysis": "op",
  "reduction": "last",
  "current_sign": 1,
  "level": 0.00025,
  "edge": "rising",
  "when": {"VDS": 5},
  "markers": true
}
```

For every matching run, extract `first`, `last`, `min`, or `max`. On `sweep_crossing`, the window limits the **assembled parameter axis**, not each run's time axis. `current_sign` optionally reverses signal direction (default 1). Samples are ordered by the parameter and threshold crossing is interpolated linearly. `parameter_unit` defaults to V; override when measuring another quantity.

`when` fixes other parameters but must not fix the measured parameter. Multiple results for the same X yield `SWEEP_AMBIGUOUS`, even if values match. Different temperatures form separate families. Missing/failed required runs are reported, never suppressed. Multiple crossings default to `AMBIGUOUS_CROSSING`; explicit `occurrence: first`/`last` chooses one.

Family-level results include `scope=sweep` and the contributing `run_ids`. A VGS(th) measured at a specified ID is not the internal VTO; it depends on VDS, temperature, model, and sampling grid. Users choose the test conditions, not Spice Sifter.

`rds_on` requires voltage/current/current_sign and optionally `when`/`temperature_c`. It defaults to a single OP point; use `analysis=dc` and `x` for a multi-point DC sweep. Voltage must be the actual VDS, including differential measurement for a floating source. Current must come from the same operating point. `VDS/(current_sign·ID)` is a static resistance in ohms, **not** dV/dI.

## Examples and Errors

Examples: `examples/measure_transient.json`, `measure_cutoff.json`, `measure_capacitor.json`, `measure_inductor.json`, `measure_resonance.json`, and `measure_mosfet.json`. The MOSFET example extracts VGS(th) and RDS(on) across three temperatures using one shared set of simulations.

Measurement error names include `MEASUREMENT_INVALID`, `UNSUPPORTED`, `SIGNAL_MISSING`, `NOT_ENOUGH_POINTS`, `NO_CROSSING`, `AMBIGUOUS_CROSSING`, `NO_EXTREMUM`, `OUT_OF_RANGE`, `ZERO_DIVISION`, `INVALID_AXIS`, `INVALID_WINDOW`, `SWEEP_AMBIGUOUS`, and `NON_FINITE`; report JSON prefixes each with `MEASUREMENT_`. Underlying AC parser errors may retain their more specific codes. Results are not rounded or assigned precision beyond the SDF resolution.
