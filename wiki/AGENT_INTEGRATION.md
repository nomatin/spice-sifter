# Batch Agent Integration

Spice Sifter remains a single-test CLI tool. Before a test series, a batch agent (such as one maintaining an Altium component library) must perform **one shared readiness check**. The reference implementation is [`batch_agent.py`](../batch_agent.py).

## Preflight

```powershell
python spice_sifter.py doctor --json
```

Example response:

```json
{
  "schema_version": "1.0",
  "status": "READY",
  "altium": {
    "path": "C:\\Program Files\\Altium\\AD24\\X2.EXE",
    "exists": true,
    "running": true,
    "responding": true,
    "version": "24.4.1.13"
  },
  "lock": {"available": true},
  "pending_run": null,
  "temporary_directory": {"path": "C:\\Users\\...\\Temp\\SpiceSifter", "writable": true},
  "dblib_resources": {"ready": true, "missing": []},
  "ready": true,
  "diagnostics": [],
  "suggested_action": null
}
```

Possible statuses:

- `READY` — tests can run. For current mode, Altium 24.4.x must be confirmed responsive (`responding = true`).
- `BUSY` — Altium does not respond, another test has the lock, or a prior simulation is active.
- `RECOVERY_REQUIRED` — a completed, stale, or indeterminate `pending-run` marker requires `recover`.
- `ERROR` — missing X2.EXE, Altium not running in current mode, unknown/unsupported version, `responding = null`, ambiguous instances, inaccessible temporary folder, or missing DbLib resources.

If `ready != true`, the agent must stop the **entire** batch:

```text
Agent
  ↓
doctor --json
  ↓
READY?
  ├── YES → run tests
  └── NO  → mark all tests NOT_RUN and stop
```

`doctor` does not run electrical simulations. It checks available evidence: executable, version, process, responsiveness, lock, marker, and temporary directory. When a blocking Altium dialog cannot be reliably detected, doctor does not claim it has verified the absence of dialogs.

## Reference Batch Execution

```python
from batch_agent import run_batch

result = run_batch(
    [
        {"name": "R0009", "path": "temp/inputs/r0009.json"},
        {"name": "R0020", "path": "temp/inputs/r0020.json"},
    ],
    altium=r"C:\Program Files\Altium\AD24\X2.EXE",
)
```

By default, `altium_instance="current"` uses an already-running responsive Altium instance; the agent does not start it.

### Hidden Batch Mode

```python
result = run_batch(
    ["temp/inputs/r0009.json", "temp/inputs/r0020.json"],
    altium_instance="hidden",
    timeout=180,
    ready_timeout=120,
)
```

CLI alternative:

```powershell
python batch_agent.py temp/inputs/r0009.json temp/inputs/r0020.json `
  --altium-instance hidden --timeout 180 --ready-timeout 120
```

Lifecycle: `BATCH START → doctor(hidden) → START → READY → Test 1..N → STOP`. One hidden instance serves the entire batch; a new call creates a new instance. Termination is handled in `finally` and completed reports are preserved. `result.altium_session` contains Desktop, PID, creation time, ready time, executed commands, and termination reason. This does not change the `results[]` JSON report contract.

Hidden mode does **not** discover or select an already-running X2.EXE. Identity is based exclusively on its own PID and creation time. Other concurrent Altium instances do not make the hidden session `AMBIGUOUS`.

When Altium is unavailable:

```json
{
  "schema_version": "1.0",
  "status": "ERROR",
  "error": {
    "code": "ALTIUM_BLOCKED_BY_PENDING_RUN",
    "message": "Previous run is unresolved"
  },
  "summary": {"planned_runs": 12, "completed_runs": 0, "not_run": 12},
  "runs": [{"index": 0, "name": "R0009", "status": "NOT_RUN", "error": {"code": "ALTIUM_BLOCKED_BY_PENDING_RUN"}}]
}
```

No tests are launched, MixedSim is not called, and no fabricated measurements are produced.

## Validating a Report Before Accepting It

`batch_agent.run_batch()` never blindly trusts `status`. It validates:

- `schema_version == "1.0"`, allowed status (`PASS`/`FAIL`/`ERROR`), and allowed `validation_status` (`ASSERTIONS`/`NO_ASSERTIONS`).
- Presence of `results[]` and `coverage` with `coverage.status == "OK"`.
- Nonnegative counters and invariants `expected = completed + missing`, `completed = passed + failed + measured + errors`, `missing = not_run`.
- Full completion: `coverage.expected == coverage.completed`, `missing == 0`, `not_run == 0`, and `len(results) == coverage.expected`.
- Unique `result_id` and all required result fields: `result_id`, `run_id`, `kind`, `id`, `status`, `value`, `unit`, `error`.
- `kind` is `check`/`measurement`; `id` is a string or integer.
- Finite numeric `value` and null `error` for PASS/FAIL/MEASURED; error object containing code and message for ERROR; `value = null` for NOT_RUN.
- Coverage counters match actual result statuses.
- PASS has no FAIL/ERROR/NOT_RUN; FAIL has at least one FAIL and no ERROR/NOT_RUN.
- Top-level `error` is null on PASS/FAIL and an object on ERROR.

`REPORT_CONTRACT_INVALID` signals structural/status contradictions; `REPORT_INCOMPLETE` means missing results or incomplete coverage. An execution ERROR may still have complete coverage.

An incomplete report **cannot** become PASS:

```json
{
  "status": "ERROR",
  "error": {
    "code": "REPORT_INCOMPLETE",
    "message": "Expected 14 results, collected 5"
  },
  "summary": {"planned_runs": 1, "completed_runs": 0, "not_run": 0, "errors": 1},
  "runs": [{
    "status": "ERROR",
    "error": {
      "code": "REPORT_INCOMPLETE",
      "diagnostics": {"report": {"status": "PASS", "results": []}}
    }
  }]
}
```

An incompatible schema or structurally invalid result returns `REPORT_CONTRACT_INVALID`. The original report is stored at `error.diagnostics.report`. Missing values are never filled in automatically.

## System Errors During a Batch

If any test returns a systemic error, the remaining series is aborted. Systemic codes include:

`BUSY`, `TIMEOUT`, `SCRIPT_NOT_EXECUTED`, `ALTIUM_NOT_RUNNING`, `ALTIUM_STATE_UNKNOWN`, `ALTIUM_PENDING_RUN`, `HIDDEN_DESKTOP_UNAVAILABLE`, `HIDDEN_LAUNCH_FAILED`, `HIDDEN_READY_TIMEOUT`, `HIDDEN_BLOCKING_DIALOG`, `HIDDEN_PROCESS_LOST`, `HIDDEN_SCRIPT_NOT_ROUTED`, `HIDDEN_SCRIPT_TIMEOUT`, `HIDDEN_STATE_AMBIGUOUS`, and `HIDDEN_STOP_FAILED`.

Completed reports are retained, while unstarted tests are marked `NOT_RUN`. The reference `batch_agent.run_batch()` handles this automatically.

Each Spice Sifter invocation still acquires the interprocess lock because Altium state may change after preflight. Hidden batches hold that lock for the entire series. Nested acquisitions within the same owning thread are allowed; independent concurrent Spice Sifter runs are blocked.

## Diagnosing an Unfinished Run

Rather than an uninformative BUSY response, an unresolved run returns structured diagnostics:

```json
{
  "status": "ERROR",
  "error": {
    "code": "ALTIUM_PENDING_RUN",
    "message": "Previous simulation has not finished",
    "diagnostics": {
      "run_id": "a38f52",
      "run_dir": "C:\\Temp\\SpiceSifter\\a38f52",
      "altium_pid": 5678,
      "age_seconds": 120.5,
      "started_at": "2026-11-01T12:05:00Z",
      "timeout_at": "2026-11-01T12:07:00Z",
      "status_files": {
        "run_dir_exists": true,
        "started": true,
        "status_file": false,
        "status": null,
        "code": null,
        "status_source": null
      },
      "reason": "process_matches",
      "recovery_possible": false
    },
    "suggested_action": "Run: python spice_sifter.py doctor --json"
  }
}
```

## Recovery

```powershell
python spice_sifter.py recover
python spice_sifter.py recover --confirm-legacy-stopped
```

| Marker state | Action |
|---|---|
| Previous run completed (`STATUS=OK/ERROR`, including `collect.tmp`/`status.tmp`) | Remove marker |
| Altium exited or PID was reused | Remove stale marker |
| Legacy path-only marker with missing directory | Remove stale marker |
| Legacy marker with existing directory, no final status, and no `X2.EXE` processes | Remove after checking all processes |
| Legacy marker with running Altium or unknown process state | Refuse; preserve marker |
| Run still active | `REFUSED`; preserve marker |
| Indeterminate state | `UNKNOWN`; preserve marker and provide instructions |
| Corrupt marker | `UNKNOWN`; preserve marker with diagnostics |

`--confirm-legacy-stopped` explicitly acknowledges an old-format marker; it **does not bypass** process checks. If X2.EXE is running or its state is unknowable, recovery is refused.

Example of successful recovery:

```json
{
  "schema_version": "1.0",
  "status": "RECOVERED",
  "run_id": "a38f52",
  "state": "STALE",
  "reason": "Previous Altium process has exited",
  "ready": true,
  "removed": true
}
```

Refusal when a simulation remains active:

```json
{
  "schema_version": "1.0",
  "status": "REFUSED",
  "run_id": "a38f52",
  "state": "ACTIVE",
  "reason": "Previous Altium process is still running; the marker was kept",
  "ready": false,
  "suggested_action": "Wait for Altium to finish, then run recover again"
}
```

Unknown state (Altium alive, timeout expired, no final status):

```json
{
  "status": "UNKNOWN",
  "ready": false,
  "suggested_action": "Close or restart Altium, verify that no simulation is running, then run: python spice_sifter.py recover"
}
```

Marker age alone is never a reason to remove it; there is no unsafe `recover --force`.

## Recommended Procedure

1. Run `python spice_sifter.py doctor --json`.
2. If `RECOVERY_REQUIRED`, use `python spice_sifter.py recover`, then repeat doctor.
3. Start the batch.
4. On a systemic failure, stop, keep completed reports, and mark remaining tests `NOT_RUN`.
5. Consume `results[]` and `coverage` rather than inferring expected measurements indirectly.

For automated batches, `hidden` is recommended. Its preflight uses `python spice_sifter.py doctor --altium-instance hidden --json` and does not require a working Altium instance to be open.

See the [JSON Report Contract](REPORT_CONTRACT.md) and [Hidden Altium Instance](HIDDEN_ALTIUM.md).
