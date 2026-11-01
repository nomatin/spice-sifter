# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Single normalized report contract for Spice Sifter.

The module is the only place that turns the historical ``checks`` /
``measurements`` / ``runs`` structures into the stable ``results[]`` collection
and computes ``coverage``.  Every runner mode (standalone SPICE, temperature
series, parameter sweep, DbLib component) goes through :func:`finalize_report`.
"""
from __future__ import annotations

import math

SCHEMA_VERSION = "1.0"
RESULT_STATUSES = ("PASS", "FAIL", "MEASURED", "ERROR", "NOT_RUN")

# Optional diagnostic keys copied from the original request/row when present.
_OPTIONAL_KEYS = ("frequency_hz", "signal", "component", "markers")


def _finite(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _check_id(check, index):
    """Stable identifier for a check: explicit id or its source array index."""
    if isinstance(check, dict) and "id" in check:
        value = check["id"]
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (str, int)):
            return value
        return str(value)
    return index


def _check_limits(check):
    limits = {}
    if "min" in check:
        limits["min"] = check["min"]
    if "max" in check:
        limits["max"] = check["max"]
    return limits or None


def _measurement_limits(request):
    limits = request.get("limits")
    if isinstance(limits, dict):
        result = {key: limits[key] for key in ("min", "max") if key in limits}
        return result or None
    return None


def _error_payload(error):
    if isinstance(error, dict):
        payload = {}
        for key in ("code", "message", "stage", "temperature_code", "sweep_code"):
            if key in error:
                payload[key] = error[key]
        for key in ("diagnostics", "suggested_action"):
            if key in error:
                payload[key] = error[key]
        return payload or {"code": "UNKNOWN", "message": "Unspecified error"}
    return {"code": "UNKNOWN", "message": str(error)}


def _run_keys(data):
    """Return the ordered run plan (temperature + parameters) for the input."""
    from itertools import product

    from spice_sifter import axis_value, parameter_axes, temperature_plan

    if "component" in data:
        return [{"temperature": None, "parameters": None, "indices": {}}], None, [None]
    if "sweep" in data:
        axes = parameter_axes(data)
        temps = temperature_plan(data) or [None]
        keys = []
        for temperature in temps:
            for combo in product(*(range(axis["count"]) for axis in axes.values())):
                indices = dict(zip(axes, combo))
                parameters = {
                    name: float(axis_value(axes[name], index))
                    for name, index in indices.items()
                }
                keys.append(
                    {
                        "temperature": temperature,
                        "parameters": parameters,
                        "indices": indices,
                    }
                )
        return keys, axes, temps
    plan = temperature_plan(data)
    if "temperatures_c" in data:
        temps = list(plan or [])
    else:
        temps = [plan[0] if plan else None]
    return (
        [
            {"temperature": temperature, "parameters": None, "indices": {}}
            for temperature in temps
        ],
        None,
        temps,
    )


def _applies(request, key, axes):
    from spice_sifter import condition_indices

    if axes is not None and "when" in request:
        try:
            wanted = condition_indices(request["when"], axes)
        except Exception:
            return False
        for name, index in wanted.items():
            if key["indices"].get(name) != index:
                return False
    if "temperature_c" in request and request["temperature_c"] != key["temperature"]:
        return False
    return True


def _plan_bundle(data):
    """Return ``(descriptors, keys, axes)`` or ``(None, None, None)``."""
    if not isinstance(data, dict):
        return None, None, None
    try:
        keys, axes, family_temps = _run_keys(data)
        checks = data.get("checks", [])
        measurements = data.get("measurements", [])
        if not isinstance(checks, list) or not isinstance(measurements, list):
            return None, None, None
        descriptors = []
        for key_index, key in enumerate(keys):
            for index, check in enumerate(checks):
                if not isinstance(check, dict):
                    return None, None, None
                if _applies(check, key, axes):
                    descriptors.append(
                        {
                            "kind": "check",
                            "id": _check_id(check, index),
                            "request": check,
                            "key_index": key_index,
                            "scope": None,
                            "temperature": key["temperature"],
                            "group_index": None,
                        }
                    )
            for request in measurements:
                if not isinstance(request, dict):
                    return None, None, None
                if request.get("type") == "sweep_crossing":
                    continue
                if _applies(request, key, axes):
                    descriptors.append(
                        {
                            "kind": "measurement",
                            "id": request.get("id"),
                            "request": request,
                            "key_index": key_index,
                            "scope": None,
                            "temperature": key["temperature"],
                            "group_index": None,
                        }
                    )
        if "sweep" in data:
            for request in measurements:
                if not isinstance(request, dict) or request.get("type") != "sweep_crossing":
                    continue
                applied = 0
                for temperature in family_temps:
                    if any(
                        key["temperature"] == temperature
                        and _applies(request, key, axes)
                        for key in keys
                    ):
                        applied += 1
                        descriptors.append(
                            {
                                "kind": "measurement",
                                "id": request.get("id"),
                                "request": request,
                                "key_index": None,
                                "scope": "sweep",
                                "temperature": temperature,
                                "group_index": applied,
                            }
                        )
        return descriptors, keys, axes
    except Exception:
        return None, None, None


def plan_results(data):
    """Return the expected result descriptors, or ``None`` if planning fails.

    One descriptor corresponds to one mandatory entry in ``results[]``.  A
    check applied to five temperatures produces five descriptors; a sweep
    family measurement produces one descriptor per applicable temperature
    group, never one per intermediate sweep point.
    """
    descriptors, _, _ = _plan_bundle(data)
    return descriptors


def _same_temperature(left, right):
    if left is None or right is None:
        return left is None and right is None
    return left == right


def _run_for_key(runs, key):
    for run in runs:
        if not isinstance(run, dict):
            continue
        if key["parameters"] is None:
            if run.get("parameters") is not None:
                continue
        elif run.get("parameters") != key["parameters"]:
            continue
        if _same_temperature(run.get("temperature_c"), key["temperature"]):
            return run
    return None


def _base_result(result_id, run_id, kind, identifier, request, descriptor, run):
    result = {
        "result_id": result_id,
        "run_id": run_id,
        "kind": kind,
        "id": identifier,
        "status": "NOT_RUN",
        "value": None,
        "unit": None,
        "error": None,
    }
    limits = _check_limits(request) if kind == "check" else _measurement_limits(request)
    if limits:
        result["limits"] = limits
    for key in _OPTIONAL_KEYS:
        if key in request:
            result[key] = request[key]
    if descriptor.get("scope") is not None:
        result["scope"] = descriptor["scope"]
    parameters = run.get("parameters") if isinstance(run, dict) else None
    if parameters is not None:
        result["parameters"] = parameters
    temperature = request.get("temperature_c")
    if temperature is None:
        temperature = descriptor.get("temperature")
    if temperature is not None:
        result["temperature_c"] = temperature
    return result


def _apply_row_status(result, row, kind):
    status = row.get("status") if kind == "measurement" else None
    if kind == "measurement":
        if status not in RESULT_STATUSES:
            result["status"] = "ERROR"
            result["error"] = {
                "code": "RESULT_STATUS_UNKNOWN",
                "message": "Measurement result has an unknown status",
            }
        else:
            result["status"] = status
        return
    error = row.get("error")
    if error:
        result["status"] = "ERROR"
        result["error"] = _error_payload(error)
    elif row.get("passed") is True:
        result["status"] = "PASS"
    elif row.get("passed") is False:
        result["status"] = "FAIL"
    else:
        result["status"] = "ERROR"
        result["error"] = {
            "code": "RESULT_STATUS_UNKNOWN",
            "message": "Check result has no pass/fail decision",
        }


def _check_result(descriptor, row, run_id, run):
    request = descriptor["request"]
    result = _base_result(
        "%s:check:%s" % (run_id, descriptor["id"]),
        run_id,
        "check",
        descriptor["id"],
        request,
        descriptor,
        run,
    )
    if row is None:
        return result
    result["value"] = row.get("value")
    if row.get("unit") is not None:
        result["unit"] = row.get("unit")
    if row.get("markers") is not None:
        result["markers"] = row.get("markers")
    _apply_row_status(result, row, "check")
    return result


def _measurement_result(descriptor, row, run_id, run):
    request = descriptor["request"]
    result = _base_result(
        "%s:measurement:%s" % (run_id, descriptor["id"]),
        run_id,
        "measurement",
        descriptor["id"],
        request,
        descriptor,
        run,
    )
    if row is None:
        return result
    _apply_row_status(result, row, "measurement")
    result["value"] = row.get("value")
    if row.get("unit") is not None:
        result["unit"] = row.get("unit")
    if row.get("error") is not None:
        result["error"] = _error_payload(row.get("error"))
    for key in ("markers", "parameters", "temperature_c", "run_ids", "scope"):
        if row.get(key) is not None:
            result[key] = row.get(key)
    return result


def _family_result_id(descriptor):
    return "sweep:%s:%s" % (descriptor["id"], descriptor["group_index"])


def _family_result(descriptor, row):
    request = descriptor["request"]
    result = _base_result(
        _family_result_id(descriptor),
        None,
        "measurement",
        descriptor["id"],
        request,
        descriptor,
        None,
    )
    result["scope"] = "sweep"
    if row is None:
        return result
    _apply_row_status(result, row, "measurement")
    result["value"] = row.get("value")
    if row.get("unit") is not None:
        result["unit"] = row.get("unit")
    if row.get("error") is not None:
        result["error"] = _error_payload(row.get("error"))
    for key in ("run_ids", "markers", "parameters"):
        if row.get(key) is not None:
            result[key] = row.get(key)
    temperature = row.get("temperature_c", descriptor.get("temperature"))
    if temperature is not None:
        result["temperature_c"] = temperature
    return result


def _pop_first(index, key):
    rows = index.get(key)
    if not rows:
        return None
    return rows.pop(0)


def _unexpected(result_id, run_id, kind, identifier, row, scope=None):
    result = {
        "result_id": result_id,
        "run_id": run_id,
        "kind": kind,
        "id": identifier,
        "status": "ERROR",
        "value": row.get("value"),
        "unit": row.get("unit"),
        "error": {
            "code": "REPORT_UNEXPECTED_RESULT",
            "message": "Result row has no planned descriptor",
        },
    }
    if scope is not None:
        result["scope"] = scope
    return result


def _build_results(descriptors, keys, runs, report):
    """Map raw check/measurement rows onto the expected descriptors."""
    problems = []
    results = []
    runs_by_key = {}
    for key_index, key in enumerate(keys):
        runs_by_key[key_index] = _run_for_key(runs, key)
    # Fallback: preserve the historical run order when matching by value fails.
    for key_index in runs_by_key:
        if runs_by_key[key_index] is None and key_index < len(runs):
            runs_by_key[key_index] = runs[key_index]

    local_index = {}
    family_index = {}
    for row in report.get("measurements", []) or []:
        if not isinstance(row, dict):
            continue
        if row.get("scope") == "sweep":
            key = ("sweep", row.get("id"), row.get("temperature_c"))
            family_index.setdefault(key, []).append(row)
        else:
            key = (row.get("run_id"), row.get("id"))
            local_index.setdefault(key, []).append(row)

    checks_by_key = {}
    measurements_by_key = {}
    families = []
    for descriptor in descriptors:
        if descriptor["scope"] == "sweep":
            families.append(descriptor)
        elif descriptor["kind"] == "check":
            checks_by_key.setdefault(descriptor["key_index"], []).append(descriptor)
        else:
            measurements_by_key.setdefault(descriptor["key_index"], []).append(descriptor)

    for key_index, run in runs_by_key.items():
        run_id = run.get("run_id") if isinstance(run, dict) else None
        if run_id is None:
            run_id = "run_%03d" % (key_index + 1)
        expected_checks = checks_by_key.get(key_index, [])
        rows = run.get("checks", []) if isinstance(run, dict) else []
        if not isinstance(rows, list):
            rows = []
        for position, descriptor in enumerate(expected_checks):
            row = rows[position] if position < len(rows) else None
            results.append(_check_result(descriptor, row, run_id, run))
        for position, row in enumerate(rows[len(expected_checks):], start=len(expected_checks)):
            identifier = _check_id(row, position)
            results.append(
                _unexpected(
                    "%s:check:%s" % (run_id, identifier),
                    run_id,
                    "check",
                    identifier,
                    row if isinstance(row, dict) else {},
                )
            )
            problems.append(
                {
                    "code": "REPORT_UNEXPECTED_RESULT",
                    "message": "Check row has no planned descriptor",
                    "run_id": run_id,
                }
            )
        for descriptor in measurements_by_key.get(key_index, []):
            key = (run_id, descriptor["id"])
            row = _pop_first(local_index, key)
            results.append(_measurement_result(descriptor, row, run_id, run))

    for descriptor in families:
        key = ("sweep", descriptor["id"], descriptor["temperature"])
        row = _pop_first(family_index, key)
        results.append(_family_result(descriptor, row))

    for (run_id, identifier), rows in local_index.items():
        for row in rows:
            results.append(
                _unexpected(
                    "%s:measurement:%s" % (run_id, identifier),
                    run_id,
                    "measurement",
                    identifier,
                    row,
                )
            )
            problems.append(
                {
                    "code": "REPORT_UNEXPECTED_RESULT",
                    "message": "Measurement row has no planned descriptor",
                    "run_id": run_id,
                    "id": identifier,
                }
            )
    for (_, identifier, _temperature), rows in family_index.items():
        for row in rows:
            results.append(
                _unexpected(
                    "sweep:%s:unexpected" % (identifier,),
                    None,
                    "measurement",
                    identifier,
                    row,
                    scope="sweep",
                )
            )
            problems.append(
                {
                    "code": "REPORT_UNEXPECTED_RESULT",
                    "message": "Sweep measurement row has no planned descriptor",
                    "id": identifier,
                }
            )
    return results, problems


def _fallback_results(runs, report):
    """Best-effort results for reports whose plan cannot be computed."""
    results = []
    for run_index, run in enumerate(runs):
        if not isinstance(run, dict):
            continue
        run_id = run.get("run_id") or "run_%03d" % (run_index + 1)
        for position, row in enumerate(run.get("checks", []) or []):
            if not isinstance(row, dict):
                continue
            results.append(
                {
                    "result_id": "%s:check:%s" % (run_id, position),
                    "run_id": run_id,
                    "kind": "check",
                    "id": position,
                    "status": "ERROR",
                    "value": row.get("value"),
                    "unit": row.get("unit"),
                    "error": _error_payload(row.get("error"))
                    if row.get("error")
                    else {
                        "code": "REPORT_PLAN_UNKNOWN",
                        "message": "Result plan unavailable",
                    },
                }
            )
    for position, row in enumerate(report.get("measurements", []) or []):
        if not isinstance(row, dict):
            continue
        results.append(
            {
                "result_id": "measurement:%s" % (row.get("id", position),),
                "run_id": row.get("run_id"),
                "kind": "measurement",
                "id": row.get("id", position),
                "status": row.get("status")
                if row.get("status") in RESULT_STATUSES
                else "ERROR",
                "value": row.get("value"),
                "unit": row.get("unit"),
                "error": _error_payload(row.get("error"))
                if row.get("error")
                else None,
                **({"scope": row["scope"]} if row.get("scope") is not None else {}),
            }
        )
    return results


def _dedupe(results):
    seen = set()
    duplicates = []
    deduped = []
    for result in results:
        result_id = result.get("result_id")
        if result_id in seen:
            if result_id not in duplicates:
                duplicates.append(result_id)
            continue
        seen.add(result_id)
        deduped.append(result)
    return deduped, duplicates


def _coverage(results, expected, status="OK"):
    counts = {status_name: 0 for status_name in RESULT_STATUSES}
    for result in results:
        counts[result["status"]] += 1
    completed = counts["PASS"] + counts["FAIL"] + counts["MEASURED"] + counts["ERROR"]
    return {
        "status": status,
        "expected": expected,
        "completed": completed,
        "missing": counts["NOT_RUN"] if expected is not None else None,
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "measured": counts["MEASURED"],
        "errors": counts["ERROR"],
        "not_run": counts["NOT_RUN"],
    }


def _has_assertions(data, results):
    if isinstance(data, dict):
        if data.get("checks"):
            return True
        for request in data.get("measurements", []) or []:
            if isinstance(request, dict) and request.get("limits"):
                return True
    return any(
        result.get("kind") == "check" or result.get("limits") is not None
        for result in results
    )


def _validated_result_value(result):
    """Enforce a finite numeric value for PASS/FAIL/MEASURED results.

    The raw value is preserved by the result builders so that an absent/null
    value (``RESULT_VALUE_MISSING``) can be distinguished from a present but
    invalid value such as NaN, Infinity, a string or a boolean
    (``RESULT_VALUE_INVALID``).
    """
    status = result.get("status")
    present = "value" in result
    value = result.get("value")
    if status in ("PASS", "FAIL", "MEASURED"):
        if not present or value is None:
            code = "RESULT_VALUE_MISSING"
            message = "Result has no numeric value"
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            code = "RESULT_VALUE_INVALID"
            message = "Result has no valid finite numeric value"
        else:
            return result
        result["status"] = "ERROR"
        result["value"] = None
        result["error"] = {"code": code, "message": message}
        return result
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        # Never let NaN/Infinity or non-numeric data reach the final JSON.
        result["value"] = None
    return result


def _apply_status(report, results, expected, problems, plan_unknown=False):
    """Compute the final status with ERROR > FAIL > PASS and report problems."""
    results = [_validated_result_value(result) for result in results]
    results, duplicates = _dedupe(results)
    if duplicates:
        problems.append(
            {
                "code": "REPORT_DUPLICATE_RESULT",
                "message": "Duplicate result_id values: " + ", ".join(duplicates),
                "result_ids": duplicates,
            }
        )
    coverage = _coverage(results, expected, status="UNKNOWN" if plan_unknown else "OK")
    missing = [result["result_id"] for result in results if result["status"] == "NOT_RUN"]
    if not plan_unknown:
        if coverage["missing"]:
            problems.append(
                {
                    "code": "REPORT_INCOMPLETE",
                    "message": "Expected %d results, collected %d"
                    % (expected, coverage["completed"]),
                    "missing_results": missing,
                }
            )
        if len(results) != expected:
            problems.append(
                {
                    "code": "REPORT_INCOMPLETE",
                    "message": "Expected %d results, collected %d"
                    % (expected, len(results)),
                    "missing_results": missing,
                }
            )
    else:
        problems.append(
            {
                "code": "REPORT_PLAN_UNKNOWN",
                "message": "Cannot compute the expected result plan from the input JSON",
            }
        )

    statuses = {result["status"] for result in results}
    existing_error = report.get("error")
    existing_status = report.get("status")
    if existing_error is not None or existing_status == "ERROR":
        status = "ERROR"
    elif plan_unknown:
        status = "ERROR"
    elif "ERROR" in statuses or "NOT_RUN" in statuses:
        status = "ERROR"
    elif problems:
        status = "ERROR"
    elif existing_status == "FAIL" or "FAIL" in statuses:
        status = "FAIL"
    else:
        status = "PASS"

    report["status"] = status
    report["coverage"] = coverage
    report["results"] = results
    report["validation_status"] = (
        "ASSERTIONS"
        if _has_assertions(report.get("_plan_data"), results)
        else "NO_ASSERTIONS"
    )
    if problems:
        report["coverage"]["diagnostics"] = problems
    if status == "ERROR" and report.get("error") is None:
        if problems:
            primary = next(
                (problem for problem in problems if problem["code"] != "REPORT_UNEXPECTED_RESULT"),
                problems[0],
            )
            report["error"] = {"code": primary["code"], "message": primary["message"]}
            if len(problems) > 1:
                report["error"]["diagnostics"] = {"problems": problems}
        else:
            first_error = next(
                (result.get("error") for result in results if result.get("error")),
                None,
            )
            report["error"] = first_error or {
                "code": "REPORT_INCOMPLETE",
                "message": "Report contains results that are not completed successfully",
            }
    elif status == "ERROR" and isinstance(report.get("error"), dict) and problems:
        diagnostics = report["error"].setdefault("diagnostics", {})
        if isinstance(diagnostics, dict):
            diagnostics.setdefault("report_problems", problems)
    return status


def _sanitize_json(value, path="report", problems=None):
    """Return a JSON-safe copy; NaN/Infinity become null recursively.

    Unsupported Python types are not stringified: they are replaced with null
    and recorded as an explicit serialization problem.
    """
    if problems is None:
        problems = []
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_json(item, "%s[%d]" % (path, index), problems)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str):
                safe_key = key
            elif isinstance(key, bool) or key is None or isinstance(key, int):
                safe_key = key
            elif isinstance(key, float):
                if not math.isfinite(key):
                    problems.append(
                        {
                            "code": "REPORT_SERIALIZATION_INVALID",
                            "message": "Non-finite dictionary key",
                            "path": path,
                        }
                    )
                    continue
                safe_key = key
            else:
                problems.append(
                    {
                        "code": "REPORT_SERIALIZATION_INVALID",
                        "message": "Unsupported dictionary key type: " + type(key).__name__,
                        "path": path,
                    }
                )
                continue
            result[safe_key] = _sanitize_json(
                item, "%s.%s" % (path, safe_key), problems
            )
        return result
    problems.append(
        {
            "code": "REPORT_SERIALIZATION_INVALID",
            "message": "Unsupported value type: " + type(value).__name__,
            "path": path,
        }
    )
    return None


def _sanitize_report(report):
    """Sanitize the complete report in place and surface serialization errors."""
    problems = []
    sanitized = _sanitize_json(report, problems=problems)
    report.clear()
    report.update(sanitized)
    if not problems:
        return report
    coverage = report.get("coverage")
    if isinstance(coverage, dict):
        diagnostics = coverage.get("diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.extend(problems)
        else:
            coverage["diagnostics"] = problems
    report["status"] = "ERROR"
    error = report.get("error")
    if error is None:
        report["error"] = {
            "code": "REPORT_SERIALIZATION_INVALID",
            "message": "Report contains values that cannot be serialized to JSON",
            "diagnostics": {"problems": problems},
        }
    elif isinstance(error, dict):
        diagnostics = error.setdefault("diagnostics", {})
        if isinstance(diagnostics, dict):
            diagnostics.setdefault("report_problems", []).extend(problems)
    return report


def finalize_report(report, data=None):
    """Attach ``schema_version``, ``results[]``, ``coverage`` and final status.

    This is the single normalization entry point.  It must be called only after
    all measurements have been aggregated (``finish_measurements``), so that a
    row is never counted both in an intermediate structure and in ``results[]``.
    """
    if not isinstance(report, dict):
        return report
    if (
        report.get("schema_version") == SCHEMA_VERSION
        and isinstance(report.get("results"), list)
        and isinstance(report.get("coverage"), dict)
    ):
        # Already normalized.  Sanitize again for callers that mutated the
        # report, but do not rebuild results from already-sanitized raw fields:
        # that would turn RESULT_VALUE_INVALID into RESULT_VALUE_MISSING.
        return _sanitize_report(report)
    report["schema_version"] = SCHEMA_VERSION
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        runs = [report]
    for index, run in enumerate(runs):
        if isinstance(run, dict) and not run.get("run_id"):
            run["run_id"] = "run_%03d" % (index + 1)
    report["_plan_data"] = data
    descriptors, keys, _ = _plan_bundle(data) if isinstance(data, dict) else (None, None, None)
    if descriptors is None:
        results = _fallback_results(runs, report)
        _apply_status(report, results, None, [], plan_unknown=True)
    else:
        results, problems = _build_results(descriptors, keys, runs, report)
        _apply_status(report, results, len(descriptors), problems)
    report.pop("_plan_data", None)
    return _sanitize_report(report)
