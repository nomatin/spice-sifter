# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Pending-run marker, safe recovery and service-command tests."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import runtime_state as rs
from spice_sifter import TestError, altium_session

START = "2026-11-01T12:00:00Z"


def marker(root, run_id="run_a38f52", altium_pid=1234, start_time=START, run_dir=None, timeout_at=None):
    run_dir = Path(run_dir) if run_dir else Path(root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    started = rs.utc_now()
    return rs.write_marker(
        root,
        run_id=run_id,
        run_dir=run_dir,
        altium_pid=altium_pid,
        altium_start_time=start_time,
        started_at=started,
        timeout_at=timeout_at if timeout_at is not None else started + timedelta(hours=1),
    )


def alive(pid):
    return {"pid": pid, "start_time": START, "responding": True}


def dead(pid):
    return None


def reused(pid):
    return {"pid": pid, "start_time": "2026-11-01T13:00:00Z", "responding": True}


def unverifiable(pid):
    raise rs.ProcessQueryError("access denied")


class MarkerTests(unittest.TestCase):
    def test_marker_is_structured_and_atomic(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            created = marker(root)
            data = json.loads(rs.marker_path(root).read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(data["run_id"], created["run_id"])
            self.assertEqual(data["state"], "PENDING")
            self.assertIn("started_at", data)
            self.assertIn("timeout_at", data)
            self.assertFalse(list(root.glob("*.tmp")))

    def test_completed_status_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            created = marker(root)
            (Path(created["run_dir"]) / "status.txt").write_text("STATUS=OK\n", encoding="utf-8")
            assessment = rs.assess_pending(root, dead)
            self.assertEqual(assessment["state"], "COMPLETED")
            self.assertTrue(assessment["removable"])
            self.assertTrue(rs.resolve_pending(root, dead)["removed"])
            self.assertFalse(rs.marker_path(root).exists())

    def test_exited_process_is_stale_and_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            assessment = rs.assess_pending(root, dead)
            self.assertEqual(assessment["state"], "STALE")
            self.assertEqual(assessment["diagnostics"]["reason"], "process_not_found")
            self.assertTrue(assessment["removable"])
            self.assertTrue(rs.resolve_pending(root, dead)["removed"])

    def test_reused_pid_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            assessment = rs.assess_pending(root, reused)
            self.assertEqual(assessment["state"], "STALE")
            self.assertEqual(assessment["diagnostics"]["reason"], "pid_reused")

    def test_running_process_is_active_and_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            assessment = rs.assess_pending(root, alive)
            self.assertEqual(assessment["state"], "ACTIVE")
            self.assertFalse(assessment["removable"])
            self.assertFalse(rs.resolve_pending(root, alive)["removed"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_expired_timeout_without_status_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root, timeout_at=rs.utc_now() - timedelta(seconds=1))
            assessment = rs.assess_pending(root, alive)
            self.assertEqual(assessment["state"], "UNKNOWN")
            self.assertEqual(
                assessment["diagnostics"]["reason"],
                "timeout_expired_without_final_status",
            )
            self.assertFalse(assessment["removable"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_unverifiable_process_is_unknown_and_kept(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            assessment = rs.assess_pending(root, unverifiable)
            self.assertEqual(assessment["state"], "UNKNOWN")
            self.assertFalse(assessment["removable"])
            self.assertIn("process_query_failed", assessment["diagnostics"]["reason"])
            self.assertFalse(rs.resolve_pending(root, unverifiable)["removed"])

    def test_marker_without_altium_pid_is_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root, altium_pid=None, start_time=None)
            self.assertEqual(rs.assess_pending(root, dead)["state"], "UNKNOWN")

    def test_collect_tmp_final_status_is_completed(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            created = marker(root)
            run_dir = Path(created["run_dir"])
            (run_dir / "status.txt").write_text(
                "STARTED\nSTATUS=SAVE_PENDING\nCODE=SDF_NOT_SAVED\n", encoding="utf-8"
            )
            (run_dir / "collect.tmp").write_text("STATUS=OK\nSDF_PATH=test.sdf\n", encoding="utf-8")
            assessment = rs.assess_pending(root, alive)
            self.assertEqual(assessment["state"], "COMPLETED")
            self.assertEqual(assessment["diagnostics"]["status_files"]["status_source"], "collect.tmp")
            self.assertTrue(rs.resolve_pending(root, alive)["removed"])

    def test_corrupted_marker_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            rs.marker_path(root).write_text("not json\nand not a path", encoding="utf-8")
            assessment = rs.assess_pending(root, dead)
            self.assertEqual(assessment["state"], "CORRUPTED")
            self.assertFalse(assessment["removable"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_legacy_format_completed_and_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            run = Path(td) / "legacy_run"
            run.mkdir()
            (run / "status.txt").write_text("STATUS=ERROR\n", encoding="utf-8")
            root.mkdir(parents=True, exist_ok=True)
            rs.marker_path(root).write_text(str(run), encoding="utf-8")
            assessment = rs.assess_pending(root, dead)
            self.assertEqual(assessment["state"], "COMPLETED")
            self.assertTrue(assessment["diagnostics"]["legacy_marker"])
            rs.marker_path(root).write_text(str(Path(td) / "missing_run"), encoding="utf-8")
            self.assertEqual(rs.assess_pending(root, dead)["state"], "STALE")


class SessionTests(unittest.TestCase):
    def test_session_recovers_stale_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            with patch("spice_sifter.tempfile.gettempdir", return_value=td), patch(
                "runtime_state.process_info", side_effect=dead
            ):
                with altium_session():
                    self.assertFalse(rs.marker_path(root).exists())

    def test_session_blocks_active_marker_without_removing_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            with patch("spice_sifter.tempfile.gettempdir", return_value=td), patch(
                "runtime_state.process_info", side_effect=alive
            ):
                with self.assertRaises(TestError) as ctx:
                    with altium_session():
                        pass
            self.assertEqual(ctx.exception.code, "ALTIUM_PENDING_RUN")
            self.assertEqual(ctx.exception.diagnostics["run_id"], "run_a38f52")
            self.assertEqual(ctx.exception.suggested_action, "Run: python spice_sifter.py doctor --json")
            self.assertTrue(rs.marker_path(root).exists())

    def test_session_blocks_unknown_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            with patch("spice_sifter.tempfile.gettempdir", return_value=td), patch(
                "runtime_state.process_info", side_effect=unverifiable
            ):
                with self.assertRaises(TestError) as ctx:
                    with altium_session():
                        pass
            self.assertEqual(ctx.exception.code, "ALTIUM_STATE_UNKNOWN")
            self.assertFalse(ctx.exception.diagnostics["recovery_possible"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_session_blocks_corrupted_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            rs.marker_path(root).write_text("broken\nmarker", encoding="utf-8")
            with patch("spice_sifter.tempfile.gettempdir", return_value=td):
                with self.assertRaises(TestError) as ctx:
                    with altium_session():
                        pass
            self.assertEqual(ctx.exception.code, "ALTIUM_STATE_UNKNOWN")
            self.assertTrue(rs.marker_path(root).exists())


class RecoverTests(unittest.TestCase):
    def test_recover_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            result = rs.recover_pending(td, dead)
            self.assertEqual(result["status"], "RECOVERED")
            self.assertTrue(result["ready"])
            self.assertEqual(result["run_id"], "run_a38f52")
            self.assertFalse(rs.marker_path(root).exists())

    def test_recover_active_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            result = rs.recover_pending(td, alive)
            self.assertEqual(result["status"], "REFUSED")
            self.assertFalse(result["ready"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_recover_unknown_keeps_marker_and_explains(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            result = rs.recover_pending(td, unverifiable)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertFalse(result["ready"])
            self.assertIn("recover", result["suggested_action"])
            self.assertTrue(rs.marker_path(root).exists())

    def test_recover_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            result = rs.recover_pending(td, dead)
            self.assertEqual(result["status"], "NOTHING_TO_RECOVER")
            self.assertTrue(result["ready"])

    def test_recover_lock_busy(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with rs.acquire_lock(root):
                result = rs.recover_pending(td, dead)
            self.assertEqual(result["status"], "BUSY")
            self.assertFalse(result["ready"])


class DoctorTests(unittest.TestCase):
    def fake_exe(self, td):
        path = Path(td) / "X2.EXE"
        path.write_text("stub", encoding="utf-8")
        return path

    def run_doctor(self, td, processes, pending=None):
        with patch("runtime_state.altium_processes", return_value=processes), patch(
            "runtime_state.file_version", return_value="24.4.1.13"
        ), patch("runtime_state.process_info", side_effect=pending or dead):
            return rs.doctor_report(str(self.fake_exe(td)), temp_dir=td)

    def test_missing_altium(self):
        with tempfile.TemporaryDirectory() as td:
            report = rs.doctor_report(str(Path(td) / "missing.EXE"), temp_dir=td)
            self.assertEqual(report["status"], "ERROR")
            self.assertFalse(report["ready"])

    def test_altium_not_running(self):
        with tempfile.TemporaryDirectory() as td:
            report = self.run_doctor(td, [])
            self.assertEqual(report["status"], "ERROR")
            self.assertFalse(report["altium"]["running"])

    def test_ready(self):
        with tempfile.TemporaryDirectory() as td:
            report = self.run_doctor(td, [{"pid": 1, "responding": True, "start_time": START}])
            self.assertEqual(report["status"], "READY")
            self.assertTrue(report["ready"])
            self.assertTrue(report["lock"]["available"])
            self.assertIsNone(report["pending_run"])

    def test_active_pending_is_busy(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            report = self.run_doctor(
                td,
                [{"pid": 1, "responding": True, "start_time": START}],
                pending=alive,
            )
            self.assertEqual(report["status"], "BUSY")
            self.assertEqual(report["pending_run"]["state"], "ACTIVE")

    def test_stale_pending_requires_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            marker(root)
            report = self.run_doctor(
                td, [{"pid": 1, "responding": True, "start_time": START}]
            )
            self.assertEqual(report["status"], "RECOVERY_REQUIRED")
            self.assertTrue(report["pending_run"]["recovery_possible"])

    def test_lock_busy(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with rs.acquire_lock(root):
                report = self.run_doctor(
                    td, [{"pid": 1, "responding": True, "start_time": START}]
                )
            self.assertEqual(report["status"], "BUSY")
            self.assertFalse(report["lock"]["available"])

    def test_unwritable_temporary_directory(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = Path(td) / "blocker"
            blocker.write_text("x", encoding="utf-8")
            with patch("runtime_state.state_root", return_value=blocker / "SpiceSifter"), patch(
                "runtime_state.altium_processes",
                return_value=[{"pid": 1, "responding": True, "start_time": START}],
            ), patch("runtime_state.file_version", return_value="24.4.1.13"):
                report = rs.doctor_report(str(self.fake_exe(td)), temp_dir=td)
            self.assertEqual(report["status"], "ERROR")
            self.assertFalse(report["temporary_directory"]["writable"])


if __name__ == "__main__":
    unittest.main()
