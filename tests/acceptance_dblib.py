# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Real DbLib acceptance against an already running Altium Designer 24.4."""
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "temp" / "dblib_runs"
TEMPLATE = ROOT / "dblib_resources" / "template"
EXIT = {"PASS": 0, "FAIL": 1, "ERROR": 2}


def component(name, page, item_id, fixture, low, high):
    return {
        "name": name,
        "component": {"library": "MY_LIB.DbLib", "page": page, "id": item_id},
        "fixture": fixture,
        "checks": [
            {"analysis": "op", "signal": "v(dut_p)", "measurement": "first", "min": low, "max": high}
        ],
    }


def template_hashes():
    return {
        str(path.relative_to(TEMPLATE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in TEMPLATE.rglob("*") if path.is_file()
    }


def run_case(folder, name, data, timeout=120):
    path = folder / (name + ".json")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(ROOT / "spice_sifter.py"), str(path), "--timeout", "90"],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError:
        report = {"status": "UNPARSED", "stdout": run.stdout, "stderr": run.stderr}
    return report, run.returncode


def check_cleanup(report):
    run_dir = Path(report.get("artifacts", {}).get("run_dir", ""))
    status_path = run_dir / "status.txt"
    if not status_path.is_file():
        return False, "missing status.txt"
    fields = {}
    for line in status_path.read_text(encoding="cp1251", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    ok = (
        fields.get("cleanup_removed") == fields.get("cleanup_expected")
        and fields.get("components_final") == "1"
        and fields.get("stage_schematic_cleanup") == "PASS"
    )
    return ok, fields


def main():
    results = []
    before = template_hashes()
    with tempfile.TemporaryDirectory(prefix="spice_sifter_acceptance_dblib_", dir=ROOT / "tests") as td:
        folder = Path(td)
        cases = [
            ("resistor_r0009", component("dblib_r0009", "Резисторы", "R0009", "resistor_dc", -1.06, -0.94), "PASS", None),
            ("resistor_r0020", component("dblib_r0020", "Резисторы", "R0020", "resistor_dc", -3.18, -2.82), "PASS", None),
            ("diode_forward", component("dblib_vd0001_forward", "Диоды", "VD0001", "diode_forward", 0.9, 1.1), "PASS", None),
            ("diode_reverse", component("dblib_vd0001_reverse", "Диоды", "VD0001", "diode_reverse", 0.9, 1.1), "FAIL", None),
            ("unknown_library", component("neg_library", "Резисторы", "R0009", "resistor_dc", -10, 10) | {"component": {"library": "NO_SUCH_LIBRARY_XYZ.DbLib", "page": "Резисторы", "id": "R0009"}}, "ERROR", "LIBRARY_NOT_FOUND"),
            ("unknown_page", component("neg_page", "НетТакойСтраницы", "R0009", "resistor_dc", -10, 10), "ERROR", "PAGE_NOT_FOUND"),
            ("nonexistent_id", component("neg_id", "Резисторы", "R0001", "resistor_dc", -10, 10), "ERROR", "COMPONENT_ID_NOT_FOUND"),
            ("missing_model", component("neg_model", "Разъёмы", "XP0002", "resistor_dc", -10, 10), "ERROR", "MISSING_SIMULATION_MODEL"),
            ("bad_label", component("neg_label", "Резисторы", "R0009", "resistor_bad_label", -10, 10), "ERROR", "CONNECTIVITY_FAILED"),
        ]
        readme_ids = []
        for name, data, expected, code in cases:
            report, returncode = run_case(folder, name, data)
            ok = report.get("status") == expected and returncode == EXIT[expected]
            if code:
                ok = ok and report.get("error", {}).get("code") == code
            if expected == "PASS":
                ok = ok and all(report.get("stages", {}).get(stage) == "PASS" for stage in report.get("stages", {}))
            elif expected == "FAIL":
                ok = ok and all(
                    value == "PASS"
                    for stage, value in report.get("stages", {}).items()
                    if stage != "validation"
                )
                ok = ok and report.get("stages", {}).get("validation") == "FAIL"
            elif report.get("artifacts", {}).get("run_dir"):
                cleanup_ok, _ = check_cleanup(report)
                ok = ok and cleanup_ok
            if expected in ("PASS", "FAIL"):
                cleanup_ok, fields = check_cleanup(report)
                ok = ok and cleanup_ok
                if expected == "PASS":
                    value = report.get("checks", [{}])[0].get("value")
                    check = data["checks"][0]
                    ok = ok and value is not None and check["min"] <= value <= check["max"]
                if name == "resistor_r0009":
                    readme_ids.append(report.get("run_id"))
            results.append(dict(case=name, passed=ok, report=report, exit=returncode))
            print(name, "OK" if ok else "FAILED", flush=True)

        # Repeatability and immutable template.
        repeat, returncode = run_case(folder, "repeat", component("repeat", "Резисторы", "R0009", "resistor_dc", -1.06, -0.94))
        cleanup_ok, _ = check_cleanup(repeat)
        repeat_ok = (
            repeat.get("status") == "PASS" and returncode == 0 and cleanup_ok
            and repeat.get("run_id") not in readme_ids
        )
        results.append(dict(case="repeat", passed=repeat_ok, report=repeat, exit=returncode))
        print("repeat", "OK" if repeat_ok else "FAILED", flush=True)

        after = template_hashes()
        template_ok = before == after
        results.append(dict(case="template_unchanged", passed=template_ok))
        print("template_unchanged", "OK" if template_ok else "FAILED", flush=True)

        # Artifacts must stay in temp/dblib_runs/<run_id>.
        artifact_ok = True
        for row in results:
            report = row.get("report") or {}
            run_dir = report.get("artifacts", {}).get("run_dir")
            if row["case"] in ("resistor_r0009", "resistor_r0020", "diode_forward", "diode_reverse", "repeat"):
                if not run_dir or not Path(run_dir).is_dir():
                    artifact_ok = False
                elif Path(run_dir).resolve().parent != RUN_ROOT.resolve():
                    artifact_ok = False
        results.append(dict(case="artifacts_in_temp", passed=artifact_ok))
        print("artifacts_in_temp", "OK" if artifact_ok else "FAILED", flush=True)

    (ROOT / "tests/dblib_acceptance_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
