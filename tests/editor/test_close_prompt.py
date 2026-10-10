"""The close prompt: wording, answers, keyboard shortcuts, and visual design."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QKeySequence, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QStyle,
    QStyleFactory,
    QStyleHintReturn,
    QStyleOption,
    QWidget,
)
from pytestqt.qtbot import QtBot

from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt, UnderlinedShortcuts
from verdiclip.settings import Theme
from verdiclip.shell.theme import ThemeManager

# Captured at import, before the suite-wide stub replaces it for each test
REAL_ASK = ClosePrompt.ask
MIN_TEXT_CONTRAST = 4.5  # WCAG AA for body text
ANSWER_TIMEOUT_MS = 3000


class HiddenShortcuts(QProxyStyle):
    """The same style with access-key underlines hidden, for comparison."""

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
        """Never underline shortcut letters."""
        if hint is QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, returnData)


@pytest.fixture
def real_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the real dialog run instead of the suite-wide automatic answer."""
    monkeypatch.setattr(ClosePrompt, "ask", REAL_ASK)


@pytest.fixture
def shown(qtbot: QtBot) -> Iterator[QMessageBox]:
    """The built prompt, shown non-modally and laid out."""
    box = ClosePrompt.describe(file_name=None, copied=False).build(None)
    qtbot.addWidget(box)
    box.show()
    qtbot.waitExposed(box)
    yield box
    box.close()


def buttons(box: QMessageBox) -> dict[str, QPushButton]:
    """Return the prompt's buttons by their visible label."""
    return {b.text().replace("&", ""): b for b in box.findChildren(QPushButton)}


def answer_when_open(action: Callable[[QMessageBox], object]) -> None:
    """Run ``action`` on the prompt as soon as it is open and focused.

    If the action fails to close it, the prompt is dismissed after a few
    seconds so the test fails instead of hanging.
    """

    def run() -> None:
        box = QApplication.activeModalWidget()
        assert isinstance(box, QMessageBox), "the close prompt should be open"
        box.activateWindow()
        QTest.qWaitForWindowActive(box)
        QTimer.singleShot(ANSWER_TIMEOUT_MS, box.reject)
        action(box)

    QTimer.singleShot(0, run)


class TestWording:
    """The message matches what would be lost (UX-G-06)."""

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

    def test_dialog_shows_the_message_and_title(self, shown: QMessageBox) -> None:
        """The built dialog carries the message and a plain title."""
        assert shown.text().startswith("This screenshot hasn't been saved")
        assert shown.windowTitle() == "Unsaved changes"
        assert shown.icon() is QMessageBox.Icon.Question


@pytest.mark.usefixtures("real_prompt")
class TestAnswers:
    """Every way of answering maps to the right choice (functional)."""

    @pytest.mark.parametrize(
        ("key", "mods", "expected"),
        [
            (Qt.Key.Key_S, Qt.KeyboardModifier.NoModifier, CloseChoice.SAVE),
            (Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier, CloseChoice.DISCARD),
            (Qt.Key.Key_S, Qt.KeyboardModifier.AltModifier, CloseChoice.SAVE),
            (Qt.Key.Key_N, Qt.KeyboardModifier.AltModifier, CloseChoice.DISCARD),
            (Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier, CloseChoice.CANCEL),
            (Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier, CloseChoice.SAVE),
        ],
    )
    def test_keys(
        self,
        qtbot: QtBot,
        key: Qt.Key,
        mods: Qt.KeyboardModifier,
        expected: CloseChoice,
    ) -> None:
        """S and N work alone or with Alt; Esc cancels; Enter takes Save."""
        del qtbot
        answer_when_open(lambda box: QTest.keyClick(box, key, mods))

        assert ClosePrompt("Save it?").ask(None) is expected

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("Save", CloseChoice.SAVE),
            ("Don't save", CloseChoice.DISCARD),
            ("Cancel", CloseChoice.CANCEL),
        ],
    )
    def test_clicks(self, qtbot: QtBot, label: str, expected: CloseChoice) -> None:
        """Clicking each button gives its answer."""
        del qtbot
        answer_when_open(
            lambda box: QTest.mouseClick(buttons(box)[label], Qt.MouseButton.LeftButton)
        )

        assert ClosePrompt("Save it?").ask(None) is expected

    def test_title_bar_close_cancels(self, qtbot: QtBot) -> None:
        """Closing the dialog window keeps the editor open."""
        del qtbot
        answer_when_open(lambda box: box.close())

        assert ClosePrompt("Save it?").ask(None) is CloseChoice.CANCEL


class TestDesign:
    """Layout, shortcuts, and legibility of the prompt (design)."""

    def test_buttons_read_save_dont_save_cancel(self, shown: QMessageBox) -> None:
        """Buttons appear left to right in the familiar Office order."""
        ordered = sorted(
            buttons(shown).items(), key=lambda item: item[1].mapToGlobal(QPoint()).x()
        )

        assert [label for label, _ in ordered] == ["Save", "Don't save", "Cancel"]

    def test_save_is_default_and_cancel_is_escape(self, shown: QMessageBox) -> None:
        """Enter saves (the safe choice) and Esc backs out."""
        by_label = buttons(shown)

        assert shown.defaultButton() is by_label["Save"]
        assert shown.escapeButton() is by_label["Cancel"]

    def test_access_keys_are_s_and_n(self, shown: QMessageBox) -> None:
        """The underlined letters are the ones the keyboard answers to."""
        by_label = buttons(shown)

        assert by_label["Save"].shortcut() == QKeySequence("Alt+S")
        assert by_label["Don't save"].shortcut() == QKeySequence("Alt+N")

    def test_access_keys_are_always_underlined(self, shown: QMessageBox) -> None:
        """Underlines show without holding Alt, unlike the Windows default."""
        underline = QStyle.StyleHint.SH_UnderlineShortcut
        for label in ("Save", "Don't save"):
            button = buttons(shown)[label]
            assert button.style().styleHint(underline, None, button) == 1

    def test_underline_is_actually_painted(self, qtbot: QtBot) -> None:
        """The prompt's button style visibly underlines the access key.

        Compared with the same style but underlines hidden, the only change
        is a few pixels low on the button, where an underline goes.
        """
        shown_style, hidden_style = UnderlinedShortcuts(), HiddenShortcuts()
        images: list[QImage] = []
        for style in (shown_style, hidden_style):
            button = QPushButton("&Save")
            qtbot.addWidget(button)
            button.setStyle(style)
            button.resize(90, 28)
            images.append(button.grab().toImage())

        rows = self._changed_rows(images[0], images[1])

        assert rows, "the underline should change some pixels"
        assert min(rows) > 28 / 2
        assert len(rows) <= 3, "only an underline, not a restyled button"

    def test_text_is_readable(self, shown: QMessageBox) -> None:
        """Message and button text contrast with their backgrounds (WCAG AA)."""
        palette = shown.palette()
        window = TestDesign._contrast(
            palette.color(QPalette.ColorRole.WindowText),
            palette.color(QPalette.ColorRole.Window),
        )
        button = TestDesign._contrast(
            palette.color(QPalette.ColorRole.ButtonText),
            palette.color(QPalette.ColorRole.Button),
        )

        assert window >= MIN_TEXT_CONTRAST
        assert button >= MIN_TEXT_CONTRAST

    @pytest.mark.parametrize("theme", [Theme.LIGHT, Theme.DARK])
    def test_prompt_builds_in_each_theme(self, qtbot: QtBot, theme: Theme) -> None:
        """Switching themes never breaks the prompt's shortcuts or underlines."""
        ThemeManager.apply(theme)
        try:
            box = ClosePrompt("Save it?").build(None)
            qtbot.addWidget(box)
            save = buttons(box)["Save"]
            underline = QStyle.StyleHint.SH_UnderlineShortcut
            assert save.style().styleHint(underline, None, save) == 1
        finally:
            ThemeManager.apply(Theme.SYSTEM)

    def test_message_is_not_clipped(self, shown: QMessageBox) -> None:
        """Long messages wrap inside the dialog instead of being cut off."""
        long = ClosePrompt.describe(
            file_name="A very long screenshot file name 2026-10-10 at 02.35.18.png",
            copied=False,
        )
        shown.setText(long.message)
        shown.adjustSize()
        QApplication.processEvents()

        assert shown.width() <= shown.screen().availableGeometry().width()
        assert shown.sizeHint().width() <= shown.width()

    @staticmethod
    def _changed_rows(a: QImage, b: QImage) -> list[int]:
        """Return the rows where two same-sized images differ."""
        height = min(a.height(), b.height())
        width = min(a.width(), b.width())
        return [
            y
            for y in range(height)
            if any(a.pixelColor(x, y) != b.pixelColor(x, y) for x in range(width))
        ]

    @staticmethod
    def _contrast(fg: QColor, bg: QColor) -> float:
        """Return the WCAG contrast ratio between two colors."""

        def luminance(color: QColor) -> float:
            def channel(value: float) -> float:
                return (
                    value / 12.92
                    if value <= 0.03928
                    else math.pow((value + 0.055) / 1.055, 2.4)
                )

            r, g, b = (
                channel(c) for c in (color.redF(), color.greenF(), color.blueF())
            )
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        lighter, darker = sorted((luminance(fg), luminance(bg)), reverse=True)
        return (lighter + 0.05) / (darker + 0.05)
