# Hidden Altium Instance

Technical details of isolated Altium Designer execution in Spice Sifter 0.6. See the [User Guide](User-Guide.md) for usage and [Agent Integration](AGENT_INTEGRATION.md) for batch workflows.

By default, Spice Sifter uses an already-running Altium Designer instance (`--altium-instance current`). The `hidden` mode starts its **own** Altium instance on a separate, invisible Win32 Desktop. It neither restarts the user's Altium, sends it scripts, nor changes any of its open documents. It does not use `SwitchDesktop`, `SendInput`, `taskkill /IM X2.EXE`, or global process termination.

```powershell
# One test: Spice Sifter starts and stops its own hidden Altium
python spice_sifter.py examples/dblib_resistor_r0009.json --altium-instance hidden

# Batch: a single hidden instance for this batch, fresh on every invocation
python batch_agent.py tests_list\a.json tests_list\b.json `
  --altium-instance hidden --timeout 180 --ready-timeout 120
```

`--ready-timeout` (default 120 s) limits instance startup and readiness checking only; `--timeout` is still the timeout for a single test. A cold Altium 24.4 startup takes approximately 30–45 s; values below 90 s are not recommended.

## Implementation

- Spice Sifter creates a Desktop named `SpiceSifterHidden-<id>` via `CreateDesktopW` and starts Altium with `STARTUPINFO.lpDesktop` pointing to it.
- Instance identity is the combination of **PID and process creation time**. Both are checked before every script and again before termination.
- The managed processes belong to a dedicated Job Object configured with `KILL_ON_JOB_CLOSE`; only that job is terminated. Normal `WM_CLOSE` is avoided because Altium 24.4 shows a `Confirm Save Locally` dialog.
- Creation and configuration of the Job Object are checked. The main process and every `-R` launcher are created suspended (`CREATE_SUSPENDED`), added to the Job Object, and only then resumed. If assignment fails, the session returns `HIDDEN_LAUNCH_FAILED` and immediately kills the newly created process, so an unmanaged Altium instance cannot become `READY`.
- Before starting a single hidden test, Spice Sifter checks earlier hidden-session state under the shared lock. `ACTIVE`, `UNKNOWN`, or `CORRUPTED` prevents startup; only a provably `STALE` record is removed.
- The batch lock belongs to a specific thread. Nested acquisition by that same thread is allowed; acquisition by another thread returns `BUSY`. The interprocess `altium.lock` remains in effect.
- A failure to stop the hidden instance makes the overall test status `ERROR` (`HIDDEN_STOP_FAILED` or a more specific code). Completed results are kept. `hidden-session.json` is not removed until termination of the managed process is verified; `doctor` and `recover` handle the resulting state.
- Every `X2.EXE -RScriptingSystem:RunScriptFile(...)` invocation, including the additional `collect.pas`, is launched on the hidden Desktop. The `STARTUPINFO` Desktop selection directs `-R` commands to the intended Altium instance.
- Readiness is established by a control DelphiScript, not by inspecting the window. Its document must appear on the hidden Desktop and belong to the hidden instance PID.
- The instance is stopped after the batch. Each subsequent batch creates a fresh instance; there is no persistent daemon or shared instance between batches.

## Requirements and Limitations

- Altium Designer **24.4.x**, Windows 10/11, and an interactive user session are required.
- An available license slot is required. The user and hidden Altium instances may run simultaneously. If the license allows only one instance, the license dialog produces `HIDDEN_BLOCKING_DIALOG`; dialogs are never bypassed.
- Creating a Desktop from a locked or disconnected RDP session has not been verified. `doctor` reports `HIDDEN_DESKTOP_UNAVAILABLE` with diagnostics.
- The hidden Altium shares the user's `%APPDATA%` and `%LOCALAPPDATA%` profile. Installed Libraries and library files are not modified.

## Diagnostics and Recovery

```powershell
python spice_sifter.py doctor --altium-instance hidden --json
python spice_sifter.py recover
```

In hidden mode, `doctor` does not require an already-running Altium and does not start it. It checks the application version, ability to create a hidden Desktop, temporary directory, lock, `pending-run`, recorded hidden session, and its PID. The `hidden_desktop`, `hidden_instance`, `hidden`, and `license_two_instances_allowed` JSON fields augment the existing response.

`recover` **never terminates processes**. A hidden-session record is removed only when both PID and creation time prove the process ended. Active or unverifiable sessions produce `REFUSED` / `RECOVERY_REQUIRED`. If the controller exits unexpectedly, its `KILL_ON_JOB_CLOSE` Job Object terminates only the managed Altium process. Session state is recorded in `%TEMP%\SpiceSifter\hidden-session.json`; the audit log is `%TEMP%\SpiceSifter\hidden-sessions.log.jsonl`.

Hidden-mode error codes: `HIDDEN_DESKTOP_UNAVAILABLE`, `HIDDEN_LAUNCH_FAILED`, `HIDDEN_READY_TIMEOUT`, `HIDDEN_BLOCKING_DIALOG`, `HIDDEN_PROCESS_LOST`, `HIDDEN_SCRIPT_NOT_ROUTED`, `HIDDEN_SCRIPT_TIMEOUT`, `HIDDEN_STATE_AMBIGUOUS`, `HIDDEN_STOP_FAILED`. These are not collapsed into a generic `SIM_NOT_RUN`; they stop the remaining tests in a batch with `NOT_RUN`.

## What Spice Sifter Never Does to Your Working Altium Instance

It does not close or restart it, terminate processes globally by name, send it scripts, call `SwitchDesktop`, save the user's documents, or modify Installed Libraries.
