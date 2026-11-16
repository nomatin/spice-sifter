# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Stage 0: prove the Win32 plumbing without Altium.

A small PowerShell WinForms window is started on a private desktop.  The test
verifies that the window exists on that desktop, that it is absent from the
interactive desktop, and that the process can be stopped safely.  This is a
control for stage 1: a negative Altium result can then be attributed to Altium,
not to the desktop plumbing.
"""
from __future__ import annotations

import sys

from hidden_session import (
    Desktop,
    Win32Error,
    default_desktop_windows,
    launch_on_desktop,
    terminate_owned,
)
from support import Evidence, new_run_dir, wait_until

WINDOW_TITLE = "SpiceSifterDesktopProbe"
PS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$f = New-Object System.Windows.Forms.Form; "
    f"$f.Text = '{WINDOW_TITLE}'; "
    "$f.Width = 320; $f.Height = 200; "
    "$f.ShowInTaskbar = $false; "
    "[System.Windows.Forms.Application]::Run($f)"
)


def main() -> int:
    run_dir = new_run_dir("stage0")
    evidence = Evidence(run_dir)
    evidence.log("start", desktop="private", probe="powershell-winforms")
    desktop = Desktop.create()
    evidence.log("desktop_created", name=desktop.name, handle=hex(desktop.handle))
    launched = None
    checks = {}
    try:
        launched = launch_on_desktop(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            ["-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT],
            desktop=desktop,
        )
        evidence.log(
            "process_launched",
            pid=launched.pid,
            creation_time=launched.identity.creation_time,
            command_line=launched.command_line,
            job=launched.extra,
        )

        def window_present():
            return desktop.windows_of([launched.pid])

        windows, elapsed, timeline = wait_until(window_present, 30)
        evidence.log("hidden_windows", elapsed=elapsed,
                     windows=[w.as_dict() for w in windows], timeline=timeline[-5:])
        checks["create_hidden_desktop"] = bool(desktop.handle)
        checks["launch_process_on_desktop"] = launched.process.is_alive()
        checks["process_alive_after_launch"] = launched.process.is_alive()
        checks["window_on_hidden_desktop"] = bool(windows)

        visible_titles = [w.as_dict() for w in windows if w.visible and w.title]
        checks["titled_window_on_hidden_desktop"] = bool(visible_titles)

        interactive = default_desktop_windows()
        leaked = [w.as_dict() for w in interactive if w.pid == launched.pid]
        checks["no_window_on_interactive_desktop"] = not leaked
        evidence.log("interactive_desktop_check", leaked=leaked,
                     interactive_window_count=len(interactive))

        stop = terminate_owned(launched, timeout=10)
        evidence.log("terminated", **stop)
        checks["terminated"] = not launched.process.is_alive()
        evidence.log("result", checks=checks)
        evidence.finish("stage0", checks=checks, stop=stop, run_dir=str(run_dir))
    except Win32Error as exc:
        checks["error"] = str(exc)
        evidence.log("win32_error", error=str(exc), api=exc.api, code=exc.code)
    finally:
        if launched is not None:
            if launched.process.is_alive():
                terminate_owned(launched, timeout=5)
            launched.close()
        desktop.close()

    failed = [name for name, value in checks.items() if value is False]
    if "error" in checks:
        failed.append("error")
    print("\nSTAGE 0 RESULT: " + ("PASS" if not failed else "FAIL"))
    for name, value in checks.items():
        print(f"  {name}: {value}")
    print("artifacts:", run_dir)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
