# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic: does a hidden Altium start/stop rewrite user settings?

Constraint: the experiment must not change global Altium settings.  The probe
snapshots ``%APPDATA%\\Altium`` and ``%LOCALAPPDATA%\\Altium`` (mtime + size,
caches excluded), starts and stops one hidden instance, and reports every
added/changed/removed file.  It does not decide whether such a write is
acceptable - it only makes the side effect measurable.
"""
from __future__ import annotations

import argparse
import sys

from hidden_session import Desktop, launch_on_desktop, terminate_owned
from instances import wait_for_main_window
from support import (
    Evidence,
    altium_exe,
    new_run_dir,
    profile_diff,
    profile_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    run_dir = new_run_dir("probe_profile")
    evidence = Evidence(run_dir)
    before = profile_snapshot()
    evidence.log("profile_before", roots=before["roots"], files=len(before["files"]))
    exe = altium_exe()
    desktop = Desktop.create()
    launched = launch_on_desktop(exe, [], desktop=desktop)
    ready_after = None
    try:
        ready = wait_for_main_window(desktop, [launched.pid], args.timeout,
                                     evidence, "hidden")
        evidence.log("ready", ready=ready["ready"],
                     elapsed=round(ready["elapsed"], 1))
        after_ready = profile_snapshot()
        ready_after = profile_diff(before, after_ready)
        evidence.log("profile_after_ready", **ready_after)
    finally:
        if launched.process.is_alive():
            stop = terminate_owned(launched, timeout=10)
            evidence.log("stopped", **stop)
        launched.close()
        desktop.close()
        after_stop = profile_snapshot()
        diff = profile_diff(before, after_stop)
        evidence.log("profile_after_stop", **diff)
        evidence.finish("probe_profile", after_ready=ready_after,
                        after_stop=diff, run_dir=str(run_dir))

    print("\nPROFILE PROBE")
    print(" after ready:", {k: ready_after.get(k) for k in (
        "before_files", "after_files", "added_count", "changed_count", "removed_count")})
    print(" changed sample:", ready_after.get("changed", [])[:10])
    print("artifacts:", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
