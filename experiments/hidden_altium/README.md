# Hidden Altium — Proof of Concept

This experiment checks whether a second, independent Altium Designer instance can run on a dedicated **hidden Win32 Desktop** and receive targeted DelphiScript and MixedSim commands without interfering with the user's working Altium instance.

**Result: all checks PASS** on Altium Designer 24.4.1.13 (Windows 11 Pro, one interactive user session). See the detailed [REPORT.md](REPORT.md).

> This is an isolated proof of concept. The production Spice Sifter code does not import it; it does not modify existing schematic generation, simulation, locking, or reporting mechanisms.

## Requirements

- Windows 10/11 and Altium Designer 24.4.x.
- Python 3.11+ (verified with 3.14.5); only the standard library and `ctypes`.
- Interactive user session, the same session used by the working Altium.
- For stages 3/4, an existing `.DbLib` and a component JSON example (defaults: `examples/dblib_resistor_r0009.json` and `C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib`).

## Files

| File | Purpose |
|---|---|
| `hidden_session.py` | Win32 primitives: `CreateDesktopW`, `CreateProcessW` with `STARTUPINFO.lpDesktop`, `EnumDesktopWindows`, `GetWindowThreadProcessId`, PID + creation time, Job Objects |
| `support.py` | Evidence log (`evidence.jsonl`), `X2.EXE` process snapshots, Altium profile snapshots, DelphiScript literals |
| `instances.py` | Reference (working) and hidden instances, readiness checks, targeted `-R` scripts, stable window snapshots |
| `spice_flow.py` | Production DbLib → NSX → MixedSim → SDF workflow executed on the selected instance |
| `stage0_plumbing.py` | Win32 plumbing verification on a test GUI process without Altium |
| `stage1_launch.py` | Two `X2.EXE` instances; hidden windows restricted to the hidden Desktop |
| `stage2_script.py` | Targeted DelphiScript delivery from both working and hidden Desktops |
| `stage3_spice.py` | Actual DbLib SPICE test in hidden Altium |
| `stage4_lifecycle.py` | Three `START → READY → SCRIPT → SIMULATION → STOP` cycles |
| `probe_hidden_altium.py` | Diagnostic probe of a single hidden startup |
| `probe_shutdown.py` | Graceful shutdown diagnostics |
| `probe_profile.py` | Checks modifications in Altium's `%APPDATA%`/`%LOCALAPPDATA%` |
| `artifacts/` | Machine-readable evidence (not committed to Git) |

## Reproducing the Experiment

Run every command from `experiments/hidden_altium`:

```powershell
# 0. Win32 plumbing (Altium not required)
python stage0_plumbing.py

# 1. Hidden Desktop and second X2.EXE
python stage1_launch.py --timeout 240

# 2. Targeted DelphiScript: working Desktop first, then hidden Desktop
python stage2_script.py --timeout 180

# 3. Actual DbLib SPICE test in hidden instance
python stage3_spice.py `
  --library "C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib" `
  --example "..\..\examples\dblib_resistor_r0009.json" `
  --timeout 300

# 4. Lifecycle: three cycles
python stage4_lifecycle.py --cycles 3 --timeout 300

# Optional diagnostics
python probe_hidden_altium.py --seconds 120
python probe_shutdown.py --wait 8
python probe_profile.py --timeout 180
```

### Altium Instance Lifecycle

- If Altium is already running, the experiment treats that process as the **reference instance** and only reads its PID, creation time, and windows. It never terminates that instance.
- Otherwise, the experiment starts a visible reference instance on the normal Desktop and stops it at the end; this process belongs to the experiment.
- The hidden instance is always created by the experiment and managed through a Job Object with `KILL_ON_JOB_CLOSE`.
- `--no-start-reference` forbids starting a new reference instance; an already-running Altium is then required.

The hidden Desktop is created in `winsta0` under a unique `SpiceSifterHidden-<id>` name. `SwitchDesktop` is not called. On completion, the Desktop is closed and experiment-owned processes are terminated.

## Results and Artifacts

Each stage writes `artifacts/<stage>_<timestamp>/` with:

- `evidence.jsonl` — step-by-step evidence: PIDs, creation times, windows, script routing, NSX/SDF, dialogs, and termination reasons.
- `result.json` — final checks.
- `altium_run/` or `cycleN/` — copies of `status.txt`, `experiment.pas`, NSX, and SDF.

Exit code 0 means all checks PASS; 1 indicates at least one FAIL.

## Proof-of-Concept Limitations

- Graceful `WM_CLOSE` on Altium 24.4 triggers the `Confirm Save Locally for (N) Modified Documents` modal dialog. The experiment records it and terminates **only its own process** through its Job Object after rechecking PID and creation time.
- Each hidden instance requires a license slot; two simultaneous Altium instances were permitted on the tested machine.
- `-R` routing uses the Desktop. The command is executed by the instance whose Desktop is provided in `STARTUPINFO.lpDesktop`.
- The experiment does **not** use production locks or pending-run markers. Do not run it concurrently with ordinary Spice Sifter sessions.
- User profile configuration is not changed; Altium may append service logs in `%LOCALAPPDATA%\Altium` (see `probe_profile.py`).
