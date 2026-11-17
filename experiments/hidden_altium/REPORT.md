# Experiment Report: Hidden Altium on a Separate Windows Desktop

**Repository:** `nomatin/spice-sifter` (dev; original commit `f8c4599`)  
**Date:** November 16, 2026  
**Scope:** standalone proof of concept under `experiments/hidden_altium/`, with no production integration at the time  
**Objective:** run automated SPICE tests in a second independent Altium Designer instance on an invisible Win32 Desktop while preserving the working Altium instance.

## 1. Summary

**All required checks PASSED** with Altium Designer 24.4.1.13, Windows 11 Pro 10.0.26200, and Python 3.14.5.

The key result is that `X2.EXE -RScriptingSystem:RunScriptFile(...)` is **Desktop-routed**. A `-R` launcher created with `STARTUPINFO.lpDesktop = winsta0\SpiceSifterHidden-<id>` delivers the script to the hidden instance, without `SwitchDesktop`.

| Check | Result |
|---|---|
| Hidden Desktop creation | PASS |
| Second X2.EXE launch | PASS |
| Independent processes | PASS |
| No test windows on working Desktop | PASS |
| Targeted DelphiScript routing | PASS |
| NSX generation | PASS |
| MixedSim execution | PASS |
| Safe restart | PASS |

The reference Altium remained running and never opened test documents. The hidden Altium was restarted three times and successfully ran scripts/simulations each time.

**Qualification:** The user's Altium was not open during the experiment. The "working instance" was therefore an equivalent **reference instance started by the experiment**, from the same installation and user session. Code to detect an already-running user instance was implemented without terminating it, but that exact condition was not tested.

## 2. Method

Only Win32 APIs accessed through Python `ctypes` were used:

1. `CreateDesktopW(..., DESKTOP_ALL_ACCESS)` creates a dedicated Desktop within `winsta0`.
2. `CreateProcessW` with `STARTUPINFO.lpDesktop` starts X2.EXE on it; no `SwitchDesktop`.
3. `EnumDesktopWindows` and `GetWindowThreadProcessId` prove window-to-PID/Desktop ownership.
4. `OpenProcess` and `GetProcessTimes` establish PID plus creation time. Identity is rechecked before any forced termination.
5. `CreateJobObject` and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` restrict termination to experiment-owned processes.
6. A nonce-named document, visible only on the executor's Desktop, proves script routing. The marker also stores Altium's reported active document path.

Production modules `runtime_state.py`, `spice_sifter.py`, `dblib_runner.py`, and `dblib_resources/*.pas` were not modified. Existing `prepare_run`, `build_report`, `preflight_component`, and `verify_netlist` were reused.

## 3. Experiment Stages

### Stage 0 — Win32 Plumbing (Without Altium)

Script: `stage0_plumbing.py`; run `stage0_20261116_173049_45970d`.

A PowerShell WinForms `SpiceSifterDesktopProbe` window (PID **60424**) appeared on the hidden Desktop, with **0 of 374** working-Desktop windows belonging to that PID. Normal `WM_CLOSE` exited with code 0. This verified `CreateProcessW` and `EnumDesktopWindows` independently of Altium.

### Stage 1 — Two Independent Instances

Script: `stage1_launch.py`; run `stage1_20261116_173513_eb9f97`.

| Metric | Observation |
|---|---|
| Working/reference Altium | PID **16400**; `2026-11-16T17:35:14.617Z` |
| Hidden Altium | PID **41420**; `2026-11-16T17:35:49.113Z` |
| Hidden Desktop | `SpiceSifterHidden-5d3fbacdf27b` |
| Initialization | 34.5 s / 34.4 s |
| Hidden windows | 121+ on hidden Desktop |
| Hidden PID windows on working Desktop | 0 |
| Blocking dialogs | Not detected |
| Second instance after 30 s | Alive |
| Process table | Both PIDs, `responding=true` |

`test_windows_on_hidden_only`, `no_test_windows_on_working_desktop`, `working_pid_unchanged`, and `working_instance_survived` all passed.

### Stage 2 — Targeted DelphiScript Routing

Script: `stage2_script.py`; run `stage2_20261116_174114_4225bc`. Reference PID **45740**, hidden PID **21696**.

| Launcher Desktop | Launcher PID | Executor PID | Proof |
|---|---|---|---|
| Working | **33156** | **45740** | `probe_dd1fd7bb6f.txt` visible only on working Desktop |
| Hidden | **32260** | **21696** | `probe_38b5465358.txt` visible only on hidden Desktop |

The launchers exited normally with code 0 in 14.1 s and 5.8 s; no new long-lived X2.EXE processes remained. Markers included `STATUS=OK`, `DOC_OPEN=YES`, and the active document path written by Altium.

**Result:** `-R` does not globally target the first instance. It targets the instance associated with the launcher's Desktop.

### Stage 3 — Live DbLib SPICE Test

Script: `stage3_spice.py`; run `stage3_20261116_175425_0a27f1`. Reference PID **41268**, hidden PID **31932**, launcher **49372**.

Input: `examples/dblib_resistor_r0009.json`, library `MY_LIB.DbLib`, the resistor table, component `R0009`, and fixture `resistor_dc`. Run directory: `temp/dblib_runs/20261116_205536_dd7ac5`.

| Check | Observation |
|---|---|
| Script STATUS | OK |
| All 9 `dblib_runner` stages | PASS (library_lookup through schematic_cleanup) |
| NSX | Altium generated, 1472 bytes |
| `verify_netlist` | All checks true; model `MY_LIB_R_TCR` |
| SDF | MixedSim generated, 9291 bytes, parsed successfully |
| Measurement | `v(dut_p) = -1.0004 V` within `[-1.06, -0.94]`: PASS |
| Cleanup | `schematic_saved_after_cleanup=1` |
| Test documents in working Altium | None |

NSX/SDF appeared after `ADVSIM:RunSimulator` in the hidden run directory. No `Test_20261116_205536_dd7ac5.PrjPcb`, NSX, or SDF window appeared on the working Desktop.

### Stage 4 — Three Lifecycle Cycles

Script: `stage4_lifecycle.py --cycles 3`; run `stage4_20261116_183819_03c005`. Reference PID **59480** remained running. Hidden Desktop: `SpiceSifterHidden-dc9b2881276e`, reused for all three cycles.

| Cycle | Hidden PID | Creation UTC | READY | SCRIPT | SIMULATION | STOP |
|---|---|---|---|---|---|---|
| 1 | **31580** | `18:38:58.298644Z` | 30.4 s | Hidden executor: OK | NSX+SDF, PASS | Forced; PID gone |
| 2 | **53060** | `18:39:59.795122Z` | 16.1 s | Hidden executor: OK | NSX+SDF, PASS | Forced; PID gone |
| 3 | **45444** | `18:40:49.991622Z` | 36.5 s | Hidden executor: OK | NSX+SDF, PASS | Forced; PID gone |

All PIDs and creation times were distinct. Before termination, `identity_rechecked=true`. Processes disappeared within 0–2 s; no hidden windows or unrelated X2.EXE remained. The working instance's identity, window set, and documents were stable (`working_ui_stable=true` for all three cycles).

## 4. Technical Limitations

### 4.1 Graceful Shutdown

Sending `WM_CLOSE` to hidden top-level windows did not terminate Altium 24.4; it displayed a modal WPF dialog:

```
Confirm Save Locally for (22) Modified Documents
```

In stage 4, the dialog listed 25, 3, and 25 modified documents. `WM_SYSCOMMAND / SC_CLOSE` (Alt+F4) produced the same dialog. Its `HwndWrapper[...]` controls did not respond to `BM_CLICK`. Keyboard emulation/`SendInput` would require switching Desktop and risk confirming unwanted saves. IClient provides `IsQuitting` but no documented `Quit`; DelphiScript lacked `Windows.pas` functions.

The PoC waited 10 s, rechecked PID + creation time, and called `TerminateJobObject` for **only experiment-owned processes**. The resulting exit code **1** was reported explicitly.

For integration, the runner must either terminate its own Job Object or close all documents and handle any remaining dialog. Blind dialog confirmation is unsafe.

### 4.2 Licensing

Two simultaneous Altium instances from one installation were allowed on this machine. Single-seat licenses may block a second instance. The PoC detected candidate `#32770`/`HwndWrapper` dialogs containing `license`, `sign in`, or `activation`; it did not bypass them.

### 4.3 Shared Profile

The hidden instance shared `%APPDATA%` and `%LOCALAPPDATA%`. `probe_profile.py` (PID **22688**) showed 167 files both before and after the run. Five existing service logs changed: `AltiumDesignerExtension.log`, `CEF_debug.log`, `DXP_Shutdown.log`, `DXP_SplashScreen.log`, and `DXP_Startup.log`. Installed Libraries, settings, and license files were unchanged. Forced termination prevented saving pending settings; behavior might differ between versions.

### 4.4 Other Limitations

- Only an interactive `winsta0` session was tested, not a locked screen, disconnected/reconnected RDP, or another user session.
- At most two persistent Altium instances were tested; temporary `-R` launchers exited within seconds.
- Cold startup was 30–45 s, warm startup approximately 16 s; batch timeouts must account for startup.
- PoC used neither production locks nor pending markers. Concurrent runs with production Spice Sifter are not recommended.

## 5. Commands Used

```powershell
cd experiments\hidden_altium

python stage0_plumbing.py
python stage1_launch.py --timeout 240
python stage2_script.py --timeout 180
python stage3_spice.py --library "C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib" `
  --example "..\..\examples\dblib_resistor_r0009.json" --timeout 300
python stage4_lifecycle.py --cycles 3 --timeout 300

python probe_hidden_altium.py --seconds 150
python probe_shutdown.py --wait 8
python probe_profile.py --timeout 180

# Check for unrelated processes
Get-Process X2 -ErrorAction SilentlyContinue | Select-Object Id,StartTime
```

Full diagnostic messages/window lists are in `artifacts/<stage>_<timestamp>/evidence.jsonl`, with `result.json` beside them. Scripts and generated NSX/SDF were copied into `altium_run/` (stage 3) and `cycleN/` (stage 4).

## 6. Not Tested

- An actual user-owned working Altium session (an equivalent reference instance was used).
- License, sign-in, or document-recovery dialogs, since they did not occur in this environment.
- Long-duration operation or dozens of restart cycles; only three cycles were required and tested.
- Locked screen and RDP scenarios.

## 7. Integration Feasibility (Historical Plan)

Integration is feasible without a second Altium installation, changing global settings, using `SwitchDesktop`, or `taskkill`. The original plan was:

1. Add `hidden_instance.py` based on PoC `hidden_session.py`: Desktop handling, `lpDesktop` launch, PID/creation-time identity, Job Object, window enumeration, and dialog detection.
2. Add an optional launcher to `dblib_runner._launch` and `spice_sifter.run_altium`, using existing `subprocess.Popen` by default and hidden `CreateProcessW` when requested; preserve `-R` syntax and `ProcName`.
3. Introduce `--altium-instance {current,hidden}`, defaulting to `current`; hidden mode should verify its own PID/readiness instead of requiring the user's Altium.
4. Use one hidden instance per batch (`START → READY → SCRIPT… → STOP`) with a cold-start timeout of at least 90 s and Job Object termination.
5. Extend doctor with `hidden_desktop.supported`, `hidden_instance.state`, and `license_two_instances_allowed`, only after actual success (never by bypassing license dialogs).
6. Test existing acceptance scripts in hidden mode and verify that working PID/windows are unaffected; test targeted nonce-document delivery.
7. Never auto-confirm dialogs, kill all X2.EXE processes, call `SwitchDesktop`, modify Installed Libraries, or rewrite locks/markers without approval.

Initial estimates: 350–450 lines for the module, 30–50 lines at launcher integration points, and CLI/doctor additions; no change to schematic generation, SDF parsing, or reports. **This proposal is historical; hidden-instance support was subsequently implemented in 0.7.0.**

## Appendix A — PID Summary

| Run | Role | PID | Creation time (UTC) |
|---|---|---|---|
| stage0 | PowerShell probe | 60424 | `17:30:49.627Z` |
| stage1 | Working Altium | 16400 | `17:35:14.617Z` |
| stage1 | Hidden Altium | 41420 | `17:35:49.113Z` |
| stage2 | Working Altium | 45740 | `17:41:14.916Z` |
| stage2 | Hidden Altium | 21696 | `17:41:51.477Z` |
| stage2 | Launcher, working route | 33156 | `17:42:27.894Z` |
| stage2 | Launcher, hidden route | 32260 | `17:42:45.058Z` |
| stage3 | Working Altium | 41268 | `17:54:26.358Z` |
| stage3 | Hidden Altium | 31932 | `17:55:01.843Z` |
| stage3 | Launcher | 49372 | `17:55:36.284Z` |
| stage4 | Working Altium | 59480 | `18:38:20.754Z` |
| stage4 | Hidden, cycle 1 | 31580 | `18:38:58.298Z` |
| stage4 | Hidden, cycle 2 | 53060 | `18:39:59.795Z` |
| stage4 | Hidden, cycle 3 | 45444 | `18:40:49.991Z` |
| probe_shutdown | Hidden Altium | 25952 | `17:52:02.747Z` |
| probe_profile | Hidden Altium | 22688 | `18:44:29.174Z` |

## Appendix B — Artifacts

```
experiments/hidden_altium/artifacts/
  stage0_20261116_173049_45970d/
  stage1_20261116_173513_eb9f97/
  stage2_20261116_174114_4225bc/
  stage3_20261116_175425_0a27f1/    (altium_run: status.txt, experiment.pas, NSX, SDF)
  stage4_20261116_183819_03c005/    (cycle1..cycle3: status.txt, experiment.pas, NSX, SDF)
  probe_hidden_20261116_173157_3d1919/
  probe_shutdown_20261116_175202_d5bda2/
  probe_profile_20261116_184429_f632d6/

temp/dblib_runs/20261116_205536_dd7ac5/   (stage 3, production folder)
temp/dblib_runs/20261116_213935_603c76/   (stage 4, cycle 1)
temp/dblib_runs/20261116_214023_57d721/   (stage 4, cycle 2)
temp/dblib_runs/20261116_214134_48572f/   (stage 4, cycle 3)
```
