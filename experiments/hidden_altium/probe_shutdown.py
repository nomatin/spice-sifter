# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Diagnostic: find a graceful way to stop the hidden Altium instance.

Altium has no documented scripting ``Quit``.  This probe compares three
external shutdown messages on a private desktop and records whether the process
actually exits:

1. ``WM_CLOSE`` to every top-level window (already used by ``terminate_owned``).
2. ``WM_SYSCOMMAND/SC_CLOSE`` to every top-level window (what Alt+F4 sends).
3. ``WM_SYSCOMMAND/SC_CLOSE`` repeated after a short delay.

If none works, stage 4 documents forced termination of the experiment-owned
process tree with an explicit PID + creation-time guard.
"""
from __future__ import annotations

import argparse
import sys

from hidden_session import (
    Desktop,
    user32,
    launch_on_desktop,
    terminate_owned,
)
from instances import summarize, titles, wait_for_main_window
from support import Evidence, altium_exe, new_run_dir

WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060


def post(windows, message, wparam=0) -> int:
    sent = 0
    for window in windows:
        if user32.PostMessageW(window.hwnd, message, wparam, 0):
            sent += 1
    return sent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=8.0)
    args = parser.parse_args()

    run_dir = new_run_dir("probe_shutdown")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    desktop = Desktop.create()
    evidence.log("desktop", name=desktop.name)
    launched = launch_on_desktop(exe, [], desktop=desktop)
    evidence.log("launched", pid=launched.pid,
                 creation_time=launched.identity.creation_time)
    results = {}
    try:
        ready = wait_for_main_window(desktop, [launched.pid], 180, evidence, "hidden")
        evidence.log("ready", ready=ready["ready"], elapsed=round(ready["elapsed"], 1))
        if not ready["ready"]:
            raise RuntimeError("hidden Altium did not become ready")

        all_windows = desktop.windows_of([launched.pid])
        evidence.log("window_inventory",
                     windows=[w.as_dict() for w in all_windows],
                     classes=sorted({w.class_name for w in all_windows}))

        for attempt, (label, fn) in enumerate((
            ("wm_close_all", lambda: post(desktop.windows_of([launched.pid]), 0x0010)),
            ("sc_close_all", lambda: post(desktop.windows_of([launched.pid]),
                                          WM_SYSCOMMAND, SC_CLOSE)),
            ("sc_close_repeat", lambda: post(desktop.windows_of([launched.pid]),
                                             WM_SYSCOMMAND, SC_CLOSE)),
        ), start=1):
            sent = fn()
            exit_after = launched.process.wait(args.wait)
            windows = desktop.windows_of([launched.pid])
            results[label] = {
                "messages_sent": sent,
                "exited": exit_after,
                "exit_code": launched.process.exit_code(),
                "windows_after": summarize(windows),
            }
            evidence.log("shutdown_attempt", attempt=attempt, label=label,
                         sent=sent, exited=exit_after,
                         exit_code=launched.process.exit_code(),
                         windows=titles(windows))
            if exit_after:
                break
        results["graceful_available"] = any(
            item["exited"] for item in results.values() if isinstance(item, dict)
        )
    finally:
        if launched.process.is_alive():
            stop = terminate_owned(launched, timeout=5)
            evidence.log("forced_stop", **stop)
        launched.close()
        desktop.close()
        evidence.finish("probe_shutdown", results=results, run_dir=str(run_dir))

    print("\nSHUTDOWN PROBE")
    for name, item in results.items():
        print(f"  {name}: {item}")
    print("artifacts:", run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
