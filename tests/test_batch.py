# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Batch preflight and service-command CLI tests."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from batch_agent import run_batch, validate_report_contract
from spice_sifter import main


def ready_doctor():
    return {
        "status": "READY",
        "ready": True,
        "altium": {"running": True, "responding": True},
        "lock": {"available": True},
        "pending_run": None,
        "temporary_directory": {"writable": True},
    }


def blocked_doctor(state="ACTIVE"):
    return {
        "status": "BUSY",
        "ready": False,
        "altium": {"running": True, "responding": True},
        "lock": {"available": True},
        "pending_run": {"state": state, "run_id": "a38f52"},
        "temporary_directory": {"writable": True},
        "diagnostics": ["A previous simulation is still running."],
    }


def valid_report(status="PASS", value=1.0):
    result_status = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERROR"}[status]
    counts = {
        "passed": 1 if status == "PASS" else 0,
        "failed": 1 if status == "FAIL" else 0,
        "measured": 0,
        "errors": 1 if status == "ERROR" else 0,
        "not_run": 0,
    }
    return {
        "schema_version": "1.0",
        "status": status,
        "validation_status": "ASSERTIONS",
        "results": [
            {
                "result_id": "run_001:check:0",
                "run_id": "run_001",
                "kind": "check",
                "id": 0,
                "status": result_status,
                "value": value if status != "ERROR" else None,
                "unit": None,
                "limits": {"min": 0, "max": 2},
                "error": None if status != "ERROR" else {"code": "SOME_ERROR", "message": "x"},
            }
        ],
        "coverage": {
            "status": "OK",
            "expected": 1,
            "completed": 1,
            "missing": 0,
            **counts,
        },
        "error": None if status != "ERROR" else {"code": "SOME_ERROR", "message": "x"},
    }


def incomplete_report():
    results = [
        {
            "result_id": "run_%03d:check:0" % index,
            "run_id": "run_%03d" % index,
            "kind": "check",
            "id": 0,
            "status": "PASS",
            "value": 1.0,
            "unit": None,
            "error": None,
        }
        for index in range(1, 6)
    ]
    return {
        "schema_version": "1.0",
        "status": "PASS",
        "validation_status": "ASSERTIONS",
        "results": results,
        "coverage": {
            "status": "OK",
            "expected": 14,
            "completed": 5,
            "missing": 9,
            "passed": 5,
            "failed": 0,
            "measured": 0,
            "errors": 0,
            "not_run": 9,
        },
        "error": None,
    }


class BatchTests(unittest.TestCase):
    def test_preflight_blocks_all_twelve_before_launch(self):
        calls = []

        def runner(*args, **kwargs):
            calls.append(args)
            raise AssertionError("runner must not be called when preflight fails")

        result = run_batch(
            ["component_%02d" % index for index in range(12)],
            runner=runner,
            doctor=blocked_doctor,
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "ALTIUM_BLOCKED_BY_PENDING_RUN")
        self.assertEqual(result["summary"]["planned_runs"], 12)
        self.assertEqual(result["summary"]["completed_runs"], 0)
        self.assertEqual(result["summary"]["not_run"], 12)
        self.assertEqual([run["status"] for run in result["runs"]], ["NOT_RUN"] * 12)

    def test_ready_batch_runs_and_keeps_results(self):
        reports = {
            "a": valid_report("PASS"),
            "b": valid_report("FAIL", value=3.0),
            "c": valid_report("PASS"),
        }

        def runner(path, **kwargs):
            return reports[path]

        result = run_batch(["a", "b", "c"], runner=runner, doctor=ready_doctor)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["summary"]["completed_runs"], 3)
        self.assertEqual(result["summary"]["not_run"], 0)
        self.assertEqual([run["status"] for run in result["runs"]], ["PASS", "FAIL", "PASS"])

    def test_incomplete_report_is_not_accepted_as_pass(self):
        result = run_batch(
            ["only"],
            runner=lambda path, **kwargs: incomplete_report(),
            doctor=ready_doctor,
        )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "REPORT_INCOMPLETE")
        self.assertEqual(result["summary"]["completed_runs"], 0)
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["runs"][0]["status"], "ERROR")
        self.assertIn("report", result["runs"][0]["error"]["diagnostics"])

    def test_contract_invalid_report_is_not_accepted(self):
        broken = valid_report("PASS")
        broken.pop("schema_version")
        result = run_batch(
            ["only"], runner=lambda path, **kwargs: broken, doctor=ready_doctor
        )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "REPORT_CONTRACT_INVALID")
        self.assertEqual(result["summary"]["errors"], 1)

    def test_pass_with_empty_results_is_not_accepted(self):
        report = {
            "schema_version": "1.0",
            "status": "PASS",
            "validation_status": "ASSERTIONS",
            "results": [],
            "coverage": {
                "status": "OK",
                "expected": 14,
                "completed": 5,
                "missing": 9,
                "passed": 5,
                "failed": 0,
                "measured": 0,
                "errors": 0,
                "not_run": 9,
            },
            "error": None,
        }
        result = run_batch(
            ["only"], runner=lambda path, **kwargs: report, doctor=ready_doctor
        )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "REPORT_INCOMPLETE")
        self.assertNotEqual(result["runs"][0]["status"], "PASS")

    def test_pass_with_error_is_contract_invalid(self):
        report = valid_report("PASS")
        report["error"] = {"code": "SOME_ERROR", "message": "x"}
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_missing_validation_status_is_contract_invalid(self):
        report = valid_report("PASS")
        report.pop("validation_status")
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_missing_result_attribute_is_contract_invalid(self):
        report = valid_report("PASS")
        report["results"][0].pop("unit")
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_unknown_kind_is_contract_invalid(self):
        report = valid_report("PASS")
        report["results"][0]["kind"] = "other"
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_pass_with_error_result_is_rejected(self):
        report = valid_report("PASS")
        report["results"][0] = {
            "result_id": "run_001:check:0",
            "run_id": "run_001",
            "kind": "check",
            "id": 0,
            "status": "ERROR",
            "value": None,
            "unit": None,
            "error": {"code": "E", "message": "m"},
        }
        report["coverage"].update(passed=0, errors=1)
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_fail_without_fail_result_is_rejected(self):
        report = valid_report("FAIL")
        report["results"][0] = valid_report("PASS")["results"][0]
        report["coverage"].update(passed=1, failed=0)
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_negative_coverage_counter_is_rejected(self):
        report = valid_report("PASS")
        report["coverage"]["errors"] = -1
        ok, code, _ = validate_report_contract(report)
        self.assertFalse(ok)
        self.assertEqual(code, "REPORT_CONTRACT_INVALID")

    def test_valid_statuses_pass_contract(self):
        for status in ("PASS", "FAIL", "ERROR"):
            with self.subTest(status=status):
                ok, code, message = validate_report_contract(valid_report(status))
                self.assertTrue(ok, (code, message))

    def test_systemic_error_stops_remaining_series(self):
        calls = []

        def runner(path, **kwargs):
            calls.append(path)
            if path == "a":
                return valid_report("PASS")
            return {
                "schema_version": "1.0",
                "status": "ERROR",
                "error": {"code": "TIMEOUT", "message": "no status"},
                "results": [],
                "coverage": {},
            }

        result = run_batch(["a", "b", "c", "d"], runner=runner, doctor=ready_doctor)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "TIMEOUT")
        self.assertEqual(result["summary"]["completed_runs"], 1)
        self.assertEqual(result["summary"]["errors"], 1)
        self.assertEqual(result["summary"]["not_run"], 2)
        self.assertEqual([run["status"] for run in result["runs"]], ["PASS", "ERROR", "NOT_RUN", "NOT_RUN"])

    def test_doctor_json_cli(self):
        output = io.StringIO()
        with patch("runtime_state.doctor_report", return_value=ready_doctor()), redirect_stdout(output):
            code = main(["doctor", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "READY")
        self.assertTrue(payload["ready"])

    def test_doctor_not_ready_exit_code(self):
        output = io.StringIO()
        with patch("runtime_state.doctor_report", return_value=blocked_doctor()), redirect_stdout(output):
            code = main(["doctor", "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "BUSY")

    def test_recover_cli(self):
        recovered = {
            "status": "RECOVERED",
            "run_id": "a38f52",
            "reason": "Previous Altium process has exited",
            "ready": True,
        }
        output = io.StringIO()
        with patch("runtime_state.recover_pending", return_value=recovered), redirect_stdout(output):
            code = main(["recover"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "RECOVERED")

    def test_recover_refused_exit_code(self):
        refused = {"status": "REFUSED", "ready": False, "reason": "still running"}
        output = io.StringIO()
        with patch("runtime_state.recover_pending", return_value=refused), redirect_stdout(output):
            code = main(["recover"])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["ready"])


if __name__ == "__main__":
    unittest.main()
