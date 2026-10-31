# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""DbLib component testing: SchDoc fixture -> Altium NSX -> MixedSim -> existing SDF pipeline.

The module keeps the standalone runner untouched. All working files live under
the repository's temp/dblib_runs/<run_id> directory; the prepared project
template in dblib_resources/template is never modified.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parent / "dblib_resources"
TEMPLATE_ROOT = RESOURCE_ROOT / "template"
SCRIPT_TEMPLATE = RESOURCE_ROOT / "dblib_experiment.pas"
PROJECT_ROOT = Path(__file__).resolve().parent
RUN_ROOT = PROJECT_ROOT / "temp" / "dblib_runs"

STAGES = (
    "library_lookup",
    "component_placement",
    "simulation_model",
    "connectivity",
    "netlist_generation",
    "simulation",
    "sdf_parsing",
    "validation",
    "schematic_cleanup",
)

FIXTURES = {
    "resistor_dc": {"current_a": "1"},
    "diode_forward": {"current_a": "-0.01"},
    "diode_reverse": {"current_a": "0.01"},
    # Diagnostic fixture used to exercise the connectivity error path.
    "resistor_bad_label": {"current_a": "1"},
}


class DblibError(Exception):
    def __init__(self, stage, code, message, diagnostics=None):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.message = message
        self.diagnostics = diagnostics or {}


def pas_literal(value):
    """DelphiScript string literal that stays ASCII even for Cyrillic paths."""
    return "'" + "".join(
        ("''" if c == "'" else c) if ord(c) < 128 else "'#" + str(ord(c)) + "'"
        for c in str(value)
    ) + "'"


def pas_codes(value):
    return "".join(f"#{ord(c)}" for c in str(value))


def resolve_library(name):
    """Accept only an absolute path to an existing .DbLib file. No searching."""
    path = Path(str(name).strip())
    if not path.is_absolute() or path.suffix.casefold() != ".dblib":
        raise DblibError(
            "library_lookup",
            "LIBRARY_PATH_INVALID",
            "component.library must be an absolute path to a .DbLib file",
        )
    if not path.is_file():
        raise DblibError(
            "library_lookup",
            "LIBRARY_NOT_FOUND",
            "DbLib file not found: " + str(path),
        )
    return path.resolve()


def library_tables(path):
    """Read table names declared by the DbLib text file without extra packages."""
    try:
        text = Path(path).read_bytes().decode("cp1251", errors="replace")
    except OSError:
        return None
    tables = re.findall(r"(?im)^\s*TableName\s*=\s*(.+?)\s*$", text)
    if not tables:
        return None
    return [table.strip() for table in tables if table.strip()]


def normalize_table(page):
    page = str(page).strip()
    return page if page.endswith("$") else page + "$"


def preflight_component(data):
    component = data.get("component") or {}
    library = resolve_library(component["library"])
    table = normalize_table(component["page"])
    tables = library_tables(library)
    if tables is not None and table.casefold() not in {t.casefold() for t in tables}:
        raise DblibError(
            "library_lookup",
            "PAGE_NOT_FOUND",
            "page/table not found in DbLib: " + str(component["page"]),
            {"library": str(library), "table": table, "available_tables": tables},
        )
    if not TEMPLATE_ROOT.is_dir() or not SCRIPT_TEMPLATE.is_file():
        raise DblibError(
            "harness",
            "RESOURCE_MISSING",
            "DbLib template resources are missing",
            {"template_root": str(TEMPLATE_ROOT), "script": str(SCRIPT_TEMPLATE)},
        )
    return {"library": library, "library_name": library.name, "table": table}


def _rewrite_project(run, base):
    project = run / (base + ".PrjPcb")
    text = project.read_text(encoding="cp1251")
    text = text.replace(
        r"Project Outputs for Test\Test.nsx",
        "Project Outputs for " + base + "\\" + base + ".nsx",
    )
    text = text.replace(r"Simulation\Test.sdf", "Simulation\\" + base + ".sdf")
    text = text.replace("Test.SchDoc", base + ".SchDoc")
    text = text.replace("Test.PrjPcb", base + ".PrjPcb")
    text = text.replace("Test.simcfg", base + ".simcfg")
    document = str(run / (base + ".SchDoc"))
    text = re.sub(
        r"DocumentPath=[^|<\r\n]*" + re.escape(base + ".SchDoc"),
        lambda m: "DocumentPath=" + document,
        text,
    )
    project.write_text(text, encoding="cp1251")

    simcfg = run / "Simulation" / (base + ".simcfg")
    cfg = simcfg.read_text(encoding="cp1251")
    cfg = cfg.replace("Test.SchDoc", base + ".SchDoc")
    cfg = re.sub(
        r"DocumentPath=[^|<\r\n]*" + re.escape(base + ".SchDoc"),
        lambda m: "DocumentPath=" + document,
        cfg,
    )
    simcfg.write_text(cfg, encoding="cp1251")


def _render_script(run, base, library, table, item_id, fixture):
    source = SCRIPT_TEMPLATE.read_text(encoding="ascii")
    values = {
        "__RUN_DIR__": pas_literal(str(run)),
        "__PROJ_PATH__": pas_literal(str(run / (base + ".PrjPcb"))),
        "__DOC_PATH__": pas_literal(str(run / (base + ".SchDoc"))),
        "__NSX_PATH__": pas_literal(str(run / ("Project Outputs for " + base) / (base + ".nsx"))),
        "__SDF_PATH__": pas_literal(str(run / "Simulation" / (base + ".sdf"))),
        "__LIB_PATH__": pas_literal(str(library)),
        "__LIB_NAME__": pas_literal(library.name),
        "__TABLE_NAME__": pas_codes(table),
        "__TARGET_ID__": pas_literal(item_id),
        "__DC_VALUE__": pas_literal(FIXTURES[fixture]["current_a"]),
    }
    for marker, value in values.items():
        token = "'" + marker + "'"
        if token not in source:
            raise DblibError("harness", "TEMPLATE_ERROR", "script marker missing: " + marker)
        source = source.replace(token, value)
    if fixture == "resistor_bad_label":
        old = "AddNetLabel(Doc, Pin1X - 200, Pin1Y, 'DUT_P', Journal);"
        new = "AddNetLabel(Doc, Pin1X - 500, Pin1Y + 100, 'DUT_P', Journal);"
        if old not in source:
            raise DblibError("harness", "TEMPLATE_ERROR", "bad-label injection point not found")
        source = source.replace(old, new)
    if "__" in re.sub(r"'#\d+'", "", source) and re.search(r"__[A-Z_]+__", source):
        raise DblibError("harness", "TEMPLATE_ERROR", "unresolved script marker")
    script = run / "experiment.pas"
    script.write_text(source, encoding="ascii")
    return script


def prepare_run(data, preflight):
    component = data["component"]
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    run = RUN_ROOT / run_id
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(TEMPLATE_ROOT, run)
    except OSError as exc:
        raise DblibError("harness", "RUN_DIR_ERROR", "cannot copy template: " + str(exc)) from exc
    base = "Test_" + run_id
    for old, new in (
        ("Test.PrjPcb", base + ".PrjPcb"),
        ("Test.SchDoc", base + ".SchDoc"),
        (str(Path("Simulation") / "Test.simcfg"), str(Path("Simulation") / (base + ".simcfg"))),
    ):
        source = run / old
        target = run / new
        if not source.is_file():
            raise DblibError("harness", "TEMPLATE_ERROR", "template file missing: " + old)
        source.rename(target)
    _rewrite_project(run, base)
    structure = run / "Test.PrjPcbStructure"
    if structure.is_file():
        structure.write_text(
            structure.read_text(encoding="cp1251").replace("Test.SchDoc", base + ".SchDoc"),
            encoding="cp1251",
        )
    script = _render_script(
        run,
        base,
        preflight["library"],
        preflight["table"],
        str(component["id"]).strip(),
        data["fixture"],
    )
    return run, base, script


def read_status(path):
    try:
        text = Path(path).read_text(encoding="cp1251", errors="replace")
    except FileNotFoundError:
        return {}
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def _launch(exe, script, run, timeout, verbose):
    from spice_sifter import altium_session
    status_path = run / "status.txt"
    command = (
        '"' + str(exe) + '" -RScriptingSystem:RunScriptFile(FileName="' + str(script)
        + '"|ProcName="RunExperiment")'
    )
    (run / "run.log").write_text(command, encoding="utf-8")
    if verbose:
        print("DbLib run directory: " + str(run), file=sys.stderr)
    start = time.time()
    with altium_session() as lock_root:
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if status_path.exists():
                fields = read_status(status_path)
                if fields.get("STATUS") in ("OK", "ERROR"):
                    return fields, start
            time.sleep(0.1)
        (lock_root / "pending-run.txt").write_text(str(run), encoding="utf-8")
        code = "TIMEOUT" if (run / "started.txt").exists() else "SCRIPT_NOT_EXECUTED"
        raise DblibError(
            "altium",
            code,
            "Altium did not write DbLib completion status before timeout",
            {"script": str(script), "run": str(run)},
        )


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _current_file(path, run, start, code, stage):
    if not path or not Path(path).is_file():
        raise DblibError(stage, code, "Expected file is missing: " + str(path))
    if not _inside(path, run):
        raise DblibError(stage, code, "Expected file is outside the current run: " + str(path))
    if Path(path).stat().st_mtime < start - 5:
        raise DblibError(stage, code, "Expected file is older than the current run: " + str(path))
    return Path(path)


def verify_netlist(nsx_path, model_name, expected_nodes=("DUT_P", "0")):
    text = Path(nsx_path).read_text(encoding="utf-8", errors="replace")
    tokens_by_line = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        tokens = re.split(r"\s+", stripped)
        if tokens[0][:1].casefold() == "x" and any(
            t.casefold() == model_name.casefold() for t in tokens
        ):
            tokens_by_line.append(tokens)
    checks = {
        "exists": True,
        "one_dut_instance": len(tokens_by_line) == 1,
        "no_duplicate_dut": len(tokens_by_line) <= 1,
        "model_definition": bool(
            model_name
            and re.search(r"(?im)^\s*\.SUBCKT\s+" + re.escape(model_name) + r"\b", text)
        ),
        "model_name_in_instance": bool(
            model_name
            and tokens_by_line
            and any(t.casefold() == model_name.casefold() for t in tokens_by_line[0])
        ),
        "pin_order_matches": False,
    }
    if tokens_by_line:
        tokens = tokens_by_line[0]
        try:
            model_index = next(
                i for i, t in enumerate(tokens) if t.casefold() == model_name.casefold()
            )
        except StopIteration:
            model_index = -1
        if model_index > 1:
            nodes = tokens[1:model_index]
            normalize = lambda value: "0" if value.casefold() in ("0", "gnd") else value.casefold()
            checks["pin_order_matches"] = [normalize(n) for n in nodes] == [
                normalize(n) for n in expected_nodes
            ]
            checks["nodes"] = nodes
    checks["instance"] = tokens_by_line[0] if tokens_by_line else None
    checks["all"] = all(v for k, v in checks.items() if isinstance(v, bool))
    return checks


def _readback(status):
    parameters = {
        key[len("parameter."):]: value
        for key, value in status.items()
        if key.startswith("parameter.")
    }
    implementations = [
        value for key, value in status.items() if re.fullmatch(r"implementation\.\d+", key)
    ]
    maps = [
        value for key, value in status.items()
        if re.fullmatch(r"implementation\.map\.\d+", key)
    ]
    try:
        pin_count = int(status.get("pin_count", "0"))
    except ValueError:
        pin_count = 0
    return {
        "design_item_id": status.get("design_item_id"),
        "lib_reference": status.get("lib_reference"),
        "database_library_name": status.get("database_library_name"),
        "database_table_name": status.get("database_table_name"),
        "database_keys": status.get("database_keys"),
        "pin_count": pin_count,
        "pins": {key: value for key, value in status.items() if key.startswith("pin.")},
        "sim_model_found": status.get("sim_model_found"),
        "sim_model_name": status.get("sim_model_name"),
        "sim_port_map": status.get("sim_port_map"),
        "parameters": parameters,
        "implementations": implementations,
        "implementation_maps": maps,
    }


def _stages(status):
    return {name: status.get("stage_" + name, "NOT_RUN") for name in STAGES}


def _error(code, message, stage, diagnostics=None):
    payload = {"code": code, "message": message, "stage": stage}
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


def _status_error(status, stages):
    for name in STAGES:
        if name in ("sdf_parsing", "validation"):
            continue
        if stages.get(name) != "PASS":
            code = status.get("error_code") or "DBLIB_" + name.upper() + "_FAILED"
            message = status.get("error_message") or ("stage failed: " + name)
            stage = status.get("error_stage") or name
            diagnostics = {"status": status}
            if name == "schematic_cleanup":
                code = status.get("cleanup_error") or "SCHEMATIC_CLEANUP_FAILED"
                message = status.get("error_message") or "schematic cleanup failed"
                stage = "schematic_cleanup"
            elif status.get("cleanup_error"):
                diagnostics["cleanup_error"] = status["cleanup_error"]
            return _error(code, message, stage, diagnostics)
    if status.get("schematic_saved_after_cleanup") != "1":
        return _error(
            "SCHEMATIC_CLEANUP_FAILED",
            "cleaned schematic was not saved",
            "schematic_cleanup",
            {"status": status},
        )
    if status.get("script_failed") == "1":
        return _error(
            status.get("error_code") or "ALTIUM_SCRIPT_FAILED",
            status.get("error_message") or "Altium script reported a failure",
            status.get("error_stage") or "altium",
            {"status": status},
        )
    if status.get("STATUS") == "ERROR":
        return _error(
            status.get("error_code") or "ALTIUM_SCRIPT_ERROR",
            status.get("error_message") or "Altium script reported an error",
            status.get("error_stage") or "altium",
            {"status": status},
        )
    if status.get("STATUS") not in ("OK", None):
        return _error("ALTIUM_SCRIPT_ERROR", "Altium script did not complete", "altium")
    return None


def _structural_failure(report, stages, error):
    report["stages"] = stages
    report["status"] = "ERROR"
    report["error"] = error
    return report


def _chart_key(chart):
    from spice_sifter import chart_analysis
    analysis = chart_analysis(chart)
    return (
        analysis if analysis is not None else chart.name.casefold(),
        tuple(sorted(w.name.casefold() for w in chart.waves)),
    )


def _values_differ(left, right):
    if len(left) != len(right):
        return True
    for a, b in zip(left, right):
        if isinstance(a, complex) or isinstance(b, complex):
            if not isinstance(a, complex) or not isinstance(b, complex):
                return True
            if a.real != b.real or a.imag != b.imag:
                return True
        elif a != b:
            return True
    return False


def _chart_difference(left, right):
    from spice_sifter import chart_analysis
    if chart_analysis(left) != chart_analysis(right):
        return "analysis differs"
    lw = {w.name.casefold(): w for w in left.waves}
    rw = {w.name.casefold(): w for w in right.waves}
    if set(lw) != set(rw):
        return "signal set differs"
    for name in sorted(lw):
        a, b = lw[name], rw[name]
        if a.unit != b.unit:
            return "signal %s: unit differs (%r != %r)" % (a.name, a.unit, b.unit)
        if a.is_complex != b.is_complex:
            return "signal %s: data type differs" % a.name
        if a.signal_type != b.signal_type:
            return "signal %s: signal type differs (%r != %r)" % (
                a.name, a.signal_type, b.signal_type,
            )
        if a.point_count != b.point_count:
            return "signal %s: point count differs (%d != %d)" % (
                a.name, a.point_count, b.point_count,
            )
        if len(a.x) != len(b.x) or len(a.y) != len(b.y):
            return "signal %s: array length differs" % a.name
        if _values_differ(a.x, b.x):
            return "signal %s: X differs" % a.name
        if _values_differ(a.y, b.y):
            return "signal %s: Y differs" % a.name
    return None


def _normalized_document(doc):
    """Collapse only truly identical duplicate chart setups.

    MixedSim can emit several identical charts for one analysis/signal set.
    The standalone validator rejects such ambiguity by design. For the DbLib
    pipeline we compare complete X/Y arrays, units, data types and point counts.
    Identical charts collapse to the first one; any real difference is an error.
    """
    from spice_sifter import SDFDocument
    groups = {}
    charts = []
    for chart in doc.charts:
        key = _chart_key(chart)
        if key in groups:
            first = groups[key]
            reason = _chart_difference(first, chart)
            if reason:
                raise DblibError(
                    "sdf_parsing",
                    "SDF_DUPLICATE_CONFLICT",
                    "Duplicate SDF charts contain different data",
                    {
                        "analysis": key[0],
                        "signals": list(key[1]),
                        "charts": [first.name, chart.name],
                        "reason": reason,
                    },
                )
        else:
            groups[key] = chart
            charts.append(chart)
    return SDFDocument(doc.version, charts, doc.metadata)


def build_report(report, data, run, status, start):
    from spice_sifter import (
        TestError, export_graphs, finish_measurements, parse_sdf,
        process_measurements, validate_document,
    )

    component = data["component"]
    stages = _stages(status)
    readback = _readback(status)
    report.update(
        component=component,
        fixture=data["fixture"],
        run_id=run.name,
        stages=stages,
        component_readback=readback,
        artifacts={
            "run_dir": str(run),
            "nsx": status.get("nsx_path"),
            "sdf": status.get("sdf_path"),
        },
    )

    error = _status_error(status, stages)
    if error:
        stages["sdf_parsing"] = "NOT_RUN"
        stages["validation"] = "NOT_RUN"
        return _structural_failure(report, stages, error)

    keys = readback.get("database_keys") or ""
    if keys.strip() != str(component["id"]).strip():
        stages["library_lookup"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("COMPONENT_ID_MISMATCH", "loaded component ID differs from requested ID", "library_lookup"),
        )
    if readback.get("database_library_name") and (
        Path(readback["database_library_name"]).name.casefold()
        != Path(component["library"]).name.casefold()
    ):
        stages["library_lookup"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("LIBRARY_SOURCE_MISMATCH", "loaded component comes from another DbLib", "library_lookup"),
        )
    expected_table = normalize_table(component["page"])
    if readback.get("database_table_name") and (
        readback["database_table_name"].casefold() != expected_table.casefold()
    ):
        stages["library_lookup"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("TABLE_NAME_MISMATCH", "loaded component table differs from requested page", "library_lookup"),
        )
    if readback.get("pin_count") != 2:
        stages["component_placement"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("PIN_COUNT_UNSUPPORTED", "this fixture supports exactly two pins", "component_placement"),
        )
    if not readback.get("sim_model_name"):
        stages["simulation_model"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("MISSING_SIMULATION_MODEL", "component has no SIM implementation", "simulation_model"),
        )
    if not readback.get("sim_port_map"):
        stages["simulation_model"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("SIM_PORT_MAP_MISSING", "SIM implementation has no port map", "simulation_model"),
        )

    try:
        nsx = _current_file(
            status.get("nsx_path"), run, start, "NSX_NOT_GENERATED", "netlist_generation"
        )
        netlist = verify_netlist(nsx, readback["sim_model_name"])
        report["netlist"] = netlist
        if not netlist["all"]:
            stages["netlist_generation"] = "FAIL"
            return _structural_failure(
                report, stages,
                _error("NSX_INVALID", "generated NSX failed DUT/model/pin checks", "netlist_generation", netlist),
            )
    except DblibError as exc:
        stages["netlist_generation"] = "FAIL"
        return _structural_failure(
            report, stages, _error(exc.code, exc.message, exc.stage, exc.diagnostics)
        )

    try:
        sdf = _current_file(
            status.get("sdf_path"), run, start, "SDF_NOT_SAVED", "simulation"
        )
        doc = parse_sdf(sdf)
    except DblibError as exc:
        stages["simulation"] = "FAIL"
        return _structural_failure(
            report, stages, _error(exc.code, exc.message, exc.stage, exc.diagnostics)
        )
    except TestError as exc:
        stages["sdf_parsing"] = "FAIL"
        return _structural_failure(
            report, stages, _error(exc.code, exc.message, "sdf_parsing")
        )
    try:
        doc = _normalized_document(doc)
    except DblibError as exc:
        stages["sdf_parsing"] = "FAIL"
        return _structural_failure(
            report, stages, _error(exc.code, exc.message, exc.stage, exc.diagnostics)
        )
    stages["sdf_parsing"] = "PASS"

    outcome, rows = validate_document(doc, data.get("checks", []))
    stages["validation"] = outcome
    report["checks"] = rows

    if data.get("measurements"):
        report["_measurement_requests"] = data["measurements"]
        process_measurements(doc, data["measurements"], report)
        try:
            finish_measurements(data, report)
        except TestError as exc:
            stages["validation"] = "ERROR"
            return _structural_failure(
                report, stages, _error(exc.code, exc.message, "measurements")
            )

    if data.get("output", {}).get("graphs"):
        try:
            report["graphs"] = export_graphs(doc, data["output"]["graphs"])
        except TestError as exc:
            stages["validation"] = "ERROR"
            return _structural_failure(
                report, stages, _error(exc.code, exc.message, "graph_export")
            )

    if stages.get("schematic_cleanup") != "PASS":
        return _structural_failure(
            report, stages,
            _error("SCHEMATIC_CLEANUP_FAILED", "test objects were not fully removed", "schematic_cleanup"),
        )

    measurements = report.get("measurements", [])
    measurement_error = next(
        (row["error"] for row in measurements if row.get("status") == "ERROR"), None
    )
    check_error = next((row["error"] for row in rows if "error" in row), None)
    if check_error:
        report["error"] = dict(check_error, stage="validation")
        report["status"] = "ERROR"
    elif measurement_error:
        report["error"] = dict(measurement_error, stage="measurements")
        report["status"] = "ERROR"
    elif outcome == "ERROR":
        report["error"] = _error("MEASUREMENT_INVALID", "validation failed", "validation")
        report["status"] = "ERROR"
    elif outcome == "FAIL" or any(row.get("status") == "FAIL" for row in measurements):
        report["status"] = "FAIL"
        report["error"] = None
    else:
        report["status"] = "PASS"
        report["error"] = None
    return report


def execute_component(exe, data, base, timeout, verbose, report):
    report.update(
        test=data.get("name"),
        status="ERROR",
        simulator="Altium MixedSim",
        checks=[],
        component=data.get("component"),
        fixture=data.get("fixture"),
        error=None,
    )
    run = None
    try:
        preflight = preflight_component(data)
        from spice_sifter import TestError, require_running_altium
        require_running_altium(exe)
        run, run_base, script = prepare_run(data, preflight)
        report["run_id"] = run.name
        report["artifacts"] = {"run_dir": str(run)}
        status, start = _launch(exe, script, run, timeout, verbose)
        build_report(report, data, run, status, start)
    except DblibError as exc:
        stages = {name: "NOT_RUN" for name in STAGES}
        if exc.stage in stages:
            stages[exc.stage] = "FAIL"
        report.update(
            status="ERROR",
            stages=stages,
            error=_error(exc.code, exc.message, exc.stage, exc.diagnostics),
        )
        if run is not None:
            report["artifacts"] = {"run_dir": str(run)}
    except TestError as exc:
        stages = {name: "NOT_RUN" for name in STAGES}
        report.update(
            status="ERROR",
            stages=stages,
            error=_error(exc.code, exc.message, "altium"),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        stages = {name: "NOT_RUN" for name in STAGES}
        stages["library_lookup"] = "FAIL"
        report.update(
            status="ERROR",
            stages=stages,
            error=_error("DBLIB_RUN_FAILED", str(exc), "harness"),
        )

    if run is not None:
        try:
            (run / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    return report
