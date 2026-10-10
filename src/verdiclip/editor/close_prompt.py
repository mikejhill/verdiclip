"""The question asked before closing an editor with work that would be lost."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Self, override

from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QStyleHintReturn,
    QStyleOption,
    QWidget,
)


class UnderlinedShortcuts(QProxyStyle):
    """The app's style, except access-key letters are always underlined.

    Windows hides them until Alt is pressed, which hides the S and N shortcuts
    exactly when they would help.
    """

    def __init__(self) -> None:
        super().__init__(QStyleFactory.create(QApplication.style().name()))

    @override
    def styleHint(
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        """Always underline shortcut letters; defer everything else."""
        if hint is QStyle.StyleHint.SH_UnderlineShortcut:
            return 1
        return super().styleHint(hint, option, widget, returnData)


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

    def build(self, parent: QWidget | None) -> QMessageBox:
        """Return the dialog, not yet shown: S saves, N discards, Esc cancels."""
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText(self.message)
        save = box.addButton("&Save", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Do&n't save", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        style = UnderlinedShortcuts()
        style.setParent(box)
        for button in (save, discard):
            button.setStyle(style)
        box.setDefaultButton(save)
        box.setEscapeButton(cancel)
        return box

    def ask(self, parent: QWidget | None) -> CloseChoice:
        """Show the prompt and return the answer."""
        box = self.build(parent)
        box.exec()
        # Closing with the title-bar X counts as the escape button (Cancel)
        role = box.buttonRole(box.clickedButton())
        if role is QMessageBox.ButtonRole.AcceptRole:
            return CloseChoice.SAVE
        if role is QMessageBox.ButtonRole.DestructiveRole:
            return CloseChoice.DISCARD
        return CloseChoice.CANCEL
