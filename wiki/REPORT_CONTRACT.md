# Spice Sifter JSON Report Contract 1.0

Since 0.6, every report contains normalized `results[]`, the primary measurement source for external agents. Legacy `checks[]`, `measurements[]`, and `runs[]` are retained for backward compatibility. Machine-readable schema: [report.schema.json](../schemas/report.schema.json).

## Top-level Object

```json
{
  "schema_version": "1.0",
  "status": "PASS",
  "validation_status": "ASSERTIONS",
  "test": "voltage_divider",
  "results": [],
  "coverage": {},
  "checks": [],
  "measurements": [],
  "runs": [],
  "error": null
}
```

`schema_version` identifies the **report contract**, not the application version.

## results[]

Each entry corresponds to one required check or measurement instance:

```json
{
  "result_id": "run_001:check:0",
  "run_id": "run_001",
  "kind": "check",
  "id": 0,
  "status": "PASS",
  "value": 2.5,
  "unit": "V",
  "limits": {"min": 2.4, "max": 2.6},
  "parameters": {"VIN": 5},
  "temperature_c": 25,
  "error": null
}
```

Required fields:

| Field | Description |
|---|---|
| `result_id` | Unique identifier within this report |
| `run_id` | Run identifier or `null` for a sweep-family measurement |
| `kind` | `check` or `measurement` |
| `id` | Original check/measurement ID; if absent on checks, the source-array index |
| `status` | `PASS`, `FAIL`, `MEASURED`, `ERROR`, or `NOT_RUN` |
| `value` | Finite numeric result or `null` |
| `unit` | Unit or `null` |
| `error` | `{code, message}` or `null` |

Optional fields: `limits`, `parameters`, `temperature_c`, `frequency_hz`, `signal`, `component`, `scope`, `run_ids`, and `markers`. Sweep-family measurements use `scope: "sweep"`, `run_id: null`, and `run_ids`; intermediate sweep points are not separate results.

## Statuses and Numeric Validation

| Status | Meaning |
|---|---|
| `PASS` | Assertions present and limits satisfied |
| `FAIL` | Assertions present but limits violated |
| `MEASURED` | Value measured without limits; **not** datasheet compliance |
| `ERROR` | No reliable result |
| `NOT_RUN` | Check/measurement was not executed; **not** PASS |

For PASS, FAIL, and MEASURED, `value` must be finite. Missing or `null` values yield `RESULT_VALUE_MISSING`; NaN, ±Infinity, strings, or booleans yield `RESULT_VALUE_INVALID`. Finite numbers, including zero, preserve their status and value.

Example invalid result:

```json
{
  "status": "ERROR",
  "value": null,
  "error": {
    "code": "RESULT_VALUE_INVALID",
    "message": "Result has no valid finite numeric value"
  }
}
```

NaN and Infinity never appear as JSON numbers. `ERROR` and `NOT_RUN` may have null values. The overall status is recalculated after numeric validation.

## coverage

```json
{
  "coverage": {
    "status": "OK",
    "expected": 14,
    "completed": 13,
    "missing": 1,
    "passed": 9,
    "failed": 1,
    "measured": 3,
    "errors": 0,
    "not_run": 1
  }
}
```

`expected` counts planned check/measurement instances, **not** SPICE runs. One check across five temperatures produces five expected results.

Invariants:

```text
expected  = completed + missing
completed = passed + failed + measured + errors
missing   = not_run
len(results[]) = expected        # when planning succeeded
```

When the plan cannot be determined, `coverage.status = "UNKNOWN"`, `expected = null`, and explanations are recorded in `coverage.diagnostics`.

## Overall Status

Priority: **ERROR > FAIL > PASS**. ERROR applies if any required result is ERROR/NOT_RUN, coverage is incomplete, IDs are duplicated, an unplanned result exists, a simulation/run fails, or report integrity cannot be assured. FAIL means complete processing with no errors but at least one violated limit. PASS means all planned work completed without errors or failed limits.

If no tolerance assertions were defined:

```json
{"validation_status": "NO_ASSERTIONS"}
```

A simulation without assertions may have `status: "PASS"`, but that does not establish compliance with electrical specifications.

## Examples

### PASS

```json
{
  "schema_version": "1.0",
  "status": "PASS",
  "validation_status": "ASSERTIONS",
  "coverage": {"status": "OK", "expected": 1, "completed": 1, "missing": 0,
               "passed": 1, "failed": 0, "measured": 0, "errors": 0, "not_run": 0},
  "results": [{"result_id": "run_001:check:0", "run_id": "run_001", "kind": "check",
               "id": 0, "status": "PASS", "value": 2.5, "unit": null,
               "limits": {"min": 2.4, "max": 2.6}, "error": null}],
  "error": null
}
```

### Failed Assertion

```json
{
  "status": "FAIL",
  "coverage": {"status": "OK", "expected": 1, "completed": 1, "missing": 0,
               "passed": 0, "failed": 1, "measured": 0, "errors": 0, "not_run": 0},
  "results": [{"result_id": "run_001:check:0", "status": "FAIL",
               "value": 3.1, "limits": {"min": 2.4, "max": 2.6}, "error": null}]
}
```

### Missing Result

```json
{
  "status": "ERROR",
  "error": {"code": "REPORT_INCOMPLETE", "message": "Expected 14 results, collected 13"},
  "coverage": {"status": "OK", "expected": 14, "completed": 13, "missing": 1,
               "passed": 9, "failed": 1, "measured": 3, "errors": 0, "not_run": 1}
}
```

The missing result must appear in `results[]` as `NOT_RUN`. Never substitute PASS, zero, or a previous value.

### Duplicate result_id

```json
{
  "status": "ERROR",
  "error": {"code": "REPORT_DUPLICATE_RESULT", "message": "Duplicate result_id values: ..."},
  "coverage": {"diagnostics": [{"code": "REPORT_DUPLICATE_RESULT"}]}
}
```

### Sweep-family Measurement

```json
{
  "result_id": "sweep:vgs_threshold:2",
  "run_id": null,
  "kind": "measurement",
  "id": "vgs_threshold",
  "scope": "sweep",
  "run_ids": ["run_005", "run_006", "run_007", "run_008"],
  "status": "MEASURED",
  "value": 2.15,
  "unit": "V",
  "error": null
}
```

## Normalization

`report_model.finalize_report()` is the single normalizer. It is invoked after `finish_measurements()` and Temperature/Sweep aggregation, preventing duplicate measurements. Standalone, Temperature, Sweep, and DbLib modes do not have independent normalizers.

## Compatibility

- Legacy `checks[]`, `measurements[]`, and `runs[]` are preserved.
- Input JSON from earlier releases remains supported.
- Exit codes: PASS=0, FAIL=1, ERROR=2.
- New agents should use `results[]` and `coverage`; older agents may continue using the legacy arrays.
