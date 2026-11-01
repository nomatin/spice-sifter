# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Real multi-pin DbLib acceptance for the spice-fixture generator.

Requires an already running Altium Designer 24.4 and SPICE_SIFTER_DBLIB_PATH.
BJT is intentionally BLOCKED: MY_LIB has no BJT component.
"""
import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_ROOT = ROOT / "temp" / "dblib_acceptance"
EXIT = {"PASS": 0, "FAIL": 1, "ERROR": 2}


def component(library, table, item_id, pins, spice, checks,
              designator=None):
    data = {
        "name": "multipin_" + item_id,
        "component": {
            "library": library, "table": table, "id": item_id,
            "pins": pins,
        },
        "spice": spice,
        "checks": checks,
    }
    if designator:
        data["component"]["designator"] = designator
    return data


MOSFET_SPICE = (
    "* 2N7002 DC test\nVDD VDD 0 DC 5\nVG GATE 0 DC 3\n"
    "R1 VDD DRAIN 1k\n{{DUT}}\n.OP\n.END\n"
)
MOSFET_CHECK = [{"analysis": "op", "signal": "v(drain)", "measurement": "first",
                 "min": 0.0, "max": 1.0}]
IC6_SPICE = (
    "* USBLC6 6-pin DC bias\nVCC VCC 0 DC 5\nVIN1 IN1 0 DC 1\n"
    "VIN2 IN2 0 DC 2\nR1 OUT1 0 10k\nR2 OUT2 0 10k\n{{DUT}}\n.OP\n.END\n"
)
IC6_CHECK = [{"analysis": "op", "signal": "v(out1)", "measurement": "first",
              "min": -1.0, "max": 6.0}]
TVS_SPICE = (
    "* 3-pin TVS DC bias\nV1 IN 0 DC 1\nR1 OUT 0 1k\n{{DUT}}\n.OP\n.END\n"
)
TVS_CHECK = [{"analysis": "op", "signal": "v(out)", "measurement": "first",
              "min": -1.0, "max": 2.0}]


def template_hashes():
    result = {}
    for template in ("template", "template_empty"):
        root = ROOT / "dblib_resources" / template
        for path in root.rglob("*"):
            if path.is_file():
                key = template + "/" + str(path.relative_to(root))
                result[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def run_case(inputs, name, data, timeout=140):
    path = inputs / (name + ".json")
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


def cleanup_ok(report):
    run_dir = Path(report.get("artifacts", {}).get("run_dir", ""))
    status = run_dir / "status.txt"
    if not status.is_file():
        return False
    fields = {}
    for line in status.read_text(encoding="cp1251", errors="replace").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return (
        fields.get("components_final") == "0"
        and fields.get("wires_final") == "0"
        and fields.get("netlabels_final") == "0"
        and fields.get("stage_schematic_cleanup") == "PASS"
        and fields.get("schematic_saved_after_cleanup") == "1"
    )


def main():
    library = os.environ.get("SPICE_SIFTER_DBLIB_PATH", "").strip()
    if not library:
        print("SPICE_SIFTER_DBLIB_PATH is required", file=sys.stderr)
        return 2
    lib = str(Path(library).resolve())
    acceptance_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    acceptance_dir = ACCEPTANCE_ROOT / acceptance_id
    inputs = acceptance_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    report_path = acceptance_dir / "acceptance_report.json"
    results = []

    before = template_hashes()
    mosfet_pins = {"1": "GATE", "2": "GND", "3": "DRAIN"}
    ic_pins = {"1": "VCC", "2": "GND", "3": "IN1", "4": "IN2",
               "5": "OUT1", "6": "OUT2"}
    cases = [
        ("mosfet_ok", component(lib, "Транзисторы", "VT0001", mosfet_pins,
                               MOSFET_SPICE, MOSFET_CHECK, "Q1"),
         "PASS", None),
        ("mosfet_wrong", component(
            lib, "Транзисторы", "VT0001",
            {"1": "DRAIN", "2": "GND", "3": "GATE"}, MOSFET_SPICE,
            MOSFET_CHECK, "Q1"), "FAIL", None),
        ("ic6_ok", component(lib, "Микросхемы", "U0003", ic_pins,
                            IC6_SPICE, IC6_CHECK, "U1"), "PASS", None),
        ("ic3_tvs_ok", component(
            lib, "Микросхемы", "U0005",
            {"1": "IN", "2": "GND", "3": "OUT"}, TVS_SPICE, TVS_CHECK, "D1"),
         "PASS", None),
        ("ic6_null", component(
            lib, "Микросхемы", "U0003", {**ic_pins, "6": None},
            IC6_SPICE, IC6_CHECK, "U1"), "PASS", None),
        ("ic6_shared", component(
            lib, "Микросхемы", "U0003", {**ic_pins, "5": "OUT1", "6": "OUT1"},
            IC6_SPICE, IC6_CHECK, "U1"), "PASS", None),
        ("ic6_bad_pin", component(
            lib, "Микросхемы", "U0003", {**ic_pins, "99": "VCC"},
            IC6_SPICE, IC6_CHECK, "U1"), "ERROR", "COMPONENT_PIN_NOT_FOUND"),
        ("ic6_missing_pin", component(
            lib, "Микросхемы", "U0003",
            {k: v for k, v in ic_pins.items() if k != "6"},
            IC6_SPICE, IC6_CHECK, "U1"), "ERROR", "COMPONENT_PIN_UNASSIGNED"),
    ]
    for name, data, expected, code in cases:
        report, returncode = run_case(inputs, name, data)
        ok = report.get("status") == expected and returncode == EXIT[expected]
        if code:
            ok = ok and report.get("error", {}).get("code") == code
        if expected in ("PASS", "FAIL"):
            ok = ok and cleanup_ok(report)
            if expected == "PASS":
                value = report.get("checks", [{}])[0].get("value")
                check = data["checks"][0]
                ok = ok and value is not None and check["min"] <= value <= check["max"]
        results.append(dict(case=name, passed=ok, report=report, exit=returncode))
        print(name, "OK" if ok else "FAILED", flush=True)

    # Unsupported fixture syntax must fail before launching a simulation.
    unsupported = component(
        lib, "Транзисторы", "VT0001", mosfet_pins,
        "Q1 c b e MODEL\n{{DUT}}\n.OP\n.END\n", MOSFET_CHECK, "Q1",
    )
    report, returncode = run_case(inputs, "unsupported_element", unsupported)
    ok = (report.get("status") == "ERROR" and returncode == 2
          and report.get("error", {}).get("code") == "FIXTURE_ELEMENT_UNSUPPORTED")
    results.append(dict(case="unsupported_element", passed=ok, report=report,
                        exit=returncode))
    print("unsupported_element", "OK" if ok else "FAILED", flush=True)

    # No BJT exists in MY_LIB. Do not substitute a MOSFET result.
    results.append(dict(case="bjt", passed=True, status="BLOCKED",
                        reason="no BJT component in MY_LIB"))
    print("bjt BLOCKED (no BJT in MY_LIB)", flush=True)

    after = template_hashes()
    template_ok = before == after
    results.append(dict(case="template_unchanged", passed=template_ok))
    print("template_unchanged", "OK" if template_ok else "FAILED", flush=True)

    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("acceptance report: " + str(report_path), flush=True)
    return 0 if all(row["passed"] for row in results) else 1


if __name__ == "__main__":
    sys.exit(main())
