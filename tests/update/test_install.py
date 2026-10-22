"""Tests for telling install kinds apart and planning their updates."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib.metadata import Distribution, PackageNotFoundError, PathDistribution
from pathlib import Path

import pytest

from verdiclip.exceptions import UpdateError
from verdiclip.update import install as install_module
from verdiclip.update.install import (
    Installation,
    InstallKind,
    UpdatePlanner,
)

PYTHON = Path("C:/env/Scripts/python.exe")
RELAUNCH = '"C:/env/Scripts/pythonw.exe" -m verdiclip'
TOOLS = {"git": "C:/Git/git.exe", "uv": "C:/uv/uv.exe"}


def make_dist(
    folder: Path,
    *,
    direct_url: object | None = None,
    installer: str | None = None,
) -> Distribution:
    """Write a verdiclip 0.10.0 dist-info into ``folder`` and return it."""
    info = folder / "verdiclip-0.10.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: verdiclip\nVersion: 0.10.0\n", encoding="utf-8"
    )
    if direct_url is not None:
        text = direct_url if isinstance(direct_url, str) else json.dumps(direct_url)
        (info / "direct_url.json").write_text(text, encoding="utf-8")
    if installer is not None:
        (info / "INSTALLER").write_text(f"{installer}\n", encoding="utf-8")
    return PathDistribution(info)


def editable(root: Path) -> dict[str, object]:
    """Return the direct_url.json of an editable install of ``root``."""
    return {"url": root.as_uri(), "dir_info": {"editable": True}}


def git(root: Path, *args: str) -> None:
    """Run git in ``root`` as a throwaway identity."""
    subprocess.run(  # noqa: S603 - git with fixed arguments
        [
            shutil.which("git") or "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """Return a git working tree on main with one committed file."""
    if shutil.which("git") is None:
        pytest.skip("needs git")
    root = tmp_path / "verdiclip"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "init")
    return root


def planner(
    tools: dict[str, str] | None = None, home: Path | None = None
) -> UpdatePlanner:
    """Return a planner that finds ``tools`` (default: git and uv)."""
    found = TOOLS if tools is None else tools
    return UpdatePlanner(found.get, home=home or Path("/nowhere"))


def checkout_planner() -> tuple[UpdatePlanner, str]:
    """Return a planner that runs the real git, and that git's path."""
    real_git = shutil.which("git") or "git"
    return planner({"git": real_git, "uv": TOOLS["uv"]}), real_git


class TestDetect:
    """Each install kind is recognized from its metadata and environment."""

    def test_editable_git_tree_is_a_checkout(self, tmp_path: Path) -> None:
        """Uv sync in a checkout: an editable install of a git working tree."""
        root = tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        dist = make_dist(tmp_path / "site", direct_url=editable(root))

        found = Installation.detect(dist, PYTHON, tmp_path / ".venv")

        assert found == Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, root)

    def test_editable_tree_without_git_is_unknown(self, tmp_path: Path) -> None:
        """A source tree that isn't a git checkout can't be pulled."""
        root = tmp_path / "src"
        root.mkdir()
        dist = make_dist(tmp_path / "site", direct_url=editable(root))

        assert Installation.detect(dist, PYTHON, tmp_path).kind is InstallKind.UNKNOWN

    def test_uv_receipt_is_a_uv_tool(self, tmp_path: Path) -> None:
        """Uv tool install leaves a receipt at the environment's root."""
        prefix = tmp_path / "tools" / "verdiclip"
        prefix.mkdir(parents=True)
        (prefix / "uv-receipt.toml").write_text("[tool]\n", encoding="utf-8")
        dist = make_dist(tmp_path / "site", installer="uv")

        assert Installation.detect(dist, PYTHON, prefix).kind is InstallKind.UV_TOOL

    def test_uv_cache_environment_is_uvx(self, tmp_path: Path) -> None:
        """Uvx runs from a throwaway environment in uv's cache."""
        prefix = tmp_path / "uv" / "cache" / "archive-v0" / "abc123"
        dist = make_dist(tmp_path / "site", installer="uv")

        assert Installation.detect(dist, PYTHON, prefix).kind is InstallKind.UVX

    @pytest.mark.parametrize("installer", ["pip", "uv", "PIP"])
    def test_pip_or_uv_pip_is_an_environment(
        self, tmp_path: Path, installer: str
    ) -> None:
        """Pip and uv pip installs remember their installer."""
        dist = make_dist(tmp_path / "site", installer=installer)

        found = Installation.detect(dist, PYTHON, tmp_path / "venv")

        assert found.kind is InstallKind.ENVIRONMENT
        assert found.installer == installer.lower()

    @pytest.mark.parametrize("installer", [None, "conda"])
    def test_other_installers_are_unknown(
        self, tmp_path: Path, installer: str | None
    ) -> None:
        """An installer VerdiClip can't drive leaves the kind unknown."""
        dist = make_dist(tmp_path / "site", installer=installer)

        assert Installation.detect(dist, PYTHON, tmp_path).kind is InstallKind.UNKNOWN

    @pytest.mark.parametrize(
        "direct_url",
        [
            "not json",
            [],
            {"url": "file:///src", "dir_info": {}},
            {"url": "file:///src", "dir_info": []},
            {"url": "https://example.com/v.whl", "dir_info": {"editable": True}},
            {"url": 5, "dir_info": {"editable": True}},
        ],
    )
    def test_non_editable_records_are_not_checkouts(
        self, tmp_path: Path, direct_url: object
    ) -> None:
        """Only an editable file: install points at a source tree."""
        dist = make_dist(tmp_path / "site", direct_url=direct_url, installer="pip")

        assert (
            Installation.detect(dist, PYTHON, tmp_path).kind is InstallKind.ENVIRONMENT
        )

    def test_current_without_metadata_is_unknown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running from bare source (no dist-info) can't be updated."""

        def missing(name: str) -> Distribution:
            raise PackageNotFoundError(name)

        monkeypatch.setattr(install_module, "distribution", missing)

        found = Installation.current()

        assert (found.kind, found.version) == (InstallKind.UNKNOWN, "0.0.0")

    def test_current_describes_this_process(self) -> None:
        """The running tests come from a checkout's uv sync."""
        found = Installation.current()

        assert found.kind is InstallKind.CHECKOUT
        assert found.root is not None
        assert (found.root / "pyproject.toml").is_file()


class TestPlan:
    """The commands each install kind runs."""

    def test_checkout_pulls_then_syncs(self, checkout: Path) -> None:
        """A clean checkout on main fast-forwards, then syncs its environment."""
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, checkout)

        checking, real_git = checkout_planner()

        plan = checking.plan(install, "0.11.0", RELAUNCH)

        root = str(checkout)
        assert plan.steps == (
            (real_git, "-C", root, "pull", "--ff-only"),
            (TOOLS["uv"], "sync", "--project", root),
        )
        assert (plan.previous, plan.target) == ("0.10.0", "0.11.0")
        assert plan.relaunch == RELAUNCH
        assert plan.verify == str(PYTHON)

    def test_checkout_on_another_branch_is_refused(self, checkout: Path) -> None:
        """A branch checked out for development isn't updated."""
        git(checkout, "switch", "-c", "feature")
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, checkout)
        real = checkout_planner()[0]

        with pytest.raises(UpdateError, match="is on feature, not main"):
            real.plan(install, "0.11.0", RELAUNCH)

    def test_checkout_with_local_changes_is_refused(self, checkout: Path) -> None:
        """Edits to tracked files block the pull, and are named."""
        for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
            (checkout / name).write_text("x", encoding="utf-8")
        git(checkout, "add", ".")
        git(checkout, "commit", "-m", "files")
        for name in ("pyproject.toml", "a.txt", "b.txt", "c.txt"):
            (checkout / name).write_text("changed", encoding="utf-8")
        (checkout / "untracked.txt").write_text("new", encoding="utf-8")
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, checkout)
        real = checkout_planner()[0]

        with pytest.raises(UpdateError, match="has local changes") as raised:
            real.plan(install, "0.11.0", RELAUNCH)

        message = str(raised.value)
        assert "a.txt, b.txt, c.txt …" in message
        assert "untracked" not in message

    def test_untracked_files_do_not_block(self, checkout: Path) -> None:
        """Files git doesn't track can't conflict with a fast-forward."""
        (checkout / "notes.txt").write_text("mine", encoding="utf-8")
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, checkout)

        assert checkout_planner()[0].plan(install, "0.11.0", RELAUNCH).steps

    def test_unreadable_checkout_is_refused(self, tmp_path: Path) -> None:
        """A folder git can't read is reported, not crashed on."""
        if shutil.which("git") is None:
            pytest.skip("needs git")
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, tmp_path)
        real = checkout_planner()[0]

        with pytest.raises(UpdateError, match="Could not read the checkout"):
            real.plan(install, "0.11.0", RELAUNCH)

    def test_checkout_needs_git(self, tmp_path: Path) -> None:
        """Without git there's no pull."""
        install = Installation(InstallKind.CHECKOUT, "0.10.0", PYTHON, tmp_path)

        with pytest.raises(UpdateError, match="git is needed"):
            planner({"uv": TOOLS["uv"]}).plan(install, "0.11.0", RELAUNCH)

    def test_uv_tool_upgrades(self) -> None:
        """Uv tool install upgrades in place."""
        install = Installation(InstallKind.UV_TOOL, "0.10.0", PYTHON)

        plan = planner().plan(install, "0.11.0", RELAUNCH)

        assert plan.steps == ((TOOLS["uv"], "tool", "upgrade", "verdiclip"),)
        assert plan.relaunch == RELAUNCH
        assert plan.verify == str(PYTHON)

    def test_uv_found_where_its_installer_puts_it(self, tmp_path: Path) -> None:
        """Uv off PATH is still found in ~/.local/bin."""
        uv = tmp_path / ".local" / "bin" / "uv"
        uv.parent.mkdir(parents=True)
        uv.write_text("", encoding="utf-8")
        install = Installation(InstallKind.UV_TOOL, "0.10.0", PYTHON)

        plan = planner({}, home=tmp_path).plan(install, "0.11.0", RELAUNCH)

        assert plan.steps[0][0] == str(uv)

    def test_uv_tool_needs_uv(self, tmp_path: Path) -> None:
        """Without uv anywhere, the update is refused."""
        install = Installation(InstallKind.UV_TOOL, "0.10.0", PYTHON)

        with pytest.raises(UpdateError, match="uv is needed"):
            planner({}, home=tmp_path).plan(install, "0.11.0", RELAUNCH)

    def test_uvx_relaunches_latest(self) -> None:
        """Uvx has nothing to install; it starts verdiclip@latest instead."""
        install = Installation(InstallKind.UVX, "0.10.0", PYTHON)

        plan = planner().plan(install, "0.11.0", RELAUNCH)

        assert plan.steps == ()
        assert plan.relaunch == subprocess.list2cmdline(
            [TOOLS["uv"], "tool", "run", "--from", "verdiclip@latest", "verdiclip-gui"]
        )
        assert plan.verify == ""

    def test_pip_environment_upgrades_with_pip(self) -> None:
        """Pip installs upgrade through the environment's own pip."""
        install = Installation(
            InstallKind.ENVIRONMENT, "0.10.0", PYTHON, installer="pip"
        )

        plan = planner().plan(install, "0.11.0", RELAUNCH)

        assert plan.steps == (
            (str(PYTHON), "-m", "pip", "install", "--upgrade", "verdiclip"),
        )

    def test_uv_pip_environment_upgrades_with_uv(self) -> None:
        """Uv pip installs (often without pip) upgrade through uv."""
        install = Installation(
            InstallKind.ENVIRONMENT, "0.10.0", PYTHON, installer="uv"
        )

        plan = planner().plan(install, "0.11.0", RELAUNCH)

        assert plan.steps == (
            (
                TOOLS["uv"],
                "pip",
                "install",
                "--python",
                str(PYTHON),
                "--upgrade",
                "verdiclip",
            ),
        )

    def test_uv_pip_without_uv_falls_back_to_pip(self) -> None:
        """If uv has gone, pip is the next best thing."""
        install = Installation(
            InstallKind.ENVIRONMENT, "0.10.0", PYTHON, installer="uv"
        )

        plan = planner({}).plan(install, "0.11.0", RELAUNCH)

        assert plan.steps[0][:3] == (str(PYTHON), "-m", "pip")

    def test_unknown_install_is_refused(self) -> None:
        """An install VerdiClip can't classify updates the way it was made."""
        install = Installation(InstallKind.UNKNOWN, "0.10.0", PYTHON)

        with pytest.raises(UpdateError, match="update it the way you installed it"):
            planner().plan(install, "0.11.0", RELAUNCH)
