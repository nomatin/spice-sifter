# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
"""Targeted tests for the hidden-Altium control fixes.

No Altium is started.  Win32 calls are replaced by small fakes so the Job
Object, suspended launch, identity and state-root rules can be tested directly.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import runtime_state as rs
import spice_sifter
from batch_agent import run_batch, validate_report_contract
from hidden_instance import (
    CREATE_SUSPENDED,
    HIDDEN_LAUNCH_FAILED,
    HIDDEN_STATE_AMBIGUOUS,
    HIDDEN_STOP_FAILED,
    HiddenAltiumError,
    HiddenAltiumSession,
    LaunchedProcess,
    ProcessIdentity,
    ProcessJob,
    Win32Error,
    launch_on_desktop,
)
from spice_sifter import (
    TestError,
    _ensure_no_previous_hidden_session,
    run_test_file,
)

ROOT = Path(__file__).resolve().parents[1]
START = "2026-01-01T00:00:00Z"


class FakeProcess:
    def __init__(self, alive=True, exit_code=1):
        self._alive = alive
        self._exit_code = exit_code
        self.terminated = []
        self.waited = []
        self.closed = False

    def is_alive(self):
        return self._alive

    def wait(self, timeout):
        self.waited.append(timeout)
        self._alive = False
        return True

    def exit_code(self):
        return None if self._alive else self._exit_code

    def terminate(self, exit_code=1):
        self.terminated.append(exit_code)
        return True

    def close(self):
        self.closed = True


class StuckProcess(FakeProcess):
    def wait(self, timeout):
        self.waited.append(timeout)
        return False


class FakeJob:
    def __init__(self, usable=True, assign=True):
        self.handle = 1 if usable else None
        self.error = None if usable else "configuration failed"
        self.configured = usable
        self.assign_error = None if assign else "assignment failed"
        self.usable = usable
        self.assign_result = assign
        self.assigned = []
        self.terminated = False
        self.closed = False

    def assign(self, process):
        self.assigned.append(process)
        return self.assign_result

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


class FakeKernel32:
    """Minimal kernel32 surface used by launch_on_desktop/ProcessHandle."""

    def __init__(self, configured=True, assigned=True, resumed=True,
                 process_handle=0x100, thread_handle=0x200, pid=4242):
        self.configured = configured
        self.assigned = assigned
        self.resumed = resumed
        self.process_handle = process_handle
        self.thread_handle = thread_handle
        self.pid = pid
        self.flags = []
        self.resume_calls = []
        self.terminated = []
        self.closed = []
        self.job_created = 0

    def CreateProcessW(self, exe, command, pa, ta, inherit, flags, env, cwd,
                       startup, info):
        self.flags.append(flags)
        info._obj.hProcess = self.process_handle
        info._obj.hThread = self.thread_handle
        info._obj.dwProcessId = self.pid
        startup._obj.dwFlags = flags
        return 1

    def GetProcessTimes(self, handle, creation, exit_, kernel, user):
        ticks = 132_000_000_000_000_000
        creation._obj.dwLowDateTime = ticks & 0xFFFFFFFF
        creation._obj.dwHighDateTime = (ticks >> 32) & 0xFFFFFFFF
        return 1

    def CreateJobObjectW(self, attributes, name):
        self.job_created += 1
        return 0x500

    def SetInformationJobObject(self, handle, info_class, info, size):
        return 1 if self.configured else 0

    def AssignProcessToJobObject(self, job, process):
        return 1 if self.assigned else 0

    def ResumeThread(self, thread):
        self.resume_calls.append(thread)
        return 0 if self.resumed else 0xFFFFFFFF

    def TerminateProcess(self, process, code):
        self.terminated.append((process, code))
        return 1

    def TerminateJobObject(self, job, code):
        return 1

    def WaitForSingleObject(self, handle, timeout):
        return 0

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


class JobObjectTests(unittest.TestCase):
    def test_job_configuration_failure_makes_job_unusable(self):
        fake = FakeKernel32(configured=False)
        with patch("hidden_instance.kernel32", fake):
            job = ProcessJob()
        self.assertFalse(job.usable)
        self.assertFalse(job.configured)
        self.assertIn("SetInformationJobObject", job.error)
        self.assertEqual(job.handle, None)
        self.assertIn(0x500, fake.closed)

    def test_start_refuses_unconfigured_job_and_cleans_up(self):
        fake = FakeKernel32(configured=False)
        desktop = FakeDesktop()
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
            with patch("hidden_instance.kernel32", fake), \
                    patch("hidden_instance.Desktop.create", return_value=desktop), \
                    patch.object(HiddenAltiumSession, "_altium_snapshot",
                                 return_value=[]):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.start()
        self.assertEqual(ctx.exception.code, HIDDEN_LAUNCH_FAILED)
        self.assertIn("job_error", ctx.exception.diagnostics)
        self.assertTrue(desktop.closed)

    def test_assignment_failure_terminates_only_the_created_process(self):
        fake = FakeKernel32(assigned=False)
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            with patch("hidden_instance.kernel32", fake):
                with self.assertRaises(Win32Error) as ctx:
                    launch_on_desktop(str(exe), use_job=True)
        self.assertEqual(ctx.exception.api, "AssignProcessToJobObject")
        self.assertEqual(fake.resume_calls, [])
        self.assertEqual(fake.terminated, [(fake.process_handle, 1)])
        self.assertIn(fake.process_handle, fake.closed)
        self.assertIn(fake.thread_handle, fake.closed)

    def test_process_is_suspended_until_assigned_then_resumed(self):
        fake = FakeKernel32()
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            with patch("hidden_instance.kernel32", fake):
                launched = launch_on_desktop(str(exe), use_job=True)
                try:
                    self.assertTrue(launched.extra["job_assigned"])
                    self.assertTrue(launched.extra["suspended"])
                finally:
                    launched.close()
        self.assertEqual(fake.flags, [CREATE_SUSPENDED])
        self.assertEqual(fake.resume_calls, [fake.thread_handle])
        self.assertEqual(fake.terminated, [])

    def test_session_rejects_launch_without_job_assignment(self):
        process = FakeProcess(alive=True)
        launched = LaunchedProcess(
            process=process, desktop=None, command_line="x", cwd=".",
            started_at=START, job=FakeJob(), job_owned=False,
            extra={"job_assigned": False, "job_error": "access denied"},
        )
        desktop = FakeDesktop()
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
            with patch("hidden_instance.Desktop.create", return_value=desktop), \
                    patch("hidden_instance.ProcessJob", return_value=FakeJob()), \
                    patch("hidden_instance.launch_on_desktop",
                          return_value=launched), \
                    patch.object(HiddenAltiumSession, "_altium_snapshot",
                                 return_value=[]):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.start()
        self.assertEqual(ctx.exception.code, HIDDEN_LAUNCH_FAILED)
        self.assertEqual(process.terminated, [1])
        self.assertTrue(process.closed)
        self.assertTrue(desktop.closed)


class ThreadedLockTests(unittest.TestCase):
    def test_nested_from_owner_thread_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with rs.acquire_batch_lock(root):
                with rs.acquire_batch_lock(root):
                    self.assertTrue(rs.batch_lock_held(root))
                self.assertTrue(rs.batch_lock_held(root))
            self.assertIsNone(rs.batch_lock_owner(root))

    def test_other_thread_cannot_own_the_batch_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            errors = []
            entered = []

            def worker():
                try:
                    with rs.acquire_batch_lock(root):
                        entered.append(True)
                except Exception as exc:  # MarkerError
                    errors.append(exc)

            with rs.acquire_batch_lock(root):
                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(5)
                self.assertTrue(rs.batch_lock_held(root))
        self.assertEqual(entered, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], rs.MarkerError)

    def test_altium_session_from_other_thread_reports_busy(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            codes = []
            with rs.state_scope(root=root), rs.acquire_batch_lock(root):
                def worker():
                    with rs.state_scope(root=root):
                        try:
                            with spice_sifter.altium_session():
                                pass
                        except TestError as exc:
                            codes.append(exc.code)

                thread = threading.Thread(target=worker)
                thread.start()
                thread.join(5)
        self.assertEqual(codes, ["BUSY"])

    def test_lock_is_released_after_an_exception(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            with self.assertRaises(RuntimeError):
                with rs.acquire_batch_lock(root):
                    raise RuntimeError("boom")
            self.assertIsNone(rs.batch_lock_owner(root))
            with rs.acquire_batch_lock(root):
                self.assertTrue(rs.batch_lock_held(root))


class PreviousHiddenSessionTests(unittest.TestCase):
    def run_check(self, td, state, diagnostics=None):
        root = rs.state_root(td)
        root.mkdir(parents=True, exist_ok=True)
        assessment = {
            "state": state,
            "diagnostics": diagnostics or {"state": state},
            "session": {"session_id": "abc"},
        }
        with patch("runtime_state.resolve_pending",
                   return_value={"state": "NONE", "reason": ""}), \
                patch("runtime_state.assess_hidden_session",
                      return_value=assessment):
            return _ensure_no_previous_hidden_session(root), root

    def test_active_session_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(TestError) as ctx:
                self.run_check(td, "ACTIVE")
        self.assertEqual(ctx.exception.code, HIDDEN_STATE_AMBIGUOUS)
        self.assertIn("already running", ctx.exception.message)

    def test_unknown_session_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(TestError) as ctx:
                self.run_check(td, "UNKNOWN")
        self.assertEqual(ctx.exception.code, HIDDEN_STATE_AMBIGUOUS)
        self.assertIn("recover", ctx.exception.suggested_action)

    def test_stale_session_is_cleared(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            rs.write_hidden_session(root, {
                "session_id": "stale", "pid": 10, "creation_time": START,
            })
            _, root = self.run_check(td, "STALE")
            self.assertFalse(rs.hidden_session_path(root).exists())

    def test_missing_session_allows_start(self):
        with tempfile.TemporaryDirectory() as td:
            assessment, _ = self.run_check(td, "NONE")
        self.assertEqual(assessment["state"], "NONE")

    def test_single_run_does_not_start_when_previous_session_is_active(self):
        created = []

        class Session(FakeSession):
            def __init__(self, *args, **kwargs):
                created.append(True)
                super().__init__(*args, **kwargs)

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            with patch("hidden_instance.HiddenAltiumSession", Session), \
                    patch("runtime_state.resolve_pending",
                          return_value={"state": "NONE", "reason": ""}), \
                    patch("runtime_state.assess_hidden_session",
                          return_value={"state": "ACTIVE",
                                        "reason": "still running",
                                        "diagnostics": {}}):
                report = run_test_file(
                    ROOT / "examples" / "divider.json", altium=str(exe),
                    altium_instance="hidden", temp_dir=td,
                )
        self.assertEqual(created, [])
        self.assertEqual(report["error"]["code"], HIDDEN_STATE_AMBIGUOUS)


class StopFailureTests(unittest.TestCase):
    def make_session(self, td, process):
        exe = Path(td) / "X2.EXE"
        exe.write_text("stub")
        session = HiddenAltiumSession(str(exe), state_root=Path(td) / "state")
        session.identity = ProcessIdentity(100, START, 1)
        session.process = process
        session.job = FakeJob()
        session.desktop = FakeDesktop()
        session._started = True
        session._ready = True
        session._job_managed = True
        return session

    def test_failed_stop_raises_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as td:
            process = StuckProcess(alive=True)
            session = self.make_session(td, process)
            with patch("hidden_instance.process_still_ours", return_value=True):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.stop()
            path = rs.hidden_session_path(session.state_root)
            state = json.loads(path.read_text(encoding="utf-8-sig"))
        self.assertEqual(ctx.exception.code, HIDDEN_STOP_FAILED)
        self.assertTrue(session.stop_result["state_kept"])
        self.assertEqual(state["state"], "STOP_FAILED")
        self.assertTrue(session.desktop.closed)

    def test_ambiguous_stop_does_not_terminate_and_keeps_state(self):
        with tempfile.TemporaryDirectory() as td:
            process = FakeProcess(alive=True)
            session = self.make_session(td, process)
            job = session.job
            with patch("hidden_instance.process_still_ours", return_value=False):
                with self.assertRaises(HiddenAltiumError) as ctx:
                    session.stop()
            kept = rs.hidden_session_path(session.state_root).exists()
        self.assertEqual(ctx.exception.code, HIDDEN_STATE_AMBIGUOUS)
        self.assertFalse(job.terminated)
        self.assertTrue(kept)

    def test_successful_stop_removes_state(self):
        with tempfile.TemporaryDirectory() as td:
            process = FakeProcess(alive=True)
            session = self.make_session(td, process)
            with patch("hidden_instance.process_still_ours", return_value=True):
                result = session.stop()
            removed = not rs.hidden_session_path(session.state_root).exists()
        self.assertTrue(result["process_gone"])
        self.assertTrue(removed)

    def test_stop_error_turns_test_status_into_error_and_keeps_results(self):
        class FailingStop(FakeSession):
            def stop(self):
                self.stopped = True
                self.stop_result = {
                    "process_gone": False, "state_kept": True,
                    "reason": HIDDEN_STOP_FAILED,
                }
                raise HiddenAltiumError(
                    HIDDEN_STOP_FAILED, "still alive",
                    {"pid": 321, "process_gone": False},
                )

        def fake_execute(exe, data, base, timeout, keep, verbose, report,
                         max_runs, launcher=None):
            report.update(status="PASS", checks=[{
                "signal": "v(out)", "measurement": "last",
                "min": 2.49, "max": 2.51, "value": 2.5, "passed": True,
            }])

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            with patch("hidden_instance.HiddenAltiumSession", FailingStop), \
                    patch("spice_sifter.execute_test", side_effect=fake_execute), \
                    patch("spice_sifter._ensure_no_previous_hidden_session"):
                report = run_test_file(
                    ROOT / "examples" / "divider.json", altium=str(exe),
                    altium_instance="hidden", temp_dir=td,
                )
        self.assertEqual(report["status"], "ERROR")
        self.assertEqual(report["error"]["code"], HIDDEN_STOP_FAILED)
        self.assertEqual(report["results"][0]["value"], 2.5)
        self.assertEqual(report["altium_session_stop_error"]["code"],
                         HIDDEN_STOP_FAILED)
        self.assertIn("altium_session", report)
        ok, code, message = validate_report_contract(report)
        self.assertTrue(ok, (code, message))


class FakeSession:
    def __init__(self, *args, **kwargs):
        state_root = kwargs.get("state_root")
        if state_root is None and args:
            state_root = args[0]
        self.state_root = Path(state_root) if state_root else rs.current_state_root()
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
        return {"altium_pid": 111, "altium_start_time": START}

    def ensure_ready(self):
        return self.diagnostics()

    def run_script(self, script, proc_name, **kwargs):
        raise AssertionError("must not run scripts")


class StateRootTests(unittest.TestCase):
    def test_batch_hidden_uses_one_custom_state_root(self):
        captured = {}
        seen_roots = []

        class CapturingSession(FakeSession):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                captured["altium"] = args[0] if args else kwargs.get("altium")
                super().__init__(kwargs.get("state_root"))

        def runner(path, **kwargs):
            seen_roots.append(rs.current_state_root())
            return {
                "schema_version": "1.0", "status": "PASS",
                "validation_status": "NO_ASSERTIONS", "results": [],
                "coverage": {
                    "status": "OK", "expected": 0, "completed": 0,
                    "missing": 0, "passed": 0, "failed": 0,
                    "measured": 0, "errors": 0, "not_run": 0,
                },
                "error": None,
            }

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            root = rs.state_root(td)
            with patch("hidden_instance.HiddenAltiumSession",
                       CapturingSession):
                result = run_batch(
                    ["a"], altium=str(exe), runner=runner,
                    doctor=lambda: {
                        "ready": True, "mode": "hidden",
                        "altium": {"running": False},
                        "hidden_desktop": {"supported": True},
                        "hidden_instance": None,
                        "lock": {"available": True},
                        "pending_run": None,
                        "temporary_directory": {"writable": True},
                    },
                    altium_instance="hidden", temp_dir=td,
                )
            lock_exists = (root / "altium.lock").is_file()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(captured["state_root"], root)
        self.assertEqual(seen_roots, [root])
        self.assertTrue(lock_exists)

    def test_single_hidden_uses_one_custom_state_root(self):
        captured = {}

        class CapturingSession(FakeSession):
            def __init__(self, *args, **kwargs):
                captured.update(kwargs)
                super().__init__(kwargs.get("state_root"))

        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "X2.EXE"
            exe.write_text("stub")
            root = rs.state_root(td)
            with patch("hidden_instance.HiddenAltiumSession",
                       CapturingSession), \
                    patch("spice_sifter.execute_test"), \
                    patch("spice_sifter._ensure_no_previous_hidden_session"):
                report = run_test_file(
                    ROOT / "examples" / "divider.json", altium=str(exe),
                    altium_instance="hidden", temp_dir=td,
                )
            lock_exists = (root / "altium.lock").is_file()
        self.assertEqual(captured["state_root"], root)
        self.assertTrue(lock_exists)
        self.assertIn("altium_session", report)


class DoctorAfterStopErrorTests(unittest.TestCase):
    def write_session(self, root):
        rs.write_hidden_session(root, {
            "session_id": "stopfailed",
            "state": "STOP_FAILED",
            "pid": 777,
            "creation_time": START,
            "desktop": "SpiceSifterHidden-stopfailed",
        })

    def doctor(self, lookup):
        with tempfile.TemporaryDirectory() as local:
            root = rs.state_root(local)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            exe = Path(local) / "X2.EXE"
            exe.write_text("stub")
            with patch("runtime_state.file_version",
                       return_value="24.4.1.13"), \
                    patch("hidden_instance.probe_hidden_desktop",
                          return_value={"supported": True,
                                        "probe_created": True}):
                return rs.doctor_report(
                    str(exe), temp_dir=local, mode="hidden",
                    process_query=lambda query: [],
                    hidden_process_lookup=lookup,
                )

    def test_doctor_is_busy_while_process_identity_matches(self):
        alive = lambda pid: {"pid": pid, "start_time": "2026-01-01T00:00:00.000Z"}
        report = self.doctor(alive)
        self.assertEqual(report["status"], "BUSY")
        self.assertEqual(report["hidden_instance"]["state"], "ACTIVE")

    def test_recover_refuses_active_stop_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            alive = lambda pid: {
                "pid": pid, "start_time": "2026-01-01T00:00:00.000Z"
            }
            result = rs.recover_pending(td, alive)
            exists = rs.hidden_session_path(root).exists()
        self.assertEqual(result["status"], "REFUSED")
        self.assertTrue(exists)

    def test_recover_clears_stale_stop_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = rs.state_root(td)
            root.mkdir(parents=True, exist_ok=True)
            self.write_session(root)
            result = rs.recover_pending(td, lambda pid: None)
            exists = rs.hidden_session_path(root).exists()
        self.assertEqual(result["status"], "RECOVERED")
        self.assertTrue(result["removed_hidden_session"])
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
