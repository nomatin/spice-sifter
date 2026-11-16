# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Stage 3: run a real DbLib SPICE test inside the hidden Altium.

The production pipeline is reused unchanged:

* ``dblib_runner.preflight_component`` validates the DbLib component;
* ``dblib_runner.prepare_run`` copies the project template and renders the
  existing ``dblib_fixture.pas`` script;
* ``dblib_runner.build_report`` validates the Altium-written status, the
  Altium-generated NSX, the MixedSim SDF and the measurement result.

Only the launch target differs: the ``-R`` process is created on the private
desktop, so the script is executed by the hidden instance.  Nothing is written
to the working instance and the production launch/lock code is not modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from instances import HiddenInstance, ensure_reference
from spice_flow import run_dblib_test
from support import Evidence, altium_exe, new_run_dir


def load_component_data(library: Path, example: Path, item_id: str | None) -> dict:
    data = json.loads(Path(example).read_text(encoding="utf-8"))
    data.setdefault("component", {})["library"] = str(library)
    if item_id:
        data["component"]["id"] = item_id
    return data


def run_stage3(exe: Path, evidence: Evidence, library: Path, example: Path,
               item_id: str | None, timeout: float,
               start_reference: bool) -> dict:
    data = load_component_data(library, example, item_id)
    evidence.log("component_input", data=data)
    reference = ensure_reference(exe, evidence, start_reference, timeout)
    hidden = HiddenInstance.start(exe, evidence, timeout, label="hidden")
    try:
        result = run_dblib_test(exe, hidden, reference, evidence, data, timeout,
                                label="stage3")
        return dict(result["checks"])
    finally:
        if hidden.launched.process.is_alive():
            hidden.stop(evidence)
        else:
            hidden.desktop.close()
        reference.stop(evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library",
                        default=r"C:\Users\Nomatin\Documents\Altium\MY_LIB\MY_LIB\MY_LIB.DbLib",
                        help="absolute path to an existing .DbLib file")
    parser.add_argument("--example", default=str(
        Path(__file__).resolve().parents[2] / "examples" / "dblib_resistor_r0009.json"
    ))
    parser.add_argument("--id", default=None, help="override component id")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--no-start-reference", action="store_true")
    args = parser.parse_args()

    run_dir = new_run_dir("stage3")
    evidence = Evidence(run_dir)
    exe = altium_exe()
    evidence.log("start", altium=str(exe), library=args.library,
                 example=args.example, timeout=args.timeout)
    checks: dict = {}
    try:
        checks = run_stage3(exe, evidence, Path(args.library), Path(args.example),
                            args.id, args.timeout, not args.no_start_reference)
        evidence.finish("stage3", checks=checks, run_dir=str(run_dir))
    except Exception as exc:
        evidence.log("error", error=repr(exc))
        evidence.finish("stage3", checks=checks, error=repr(exc),
                        run_dir=str(run_dir))
        raise

    print("\nSTAGE 3 RESULT")
    for name, value in checks.items():
        print(f"  {name}: {value}")
    print("artifacts:", run_dir)
    failed = [name for name, value in checks.items() if value is False]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
