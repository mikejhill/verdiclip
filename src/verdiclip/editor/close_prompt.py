"""The question asked before closing an editor with work that would be lost."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from PySide6.QtWidgets import QMessageBox, QWidget


class CloseChoice(StrEnum):
    """How the user answered the close prompt."""

    SAVE = "save"
    DISCARD = "discard"
    CANCEL = "cancel"


@dataclass(frozen=True, slots=True)
class ClosePrompt:
    """Wording that matches the image's history, plus the dialog that asks it."""

    message: str

    @classmethod
    def describe(cls, *, file_name: str | None, copied: bool) -> Self:
        """Return the prompt for an image with undelivered changes (UX-G-06).

        Args:
            file_name: The file the image was opened from or last saved to.
            copied: Whether an earlier state was copied to the clipboard.
        """
        if file_name is not None:
            return cls(f"Save changes to “{file_name}” before closing?")
        if copied:
            return cls(
                "This screenshot has changed since you copied it. "
                "Save it before closing?"
            )
        return cls(
            "This screenshot hasn't been saved or copied. Save it before closing?"
        )

    def ask(self, parent: QWidget | None) -> CloseChoice:
        """Show the prompt; S saves, N discards, Esc cancels."""
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText(self.message)
        save = box.addButton("&Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Do&n't save", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return CloseChoice.SAVE
        if clicked is discard:
            return CloseChoice.DISCARD
        return CloseChoice.CANCEL
