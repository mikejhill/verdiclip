"""The close prompt's wording and its keyboard shortcuts."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from pytestqt.qtbot import QtBot

from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt


class TestWording:
    """The message matches what would be lost."""

    def test_never_delivered_screenshot(self) -> None:
        """A fresh capture says it was never saved or copied."""
        prompt = ClosePrompt.describe(file_name=None, copied=False)

        assert "hasn't been saved or copied" in prompt.message

    def test_copied_screenshot_with_new_edits(self) -> None:
        """After a copy, the message talks about changes since then."""
        prompt = ClosePrompt.describe(file_name=None, copied=True)

        assert "changed since you copied it" in prompt.message

    def test_file_names_the_file_even_if_copied(self) -> None:
        """A file on disk is what the user expects to keep up to date."""
        prompt = ClosePrompt.describe(file_name="shot.png", copied=True)

        assert prompt.message == "Save changes to “shot.png” before closing?"


class TestKeyboard:
    """UX-G-06: S saves, N discards, Esc cancels, Enter takes the default (Save)."""

    @pytest.fixture(autouse=True)
    def real_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Undo the suite-wide stub so the real dialog runs."""
        monkeypatch.undo()

    @staticmethod
    def answer_with(key: Qt.Key, seen: list[list[str]]) -> None:
        """Press ``key`` in the prompt once it opens, recording its buttons."""

        def press() -> None:
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox), "the close prompt should be open"
            seen.append([b.text() for b in box.findChildren(QPushButton)])
            QTest.keyClick(box, key)

        QTimer.singleShot(0, press)

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            (Qt.Key.Key_S, CloseChoice.SAVE),
            (Qt.Key.Key_N, CloseChoice.DISCARD),
            (Qt.Key.Key_Escape, CloseChoice.CANCEL),
            (Qt.Key.Key_Return, CloseChoice.SAVE),
        ],
    )
    def test_single_keys_answer(
        self, qtbot: QtBot, key: Qt.Key, expected: CloseChoice
    ) -> None:
        """Each answer is one key press, shown as an underlined letter."""
        del qtbot
        seen: list[list[str]] = []
        self.answer_with(key, seen)

        answer = ClosePrompt("Save it?").ask(None)

        assert answer is expected
        assert {"&Save", "Do&n't save"} <= set(seen[0])
