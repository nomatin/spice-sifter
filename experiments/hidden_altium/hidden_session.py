# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Win32 primitives for running one process on a private, invisible Desktop.

This module is part of the hidden-Altium proof of concept.  It is deliberately
self-contained and imports nothing from the main Spice Sifter package, so that
the experiment cannot change production behaviour.

The core idea: a new window station desktop is created with ``CreateDesktopW``
and a child process is started with ``STARTUPINFO.lpDesktop`` pointing at it.
Windows owned by that process are then enumerated with ``EnumDesktopWindows``
and attributed to PIDs with ``GetWindowThreadProcessId``.  Nothing here calls
``SwitchDesktop``, so the interactive desktop is never replaced.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence
import uuid

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------------------
# Access rights / constants
# ---------------------------------------------------------------------------

DESKTOP_READOBJECTS = 0x0001
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_ENUMERATE = 0x0040
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_ALL_ACCESS = 0x000F01FF

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

GW_OWNER = 4
GWL_STYLE = -16
WS_VISIBLE = 0x10000000
WS_DISABLED = 0x08000000
WM_CLOSE = 0x0010

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9


class Win32Error(OSError):
    """A Win32 call failed; carries the API name and GetLastError code."""

    def __init__(self, api: str, code: int | None = None):
        self.api = api
        self.code = ctypes.get_last_error() if code is None else code
        super().__init__(self.code, f"{api} failed (WinError {self.code})")


def _check(api: str, result):
    if not result:
        raise Win32Error(api)
    return result


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    @property
    def ticks(self) -> int:
        return (self.dwHighDateTime << 32) | self.dwLowDateTime


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# ---------------------------------------------------------------------------
# Prototypes
# ---------------------------------------------------------------------------

user32.CreateDesktopW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p
]
user32.CreateDesktopW.restype = wt.HANDLE
user32.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
user32.OpenDesktopW.restype = wt.HANDLE
user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
user32.OpenInputDesktop.restype = wt.HANDLE
user32.CloseDesktop.argtypes = [wt.HANDLE]
user32.CloseDesktop.restype = wt.BOOL
user32.GetThreadDesktop.argtypes = [wt.DWORD]
user32.GetThreadDesktop.restype = wt.HANDLE

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumDesktopWindows.argtypes = [wt.HANDLE, WNDENUMPROC, wt.LPARAM]
user32.EnumDesktopWindows.restype = wt.BOOL

user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowEnabled.argtypes = [wt.HWND]
user32.IsWindowEnabled.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
user32.GetWindow.restype = wt.HWND
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.GetProcessTimes.argtypes = [
    wt.HANDLE,
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
    ctypes.POINTER(FILETIME),
]
kernel32.GetProcessTimes.restype = wt.BOOL
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
kernel32.GetExitCodeProcess.restype = wt.BOOL
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.WaitForSingleObject.restype = wt.DWORD
kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateProcess.restype = wt.BOOL
kernel32.CreateProcessW.argtypes = [
    wt.LPCWSTR,
    wt.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.BOOL,
    wt.DWORD,
    ctypes.c_void_p,
    wt.LPCWSTR,
    ctypes.POINTER(STARTUPINFOW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wt.BOOL
kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
kernel32.CreateJobObjectW.restype = wt.HANDLE
kernel32.SetInformationJobObject.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD
]
kernel32.SetInformationJobObject.restype = wt.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
kernel32.AssignProcessToJobObject.restype = wt.BOOL
kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
kernel32.TerminateJobObject.restype = wt.BOOL

EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


def filetime_to_datetime(value: FILETIME) -> datetime:
    return EPOCH_1601 + timedelta(microseconds=value.ticks / 10)


def filetime_to_iso(value: FILETIME) -> str:
    return filetime_to_datetime(value).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessIdentity:
    """PID plus creation time, so a recycled PID can never be mistaken for ours."""

    pid: int
    creation_time: str
    creation_ticks: int

    def matches(self, other: "ProcessIdentity | None") -> bool:
        return bool(other) and other.pid == self.pid and (
            other.creation_ticks == self.creation_ticks
        )


class ProcessHandle:
    """A process handle owned by the experiment."""

    def __init__(self, handle: int, pid: int):
        self.handle = handle
        self.pid = int(pid)
        self.identity = self.read_identity()

    def read_identity(self) -> ProcessIdentity:
        creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        _check(
            "GetProcessTimes",
            kernel32.GetProcessTimes(
                self.handle,
                ctypes.byref(creation),
                ctypes.byref(exit_),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ),
        )
        return ProcessIdentity(self.pid, filetime_to_iso(creation), creation.ticks)

    def image(self) -> str:
        size = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        _check(
            "QueryFullProcessImageNameW",
            kernel32.QueryFullProcessImageNameW(
                self.handle, 0, buffer, ctypes.byref(size)
            ),
        )
        return buffer.value

    def is_alive(self) -> bool:
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def exit_code(self) -> int | None:
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return None
        return None if code.value == STILL_ACTIVE else int(code.value)

    def wait(self, timeout: float) -> bool:
        """Return ``True`` when the process exited within *timeout* seconds."""
        result = kernel32.WaitForSingleObject(self.handle, int(timeout * 1000))
        return result == 0

    def terminate(self, exit_code: int = 1) -> bool:
        return bool(kernel32.TerminateProcess(self.handle, exit_code))

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None  # type: ignore[assignment]

    def __enter__(self) -> "ProcessHandle":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def identify_process(pid: int) -> ProcessIdentity | None:
    """Open *pid* read-only and read its creation time; ``None`` if it is gone."""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid)
    )
    if not handle:
        return None
    try:
        return ProcessHandle(handle, pid).identity
    finally:
        kernel32.CloseHandle(handle)


def process_still_ours(identity: ProcessIdentity) -> bool:
    current = identify_process(identity.pid)
    return bool(current and identity.matches(current))


# ---------------------------------------------------------------------------
# Job object (guarantees our process tree can be closed reliably)
# ---------------------------------------------------------------------------


class ProcessJob:
    """Job object with KILL_ON_JOB_CLOSE.

    Only processes started by the experiment are assigned, so closing the job
    can never touch the user's Altium.
    """

    def __init__(self):
        self.handle = kernel32.CreateJobObjectW(None, None)
        self.error: str | None = None
        if not self.handle:
            self.error = str(Win32Error("CreateJobObjectW"))
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            self.handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            self.error = str(Win32Error("SetInformationJobObject"))

    def assign(self, process: ProcessHandle) -> bool:
        if not self.handle:
            return False
        return bool(kernel32.AssignProcessToJobObject(self.handle, process.handle))

    def terminate(self, exit_code: int = 1) -> bool:
        if not self.handle:
            return False
        return bool(kernel32.TerminateJobObject(self.handle, exit_code))

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Desktops and windows
# ---------------------------------------------------------------------------


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    enabled: bool
    style: int
    owner: int

    def as_dict(self) -> dict:
        return {
            "hwnd": hex(self.hwnd),
            "pid": self.pid,
            "title": self.title,
            "class": self.class_name,
            "visible": self.visible,
            "enabled": self.enabled,
            "style": hex(self.style & 0xFFFFFFFF),
            "owner": hex(self.owner) if self.owner else None,
        }


def _window_info(hwnd: int) -> WindowInfo:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(max(length, 0) + 2)
    user32.GetWindowTextW(hwnd, title, len(title))
    class_name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_name, 256)
    return WindowInfo(
        hwnd=int(hwnd),
        pid=int(pid.value),
        title=title.value,
        class_name=class_name.value,
        visible=bool(user32.IsWindowVisible(hwnd)),
        enabled=bool(user32.IsWindowEnabled(hwnd)),
        style=int(user32.GetWindowLongW(hwnd, GWL_STYLE)),
        owner=int(user32.GetWindow(hwnd, GW_OWNER) or 0),
    )


def _enumerate(handle: int) -> list[WindowInfo]:
    windows: list[WindowInfo] = []

    def callback(hwnd, _lparam):
        try:
            windows.append(_window_info(hwnd))
        except Exception:  # pragma: no cover - a window may vanish mid-enumeration
            pass
        return True

    proc = WNDENUMPROC(callback)
    ctypes.set_last_error(0)
    if not user32.EnumDesktopWindows(handle, proc, 0):
        # A desktop with no windows makes the call return 0.  Windows does not
        # guarantee that GetLastError is reset, so clear it first and treat a
        # remaining error as a real failure.
        code = ctypes.get_last_error()
        if code:
            raise Win32Error("EnumDesktopWindows", code)
    return windows


class Desktop:
    """A Win32 desktop created and owned by this experiment."""

    def __init__(self, name: str | None = None, handle: int | None = None):
        self.name = name or ("SpiceSifterHidden-" + uuid.uuid4().hex[:12])
        self.handle = handle
        self.closed = False

    @classmethod
    def create(cls, name: str | None = None) -> "Desktop":
        desktop = cls(name)
        desktop.handle = _check(
            "CreateDesktopW",
            user32.CreateDesktopW(
                desktop.name, None, None, 0, DESKTOP_ALL_ACCESS, None
            ),
        )
        return desktop

    @property
    def lp_desktop(self) -> str:
        """Value for ``STARTUPINFO.lpDesktop`` (window station ``winsta0``)."""
        return "winsta0\\" + self.name

    def windows(self) -> list[WindowInfo]:
        if not self.handle:
            raise RuntimeError("Desktop is closed")
        return _enumerate(self.handle)

    def windows_of(self, pids: Iterable[int]) -> list[WindowInfo]:
        wanted = {int(pid) for pid in pids}
        return [w for w in self.windows() if w.pid in wanted]

    def close(self) -> None:
        if self.handle and not self.closed:
            user32.CloseDesktop(self.handle)
            self.closed = True
            self.handle = None  # type: ignore[assignment]

    def __enter__(self) -> "Desktop":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _open_and_enumerate(handle: int) -> list[WindowInfo]:
    try:
        return _enumerate(handle)
    finally:
        user32.CloseDesktop(handle)


def default_desktop_windows() -> list[WindowInfo]:
    """Windows on the interactive (user-facing) desktop."""
    attempts = (
        ("OpenInputDesktop", lambda: user32.OpenInputDesktop(
            0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS)),
        ("OpenDesktopW(Default)", lambda: user32.OpenDesktopW(
            "Default", 0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS)),
        ("GetThreadDesktop", lambda: user32.GetThreadDesktop(
            kernel32.GetCurrentThreadId())),
    )
    errors = []
    for label, opener in attempts:
        handle = opener()
        if not handle:
            errors.append(f"{label}: {Win32Error(label).code}")
            continue
        try:
            windows = _enumerate(handle)
        except Win32Error as exc:
            errors.append(f"{label}: {exc.code}")
            continue
        finally:
            if label != "GetThreadDesktop":
                user32.CloseDesktop(handle)
        return windows
    raise Win32Error("OpenInputDesktop", errors=errors)  # type: ignore[call-arg]


def windows_for_pids(windows: Sequence[WindowInfo], pids: Iterable[int]) -> list[WindowInfo]:
    wanted = {int(pid) for pid in pids}
    return [w for w in windows if w.pid in wanted]


def post_close(windows: Sequence[WindowInfo]) -> int:
    """Ask every top-level window to close; returns the number posted."""
    sent = 0
    for window in windows:
        if user32.PostMessageW(window.hwnd, WM_CLOSE, 0, 0):
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# Launch / terminate
# ---------------------------------------------------------------------------


@dataclass
class LaunchedProcess:
    process: ProcessHandle
    desktop: Desktop | None
    command_line: str
    cwd: str
    started_at: str
    job: ProcessJob | None = None
    extra: dict = field(default_factory=dict)

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def identity(self) -> ProcessIdentity:
        return self.process.identity

    def close(self) -> None:
        self.process.close()
        if self.job:
            self.job.close()
            self.job = None

    def __enter__(self) -> "LaunchedProcess":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def launch_on_desktop(
    exe: str,
    args: Sequence[str] = (),
    desktop: Desktop | None = None,
    cwd: str | None = None,
    use_job: bool = True,
    raw_args: str | None = None,
) -> LaunchedProcess:
    """Start *exe* on *desktop*.

    ``desktop=None`` means "inherit the caller's desktop" (a normal launch).
    Hidden instances always pass an explicit :class:`Desktop`.  ``raw_args``
    is appended verbatim; it exists because Altium's ``-R`` argument contains
    nested quotes and must not be re-quoted.
    """
    exe = str(exe)
    if not os.path.isfile(exe):
        raise FileNotFoundError(exe)
    if raw_args is not None:
        command_line = subprocess_quote(exe) + " " + raw_args
    else:
        command_line = subprocess_quote(exe) + "".join(
            " " + subprocess_quote(a) for a in args
        )
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    if desktop is not None:
        startup.lpDesktop = desktop.lp_desktop
    info = PROCESS_INFORMATION()
    cwd = cwd or os.path.dirname(exe)
    started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    _check(
        "CreateProcessW",
        kernel32.CreateProcessW(
            exe,
            ctypes.create_unicode_buffer(command_line),
            None,
            None,
            False,
            0,
            None,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(info),
        ),
    )
    kernel32.CloseHandle(info.hThread)
    process = ProcessHandle(info.hProcess, info.dwProcessId)
    job = ProcessJob() if use_job else None
    job_error = job.error if job else None
    assigned = job.assign(process) if job else False
    if job and not assigned:
        job_error = job_error or str(Win32Error("AssignProcessToJobObject"))
    return LaunchedProcess(
        process=process,
        desktop=desktop,
        command_line=command_line,
        cwd=cwd,
        started_at=started_at,
        job=job,
        extra={"job_assigned": assigned, "job_error": job_error},
    )


def subprocess_quote(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def terminate_owned(launched: LaunchedProcess, timeout: float = 20.0,
                    exit_code: int = 1) -> dict:
    """Stop only the process tree started by this experiment.

    Order: close its windows, wait, then TerminateJobObject/TerminateProcess.
    The recorded PID + creation time is re-checked before any forceful step.
    """
    result = {
        "pid": launched.pid,
        "creation_time": launched.identity.creation_time,
        "graceful_windows": 0,
        "windows_after_close": [],
        "exited_after_close": None,
        "forced": False,
        "job_terminated": False,
        "exit_code": None,
        "identity_rechecked": None,
    }
    try:
        if launched.desktop is None:
            windows = windows_for_pids(default_desktop_windows(), [launched.pid])
        else:
            windows = launched.desktop.windows_of([launched.pid])
        result["graceful_windows"] = post_close(windows)
    except Win32Error as exc:
        result["close_error"] = str(exc)
    if launched.process.wait(timeout):
        result["exited_after_close"] = True
        result["exit_code"] = launched.process.exit_code()
        return result
    result["exited_after_close"] = False
    try:
        if launched.desktop is None:
            remaining = windows_for_pids(default_desktop_windows(), [launched.pid])
        else:
            remaining = launched.desktop.windows_of([launched.pid])
        result["windows_after_close"] = [
            w.as_dict() for w in remaining if w.visible and w.title.strip()
        ]
    except Win32Error:
        pass

    # Guard against PID reuse before force-terminating anything.
    rechecked = process_still_ours(launched.identity)
    result["identity_rechecked"] = rechecked
    if not rechecked:
        result["reason"] = "pid_no_longer_matches_creation_time"
        return result
    if launched.job:
        result["job_terminated"] = launched.job.terminate(exit_code)
    result["forced"] = launched.process.terminate(exit_code)
    launched.process.wait(10)
    result["exit_code"] = launched.process.exit_code()
    return result
