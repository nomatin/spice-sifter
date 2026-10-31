# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dblib_runner as dr
from spice_sifter import SDFChart, SDFDocument, SDFWave, TestError, load_test


def write_json(folder, name, data):
    path = Path(folder) / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def component_data(**overrides):
    data = {
        "name": "component_test",
        "component": {"library": "MY_LIB.DbLib", "page": "Резисторы", "id": "R0009"},
        "fixture": "resistor_dc",
        "checks": [{"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": -1.1, "max": -0.9}],
    }
    data.update(overrides)
    return data


def all_pass_status(**overrides):
    status = { "stage_" + name: "PASS" for name in dr.STAGES }
    status.update({
        "STATUS": "OK",
        "script_failed": "0",
        "schematic_saved_after_cleanup": "1",
        "database_keys": "R0009",
        "design_item_id": "R0009",
        "database_library_name": "MY_LIB.DbLib",
        "database_table_name": "Резисторы$",
        "pin_count": "2",
        "sim_model_found": "True",
        "sim_model_name": "MY_LIB_R_TCR",
        "sim_port_map": "(1:1),(2:2)",
    })
    status.update(overrides)
    return status


class InputTests(unittest.TestCase):
    def test_component_json_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "ok.json", component_data())
            data = load_test(path)
            self.assertNotIn("spice", data)
            self.assertEqual(data["component"]["id"], "R0009")

    def test_mutually_exclusive_spice_and_component(self):
        with tempfile.TemporaryDirectory() as td:
            data = component_data(spice="* test\n.OP\n.END")
            with self.assertRaises(TestError) as ctx:
                load_test(write_json(td, "both.json", data))
            self.assertEqual(ctx.exception.code, "INPUT_INVALID")

    def test_required_component_fields(self):
        for field in ("library", "page", "id"):
            data = component_data()
            data["component"][field] = ""
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(TestError) as ctx:
                    load_test(write_json(td, "bad.json", data))
                self.assertEqual(ctx.exception.code, "INPUT_INVALID")

    def test_component_measurements_and_graphs_accepted(self):
        data = component_data(
            checks=[],
            measurements=[{"id": "at0", "type": "at", "signal": "v(dut_p)", "analysis": "op", "x": 0}],
            output={"graphs": [{"name": "op", "type": "signal", "signal": "v(dut_p)", "analysis": "op"}]},
        )
        with tempfile.TemporaryDirectory() as td:
            parsed = load_test(write_json(td, "measure.json", data))
        self.assertEqual(parsed["measurements"][0]["id"], "at0")
        self.assertEqual(parsed["output"]["graphs"][0]["name"], "op")

    def test_component_without_checks_accepted(self):
        data = component_data()
        data.pop("checks")
        with tempfile.TemporaryDirectory() as td:
            parsed = load_test(write_json(td, "no_checks.json", data))
        self.assertEqual(parsed["name"], "component_test")

    def test_fixture_and_unsupported_layers(self):
        for data in (
            component_data(fixture="unknown"),
            component_data(sweep={"parameters": {"X": {"values": [1]}}}),
            component_data(temperatures_c=[25]),
            component_data(models=["models/diode.lib"]),
        ):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(TestError) as ctx:
                    load_test(write_json(td, "bad.json", data))
                self.assertEqual(ctx.exception.code, "INPUT_INVALID")
        for request in (
            {"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": 0, "max": 1, "when": {"X": 1}},
            {"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": 0, "max": 1, "temperature_c": 25},
        ):
            with self.subTest(request=request), tempfile.TemporaryDirectory() as td:
                with self.assertRaises(TestError):
                    load_test(write_json(td, "bad.json", component_data(checks=[request])))


class LibraryTests(unittest.TestCase):
    def test_table_normalization(self):
        self.assertEqual(dr.normalize_table("Резисторы"), "Резисторы$")
        self.assertEqual(dr.normalize_table("Резисторы$"), "Резисторы$")

    def test_dblib_tables(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "X.DbLib"
            path.write_bytes("[Table1]\nTableName=Резисторы$\n[Table2]\nTableName=Диоды$\n".encode("cp1251"))
            self.assertEqual(dr.library_tables(path), ["Резисторы$", "Диоды$"])

    def test_resolve_requires_absolute_existing_dblib(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "MY_TEST" / "MY_TEST" / "MY_TEST.DbLib"
            target.parent.mkdir(parents=True)
            target.write_text("", encoding="ascii")
            self.assertEqual(dr.resolve_library(str(target)), target.resolve())
            for value in ("MY_TEST.DbLib", "libraries/MY_TEST.DbLib"):
                with self.subTest(value=value), self.assertRaises(dr.DblibError) as ctx:
                    dr.resolve_library(value)
                self.assertEqual(ctx.exception.code, "LIBRARY_PATH_INVALID")
                self.assertEqual(
                    ctx.exception.message,
                    "component.library must be an absolute path to a .DbLib file",
                )
            other = Path(td) / "library.SVNDbLib"
            other.write_text("", encoding="ascii")
            with self.assertRaises(dr.DblibError) as ctx:
                dr.resolve_library(str(other))
            self.assertEqual(ctx.exception.code, "LIBRARY_PATH_INVALID")
            with self.assertRaises(dr.DblibError) as ctx:
                dr.resolve_library(str(Path(td) / "missing.DbLib"))
            self.assertEqual(ctx.exception.code, "LIBRARY_NOT_FOUND")

    def test_preflight_unknown_page(self):
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td) / "MY_LIB.DbLib"
            lib.write_bytes("[Table1]\nTableName=Резисторы$\n".encode("cp1251"))
            data = component_data()
            data["component"]["library"] = str(lib)
            data["component"]["page"] = "НетТакойСтраницы"
            with self.assertRaises(dr.DblibError) as ctx:
                dr.preflight_component(data)
            self.assertEqual(ctx.exception.code, "PAGE_NOT_FOUND")


class ScriptTests(unittest.TestCase):
    def test_render_script(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            script = dr._render_script(
                run, "Test_x", Path("C:/Libraries/MY_LIB.DbLib"), "Резисторы$", "R0009", "diode_forward"
            )
            text = script.read_text(encoding="ascii")
            self.assertNotIn("__", text)
            self.assertIn("DcValue := '-0.01';", text)
            self.assertIn("TableName := #", text)
            self.assertIn("R0009", text)

    def test_bad_label_fixture(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            script = dr._render_script(
                run, "Test_x", Path("C:/Libraries/MY_LIB.DbLib"), "Резисторы$", "R0009", "resistor_bad_label"
            )
            self.assertIn("Pin1X - 500", script.read_text(encoding="ascii"))


class NetlistTests(unittest.TestCase):
    def test_valid_netlist(self):
        with tempfile.TemporaryDirectory() as td:
            nsx = Path(td) / "test.nsx"
            nsx.write_text(
                "XR? DUT_P 0 MY_LIB_R_TCR PARAMS: Rvalue=1R\n"
                ".SUBCKT MY_LIB_R_TCR 1 2 PARAMS: Rvalue=1\nRthermal 1 2 RTCR {Rvalue}\n.ENDS\n.END\n",
                encoding="utf-8",
            )
            checks = dr.verify_netlist(nsx, "MY_LIB_R_TCR")
            self.assertTrue(checks["all"], checks)
            self.assertTrue(checks["pin_order_matches"])

    def test_duplicate_or_wrong_order(self):
        with tempfile.TemporaryDirectory() as td:
            nsx = Path(td) / "test.nsx"
            nsx.write_text(
                "XR? DUT_P 0 MY_LIB_R_TCR\nXR? DUT_P 0 MY_LIB_R_TCR\n"
                ".SUBCKT MY_LIB_R_TCR 1 2\n.ENDS\n.END\n",
                encoding="utf-8",
            )
            checks = dr.verify_netlist(nsx, "MY_LIB_R_TCR")
            self.assertFalse(checks["one_dut_instance"])
            self.assertFalse(checks["all"])
            nsx.write_text(
                "XR? 0 DUT_P MY_LIB_R_TCR\n.SUBCKT MY_LIB_R_TCR 1 2\n.ENDS\n.END\n",
                encoding="utf-8",
            )
            checks = dr.verify_netlist(nsx, "MY_LIB_R_TCR")
            self.assertFalse(checks["pin_order_matches"])

    def test_missing_model_definition(self):
        with tempfile.TemporaryDirectory() as td:
            nsx = Path(td) / "test.nsx"
            nsx.write_text("XR? DUT_P 0 OTHER\n.END\n", encoding="utf-8")
            checks = dr.verify_netlist(nsx, "MY_LIB_R_TCR")
            self.assertFalse(checks["model_definition"])
            self.assertFalse(checks["all"])


class ReportGuardTests(unittest.TestCase):
    def wave(self, name="v(dut_p)", unit="V", x=(0,), y=(-1.0,), complex_data=False):
        return SDFWave(name, unit, "General", len(x), list(x), list(y), complex_data)

    def test_identical_duplicate_charts_are_collapsed(self):
        doc = SDFDocument("1.0", [
            SDFChart("Operating Point", [self.wave()]),
            SDFChart("Operating Point", [self.wave()]),
            SDFChart("Transient Analysis", [self.wave()]),
        ])
        reduced = dr._normalized_document(doc)
        self.assertEqual([c.name for c in reduced.charts], ["Operating Point", "Transient Analysis"])

    def test_conflicting_duplicate_charts_are_rejected(self):
        cases = [
            ("Y", [SDFChart("Operating Point", [self.wave(y=(-1.0,))]),
                   SDFChart("Operating Point", [self.wave(y=(-2.0,))])]),
            ("X", [SDFChart("Operating Point", [self.wave(x=(0,))]),
                   SDFChart("Operating Point", [self.wave(x=(1,))])]),
            ("unit", [SDFChart("Operating Point", [self.wave(unit="V")]),
                      SDFChart("Operating Point", [self.wave(unit="mV")])]),
            ("Y", [SDFChart("Operating Point", [self.wave(y=(-1 + 0j,), complex_data=True)]),
                   SDFChart("Operating Point", [self.wave(y=(-1 + 1j,), complex_data=True)])]),
        ]
        for reason, charts in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(dr.DblibError) as ctx:
                    dr._normalized_document(SDFDocument("1.0", charts))
                self.assertEqual(ctx.exception.code, "SDF_DUPLICATE_CONFLICT")
                self.assertEqual(ctx.exception.message, "Duplicate SDF charts contain different data")
                self.assertIn(reason, ctx.exception.diagnostics["reason"])

    def test_different_analyses_and_signals_are_not_merged(self):
        doc = SDFDocument("1.0", [
            SDFChart("Operating Point", [self.wave(name="v(a)")]),
            SDFChart("Operating Point", [self.wave(name="v(b)")]),
            SDFChart("Transient Analysis", [self.wave(name="v(a)")]),
        ])
        reduced = dr._normalized_document(doc)
        self.assertEqual(len(reduced.charts), 3)

    def test_equivalent_complex_duplicates_are_collapsed(self):
        zone = self.wave(y=(-1 + 2j,), complex_data=True)
        doc = SDFDocument("1.0", [
            SDFChart("AC Analysis", [zone]),
            SDFChart("AC Analysis", [self.wave(y=(-1 + 2j,), complex_data=True)]),
        ])
        self.assertEqual(len(dr._normalized_document(doc).charts), 1)

    def test_save_failure_is_not_pass(self):
        status = all_pass_status(schematic_saved_after_cleanup="0")
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SCHEMATIC_CLEANUP_FAILED")

    def test_script_failed_is_not_pass(self):
        status = all_pass_status(
            script_failed="1", STATUS="ERROR",
            error_code="SDF_NOT_SAVED", error_stage="simulation", error_message="no SDF",
        )
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SDF_NOT_SAVED")

    def test_status_error_concrete_code_is_preserved(self):
        status = all_pass_status(
            STATUS="ERROR", script_failed="0",
            error_code="SDF_NOT_SAVED", error_stage="simulation", error_message="no SDF",
        )
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SDF_NOT_SAVED")
        self.assertEqual(error["stage"], "simulation")

    def test_simulation_error_with_successful_cleanup(self):
        status = all_pass_status(
            stage_simulation="FAIL", script_failed="1", STATUS="ERROR",
            error_code="SDF_NOT_SAVED", error_stage="simulation", error_message="no SDF",
        )
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SDF_NOT_SAVED")
        self.assertEqual(error["stage"], "simulation")
        self.assertEqual(status["schematic_saved_after_cleanup"], "1")

    def test_simulation_and_cleanup_errors_are_both_reported(self):
        status = all_pass_status(
            stage_simulation="FAIL", stage_schematic_cleanup="FAIL",
            script_failed="1", STATUS="ERROR",
            error_code="SDF_NOT_SAVED", error_stage="simulation", error_message="no SDF",
            cleanup_error="SCHEMATIC_CLEANUP_FAILED", schematic_saved_after_cleanup="0",
        )
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SDF_NOT_SAVED")
        self.assertEqual(error["diagnostics"]["cleanup_error"], "SCHEMATIC_CLEANUP_FAILED")

    def test_open_error_is_not_masked_by_cleanup(self):
        status = all_pass_status(
            stage_library_lookup="FAIL", stage_schematic_cleanup="NOT_APPLICABLE",
            script_failed="1", STATUS="ERROR", schematic_saved_after_cleanup="0",
            error_code="SCHEMATIC_NOT_OPENED", error_stage="library_lookup",
            error_message="schematic not opened",
        )
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SCHEMATIC_NOT_OPENED")
        self.assertEqual(error["stage"], "library_lookup")

    def test_missing_nsx_is_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            status = all_pass_status(nsx_path=str(run / "missing.nsx"), sdf_path=str(run / "missing.sdf"))
            report = dr.build_report({}, component_data(), run, status, start=0)
            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["error"]["code"], "NSX_NOT_GENERATED")
            self.assertNotEqual(report["status"], "PASS")

    def test_invalid_nsx_is_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            nsx = run / "test.nsx"
            nsx.write_text("XR? DUT_P 0 OTHER\n.END\n", encoding="utf-8")
            status = all_pass_status(nsx_path=str(nsx), sdf_path=str(run / "test.sdf"))
            report = dr.build_report({}, component_data(), run, status, start=0)
            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["error"]["code"], "NSX_INVALID")

    def test_missing_sdf_is_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            nsx = run / "test.nsx"
            nsx.write_text(
                "XR? DUT_P 0 MY_LIB_R_TCR\n.SUBCKT MY_LIB_R_TCR 1 2\n.ENDS\n.END\n",
                encoding="utf-8",
            )
            status = all_pass_status(nsx_path=str(nsx), sdf_path=str(run / "missing.sdf"))
            report = dr.build_report({}, component_data(), run, status, start=0)
            self.assertEqual(report["status"], "ERROR")
            self.assertEqual(report["error"]["code"], "SDF_NOT_SAVED")
            self.assertEqual(report["stages"]["simulation"], "FAIL")

    def test_status_error_mapping(self):
        status = all_pass_status(
            stage_connectivity="FAIL",
            error_stage="connectivity",
            error_code="CONNECTIVITY_FAILED",
            error_message="bad labels",
        )
        stages = dr._stages(status)
        error = dr._status_error(status, stages)
        self.assertEqual(error["code"], "CONNECTIVITY_FAILED")
        self.assertEqual(error["stage"], "connectivity")

    def test_cleanup_failure_is_error(self):
        status = all_pass_status(stage_schematic_cleanup="FAIL", cleanup_error="SCHEMATIC_CLEANUP_FAILED")
        error = dr._status_error(status, dr._stages(status))
        self.assertEqual(error["code"], "SCHEMATIC_CLEANUP_FAILED")

    def test_early_preflight_error(self):
        report = {}
        with patch.object(
            dr, "preflight_component",
            side_effect=dr.DblibError("library_lookup", "LIBRARY_NOT_FOUND", "missing"),
        ):
            dr.execute_component("X2.EXE", component_data(), Path("."), 1, False, report)
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["error"]["code"], "LIBRARY_NOT_FOUND")
        self.assertEqual(report["stages"]["library_lookup"], "FAIL")


if __name__ == "__main__":
    unittest.main()
