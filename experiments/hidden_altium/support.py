# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Shared helpers for the hidden-Altium experiment: evidence, snapshots, files."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

EXPERIMENT_ROOT = Path(__file__).resolve().parent
ARTIFACT_ROOT = EXPERIMENT_ROOT / "artifacts"
REPO_ROOT = EXPERIMENT_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    # Reuse production modules read-only (dblib_runner, spice_sifter, runtime_state).
    sys.path.insert(0, str(REPO_ROOT))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def new_run_dir(label: str) -> Path:
    run_id = utc_now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
    path = ARTIFACT_ROOT / f"{label}_{run_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Evidence:
    """Append-only JSONL evidence log plus a final JSON result."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.started = iso_now()
        self.events: list[dict] = []
        self.path = self.run_dir / "evidence.jsonl"

    def log(self, kind: str, **payload) -> dict:
        event = {"at": iso_now(), "kind": kind, **payload}
        self.events.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        print(f"[{event['at']}] {kind}: " + json.dumps(payload, ensure_ascii=False, default=str)[:400])
        return event

    def finish(self, name: str, **payload) -> Path:
        result = {"experiment": name, "started_at": self.started,
                  "finished_at": iso_now(), **payload}
        path = self.run_dir / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        return path


def powershell(command: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "powershell failed").strip())
    return result.stdout


def x2_process_snapshot(exe: str | None = None) -> list[dict]:
    """All X2.EXE processes, including launchers, with parent and creation time."""
    command = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$items = Get-CimInstance Win32_Process -Filter \"name='X2.EXE'\" "
        "-ErrorAction SilentlyContinue | ForEach-Object { "
        "$q = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; "
        "[pscustomobject]@{ pid=$_.ProcessId; parent=$_.ParentProcessId; "
        "path=$_.ExecutablePath; command_line=$_.CommandLine; "
        "creation=$_.CreationDate.ToUniversalTime().ToString('o'); "
        "session=$_.SessionId; "
        "responding=$(if ($q) { $q.Responding } else { $null }) } }; "
        "if ($null -eq $items) { '[]' } else { ConvertTo-Json -InputObject @($items) -Compress }"
    )
    output = powershell(command).strip()
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, dict):
        data = [data]
    expected = os.path.normcase(os.path.abspath(exe)) if exe else None
    for item in data:
        path = item.get("path")
        item["matches_installation"] = bool(
            expected and path and os.path.normcase(os.path.abspath(path)) == expected
        )
        item["is_script_launcher"] = "-R" in (item.get("command_line") or "")
    return data


def new_x2_since(exe: str, known_pids: set[int]) -> list[dict]:
    return [
        item for item in x2_process_snapshot(exe)
        if int(item["pid"]) not in known_pids
    ]


def profile_snapshot() -> dict:
    """mtime/size snapshot of the Altium user-profile trees (read-only)."""
    files: dict[str, list[int]] = {}
    roots = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = Path(os.environ.get(env, "")) / "Altium"
        if not base.is_dir():
            continue
        roots.append(str(base))
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part.casefold().startswith("cache") for part in path.parts):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files[str(path)] = [int(stat.st_mtime), stat.st_size]
    return {"roots": roots, "files": files}


def profile_diff(before: dict, after: dict) -> dict:
    a, b = before.get("files", {}), after.get("files", {})
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    return {
        "roots": after.get("roots"),
        "before_files": len(a),
        "after_files": len(b),
        "added": added[:50],
        "removed": removed[:50],
        "changed": changed[:50],
        "added_count": len(added),
        "removed_count": len(removed),
        "changed_count": len(changed),
    }


def wait_until(predicate, timeout: float, interval: float = 0.5):
    """Poll *predicate*; returns ``(value, elapsed, timeline)``."""
    start = time.monotonic()
    timeline = []
    while True:
        value = predicate()
        elapsed = time.monotonic() - start
        timeline.append((round(elapsed, 1), bool(value)))
        if value:
            return value, elapsed, timeline
        if elapsed >= timeout:
            return value, elapsed, timeline
        time.sleep(interval)


def write_json(path: Path, data) -> Path:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                          encoding="utf-8")
    return Path(path)


def altium_exe() -> Path:
    candidates = [
        os.environ.get("SPICE_SIFTER_ALTIUM", ""),
        r"C:\Program Files\Altium\AD24\X2.EXE",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Altium X2.EXE not found; set SPICE_SIFTER_ALTIUM")


def pas_literal(value: str) -> str:
    """DelphiScript ASCII literal (Cyrillic-safe), same escaping as production."""
    return "'" + "".join(
        ("''" if c == "'" else c) if ord(c) < 128 else "'#" + str(ord(c)) + "'"
        for c in str(value)
    ) + "'"


if __name__ == "__main__":
    print(json.dumps({
        "repo_root": str(REPO_ROOT),
        "altium": str(altium_exe()),
        "processes": x2_process_snapshot(),
    }, ensure_ascii=False, indent=2))
