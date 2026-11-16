# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""A managed Altium Designer instance on a private, invisible Win32 desktop.

The module is the production port of the proven PoC in
``experiments/hidden_altium``.  It owns one ``X2.EXE`` process:

* a desktop is created with ``CreateDesktopW`` and Altium is started with
  ``STARTUPINFO.lpDesktop`` pointing at it;
* PID **and** creation time identify the process, so a recycled PID can never be
  mistaken for the managed instance;
* all processes started here belong to one Job Object with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, so cleanup can never reach the user's
  Altium;
* ``-R`` script launchers are created on the same desktop.  The PoC proved that
  Altium routes ``-R`` by desktop, which is the addressing mechanism;
* readiness is not inferred from a process or a window alone: a control
  DelphiScript must be executed by the managed instance and its document window
  must appear on the private desktop only.

Nothing here calls ``SwitchDesktop``, ``SendInput`` or ``taskkill``, and no
window is ever asked to close: Altium 24.4 answers ``WM_CLOSE`` with a modal
``Confirm Save Locally`` dialog, so termination is done through the owned job
object only.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import time
import uuid

# ---------------------------------------------------------------------------
# Error codes (also used by runners, batch agent and doctor)
# ---------------------------------------------------------------------------

HIDDEN_DESKTOP_UNAVAILABLE = "HIDDEN_DESKTOP_UNAVAILABLE"
HIDDEN_LAUNCH_FAILED = "HIDDEN_LAUNCH_FAILED"
HIDDEN_READY_TIMEOUT = "HIDDEN_READY_TIMEOUT"
HIDDEN_BLOCKING_DIALOG = "HIDDEN_BLOCKING_DIALOG"
HIDDEN_PROCESS_LOST = "HIDDEN_PROCESS_LOST"
HIDDEN_SCRIPT_NOT_ROUTED = "HIDDEN_SCRIPT_NOT_ROUTED"
HIDDEN_SCRIPT_TIMEOUT = "HIDDEN_SCRIPT_TIMEOUT"
HIDDEN_STATE_AMBIGUOUS = "HIDDEN_STATE_AMBIGUOUS"
HIDDEN_STOP_FAILED = "HIDDEN_STOP_FAILED"

HIDDEN_ERROR_CODES = {
    HIDDEN_DESKTOP_UNAVAILABLE,
    HIDDEN_LAUNCH_FAILED,
    HIDDEN_READY_TIMEOUT,
    HIDDEN_BLOCKING_DIALOG,
    HIDDEN_PROCESS_LOST,
    HIDDEN_SCRIPT_NOT_ROUTED,
    HIDDEN_SCRIPT_TIMEOUT,
    HIDDEN_STATE_AMBIGUOUS,
    HIDDEN_STOP_FAILED,
}

DEFAULT_READY_TIMEOUT = 120.0
DEFAULT_PROBE_TIMEOUT = 60.0
DEFAULT_STOP_TIMEOUT = 30.0
DESKTOP_PREFIX = "SpiceSifterHidden-"
DIALOG_CLASSES = {"#32770", "TfrmDialog", "TMessageForm", "HwndWrapper"}
BLOCKING_KEYWORDS = (
    "license",
    "licence",
    "sign in",
    "sign-in",
    "activation",
    "activate",
    "trial",
    "expired",
    "welcome",
    "first run",
    "error",
)


class HiddenAltiumError(Exception):
    """A hidden-session failure with a specific, non-masked code."""

    def __init__(self, code, message, diagnostics=None, suggested_action=None):
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or {}
        self.suggested_action = suggested_action
        super().__init__(message)

    def as_error(self):
        payload = {"code": self.code, "message": self.message}
        if self.diagnostics:
            payload["diagnostics"] = self.diagnostics
        if self.suggested_action:
            payload["suggested_action"] = self.suggested_action
        return payload


# ---------------------------------------------------------------------------
# Win32 bindings
# ---------------------------------------------------------------------------

DESKTOP_READOBJECTS = 0x0001
DESKTOP_ENUMERATE = 0x0040
DESKTOP_ALL_ACCESS = 0x000F01FF

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

GW_OWNER = 4
GWL_STYLE = -16

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)

try:  # pragma: no cover - availability is environment specific
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    WINDOWS_AVAILABLE = True
except (AttributeError, OSError):  # pragma: no cover - non-Windows
    user32 = None
    kernel32 = None
    WINDOWS_AVAILABLE = False


class Win32Error(OSError):
    """A Win32 call failed; carries the API name, code and context."""

    def __init__(self, api, code=None, errors=None):
        self.api = api
        self.code = ctypes.get_last_error() if code is None else code
        self.errors = errors
        detail = "WinError %s" % self.code
        if errors:
            detail += " (" + "; ".join(str(item) for item in errors) + ")"
        super().__init__(self.code, "%s failed (%s)" % (api, detail))


def _check(api, result):
    if not result:
        raise Win32Error(api)
    return result


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
    def ticks(self):
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


def _bind_prototypes():
    if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
        return
    user32.CreateDesktopW.argtypes = [
        wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD,
        ctypes.c_void_p,
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

    wndenumproc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    user32.EnumDesktopWindows.argtypes = [wt.HANDLE, wndenumproc, wt.LPARAM]
    user32.EnumDesktopWindows.restype = wt.BOOL
    user32.EnumWindows.argtypes = [wndenumproc, wt.LPARAM]
    user32.EnumWindows.restype = wt.BOOL
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
    user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
    user32.GetWindow.restype = wt.HWND
    user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long

    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
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
        wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wt.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wt.BOOL
    kernel32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.TerminateJobObject.restype = wt.BOOL


_bind_prototypes()


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat().replace("+00:00", "Z")


def filetime_to_datetime(value):
    return EPOCH_1601 + timedelta(microseconds=value.ticks / 10)


def filetime_to_iso(value):
    return filetime_to_datetime(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ProcessIdentity:
    """PID plus creation time, so a recycled PID can never be mistaken for ours."""

    pid: int
    creation_time: str
    creation_ticks: int = 0

    def matches(self, other):
        return bool(other) and other.pid == self.pid and (
            other.creation_ticks == self.creation_ticks
        )

    @property
    def is_complete(self):
        return bool(self.pid) and bool(self.creation_time)


class ProcessHandle:
    """A process handle owned by this session."""

    def __init__(self, handle, pid):
        self.handle = handle
        self.pid = int(pid)
        self.identity = self.read_identity()

    def read_identity(self):
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

    def image(self):
        size = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        _check(
            "QueryFullProcessImageNameW",
            kernel32.QueryFullProcessImageNameW(
                self.handle, 0, buffer, ctypes.byref(size)
            ),
        )
        return buffer.value

    def is_alive(self):
        if not self.handle:
            return False
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def exit_code(self):
        if not self.handle:
            return None
        code = wt.DWORD(0)
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return None
        return None if code.value == STILL_ACTIVE else int(code.value)

    def wait(self, timeout):
        if not self.handle:
            return True
        return kernel32.WaitForSingleObject(self.handle, int(timeout * 1000)) == 0

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


def identify_process(pid):
    """Open *pid* read-only and read its creation time; ``None`` if it is gone."""
    if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not handle:
        return None
    try:
        return ProcessHandle(handle, pid).identity
    finally:
        kernel32.CloseHandle(handle)


def process_still_ours(identity):
    if identity is None or not identity.is_complete:
        return False
    current = identify_process(identity.pid)
    return bool(current and identity.matches(current))


class ProcessJob:
    """Job object with ``KILL_ON_JOB_CLOSE`` for this session's process tree."""

    def __init__(self):
        self.handle = kernel32.CreateJobObjectW(None, None) if WINDOWS_AVAILABLE else None
        self.error = None
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

    def assign(self, process):
        if not self.handle:
            return False
        return bool(kernel32.AssignProcessToJobObject(self.handle, process.handle))

    def terminate(self, exit_code=1):
        if not self.handle:
            return False
        return bool(kernel32.TerminateJobObject(self.handle, exit_code))

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None


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

    def as_dict(self):
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


def _window_info(hwnd):
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


def _enumerate(handle):
    windows = []

    def callback(hwnd, _lparam):
        try:
            windows.append(_window_info(hwnd))
        except Exception:  # pragma: no cover - a window may vanish mid-enumeration
            pass
        return True

    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(callback)
    ctypes.set_last_error(0)
    if not user32.EnumDesktopWindows(handle, proc, 0):
        code = ctypes.get_last_error()
        if code:
            raise Win32Error("EnumDesktopWindows", code)
    return windows


class Desktop:
    """A Win32 desktop created and owned by Spice Sifter."""

    def __init__(self, name=None, handle=None):
        self.name = name or (DESKTOP_PREFIX + uuid.uuid4().hex[:12])
        self.handle = handle
        self.closed = False

    @classmethod
    def create(cls, name=None):
        if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
            raise HiddenAltiumError(
                HIDDEN_DESKTOP_UNAVAILABLE,
                "Win32 desktop APIs are not available on this platform",
            )
        desktop = cls(name)
        try:
            desktop.handle = _check(
                "CreateDesktopW",
                user32.CreateDesktopW(
                    desktop.name, None, None, 0, DESKTOP_ALL_ACCESS, None
                ),
            )
        except Win32Error as exc:
            raise HiddenAltiumError(
                HIDDEN_DESKTOP_UNAVAILABLE,
                "Cannot create the hidden desktop: " + str(exc),
                {"desktop": desktop.name, "winerror": exc.code},
                "Run doctor from the same interactive session as Altium; "
                "locked or disconnected sessions may not allow a new desktop.",
            ) from exc
        return desktop

    @property
    def lp_desktop(self):
        return "winsta0\\" + self.name

    def windows(self):
        if not self.handle:
            raise RuntimeError("Desktop is closed")
        return _enumerate(self.handle)

    def windows_of(self, pids):
        wanted = {int(pid) for pid in pids}
        return [w for w in self.windows() if w.pid in wanted]

    def close(self):
        if self.handle and not self.closed:
            user32.CloseDesktop(self.handle)
            self.closed = True
            self.handle = None


def default_desktop_windows():
    """Windows on the interactive (user-facing) desktop."""
    if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
        return []
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
            errors.append("%s: %s" % (label, Win32Error(label).code))
            continue
        try:
            return _enumerate(handle)
        except Win32Error as exc:
            errors.append("%s: %s" % (label, exc.code))
        finally:
            if label != "GetThreadDesktop":
                user32.CloseDesktop(handle)
    raise Win32Error("OpenInputDesktop", code=0, errors=errors)


def windows_for_pids(windows, pids):
    wanted = {int(pid) for pid in pids}
    return [w for w in windows if w.pid in wanted]


def titles(windows):
    return [w.title for w in windows if w.title.strip()]


def blocking_dialogs(windows, pids):
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


def main_windows(windows, pids):
    return [
        w for w in windows
        if w.pid in pids and w.visible and not w.owner and w.title.strip()
    ]


def window_owner_summary(windows, nonce):
    """Group windows that mention *nonce* by owning PID."""
    result = {}
    needle = nonce.casefold()
    for window in windows:
        if needle in window.title.casefold():
            result.setdefault(window.pid, []).append(window.title)
    return result


@dataclass
class LaunchedProcess:
    process: ProcessHandle
    desktop: Desktop | None
    command_line: str
    cwd: str
    started_at: str
    job: ProcessJob | None = None
    job_owned: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def pid(self):
        return self.process.pid

    @property
    def identity(self):
        return self.process.identity

    def close(self):
        self.process.close()
        if self.job is not None and self.job_owned:
            self.job.close()
        self.job = None


def subprocess_quote(value):
    return '"' + str(value).replace('"', '\\"') + '"'


def launch_on_desktop(exe, args=(), desktop=None, cwd=None, use_job=True,
                      raw_args=None, job=None):
    """Start *exe* on *desktop*.

    ``desktop=None`` inherits the caller's desktop.  ``raw_args`` is appended
    verbatim because Altium's ``-R`` argument contains nested quotes.  ``job``
    assigns the process to an existing session job instead of creating a new
    one; such a job stays owned by the caller.
    """
    exe = str(exe)
    if not Path(exe).is_file():
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
    started_at = iso_now()
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
    job_owned = job is None
    job_error = None
    if job is None and use_job:
        job = ProcessJob()
        job_error = job.error
    assigned = False
    if job is not None:
        assigned = job.assign(process)
        if not assigned:
            job_error = job_error or str(Win32Error("AssignProcessToJobObject"))
    return LaunchedProcess(
        process=process,
        desktop=desktop,
        command_line=command_line,
        cwd=cwd,
        started_at=started_at,
        job=job,
        job_owned=job_owned,
        extra={"job_assigned": assigned, "job_error": job_error},
    )


def probe_hidden_desktop():
    """Create and immediately close one desktop; no Altium is started."""
    if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
        return {
            "supported": False,
            "probe_created": False,
            "error": "Win32 desktop APIs are not available",
        }
    name = "SpiceSifterDoctor-" + uuid.uuid4().hex[:10]
    try:
        desktop = Desktop.create(name)
    except HiddenAltiumError as exc:
        return {
            "supported": False,
            "probe_created": False,
            "desktop": name,
            "error": exc.message,
            "winerror": exc.diagnostics.get("winerror"),
        }
    try:
        return {
            "supported": True,
            "probe_created": True,
            "desktop": desktop.name,
            "error": None,
        }
    finally:
        desktop.close()


# ---------------------------------------------------------------------------
# Readiness probe script
# ---------------------------------------------------------------------------

PROBE_SCRIPT = """procedure RunSpiceSifterProbe;
var S, D;
begin
  S := TStringList.Create;
  try
    S.Add('STATUS=STARTED');
    S.Add('NONCE={nonce}');
    S.SaveToFile({marker});
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', {document});
    RunProcess('WorkspaceManager:OpenObject');
    D := Client.GetDocumentByPath({document});
    if D = Nil then
      S.Add('DOC_OPEN=NO')
    else begin
      S.Add('DOC_OPEN=YES');
      S.Add('ACTIVE_DOC=' + D.FileName);
    end;
    S.Add('STATUS=OK');
  except
    S.Add('STATUS=ERROR');
  end;
  S.SaveToFile({marker});
  S.Free;
end;
"""


def _pas_literal(value):
    return "'" + "".join(
        ("''" if c == "'" else c) if ord(c) < 128 else "'#" + str(ord(c)) + "'"
        for c in str(value)
    ) + "'"


@dataclass
class ScriptRun:
    """One ``-R`` launcher process created for the managed instance."""

    proc_name: str
    script: str
    launched: LaunchedProcess
    started_at: str
    elapsed: float = 0.0

    @property
    def pid(self):
        return self.launched.pid

    @property
    def command_line(self):
        return self.launched.command_line

    def alive(self):
        return self.launched.process.is_alive()

    def exit_code(self):
        return self.launched.process.exit_code()

    def wait_exit(self, timeout):
        return self.launched.process.wait(timeout)

    def close(self):
        self.launched.close()


class HiddenAltiumSession:
    """Lifecycle of one Altium instance on a private desktop.

    The session is also the script launcher passed to runners, so every ``-R``
    command is created on the session's desktop and assigned to its job.
    """

    def __init__(self, altium_path, ready_timeout=DEFAULT_READY_TIMEOUT,
                 probe_script=True, probe_timeout=DEFAULT_PROBE_TIMEOUT,
                 stop_timeout=DEFAULT_STOP_TIMEOUT, verbose=False,
                 state_root=None, desktop_name=None):
        self.altium_path = str(altium_path)
        self.ready_timeout = float(ready_timeout)
        self.probe_script = bool(probe_script)
        self.probe_timeout = float(probe_timeout)
        self.stop_timeout = float(stop_timeout)
        self.verbose = verbose
        self.session_id = uuid.uuid4().hex[:12]
        self.desktop_name = desktop_name or (DESKTOP_PREFIX + self.session_id)
        self.state_root = Path(state_root) if state_root else self._default_state_root()

        self.desktop = None
        self.job = None
        self._job_created = False
        self.process = None
        self.identity = None
        self.started_at = None
        self.ready_at = None
        self.ready_elapsed = None
        self._ready = False
        self._started = False
        self._stopped = False
        self.stop_result = None
        self.commands = []
        self.launchers = []
        self.events = []
        self.working_before = []
        self.working_after = []
        self.reason = None

    # -- state directory ---------------------------------------------------

    @staticmethod
    def _default_state_root():
        try:
            from runtime_state import state_root
            return Path(state_root())
        except Exception:  # pragma: no cover - defensive
            return Path(tempfile.gettempdir()) / "SpiceSifter"

    def _event(self, kind, **payload):
        event = {"at": iso_now(), "kind": kind, "session_id": self.session_id, **payload}
        self.events.append(event)
        if self.verbose:
            print("[hidden] %s: %s" % (kind, payload), file=__import__("sys").stderr)
        self._append_log(event)
        return event

    def _append_log(self, event):
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
            path = self.state_root / "hidden-sessions.log.jsonl"
            import json
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass

    def _state_snapshot(self, state):
        stop = self.stop_result or {}
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "state": state,
            "mode": "hidden",
            "controller_pid": os.getpid(),
            "altium_path": self.altium_path,
            "desktop": self.desktop_name,
            "pid": self.identity.pid if self.identity else None,
            "creation_time": self.identity.creation_time if self.identity else None,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "ready_elapsed": self.ready_elapsed,
            "job_object": bool(self._job_created),
            "owned": True,
            "working_altium_count": len(self.working_before),
            "reason": self.reason or stop.get("reason"),
            "updated_at": iso_now(),
        }

    def _write_state(self, state, extra=None):
        try:
            from runtime_state import write_hidden_session
            data = self._state_snapshot(state)
            if extra:
                data.update(extra)
            write_hidden_session(self.state_root, data)
        except Exception as exc:  # pragma: no cover - diagnostics only
            self._event("state_write_failed", error=repr(exc))

    def _clear_state(self):
        try:
            from runtime_state import clear_hidden_session
            clear_hidden_session(self.state_root)
        except Exception:  # pragma: no cover - defensive
            pass

    def _write_evidence(self):
        """Keep launch-proven facts (e.g. two licence slots) after a clean stop."""
        if not self._ready:
            return
        try:
            from runtime_state import write_hidden_evidence
            write_hidden_evidence(self.state_root, {
                "license_two_instances_allowed": bool(len(self.working_before) > 0),
                "working_altium_count": len(self.working_before),
                "last_session_id": self.session_id,
                "last_pid": self.pid,
                "last_desktop": self.desktop_name,
                "last_ready_at": self.ready_at,
                "last_ready_elapsed": self.ready_elapsed,
            })
        except Exception:  # pragma: no cover - diagnostics only
            pass

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        """Create the desktop, the job object and the managed Altium process."""
        if self._started:
            return self
        if not WINDOWS_AVAILABLE:  # pragma: no cover - non-Windows
            raise HiddenAltiumError(
                HIDDEN_DESKTOP_UNAVAILABLE,
                "Win32 desktop APIs are not available on this platform",
            )
        if not Path(self.altium_path).is_file():
            raise HiddenAltiumError(
                HIDDEN_LAUNCH_FAILED,
                "Altium X2.EXE not found: " + self.altium_path,
            )
        self.working_before = self._altium_snapshot()
        self.desktop = Desktop.create(self.desktop_name)
        self._event("desktop_created", desktop=self.desktop.name,
                    handle=hex(int(self.desktop.handle or 0)),
                    working_altium=self.working_before)
        self.job = ProcessJob()
        self._job_created = bool(self.job.handle)
        if not self.job.handle:
            self._cleanup_resources()
            raise HiddenAltiumError(
                HIDDEN_LAUNCH_FAILED,
                "Cannot create the job object: " + str(self.job.error),
            )
        if self.job.error:
            self._event("job_warning", error=self.job.error)
        try:
            launched = launch_on_desktop(
                self.altium_path, desktop=self.desktop, use_job=False, job=self.job
            )
        except (OSError, Win32Error) as exc:
            self._cleanup_resources()
            raise HiddenAltiumError(
                HIDDEN_LAUNCH_FAILED,
                "Cannot start Altium on the hidden desktop: " + str(exc),
                {"desktop": self.desktop_name, "altium": self.altium_path},
            ) from exc
        self.process = launched.process
        self.identity = launched.identity
        self.started_at = iso_now()
        self._started = True
        self._event(
            "altium_started",
            pid=self.identity.pid,
            creation_time=self.identity.creation_time,
            desktop=self.desktop_name,
            command_line=launched.command_line,
            job=launched.extra,
        )
        self._write_state("STARTING")
        return self

    def wait_ready(self, timeout=None):
        """Wait for a proven-ready instance and verify ``-R`` routing."""
        if not self._started:
            raise HiddenAltiumError(
                HIDDEN_STATE_AMBIGUOUS, "The hidden session has not been started"
            )
        timeout = self.ready_timeout if timeout is None else float(timeout)
        started = time.monotonic()
        deadline = started + timeout
        windows = []
        while True:
            if not self.process.is_alive():
                raise self._fail(
                    HIDDEN_PROCESS_LOST,
                    "The managed Altium process exited during start-up",
                    windows=len(windows),
                )
            try:
                windows = self.windows()
            except (Win32Error, RuntimeError) as exc:
                windows = []
                self._event("enumeration_error", error=str(exc))
            dialogs = blocking_dialogs(windows, {self.pid})
            if dialogs:
                raise self._fail(
                    HIDDEN_BLOCKING_DIALOG,
                    "A blocking dialog appeared in the hidden Altium instance",
                    dialogs=[w.as_dict() for w in dialogs],
                    windows=len(windows),
                )
            if main_windows(windows, {self.pid}):
                break
            if time.monotonic() >= deadline:
                raise self._fail(
                    HIDDEN_READY_TIMEOUT,
                    "The hidden Altium did not become ready within %.0f s" % timeout,
                    timeout=timeout,
                    windows=[w.as_dict() for w in windows if w.title.strip()],
                )
            time.sleep(1.0)

        self._event("main_window_detected", elapsed=round(time.monotonic() - started, 1),
                    windows=len(windows))
        if self.probe_script:
            remaining = max(5.0, deadline - time.monotonic())
            self._run_readiness_probe(min(self.probe_timeout, remaining))
        if not process_still_ours(self.identity):
            raise self._fail(
                HIDDEN_PROCESS_LOST,
                "The managed Altium process disappeared during the readiness probe",
            )
        self._ready = True
        self.ready_at = iso_now()
        self.ready_elapsed = time.monotonic() - started
        self._event("ready", elapsed=round(self.ready_elapsed, 1),
                    pid=self.pid, desktop=self.desktop_name)
        self._write_state("READY")
        self._write_evidence()
        return self.diagnostics()

    def _run_readiness_probe(self, timeout):
        nonce = uuid.uuid4().hex[:10]
        work = self.state_root / "hidden-probes" / nonce
        work.mkdir(parents=True, exist_ok=True)
        document = work / ("probe_%s.txt" % nonce)
        document.write_text(
            "Spice Sifter hidden desktop probe\nnonce=%s\n" % nonce,
            encoding="ascii",
        )
        marker = work / "marker.txt"
        script = work / "probe.pas"
        script.write_text(
            PROBE_SCRIPT.format(
                nonce=nonce,
                marker=_pas_literal(marker),
                document=_pas_literal(document),
            ),
            encoding="ascii",
        )
        started = time.monotonic()
        run = self._launch_script(script, "RunSpiceSifterProbe", label="readiness")
        fields = {}
        try:
            while time.monotonic() - started < timeout:
                fields = _read_fields(marker)
                if fields.get("STATUS") in ("OK", "ERROR"):
                    break
                if not self.process.is_alive():
                    raise self._fail(
                        HIDDEN_PROCESS_LOST,
                        "The managed Altium process exited during the readiness probe",
                        probe=nonce,
                    )
                time.sleep(0.25)
        finally:
            run.close()
        elapsed = time.monotonic() - started
        self._event("probe_finished", nonce=nonce, elapsed=round(elapsed, 1),
                    fields=fields, probe_dir=str(work))
        if fields.get("STATUS") != "OK":
            raise self._fail(
                HIDDEN_READY_TIMEOUT,
                "The control DelphiScript was not executed by the hidden instance",
                probe=nonce,
                elapsed=round(elapsed, 1),
                fields=fields,
            )
        if fields.get("DOC_OPEN") != "YES":
            raise self._fail(
                HIDDEN_STATE_AMBIGUOUS,
                "The control script ran but its document did not open",
                probe=nonce,
                fields=fields,
            )
        time.sleep(1.0)
        hidden_owners = window_owner_summary(self.windows(), nonce)
        try:
            interactive = window_owner_summary(default_desktop_windows(), nonce)
        except Win32Error as exc:
            interactive = {}
            self._event("interactive_enumeration_failed", error=str(exc))
        self._event("probe_routing", nonce=nonce,
                    hidden_owners=sorted(hidden_owners),
                    interactive_owners=sorted(interactive))
        if self.pid not in hidden_owners:
            raise self._fail(
                HIDDEN_STATE_AMBIGUOUS,
                "The control script was not proven to run in the hidden instance",
                probe=nonce,
                hidden_owners=sorted(hidden_owners),
                interactive_owners=sorted(interactive),
            )
        if interactive:
            raise self._fail(
                HIDDEN_STATE_AMBIGUOUS,
                "The control document also appeared on the interactive desktop",
                probe=nonce,
                interactive_owners=sorted(interactive),
            )
        return {
            "nonce": nonce,
            "elapsed": elapsed,
            "hidden_owners": sorted(hidden_owners),
            "interactive_owners": sorted(interactive),
            "probe_dir": str(work),
        }

    def _fail(self, code, message, **diagnostics):
        diagnostics.setdefault("session_id", self.session_id)
        diagnostics.setdefault("desktop", self.desktop_name)
        diagnostics.setdefault("pid", self.identity.pid if self.identity else None)
        self.reason = code
        self._event("failure", code=code, message=message, **diagnostics)
        self._write_state("FAILED", {"failure": code, "message": message})
        return HiddenAltiumError(code, message, diagnostics)

    def ensure_ready(self):
        """Verify identity and readiness before every addressed script launch."""
        if not self._started:
            raise HiddenAltiumError(
                HIDDEN_STATE_AMBIGUOUS, "The hidden session has not been started"
            )
        if not self._ready:
            raise HiddenAltiumError(
                HIDDEN_READY_TIMEOUT, "The hidden Altium instance is not ready yet"
            )
        if not self.alive():
            raise self._fail(
                HIDDEN_PROCESS_LOST,
                "The managed Altium process is no longer the one this session started",
            )
        return self.diagnostics()

    def alive(self):
        if not self._started or self._stopped:
            return False
        if not self.process or not self.process.is_alive():
            return False
        return process_still_ours(self.identity)

    @property
    def pid(self):
        return self.identity.pid if self.identity else None

    def windows(self):
        if not self.desktop:
            return []
        return self.desktop.windows_of([self.pid])

    # -- scripts -----------------------------------------------------------

    def run_script(self, script, proc_name, label=None, wait=False, timeout=None):
        """Start ``-R`` on the session desktop; never via the interactive one."""
        self.ensure_ready()
        return self._launch_script(script, proc_name, label=label, wait=wait,
                                   timeout=timeout)

    def _launch_script(self, script, proc_name, label=None, wait=False,
                       timeout=None):
        """Create the addressed ``-R`` launcher (used by readiness too)."""
        script = Path(script)
        if not script.is_file():
            raise HiddenAltiumError(
                HIDDEN_SCRIPT_NOT_ROUTED,
                "Script file does not exist: " + str(script),
                {"script": str(script), "proc": proc_name},
            )
        raw = (
            '-RScriptingSystem:RunScriptFile(FileName="' + str(script)
            + '"|ProcName="' + str(proc_name) + '")'
        )
        started = time.monotonic()
        try:
            launched = launch_on_desktop(
                self.altium_path,
                desktop=self.desktop,
                raw_args=raw,
                use_job=False,
                job=self.job,
            )
        except (OSError, Win32Error) as exc:
            raise self._fail(
                HIDDEN_SCRIPT_NOT_ROUTED,
                "Cannot create the addressed -R launcher: " + str(exc),
                script=str(script),
                proc=proc_name,
                desktop=self.desktop_name,
            ) from exc
        run = ScriptRun(
            proc_name=proc_name,
            script=str(script),
            launched=launched,
            started_at=iso_now(),
        )
        self.launchers.append(run)
        self.commands.append({
            "proc_name": proc_name,
            "script": str(script),
            "launcher_pid": run.pid,
            "launcher_creation_time": launched.identity.creation_time,
            "started_at": run.started_at,
            "label": label,
            "command_line": launched.command_line,
            "job_assigned": launched.extra.get("job_assigned"),
        })
        self._event("script_launched", label=label, proc_name=proc_name,
                    launcher_pid=run.pid, script=str(script),
                    command_line=launched.command_line,
                    job_assigned=launched.extra.get("job_assigned"))
        if wait:
            limit = self.probe_timeout if timeout is None else float(timeout)
            if run.wait_exit(limit):
                run.elapsed = time.monotonic() - started
            else:
                raise self._fail(
                    HIDDEN_SCRIPT_TIMEOUT,
                    "The -R launcher did not exit within %.0f s" % limit,
                    script=str(script), proc=proc_name,
                )
        self.commands[-1]["exit_code"] = run.exit_code()
        return run

    def marker_fields(self):
        """Metadata written into ``pending-run.txt`` for this instance."""
        return {
            "altium_pid": self.pid,
            "altium_start_time": self.identity.creation_time if self.identity else None,
            "altium_instance": "hidden",
            "altium_desktop": self.desktop_name,
            "altium_session_id": self.session_id,
            "altium_owned": True,
            "altium_image": self.altium_path,
        }

    # -- stop --------------------------------------------------------------

    def _altium_snapshot(self):
        try:
            from runtime_state import altium_processes
            return [
                {
                    "pid": item.get("pid"),
                    "start_time": item.get("start_time"),
                    "command_line": item.get("command_line"),
                }
                for item in altium_processes(self.altium_path)
            ]
        except Exception as exc:  # pragma: no cover - diagnostics only
            self._event("process_snapshot_failed", error=repr(exc))
            return []

    def stop(self):
        """Terminate only this session's job and release every resource."""
        if self._stopped:
            return self.stop_result or {}
        result = {
            "session_id": self.session_id,
            "desktop": self.desktop_name,
            "pid": self.pid,
            "creation_time": self.identity.creation_time if self.identity else None,
            "graceful": False,
            "job_terminated": False,
            "identity_rechecked": None,
            "exit_code": None,
            "process_gone": None,
            "reason": None,
            "elapsed": 0.0,
        }
        started = time.monotonic()
        self._event("stop_requested", pid=self.pid)
        try:
            for run in self.launchers:
                if run.alive():
                    run.wait_exit(2.0)
            if self.process is not None:
                rechecked = process_still_ours(self.identity)
                result["identity_rechecked"] = rechecked
                if not self.process.is_alive():
                    result["process_gone"] = True
                    result["exit_code"] = self.process.exit_code()
                elif not rechecked:
                    result["reason"] = HIDDEN_STATE_AMBIGUOUS
                    self.reason = HIDDEN_STATE_AMBIGUOUS
                    result["process_gone"] = not self.process.is_alive()
                else:
                    if self.job is not None and self.job.handle:
                        result["job_terminated"] = self.job.terminate(1)
                    result["graceful"] = False
                    self.process.wait(self.stop_timeout)
                    result["process_gone"] = not self.process.is_alive()
                    result["exit_code"] = self.process.exit_code()
                    if not result["process_gone"]:
                        result["reason"] = HIDDEN_STOP_FAILED
                        self.reason = HIDDEN_STOP_FAILED
                    else:
                        result["reason"] = "terminated_owned_job_object"
                        self.reason = result["reason"]
        finally:
            result["elapsed"] = round(time.monotonic() - started, 2)
            self._stopped = True
            self.stop_result = result
            self._event("stopped", **result)
            self._write_state("STOPPED")
            self._cleanup_resources()
            self._clear_state()
        if result["reason"] == HIDDEN_STOP_FAILED:
            raise HiddenAltiumError(
                HIDDEN_STOP_FAILED,
                "The managed Altium process did not exit after job termination",
                result,
            )
        return result

    def _cleanup_resources(self):
        for run in self.launchers:
            try:
                run.close()
            except Exception:  # pragma: no cover - defensive
                pass
        self.launchers = []
        if self.process is not None:
            try:
                self.process.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self.process = None
        if self.job is not None:
            try:
                self.job.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self.job = None
        if self.desktop is not None:
            try:
                self.desktop.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.stop()
        except HiddenAltiumError:
            if exc_type is None:
                raise
        finally:
            self._cleanup_resources()
            self._clear_state()
        return False

    # -- diagnostics -------------------------------------------------------

    def diagnostics(self):
        return {
            "mode": "hidden",
            "session_id": self.session_id,
            "desktop": self.desktop_name,
            "pid": self.pid,
            "creation_time": self.identity.creation_time if self.identity else None,
            "altium": self.altium_path,
            "started_at": self.started_at,
            "ready_at": self.ready_at,
            "ready_elapsed": round(self.ready_elapsed, 2)
            if self.ready_elapsed is not None else None,
            "ready_timeout": self.ready_timeout,
            "owned": True,
            "job_object": bool(self._job_created),
            "working_altium_before": self.working_before,
            "commands": list(self.commands),
            "stop": self.stop_result,
            "reason": self.reason,
        }


def _read_fields(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields
