# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Reference batch-agent integration for Spice Sifter.

This is not a queue and not a server.  It documents and exercises the contract
external agents should follow:

1. Call ``doctor --json`` once before a series.
2. If it is not READY, do not start any simulation; mark the whole series
   ``NOT_RUN`` with one shared blocking reason.
3. If a systemic error appears mid-series (BUSY, TIMEOUT, SCRIPT_NOT_EXECUTED,
   ALTIUM_NOT_RUNNING, ALTIUM_STATE_UNKNOWN), stop the remaining series and
   keep the reports that were already produced.
"""
from __future__ import annotations

from pathlib import Path

SCHEMA_VERSION = "1.0"

SYSTEMIC_CODES = {
    "BUSY",
    "TIMEOUT",
    "SCRIPT_NOT_EXECUTED",
    "ALTIUM_NOT_RUNNING",
    "ALTIUM_STATE_UNKNOWN",
    "ALTIUM_PENDING_RUN",
    "ALTIUM_BLOCKED_BY_PENDING_RUN",
}


def _name(test):
    if isinstance(test, dict):
        return str(test.get("name") or test.get("path") or test.get("test") or "test")
    return Path(str(test)).stem


def _path(test):
    if isinstance(test, dict):
        return test.get("path") or test.get("test")
    return test


def _not_run(index, test, error):
    return {
        "index": index,
        "name": _name(test),
        "status": "NOT_RUN",
        "error": error,
    }


def _blocked_code(doctor):
    pending = doctor.get("pending_run") or {}
    state = pending.get("state")
    if state in ("ACTIVE", "COMPLETED", "STALE"):
        return "ALTIUM_BLOCKED_BY_PENDING_RUN"
    if state in ("UNKNOWN", "CORRUPTED"):
        return "ALTIUM_STATE_UNKNOWN"
    if not (doctor.get("altium") or {}).get("running"):
        return "ALTIUM_NOT_RUNNING"
    if not (doctor.get("lock") or {}).get("available"):
        return "ALTIUM_BUSY"
    if not (doctor.get("temporary_directory") or {}).get("writable"):
        return "ALTIUM_TEMP_UNAVAILABLE"
    return "ALTIUM_NOT_READY"


def _blocked_message(doctor):
    if _blocked_code(doctor) == "ALTIUM_BLOCKED_BY_PENDING_RUN":
        return "Previous run is unresolved"
    diagnostics = doctor.get("diagnostics") or []
    return diagnostics[0] if diagnostics else "Altium is not ready"


def run_batch(tests, altium=None, timeout=120, runner=None, doctor=None):
    """Run a series only after one shared preflight succeeds."""
    from runtime_state import doctor_report
    from spice_sifter import run_test_file

    runner = runner or run_test_file
    doctor = doctor or (lambda: doctor_report(altium))
    tests = list(tests)
    planned = len(tests)
    status_report = doctor()
    if not status_report.get("ready"):
        code = _blocked_code(status_report)
        error = {
            "code": code,
            "message": _blocked_message(status_report),
            "diagnostics": status_report,
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "ERROR",
            "error": error,
            "summary": {
                "planned_runs": planned,
                "completed_runs": 0,
                "not_run": planned,
            },
            "runs": [_not_run(index, test, error) for index, test in enumerate(tests)],
        }

    runs = []
    completed = 0
    errors = 0
    failed = False
    systemic = None
    for index, test in enumerate(tests):
        if systemic is not None:
            runs.append(
                _not_run(
                    index,
                    test,
                    {
                        "code": systemic,
                        "message": "Batch stopped after a systemic error",
                    },
                )
            )
            continue
        report = runner(_path(test), altium=altium, timeout=timeout)
        runs.append(
            {
                "index": index,
                "name": _name(test),
                "status": report.get("status"),
                "report": report,
            }
        )
        if report.get("status") in ("PASS", "FAIL"):
            completed += 1
            failed = failed or report.get("status") == "FAIL"
        else:
            errors += 1
            code = (report.get("error") or {}).get("code")
            if code in SYSTEMIC_CODES:
                systemic = code

    not_run = sum(1 for run in runs if run["status"] == "NOT_RUN")
    status = "ERROR" if systemic or errors or not_run else ("FAIL" if failed else "PASS")
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "summary": {
            "planned_runs": planned,
            "completed_runs": completed,
            "not_run": not_run,
            "errors": errors,
        },
        "runs": runs,
    }
    if systemic is not None:
        result["error"] = {
            "code": systemic,
            "message": "Batch stopped after a systemic error",
        }
    return result
