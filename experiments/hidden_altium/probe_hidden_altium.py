# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic: start one Altium on a fresh hidden desktop, observe, stop.

Used to learn Altium's real startup behaviour (splash, license dialogs,
long-lived process, window classes) before the two-instance stage.
"""
from __future__ import annotations

import argparse
import sys
import time

from hidden_session import (
    Desktop,
    launch_on_desktop,
    terminate_owned,
    windows_for_pids,
)
from support import Evidence, altium_exe, new_run_dir, x2_process_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()

    run_dir = new_run_dir("probe_hidden")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    evidence.log("start", altium=str(exe), seconds=args.seconds)
    desktop = Desktop.create()
    evidence.log("desktop", name=desktop.name)
    launched = launch_on_desktop(exe, [], desktop=desktop)
    evidence.log("launched", pid=launched.pid,
                 creation_time=launched.identity.creation_time,
                 command_line=launched.command_line, job=launched.extra)
    try:
        deadline = time.monotonic() + args.seconds
        last_signature = None
        while time.monotonic() < deadline:
            if not launched.process.is_alive():
                evidence.log("process_exited", exit_code=launched.process.exit_code(),
                             after=round(args.seconds - (deadline - time.monotonic()), 1))
                break
            try:
                hidden = desktop.windows_of([launched.pid])
            except Win32Error as exc:
                hidden = []
                evidence.log("enumeration_error", error=str(exc))
            titled = [(w.title, w.class_name, w.visible) for w in hidden if w.title.strip()]
            interactive = windows_for_pids(default_desktop_windows(), [launched.pid])
            signature = (len(hidden), tuple(sorted(titled)), len(interactive))
            if signature != last_signature:
                evidence.log("windows", hidden_count=len(hidden),
                             titled=titled, leaked_to_interactive=len(interactive),
                             elapsed=round(args.seconds - (deadline - time.monotonic()), 1))
                last_signature = signature
            time.sleep(2)
        evidence.log("snapshot", processes=x2_process_snapshot(str(exe)))
        stop = terminate_owned(launched, timeout=30)
        evidence.log("terminated", **stop)
        evidence.finish("probe_hidden", run_dir=str(run_dir), stop=stop)
    finally:
        if launched.process.is_alive():
            terminate_owned(launched, timeout=10)
        launched.close()
        desktop.close()
    print("artifacts:", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
