# Spice Sifter 0.4 — Parameter Sweep

## Implementation

The existing independent-run scheduler was extended. Sweeps only substitute numeric values into the main SPICE text; the runner, SDF export, complex parser, and numerical validators were not replaced. External models are not modified and Altium documents are not closed automatically.

`values` grids preserve input order; `linear` grids are built with Decimal using `start+i·step`. The total number of parameter/temperature combinations is validated before invoking Altium; the default sweep limit is 100 runs. Every prepared circuit is then validated before the first run. Duplicate values and values indistinguishable in SPICE float representation are rejected.

The iteration order is temperature, first parameter, remaining parameters (the last changes fastest). `when` conditions match original grid indices; exact Decimal matches take precedence. For float representation differences, one uniquely close candidate is allowed; ambiguous matches are rejected.

Each run has parameters and a unique `run_id`. Graph metadata mirror run metadata. Runs with no applicable checks report `checks_count=0` and `checks_status=NOT_CONFIGURED`. Ordinary failures do not stop the series; `TIMEOUT`, `SCRIPT_NOT_EXECUTED`, `BUSY`, and loss of the accessible process stop subsequent runs. Completed results and the `not_run` count are returned.

## Live Verification

**Environment:** Windows, Python 3.14.5, Altium Designer 24.4.1.13; October 30, 2026.

| Scenario | Result |
|---|---|
| VIN 0 through 5, step 1 | Six independent runs; V(out)=VIN/2 |
| VIN=[2,5,10], RLOAD=[1000,2000] | Six runs in the specified order; matched divider equation |
| `when` on two parameters | One check at VIN=5/RLOAD=1000; other runs have checks_count=0 |
| Graph when VIN=5 | Only two graphs, 200 points each; correct run_id/parameters |
| Three temperatures × two VIN values | Six runs, temperature outermost; graphs only at VIN=5 and T=−40/+85 |
| AC R=[100,1000] | Re(Z), Im(Z), and magnitude match nominal values |
| RC transient | Two time constants; final voltage matches 1−exp(−t/RC) |
| Faulty model in the second combination | PASS / ERROR / PASS; first and third results retained |
| FAIL with graphs | FAIL; requested graphs returned |
| Run limit exceeded / unknown placeholder | INPUT_INVALID before simulation |
| DC with VMAX=[2,4] | PASS, final V(out)=1/2 V; graphs with V axis and parameter metadata |
| OP with VIN=[2,5] | PASS, V(out)=1.0/2.5 V; separate real SDFs obtained |

Sweep acceptance: **11/11** scenarios, 35 actual independent simulations and two input errors detected without Altium. The local report, `tests/sweep_acceptance_report.json`, is excluded from Git. Reproduce with `python tests/acceptance_sweep.py` while Altium is open.

## Operating Point

OP was not claimed in earlier versions. Version 0.4 separately verified `.OP` and `.PLOT OP V(out)`. A standalone `.OP` circuit without explicit PLOT OP did not generate SDF. Working example: `examples/sweep_op.json`. Checks use `analysis=op`; existing checks without `analysis` still use transient. A real SDF is included as `tests/fixtures/op_ad24_4.sdf`. The X axis of OP graphs is dimensionless (`x_unit=1`).

## Automated Tests

**58 tests:** 43 prior tests and 15 Sweep/OP/process-responsiveness tests. Covered cases include Decimal grids in both directions, excluded endpoints, single points, empty/invalid ranges, duplicates, parameter-name case handling, substitution, unused and unknown parameters, `when` and float tolerances, preflight run limits, increased limits, temperature/parameter order, graph metadata and filters, continuing after a failure, and stopping on timeouts. OP was also verified with an actual SDF.

The timeout stop test mocks the runner; Altium was not deliberately hung. An individual model failure was verified with real MixedSim.

## Regression

The original JSON files were unchanged. `tests/acceptance.py` passed **7/7**, `tests/acceptance_ac.py` passed **11/11**, and `tests/acceptance_temperature.py` passed **9/9**. All suites exited with code 0. They cover ordinary models, missing signals, invalid limits, faulty models, repeated runs, Cyrillic paths, AC interpolation, temperature series, and graphs.

## Limitations

- Only Cartesian products: no zip, built-in Sweep, `.STEP`, random grids, or parallel simulations.
- Parameters are numeric only, not SPICE suffixes or text. External model files are not subject to substitution.
- Use `temperatures_c` or fixed `.TEMP` for temperatures; existing validation checks temperature directives before substitution.
- Restrictions on short Windows filenames for Cyrillic paths and full-text MixedSim diagnostics remain.
- Opened NSX/SDF documents remain in Altium; large sweeps produce many tabs.
