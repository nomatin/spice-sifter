# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Instance lifecycle helpers shared by the hidden-Altium stages."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import time

from hidden_session import (
    Desktop,
    LaunchedProcess,
    ProcessIdentity,
    Win32Error,
    default_desktop_windows,
    identify_process,
    launch_on_desktop,
    terminate_owned,
    windows_for_pids,
)
from support import Evidence, x2_process_snapshot

DIALOG_CLASSES = {"#32770", "TfrmDialog", "TMessageForm"}
BLOCKING_KEYWORDS = (
    "license", "licence", "sign in", "sign-in", "activation", "activate",
    "trial", "expired", "welcome", "first run", "error", "warning",
)


def summarize(windows) -> list[dict]:
    return [
        {
            "pid": w.pid,
            "hwnd": hex(w.hwnd),
            "title": w.title,
            "class": w.class_name,
            "visible": w.visible,
            "enabled": w.enabled,
        }
        for w in windows
        if w.title.strip() or w.class_name in DIALOG_CLASSES
    ]


def titles(windows) -> list[str]:
    return [w.title for w in windows if w.title.strip()]


def stable_titles(window_source, timeout: float = 20.0,
                  interval: float = 2.0) -> list[str]:
    """Sorted window titles after Altium's start-up panels stop changing."""
    previous = None
    deadline = time.monotonic() + timeout
    while True:
        current = sorted(titles(window_source()))
        if current == previous:
            return current
        previous = current
        if time.monotonic() >= deadline:
            return current
        time.sleep(interval)


def blocking_dialogs(windows, pids) -> list:
    found = []
    for window in windows:
        if window.pid not in pids or not window.visible:
            continue
        title = window.title.casefold()
        if window.class_name in DIALOG_CLASSES or any(
            keyword in title for keyword in BLOCKING_KEYWORDS
        ):
            found.append(window)
    return found


def main_windows(windows, pids) -> list:
    """Visible top-level frames with a non-empty title."""
    return [
        w for w in windows
        if w.pid in pids and w.visible and not w.owner and w.title.strip()
    ]


def desktop_windows(desktop: Desktop | None, pids) -> list:
    if desktop is None:
        return windows_for_pids(default_desktop_windows(), pids)
    return desktop.windows_of(pids)


def wait_for_main_window(desktop: Desktop | None, pids, timeout: float,
                         evidence: Evidence, label: str) -> dict:
    start = time.monotonic()
    last: list = []
    while time.monotonic() - start < timeout:
        try:
            windows = desktop_windows(desktop, pids)
        except Win32Error as exc:
            evidence.log("enumeration_error", label=label, error=str(exc))
            windows = []
        last = windows
        dialogs = blocking_dialogs(windows, pids)
        if dialogs:
            evidence.log("blocking_dialog_detected", label=label,
                         windows=[w.as_dict() for w in dialogs])
            return {"ready": False, "blocking": True, "windows": last,
                    "dialogs": dialogs, "elapsed": time.monotonic() - start}
        if main_windows(windows, pids):
            return {"ready": True, "blocking": False, "windows": last,
                    "dialogs": dialogs, "elapsed": time.monotonic() - start}
        time.sleep(1.0)
    return {"ready": False, "blocking": False, "windows": last, "dialogs": [],
            "elapsed": time.monotonic() - start}


@dataclass
class Reference:
    """The "working" Altium on the interactive desktop."""

    pid: int
    identity: ProcessIdentity
    owned: bool
    launched: LaunchedProcess | None = None

    def alive(self) -> bool:
        current = identify_process(self.pid)
        return bool(current and self.identity.matches(current))

    def windows(self):
        return windows_for_pids(default_desktop_windows(), [self.pid])

    def stop(self, evidence: Evidence, timeout: float = 60) -> dict | None:
        if not (self.owned and self.launched):
            return None
        result = terminate_owned(self.launched, timeout=timeout)
        evidence.log("reference_terminated", **result)
        return result


def ensure_reference(exe: Path, evidence: Evidence, start: bool,
                     timeout: float) -> Reference:
    existing = [
        item for item in x2_process_snapshot(str(exe))
        if item.get("matches_installation")
        and "-R" not in (item.get("command_line") or "")
    ]
    if existing:
        chosen = existing[0]
        identity = identify_process(int(chosen["pid"]))
        if identity is None:
            raise RuntimeError("Reference Altium disappeared while identifying it")
        evidence.log("reference_preexisting", snapshot=chosen,
                     pid=identity.pid, creation_time=identity.creation_time)
        return Reference(identity.pid, identity, owned=False)
    if not start:
        raise RuntimeError("No Altium is running and --start-reference was not given")

    launched = launch_on_desktop(exe, [], desktop=None)
    evidence.log("reference_launched", pid=launched.pid,
                 creation_time=launched.identity.creation_time,
                 command_line=launched.command_line, job=launched.extra)
    wait = wait_for_main_window(None, [launched.pid], timeout, evidence, "reference")
    evidence.log("reference_ready" if wait["ready"] else "reference_not_ready",
                 elapsed=round(wait["elapsed"], 1),
                 windows=summarize(wait["windows"]),
                 dialogs=[w.as_dict() for w in wait["dialogs"]])
    if not wait["ready"]:
        raise RuntimeError("Reference Altium did not become ready: " + json.dumps(
            summarize(wait["windows"]), ensure_ascii=False))
    return Reference(launched.pid, launched.identity, owned=True, launched=launched)


@dataclass
class HiddenInstance:
    desktop: Desktop
    launched: LaunchedProcess
    ready: dict = field(default_factory=dict)

    @classmethod
    def start(cls, exe: Path, evidence: Evidence, timeout: float,
              label: str = "hidden",
              desktop: Desktop | None = None) -> "HiddenInstance":
        desktop = desktop or Desktop.create()
        if not desktop.handle:
            raise RuntimeError("Desktop is closed")
        evidence.log("hidden_desktop", name=desktop.name,
                     handle=hex(desktop.handle), created=desktop.handle is not None)
        launched = launch_on_desktop(exe, [], desktop=desktop)
        evidence.log("hidden_launched", pid=launched.pid,
                     creation_time=launched.identity.creation_time,
                     desktop=desktop.name, command_line=launched.command_line,
                     job=launched.extra)
        ready = wait_for_main_window(desktop, [launched.pid], timeout, evidence, label)
        evidence.log("hidden_ready" if ready["ready"] else "hidden_not_ready",
                     elapsed=round(ready["elapsed"], 1),
                     windows=summarize(ready["windows"]),
                     dialogs=[w.as_dict() for w in ready["dialogs"]])
        if not ready["ready"]:
            raise RuntimeError("Hidden Altium did not initialise: " + json.dumps(
                summarize(ready["windows"]), ensure_ascii=False))
        return cls(desktop=desktop, launched=launched, ready=ready)

    @property
    def pid(self) -> int:
        return self.launched.pid

    @property
    def identity(self) -> ProcessIdentity:
        return self.launched.identity

    def alive(self) -> bool:
        return self.launched.process.is_alive() and self.identity.matches(
            identify_process(self.pid) or ProcessIdentity(0, "", 0)
        )

    def windows(self) -> list:
        return self.desktop.windows_of([self.pid])

    def stop(self, evidence: Evidence, timeout: float = 45,
             close_desktop: bool = True) -> dict:
        result = terminate_owned(self.launched, timeout=timeout)
        evidence.log("hidden_terminated", **result)
        self.launched.close()
        if close_desktop:
            self.desktop.close()
        return result


def send_script(exe: Path, script: Path, desktop: Desktop | None,
                procname: str = "RunExperiment",
                evidence: Evidence | None = None,
                label: str = "") -> LaunchedProcess:
    """Start Altium with the production ``-R`` argument on *desktop*."""
    raw = (
        '-RScriptingSystem:RunScriptFile(FileName="' + str(script)
        + '"|ProcName="' + procname + '")'
    )
    launched = launch_on_desktop(exe, desktop=desktop, raw_args=raw)
    if evidence is not None:
        evidence.log("script_launch", label=label, pid=launched.pid,
                     creation_time=launched.identity.creation_time,
                     desktop=desktop.name if desktop else "Default",
                     command_line=launched.command_line, job=launched.extra)
    return launched


def window_owner_summary(windows, nonce: str) -> dict:
    """Group windows that mention *nonce* by owning PID."""
    result: dict[int, list[str]] = {}
    needle = nonce.casefold()
    for window in windows:
        if needle in window.title.casefold():
            result.setdefault(window.pid, []).append(window.title)
    return result
