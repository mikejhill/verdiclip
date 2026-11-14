"""Install the VerdiClip skill for AI coding agents from the app (Settings)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from verdiclip.automation.skills import AGENTS, Installed, SkillScope, install_skill

AGENT_LABELS: Final = {
    "claude": "Claude Code",
    "codex": "Codex",
    "copilot": "GitHub Copilot",
    "agents": "Other agents that read .agents/skills",
}
ERROR_STYLE: Final = "color: #c62828;"

type Installer = Callable[[list[str], SkillScope, Path | None], list[Installed]]


def _install(
    agents: list[str], scope: SkillScope, project: Path | None
) -> list[Installed]:
    return install_skill(agents, scope=scope, project=project)


class SkillDialog(QDialog):
    """Pick agents and where, then write the skill and say where it went."""

    def __init__(
        self, parent: QWidget | None = None, *, installer: Installer = _install
    ) -> None:
        super().__init__(parent)
        self._installer = installer
        self.setWindowTitle("Install AI agent skill")
        intro = QLabel(
            "The skill teaches AI coding agents to use the verdiclip ctl command: "
            "take screenshots, and open, annotate, save and close images in "
            "VerdiClip. Install it again after updating VerdiClip to refresh it."
        )
        intro.setWordWrap(True)
        self._agents = {name: QCheckBox(AGENT_LABELS[name]) for name in AGENTS}
        self._agents["claude"].setChecked(True)
        self._user = QRadioButton("For all my projects")
        self._project = QRadioButton("For one project folder:")
        self._user.setChecked(True)
        self._folder = QLineEdit()
        self._folder.setAccessibleName("Project folder")
        self._browse = QPushButton("Browse…")
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._install_button = self._buttons.addButton(
            "&Install", QDialogButtonBox.ButtonRole.ActionRole
        )
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        for box in self._agents.values():
            box.toggled.connect(self._validate)
            layout.addWidget(box)
        layout.addWidget(self._user)
        folder = QHBoxLayout()
        folder.addWidget(self._project)
        folder.addWidget(self._folder, 1)
        folder.addWidget(self._browse)
        layout.addLayout(folder)
        layout.addWidget(self._status)
        layout.addWidget(self._buttons)
        self._project.toggled.connect(self._validate)
        self._folder.textChanged.connect(self._validate)
        self._browse.clicked.connect(self._choose_folder)
        self._install_button.clicked.connect(self.install)
        self._buttons.rejected.connect(self.reject)
        self._validate()

    # For tests and the settings dialog

    def agent_box(self, name: str) -> QCheckBox:
        """Return the checkbox of agent ``name``."""
        return self._agents[name]

    @property
    def project_button(self) -> QRadioButton:
        """Return the "one project folder" choice."""
        return self._project

    @property
    def folder_edit(self) -> QLineEdit:
        """Return the project folder field."""
        return self._folder

    @property
    def install_button(self) -> QPushButton:
        """Return the Install button."""
        return self._install_button

    @property
    def status(self) -> str:
        """Return the result or problem shown under the choices."""
        return self._status.text()

    def install(self) -> bool:
        """Write the skill for the checked agents; report where or why not."""
        agents = [name for name, box in self._agents.items() if box.isChecked()]
        project = self._project.isChecked()
        scope = SkillScope.PROJECT if project else SkillScope.USER
        folder = Path(self._folder.text().strip()) if project else None
        try:
            written = self._installer(agents, scope, folder)
        except (OSError, ValueError) as err:
            self._show(f"Could not install the skill: {err}", error=True)
            return False
        lines = [
            f"{AGENT_LABELS[w.agent]}: {'updated' if w.replaced else 'installed'} "
            f"{w.path}"
            for w in written
        ]
        self._show("\n".join(lines), error=False)
        return True

    def _validate(self) -> None:
        """Enable Install once an agent and, for a project, a folder are chosen."""
        project = self._project.isChecked()
        self._folder.setEnabled(project)
        self._browse.setEnabled(project)
        folder = self._folder.text().strip()
        problem = ""
        if not any(box.isChecked() for box in self._agents.values()):
            problem = "Choose at least one agent."
        elif project and not folder:
            problem = "Choose the project folder."
        elif project and not Path(folder).is_dir():
            problem = "That project folder doesn't exist."
        self._install_button.setEnabled(not problem)
        if problem:
            self._show(problem, error=True)
        elif self._status.styleSheet():
            self._show("", error=False)

    def _show(self, text: str, *, error: bool) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(ERROR_STYLE if error else "")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Project folder", self._folder.text()
        )
        if chosen:
            self._folder.setText(chosen)
