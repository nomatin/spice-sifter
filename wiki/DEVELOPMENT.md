# Spice Sifter 0.6 — Architecture and Development

Public interface: `python spice_sifter.py test.json`. Usage and limitations: [User Guide](User-Guide.md). Report contract: [REPORT_CONTRACT.md](REPORT_CONTRACT.md). Batch agents: [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md). Historical verification: [0.4](SWEEP_VERIFICATION.md), [0.3](TEMPERATURE_VERIFICATION.md), [0.2](AC_VERIFICATION.md), and [MVP 1](VERIFICATION.md).

## Repository Branches

- `master` — stable version for users and integration with Altium component libraries.
- `dev` — ongoing development and fixes prior to verification. Changes move to `master` only after a separate explicit decision.

## Testing Policy

**Test impacted functionality during development; run the full regression before promoting to `master`.** Do not introduce a separate testing framework, runner queue, or extra automation: use the project's existing `unittest` suites, acceptance scripts, and tools.

### 1. Development Tests

For every source change:

1. Identify affected functions, modules, and direct dependencies.
2. Add or update tests for the changed behavior.
3. Cover normal, error, and boundary cases.
4. Run only existing tests directly related to the change.
5. Commit to `dev` after those targeted tests pass.

**Do not automatically run the entire test suite.** Example targeted commands:

```powershell
python -m unittest discover -s tests -p "test_measurements.py" -v
python tests/test_measurements.py
```

If a shared function changes, test all directly affected subsystems rather than only the modified file. For example, changes to `report_model.finalize_report()` require `tests/test_report_contract.py` and tests producing its `results[]`/`coverage` inputs, such as `test_batch.py`, `test_pending_run.py`, `test_temperature.py`, `test_sweep.py`, `test_measurements.py`, `test_ac.py`, and `test_dblib.py`.

When real Altium testing is necessary, execute only the corresponding acceptance scenario, such as `python tests/acceptance_ac.py` or `python tests/acceptance_dblib.py` with Altium running. Defer expensive unrelated integration tests to the full regression.

### 2. Full Regression Before Promotion to master

Before every promotion of `dev` to `master`, run the following **against the exact dev commit being promoted**:

- All unit tests: `python -m unittest discover -s tests -v`.
- All real Altium MixedSim acceptance scripts:
  - `python tests/acceptance.py` — standalone SPICE basics;
  - `python tests/acceptance_ac.py` — AC analysis and graph export;
  - `python tests/acceptance_temperature.py` — temperature series;
  - `python tests/acceptance_sweep.py` — parameter sweeps;
  - `python tests/acceptance_measurements.py` — automatic measurements;
  - `python tests/acceptance_dblib.py` and `python tests/acceptance_dblib_multipin.py` — DbLib and multi-pin components (requires `SPICE_SIFTER_DBLIB_PATH`).
- Confirm standalone, DbLib, Temperature, Sweep, AC, and Automatic Measurements.
- Validate JSON reports, including `schema_version`, `results[]`, `coverage`, and serialization without NaN/Infinity.
- Check for regressions against `master`.

Record results against the exact commit. If executable code changes after a full regression, determine which results are invalidated and rerun those tests. Repeat the full regression for substantial changes.

### 3. Promotion Requirements

Promote to `master` only when the full regression is complete and passing, essential Altium acceptance tests have verifiable results, regressions and blockers are resolved or explicitly recorded, and promotion is separately approved. `BLOCKED`, `NOT_RUN`, and skipped checks must never be silently counted as passing.

### 4. Reporting

For each development change, state the affected area, tests actually executed and their results, and any tests deferred until the full regression. Never claim "all tests passed" after only targeted tests.

Before promotion, prepare a separate full report containing the verified commit SHA, every suite/scenario run and its result, explicit `BLOCKED`/`NOT_RUN`/skipped items, JSON contract results, and a comparison against `master`. Report status must be `PASS`, `FAIL`, or `ERROR` with an explanation for every deviation.

## Data Flow

1. Parse and validate test JSON, including AC checks and graph requests.
2. Prepare NSX: preserve original directives, include models after the header, and append missing `.END`.
3. Confirm the selected primary X2.EXE process; auxiliary `-R` launchers do not count as running Altium.
4. Acquire the file lock, safely examine `pending-run`, and create `%TEMP%/SpiceSifter/<run_id>`.
5. Generate BOM-free ASCII DelphiScript with `RunTests`; Unicode paths are emitted as character literals.
6. Open the standalone NSX and execute `ADVSIM:RunSimulator`.
7. Save the current run's SDF and publish status via temporary file and atomic rename. `collect.tmp`/`status.tmp` can provide completion evidence.
8. Parse SDF, validate full-resolution signals, then optionally export reduced graph arrays.
9. Aggregate Temperature/Sweep and run `finish_measurements()`.
10. Normalize once via `report_model.finalize_report()`: generate `results[]`, `coverage`, check invariants, and finalize status.
11. Return a single JSON response; exit code PASS=0, FAIL=1, ERROR=2.

No persistent Altium projects or components are created in standalone mode. User documents are not saved or closed. Parallel simulations are unsupported.

## Altium Protocol

The integration uses `WorkspaceManager:OpenObject`, `ADVSIM:RunSimulator`, and `ScriptingSystem:RunScriptFile`. The X2 invocation passes arguments directly, using native Altium quoting rather than a shell. An auxiliary launcher exit code does **not** prove simulation completion.

`SAVE_PENDING` indicates that a separate save operation must follow MixedSim returning control. Exactly one additional save script is permitted. Final statuses are `OK` and `ERROR`; SDF path must match the expected file inside the current run directory.

After a timeout, the user's Altium process is not terminated. The atomically written `pending-run.txt` includes `run_id`, `run_dir`, Altium PID and creation time, `runner_pid`, `started_at`, `timeout_at`, and state. The next run checks the final status and process under a shared lock: completed/stale markers are safely removed, while active/unknown states block execution with diagnostics. Legacy path-only markers are removed only after all X2.EXE processes are verifiably absent. Marker age alone never justifies removal. See `doctor` and `recover`.

For a separately managed hidden Altium instance, see [HIDDEN_ALTIUM.md](HIDDEN_ALTIUM.md).

## Numerical Data

`SDFDocument` stores Charts and metadata, `SDFChart` stores Waves, and `SDFWave` stores signal name, unit, type, sample count, and full X/Y arrays. Complex signals use Python `complex`.

The SDF 1.0 parser derives sizes from WavePoints and complexity flags. Real points contain float64 X/Y; complex X/Y contains Re(X), Im(X), Re(Y), Im(Y), followed by the corresponding auxiliary float32 block. Boundaries, total length, and TotalPoints are checked. Unknown formats are rejected rather than guessed.

The validator selects a Chart by analysis and required signals, rejecting ambiguity. AC checks require finite frequencies in strictly increasing positive order and identical V/I grids. Interpolation computes V(f) and I(f) **before** calculating `Z=V/(current_sign·I)`. Limits apply to original full-resolution data.

Graph export returns synchronized real/imag/magnitude arrays and units; real-valued signals export only real data. `max_points` reduces exported indices only, without affecting validation. Graphs are included even on FAIL; absent a graph request, the `graphs` field is omitted.

## Project Files and Verification

- `tests/test_*.py` — standalone unit tests, including real SDFs in `tests/fixtures`.
- `tests/acceptance.py` — live MVP 1 regression.
- `tests/acceptance_ac.py` — live AC acceptance and graph export.
- `examples/` — divider, external-model diode, AC R/C/L/RC, full and reduced graph examples.
- `diagnostics/` and local acceptance JSON reports are Git-ignored; original models and SDF fixtures are tracked.

## Report Contract 0.6

`report_model.py` owns normalization. `finalize_report(report, data)` runs after `finish_measurements()` and Temperature/Sweep aggregation and is the sole source of `results[]`, `coverage`, and final status. `plan_results()` calculates required check/measurement instances from JSON and the run plan, accounting for `when`, `temperature_c`, `temperatures_c`, and `sweep.parameters`. A family-level `sweep_crossing` creates one result per temperature group, not one arbitrarily selected run. PASS/FAIL/MEASURED values must be finite numbers; otherwise they become `ERROR` (`RESULT_VALUE_INVALID` or `RESULT_VALUE_MISSING`) before computing coverage and report status.

`runtime_state.py` implements the interprocess lock, atomic `pending-run.txt`, PID/creation-time verification, state classification (`COMPLETED`, `STALE`, `ACTIVE`, `UNKNOWN`, `CORRUPTED`), `doctor_report()`, and `recover_pending()`. `spice_sifter.py` exposes `doctor` and `recover` while keeping the original `python spice_sifter.py test.json` interface.

`schemas/report.schema.json` defines the 1.0 contract. `batch_agent.py` is the reference batch agent: preflight, stop-on-system-error, and `NOT_RUN` for skipped tests.

## Altium API References

- [Running scripts in Altium](https://www.altium.com/documentation/altium-designer/scripting/running-scripts)
- [IServerDocument and saving](https://www.altium.com/documentation/altium-dxp-developer/iserverdocument-interface)

## Temperature Layer 0.3

Before the existing runner, `execute_test` plans temperature runs, parses directives, validates graph/check references, and sequentially calls `execute_one` for each temperature. The SDF parser is unchanged. Temperature is tied to each unique `run_id` rather than inferred from SDF; AC data from different runs are never mixed.

Altium 24.4 was verified with `.OPTIONS TEMP`, since direct `.TEMP` did not affect the control diode. The substitution occurs in `generate_nsx` after validating the external `.TEMP`. TNOM and model files remain untouched. Series directives are inserted immediately after the header, outside nested blocks. Graphs reside in `runs[i].graphs`; errors do not discard other run results. Locks and `pending-run.txt` remain shared; timeouts never force-stop the user's Altium. See [TEMPERATURE_VERIFICATION.md](TEMPERATURE_VERIFICATION.md).

## Parameter Sweep 0.4

`parameter_axes` parses explicit values and compact linear axes. `sweep_plan` calculates the Cartesian product with temperatures, validates `max_runs`, `when`, placeholders, and prepared circuits before launching Altium. Decimal arithmetic prevents step accumulation errors; very large linear ranges are rejected before expanding them.

`execute_sweep` iterates temperatures and parameter-index products, substitutes Decimal values, filters checks/graphs, and invokes existing `execute_one`. Parameter context is attached to `run_id` outside the SDF parser. Without sweep, `execute_one` retains the legacy structure; independent temperature series are unchanged.

`require_running_altium` checks `Responding` on the primary process. An unresponsive Altium returns `BUSY` without UI automation. See [SWEEP_VERIFICATION.md](SWEEP_VERIFICATION.md).

## Measurement Engine 0.5

`validate_measurements` validates a fixed request schema before Altium. `process_measurements` runs after full SDF parsing and before graph decimation. `measure_value` works exclusively on `SDFDocument`: it does not access the runner, files, models, or simulator. The existing validator applies limits; there is no second tolerance engine.

For `sweep_crossing`, each matching run retains only one scalar signal reduction in its internal context. `finish_measurements` groups values by temperature, checks completeness and ambiguity, and produces top-level results. Entire sweep waveform arrays need not remain in memory. Internal fields are removed before JSON output.

Individual measurements have `run_id`, while family measurements use `scope=sweep` and `run_ids`. Overall status includes measurements; existing `checks`, `runs`, `graphs`, and `summary` remain. See [MEASUREMENTS.md](MEASUREMENTS.md) and [MEASUREMENT_VERIFICATION.md](MEASUREMENT_VERIFICATION.md).

## DbLib Component Mode

The presence of `component` in input JSON selects the second mode. `dblib_runner.py` verifies that `component.library` is an **absolute path** to an existing .DbLib, parses its table, copies the immutable `dblib_resources/template` into `temp/dblib_runs/<run_id>/`, substitutes paths in `dblib_resources/dblib_experiment.pas`, and runs it inside the selected Altium instance. No name-only library search is performed. Standalone mode and runner remain unchanged; both modes share `altium_session()` locking and `pending-run.txt`.

DelphiScript calls `SchServer.LoadComponentFromDatabaseLibrary`, places `ISch_Component` via `MoveToXY`/`RegisterSchObjectInContainer`, finds pin hot spots, creates short wires and net labels, compiles the project, and verifies the DUT pins belong to `DUT_P`/`GND` nets. Altium then generates NSX from SchDoc and executes `ADVSIM:RunSimulator` again to produce SDF. Cleanup does **not** use coordinate fallback `X >= 4000`; only objects logged for this run (DUT UniqueID, Wire, Net Label) are removed, including after FAIL/ERROR. Successful cleanup requires all logged objects removed, fixture unchanged, cleaned SchDoc successfully saved by `DoFileSave()`, and `STATUS=OK`. Otherwise `script_failed=1`, `STATUS=ERROR`, and original failure diagnostics are retained. Altium documents are not closed.

Python also verifies component library/table/ID, two-pin structure, SIM Implementation and Port Map, NSX (exactly one DUT, `.SUBCKT`, net order), and that NSX/SDF belong to the current run. Duplicate project SDF Charts are compared by analysis, signal names, units, data type, sample counts, and full real/imag X/Y arrays. Exact duplicates are merged; differing duplicates yield `ERROR / SDF_DUPLICATE_CONFLICT` rather than silently choosing one. The existing `parse_sdf()`, `validate_document()`, `process_measurements()`, `finish_measurements()`, and `export_graphs()` process the results.

DbLib supports single tests only; `sweep`, `temperatures_c`, and `when` are rejected before launching. Resources are tracked in the repository; working copies are created under `temp`. Tests: `tests/test_dblib.py` (no Altium) and `tests/acceptance_dblib.py` (live library). Live acceptance requires the absolute `.DbLib` path in `SPICE_SIFTER_DBLIB_PATH`; inputs/reports appear in `temp/dblib_acceptance/<acceptance_id>/`, projects and SDFs in `temp/dblib_runs/<run_id>/`.

### Multi-pin SPICE Fixtures

For a `component` JSON containing `spice`, Spice Sifter uses `dblib_resources/template_empty` and `dblib_fixture.pas`. `dblib_fixture.py` parses only V/I/R/C/L elements, `{{DUT}}`, and one `.OP`, `.AC`, or `.TRAN` directive; unsupported constructs yield `FIXTURE_*` before Altium starts. The generator places fixtures from `Simulation Generic Components.IntLib` and Net Labels directly on electrical hot spots (verified live). Display-mode duplicate pins are deduplicated by designator + hot spot; different hot spots yield `COMPONENT_PIN_AMBIGUOUS`.

`component.pins` must map **all** electrical pins; `null` explicitly denotes an unconnected pin. Mapping uses the loaded component's actual Sim Port Map and NSX node order, not iteration order. The legacy two-pin fixture path is unchanged. Live multi-pin acceptance: `tests/acceptance_dblib_multipin.py`. BJT testing was `BLOCKED` because `MY_LIB` had no BJT. Project-mode `.DC` is rejected; AC impedance requires current probes.
