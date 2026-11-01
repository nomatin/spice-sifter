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

import math
from pathlib import Path

SCHEMA_VERSION = "1.0"
RESULT_STATUSES = ("PASS", "FAIL", "MEASURED", "ERROR", "NOT_RUN")

SYSTEMIC_CODES = {
    "BUSY",
    "TIMEOUT",
    "SCRIPT_NOT_EXECUTED",
    "ALTIUM_NOT_RUNNING",
    "ALTIUM_STATE_UNKNOWN",
    "ALTIUM_PENDING_RUN",
    "ALTIUM_BLOCKED_BY_PENDING_RUN",
}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def validate_report_contract(report):
    """Return ``(ok, code, message)`` for one Spice Sifter report.

    The checks mirror the published JSON Schema and report contract.  A report
    is never accepted merely because it claims ``status: PASS``.
    """
    if not isinstance(report, dict):
        return False, "REPORT_CONTRACT_INVALID", "Report is not a JSON object"
    if report.get("schema_version") != SCHEMA_VERSION:
        return False, "REPORT_CONTRACT_INVALID", "Unsupported or missing schema_version"
    results = report.get("results")
    coverage = report.get("coverage")
    if not isinstance(results, list):
        return False, "REPORT_CONTRACT_INVALID", "results must be an array"
    if not isinstance(coverage, dict):
        return False, "REPORT_CONTRACT_INVALID", "coverage must be an object"
    required = (
        "status",
        "expected",
        "completed",
        "missing",
        "passed",
        "failed",
        "measured",
        "errors",
        "not_run",
    )
    missing_fields = [key for key in required if key not in coverage]
    if missing_fields:
        return (
            False,
            "REPORT_CONTRACT_INVALID",
            "coverage is missing: " + ", ".join(missing_fields),
        )
    for key in required[1:]:
        if not _is_int(coverage.get(key)):
            return False, "REPORT_CONTRACT_INVALID", "coverage.%s must be an integer" % key
    if coverage.get("status") != "OK":
        return False, "REPORT_INCOMPLETE", "coverage.status is not OK"

    counts = {status: 0 for status in RESULT_STATUSES}
    seen = set()
    for position, result in enumerate(results):
        if not isinstance(result, dict):
            return False, "REPORT_CONTRACT_INVALID", "results[%d] is not an object" % position
        result_id = result.get("result_id")
        if not isinstance(result_id, str) or not result_id:
            return False, "REPORT_CONTRACT_INVALID", "results[%d] has no result_id" % position
        if result_id in seen:
            return False, "REPORT_CONTRACT_INVALID", "Duplicate result_id: " + result_id
        seen.add(result_id)
        status = result.get("status")
        if status not in RESULT_STATUSES:
            return False, "REPORT_CONTRACT_INVALID", "Unknown result status: " + str(status)
        counts[status] += 1
        if status in ("PASS", "FAIL", "MEASURED") and not _finite(result.get("value")):
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "Result %s has no valid finite numeric value" % result_id,
            )

    expected = coverage["expected"]
    completed = coverage["completed"]
    if len(results) != expected:
        return (
            False,
            "REPORT_INCOMPLETE",
            "Expected %d results, collected %d" % (expected, len(results)),
        )
    if expected != completed:
        return (
            False,
            "REPORT_INCOMPLETE",
            "Expected %d results, completed %d" % (expected, completed),
        )
    if coverage["missing"] != 0 or coverage["not_run"] != 0:
        return (
            False,
            "REPORT_INCOMPLETE",
            "Coverage has missing=%d, not_run=%d"
            % (coverage["missing"], coverage["not_run"]),
        )
    if completed != (
        coverage["passed"]
        + coverage["failed"]
        + coverage["measured"]
        + coverage["errors"]
    ):
        return False, "REPORT_CONTRACT_INVALID", "coverage.completed is inconsistent"
    for key, status in (
        ("passed", "PASS"),
        ("failed", "FAIL"),
        ("measured", "MEASURED"),
        ("errors", "ERROR"),
        ("not_run", "NOT_RUN"),
    ):
        if coverage[key] != counts[status]:
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "coverage.%s does not match results[]" % key,
            )

    claimed = report.get("status")
    if claimed not in ("PASS", "FAIL", "ERROR"):
        return False, "REPORT_CONTRACT_INVALID", "Unknown report status"
    if claimed == "PASS" and (
        counts["ERROR"] or counts["NOT_RUN"] or counts["FAIL"]
    ):
        return False, "REPORT_INCOMPLETE", "PASS report contains failed/error/not_run results"
    if claimed == "FAIL" and (counts["ERROR"] or counts["NOT_RUN"]):
        return False, "REPORT_INCOMPLETE", "FAIL report contains error/not_run results"
    return True, None, None


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
    first_error = None
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
        error_code = None
        if isinstance(report, dict):
            error_code = (report.get("error") or {}).get("code")
        if error_code in SYSTEMIC_CODES:
            runs.append(
                {
                    "index": index,
                    "name": _name(test),
                    "status": "ERROR",
                    "error": report.get("error"),
                    "report": report,
                }
            )
            errors += 1
            systemic = error_code
            continue
        valid, code, message = validate_report_contract(report)
        if not valid:
            error = {
                "code": code,
                "message": message,
                "diagnostics": {"report": report},
            }
            if first_error is None:
                first_error = error
            runs.append(
                {
                    "index": index,
                    "name": _name(test),
                    "status": "ERROR",
                    "error": error,
                    "report": report,
                }
            )
            errors += 1
            continue
        accepted_status = report.get("status")
        runs.append(
            {
                "index": index,
                "name": _name(test),
                "status": accepted_status,
                "report": report,
            }
        )
        if accepted_status in ("PASS", "FAIL"):
            completed += 1
            failed = failed or accepted_status == "FAIL"
        else:
            errors += 1
            if first_error is None:
                first_error = report.get("error") or {
                    "code": "REPORT_INCOMPLETE",
                    "message": "Report status is ERROR",
                }

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
    elif first_error is not None:
        result["error"] = first_error
    return result
