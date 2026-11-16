# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Stage 1: can a second, independent Altium instance live on a hidden desktop?

The experiment never touches an Altium instance it did not start.  An already
running installation (the user's "working" Altium) is detected and only read.
If none is running, ``--start-reference`` starts one on the interactive desktop
so that the two-instance scenario can be reproduced on a clean machine.

A second process alone is *not* proof of independence: Altium may forward the
request to the already running instance.  The checks therefore combine the PID
with window ownership on both desktops.
"""
from __future__ import annotations

import argparse
import sys

from hidden_session import default_desktop_windows, windows_for_pids
from instances import (
    HiddenInstance,
    ensure_reference,
    main_windows,
    summarize,
)
from support import (
    Evidence,
    altium_exe,
    new_run_dir,
    profile_diff,
    profile_snapshot,
    x2_process_snapshot,
)


def run_stage1(exe, evidence: Evidence, timeout: float,
               start_reference: bool) -> dict:
    checks: dict[str, bool] = {}
    before_profile = profile_snapshot()
    reference = ensure_reference(exe, evidence, start_reference, timeout)
    checks["working_altium_running"] = reference.alive()

    hidden = HiddenInstance.start(exe, evidence, timeout, label="hidden")
    checks["create_hidden_desktop"] = bool(hidden.desktop.handle)
    checks["different_pid"] = hidden.pid != reference.pid

    try:
        checks["hidden_instance_initialised"] = bool(hidden.ready["ready"])
        checks["no_blocking_dialogs"] = not hidden.ready["blocking"]
        checks["hidden_windows_on_hidden_desktop"] = bool(
            main_windows(hidden.ready["windows"], [hidden.pid])
        )

        # Longevity: a forwarding launcher exits quickly; a real instance stays.
        exited = hidden.launched.process.wait(30)
        evidence.log("longevity_30s", pid=hidden.pid, alive=not exited,
                     exit_code=hidden.launched.process.exit_code())
        checks["second_instance_long_lived"] = not exited

        # Window ownership on both desktops, sampled at the same moment.
        hidden_windows = hidden.windows()
        interactive_windows = default_desktop_windows()
        leaked = windows_for_pids(interactive_windows, [hidden.pid])
        reference_windows = windows_for_pids(interactive_windows, [reference.pid])
        evidence.log("ownership_snapshot", hidden_pid=hidden.pid,
                     hidden_windows=summarize(hidden_windows),
                     leaked_to_interactive=[w.as_dict() for w in leaked],
                     reference_windows=summarize(reference_windows))
        checks["test_windows_on_hidden_only"] = bool(hidden_windows) and not leaked
        checks["no_test_windows_on_working_desktop"] = not leaked

        checks["working_pid_unchanged"] = reference.alive()
        checks["working_windows_unchanged"] = bool(reference_windows)

        processes = x2_process_snapshot(str(exe))
        evidence.log("x2_process_snapshot", processes=processes)
        long_lived = [
            item for item in processes
            if item.get("matches_installation")
            and "-R" not in (item.get("command_line") or "")
        ]
        checks["process_table_has_both"] = len(long_lived) >= 2

        diff = profile_diff(before_profile, profile_snapshot())
        evidence.log("profile_side_effects", **diff)

        stop = hidden.stop(evidence)
        checks["hidden_terminated"] = not hidden.launched.process.is_alive()
        checks["working_instance_survived"] = reference.alive()
        evidence.log("hidden_stop_evidence", **stop)
    finally:
        if hidden.launched.process.is_alive():
            hidden.stop(evidence, timeout=10)
        elif not hidden.desktop.closed:
            hidden.desktop.close()
        reference.stop(evidence)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=240.0,
                        help="seconds to wait for each Altium instance to initialise")
    parser.add_argument("--no-start-reference", action="store_true",
                        help="fail instead of starting a stand-in working Altium")
    args = parser.parse_args()

    run_dir = new_run_dir("stage1")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    evidence.log("start", altium=str(exe), timeout=args.timeout,
                 desktop_mode="CreateDesktopW")
    checks: dict = {}
    try:
        checks = run_stage1(exe, evidence, args.timeout, not args.no_start_reference)
        evidence.finish("stage1", checks=checks, run_dir=str(run_dir))
    except Exception as exc:
        evidence.log("error", error=repr(exc))
        evidence.finish("stage1", checks=checks, error=repr(exc), run_dir=str(run_dir))
        raise

    print("\nSTAGE 1 RESULT")
    for name, value in checks.items():
        print(f"  {name}: {value}")
    print("artifacts:", run_dir)
    failed = [name for name, value in checks.items() if value is False]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
