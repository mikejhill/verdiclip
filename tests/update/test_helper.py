"""Tests for the helper that installs an update after the app exits."""

from __future__ import annotations

import io
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from verdiclip import update as update_package
from verdiclip.update import helper

PYTHON = sys.executable


def command_line(argv: Sequence[str]) -> str:
    """Quote ``argv`` the way the platform's helper splits it."""
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def python_writing(path: Path, text: str) -> list[str]:
    """Return a command that writes ``text`` to ``path``."""
    code = f"open({path.as_posix()!r}, 'w').write({text!r})"
    return [PYTHON, "-c", code]


def exited_pid() -> int:
    """Return the process id of a process that has finished."""
    process = subprocess.Popen([PYTHON, "-c", "pass"])  # noqa: S603 - this Python
    process.wait()
    return process.pid


def wait_for_file(path: Path, timeout: float = 15.0) -> str:
    """Return ``path``'s text once it exists."""
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            msg = f"{path} never appeared"
            raise AssertionError(msg)
        time.sleep(0.05)
    time.sleep(0.05)  # Let the writer finish
    return path.read_text(encoding="utf-8")


def write_plan(tmp_path: Path, **fields: object) -> Path:
    """Write a plan for the helper and return its path."""
    plan: dict[str, object] = {
        "previous": "0.10.0",
        "target": "0.11.0",
        "steps": [],
        "relaunch": "",
        "verify": "",
        "pid": exited_pid(),
        "log": str(tmp_path / "update.log"),
        "result": str(tmp_path / "result.json"),
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan | fields), encoding="utf-8")
    return path


def result_of(tmp_path: Path) -> dict[str, object]:
    """Return the result the helper wrote."""
    raw: object = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return {str(key): value for key, value in raw.items()}


class TestHelperRun:
    """A whole run: wait, install, report, and start the app again."""

    def test_runs_steps_reports_version_and_relaunches(self, tmp_path: Path) -> None:
        """Every step runs in order, then the new version is read and reported."""
        first, second, started = tmp_path / "1", tmp_path / "2", tmp_path / "up"
        plan = write_plan(
            tmp_path,
            steps=[python_writing(first, "a"), python_writing(second, "b")],
            verify=PYTHON,
            relaunch=command_line(python_writing(started, "started")),
        )

        assert helper.main(["helper.py", str(plan)]) == 0

        result = result_of(tmp_path)
        assert result["ok"] is True
        assert result["installed"]  # The running checkout's version
        assert (result["previous"], result["target"]) == ("0.10.0", "0.11.0")
        assert result["log"] == str(tmp_path / "update.log")
        assert first.read_text(encoding="utf-8") == "a"
        assert second.read_text(encoding="utf-8") == "b"
        assert wait_for_file(started) == "started"
        log = (tmp_path / "update.log").read_text(encoding="utf-8")
        assert "update started" in log
        assert "Starting" in log

    def test_failed_step_stops_and_still_relaunches(self, tmp_path: Path) -> None:
        """A failing step skips the rest, and the old version starts again."""
        later, started = tmp_path / "later", tmp_path / "up"
        plan = write_plan(
            tmp_path,
            steps=[[PYTHON, "-c", "raise SystemExit(3)"], python_writing(later, "x")],
            verify=PYTHON,
            relaunch=command_line(python_writing(started, "started")),
        )

        assert helper.main(["helper.py", str(plan)]) == 1

        result = result_of(tmp_path)
        assert result["ok"] is False
        assert result["message"] == f"{Path(PYTHON).name} failed with exit code 3"
        assert "installed" not in result
        assert not later.exists()
        assert wait_for_file(started) == "started"

    def test_failed_step_starts_the_fallback(self, tmp_path: Path) -> None:
        """When a plan names a fallback (uvx), a failure starts that instead."""
        new, old = tmp_path / "new", tmp_path / "old"
        plan = write_plan(
            tmp_path,
            steps=[[PYTHON, "-c", "raise SystemExit(1)"]],
            relaunch=command_line(python_writing(new, "new")),
            fallback=command_line(python_writing(old, "old")),
        )

        assert helper.main(["helper.py", str(plan)]) == 1

        assert wait_for_file(old) == "old"
        assert not new.exists()

    def test_success_ignores_the_fallback(self, tmp_path: Path) -> None:
        """A good run starts the new version, not the fallback."""
        new, old = tmp_path / "new", tmp_path / "old"
        plan = write_plan(
            tmp_path,
            relaunch=command_line(python_writing(new, "new")),
            fallback=command_line(python_writing(old, "old")),
        )

        assert helper.main(["helper.py", str(plan)]) == 0

        assert wait_for_file(new) == "new"
        assert not old.exists()

    def test_missing_program_is_reported(self, tmp_path: Path) -> None:
        """A step whose program is gone fails with the reason."""
        missing = str(tmp_path / "no-such-uv")
        plan = write_plan(tmp_path, steps=[[missing, "sync"]])

        assert helper.main(["helper.py", str(plan)]) == 1

        message = str(result_of(tmp_path)["message"])
        assert message.startswith("no-such-uv could not run:")

    def test_app_that_keeps_running_is_left_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the app never exits, nothing is installed or started."""
        monkeypatch.setattr(helper, "EXIT_WAIT_S", 0.1)
        step, started = tmp_path / "step", tmp_path / "up"
        plan = write_plan(
            tmp_path,
            pid=os.getpid(),
            steps=[python_writing(step, "x")],
            relaunch=command_line(python_writing(started, "started")),
        )

        assert helper.main(["helper.py", str(plan)]) == 1

        assert result_of(tmp_path)["message"] == helper.STILL_RUNNING
        time.sleep(0.5)
        assert not step.exists()
        assert not started.exists()

    def test_uvx_plan_has_nothing_to_verify(self, tmp_path: Path) -> None:
        """Without an interpreter to ask, the installed version is empty."""
        plan = write_plan(tmp_path)

        assert helper.main(["helper.py", str(plan)]) == 0

        assert result_of(tmp_path)["installed"] == ""

    def test_plan_that_is_not_an_object_is_refused(self, tmp_path: Path) -> None:
        """A malformed plan does nothing."""
        plan = tmp_path / "plan.json"
        plan.write_text("[]", encoding="utf-8")

        assert helper.main(["helper.py", str(plan)]) == 2

        assert not (tmp_path / "result.json").exists()

    def test_runs_as_a_standalone_script(self, tmp_path: Path) -> None:
        """The app runs a copy with -I, away from the verdiclip package."""
        script = tmp_path / "copy" / "helper.py"
        script.parent.mkdir()
        source = Path(update_package.__file__).with_name("helper.py")
        script.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        plan = write_plan(tmp_path, steps=[[PYTHON, "-c", "pass"]])

        done = subprocess.run(  # noqa: S603 - this Python and a test copy
            [PYTHON, "-I", str(script), str(plan)], check=False, timeout=60
        )

        assert done.returncode == 0
        assert result_of(tmp_path)["ok"] is True


class TestHelperParts:
    """Edge cases of the individual steps."""

    def test_wait_for_exit_sees_a_finished_process(self) -> None:
        """A process that has exited needs no waiting."""
        assert helper.wait_for_exit(exited_pid(), 5.0)

    def test_wait_for_exit_times_out_on_a_live_process(self) -> None:
        """A live process is still running when the wait ends."""
        assert not helper.wait_for_exit(os.getpid(), 0.1)

    def test_wait_for_exit_returns_when_the_process_ends(self) -> None:
        """The wait ends as soon as the process does."""
        sleep = [PYTHON, "-c", "import time; time.sleep(0.5)"]
        process = subprocess.Popen(sleep)  # noqa: S603 - this Python
        # Reap it as it ends; on POSIX an unreaped child still looks alive
        reaper = threading.Thread(target=process.wait)
        reaper.start()
        try:
            assert helper.wait_for_exit(process.pid, 30.0)
        finally:
            reaper.join()

    def test_version_from_a_broken_interpreter_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An interpreter that can't report a version reports none."""
        monkeypatch.setattr(helper, "VERSION_CODE", "raise SystemExit('no verdiclip')")
        log = io.StringIO()

        assert helper.installed_version(PYTHON, log) == ""
        assert "no verdiclip" in log.getvalue()

    def test_version_from_a_missing_interpreter_is_empty(self, tmp_path: Path) -> None:
        """A missing interpreter is logged, not raised."""
        log = io.StringIO()

        assert helper.installed_version(str(tmp_path / "python"), log) == ""
        assert "Could not read the installed version" in log.getvalue()

    def test_relaunch_without_a_command_does_nothing(self) -> None:
        """No command, nothing started and nothing logged."""
        log = io.StringIO()

        helper.relaunch("", log)

        assert log.getvalue() == ""

    def test_relaunch_failure_is_logged(self, tmp_path: Path) -> None:
        """A launch command that can't start is logged."""
        log = io.StringIO()

        helper.relaunch(command_line([str(tmp_path / "missing.exe")]), log)

        assert "Could not start VerdiClip" in log.getvalue()
