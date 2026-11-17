# Changelog

## 0.7.1 — Hidden Altium Control Fixes

- `hidden_instance.py`: verify Job Object creation and configuration. The main process and `-R` launchers start with `CREATE_SUSPENDED`, join the Job Object, then resume. Assignment failure yields `HIDDEN_LAUNCH_FAILED` and terminates only the new process. An unmanaged process cannot become READY.
- `runtime_state.py`: batch lock tracks its owning thread. Re-entry is allowed only from that thread; another thread gets BUSY / MarkerError. Added `current_state_root()` and `state_scope()` for consistent state storage.
- `spice_sifter.py`: single hidden runs inspect prior session state (refuse ACTIVE/UNKNOWN/CORRUPTED; remove provably STALE entries). Stop failures change the final status to ERROR while preserving `results[]` and diagnostics. `run_test_file(..., temp_dir=...)` uses the shared state root.
- `batch_agent.py`: batch and nested tests use the same state root. Stop failures appear under `altium_session_stop_error`.
- Tests: 22 targeted cases in `tests/test_hidden_control.py` and updates to impacted tests. Full regression was not run.

## 0.7.0 — Hidden Altium Instance

- Added `hidden_instance.py` to manage Altium on a separate invisible Win32 Desktop (`CreateDesktopW`, `CreateProcessW` with `STARTUPINFO.lpDesktop`, and a `KILL_ON_JOB_CLOSE` Job Object). Identity is PID + creation time; no `WM_CLOSE`, `SwitchDesktop`, `SendInput`, or `taskkill`.
- Added `--altium-instance {current,hidden}` to single-run, doctor, and batch-agent interfaces. Default `current` preserves previous behavior.
- All `-R` commands, including `collect.pas`, are routed through a launcher. Hidden mode creates launchers on the hidden Desktop; current mode continues using `subprocess.Popen`.
- Added `run_batch(..., altium_instance="hidden")`: a fresh instance per batch, `START → READY → Test 1..N → STOP`, cleanup in `finally`. Systemic `HIDDEN_*` failures stop the series and mark unstarted tests NOT_RUN without discarding completed reports.
- The batch holds the existing `altium.lock` throughout; nested calls within the owning process avoid re-locking while independent Spice Sifter runs are blocked.
- `runtime_state.py` records sessions, validates PID + creation time, supports hidden doctor without an already-open Altium, and safely recovers stale session records. Existing diagnostic fields remain.
- Optional report `altium_session` records Desktop, PID, creation/READY times, commands, and termination reason, without changing `results[]`/`coverage`.
- Added 27 unit tests in `tests/test_hidden.py`; fixed `test_filter_graphs_no_checks_and_fail`, which violated the report contract by reporting FAIL without a numeric value.

## 0.6.3 — JSON-safe Report Finalization

- `report_model.finalize_report()` now recursively sanitizes all report fields: `results[]`, `checks[]`, `measurements[]`, `runs[]`, `coverage`, `summary`, `graphs`, `diagnostics`, `error`, and arbitrary nested objects/arrays.
- NaN and ±Infinity become null; finite values, including zero and negatives, remain unchanged. Strings and booleans are not coerced.
- `_validated_result_value()` still runs **before** sanitization so RESULT_VALUE_MISSING/RESULT_VALUE_INVALID remain correctly distinguished. Re-finalization is idempotent and preserves the diagnostic code.
- Unsupported JSON types become null (not strings), and the report becomes serializable with `REPORT_SERIALIZATION_INVALID` / ERROR.
- Added local `json.dumps(report, allow_nan=False)` tests. Altium/MixedSim/full regression were not run.

## 0.6.2 — Value Classification and Strict Batch Contract

- `report_model.py` distinguishes missing/null results (`RESULT_VALUE_MISSING`) from invalid present values (NaN/±Infinity/string/boolean, `RESULT_VALUE_INVALID`). Raw values are validated before normalization, nonfinite numbers are excluded from JSON, and valid numbers including zero are preserved.
- `batch_agent.validate_report_contract()` validates `validation_status`, required result fields, kind/id/unit, errors relative to status, null values for NOT_RUN, nonnegative coverage counters, coverage invariants, PASS/FAIL consistency, and exact correspondence between status counts and results.
- Structural contradictions yield `REPORT_CONTRACT_INVALID`; lost results/incomplete coverage yield `REPORT_INCOMPLETE`.
- Added corresponding local tests. Altium/MixedSim/full regression were not run.

## 0.6.1 — Review Fixes

- `batch_agent.run_batch()` validates schema_version, results, coverage, unique result_id, counters, and status integrity before accepting reports. Incomplete reports yield REPORT_INCOMPLETE; structural violations yield REPORT_CONTRACT_INVALID; original reports are preserved in diagnostics.
- The normalizer prohibits PASS/FAIL/MEASURED without a finite numeric value. Null, NaN, Infinity, strings, and booleans become ERROR with RESULT_VALUE_INVALID/RESULT_VALUE_MISSING, followed by final status recomputation.
- Legacy path-only pending markers now check **all** X2.EXE processes. Markers are removed only when Altium is verifiably absent; `recover --confirm-legacy-stopped` does not bypass process checks.
- `doctor` no longer treats `responding = null` as READY. Altium 24.4.x and `responding = true` are required; unsupported/unknown versions, process-query errors, and ambiguous instances cannot pass.
- Added targeted unit tests. No full acceptance suite or live Altium simulation was run for these fixes.

## 0.6 — Reliable Execution and Report Contract

- Added `schema_version: "1.0"` to reports and unified `results[]` for standalone, DbLib, Temperature, Sweep, Measurements, and partial/failed runs.
- Added coverage invariants: `expected = completed + missing`, `completed = passed + failed + measured + errors`, `missing = not_run`.
- Result planning considers checks, measurements, temperatures_c, sweep.parameters, when, and temperature_c. Sweep-family measurements produce one result per temperature group with `scope: "sweep"` and `run_ids`.
- Missing/unexecuted results yield ERROR / REPORT_INCOMPLETE; duplicate result_id produces a diagnostic error, preventing false PASS.
- Added MEASURED/NOT_RUN statuses and `validation_status: "NO_ASSERTIONS"` when no tolerance assertions exist.
- Replaced `pending-run.txt` with an atomically written structured JSON marker (run_id, run_dir, Altium PID + creation time, runner_pid, started_at, timeout_at, state); legacy path-only format remains supported.
- Added safe recovery only when previous run/process termination is proven; active/uncertain simulations are never interrupted and marker age is not grounds for deletion.
- Added `doctor [--json]` and `recover` commands without changing existing CLI invocation.
- Added reference `batch_agent.py`: shared preflight, stop-on-system-error, and NOT_RUN for unstarted tests.
- Fixed `collect.tmp`/`status.txt` read races; transient file locks and SAVE_PENDING with a completed collect.tmp no longer cause false timeouts.
- Added report schema, report_model.py, runtime_state.py, and tests for contract, pending runs, doctor/recover, and batch preflight.
- Documentation: [Report Contract](wiki/REPORT_CONTRACT.md), [Agent Integration](wiki/AGENT_INTEGRATION.md), updated README, User Guide, Development, and Home.

Verification on Altium Designer 24.4.1.13: 174 automated tests, Standalone 7/7, Sweep 11/11, Temperature 9/9, Measurements 7/7, AC 11/11, DbLib 13/13, and all available multi-pin DbLib cases passed (BJT expected BLOCKED). All 65 actual v1.0 JSON reports passed schema and coverage invariants.

## Previously Unreleased Changes

- Added DbLib Component mode: load a database component, place on a prepared SchDoc with wires/net labels, compile, generate NSX, run MixedSim to SDF, then reuse Validator/Measurements/Graph Export.
- Added `dblib_runner.py`, `dblib_resources/` templates/DelphiScript, `examples/dblib_*`, standalone tests, and live `tests/acceptance_dblib.py`.
- Added run-object journaling and cleanup on PASS/FAIL/ERROR, without closing Altium documents. Run artifacts remain under `temp/dblib_runs/<run_id>/`.
- Enforced an absolute existing `.DbLib` path in `component.library`, removing name-only discovery.
- Cleanup now succeeds only when generated objects are removed and the cleaned SchDoc saves successfully; otherwise STATUS=ERROR retains the original cause.
- Duplicate SDF graphs are compared across full X/Y arrays, units, and complex values; conflicting duplicates produce SDF_DUPLICATE_CONFLICT.
- Moved acceptance inputs/results under `temp/dblib_acceptance/<acceptance_id>/` and added `SPICE_SIFTER_DBLIB_PATH`.
- Added GNU GPL-3.0-only LICENSE, SPDX headers, and README license section.
- Corrected RMS from trapezoids of squared samples to exact integration of the squared piecewise-linear interpolation; added mathematical tests.
- Extended DbLib to multi-pin components with `component.spice`, `component.pins`, `{{DUT}}`, dynamic pin discovery, arbitrary designators, and explicit null for unconnected pins.
- Added a minimal SchDoc fixture generator for V/I/R/C/L and one .OP/.AC/.TRAN directive, plus `template_empty` and `dblib_fixture.pas`.
- Added component and pin errors: COMPONENT_NOT_FOUND, COMPONENT_NO_PINS, COMPONENT_PIN_NOT_FOUND, COMPONENT_PIN_AMBIGUOUS, COMPONENT_PIN_UNASSIGNED, COMPONENT_PIN_CONNECTION_FAILED, COMPONENT_NETLIST_MISMATCH, COMPONENT_SIM_MODEL_MISSING, COMPONENT_SIM_PORT_MAP_INVALID, MULTIPART_UNSUPPORTED.
- Pin Mapping uses the real Sim Port Map and Altium NSX node order, not the order of pins in JSON.
- Added `tests/acceptance_dblib_multipin.py` covering 3-pin MOSFET, 6-pin IC, null/shared/bad/missing pin, and unsupported SPICE. BJT BLOCKED due to missing BJT in library.
- Project-mode .DC is rejected as FIXTURE_ANALYSIS_UNSUPPORTED; AC impedance needs current probes; multipart symbols are rejected.

## Project Rename

- Renamed DBUR-Test to Spice Sifter: `dbur_test.py` → `spice_sifter.py`, `DBUR_ALTIUM` → `SPICE_SIFTER_ALTIUM`, `%TEMP%/DBUR-Test` → `%TEMP%/SpiceSifter`.
- No changes to version 0.5 functionality; updated documentation, examples, and tests.

## 0.5 — Automatic Measurements

- Added Measurement Engine on full SDF data without rerunning simulations for individual measurements.
- Added windowed statistics, edges, delays, settling, AC cutoff, minimum impedance, and equivalent C/L/ESR.
- Added sweep_crossing for VGS(th), static rds_on, when/temperature_c, and separate temperature families.
- Reused existing Validator for limits; results distinguish MEASURED/PASS/FAIL/ERROR and support diagnostic markers.
- Added examples, mathematical tests, real SDF fixtures, and acceptance scripts. See [Measurement Verification](wiki/MEASUREMENT_VERIFICATION.md).

Verified: 79 unit tests, 7 new live measurement scenarios and 38 previous regression cases on Altium Designer 24.4.1.13.

## 0.4 — Parameter Sweep

- Added explicit values, Decimal start/stop/step, and Cartesian parameter/temperature products.
- Added safe numeric `{{NAME}}` placeholders, when filters, and `--max-runs` (default 100).
- Added parameters/run_id metadata, independent checks, and graph exports per combination.
- Aborted remaining combinations on timeout or unresponsive/lost Altium while retaining completed reports.
- Verified standalone OP with .PLOT OP; added analysis=op, example, and real SDF.
- Did not introduce automatic closing of documents.

Verified: 58 unit tests, 11 live Sweep cases, 27 prior regression cases. See [Sweep Verification](wiki/SWEEP_VERIFICATION.md).

## 0.3 — Temperature Testing

- Added single .TEMP and independent sequential `temperatures_c` series.
- Verified temperature control via .OPTIONS TEMP in MixedSim 24.4; input .TEMP is converted in working NSX.
- Added temperature-scoped checks, graph filters, run_id and aggregate runs/summary.
- Separated simulation success from checks status; allowed collecting temperature graphs without assertions.
- Kept per-temperature errors and original diagnostics intact.
- Added examples, real temperature SDF fixtures, 12 new unit tests, and live acceptance.

Verified: 43 unit tests, 9 real temperature scenarios on AD24.4.1.13. See [Temperature Verification](wiki/TEMPERATURE_VERIFICATION.md).

## 0.2 — AC Analysis and Graph Export

- Added AC DEC/OCT/LIN and complex SDF parsing from Altium 24.4.
- Added Re(Z), Im(Z), |Z| checks, explicit current polarity, and min_current_a.
- Added exact frequency selection and complex V/I interpolation without extrapolation.
- Added output.graphs for transient/DC/AC signals and impedance with full or endpoint-preserving decimated arrays.
- Added ordinary DC checks, AC examples, real SDF fixture, automated tests, and acceptance cases.
- Preserved CLI, legacy checks, statuses, and exit codes.

Verified: 31 unit tests, 11 live AC scenarios, and 7 live regression scenarios on Altium Designer 24.4.1.13. See [AC Verification](wiki/AC_VERIFICATION.md).

## 0.1 — MVP

- Implemented NSX preparation, temporary DelphiScript, MixedSim execution, and custom SDF parser.
- Added first/last/min/max measurements, JSON report, timeout, run isolation, and a check for running Altium.
- Added examples for a voltage divider and a diode with an external model.
