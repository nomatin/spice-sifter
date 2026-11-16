# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Hidden Altium integration tests that never start Altium.

These tests cover mode selection, launcher propagation into every script launch
point (including ``collect.pas``), identity checks, cleanup, batch stop policy
and the hidden doctor/recover contract.
"""
import json
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

import runtime_state as rs
from batch_agent import run_batch
from hidden_instance import (
    HIDDEN_DESKTOP_UNAVAILABLE,
    HIDDEN_LAUNCH_FAILED,
    HIDDEN_PROCESS_LOST,
    HIDDEN_SCRIPT_TIMEOUT,
    HIDDEN_STATE_AMBIGUOUS,
    HIDDEN_STOP_FAILED,
    HiddenAltiumError,
    HiddenAltiumSession,
    ProcessIdentity,
)
from spice_sifter import (
    SYSTEMIC_ERROR_CODES,
    TestError,
    load_test,
    main,
    run_altium,
    run_test_file,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self, alive=True):
        self._alive = alive
        self.closed = False

    def is_alive(self):
        return self._alive

    def wait(self, timeout):
        self._alive = False
        return True

    def exit_code(self):
        return None if self._alive else 1

    def close(self):
        self.closed = True


class FakeJob:
    def __init__(self):
        self.handle = 1
        self.error = None
        self.assign_error = None
        self.configured = True
        self.assigned = []
        self.terminated = False
        self.closed = False

    @property
    def usable(self):
        return bool(self.handle and self.configured and not self.error)

    def assign(self, process):
        self.assigned.append(process)
        return True

    def terminate(self, exit_code=1):
        self.terminated = True
        return True

    def close(self):
        self.closed = True
        self.handle = None


class FakeDesktop:
    def __init__(self, name="SpiceSifterHidden-test"):
        self.name = name
        self.handle = 1
        self.closed = False

    def windows(self):
        return []

    def windows_of(self, pids):
        return []

    def close(self):
        self.closed = True
        self.handle = None


class FakeLauncher:
    """Records every addressed script launch and simulates Altium results."""

    def __init__(self, hooks=None, alive=True):
        self.calls = []
        self.hooks = hooks or {}
        self._alive = alive
        self.marker = {
            "altium_pid": 4242,
            "altium_start_time": "2026-01-01T00:00:00Z",
            "altium_instance": "hidden",
            "altium_desktop": "SpiceSifterHidden-test",
        }

    def ensure_ready(self):
        if not self._alive:
            raise HiddenAltiumError(HIDDEN_PROCESS_LOST, "gone")
        return {"mode": "hidden", "pid": self.marker["altium_pid"]}

    def marker_fields(self):
        return dict(self.marker)

    def run_script(self, script, proc_name, **kwargs):
        self.calls.append((str(script), proc_name))
        hook = self.hooks.get(proc_name)
        if hook is not None:
            hook(Path(script))
        return types.SimpleNamespace(pid=9000)

    def alive(self):
        return self._alive

    def diagnostics(self):
        return {"mode": "hidden", "pid": self.marker["altium_pid"],
                "commands": [{"proc_name": name} for _, name in self.calls]}


class FakeSession:
    def __init__(self, state_root):
        self.state_root = Path(state_root)
        self.started = False
        self.ready = False
        self.stopped = False
        self.stop_result = {}

    def start(self):
        self.started = True
        return self

    def wait_ready(self, timeout=None):
        self.ready = True
        return self.diagnostics()

    def stop(self):
        self.stopped = True
        return {}

    def diagnostics(self):
        return {"mode": "hidden", "session_id": "test", "pid": 111,
                "desktop": "SpiceSifterHidden-test", "commands": [],
                "stop": self.stop_result}

    def marker_fields(self):
        return {"altium_pid": 111,
                "altium_start_time": "2026-01-01T00:00:00Z"}

    def ensure_ready(self):
        return self.diagnostics()

    def run_script(self, script, proc_name, **kwargs):
        return types.SimpleNamespace(pid=9001)

    def alive(self):
        return True


def valid_report(status="PASS", value=1.0):
    counts = dict(
        passed=1 if status == "PASS" else 0,
        failed=1 if status == "FAIL" else 0,
        measured=0,
        errors=1 if status == "ERROR" else 0,
        not_run=0,
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "validation_status": "ASSERTIONS",
        "results": [{
            "result_id": "run_001:check:0",
            "run_id": "run_001",
            "kind": "check",
            "id": 0,
            "status": status,
            "value": value if status != "ERROR" else None,
            "unit": None,
            "error": None if status != "ERROR"
            else {"code": "X", "message": "x"},
        }],
        "coverage": {
            "status": "OK", "expected": 1, "completed": 1, "missing": 0,
            **counts,
        },
        "error": None if status != "ERROR" else {"code": "X", "message": "x"},
    }


def hidden_ready_doctor():
    return {
        "status": "READY",
        "ready": True,
        "mode": "hidden",
        "altium": {"running": False, "responding": None, "processes": []},
        "hidden_desktop": {"supported": True, "probe_created": True},
        "hidden_instance": None,
        "lock": {"available": True},
        "pending_run": None,
        "temporary_directory": {"writable": True},
    }


class ModeSelectionTests(unittest.TestCase):
    def test_default_batch_mode_is_current(self):
        calls = []

        def runner(path, **kwargs):
            calls.append(kwargs)
            return valid_report()

        with patch("hidden_instance.HiddenAltiumSession") as session:
            result = run_batch(
                ["a"], runner=runner,
                doctor=lambda: {
                    "ready": True,
                    "altium": {"running": True, "responding": True},
                    "lock": {"available": True},
                    "pending_run": None,
                    "temporary_directory": {"writable": True},
                },
            )
        session.assert_not_called()
        self.assertEqual(calls, [{"altium": None, "timeout": 120}])
        self.assertEqual(result["status"], "PASS")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            run_batch(["a"], altium_instance="remote")

    def test_single_test_default_stays_current(self):
        args = {}

        def fake_execute_test(*positional, **kwargs):
            args.update(kwargs)

        with patch("spice_sifter.execute_test", side_effect=fake_execute_test), \
                patch("spice_sifter.require_running_altium") as require:
            report = run_test_file(
                ROOT / "examples" / "divider.json",
                altium=str(ROOT / "spice_sifter.py"),
            )
        self.assertNotIn("launcher", args)
        require.assert_called_once()
        self.assertNotIn("altium_session", report)


class LauncherPropagationTests(unittest.TestCase):
    def test_experiment_and_collect_are_routed_to_hidden(self):
        data = load_test(ROOT / "examples" / "divider.json")
        fixture = ROOT / "tests" / "fixtures" / "divider_ad24_4.sdf"
        captured = []

        def hook(script):
            work = script.parent
            captured.append({
                "script": script.name,
                "marker": json.loads(
                    (Path(td) / "SpiceSifter"
                     / "pending-run.txt").read_text(encoding="utf-8")
                ),
            })
            if script.name == "collect.pas":
                shutil.copy2(fixture, work / "test.sdf")
                (work / "status.txt").write_text(
                    "STATUS=OK\nSDF_PATH=" + str(work / "test.sdf"),
                    encoding="utf-8",
                )
            else:
                (work / "started.txt").write_text("STARTED")
                (work / "status.txt").write_text(
                    "STATUS=SAVE_PENDING", encoding="utf-8"
                )

        launcher = FakeLauncher(hooks={"RunTests": hook})
        report = {}
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.tempfile.gettempdir", return_value=td), \
                patch("spice_sifter.subprocess.Popen") as popen:
            outcome, rows = run_altium(
                "X2.EXE", data, ROOT, 30, False, report, launcher=launcher
            )
        popen.assert_not_called()
        self.assertEqual(len(launcher.calls), 2)
        self.assertEqual(Path(launcher.calls[1][0]).name, "collect.pas")
        self.assertNotEqual(Path(launcher.calls[0][0]).name, "collect.pas")
        self.assertEqual(outcome, "PASS")
        self.assertEqual(captured[0]["marker"]["altium_instance"], "hidden")
        self.assertEqual(captured[0]["marker"]["altium_desktop"],
                         "SpiceSifterHidden-test")
        self.assertEqual(captured[0]["marker"]["altium_pid"], 4242)
        self.assertEqual(report["altium_session"]["mode"], "hidden")

    def test_dblib_launch_uses_launcher(self):
        from dblib_runner import _launch

        launcher = FakeLauncher()
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.tempfile.gettempdir", return_value=td):
            run = Path(td) / "run"
            run.mkdir()
            script = run / "experiment.pas"
            script.write_text("procedure RunExperiment; begin end;")

            def hook(_script):
                (run / "status.txt").write_text(
                    "STATUS=OK\nRESULT=1", encoding="cp1251"
                )

            launcher.hooks["RunExperiment"] = hook
            with patch("dblib_runner.subprocess.Popen") as popen:
                status, _ = _launch(
                    "X2.EXE", script, run, 5, False, launcher=launcher
                )
            popen.assert_not_called()
            self.assertFalse(
                (Path(td) / "SpiceSifter" / "pending-run.txt").exists()
            )
        self.assertEqual(status["STATUS"], "OK")
        self.assertEqual(launcher.calls, [(str(script), "RunExperiment")])

    def test_hidden_launcher_error_is_not_masked(self):
        data = load_test(ROOT / "examples" / "divider.json")
        launcher = FakeLauncher(alive=False)

        def hook(_script):
            (Path(_script).parent / "started.txt").write_text("STARTED")

        launcher.hooks["RunTests"] = hook
        report = {}
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.tempfile.gettempdir", return_value=td):
            with self.assertRaises(TestError) as ctx:
                run_altium(
                    "X2.EXE", data, ROOT, 0.05, False, report,
                    launcher=launcher,
                )
        self.assertEqual(ctx.exception.code, HIDDEN_PROCESS_LOST)


class SessionIdentityTests(unittest.TestCase):
    def make_session(self, td):
        exe = Path(td) / "X2.EXE"
        exe.write_text("stub")
        session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
        session.identity = ProcessIdentity(100, "2026-01-01T00:00:00Z", 1)
        session.process = FakeProcess(alive=True)
        session.job = FakeJob()
        session.desktop = FakeDesktop()
        session._started = True
        session._ready = True
        session._job_managed = True
        return session

    def test_wrong_pid_or_creation_time_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            session = self.make_session(td)
            with patch("hidden_instance.process_still_ours", return_value=False):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.ensure_ready()
        self.assertEqual(ctx.exception.code, HIDDEN_PROCESS_LOST)

    def test_foreign_process_is_never_terminated(self):
        with tempfile.TemporaryDirectory() as td:
            session = self.make_session(td)
            job = session.job
            with patch("hidden_instance.process_still_ours", return_value=False):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.stop()
            kept = rs.hidden_session_path(session.state_root).exists()
        self.assertEqual(ctx.exception.code, HIDDEN_STATE_AMBIGUOUS)
        self.assertFalse(job.terminated)
        self.assertTrue(job.closed)
        self.assertTrue(kept)
        self.assertTrue(session.stop_result["state_kept"])

    def test_owned_process_is_terminated_through_the_job(self):
        with tempfile.TemporaryDirectory() as td:
            session = self.make_session(td)
            job = session.job
            with patch("hidden_instance.process_still_ours", return_value=True):
                result = session.stop()
        self.assertTrue(job.terminated)
        self.assertTrue(result["process_gone"])
        self.assertEqual(result["reason"], "terminated_owned_job_object")
        self.assertTrue(job.closed)
        self.assertTrue(session.desktop.closed)

    def test_start_failure_closes_desktop_and_job(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
            desktop = FakeDesktop()
            job = FakeJob()
            with patch("hidden_instance.Desktop.create", return_value=desktop), \
                    patch("hidden_instance.ProcessJob", return_value=job), \
                    patch.object(HiddenAltiumSession, "_altium_snapshot",
                                 return_value=[]), \
                    patch("hidden_instance.launch_on_desktop",
                          side_effect=OSError("boom")):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.start()
        self.assertEqual(ctx.exception.code, HIDDEN_LAUNCH_FAILED)
        self.assertTrue(desktop.closed)
        self.assertTrue(job.closed)

    def test_desktop_failure_has_specific_code(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
            with patch("hidden_instance.Desktop.create",
                       side_effect=HiddenAltiumError(
                           HIDDEN_DESKTOP_UNAVAILABLE, "no desktop")), \
                    patch.object(HiddenAltiumSession, "_altium_snapshot",
                                 return_value=[]):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.start()
        self.assertEqual(ctx.exception.code, HIDDEN_DESKTOP_UNAVAILABLE)


class HiddenBatchTests(unittest.TestCase):
    def test_single_instance_serves_batch_and_stop_is_guaranteed(self):
        calls = []
        sessions = []

        def factory():
            session = FakeSession(self.temp.name)
            sessions.append(session)
            return session

        def runner(path, **kwargs):
            calls.append((path, kwargs.get("launcher")))
            if path == "b":
                return {
                    "schema_version": "1.0",
                    "status": "ERROR",
                    "error": {"code": HIDDEN_SCRIPT_TIMEOUT, "message": "x"},
                    "results": [], "coverage": {},
                }
            return valid_report()

        with tempfile.TemporaryDirectory() as td:
            self.temp = Path(td)
            result = run_batch(
                ["a", "b", "c"], altium=str(ROOT / "spice_sifter.py"),
                runner=runner, doctor=hidden_ready_doctor,
                altium_instance="hidden", session_factory=factory,
                temp_dir=td,
            )
        self.assertEqual([call[0] for call in calls], ["a", "b"])
        self.assertTrue(all(call[1] is sessions[0] for call in calls))
        self.assertEqual(sessions[0].started, True)
        self.assertEqual(sessions[0].stopped, True)
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], HIDDEN_SCRIPT_TIMEOUT)
        self.assertEqual([run["status"] for run in result["runs"]],
                         ["PASS", "ERROR", "NOT_RUN"])
        self.assertEqual(result["runs"][0]["report"]["status"], "PASS")
        self.assertEqual(result["altium_session"]["mode"], "hidden")

    def test_start_failure_marks_every_test_not_run(self):
        def factory():
            class Failing(FakeSession):
                def start(self):
                    raise HiddenAltiumError(HIDDEN_LAUNCH_FAILED, "cannot start")
            return Failing(self.temp.name)

        def runner(path, **kwargs):
            raise AssertionError("runner must not be called")

        with tempfile.TemporaryDirectory() as td:
            self.temp = Path(td)
            result = run_batch(
                ["a", "b"], altium=str(ROOT / "spice_sifter.py"),
                runner=runner, doctor=hidden_ready_doctor,
                altium_instance="hidden", session_factory=factory,
                temp_dir=td,
            )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], HIDDEN_LAUNCH_FAILED)
        self.assertEqual([run["status"] for run in result["runs"]],
                         ["NOT_RUN", "NOT_RUN"])

    def test_stop_error_is_reported_without_losing_results(self):
        def factory():
            class BadStop(FakeSession):
                def stop(self):
                    self.stopped = True
                    raise HiddenAltiumError(HIDDEN_STOP_FAILED, "still alive")
            return BadStop(self.temp.name)

        with tempfile.TemporaryDirectory() as td:
            self.temp = Path(td)
            result = run_batch(
                ["a"], altium=str(ROOT / "spice_sifter.py"),
                runner=lambda path, **kwargs: valid_report(),
                doctor=hidden_ready_doctor, altium_instance="hidden",
                session_factory=factory, temp_dir=td,
            )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], HIDDEN_STOP_FAILED)
        self.assertEqual(result["summary"]["completed_runs"], 1)
        self.assertEqual(result["runs"][0]["report"]["status"], "PASS")


class HiddenDoctorTests(unittest.TestCase):
    def fake_exe(self, td):
        path = Path(td) / "X2.EXE"
        path.write_text("stub")
        return path

    def test_hidden_doctor_does_not_require_running_altium(self):
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"), \
                patch("hidden_instance.probe_hidden_desktop",
                      return_value={"supported": True, "probe_created": True,
                                    "error": None}):
            report = rs.doctor_report(
                str(self.fake_exe(td)), temp_dir=td,
                process_query=lambda exe: [], mode="hidden",
            )
        self.assertEqual(report["status"], "READY")
        self.assertTrue(report["ready"])
        self.assertFalse(report["altium"]["running"])
        self.assertEqual(report["mode"], "hidden")
        self.assertTrue(report["hidden"]["ready"])
        self.assertTrue(report["hidden_desktop"]["supported"])

    def test_hidden_doctor_ignores_multiple_altium_processes(self):
        processes = [{"pid": 1, "responding": True},
                     {"pid": 2, "responding": True}]
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"), \
                patch("hidden_instance.probe_hidden_desktop",
                      return_value={"supported": True, "probe_created": True}):
            report = rs.doctor_report(
                str(self.fake_exe(td)), temp_dir=td,
                process_query=lambda exe: processes, mode="hidden",
            )
        self.assertEqual(report["status"], "READY")
        self.assertEqual(report["altium"]["state"], "AMBIGUOUS")

    def test_hidden_doctor_reports_missing_desktop_support(self):
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"), \
                patch("hidden_instance.probe_hidden_desktop",
                      return_value={"supported": False, "probe_created": False,
                                    "error": "locked session"}):
            report = rs.doctor_report(
                str(self.fake_exe(td)), temp_dir=td,
                process_query=lambda exe: [], mode="hidden",
            )
        self.assertEqual(report["status"], "ERROR")
        self.assertIn("hidden", " ".join(report["diagnostics"]).lower())

    def test_current_doctor_still_requires_running_altium(self):
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"):
            report = rs.doctor_report(
                str(self.fake_exe(td)), temp_dir=td,
                process_query=lambda exe: [],
            )
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["mode"], "current")

    def test_hidden_doctor_cli_exit_code_and_fields(self):
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"), \
                patch("hidden_instance.probe_hidden_desktop",
                      return_value={"supported": True, "probe_created": True}), \
                patch("runtime_state.tempfile.gettempdir", return_value=td), \
                patch("runtime_state.altium_processes", return_value=[]):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main([
                    "doctor", "--json", "--altium-instance", "hidden",
                    "--altium", str(self.fake_exe(td)),
                ])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "hidden")
        self.assertTrue(payload["hidden"]["ready"])


class HiddenRecoveryTests(unittest.TestCase):
    def dead(self, pid):
        return None

    def alive_lookup(self, pid):
        return {"pid": pid, "start_time": "2026-01-01T00:00:00.000Z"}

    def write_session(self, root):
        rs.write_hidden_session(root, {
            "session_id": "abc123",
            "state": "READY",
            "pid": 777,
            "creation_time": "2026-01-01T00:00:00.000Z",
            "desktop": "SpiceSifterHidden-abc123",
        })

    def test_stale_hidden_session_is_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            result = rs.recover_pending(td, self.dead)
            self.assertEqual(result["status"], "RECOVERED")
            self.assertTrue(result["removed_hidden_session"])
            self.assertFalse(rs.hidden_session_path(root).exists())

    def test_active_hidden_session_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            result = rs.recover_pending(td, self.alive_lookup)
            self.assertEqual(result["status"], "REFUSED")
            self.assertTrue(rs.hidden_session_path(root).exists())

    def test_hidden_session_never_matches_a_recycled_pid(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            recycled = lambda pid: {
                "pid": pid, "start_time": "2027-07-07T00:00:00.000Z"
            }
            assessment = rs.assess_hidden_session(root, recycled)
        self.assertEqual(assessment["state"], "STALE")

    def test_doctor_requires_recovery_for_stale_hidden_session(self):
        with tempfile.TemporaryDirectory() as td, \
                patch("runtime_state.file_version", return_value="24.4.1.13"), \
                patch("hidden_instance.probe_hidden_desktop",
                      return_value={"supported": True, "probe_created": True}):
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            report = rs.doctor_report(
                str(exe), temp_dir=td,
                process_query=lambda exe: [], mode="hidden",
                hidden_process_lookup=self.dead,
            )
        self.assertEqual(report["status"], "RECOVERY_REQUIRED")
        self.assertEqual(report["hidden_instance"]["state"], "STALE")


class LockingTests(unittest.TestCase):
    def test_batch_lock_prevents_runner_deadlock(self):
        from spice_sifter import altium_session

        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with patch("runtime_state.tempfile.gettempdir", return_value=td), \
                    rs.acquire_batch_lock(root):
                with altium_session() as held:
                    self.assertEqual(held, Path(td) / "SpiceSifter")

    def test_plain_lock_is_not_reentrant(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with rs.acquire_lock(root):
                with self.assertRaises(rs.MarkerError):
                    with rs.acquire_lock(root):
                        pass

    def test_systemic_codes_include_hidden_failures(self):
        for code in (
            HIDDEN_DESKTOP_UNAVAILABLE,
            HIDDEN_LAUNCH_FAILED,
            HIDDEN_PROCESS_LOST,
            HIDDEN_SCRIPT_TIMEOUT,
            HIDDEN_STATE_AMBIGUOUS,
            HIDDEN_STOP_FAILED,
        ):
            self.assertIn(code, SYSTEMIC_ERROR_CODES)

    def test_hidden_batch_reports_busy_instead_of_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with rs.acquire_lock(root):
                result = run_batch(
                    ["a"], altium=str(ROOT / "spice_sifter.py"),
                    runner=lambda *args, **kwargs: valid_report(),
                    doctor=hidden_ready_doctor, altium_instance="hidden",
                    session_factory=lambda: FakeSession(td), temp_dir=td,
                )
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["error"]["code"], "BUSY")
        self.assertEqual(result["runs"][0]["status"], "NOT_RUN")

    def test_hidden_single_run_reports_busy_instead_of_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")

            class Session(FakeSession):
                def __init__(self, *args, **kwargs):
                    super().__init__(root)

            with rs.acquire_lock(root), \
                    patch("hidden_instance.HiddenAltiumSession", Session), \
                    patch("spice_sifter.execute_test") as execute:
                report = run_test_file(
                    ROOT / "examples" / "divider.json", altium=str(exe),
                    altium_instance="hidden", temp_dir=td,
                )
        execute.assert_not_called()
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["error"]["code"], "BUSY")


class SingleRunSessionTests(unittest.TestCase):
    def test_hidden_single_run_owns_one_session(self):
        created = []

        class Session(FakeSession):
            def __init__(self, altium_path, **kwargs):
                super().__init__(self.state_root_dir)
                created.append((altium_path, kwargs))

        with tempfile.TemporaryDirectory() as td:
            Session.state_root_dir = td
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            executed = []

            def fake_execute(*args, **kwargs):
                executed.append(kwargs.get("launcher"))

            with patch("hidden_instance.HiddenAltiumSession", Session), \
                    patch("spice_sifter.execute_test",
                          side_effect=fake_execute):
                report = run_test_file(
                    ROOT / "examples" / "divider.json", altium=str(exe),
                    altium_instance="hidden", ready_timeout=45,
                )
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][0], str(exe))
        self.assertEqual(created[0][1]["ready_timeout"], 45)
        self.assertIsInstance(executed[0], Session)
        self.assertTrue(executed[0].started)
        self.assertTrue(executed[0].ready)
        self.assertTrue(executed[0].stopped)
        self.assertEqual(report["altium_session"]["mode"], "hidden")


if __name__ == "__main__":
    unittest.main()
