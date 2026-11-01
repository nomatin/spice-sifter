# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Runtime state: inter-process lock, pending-run marker, doctor and recover.

The marker is the only durable record of a run that may still be executing
inside Altium.  It is written atomically and is removed automatically only when
the previous run is *proven* finished.  Wall-clock age alone is never a reason
to delete it.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

MARKER_NAME = "pending-run.txt"
LOCK_NAME = "altium.lock"
MARKER_SCHEMA_VERSION = 1
START_TIME_TOLERANCE_SECONDS = 3.0


class MarkerError(Exception):
    """The marker exists but cannot be interpreted safely."""


class ProcessQueryError(Exception):
    """The state of a Windows process cannot be verified."""


def utc_now():
    return datetime.now(timezone.utc)


def iso_time(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def state_root(temp_dir=None):
    base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
    return base / "SpiceSifter"


def marker_path(root=None):
    return (Path(root) if root else state_root()) / MARKER_NAME


def lock_path(root=None):
    return (Path(root) if root else state_root()) / LOCK_NAME


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def write_marker(
    root,
    run_id,
    run_dir,
    altium_pid=None,
    altium_start_time=None,
    started_at=None,
    timeout_at=None,
    runner_pid=None,
    state="PENDING",
    extra=None,
):
    """Atomically persist structured pending-run metadata."""
    started = started_at or utc_now()
    marker = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "runner_pid": runner_pid if runner_pid is not None else os.getpid(),
        "altium_pid": altium_pid,
        "altium_start_time": altium_start_time,
        "started_at": iso_time(started),
        "timeout_at": iso_time(timeout_at) if timeout_at else None,
        "state": state,
    }
    if extra:
        marker.update(extra)
    _atomic_write(marker_path(root), json.dumps(marker, ensure_ascii=False, indent=2))
    return marker


def read_marker(root=None):
    """Read a marker, accepting the legacy plain-path format.

    Returns ``None`` when no marker exists.  A marker that is neither valid
    JSON metadata nor a plausible legacy path is returned with ``_corrupt``.
    """
    path = marker_path(root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MarkerError("Cannot read pending-run marker: " + str(exc)) from exc
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        data.setdefault("schema_version", MARKER_SCHEMA_VERSION)
        data["_legacy"] = False
        return data
    legacy = text.strip()
    if legacy and "\n" not in legacy and "\r" not in legacy:
        return {
            "schema_version": 0,
            "run_id": None,
            "run_dir": legacy,
            "state": "PENDING",
            "_legacy": True,
        }
    return {"_corrupt": True, "_legacy": False, "raw": text[:400]}


def clear_marker(root=None):
    try:
        marker_path(root).unlink()
        return True
    except FileNotFoundError:
        return False


def read_status_file(path):
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (FileNotFoundError, OSError):
        return {}
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def _status_snapshot(run_dir):
    if not run_dir:
        return {"run_dir_exists": False, "started": False, "status_file": False}
    path = Path(run_dir)
    fields = read_status_file(path / "status.txt")
    source = "status.txt" if fields else None
    if fields.get("STATUS") not in ("OK", "ERROR"):
        for name in ("collect.tmp", "status.tmp"):
            candidate = read_status_file(path / name)
            if candidate.get("STATUS") in ("OK", "ERROR"):
                fields = candidate
                source = name
                break
    return {
        "run_dir_exists": path.is_dir(),
        "started": (path / "started.txt").is_file(),
        "status_file": (path / "status.txt").is_file(),
        "status": fields.get("STATUS"),
        "code": fields.get("CODE"),
        "status_source": source,
    }


def _powershell(command):
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessQueryError(str(exc)) from exc
    if result.returncode:
        raise ProcessQueryError(
            (result.stderr or result.stdout or "Windows process query failed").strip()
        )
    return result.stdout


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return value
    return []


def process_info(pid):
    """Return metadata for one PID, or ``None`` when the process is absent."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    command = (
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" "
        "-ErrorAction SilentlyContinue; "
        "if ($null -eq $p) { '' } else { "
        "$q = Get-Process -Id %d -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ ProcessId = $p.ProcessId; Name = $p.Name; "
        "StartTime = $(if ($q) { $q.StartTime.ToUniversalTime().ToString('o') } else { $null }); "
        "Responding = $(if ($q) { $q.Responding } else { $false }) } | ConvertTo-Json -Compress }"
        % (pid, pid)
    )
    output = _powershell(command).strip()
    if not output:
        return None
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise ProcessQueryError("Invalid process query output") from exc
    if not isinstance(data, dict):
        return None
    return {
        "pid": data.get("ProcessId", pid),
        "name": data.get("Name"),
        "start_time": data.get("StartTime"),
        "responding": data.get("Responding"),
    }


def altium_processes(exe):
    """Return running X2.EXE processes that match the selected installation."""
    command = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$items = Get-CimInstance Win32_Process -Filter \"name='X2.EXE'\" "
        "-ErrorAction Stop | ForEach-Object { "
        "$q = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ ProcessId = $_.ProcessId; ExecutablePath = $_.ExecutablePath; "
        "CommandLine = $_.CommandLine; "
        "StartTime = $(if ($q) { $q.StartTime.ToUniversalTime().ToString('o') } else { $null }); "
        "Responding = $(if ($q) { $q.Responding } else { $false }) } }; "
        "$items | ConvertTo-Json -Compress"
    )
    output = _powershell(command)
    try:
        processes = _as_list(json.loads(output) if output.strip() else [])
    except ValueError as exc:
        raise ProcessQueryError("Invalid Altium process query output") from exc
    expected = os.path.normcase(os.path.abspath(str(exe))) if exe else None
    matched = []
    for process in processes:
        if not isinstance(process, dict):
            continue
        path = process.get("ExecutablePath")
        command_line = process.get("CommandLine") or ""
        if expected is not None:
            if not path or os.path.normcase(os.path.abspath(path)) != expected:
                continue
        if expected is not None and re_search_launcher(command_line):
            continue
        matched.append(
            {
                "pid": process.get("ProcessId"),
                "path": path,
                "command_line": command_line,
                "start_time": process.get("StartTime"),
                "responding": process.get("Responding"),
            }
        )
    return matched


def re_search_launcher(command_line):
    import re

    return bool(re.search(r'(?:^|[\s"])\-R', command_line or "", re.I))


def running_altium(exe):
    """First matching Altium process, or ``None``.  Raises ProcessQueryError."""
    processes = altium_processes(exe)
    return processes[0] if processes else None


def safe_running_altium(exe):
    try:
        return running_altium(exe)
    except Exception:
        return None


def process_matches(pid, start_time, lookup=None):
    """Return ``(matches, reason)`` for a recorded PID and creation time.

    ``matches`` is ``True`` for the same process, ``False`` when the process is
    gone or the PID was reused, and ``None`` when the state cannot be proven.
    """
    lookup = lookup or process_info
    try:
        info = lookup(pid)
    except ProcessQueryError as exc:
        return None, "process_query_failed: " + str(exc)
    if info is None:
        return False, "process_not_found"
    expected = parse_time(start_time)
    actual = parse_time(info.get("start_time"))
    if expected is None or actual is None:
        return None, "process_start_time_unknown"
    if abs((actual - expected).total_seconds()) <= START_TIME_TOLERANCE_SECONDS:
        return True, "process_matches"
    return False, "pid_reused"


def _diagnostics(marker, snapshot, reason, recovery_possible=False, extra=None):
    run_dir = marker.get("run_dir") if isinstance(marker, dict) else None
    started = parse_time(marker.get("started_at")) if isinstance(marker, dict) else None
    age = None
    if started is not None:
        age = max(0.0, (utc_now() - started).total_seconds())
    diagnostics = {
        "run_id": marker.get("run_id") if isinstance(marker, dict) else None,
        "run_dir": run_dir,
        "altium_pid": marker.get("altium_pid") if isinstance(marker, dict) else None,
        "altium_start_time": marker.get("altium_start_time")
        if isinstance(marker, dict)
        else None,
        "age_seconds": age,
        "started_at": marker.get("started_at") if isinstance(marker, dict) else None,
        "timeout_at": marker.get("timeout_at") if isinstance(marker, dict) else None,
        "legacy_marker": bool(marker.get("_legacy")) if isinstance(marker, dict) else False,
        "status_files": snapshot,
        "reason": reason,
        "recovery_possible": bool(recovery_possible),
    }
    if extra:
        diagnostics.update(extra)
    return diagnostics


def _any_altium_processes(lookup=None):
    """Return ``(processes, error)`` for all X2.EXE instances."""
    lookup = lookup or (lambda: altium_processes(None))
    try:
        processes = lookup()
    except Exception as exc:
        return None, str(exc)
    if processes is None:
        return None, "process query returned no state"
    if isinstance(processes, dict):
        processes = [processes]
    if not isinstance(processes, list):
        return None, "invalid process query result"
    return processes, None


def assess_pending(root=None, process_lookup=None, altium_lookup=None,
                   legacy_confirmed=False):
    """Classify the pending marker without modifying it.

    States: ``NONE``, ``COMPLETED``, ``STALE``, ``ACTIVE``, ``UNKNOWN``,
    ``CORRUPTED``.
    """
    path = marker_path(root)
    if not path.exists():
        return {
            "state": "NONE",
            "removable": False,
            "marker": None,
            "diagnostics": None,
            "reason": "No pending run marker",
        }
    try:
        marker = read_marker(root)
    except MarkerError as exc:
        return {
            "state": "CORRUPTED",
            "removable": False,
            "marker": None,
            "diagnostics": {
                "run_id": None,
                "run_dir": None,
                "altium_pid": None,
                "age_seconds": None,
                "reason": str(exc),
                "recovery_possible": False,
            },
            "reason": str(exc),
        }
    if not marker or marker.get("_corrupt"):
        raw = marker.get("raw") if isinstance(marker, dict) else None
        return {
            "state": "CORRUPTED",
            "removable": False,
            "marker": marker,
            "diagnostics": {
                "run_id": None,
                "run_dir": None,
                "altium_pid": None,
                "age_seconds": None,
                "reason": "pending-run marker is not valid JSON or a legacy path",
                "raw": raw,
                "recovery_possible": False,
            },
            "reason": "pending-run marker is corrupted",
        }

    snapshot = _status_snapshot(marker.get("run_dir"))
    if snapshot.get("status") in ("OK", "ERROR"):
        return {
            "state": "COMPLETED",
            "removable": True,
            "marker": marker,
            "diagnostics": _diagnostics(
                marker, snapshot, "previous run wrote a final status", True
            ),
            "reason": "Previous run finished with STATUS=" + snapshot["status"],
        }

    if marker.get("_legacy"):
        if not snapshot.get("run_dir_exists"):
            return {
                "state": "STALE",
                "removable": True,
                "marker": marker,
                "diagnostics": _diagnostics(
                    marker, snapshot, "legacy run directory no longer exists", True
                ),
                "reason": "Legacy run directory no longer exists",
            }
        processes, error = _any_altium_processes(altium_lookup)
        if error is not None:
            return {
                "state": "UNKNOWN",
                "removable": False,
                "marker": marker,
                "diagnostics": _diagnostics(
                    marker,
                    snapshot,
                    "legacy_process_query_failed: " + error,
                    False,
                    {"legacy_confirmed": bool(legacy_confirmed)},
                ),
                "reason": "Cannot verify whether any Altium process is running",
            }
        if processes:
            return {
                "state": "UNKNOWN",
                "removable": False,
                "marker": marker,
                "diagnostics": _diagnostics(
                    marker,
                    snapshot,
                    "legacy_marker_altium_running",
                    False,
                    {
                        "legacy_confirmed": bool(legacy_confirmed),
                        "altium_process_count": len(processes),
                    },
                ),
                "reason": "Altium is running; the legacy marker cannot be cleared safely",
            }
        return {
            "state": "STALE",
            "removable": True,
            "marker": marker,
            "diagnostics": _diagnostics(
                marker,
                snapshot,
                "legacy_marker_no_altium_processes",
                True,
                {"legacy_confirmed": bool(legacy_confirmed)},
            ),
            "reason": "No running Altium process; the legacy marker is stale",
        }

    altium_pid = marker.get("altium_pid")
    if not altium_pid:
        return {
            "state": "UNKNOWN",
            "removable": False,
            "marker": marker,
            "diagnostics": _diagnostics(
                marker, snapshot, "marker has no Altium PID", False
            ),
            "reason": "Cannot verify the Altium process for this run",
        }
    matches, reason = process_matches(
        altium_pid, marker.get("altium_start_time"), process_lookup
    )
    if matches is True:
        timeout_at = parse_time(marker.get("timeout_at"))
        if timeout_at is None:
            return {
                "state": "UNKNOWN",
                "removable": False,
                "marker": marker,
                "diagnostics": _diagnostics(
                    marker,
                    snapshot,
                    "process_matches_without_timeout",
                    False,
                    {"detail": "Altium is alive but the run has no recorded timeout"},
                ),
                "reason": "Cannot determine whether the previous script is still executing",
            }
        if utc_now() > timeout_at:
            return {
                "state": "UNKNOWN",
                "removable": False,
                "marker": marker,
                "diagnostics": _diagnostics(
                    marker,
                    snapshot,
                    "timeout_expired_without_final_status",
                    False,
                    {
                        "detail": (
                            "Altium is alive, the run timed out and no final status "
                            "was written; the script state cannot be proven"
                        )
                    },
                ),
                "reason": "Previous run timed out without a final status",
            }
        return {
            "state": "ACTIVE",
            "removable": False,
            "marker": marker,
            "diagnostics": _diagnostics(
                marker, snapshot, "process_matches", False,
                {"detail": "recorded Altium process is still running"},
            ),
            "reason": "Previous Altium process is still running",
        }
    if matches is False:
        return {
            "state": "STALE",
            "removable": True,
            "marker": marker,
            "diagnostics": _diagnostics(
                marker,
                snapshot,
                reason,
                True,
                {"detail": "recorded Altium process has exited or the PID was reused"},
            ),
            "reason": "Previous Altium process has exited",
        }
    return {
        "state": "UNKNOWN",
        "removable": False,
        "marker": marker,
        "diagnostics": _diagnostics(marker, snapshot, reason, False),
        "reason": "Cannot determine whether the previous Altium process finished",
    }


def resolve_pending(root=None, process_lookup=None, altium_lookup=None):
    """Assess the marker and remove it only when proven stale/completed."""
    assessment = assess_pending(root, process_lookup, altium_lookup)
    if assessment["state"] in ("COMPLETED", "STALE"):
        assessment["removed"] = clear_marker(root)
    else:
        assessment["removed"] = False
    return assessment


def _lock_file(root):
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / LOCK_NAME).open("a+b")
    except OSError as exc:
        raise MarkerError("Cannot open the Altium lock: " + str(exc)) from exc
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
    except OSError as exc:
        handle.close()
        raise MarkerError("Cannot read the Altium lock: " + str(exc)) from exc
    return handle


@contextmanager
def acquire_lock(root=None, blocking=False):
    """Hold the shared Altium lock for the duration of the context."""
    import msvcrt

    handle = _lock_file(root)
    mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
    try:
        msvcrt.locking(handle.fileno(), mode, 1)
    except OSError as exc:
        handle.close()
        raise MarkerError("Another Spice Sifter run holds the Altium lock") from exc
    try:
        yield root
    finally:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


def lock_available(root=None):
    try:
        with acquire_lock(root):
            return True
    except MarkerError:
        return False


def file_version(exe):
    if not exe or not Path(exe).is_file():
        return None
    escaped = str(exe).replace("'", "''")
    try:
        output = _powershell(
            "(Get-Item -LiteralPath '%s').VersionInfo.ProductVersion" % escaped
        )
    except ProcessQueryError:
        return None
    return output.strip() or None


def _pending_summary(assessment):
    if assessment["state"] == "NONE":
        return None
    diagnostics = dict(assessment.get("diagnostics") or {})
    diagnostics["state"] = assessment["state"]
    diagnostics["removable"] = assessment["removable"]
    return diagnostics


def doctor_report(altium=None, temp_dir=None, process_query=None):
    """Check readiness without starting any electrical simulation."""
    root = state_root(temp_dir)
    query = process_query or altium_processes
    exe = altium or os.environ.get("SPICE_SIFTER_ALTIUM")
    if not exe:
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Altium/AD24/X2.EXE"
        if candidate.is_file():
            exe = str(candidate)
    report = {
        "schema_version": "1.0",
        "status": "ERROR",
        "altium": {
            "path": exe,
            "exists": bool(exe and Path(exe).is_file()),
            "running": False,
            "responding": None,
            "version": None,
            "processes": [],
            "state": "UNKNOWN",
        },
        "lock": {"available": False},
        "pending_run": None,
        "temporary_directory": {"path": str(root), "writable": False},
        "dblib_resources": {"ready": False, "missing": []},
        "ready": False,
        "diagnostics": [],
        "suggested_action": None,
    }
    diagnostics = report["diagnostics"]

    if not report["altium"]["exists"]:
        diagnostics.append(
            "Selected Altium X2.EXE was not found. Set --altium or SPICE_SIFTER_ALTIUM."
        )
    else:
        report["altium"]["version"] = file_version(exe)

    if report["altium"]["exists"]:
        try:
            processes = query(exe)
        except ProcessQueryError as exc:
            diagnostics.append("Cannot query Altium processes: " + str(exc))
            report["altium"]["state"] = "UNKNOWN"
            processes = None
        if processes is not None:
            if isinstance(processes, dict):
                processes = [processes]
            if not isinstance(processes, list):
                diagnostics.append("Altium process query returned an invalid result.")
                report["altium"]["state"] = "UNKNOWN"
                processes = None
        if processes is not None:
            report["altium"]["processes"] = processes
            report["altium"]["running"] = bool(processes)
            if len(processes) > 1:
                report["altium"]["state"] = "AMBIGUOUS"
                report["altium"]["responding"] = None
                diagnostics.append(
                    "Multiple matching Altium processes; cannot choose one safely."
                )
            elif len(processes) == 1:
                responding = processes[0].get("responding")
                report["altium"]["responding"] = responding
                if responding is True:
                    report["altium"]["state"] = "RESPONDING"
                elif responding is False:
                    report["altium"]["state"] = "NOT_RESPONDING"
                else:
                    report["altium"]["state"] = "UNKNOWN"
                    diagnostics.append("Altium responsiveness is unknown.")
            else:
                report["altium"]["state"] = "NOT_RUNNING"

    report["lock"]["available"] = lock_available(root)

    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ("doctor-" + uuid.uuid4().hex + ".tmp")
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        report["temporary_directory"]["writable"] = True
    except OSError as exc:
        diagnostics.append("Temporary directory is not writable: " + str(exc))

    try:
        from dblib_runner import SCRIPT_FIXTURE, SCRIPT_TEMPLATE, TEMPLATE_EMPTY, TEMPLATE_ROOT

        missing = [
            str(path)
            for path in (TEMPLATE_ROOT, TEMPLATE_EMPTY, SCRIPT_TEMPLATE, SCRIPT_FIXTURE)
            if not path.exists()
        ]
        report["dblib_resources"] = {"ready": not missing, "missing": missing}
        if missing:
            diagnostics.append("DbLib template resources are missing: " + ", ".join(missing))
    except Exception as exc:  # pragma: no cover - defensive
        report["dblib_resources"] = {"ready": False, "missing": [str(exc)]}
        diagnostics.append("Cannot inspect DbLib resources: " + str(exc))

    assessment = assess_pending(root)
    report["pending_run"] = _pending_summary(assessment)

    version = report["altium"]["version"]
    version_supported = isinstance(version, str) and version.startswith("24.4.")
    if not report["altium"]["exists"]:
        status = "ERROR"
    elif version is None:
        status = "ERROR"
        diagnostics.append(
            "Cannot determine Altium version; compatibility with 24.4.x is unconfirmed."
        )
    elif not version_supported:
        status = "ERROR"
        diagnostics.append(
            "Unsupported Altium version: %s; expected 24.4.x." % version
        )
    elif report["altium"]["state"] == "UNKNOWN":
        status = "ERROR"
    elif report["altium"]["state"] == "AMBIGUOUS":
        status = "ERROR"
    elif not report["altium"]["running"]:
        status = "ERROR"
        diagnostics.append("Start the selected Altium Designer before running tests.")
    elif report["altium"]["responding"] is False:
        status = "BUSY"
        diagnostics.append("Altium is running but not responding.")
    elif report["altium"]["responding"] is not True:
        status = "ERROR"
        diagnostics.append("Altium responsiveness is unknown; readiness is unconfirmed.")
    elif not report["temporary_directory"]["writable"]:
        status = "ERROR"
    elif not report["lock"]["available"]:
        status = "BUSY"
        diagnostics.append("Another Spice Sifter run holds the Altium lock.")
    elif assessment["state"] == "ACTIVE":
        status = "BUSY"
        diagnostics.append("A previous simulation is still running.")
    elif assessment["state"] in ("UNKNOWN", "CORRUPTED"):
        status = "RECOVERY_REQUIRED"
        diagnostics.append("The previous run state cannot be proven; run recover for diagnostics.")
    elif assessment["state"] in ("COMPLETED", "STALE"):
        status = "RECOVERY_REQUIRED"
        diagnostics.append("A finished pending-run marker can be removed with recover.")
    elif not report["dblib_resources"]["ready"]:
        status = "ERROR"
    else:
        status = "READY"

    report["status"] = status
    report["ready"] = status == "READY"
    if status == "READY":
        report["suggested_action"] = None
    elif status in ("BUSY", "RECOVERY_REQUIRED"):
        report["suggested_action"] = "Run: python spice_sifter.py doctor --json"
        if status == "RECOVERY_REQUIRED":
            report["suggested_action"] = "Run: python spice_sifter.py recover"
    else:
        report["suggested_action"] = (
            "Start the selected Altium Designer or fix the reported environment issue."
        )
    return report


def recover_pending(temp_dir=None, process_lookup=None, altium_lookup=None,
                    confirm_legacy_stopped=False):
    """Safely clear a pending marker that is proven finished."""
    root = state_root(temp_dir)
    try:
        with acquire_lock(root):
            assessment = assess_pending(
                root,
                process_lookup,
                altium_lookup,
                legacy_confirmed=confirm_legacy_stopped,
            )
            state = assessment["state"]
            base = {
                "schema_version": "1.0",
                "run_id": (assessment.get("marker") or {}).get("run_id"),
                "state": state,
                "diagnostics": assessment.get("diagnostics"),
                "ready": False,
            }
            if state == "NONE":
                base.update(status="NOTHING_TO_RECOVER", ready=True, reason="No pending run")
                return base
            if state in ("COMPLETED", "STALE"):
                removed = clear_marker(root)
                base.update(
                    status="RECOVERED",
                    ready=True,
                    removed=removed,
                    reason=assessment["reason"],
                )
                return base
            if state == "ACTIVE":
                base.update(
                    status="REFUSED",
                    reason="Previous Altium process is still running; the marker was kept",
                    suggested_action="Wait for Altium to finish, then run recover again",
                )
                return base
            if (
                state == "UNKNOWN"
                and (assessment.get("diagnostics") or {}).get("reason")
                == "legacy_marker_altium_running"
            ):
                base.update(
                    status="REFUSED",
                    reason="Altium is running; the legacy marker cannot be cleared safely",
                    suggested_action="Close Altium, verify that no simulation is running, then run recover again",
                )
                return base
            base.update(
                status="UNKNOWN",
                reason=assessment["reason"],
                suggested_action=(
                    "Close or restart Altium, verify that no simulation is running, "
                    "then run: python spice_sifter.py recover"
                ),
            )
            return base
    except MarkerError as exc:
        return {
            "schema_version": "1.0",
            "status": "BUSY",
            "ready": False,
            "reason": str(exc),
            "suggested_action": "Wait for the other Spice Sifter process, then run recover again",
        }
