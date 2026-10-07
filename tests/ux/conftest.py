"""User-journey driver: real mouse and keyboard input, observable assertions only."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QToolButton
from pytestqt.qtbot import QtBot

from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorWindow
from verdiclip.geometry import Point
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings

type Modifiers = Qt.KeyboardModifier

NO_MODS = Qt.KeyboardModifier.NoModifier
DRAG_STEPS = 6


class Stopwatch:
    """Measure elapsed wall time in milliseconds."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        """Milliseconds since construction."""
        return (time.perf_counter() - self._start) * 1000


class User:
    """Drives an editor window the way a person would, and saves step snapshots."""

    def __init__(
        self, qtbot: QtBot, window: EditorWindow, snapshots: Path | None, name: str
    ) -> None:
        self._qtbot = qtbot
        self._window = window
        self._snapshots = snapshots
        self._name = re.sub(r"[^A-Za-z0-9_]+", "_", name)
        self._step = 0

    @property
    def window(self) -> EditorWindow:
        """Return the editor window under test."""
        return self._window

    # Pointer

    def view_point(self, x: float, y: float) -> QPoint:
        """Return the viewport position of image pixel (x, y)."""
        pos = self._window.canvas.to_view(Point(x, y))
        return QPoint(round(pos.x()), round(pos.y()))

    def drag(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        mods: Modifiers = NO_MODS,
    ) -> None:
        """Press at ``start``, move in steps, and release at ``end`` (image pixels)."""
        viewport = self._window.canvas.viewport()
        QTest.mousePress(
            viewport, Qt.MouseButton.LeftButton, mods, self.view_point(*start)
        )
        for i in range(1, DRAG_STEPS + 1):
            t = i / DRAG_STEPS
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            QTest.mouseMove(viewport, self.view_point(x, y))
        QTest.mouseRelease(
            viewport, Qt.MouseButton.LeftButton, mods, self.view_point(*end)
        )

    def click(self, at: tuple[float, float], mods: Modifiers = NO_MODS) -> None:
        """Left-click image pixel ``at``."""
        QTest.mouseClick(
            self._window.canvas.viewport(),
            Qt.MouseButton.LeftButton,
            mods,
            self.view_point(*at),
        )

    def double_click(self, at: tuple[float, float]) -> None:
        """Double-click image pixel ``at``."""
        QTest.mouseDClick(
            self._window.canvas.viewport(),
            Qt.MouseButton.LeftButton,
            NO_MODS,
            self.view_point(*at),
        )

    # Keyboard

    def key(self, key: Qt.Key, mods: Modifiers = NO_MODS) -> None:
        """Press and release ``key`` on whatever has focus (shortcuts fire)."""
        target = self._window.focusWidget() or self._window.canvas
        QTest.keyClick(target, key, mods)

    def type_text(self, text: str) -> None:
        """Type ``text`` into the focused widget."""
        target = self._window.focusWidget() or self._window.canvas
        QTest.keyClicks(target, text)

    def shortcut(self, sequence: str) -> None:
        """Trigger a shortcut such as ``Ctrl+Shift+C`` via real key events."""
        parts = sequence.split("+")
        mods = NO_MODS
        names = {
            "Ctrl": Qt.KeyboardModifier.ControlModifier,
            "Shift": Qt.KeyboardModifier.ShiftModifier,
            "Alt": Qt.KeyboardModifier.AltModifier,
        }
        for part in parts[:-1]:
            mods |= names[part]
        keys = {
            "=": Qt.Key.Key_Equal,
            "-": Qt.Key.Key_Minus,
            "[": Qt.Key.Key_BracketLeft,
            "]": Qt.Key.Key_BracketRight,
            "Delete": Qt.Key.Key_Delete,
            "Enter": Qt.Key.Key_Return,
            "Esc": Qt.Key.Key_Escape,
        }
        last = parts[-1]
        key = keys.get(last) or Qt.Key(ord(last.upper()))
        self.key(key, mods)

    # Observation

    def status_text(self) -> str:
        """Return all text currently shown in the status bar."""
        bar = self._window.statusBar()
        texts = [w.text() for w in bar.findChildren(QLabel)]
        texts += [w.text() for w in bar.findChildren(QToolButton)]
        return " | ".join([bar.currentMessage(), *texts])

    def screen_pixels(self) -> QImage:
        """Return what the canvas currently shows."""
        return self._window.canvas.viewport().grab().toImage()

    def snap(self, step: str) -> None:
        """Save a screenshot of the window for human review when enabled."""
        self._step += 1
        if self._snapshots is None:
            return
        QApplication.processEvents()  # Let deferred toolbar layouts settle
        name = (
            f"{self._name}__{self._step:02d}_{re.sub(r'[^A-Za-z0-9]+', '-', step)}.png"
        )
        self._window.grab().save(str(self._snapshots / name))


class Pixels:
    """Pixel helpers for assertions."""

    @staticmethod
    def at(image: QImage, x: int, y: int) -> QColor:
        """Return the color at (x, y)."""
        return image.pixelColor(x, y)

    @staticmethod
    def is_reddish(color: QColor) -> bool:
        """True for a strong red."""
        return color.red() > 180 and color.green() < 90 and color.blue() < 90

    @staticmethod
    def is_white(color: QColor) -> bool:
        """True for near-white."""
        return min(color.red(), color.green(), color.blue()) > 240


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer "Discard" to unsaved-change prompts so teardown never blocks."""
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Discard,
    )


@pytest.fixture
def clipboard(qapp: QApplication) -> Iterator[Callable[[], QImage]]:
    """Return a reader for the clipboard image; clears the clipboard around the test."""
    del qapp
    board = QGuiApplication.clipboard()
    board.clear()
    yield board.image
    board.clear()


@pytest.fixture
def open_editor(
    qtbot: QtBot,
    output_settings: OutputSettings,
    snapshot_dir: Path | None,
    request: pytest.FixtureRequest,
) -> Callable[[QImage], User]:
    """Return a factory that opens a visible, focused editor and its User driver."""

    def factory(image: QImage) -> User:
        session = EditorSession(Document(image), EditorSettings())
        window = EditorWindow(session, ImageDelivery(output_settings))
        qtbot.addWidget(window)
        window.resize(900, 700)
        window.show()
        qtbot.waitExposed(window)
        window.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is window)
        window.canvas.setFocus()
        return User(qtbot, window, snapshot_dir, request.node.name)

    return factory


@pytest.fixture
def user(open_editor: Callable[[QImage], User], sample_image: QImage) -> User:
    """A user with an editor open on the sample image."""
    return open_editor(sample_image)
