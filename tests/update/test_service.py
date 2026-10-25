"""Tests for the updater: reports, the helper launch, and release tracking."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import override

import pytest
from pytestqt.qtbot import QtBot
from tests.update.test_helper import python_writing, wait_for_file

from verdiclip.exceptions import UpdateError
from verdiclip.update.feed import ReleaseFeed
from verdiclip.update.install import (
    Installation,
    InstallKind,
    UpdatePlan,
    UpdatePlanner,
)
from verdiclip.update.service import (
    UpdateLauncher,
    Updater,
    UpdateReport,
    helper_interpreter,
    spawn_detached,
)

PYTHON = Path("C:/tools/verdiclip/Scripts/python.exe")
PLAN = UpdatePlan("0.10.0", "0.11.0", (("uv", "tool", "upgrade", "verdiclip"),), "run")


class FakeFeed(ReleaseFeed):
    """A feed that answers with ``latest``, or fails with ``error``."""

    def __init__(self, latest: str = "0.11.0", error: str = "") -> None:
        super().__init__("file:///unused")
        self.latest = latest
        self.error = error
        self.fetches = 0
        self.deferred = False

    @override
    def fetch(self) -> None:
        """Answer at once."""
        self.fetches += 1
        if self.deferred:
            return
        if self.error:
            self.failed.emit(self.error)
        else:
            self.fetched.emit(self.latest)


class Spawns:
    """Record what the launcher would start."""

    def __init__(self, error: OSError | None = None) -> None:
        self.calls: list[tuple[list[str], Path]] = []
        self.error = error

    def __call__(self, argv: Sequence[str], cwd: Path) -> None:
        """Record the launch, or fail."""
        if self.error is not None:
            raise self.error
        self.calls.append((list(argv), cwd))


def write_result(folder: Path, **fields: object) -> None:
    """Write a helper result into ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(fields), encoding="utf-8")


class TestUpdateReport:
    """What the restarted app says about the last update."""

    def test_success_names_both_versions(self) -> None:
        """A finished update says where it went from and to."""
        report = UpdateReport(
            ok=True, previous="0.10.0", target="0.11.0", installed="0.11.0"
        )

        assert report.reached_target
        assert report.describe() == (
            "VerdiClip updated",
            "Updated from 0.10.0 to 0.11.0.",
        )

    def test_uvx_success_names_the_target(self) -> None:
        """Uvx reports no version, so the target stands in."""
        report = UpdateReport(ok=True, previous="0.10.0", target="0.11.0")

        assert report.reached_target
        assert report.describe()[1] == "Updated from 0.10.0 to 0.11.0."

    def test_success_without_a_new_version_is_not_an_update(self) -> None:
        """A run that left the version alone (a pinned tool) says so."""
        report = UpdateReport(
            ok=True, previous="0.10.0", target="0.11.0", installed="0.10.0", log="L"
        )

        assert not report.reached_target
        title, message = report.describe()
        assert title == "VerdiClip wasn't updated"
        assert "still 0.10.0" in message
        assert message.endswith("Details: L")

    def test_failure_gives_the_reason_and_log(self) -> None:
        """A failed update names why and where the details are."""
        report = UpdateReport(
            ok=False, previous="0.10.0", target="0.11.0", message="uv failed", log="L"
        )

        assert not report.reached_target
        assert report.describe() == (
            "VerdiClip update failed",
            "uv failed. VerdiClip is still 0.10.0. Details: L",
        )

    def test_read_parses_the_helper_result(self, tmp_path: Path) -> None:
        """Every field comes from the result file."""
        write_result(
            tmp_path,
            ok=True,
            previous="0.10.0",
            target="0.11.0",
            installed="0.11.0",
            message="",
            log="update.log",
        )

        assert UpdateReport.read(tmp_path / "result.json") == UpdateReport(
            ok=True,
            previous="0.10.0",
            target="0.11.0",
            installed="0.11.0",
            log="update.log",
        )

    def test_read_treats_only_true_as_ok(self, tmp_path: Path) -> None:
        """A truthy but non-boolean ok is not success."""
        write_result(tmp_path, ok="yes")

        report = UpdateReport.read(tmp_path / "result.json")

        assert report == UpdateReport(ok=False, previous="", target="")

    @pytest.mark.parametrize("text", [None, "not json", "[]", "\udcff"])
    def test_unreadable_results_are_none(
        self, tmp_path: Path, text: str | None
    ) -> None:
        """A missing or malformed result is no report."""
        path = tmp_path / "result.json"
        if text is not None:
            path.write_bytes(text.encode("utf-8", errors="surrogateescape"))

        assert UpdateReport.read(path) is None


class TestHelperInterpreter:
    """The helper runs on a Python outside the environment being updated."""

    def test_prefers_base_pythonw(self, tmp_path: Path) -> None:
        """The base installation's pythonw.exe opens no console."""
        for name in ("pythonw.exe", "python.exe"):
            (tmp_path / name).write_text("", encoding="utf-8")

        assert helper_interpreter(PYTHON, tmp_path) == tmp_path / "pythonw.exe"

    def test_falls_back_to_base_python(self, tmp_path: Path) -> None:
        """Without pythonw.exe, the base python.exe."""
        (tmp_path / "python.exe").write_text("", encoding="utf-8")

        assert helper_interpreter(PYTHON, tmp_path) == tmp_path / "python.exe"

    def test_falls_back_to_the_running_interpreter(self, tmp_path: Path) -> None:
        """With no base executables at all, the running one."""
        assert helper_interpreter(PYTHON, tmp_path) == PYTHON


class TestUpdateLauncher:
    """Writing the helper and plan, starting it, and reading its result."""

    def test_start_writes_helper_and_plan_then_spawns(self, tmp_path: Path) -> None:
        """The plan carries this process id and where to log and report."""
        spawns = Spawns()
        folder = tmp_path / "update"
        launcher = UpdateLauncher(folder, spawns)

        launcher.start(PLAN)

        plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
        assert plan == {
            "previous": "0.10.0",
            "target": "0.11.0",
            "steps": [["uv", "tool", "upgrade", "verdiclip"]],
            "relaunch": "run",
            "verify": "",
            "fallback": "",
            "pid": os.getpid(),
            "log": str(folder / "update.log"),
            "result": str(folder / "result.json"),
        }
        script = folder / "helper.py"
        assert "def main(argv" in script.read_text(encoding="utf-8")
        python = helper_interpreter(Path(sys.executable), Path(sys.base_prefix))
        assert spawns.calls == [
            ([str(python), "-I", str(script), str(folder / "plan.json")], folder)
        ]

    def test_start_failure_is_an_update_error(self, tmp_path: Path) -> None:
        """A helper that can't start is reported, not raised raw."""
        launcher = UpdateLauncher(tmp_path, Spawns(OSError("denied")))

        with pytest.raises(UpdateError, match="Could not start the updater: denied"):
            launcher.start(PLAN)

    def test_take_report_returns_each_result_once(self, tmp_path: Path) -> None:
        """The result shows once; a new update shows its own."""
        launcher = UpdateLauncher(tmp_path, Spawns())
        write_result(tmp_path, ok=True, previous="0.10.0", target="0.11.0")

        first = launcher.take_report()

        assert first is not None
        assert first.target == "0.11.0"
        assert launcher.take_report() is None
        assert launcher.last_report() == first
        launcher.start(PLAN)
        write_result(tmp_path, ok=False, previous="0.11.0", target="0.12.0")
        second = launcher.take_report()
        assert second is not None
        assert second.target == "0.12.0"

    def test_no_result_no_report(self, tmp_path: Path) -> None:
        """Before any update there's nothing to report."""
        launcher = UpdateLauncher(tmp_path, Spawns())

        assert launcher.take_report() is None
        assert launcher.last_report() is None

    def test_unmarkable_report_is_still_returned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the shown marker can't be written, the report still shows."""
        write_result(tmp_path, ok=True, previous="0.10.0", target="0.11.0")

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise OSError("read-only")

        monkeypatch.setattr(Path, "touch", refuse)

        assert UpdateLauncher(tmp_path, Spawns()).take_report() is not None

    def test_spawn_detached_starts_a_process(self, tmp_path: Path) -> None:
        """The real spawn runs the command in the given folder."""
        marker = tmp_path / "ran"

        spawn_detached(python_writing(marker, "yes"), tmp_path)

        assert wait_for_file(marker) == "yes"


def make_updater(
    tmp_path: Path, feed: FakeFeed | None = None, spawns: Spawns | None = None
) -> Updater:
    """Return an updater for a uv tool install of 0.10.0."""
    install = Installation(InstallKind.UV_TOOL, "0.10.0", PYTHON)
    planner = UpdatePlanner({"uv": "C:/uv/uv.exe"}.get, home=tmp_path)
    launcher = UpdateLauncher(tmp_path / "update", spawns or Spawns())
    return Updater(install, feed or FakeFeed(), launcher, planner)


class TestUpdater:
    """Tracking the latest release and handing it to the helper."""

    @pytest.mark.parametrize(
        ("kind", "version"),
        [(InstallKind.UNKNOWN, "0.0.0"), (InstallKind.CHECKOUT, "0.12.0b1")],
    )
    def test_unsupported_check_fails_without_contacting_feed(
        self,
        qtbot: QtBot,
        tmp_path: Path,
        kind: InstallKind,
        version: str,
    ) -> None:
        """Callers bypassing the tray still cannot compare development builds."""
        feed = FakeFeed()
        updater = Updater(
            Installation(kind, version, PYTHON),
            feed,
            UpdateLauncher(tmp_path / "update"),
        )
        with qtbot.waitSignal(updater.check_failed) as failure:
            updater.check()
        assert "development" in failure.args[0]
        assert updater.available is None, "Unsupported checks must not offer an update"
        assert feed.fetches == 0, "Do not contact PyPI for an incomparable build"

    def test_check_finds_a_newer_release(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A newer release becomes available."""
        updater = make_updater(tmp_path)
        assert updater.available is None

        with qtbot.waitSignal(updater.checked) as signal:
            updater.check()

        assert signal.args == ["0.11.0"]
        assert updater.available == "0.11.0"
        assert updater.installation.version == "0.10.0"

    @pytest.mark.parametrize("latest", ["0.10.0", "0.9.0", "garbage"])
    def test_same_or_older_is_not_available(
        self, qtbot: QtBot, tmp_path: Path, latest: str
    ) -> None:
        """Only a newer release is offered."""
        updater = make_updater(tmp_path, FakeFeed(latest))

        with qtbot.waitSignal(updater.checked):
            updater.check()

        assert updater.available is None

    def test_failed_check_is_passed_on(self, qtbot: QtBot, tmp_path: Path) -> None:
        """A check that can't reach PyPI says why."""
        updater = make_updater(tmp_path, FakeFeed(error="offline"))

        with qtbot.waitSignal(updater.check_failed) as signal:
            updater.check()

        assert signal.args == ["offline"]

    def test_prepare_needs_a_known_release(self, tmp_path: Path) -> None:
        """Nothing to install before a check finds something."""
        with pytest.raises(UpdateError, match="No newer release"):
            make_updater(tmp_path).prepare("run")

    def test_prepare_and_start_hand_the_plan_over(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """The plan targets the available release and the helper gets it."""
        spawns = Spawns()
        updater = make_updater(tmp_path, spawns=spawns)
        with qtbot.waitSignal(updater.checked):
            updater.check()

        plan = updater.prepare("run")
        updater.start(plan)

        assert plan == UpdatePlan(
            "0.10.0",
            "0.11.0",
            (("C:/uv/uv.exe", "tool", "upgrade", "verdiclip"),),
            "run",
            str(PYTHON),
            "",
        )
        assert len(spawns.calls) == 1

    def test_failed_before_only_for_the_same_unreached_target(
        self, tmp_path: Path
    ) -> None:
        """An attempt that didn't get there blocks retrying that release."""
        updater = make_updater(tmp_path)
        folder = tmp_path / "update"
        assert not updater.failed_before("0.11.0")

        write_result(folder, ok=False, previous="0.10.0", target="0.11.0")
        assert updater.failed_before("0.11.0")
        assert not updater.failed_before("0.12.0")

        write_result(
            folder, ok=True, previous="0.10.0", target="0.11.0", installed="0.11.0"
        )
        assert not updater.failed_before("0.11.0")

    def test_take_report_comes_from_the_launcher(self, tmp_path: Path) -> None:
        """The updater passes on the helper's result once."""
        updater = make_updater(tmp_path)
        write_result(tmp_path / "update", ok=True, previous="0.9.0", target="0.10.0")

        report = updater.take_report()

        assert report is not None
        assert report.previous == "0.9.0"
        assert updater.take_report() is None
