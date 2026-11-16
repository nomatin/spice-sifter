# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Stage 2: deliver DelphiScript to one specific Altium instance.

Production Spice Sifter starts::

    X2.EXE -RScriptingSystem:RunScriptFile(FileName="...pas"|ProcName="RunExperiment")

while the user's Altium is already running.  With two instances alive the
question is which one executes the script.  Two routes are compared:

* ``interactive`` – the ``-R`` launcher inherits the interactive desktop.
* ``hidden``      – the ``-R`` launcher is created on the private desktop.

Attribution is external and independent of the marker file: the script opens a
uniquely named text document, and the document window can only exist on the
desktop of the instance that executed the script.  The marker itself records
the document path seen by the executing instance.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time
import uuid

from hidden_session import (
    default_desktop_windows,
    terminate_owned,
    windows_for_pids,
)
from instances import (
    HiddenInstance,
    Reference,
    ensure_reference,
    send_script,
    titles,
    window_owner_summary,
)
from support import (
    Evidence,
    altium_exe,
    new_run_dir,
    pas_literal,
    x2_process_snapshot,
)

SCRIPT_TEMPLATE = """procedure RunExperiment;
var S, D;
begin
  S := TStringList.Create;
  try
    S.Add('STATUS=STARTED');
    S.Add('NONCE={nonce}');
    S.SaveToFile({marker});
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', {document});
    RunProcess('WorkspaceManager:OpenObject');
    D := Client.GetDocumentByPath({document});
    if D = Nil then
      S.Add('DOC_OPEN=NO')
    else begin
      S.Add('DOC_OPEN=YES');
      S.Add('ACTIVE_DOC=' + D.FileName);
    end;
    S.Add('STATUS=OK');
  except
    S.Add('STATUS=ERROR');
  end;
  S.SaveToFile({marker});
  S.Free;
end;
"""


def render_script(marker: Path, document: Path, nonce: str) -> str:
    return SCRIPT_TEMPLATE.format(
        nonce=nonce,
        marker=pas_literal(str(marker)),
        document=pas_literal(str(document)),
    )


def read_fields(path: Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def wait_for_marker(path: Path, timeout: float) -> tuple[dict, float]:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        fields = read_fields(path)
        if fields.get("STATUS") in ("OK", "ERROR"):
            return fields, time.monotonic() - start
        time.sleep(0.25)
    return read_fields(path), time.monotonic() - start


def run_route(exe: Path, reference: Reference, hidden: HiddenInstance,
              evidence: Evidence, route: str, base_dir: Path,
              timeout: float) -> dict:
    nonce = uuid.uuid4().hex[:10]
    work = base_dir / f"route_{route}_{nonce}"
    work.mkdir(parents=True, exist_ok=True)
    document = work / f"probe_{nonce}.txt"
    document.write_text(
        "Spice Sifter hidden-desktop routing probe\nnonce=" + nonce + "\n",
        encoding="ascii",
    )
    marker = work / "marker.txt"
    script = work / "probe.pas"
    script.write_text(render_script(marker, document, nonce), encoding="ascii")

    working_before = sorted(titles(reference.windows()))
    hidden_before = sorted(titles(hidden.windows()))
    evidence.log("route_start", route=route, nonce=nonce, document=str(document),
                 working_titles=working_before, hidden_titles=hidden_before)

    desktop = hidden.desktop if route == "hidden" else None
    launcher = send_script(exe, script, desktop, evidence=evidence, label=route)
    try:
        fields, elapsed = wait_for_marker(marker, timeout)
        launcher_alive = launcher.process.is_alive()
        evidence.log("route_marker", route=route, fields=fields,
                     elapsed=round(elapsed, 1), launcher_pid=launcher.pid,
                     launcher_alive=launcher_alive,
                     launcher_exit=launcher.process.exit_code())

        # Give Altium a moment to finish opening the document window.
        time.sleep(2)
        hidden_windows = hidden.windows()
        interactive_windows = default_desktop_windows()
        hidden_owners = window_owner_summary(hidden_windows, nonce)
        interactive_owners = window_owner_summary(interactive_windows, nonce)
        working_after = sorted(titles(windows_for_pids(interactive_windows, [reference.pid])))
        hidden_after = sorted(titles(hidden_windows))
        evidence.log(
            "route_windows",
            route=route,
            hidden_owners=hidden_owners,
            interactive_owners=interactive_owners,
            working_titles=working_after,
            hidden_titles=hidden_after,
        )

        if hidden_owners and not interactive_owners:
            executor = "hidden"
        elif interactive_owners and not hidden_owners:
            executor = "working"
        elif hidden_owners and interactive_owners:
            executor = "both"
        else:
            executor = "unknown"

        executor_pids = sorted(set(hidden_owners) | set(interactive_owners))
        known = {hidden.pid: "hidden", reference.pid: "working"}
        executor_pids_known = {pid: known.get(pid, "third") for pid in executor_pids}

        # Detect a third full instance created by the -R launcher.
        launcher_alive_after = launcher.process.is_alive()
        if launcher_alive_after:
            evidence.log("launcher_still_alive", route=route, pid=launcher.pid)

        processes = x2_process_snapshot(str(exe))
        evidence.log("route_processes", route=route, processes=processes)

        result = {
            "route": route,
            "nonce": nonce,
            "marker_status": fields.get("STATUS"),
            "marker_fields": fields,
            "marker_elapsed": round(elapsed, 1),
            "executor": executor,
            "executor_pids": executor_pids_known,
            "launcher_pid": launcher.pid,
            "launcher_alive_after_marker": launcher_alive_after,
            "launcher_exit_code": launcher.process.exit_code(),
            "working_titles_before": working_before,
            "working_titles_after": working_after,
            "hidden_titles_before": hidden_before,
            "hidden_titles_after": hidden_after,
            "working_windows_unchanged": working_before == working_after,
            "hidden_gained_window": hidden_after != hidden_before,
            "work_dir": str(work),
        }
        evidence.log("route_result", **result)

        # Stop only the -R process tree created above, and only when it did not
        # collapse into one of the two long-lived instances.
        if launcher.process.is_alive():
            stop = terminate_owned(launcher, timeout=10)
            evidence.log("launcher_terminated", route=route, **stop)
        else:
            evidence.log("launcher_exited_naturally", route=route,
                         exit_code=launcher.process.exit_code())
        return result
    finally:
        launcher.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="seconds to wait for each stage")
    parser.add_argument("--no-start-reference", action="store_true")
    parser.add_argument("--routes", default="interactive,hidden",
                        help="comma separated route order")
    args = parser.parse_args()

    run_dir = new_run_dir("stage2")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    evidence.log("start", altium=str(exe), timeout=args.timeout)

    reference = ensure_reference(exe, evidence, not args.no_start_reference,
                                 args.timeout)
    hidden = HiddenInstance.start(exe, evidence, args.timeout, label="hidden")
    results = []
    checks: dict = {}
    try:
        for route in [r.strip() for r in args.routes.split(",") if r.strip()]:
            results.append(run_route(exe, reference, hidden, evidence, route,
                                     run_dir, args.timeout))

        by_route = {item["route"]: item for item in results}
        checks["marker_written"] = all(
            item.get("marker_status") == "OK" for item in results
        )
        checks["hidden_pid_alive"] = hidden.alive()
        checks["working_pid_alive"] = reference.alive()
        if "hidden" in by_route:
            checks["hidden_route_executed_by_hidden"] = (
                by_route["hidden"]["executor"] == "hidden"
                and by_route["hidden"]["marker_status"] == "OK"
            )
            checks["hidden_route_not_executed_by_working"] = (
                not by_route["hidden"]["executor_pids"].get(reference.pid)
            )
            checks["working_untouched_by_hidden_route"] = (
                by_route["hidden"]["working_windows_unchanged"]
            )
        if "interactive" in by_route:
            checks["interactive_route_executed_by_working"] = (
                by_route["interactive"]["executor"] == "working"
            )
        evidence.finish("stage2", checks=checks, routes=results,
                        run_dir=str(run_dir))
    except Exception as exc:
        evidence.log("error", error=repr(exc))
        evidence.finish("stage2", checks=checks, routes=results,
                        error=repr(exc), run_dir=str(run_dir))
        raise
    finally:
        if hidden.launched.process.is_alive():
            hidden.stop(evidence)
        else:
            hidden.desktop.close()
        reference.stop(evidence)

    print("\nSTAGE 2 RESULT")
    for item in results:
        print(f"  route={item['route']}: marker={item['marker_status']} "
              f"executor={item['executor']} pids={item['executor_pids']} "
              f"launcher_exit={item['launcher_exit_code']}")
    for name, value in checks.items():
        print(f"  {name}: {value}")
    print("artifacts:", run_dir)
    failed = [name for name, value in checks.items() if value is False]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
