# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Reusable DbLib -> NSX -> MixedSim -> SDF flow executed in a chosen instance.

Production modules do all preparation and validation; this module only decides
*where* the ``-R`` process is created and collects independent evidence.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from hidden_session import terminate_owned
from instances import HiddenInstance, Reference, send_script, titles
from support import Evidence


def wait_for_run_status(run: Path, timeout: float, evidence: Evidence,
                        hidden: HiddenInstance, reference: Reference,
                        base: str, label: str = "") -> tuple[dict, float]:
    from dblib_runner import read_status

    status_path = run / "status.txt"
    start = time.monotonic()
    last_hidden: list[str] = []
    while time.monotonic() - start < timeout:
        fields = read_status(status_path)
        if fields.get("STATUS") in ("OK", "ERROR"):
            return fields, time.monotonic() - start
        try:
            hidden_titles = titles(hidden.windows())
            working_titles = titles(reference.windows())
        except Exception:
            hidden_titles, working_titles = [], []
        if hidden_titles != last_hidden:
            evidence.log(
                "during_run", label=label,
                elapsed=round(time.monotonic() - start, 1),
                hidden_has_project=any(base.casefold() in t.casefold()
                                       for t in hidden_titles),
                working_has_project=any(base.casefold() in t.casefold()
                                        for t in working_titles),
                hidden_titles=hidden_titles,
            )
            last_hidden = hidden_titles
        time.sleep(1.0)
    return read_status(status_path), time.monotonic() - start


def run_dblib_test(exe: Path, hidden: HiddenInstance, reference: Reference,
                   evidence: Evidence, data: dict, timeout: float,
                   label: str = "spice",
                   artifact_subdir: str = "altium_run") -> dict:
    from dblib_runner import (
        STAGES, build_report, preflight_component, prepare_run, verify_netlist,
    )
    from spice_sifter import parse_sdf

    checks: dict[str, bool] = {}
    preflight = preflight_component(data)
    evidence.log("preflight", label=label, template=str(preflight["template"]),
                 library=str(preflight["library"]), table=preflight["table"])

    working_before = sorted(titles(reference.windows()))
    hidden_before = sorted(titles(hidden.windows()))
    run, base, script = prepare_run(data, preflight)
    evidence.log("run_prepared", label=label, run_dir=str(run), base=base,
                 script=str(script))

    start = time.time()
    launcher = send_script(exe, script, hidden.desktop, evidence=evidence,
                           label=label)
    try:
        status, elapsed = wait_for_run_status(
            run, timeout, evidence, hidden, reference, base, label=label
        )
    finally:
        if launcher.process.is_alive():
            stop = terminate_owned(launcher, timeout=15)
            evidence.log("launcher_terminated", label=label, **stop)
        launcher.close()

    evidence.log("run_status", label=label, elapsed=round(elapsed, 1),
                 fields=status)

    working_after = sorted(titles(reference.windows()))
    hidden_after = sorted(titles(hidden.windows()))
    hidden_has_project = any(base.casefold() in t.casefold() for t in hidden_after)
    working_has_project = any(base.casefold() in t.casefold() for t in working_after)
    evidence.log("after_run", label=label, working_titles=working_after,
                 hidden_titles=hidden_after,
                 hidden_has_project=hidden_has_project,
                 working_has_project=working_has_project)

    report: dict = {"test": data.get("name"), "simulator": "Altium MixedSim",
                    "checks": []}
    build_report(report, data, run, status, start)
    try:
        from report_model import finalize_report
        finalize_report(report, data)
    except Exception as exc:  # pragma: no cover - report shaping only
        evidence.log("finalize_report_failed", label=label, error=repr(exc))
    evidence.log("report", label=label, report=report)

    nsx = Path(status.get("nsx_path") or
               run / ("Project Outputs for " + base) / (base + ".nsx"))
    sdf = Path(status.get("sdf_path") or run / "Simulation" / (base + ".sdf"))
    nsx_exists = nsx.is_file() and nsx.stat().st_size > 0
    sdf_exists = sdf.is_file() and sdf.stat().st_size > 0
    model_name = status.get("sim_model_name") or ""
    netlist = verify_netlist(nsx, model_name) if nsx_exists and model_name else {}
    sdf_doc = None
    if sdf_exists:
        try:
            sdf_doc = parse_sdf(sdf)
        except Exception as exc:
            evidence.log("sdf_parse_failed", label=label, error=repr(exc))

    evidence.log(
        "artifacts", label=label,
        nsx=str(nsx), nsx_exists=nsx_exists,
        nsx_size=nsx.stat().st_size if nsx.exists() else None,
        netlist=netlist,
        sdf=str(sdf), sdf_exists=sdf_exists,
        sdf_size=sdf.stat().st_size if sdf.exists() else None,
        sdf_charts=len(sdf_doc.charts) if sdf_doc else None,
    )

    checks["script_executed"] = status.get("STATUS") in ("OK", "ERROR")
    checks["script_status_ok"] = status.get("STATUS") == "OK"
    checks["all_stages_pass"] = all(
        report.get("stages", {}).get(name) == "PASS" for name in STAGES
    )
    checks["nsx_generated"] = nsx_exists
    checks["nsx_verified"] = bool(netlist.get("all"))
    checks["mixed_sim_completed"] = sdf_exists
    checks["sdf_parsed"] = sdf_doc is not None
    checks["validation_pass"] = report.get("status") == "PASS"
    checks["schematic_cleanup"] = (
        report.get("stages", {}).get("schematic_cleanup") == "PASS"
    )
    checks["ran_in_hidden"] = hidden_has_project and not working_has_project
    checks["working_titles_unchanged"] = working_before == working_after
    checks["working_pid_alive"] = reference.alive()

    target = evidence.run_dir / artifact_subdir
    target.mkdir(parents=True, exist_ok=True)
    for source in (run / "status.txt", run / "report.json", nsx, sdf):
        if source.is_file():
            try:
                shutil.copy2(source, target / source.name)
            except OSError as exc:
                evidence.log("copy_failed", label=label, source=str(source),
                             error=str(exc))
    (target / "run_dir.txt").write_text(str(run), encoding="utf-8")
    (target / "experiment.pas").write_text(
        script.read_text(encoding="ascii", errors="replace"), encoding="utf-8"
    )

    return {
        "label": label,
        "checks": checks,
        "status": status,
        "report": report,
        "run_dir": str(run),
        "base": base,
        "nsx": str(nsx),
        "sdf": str(sdf),
        "netlist": netlist,
        "hidden_has_project": hidden_has_project,
        "working_has_project": working_has_project,
        "elapsed": round(elapsed, 1),
    }
