# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Batch preflight and service-command CLI tests."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from batch_agent import run_batch
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
            "a": {"status": "PASS", "error": None, "results": []},
            "b": {"status": "FAIL", "error": None, "results": []},
            "c": {"status": "PASS", "error": None, "results": []},
        }

        def runner(path, **kwargs):
            return reports[path]

        result = run_batch(["a", "b", "c"], runner=runner, doctor=ready_doctor)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["summary"]["completed_runs"], 3)
        self.assertEqual(result["summary"]["not_run"], 0)
        self.assertEqual([run["status"] for run in result["runs"]], ["PASS", "FAIL", "PASS"])

    def test_systemic_error_stops_remaining_series(self):
        calls = []

        def runner(path, **kwargs):
            calls.append(path)
            if path == "a":
                return {"status": "PASS", "error": None}
            return {"status": "ERROR", "error": {"code": "TIMEOUT", "message": "no status"}}

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
