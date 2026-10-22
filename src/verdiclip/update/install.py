"""Tell how VerdiClip was installed, and plan the commands that update it.

Each kind of install updates its own way:

- a git checkout run from its ``.venv`` (``uv sync``): pull the release
  branch, then sync the environment;
- ``uv tool install verdiclip``: ``uv tool upgrade``;
- ``uvx verdiclip`` (a throwaway environment in uv's cache): nothing to
  install, start ``verdiclip@latest`` instead;
- ``pip`` or ``uv pip`` into an environment: upgrade the package there.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import Distribution, PackageNotFoundError, distribution
from pathlib import Path
from typing import Final
from urllib.parse import urlparse
from urllib.request import url2pathname

from verdiclip.exceptions import UpdateError

PACKAGE: Final = "verdiclip"
RELEASE_BRANCH: Final = "main"
GIT_TIMEOUT_S: Final = 20
NO_WINDOW: Final = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class InstallKind(StrEnum):
    """How the running VerdiClip was installed."""

    CHECKOUT = "checkout"
    UV_TOOL = "uv tool"
    UVX = "uvx"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Installation:
    """The running install: its kind, release, and where it lives."""

    kind: InstallKind
    version: str
    executable: Path
    root: Path | None = None  # A checkout's working tree
    installer: str = ""  # The INSTALLER record: "pip" or "uv"

    @classmethod
    def current(cls) -> Installation:
        """Return the install this process runs from."""
        executable = Path(sys.executable)
        try:
            dist = distribution(PACKAGE)
        except PackageNotFoundError:
            return cls(InstallKind.UNKNOWN, "0.0.0", executable)
        return cls.detect(dist, executable, Path(sys.prefix))

    @classmethod
    def detect(cls, dist: Distribution, executable: Path, prefix: Path) -> Installation:
        """Classify the install of ``dist`` run by ``executable`` from ``prefix``."""
        version = dist.version
        root = cls._editable_root(dist)
        if root is not None:
            kind = (
                InstallKind.CHECKOUT
                if (root / ".git").exists()
                else InstallKind.UNKNOWN
            )
            return cls(kind, version, executable, root)
        if (prefix / "uv-receipt.toml").is_file():
            return cls(InstallKind.UV_TOOL, version, executable)
        # uvx builds its environments under the cache's archive-vN folder
        if any(part.startswith("archive-v") for part in prefix.parts):
            return cls(InstallKind.UVX, version, executable)
        installer = (dist.read_text("INSTALLER") or "").strip().lower()
        if installer in {"pip", "uv"}:
            return cls(
                InstallKind.ENVIRONMENT, version, executable, installer=installer
            )
        return cls(InstallKind.UNKNOWN, version, executable)

    @staticmethod
    def _editable_root(dist: Distribution) -> Path | None:
        """Return the source tree of an editable install, else None."""
        text = dist.read_text("direct_url.json")
        if text is None:
            return None
        try:
            info: object = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(info, dict):
            return None
        dir_info, url = info.get("dir_info"), info.get("url")
        if not (isinstance(dir_info, dict) and dir_info.get("editable") is True):
            return None
        if not isinstance(url, str) or not url.startswith("file:"):
            return None
        return Path(url2pathname(urlparse(url).path))


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """What the helper runs once VerdiClip has exited."""

    previous: str
    target: str
    steps: tuple[tuple[str, ...], ...]
    relaunch: str  # Command line that starts the tray app again
    verify: str = ""  # Interpreter that reports the version afterwards
    fallback: str = ""  # Starts the old version if a step fails ("" = relaunch)


type Which = Callable[[str], str | None]


class UpdatePlanner:
    """Turn an install and a target release into an ``UpdatePlan``."""

    def __init__(self, which: Which = shutil.which, home: Path | None = None) -> None:
        self._which = which
        self._home = home if home is not None else Path.home()

    def plan(self, install: Installation, target: str, relaunch: str) -> UpdatePlan:
        """Return the plan, or raise if this install can't update itself.

        Raises:
            UpdateError: If a needed tool is missing, a checkout isn't on the
                release branch or has local changes, or the install is unknown.
        """
        steps: tuple[tuple[str, ...], ...]
        verify, fallback = str(install.executable), ""
        match install.kind:
            case InstallKind.CHECKOUT if install.root is not None:
                git, uv = self._tool("git"), self._uv()
                self._check_checkout(git, install.root)
                root = str(install.root)
                steps = (
                    (git, "-C", root, "pull", "--ff-only"),
                    (uv, "sync", "--project", root),
                )
            case InstallKind.UV_TOOL:
                steps = ((self._uv(), "tool", "upgrade", PACKAGE),)
            case InstallKind.UVX:
                # Fetch the release into uv's cache as a step, so a failure is
                # logged and the old version starts instead; the relaunch then
                # finds it cached
                spec = ("tool", "run", "--from", f"{PACKAGE}=={target}")
                uv = self._uv()
                steps = ((uv, *spec, "verdiclip", "--version"),)
                verify, fallback = "", relaunch
                relaunch = subprocess.list2cmdline([uv, *spec, "verdiclip-gui"])
            case InstallKind.ENVIRONMENT:
                steps = (self._environment_step(install),)
            case _:
                msg = (
                    "VerdiClip can't tell how it was installed, "
                    "so update it the way you installed it"
                )
                raise UpdateError(msg)
        return UpdatePlan(install.version, target, steps, relaunch, verify, fallback)

    def _environment_step(self, install: Installation) -> tuple[str, ...]:
        """Upgrade with the installer that put VerdiClip in its environment."""
        python = str(install.executable)
        uv = self._which("uv") if install.installer == "uv" else None
        if uv is not None:
            return (uv, "pip", "install", "--python", python, "--upgrade", PACKAGE)
        return (python, "-m", "pip", "install", "--upgrade", PACKAGE)

    def _uv(self) -> str:
        """Return uv, also looking where its installer puts it."""
        found = self._which("uv")
        if found is not None:
            return found
        for name in ("uv.exe", "uv"):
            candidate = self._home / ".local" / "bin" / name
            if candidate.is_file():
                return str(candidate)
        msg = "uv is needed to update VerdiClip, and it isn't on PATH"
        raise UpdateError(msg)

    def _tool(self, name: str) -> str:
        """Return the path of program ``name`` on PATH."""
        found = self._which(name)
        if found is None:
            msg = f"{name} is needed to update VerdiClip, and it isn't on PATH"
            raise UpdateError(msg)
        return found

    @classmethod
    def _check_checkout(cls, git: str, root: Path) -> None:
        """Refuse a checkout off the release branch or with local changes.

        ``git pull --ff-only`` would refuse too, but only after VerdiClip had
        exited for the update.
        """
        branch = cls._git(git, root, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if branch != RELEASE_BRANCH:
            msg = f"The checkout at {root} is on {branch}, not {RELEASE_BRANCH}"
            raise UpdateError(msg)
        changed = cls._git(
            git, root, "status", "--porcelain", "--untracked-files=no"
        ).rstrip()
        if changed:
            files = [line[3:] for line in changed.splitlines()]
            shown = ", ".join(files[:3]) + (" …" if len(files) > 3 else "")  # noqa: PLR2004
            msg = f"The checkout at {root} has local changes: {shown}"
            raise UpdateError(msg)

    @staticmethod
    def _git(git: str, root: Path, *args: str) -> str:
        """Run git in ``root`` and return its output."""
        try:
            done = subprocess.run(  # noqa: S603 - git from PATH, fixed arguments
                [git, "-C", str(root), *args],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                check=True,
                creationflags=NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError) as err:
            msg = f"Could not read the checkout at {root}: {err}"
            raise UpdateError(msg) from err
        return done.stdout
