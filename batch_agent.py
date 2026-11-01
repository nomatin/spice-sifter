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


def _valid_error(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("code"), str)
        and value["code"] != ""
        and isinstance(value.get("message"), str)
    )


def _valid_result_id(value):
    return isinstance(value, str) and value != ""


def _valid_result_identifier(value):
    return isinstance(value, str) or _is_int(value)


def validate_report_contract(report):
    """Return ``(ok, code, message)`` for one Spice Sifter report.

    The checks mirror the published JSON Schema and report contract.  A report
    is never accepted merely because it claims ``status: PASS``.
    """
    if not isinstance(report, dict):
        return False, "REPORT_CONTRACT_INVALID", "Report is not a JSON object"
    if report.get("schema_version") != SCHEMA_VERSION:
        return False, "REPORT_CONTRACT_INVALID", "Unsupported or missing schema_version"

    claimed = report.get("status")
    if claimed not in ("PASS", "FAIL", "ERROR"):
        return False, "REPORT_CONTRACT_INVALID", "Unknown report status"
    if report.get("validation_status") not in ("ASSERTIONS", "NO_ASSERTIONS"):
        return False, "REPORT_CONTRACT_INVALID", "Missing or invalid validation_status"

    results = report.get("results")
    coverage = report.get("coverage")
    if not isinstance(results, list):
        return False, "REPORT_CONTRACT_INVALID", "results must be an array"
    if not isinstance(coverage, dict):
        return False, "REPORT_CONTRACT_INVALID", "coverage must be an object"

    error = report.get("error")
    if claimed in ("PASS", "FAIL"):
        if error is not None:
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "%s report requires error=null" % claimed,
            )
    elif not _valid_error(error):
        return (
            False,
            "REPORT_CONTRACT_INVALID",
            "ERROR report requires an error object with code and message",
        )

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
        if coverage[key] < 0:
            return False, "REPORT_CONTRACT_INVALID", "coverage.%s must be non-negative" % key
    if coverage.get("status") != "OK":
        return False, "REPORT_INCOMPLETE", "coverage.status is not OK"

    counts = {status: 0 for status in RESULT_STATUSES}
    seen = set()
    required_result_fields = (
        "result_id",
        "run_id",
        "kind",
        "id",
        "status",
        "value",
        "unit",
        "error",
    )
    for position, result in enumerate(results):
        if not isinstance(result, dict):
            return False, "REPORT_CONTRACT_INVALID", "results[%d] is not an object" % position
        absent = [key for key in required_result_fields if key not in result]
        if absent:
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "results[%d] is missing: %s" % (position, ", ".join(absent)),
            )
        result_id = result["result_id"]
        if not _valid_result_id(result_id):
            return False, "REPORT_CONTRACT_INVALID", "results[%d] has no result_id" % position
        if result_id in seen:
            return False, "REPORT_CONTRACT_INVALID", "Duplicate result_id: " + result_id
        seen.add(result_id)
        if not (result["run_id"] is None or isinstance(result["run_id"], str)):
            return False, "REPORT_CONTRACT_INVALID", "results[%d].run_id must be string or null" % position
        if result["kind"] not in ("check", "measurement"):
            return False, "REPORT_CONTRACT_INVALID", "results[%d] has unknown kind" % position
        if not _valid_result_identifier(result["id"]):
            return False, "REPORT_CONTRACT_INVALID", "results[%d].id must be string or integer" % position
        status = result["status"]
        if status not in RESULT_STATUSES:
            return False, "REPORT_CONTRACT_INVALID", "Unknown result status: " + str(status)
        counts[status] += 1
        value = result["value"]
        if value is not None and not _finite(value):
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "Result %s value must be a finite number or null" % result_id,
            )
        if not (result["unit"] is None or isinstance(result["unit"], str)):
            return False, "REPORT_CONTRACT_INVALID", "Result %s unit must be string or null" % result_id
        result_error = result["error"]
        if result_error is not None and not _valid_error(result_error):
            return False, "REPORT_CONTRACT_INVALID", "Result %s has an invalid error object" % result_id
        if status in ("PASS", "FAIL", "MEASURED"):
            if not _finite(value):
                return (
                    False,
                    "REPORT_CONTRACT_INVALID",
                    "Result %s has no valid finite numeric value" % result_id,
                )
            if result_error is not None:
                return (
                    False,
                    "REPORT_CONTRACT_INVALID",
                    "Result %s with status %s requires error=null" % (result_id, status),
                )
        elif status == "ERROR":
            if not _valid_error(result_error):
                return (
                    False,
                    "REPORT_CONTRACT_INVALID",
                    "Result %s with status ERROR requires an error object" % result_id,
                )
        elif status == "NOT_RUN" and value is not None:
            return (
                False,
                "REPORT_CONTRACT_INVALID",
                "Result %s with status NOT_RUN requires value=null" % result_id,
            )

    expected = coverage["expected"]
    completed = coverage["completed"]
    if expected != completed + coverage["missing"]:
        return False, "REPORT_CONTRACT_INVALID", "coverage.expected is inconsistent"
    if completed != (
        coverage["passed"]
        + coverage["failed"]
        + coverage["measured"]
        + coverage["errors"]
    ):
        return False, "REPORT_CONTRACT_INVALID", "coverage.completed is inconsistent"
    if coverage["missing"] != coverage["not_run"]:
        return False, "REPORT_CONTRACT_INVALID", "coverage.missing does not match not_run"
    if len(results) != expected:
        return (
            False,
            "REPORT_INCOMPLETE",
            "Expected %d results, collected %d" % (expected, len(results)),
        )
    if coverage["missing"] != 0 or coverage["not_run"] != 0:
        return (
            False,
            "REPORT_INCOMPLETE",
            "Coverage has missing=%d, not_run=%d"
            % (coverage["missing"], coverage["not_run"]),
        )
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

    if claimed == "PASS" and (
        counts["FAIL"] or counts["ERROR"] or counts["NOT_RUN"]
    ):
        return False, "REPORT_CONTRACT_INVALID", "PASS report contradicts results[]"
    if claimed == "FAIL":
        if counts["ERROR"] or counts["NOT_RUN"]:
            return False, "REPORT_CONTRACT_INVALID", "FAIL report contains error/not_run results"
        if counts["FAIL"] == 0:
            return False, "REPORT_CONTRACT_INVALID", "FAIL report has no FAIL results"
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
