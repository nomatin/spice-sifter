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
TEMPLATE_EMPTY = RESOURCE_ROOT / "template_empty"
SCRIPT_TEMPLATE = RESOURCE_ROOT / "dblib_experiment.pas"
SCRIPT_FIXTURE = RESOURCE_ROOT / "dblib_fixture.pas"
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
    page = component.get("page") if "page" in component else component.get("table")
    library = resolve_library(component["library"])
    table = normalize_table(page)
    tables = library_tables(library)
    if tables is not None and table.casefold() not in {t.casefold() for t in tables}:
        raise DblibError(
            "library_lookup",
            "PAGE_NOT_FOUND",
            "page/table not found in DbLib: " + str(page),
            {"library": str(library), "table": table, "available_tables": tables},
        )
    mode = "spice" if data.get("spice") is not None else "legacy"
    if mode == "spice":
        from dblib_fixture import FixtureError, parse_spice_fixture, validate_pin_map
        pins = component.get("pins")
        if not isinstance(pins, dict) or not pins:
            raise DblibError(
                "library_lookup",
                "COMPONENT_PIN_MAP_INVALID",
                "component.pins must be a nonempty object in spice fixture mode",
            )
        try:
            plan = parse_spice_fixture(data["spice"])
            pins = validate_pin_map(pins, plan)
        except FixtureError as exc:
            raise DblibError(
                "component_placement", exc.code, exc.message,
                {"line": exc.line, **(exc.diagnostics or {})},
            ) from exc
        designator = str(component.get("designator") or "").strip()
        if designator and any(
            element.name.casefold() == designator.casefold()
            for element in plan.elements
        ):
            raise DblibError(
                "component_placement",
                "COMPONENT_DESIGNATOR_CONFLICT",
                "component.designator collides with a fixture element: " + designator,
                {"designator": designator},
            )
        if "dc" in plan.analyses:
            raise DblibError(
                "component_placement",
                "FIXTURE_ANALYSIS_UNSUPPORTED",
                "DC sweep is not executed by the current project-template "
                "MixedSim flow; use .OP/.AC/.TRAN or standalone mode",
                {"analysis": "dc"},
            )
        template = TEMPLATE_EMPTY
        script = SCRIPT_FIXTURE
    else:
        plan = None
        pins = None
        template = TEMPLATE_ROOT
        script = SCRIPT_TEMPLATE
    if not template.is_dir() or not script.is_file():
        raise DblibError(
            "harness",
            "RESOURCE_MISSING",
            "DbLib template resources are missing",
            {"template_root": str(template), "script": str(script)},
        )
    return {
        "library": library, "library_name": library.name, "table": table,
        "mode": mode, "plan": plan, "pins": pins,
        "template": template, "script": script,
    }


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
    if "[Document2]" not in text:
        doc2 = (
            "\n[Document2]\n"
            "DocumentPath=Simulation\\" + base + ".simcfg\n"
            "AnnotationEnabled=1\nAnnotateStartValue=1\n"
            "AnnotationIndexControlEnabled=0\nAnnotateSuffix=\n"
            "AnnotateScope=All\nAnnotateOrder=-1\nDoLibraryUpdate=1\n"
            "DoDatabaseUpdate=1\nClassGenCCAutoEnabled=0\n"
            "ClassGenCCAutoRoomEnabled=0\nClassGenNCAutoScope=None\n"
            "DItemRevisionGUID=\nGenerateClassCluster=0\nDocumentUniqueId=\n"
        )
        marker = "\n[Configuration1]"
        if marker in text:
            text = text.replace(marker, doc2 + marker, 1)
        else:
            text += doc2
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


def _replace_config_keys(text, updates):
    for key, value in updates.items():
        text = re.sub(
            r"(?<![A-Za-z0-9_])" + re.escape(key) + r"=[^|<\r\n]*",
            key + "=" + str(value),
            text,
        )
    return text


def _apply_sim_config(run, base, updates):
    if not updates:
        return
    project = run / (base + ".PrjPcb")
    project.write_text(
        _replace_config_keys(project.read_text(encoding="cp1251"), updates),
        encoding="cp1251",
    )
    simcfg = run / "Simulation" / (base + ".simcfg")
    simcfg.write_text(
        _replace_config_keys(simcfg.read_text(encoding="cp1251"), updates),
        encoding="cp1251",
    )


def _apply_analysis_name(run, base, analysis):
    names = {
        "op": ("Operating Point", "Operating Point"),
        "ac": ("AC Analysis", "AC Analysis"),
        "tran": ("Transient Analysis", "Transient Analysis"),
        "dc": ("DC Sweep", "DC Sweep"),
    }
    name, kind = names[analysis]
    simcfg = run / "Simulation" / (base + ".simcfg")
    text = simcfg.read_text(encoding="cp1251")
    text = re.sub(
        r"(<ResultConfig>.*?<Name>)[^<]*(</Name>.*?<Type>)[^<]*(</Type>)",
        lambda m: m.group(1) + name + m.group(2) + kind + m.group(3),
        text, count=1, flags=re.S,
    )
    simcfg.write_text(text, encoding="cp1251")


def _render_fixture_script(run, base, library, table, item_id, designator,
                           pins, plan):
    from dblib_fixture import (analysis_name, element_plan_lines, pin_map_lines,
                               sim_config_updates)
    pin_map = run / "pin_map.txt"
    elements = run / "elements.txt"
    pin_map.write_text("\n".join(pin_map_lines(pins)) + "\n", encoding="cp1251")
    elements.write_text("\n".join(element_plan_lines(plan)) + "\n", encoding="cp1251")
    _apply_sim_config(run, base, sim_config_updates(plan))
    _apply_analysis_name(run, base, analysis_name(plan))

    source = SCRIPT_FIXTURE.read_text(encoding="ascii")
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
        "__DESIGNATOR__": pas_literal(designator or ""),
        "__PIN_MAP_PATH__": pas_literal(str(pin_map)),
        "__ELEMENT_PLAN_PATH__": pas_literal(str(elements)),
    }
    for marker, value in values.items():
        token = "'" + marker + "'"
        if token not in source:
            raise DblibError("harness", "TEMPLATE_ERROR", "script marker missing: " + marker)
        source = source.replace(token, value)
    if re.search(r"__[A-Z_]+__", source):
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
        shutil.copytree(preflight["template"], run)
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
    if preflight["mode"] == "spice":
        script = _render_fixture_script(
            run, base, preflight["library"], preflight["table"],
            str(component["id"]).strip(), component.get("designator", ""),
            preflight["pins"], preflight["plan"],
        )
    else:
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


def verify_netlist(nsx_path, model_name, expected_nodes=("DUT_P", "0"),
                   assigned_nets=None):
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
        "port_count_matches": False,
        "unassigned_ports_not_connected": True,
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
            checks["port_count_matches"] = len(nodes) == len(expected_nodes)
            assigned = {
                normalize(n) for n in (assigned_nets or expected_nodes)
                if n is not None
            }
            matches = []
            for actual, expected in zip(nodes, expected_nodes):
                if expected is None:
                    matches.append(normalize(actual) not in assigned)
                    if normalize(actual) in assigned:
                        checks["unassigned_ports_not_connected"] = False
                else:
                    matches.append(normalize(actual) == normalize(expected))
            checks["pin_order_matches"] = bool(matches) and all(matches)
            checks["nodes"] = nodes
    checks["instance"] = tokens_by_line[0] if tokens_by_line else None
    checks["all"] = all(v for k, v in checks.items() if isinstance(v, bool))
    return checks


def parse_port_map(text):
    """Parse (symbol_pin:model_port) pairs without guessing their meaning."""
    pairs = re.findall(r"\(([^:()]+):([^()]+)\)", str(text or ""))
    result = []
    seen_ports = set()
    seen_pins = set()
    for symbol_pin, port_text in pairs:
        symbol_pin = symbol_pin.strip()
        try:
            port = int(port_text.strip())
        except ValueError as exc:
            raise DblibError(
                "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
                "SIM port map contains a non-numeric model port: " + str(text),
            ) from exc
        if port <= 0 or port in seen_ports or symbol_pin.casefold() in seen_pins:
            raise DblibError(
                "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
                "SIM port map contains a duplicate or invalid port: " + str(text),
            )
        seen_ports.add(port)
        seen_pins.add(symbol_pin.casefold())
        result.append((symbol_pin, port))
    if not result:
        raise DblibError(
            "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
            "SIM port map is empty or malformed: " + str(text),
        )
    ports = sorted(port for _, port in result)
    if ports != list(range(1, len(ports) + 1)):
        raise DblibError(
            "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
            "SIM model ports must be exactly 1..N: " + str(text),
        )
    return result


def expected_nodes_from_port_map(readback, pins):
    """Build expected NSX node order from the real Sim Port Map and pin map."""
    port_map = parse_port_map(readback.get("sim_port_map"))
    expected = [None] * (max(port for _, port in port_map) + 1)
    used_symbols = set()
    for symbol_pin, port in port_map:
        key = symbol_pin.casefold()
        if key not in {str(k).casefold() for k in pins}:
            raise DblibError(
                "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
                "SIM port map references a pin absent from component.pins: "
                + symbol_pin,
                {"pin": symbol_pin, "pin_map": sorted(map(str, pins))},
            )
        actual_key = next(k for k in pins if str(k).casefold() == key)
        expected[port] = pins[actual_key]
        used_symbols.add(actual_key)
    for designator, net in pins.items():
        if net is not None and designator not in used_symbols:
            raise DblibError(
                "simulation_model", "COMPONENT_SIM_PORT_MAP_INVALID",
                "assigned pin is absent from the SIM port map: " + str(designator),
                {"pin": str(designator), "net": net,
                 "sim_port_map": readback.get("sim_port_map")},
            )
    return tuple(expected[1:]), {
        net for net in pins.values() if net is not None
    }


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
    pin_map = {}
    try:
        map_count = int(status.get("pin_map_count", "0"))
    except ValueError:
        map_count = 0
    for index in range(map_count):
        designator = status.get(f"pin_map.{index}.designator", "")
        net = status.get(f"pin_map.{index}.net", "")
        pin_map[designator] = None if net == "<NULL>" else net
    fixtures = []
    try:
        fixture_count = int(status.get("fixture_count", "0"))
    except ValueError:
        fixture_count = 0
    for index in range(fixture_count):
        fixtures.append({
            "type": status.get(f"fixture.{index}.type"),
            "name": status.get(f"fixture.{index}.name"),
            "designator": status.get(f"fixture.{index}.designator"),
            "x": status.get(f"fixture.{index}.x"),
            "y": status.get(f"fixture.{index}.y"),
            "value": status.get(f"fixture.{index}.value"),
            "ac_magnitude": status.get(f"fixture.{index}.ac_magnitude"),
            "ac_phase": status.get(f"fixture.{index}.ac_phase"),
        })
    return {
        "design_item_id": status.get("design_item_id"),
        "lib_reference": status.get("lib_reference"),
        "database_library_name": status.get("database_library_name"),
        "database_table_name": status.get("database_table_name"),
        "database_keys": status.get("database_keys"),
        "pin_count": pin_count,
        "pins": {key: value for key, value in status.items() if key.startswith("pin.")},
        "pin_map": pin_map,
        "fixtures": fixtures,
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
        fixture=data.get("fixture"),
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
    mode = "spice" if data.get("spice") is not None else "legacy"
    if mode == "spice":
        if readback.get("pin_count", 0) <= 0:
            stages["component_placement"] = "FAIL"
            return _structural_failure(
                report, stages,
                _error("COMPONENT_NO_PINS", "component has no pins", "component_placement"),
            )
        actual_pins = set()
        pin_count = readback.get("pin_count", 0)
        for index in range(pin_count):
            designator = readback["pins"].get(f"pin.{index + 1}.designator")
            if designator is None:
                break
            actual_pins.add(str(designator).casefold())
        map_pins = set(str(key).casefold() for key in readback.get("pin_map", {}))
        if actual_pins != map_pins:
            missing = sorted(actual_pins - map_pins)
            extra = sorted(map_pins - actual_pins)
            stages["component_placement"] = "FAIL"
            return _structural_failure(
                report, stages,
                _error(
                    "COMPONENT_PIN_NOT_FOUND" if extra else "COMPONENT_PIN_UNASSIGNED",
                    "pin map does not match the loaded component",
                    "component_placement",
                    {"missing_from_map": missing, "not_on_component": extra},
                ),
            )
    elif readback.get("pin_count") != 2:
        stages["component_placement"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error("PIN_COUNT_UNSUPPORTED", "this fixture supports exactly two pins", "component_placement"),
        )
    if not readback.get("sim_model_name"):
        stages["simulation_model"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error(
                "COMPONENT_SIM_MODEL_MISSING" if mode == "spice" else "MISSING_SIMULATION_MODEL",
                "component has no SIM implementation", "simulation_model",
            ),
        )
    if not readback.get("sim_port_map"):
        stages["simulation_model"] = "FAIL"
        return _structural_failure(
            report, stages,
            _error(
                "COMPONENT_SIM_PORT_MAP_INVALID" if mode == "spice" else "SIM_PORT_MAP_MISSING",
                "SIM implementation has no port map", "simulation_model",
            ),
        )

    try:
        if mode == "spice":
            expected_nodes, assigned_nets = expected_nodes_from_port_map(
                readback, readback.get("pin_map", {})
            )
        else:
            expected_nodes, assigned_nets = ("DUT_P", "0"), None
        nsx = _current_file(
            status.get("nsx_path"), run, start, "NSX_NOT_GENERATED", "netlist_generation"
        )
        netlist = verify_netlist(
            nsx, readback["sim_model_name"], expected_nodes, assigned_nets
        )
        report["netlist"] = netlist
        if not netlist["all"]:
            stages["netlist_generation"] = "FAIL"
            return _structural_failure(
                report, stages,
                _error("COMPONENT_NETLIST_MISMATCH" if mode == "spice" else "NSX_INVALID",
                       "generated NSX failed DUT/model/pin-order checks",
                       "netlist_generation", netlist),
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

    if mode == "spice":
        leftovers = {
            "components_final": status.get("components_final"),
            "wires_final": status.get("wires_final"),
            "netlabels_final": status.get("netlabels_final"),
        }
        if any(value not in ("0", None) for value in leftovers.values()):
            stages["schematic_cleanup"] = "FAIL"
            return _structural_failure(
                report, stages,
                _error("SCHEMATIC_CLEANUP_FAILED",
                       "generated schematic objects were not fully removed",
                       "schematic_cleanup", leftovers),
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
