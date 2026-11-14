"""Install the AI agent skill from Settings > General."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from verdiclip.automation.skills import Installed, SkillScope
from verdiclip.settings import Settings
from verdiclip.shell.settings_dialog import SettingsDialog
from verdiclip.shell.skill_dialog import SkillDialog


class Recorder:
    """Stand in for the installer: record calls, or fail."""

    def __init__(self, tmp_path: Path, error: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], SkillScope, Path | None]] = []
        self._root = tmp_path
        self._error = error

    def __call__(
        self, agents: list[str], scope: SkillScope, project: Path | None
    ) -> list[Installed]:
        """Record the request and report one file per agent."""
        self.calls.append((agents, scope, project))
        if self._error is not None:
            raise self._error
        return [
            Installed(a, self._root / a / "SKILL.md", replaced=a == "codex")
            for a in agents
        ]


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    """An installer that succeeds."""
    return Recorder(tmp_path)


@pytest.fixture
def dialog(qtbot: QtBot, recorder: Recorder) -> Iterator[SkillDialog]:
    """The skill dialog over ``recorder``."""
    created = SkillDialog(installer=recorder)
    qtbot.addWidget(created)
    yield created
    created.reject()


def test_installs_for_claude_in_every_project_by_default(
    dialog: SkillDialog, recorder: Recorder, qtbot: QtBot, tmp_path: Path
) -> None:
    """Claude Code is checked and user scope chosen; Install reports the files."""
    dialog.agent_box("codex").setChecked(True)

    qtbot.mouseClick(dialog.install_button, Qt.MouseButton.LeftButton)

    assert recorder.calls == [(["claude", "codex"], SkillScope.USER, None)]
    assert dialog.status.splitlines() == [
        f"Claude Code: installed {tmp_path / 'claude' / 'SKILL.md'}",
        f"Codex: updated {tmp_path / 'codex' / 'SKILL.md'}",
    ]


def test_project_scope_needs_an_existing_folder(
    dialog: SkillDialog, recorder: Recorder, tmp_path: Path
) -> None:
    """One project folder must be chosen and exist before Install is enabled."""
    dialog.project_button.setChecked(True)
    empty = dialog.install_button.isEnabled(), dialog.status
    dialog.folder_edit.setText(str(tmp_path / "missing"))
    missing = dialog.install_button.isEnabled(), dialog.status
    dialog.folder_edit.setText(str(tmp_path))

    assert empty == (False, "Choose the project folder.")
    assert missing == (False, "That project folder doesn't exist.")
    assert dialog.install_button.isEnabled()
    assert dialog.status == ""
    assert dialog.install()
    assert recorder.calls == [(["claude"], SkillScope.PROJECT, tmp_path)]


def test_needs_an_agent(dialog: SkillDialog) -> None:
    """With no agent checked, Install is off and the dialog says why."""
    dialog.agent_box("claude").setChecked(False)

    assert not dialog.install_button.isEnabled()
    assert dialog.status == "Choose at least one agent."


def test_reports_write_failures(qtbot: QtBot, tmp_path: Path) -> None:
    """A folder that can't be written is reported in the dialog."""
    failing = SkillDialog(installer=Recorder(tmp_path, PermissionError("denied")))
    qtbot.addWidget(failing)

    assert not failing.install()
    assert failing.status == "Could not install the skill: denied"


def test_writes_the_real_skill(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default installer writes the shipped skill into the project."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    real = SkillDialog()
    qtbot.addWidget(real)
    real.project_button.setChecked(True)
    real.folder_edit.setText(str(tmp_path))

    assert real.install()
    written = tmp_path / ".claude" / "skills" / "verdiclip" / "SKILL.md"
    assert "verdiclip ctl --help" in written.read_text(encoding="utf-8")


def test_settings_general_tab_opens_the_installer(qtbot: QtBot) -> None:
    """Settings > General has a button that opens the skill installer once."""
    settings = SettingsDialog(Settings())
    qtbot.addWidget(settings)

    assert settings.skill_dialog is None
    settings.install_skill_button.click()
    first = settings.skill_dialog
    settings.install_skill_button.click()

    assert first is not None
    assert settings.skill_dialog is first
    assert first.isVisible()
    first.reject()
    settings.reject()
