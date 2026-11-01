# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Report contract tests: results[], coverage, status semantics and JSON Schema."""
import json
import unittest
from pathlib import Path

from report_model import finalize_report, plan_results
from spice_sifter import finish_measurements

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "report.schema.json"

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


def divider_data():
    return json.loads((ROOT / "examples/divider.json").read_text(encoding="utf-8"))


def check_row(request, value=2.5, passed=True):
    return dict(request, value=value, passed=passed)


class ContractTests(unittest.TestCase):
    def validate_schema(self, report):
        if jsonschema is None:
            self.skipTest("jsonschema is not installed")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(report, schema)

    def test_standalone_all_results_present(self):
        data = divider_data()
        data["checks"] = [dict(data["checks"][0]), dict(data["checks"][0], measurement="max")]
        data["measurements"] = [
            {"id": "mean_out", "type": "mean", "signal": "v(out)"},
            {"id": "p2p", "type": "peak_to_peak", "signal": "v(out)", "limits": {"min": 0, "max": 5}},
        ]
        report = {
            "status": "PASS",
            "error": None,
            "run_id": "run_001",
            "checks": [check_row(data["checks"][0]), check_row(data["checks"][1])],
            "measurements": [
                {"id": "mean_out", "type": "mean", "value": 2.5, "unit": "V", "status": "MEASURED", "run_id": "run_001"},
                {"id": "p2p", "type": "peak_to_peak", "value": 0.0, "unit": "V", "status": "PASS", "run_id": "run_001", "limits": {"min": 0, "max": 5}},
            ],
        }
        finalize_report(report, data)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["results"]), 4)
        self.assertEqual(
            [row["status"] for row in report["results"]],
            ["PASS", "PASS", "MEASURED", "PASS"],
        )
        self.assertEqual(report["coverage"]["expected"], 4)
        self.assertEqual(report["coverage"]["completed"], 4)
        self.assertEqual(report["coverage"]["missing"], 0)
        self.validate_schema(report)

    def test_temperature_expected_is_per_run(self):
        data = json.loads((ROOT / "examples/temperature_series.json").read_text(encoding="utf-8"))
        report = {"status": "PASS", "error": None, "runs": []}
        for temperature in data["temperatures_c"]:
            request = next(c for c in data["checks"] if c["temperature_c"] == temperature)
            report["runs"].append(
                {
                    "run_id": "temp_%s" % temperature,
                    "temperature_c": temperature,
                    "checks": [check_row(request, value=0.7)],
                }
            )
        finalize_report(report, data)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["expected"], 3)
        self.assertEqual(report["coverage"]["completed"], 3)
        self.assertEqual(len(report["results"]), 3)
        self.assertEqual(
            [row["temperature_c"] for row in report["results"]], [-40, 25, 85]
        )
        self.validate_schema(report)

    def test_sweep_when_filters_expected_count(self):
        data = json.loads((ROOT / "examples/sweep_divider.json").read_text(encoding="utf-8"))
        report = {"status": "PASS", "error": None, "runs": []}
        for vin, rload in [(2, 1000), (2, 2000), (5, 1000), (5, 2000), (10, 1000), (10, 2000)]:
            checks = []
            if (vin, rload) == (5, 1000):
                checks = [check_row(data["checks"][0])]
            report["runs"].append(
                {
                    "run_id": "sweep_%s_%s" % (vin, rload),
                    "parameters": {"VIN": vin, "RLOAD": rload},
                    "checks": checks,
                }
            )
        finalize_report(report, data)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["expected"], 1)
        self.assertEqual(report["coverage"]["completed"], 1)
        self.assertEqual(len(report["results"]), 1)
        self.validate_schema(report)

    def test_sweep_family_measurement_once_per_group(self):
        request = {
            "id": "vth",
            "type": "sweep_crossing",
            "parameter": "VGS",
            "signal": "id",
            "level": 0.25,
            "edge": "rising",
        }
        data = {
            "name": "family",
            "spice": "* {{VGS}}",
            "sweep": {"parameters": {"VGS": {"values": [1, 2, 3]}}},
            "temperatures_c": [-40, 25, 85],
            "measurements": [request],
        }
        report = {"status": "PASS", "error": None, "runs": []}
        for temperature in data["temperatures_c"]:
            for vgs, value in [(1, 0.1), (2, 0.2), (3, 0.4)]:
                report["runs"].append(
                    {
                        "run_id": "%s_%s" % (temperature, vgs),
                        "parameters": {"VGS": vgs},
                        "temperature_c": temperature,
                        "_measurement_requests": [request],
                        "_sweep_samples": {
                            "vth": {"status": "MEASURED", "value": value, "unit": "A"}
                        },
                    }
                )
        finish_measurements(data, report)
        finalize_report(report, data)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["expected"], 3)
        self.assertEqual(report["coverage"]["completed"], 3)
        families = [row for row in report["results"] if row.get("scope") == "sweep"]
        self.assertEqual(len(families), 3)
        self.assertTrue(all(row["run_id"] is None for row in families))
        self.assertTrue(all(len(row["run_ids"]) == 3 for row in families))
        self.assertEqual([row["temperature_c"] for row in families], [-40, 25, 85])
        self.validate_schema(report)

    def test_lost_result_is_report_incomplete(self):
        data = divider_data()
        data["checks"] = [dict(data["checks"][0]), dict(data["checks"][0], measurement="max")]
        report = {"status": "PASS", "error": None, "checks": [check_row(data["checks"][0])]}
        finalize_report(report, data)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["error"]["code"], "REPORT_INCOMPLETE")
        self.assertEqual(report["coverage"]["expected"], 2)
        self.assertEqual(report["coverage"]["completed"], 1)
        self.assertEqual(report["coverage"]["missing"], 1)
        self.assertEqual(report["coverage"]["not_run"], 1)
        self.assertEqual(
            [row["status"] for row in report["results"]], ["PASS", "NOT_RUN"]
        )
        self.assertNotEqual(report["status"], "PASS")

    def test_duplicate_result_id_is_error(self):
        data = divider_data()
        data["measurements"] = [{"id": "m1", "type": "mean", "signal": "v(out)"}]
        row = {"id": "m1", "type": "mean", "value": 2.5, "unit": "V", "status": "MEASURED", "run_id": "run_001"}
        report = {"status": "PASS", "error": None, "run_id": "run_001", "measurements": [dict(row), dict(row)]}
        finalize_report(report, data)
        self.assertEqual(report["status"], "ERROR")
        codes = [problem["code"] for problem in report["coverage"]["diagnostics"]]
        self.assertTrue(
            "REPORT_DUPLICATE_RESULT" in codes or "REPORT_UNEXPECTED_RESULT" in codes,
            codes,
        )
        self.assertEqual(len({r["result_id"] for r in report["results"]}), len(report["results"]))

    def test_measured_without_limits_is_not_pass_assertion(self):
        data = divider_data()
        data["checks"] = []
        data["measurements"] = [{"id": "mean_out", "type": "mean", "signal": "v(out)"}]
        report = {
            "status": "PASS",
            "error": None,
            "run_id": "run_001",
            "measurements": [
                {"id": "mean_out", "type": "mean", "value": 2.5, "unit": "V", "status": "MEASURED", "run_id": "run_001"}
            ],
        }
        finalize_report(report, data)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["validation_status"], "NO_ASSERTIONS")
        self.assertEqual(report["results"][0]["status"], "MEASURED")

    def test_not_run_never_passes(self):
        data = divider_data()
        report = {"status": "PASS", "error": None, "checks": []}
        finalize_report(report, data)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["results"][0]["status"], "NOT_RUN")
        self.assertEqual(report["coverage"]["missing"], 1)

    def test_fail_priority(self):
        data = divider_data()
        report = {"status": "PASS", "error": None, "checks": [check_row(data["checks"][0], value=3.1, passed=False)]}
        finalize_report(report, data)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["results"][0]["status"], "FAIL")
        self.assertEqual(report["coverage"]["failed"], 1)

    def test_component_results_use_same_collection(self):
        data = {
            "name": "component",
            "component": {"library": "C:/L/MY.DbLib", "page": "T", "id": "R1"},
            "fixture": "resistor_dc",
            "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.1, "max": -0.9}],
            "measurements": [{"id": "m1", "type": "at", "signal": "v(dut_p)", "analysis": "op", "x": 0}],
        }
        report = {
            "status": "PASS",
            "error": None,
            "run_id": "20261101_000000_abcdef",
            "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.1, "max": -0.9, "value": -1.0, "passed": True}],
            "measurements": [{"id": "m1", "type": "at", "value": -1.0, "unit": "V", "status": "MEASURED", "run_id": "20261101_000000_abcdef"}],
        }
        finalize_report(report, data)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["coverage"]["expected"], 2)
        self.assertEqual([row["kind"] for row in report["results"]], ["check", "measurement"])
        self.validate_schema(report)

    def test_component_structural_failure_marks_planned_results_not_run(self):
        data = {
            "name": "component",
            "component": {"library": "C:/L/MY.DbLib", "page": "T", "id": "R1"},
            "fixture": "resistor_dc",
            "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.1, "max": -0.9}],
            "measurements": [{"id": "m1", "type": "at", "signal": "v(dut_p)", "analysis": "op", "x": 0}],
        }
        report = {"status": "ERROR", "error": {"code": "LIBRARY_NOT_FOUND", "message": "missing"}, "checks": []}
        finalize_report(report, data)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["coverage"]["expected"], 2)
        self.assertEqual(report["coverage"]["not_run"], 2)
        self.assertEqual([row["status"] for row in report["results"]], ["NOT_RUN", "NOT_RUN"])

    def test_plan_is_stable_for_checks_without_ids(self):
        data = divider_data()
        data["checks"] = [dict(data["checks"][0]), dict(data["checks"][0])]
        descriptors = plan_results(data)
        self.assertEqual([d["id"] for d in descriptors], [0, 1])

    def test_unknown_plan_reports_unknown_coverage(self):
        report = {"status": "PASS", "error": None, "checks": []}
        finalize_report(report, None)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["coverage"]["status"], "UNKNOWN")
        self.assertIsNone(report["coverage"]["expected"])
        self.assertEqual(report["error"]["code"], "REPORT_PLAN_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
