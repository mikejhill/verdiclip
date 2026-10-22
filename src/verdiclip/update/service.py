"""Check for newer releases, hand one to the helper, and read back the outcome."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from importlib import resources
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, Signal

from verdiclip import APP_NAME
from verdiclip.exceptions import UpdateError
from verdiclip.update.feed import ReleaseFeed, ReleaseVersion
from verdiclip.update.install import NO_WINDOW, Installation, UpdatePlan, UpdatePlanner

logger = logging.getLogger(__name__)

HELPER_SCRIPT: Final = "helper.py"
_CREATE_NEW_PROCESS_GROUP: Final = 0x00000200

type Spawn = Callable[[Sequence[str], Path], None]


@dataclass(frozen=True, slots=True)
class UpdateReport:
    """What the helper wrote after its last run."""

    ok: bool
    previous: str
    target: str
    installed: str = ""
    message: str = ""
    log: str = ""

    @property
    def reached_target(self) -> bool:
        """Return whether the install moved past ``previous``.

        A run that succeeded without changing the version (a pinned uv tool,
        say) didn't update anything; uvx reports no version.
        """
        if not self.ok:
            return False
        return not self.installed or ReleaseVersion.is_newer(
            self.installed, self.previous
        )

    def describe(self) -> tuple[str, str]:
        """Return the title and message that tell the user how it went."""
        if self.reached_target:
            return (
                "VerdiClip updated",
                f"Updated from {self.previous} to {self.installed or self.target}.",
            )
        if self.ok:
            return (
                "VerdiClip wasn't updated",
                f"The update finished, but {APP_NAME} is still {self.previous}. "
                f"Details: {self.log}",
            )
        return (
            "VerdiClip update failed",
            f"{self.message}. {APP_NAME} is still {self.previous}. Details: {self.log}",
        )

    @classmethod
    def read(cls, path: Path) -> UpdateReport | None:
        """Return the report in ``path``, or None if it's missing or unreadable."""
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return cls(
            ok=raw.get("ok") is True,
            previous=str(raw.get("previous", "")),
            target=str(raw.get("target", "")),
            installed=str(raw.get("installed", "")),
            message=str(raw.get("message", "")),
            log=str(raw.get("log", "")),
        )


def spawn_detached(argv: Sequence[str], cwd: Path) -> None:
    """Start ``argv`` with no window, in its own process group or session."""
    windows = sys.platform == "win32"
    subprocess.Popen(  # noqa: S603 - the planned helper and interpreter
        list(argv),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW | (_CREATE_NEW_PROCESS_GROUP if windows else 0),
        start_new_session=not windows,
        close_fds=True,
    )


def helper_interpreter(executable: Path, base_prefix: Path) -> Path:
    """Return a Python outside the environment that's about to change.

    The base installation's ``pythonw.exe`` (or ``python.exe``) runs the
    helper; with neither, the running interpreter has to do.
    """
    for name in ("pythonw.exe", "python.exe"):
        candidate = base_prefix / name
        if candidate.is_file():
            return candidate
    return executable


class UpdateLauncher:
    """Write the helper and its plan to ``folder``, and start it."""

    def __init__(self, folder: Path, spawn: Spawn = spawn_detached) -> None:
        self._folder = folder
        self._spawn = spawn

    @property
    def folder(self) -> Path:
        """Return the folder holding the helper, plan, log, and result."""
        return self._folder

    @property
    def result_path(self) -> Path:
        """Return where the helper writes its result."""
        return self._folder / "result.json"

    @property
    def _shown_path(self) -> Path:
        """Return the marker that the latest result was already shown."""
        return self._folder / "result.shown"

    def start(self, plan: UpdatePlan) -> None:
        """Start the helper, which waits for this process to exit.

        Raises:
            UpdateError: If the files can't be written or the helper can't start.
        """
        script = self._folder / HELPER_SCRIPT
        plan_path = self._folder / "plan.json"
        payload = asdict(plan) | {
            "pid": os.getpid(),
            "log": str(self._folder / "update.log"),
            "result": str(self.result_path),
        }
        source = resources.files("verdiclip.update").joinpath(HELPER_SCRIPT)
        python = helper_interpreter(Path(sys.executable), Path(sys.base_prefix))
        try:
            self._folder.mkdir(parents=True, exist_ok=True)
            script.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            plan_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._shown_path.unlink(missing_ok=True)
            self._spawn([str(python), "-I", str(script), str(plan_path)], self._folder)
        except OSError as err:
            msg = f"Could not start the updater: {err}"
            raise UpdateError(msg) from err

    def last_report(self) -> UpdateReport | None:
        """Return the latest result, shown or not."""
        return UpdateReport.read(self.result_path)

    def take_report(self) -> UpdateReport | None:
        """Return the latest result once; later calls return None."""
        if self._shown_path.exists():
            return None
        report = self.last_report()
        if report is not None:
            try:
                self._shown_path.touch()
            except OSError as err:
                logger.warning("Could not mark the update result shown: %s", err)
        return report


class Updater(QObject):
    """Track the latest release and start installing it."""

    checked = Signal(str)  # The latest release, newer or not
    check_failed = Signal(str)

    def __init__(
        self,
        installation: Installation,
        feed: ReleaseFeed,
        launcher: UpdateLauncher,
        planner: UpdatePlanner | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._installation = installation
        self._feed = feed
        self._launcher = launcher
        self._planner = planner or UpdatePlanner()
        self._latest: str | None = None
        feed.fetched.connect(self._on_fetched)
        feed.failed.connect(self._on_failed)

    @property
    def installation(self) -> Installation:
        """Return the running install."""
        return self._installation

    @property
    def available(self) -> str | None:
        """Return the newer release found by the last check, if any."""
        latest = self._latest
        if latest is None or not ReleaseVersion.is_newer(
            latest, self._installation.version
        ):
            return None
        return latest

    def check(self) -> None:
        """Ask for the latest release; ``checked`` or ``check_failed`` follows."""
        self._feed.fetch()

    def prepare(self, relaunch: str) -> UpdatePlan:
        """Return the plan that installs the available release.

        Raises:
            UpdateError: If no newer release is known, or this install can't
                update itself.
        """
        target = self.available
        if target is None:
            msg = "No newer release is known"
            raise UpdateError(msg)
        return self._planner.plan(self._installation, target, relaunch)

    def start(self, plan: UpdatePlan) -> None:
        """Start the helper; the caller then quits.

        Raises:
            UpdateError: If the helper can't start.
        """
        self._launcher.start(plan)
        logger.info("Updating %s from %s to %s", APP_NAME, plan.previous, plan.target)

    def take_report(self) -> UpdateReport | None:
        """Return the last update's outcome if it hasn't been shown yet."""
        return self._launcher.take_report()

    def failed_before(self, version: str) -> bool:
        """Return whether the last attempt at ``version`` didn't get there."""
        report = self._launcher.last_report()
        return (
            report is not None
            and report.target == version
            and not report.reached_target
        )

    def _on_fetched(self, version: str) -> None:
        """Remember and announce the latest release."""
        self._latest = version
        logger.info("Latest %s release: %s", APP_NAME, version)
        self.checked.emit(version)

    def _on_failed(self, reason: str) -> None:
        """Pass on why the check failed."""
        logger.info("Could not check for updates: %s", reason)
        self.check_failed.emit(reason)
