# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Stage 4: START -> READY -> SCRIPT -> SIMULATION -> STOP, three times.

Each cycle starts a fresh Altium on the same private desktop, waits for its
main window, executes a marker DelphiScript, runs the real DbLib SPICE test,
then stops exactly the process tree created by the cycle.

Stopping rules:
* the experiment-owned PID and creation time are re-checked before any forced
  step, so a recycled PID can never be hit;
* a graceful close is attempted first.  Altium 24.4 answers with a modal
  ``Confirm Save Locally...`` dialog on the hidden desktop, so the attempt is
  recorded and the process is then terminated through the job object (only the
  processes started by this experiment belong to it);
* the working Altium is never in that job and is never signalled.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import uuid

from hidden_session import (
    Desktop,
    default_desktop_windows,
    identify_process,
    terminate_owned,
    windows_for_pids,
)
from instances import (
    HiddenInstance,
    ensure_reference,
    send_script,
    stable_titles,
    titles,
    window_owner_summary,
)
from spice_flow import run_dblib_test
from stage2_script import render_script, wait_for_marker
from support import Evidence, altium_exe, new_run_dir, x2_process_snapshot


def wait_until_process_gone(pid: int, identity, timeout: float = 20.0) -> dict:
    """Wait for the exact process identity to disappear; detect PID reuse."""
    start = time.monotonic()
    while True:
        current = identify_process(pid)
        elapsed = time.monotonic() - start
        if current is None:
            return {"gone": True, "reused": False, "elapsed": round(elapsed, 2)}
        if not identity.matches(current):
            return {"gone": True, "reused": True, "elapsed": round(elapsed, 2)}
        if elapsed >= timeout:
            return {"gone": False, "reused": False, "elapsed": round(elapsed, 2)}
        time.sleep(0.5)


def run_probe_script(exe: Path, hidden: HiddenInstance, reference, evidence: Evidence,
                     base_dir: Path, label: str) -> dict:
    nonce = uuid.uuid4().hex[:10]
    work = base_dir / f"script_{label}_{nonce}"
    work.mkdir(parents=True, exist_ok=True)
    document = work / f"probe_{nonce}.txt"
    document.write_text("hidden lifecycle probe\nnonce=" + nonce + "\n",
                        encoding="ascii")
    marker = work / "marker.txt"
    script = work / "probe.pas"
    script.write_text(render_script(marker, document, nonce), encoding="ascii")

    working_before = sorted(titles(reference.windows()))
    launcher = send_script(exe, script, hidden.desktop, evidence=evidence,
                           label=label)
    try:
        fields, elapsed = wait_for_marker(marker, 120)
        time.sleep(1.5)
        hidden_windows = hidden.windows()
        working_windows = windows_for_pids(default_desktop_windows(),
                                           [reference.pid])
        hidden_owners = window_owner_summary(hidden_windows, nonce)
        working_owners = window_owner_summary(working_windows, nonce)
        working_after = sorted(titles(working_windows))
        result = {
            "nonce": nonce,
            "status": fields.get("STATUS"),
            "elapsed": round(elapsed, 1),
            "hidden_owner_pids": sorted(hidden_owners),
            "working_owner_pids": sorted(working_owners),
            "executed_by_hidden": hidden.pid in hidden_owners,
            "not_executed_by_working": not working_owners,
            "working_titles_unchanged": working_before == working_after,
        }
        evidence.log("script_stage", label=label, **result,
                     marker_fields=fields)
    finally:
        if launcher.process.is_alive():
            stop = terminate_owned(launcher, timeout=10)
            evidence.log("launcher_terminated", label=label, **stop)
        launcher.close()
    return result


def run_lifecycle(exe: Path, evidence: Evidence, data: dict, cycles: int,
                  timeout: float, start_reference: bool) -> tuple[dict, list]:
    reference = ensure_reference(exe, evidence, start_reference, timeout)
    desktop = Desktop.create()
    evidence.log("lifecycle_desktop", name=desktop.name, handle=hex(desktop.handle))
    working_initial = stable_titles(reference.windows)
    evidence.log("working_baseline", titles=working_initial)
    results: list[dict] = []
    try:
        for cycle in range(1, cycles + 1):
            label = f"cycle{cycle}"
            evidence.log("cycle_start", cycle=cycle)
            hidden = HiddenInstance.start(exe, evidence, timeout, label=label,
                                          desktop=desktop)
            cycle_info: dict = {
                "cycle": cycle,
                "pid": hidden.pid,
                "creation_time": hidden.identity.creation_time,
                "ready_elapsed": round(hidden.ready["elapsed"], 1),
                "ready": bool(hidden.ready["ready"]),
                "working_alive_before": reference.alive(),
            }
            working_during: list[str] = []
            try:
                cycle_info["script"] = run_probe_script(
                    exe, hidden, reference, evidence, evidence.run_dir, label
                )
                sim = run_dblib_test(
                    exe, hidden, reference, evidence, data, timeout,
                    label=label, artifact_subdir=label,
                )
                cycle_info["simulation"] = dict(sim["checks"])
                cycle_info["simulation_status"] = sim["report"].get("status")
                cycle_info["nsx"] = sim["nsx"]
                cycle_info["sdf"] = sim["sdf"]
                cycle_info["run_dir"] = sim["run_dir"]
                cycle_info["test_base"] = sim["base"]
                working_during = sorted(titles(reference.windows()))
                cycle_info["working_titles_added_during"] = sorted(
                    set(working_during) - set(working_initial)
                )
                cycle_info["working_titles_removed_during"] = sorted(
                    set(working_initial) - set(working_during)
                )
                cycle_info["working_titles_unchanged_during"] = (
                    working_during == working_initial
                )
            finally:
                stop = hidden.stop(evidence, timeout=10, close_desktop=False)
                cycle_info["stop"] = stop
                try:
                    cycle_info["windows_left_after_stop"] = len(
                        desktop.windows_of([hidden.pid])
                    )
                except Exception as exc:
                    cycle_info["windows_left_after_stop"] = None
                    evidence.log("post_stop_enumeration_failed", cycle=cycle,
                                 error=repr(exc))
                time.sleep(0.5)
                gone = wait_until_process_gone(hidden.pid, hidden.identity)
                cycle_info["pid_gone"] = gone["gone"]
                cycle_info["pid_reused"] = gone["reused"]
                cycle_info["pid_gone_elapsed"] = gone["elapsed"]
                others = [
                    item for item in x2_process_snapshot(str(exe))
                    if item.get("matches_installation")
                    and int(item["pid"]) != reference.pid
                ]
                cycle_info["leftover_x2_pids"] = [item["pid"] for item in others]
                evidence.log("cycle_stop", cycle=cycle, **cycle_info["stop"],
                             pid_gone=cycle_info["pid_gone"],
                             pid_reused=cycle_info["pid_reused"],
                             leftover_x2=cycle_info["leftover_x2_pids"])
            cycle_info["working_alive_after"] = reference.alive()
            working_after = sorted(titles(reference.windows()))
            cycle_info["working_titles_added_after"] = sorted(
                set(working_after) - set(working_initial)
            )
            cycle_info["working_titles_removed_after"] = sorted(
                set(working_initial) - set(working_after)
            )
            cycle_info["working_titles_unchanged_after"] = (
                working_after == working_initial
            )
            test_markers = [
                cycle_info.get("test_base") or "",
                cycle_info["script"].get("nonce") or "",
            ]
            watched = working_during + working_after
            cycle_info["working_test_windows"] = sorted({
                title for title in watched
                for marker in test_markers
                if marker and marker.casefold() in title.casefold()
            })
            evidence.log("cycle_result", **cycle_info)
            results.append(cycle_info)
    finally:
        desktop.close()
        reference.stop(evidence)

    checks: dict[str, bool] = {}
    checks["cycles_completed"] = len(results) == cycles
    checks["all_ready"] = all(item["ready"] for item in results)
    checks["unique_pids"] = len({item["pid"] for item in results}) == len(results)
    checks["unique_creation_times"] = (
        len({item["creation_time"] for item in results}) == len(results)
    )
    checks["all_scripts_executed_by_hidden"] = all(
        item["script"].get("status") == "OK"
        and item["script"].get("executed_by_hidden")
        and item["script"].get("not_executed_by_working")
        for item in results
    )
    checks["all_simulations_passed"] = all(
        all(item["simulation"].get(name) for name in (
            "script_status_ok", "nsx_generated", "nsx_verified",
            "mixed_sim_completed", "sdf_parsed", "validation_pass",
            "schematic_cleanup",
        ))
        for item in results
    )
    checks["all_pids_stopped"] = all(item["pid_gone"] for item in results)
    checks["no_pid_reuse"] = all(not item["pid_reused"] for item in results)
    checks["no_leftover_x2"] = all(
        not item["leftover_x2_pids"] for item in results
    )
    checks["identity_guard_used"] = all(
        item["stop"].get("exited_after_close")
        or item["stop"].get("identity_rechecked") is True
        for item in results
    )
    checks["working_alive_and_same_pid"] = all(
        item["working_alive_before"] and item["working_alive_after"]
        for item in results
    )
    checks["working_no_test_windows"] = all(
        not item["working_test_windows"] for item in results
    )
    # Kept as diagnostics, not pass criteria: Altium's Home Page/panel windows
    # are transient and may be re-created when any instance starts or exits.
    for item in results:
        item["working_ui_stable"] = bool(
            item["working_titles_unchanged_during"]
            and item["working_titles_unchanged_after"]
        )
    checks["graceful_unavailable_reported"] = all(
        item["stop"].get("exited_after_close") is False for item in results
    )
    return checks, results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--library",
                        default=r"C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib")
    parser.add_argument("--example", default=str(
        Path(__file__).resolve().parents[2] / "examples" / "dblib_resistor_r0009.json"
    ))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--no-start-reference", action="store_true")
    args = parser.parse_args()

    data = json.loads(Path(args.example).read_text(encoding="utf-8"))
    data.setdefault("component", {})["library"] = args.library

    run_dir = new_run_dir("stage4")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    evidence.log("start", altium=str(exe), cycles=args.cycles,
                 timeout=args.timeout)
    checks: dict = {}
    results: list = []
    try:
        checks, results = run_lifecycle(
            exe, evidence, data, args.cycles, args.timeout,
            not args.no_start_reference,
        )
        evidence.finish("stage4", checks=checks, cycles=results,
                        run_dir=str(run_dir))
    except Exception as exc:
        evidence.log("error", error=repr(exc))
        evidence.finish("stage4", checks=checks, cycles=results,
                        error=repr(exc), run_dir=str(run_dir))
        raise

    print("\nSTAGE 4 RESULT")
    for item in results:
        stop = item["stop"]
        print(f"  cycle {item['cycle']}: pid={item['pid']} "
              f"ready={item['ready']} script={item['script'].get('status')} "
              f"sim={item['simulation_status']} "
              f"graceful={stop.get('exited_after_close')} "
              f"job={stop.get('job_terminated')} gone={item['pid_gone']}")
    for name, value in checks.items():
        print(f"  {name}: {value}")
    print("artifacts:", run_dir)
    failed = [name for name, value in checks.items() if value is False]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
