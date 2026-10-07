"""Capture journeys: the frozen overlay, window picking, and repeat."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from pytestqt.wait_signal import SignalBlocker
from tests.ux.conftest import Stopwatch

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect

pytestmark = pytest.mark.ux


@dataclass(frozen=True, slots=True)
class FakeWindow:
    """A window on the fake desktop."""

    title: str
    bounds: Rect
    handle: int = 0


@dataclass(slots=True)
class FakeDesktop:
    """A desktop whose pixels change every time it is frozen."""

    frames: int = 0
    fail: bool = False

    def freeze(self) -> FrozenScreen:
        """Return a desktop image covering every Qt screen, tinted per frame."""
        if self.fail:
            msg = "screen is locked"
            raise CaptureError(msg)
        self.frames += 1
        geo = QGuiApplication.primaryScreen().virtualGeometry()
        image = QImage(geo.width(), geo.height(), QImage.Format.Format_RGB32)
        image.fill(QColor(10 * self.frames, 100, 200))
        painter = QPainter(image)
        painter.fillRect(100, 100, 50, 50, QColor("black"))
        painter.end()
        return FrozenScreen(
            image=image, bounds=Rect(geo.x(), geo.y(), geo.width(), geo.height())
        )


@dataclass(slots=True)
class FakeWindows:
    """Windows on the fake desktop, topmost first."""

    windows: list[FakeWindow] = field(default_factory=list)
    active: FakeWindow | None = None

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[FakeWindow]:
        """Return the windows."""
        return [w for w in self.windows if w.handle not in exclude]

    def foreground(self) -> FakeWindow | None:
        """Return the active window."""
        return self.active


@pytest.fixture
def desktop() -> FakeDesktop:
    """The fake desktop."""
    return FakeDesktop()


@pytest.fixture
def windows() -> FakeWindows:
    """Two overlapping windows; the small one is on top."""
    small = FakeWindow("Dialog", Rect(120, 120, 200, 150), 2)
    big = FakeWindow("Editor", Rect(50, 50, 600, 400), 1)
    return FakeWindows([small, big], active=big)


@pytest.fixture
def service(
    qtbot: QtBot, desktop: FakeDesktop, windows: FakeWindows
) -> Iterator[CaptureService]:
    """A capture service over the fakes."""
    del qtbot
    created = CaptureService(desktop, windows)
    yield created
    created.cancel()


class CaptureOf:
    """Typed access to captures carried by signals."""

    @staticmethod
    def signal(blocker: SignalBlocker) -> Capture:
        """Return the Capture emitted to ``blocker``."""
        args = blocker.args or []
        value = args[0]
        assert isinstance(value, Capture)
        return value


class Overlay:
    """Drive the overlay like a mouse and keyboard would."""

    def __init__(self, service: CaptureService) -> None:
        self._overlay: SelectionOverlay = service.overlays[0]

    @property
    def widget(self) -> SelectionOverlay:
        """Return the overlay widget."""
        return self._overlay

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """Drag from ``start`` to ``end`` (overlay pixels)."""
        QTest.mousePress(
            self._overlay,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(*start),
        )
        for i in range(1, 5):
            x = start[0] + (end[0] - start[0]) * i // 4
            y = start[1] + (end[1] - start[1]) * i // 4
            QTest.mouseMove(self._overlay, QPoint(x, y))
        QTest.mouseRelease(
            self._overlay,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(*end),
        )

    def hover(self, at: tuple[int, int]) -> None:
        """Move the pointer without pressing."""
        QTest.mouseMove(self._overlay, QPoint(*at))

    def click(
        self, at: tuple[int, int], button: Qt.MouseButton = Qt.MouseButton.LeftButton
    ) -> None:
        """Click at ``at``."""
        QTest.mouseMove(self._overlay, QPoint(*at))
        QTest.mouseClick(
            self._overlay, button, Qt.KeyboardModifier.NoModifier, QPoint(*at)
        )


class TestRegionCapture:
    """The region overlay."""

    def test_overlay_appears_frozen_within_budget(
        self, service: CaptureService, desktop: FakeDesktop, qtbot: QtBot
    ) -> None:
        """Every monitor gets a frozen overlay quickly; content changes don't affect it.

        UX-CAP-01.
        """
        clock = Stopwatch()

        service.capture_region()

        overlays = service.overlays
        assert len(overlays) == len(QGuiApplication.screens())
        assert all(o.isVisible() for o in overlays)
        assert clock.elapsed_ms < 150
        assert desktop.frames == 1
        qtbot.wait(20)
        assert desktop.frames == 1

    def test_drag_captures_exact_frozen_pixels(
        self, service: CaptureService, desktop: FakeDesktop, qtbot: QtBot
    ) -> None:
        """The dragged rectangle's frozen pixels are captured at full resolution.

        UX-CAP-02, UX-CAP-03.
        """
        service.capture_region()
        overlay = Overlay(service)
        desktop.frames = 50  # Anything frozen later would look different

        with qtbot.waitSignal(service.captured) as signal:
            overlay.drag((90, 90), (160, 140))

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.REGION
        assert (capture.image.width(), capture.image.height()) == (70, 50)
        assert capture.image.pixelColor(20, 20) == QColor("black")
        assert capture.image.pixelColor(2, 2) == QColor(10, 100, 200)
        assert not service.is_selecting

    def test_dimension_label_shows_while_dragging(
        self, service: CaptureService
    ) -> None:
        """UX-CAP-02: the selection is reported in physical pixels while dragging."""
        service.capture_region()
        overlay = Overlay(service)
        QTest.mousePress(
            overlay.widget,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(10, 10),
        )
        QTest.mouseMove(overlay.widget, QPoint(110, 60))

        selection = overlay.widget.selection()

        assert selection == Rect(10, 10, 100, 50)

    @pytest.mark.parametrize("cancel", ["escape", "right_click"])
    def test_escape_or_right_click_cancels(
        self, service: CaptureService, qtbot: QtBot, cancel: str
    ) -> None:
        """UX-CAP-05: Esc or right-click closes the overlay without capturing."""
        service.capture_region()
        overlay = Overlay(service)
        captured: list[object] = []
        service.captured.connect(captured.append)

        with qtbot.waitSignal(service.cancelled):
            if cancel == "escape":
                QTest.keyClick(overlay.widget, Qt.Key.Key_Escape)
            else:
                overlay.click((50, 50), Qt.MouseButton.RightButton)

        assert not service.is_selecting
        assert captured == []

    def test_jittery_click_is_a_click_not_a_tiny_region(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """Moving under 3 px counts as a click on what is beneath.

        UX-CAP-03, UX-CAP-04.
        """
        service.capture_region()
        overlay = Overlay(service)
        overlay.hover((200, 200))

        with qtbot.waitSignal(service.captured) as signal:
            overlay.drag((200, 200), (202, 201))

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.WINDOW
        assert capture.image.width() > 3


class TestWindowPicking:
    """Clicking windows on the overlay."""

    def test_hover_highlights_topmost_and_click_captures_it(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """Hovering highlights the topmost window; a click captures exactly its bounds.

        UX-CAP-04.
        """
        service.capture_region()
        overlay = Overlay(service)
        overlay.hover((200, 200))
        hovered = overlay.widget.hovered_window()

        with qtbot.waitSignal(service.captured) as signal:
            overlay.click((200, 200))

        assert hovered is not None
        assert hovered.title == "Dialog"
        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.WINDOW
        assert capture.title == "Dialog"
        assert (capture.image.width(), capture.image.height()) == (200, 150)

    def test_click_on_desktop_captures_monitor(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-04: clicking where no window is captures that whole monitor."""
        service.capture_region()
        overlay = Overlay(service)
        screen = QGuiApplication.primaryScreen().geometry()

        with qtbot.waitSignal(service.captured) as signal:
            overlay.click((screen.width() - 5, screen.height() - 5))

        capture = CaptureOf.signal(signal)
        assert (capture.image.width(), capture.image.height()) == (
            screen.width(),
            screen.height(),
        )


class TestImmediateCaptures:
    """Full screen, active window, and repeat."""

    def test_fullscreen_captures_whole_desktop(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-07: full screen is captured immediately without an overlay."""
        with qtbot.waitSignal(service.captured) as signal:
            service.capture_fullscreen()

        capture = CaptureOf.signal(signal)
        geo = QGuiApplication.primaryScreen().virtualGeometry()
        assert capture.image.width() == geo.width()
        assert not service.is_selecting

    def test_active_window_uses_its_bounds(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-08: the foreground window is captured immediately."""
        with qtbot.waitSignal(service.captured) as signal:
            service.capture_active_window()

        capture = CaptureOf.signal(signal)
        assert capture.title == "Editor"
        assert (capture.image.width(), capture.image.height()) == (600, 400)

    def test_no_active_window_reports_failure(
        self, service: CaptureService, windows: FakeWindows, qtbot: QtBot
    ) -> None:
        """UX-G-05: a capture that can't happen says why."""
        windows.active = None

        with qtbot.waitSignal(service.failed) as signal:
            service.capture_active_window()

        assert "no active window" in signal.args[0]

    def test_screen_failure_reports_and_leaves_no_overlay(
        self, service: CaptureService, desktop: FakeDesktop, qtbot: QtBot
    ) -> None:
        """If the screen can't be read, the user is told and nothing is left on screen.

        UX-G-05.
        """
        desktop.fail = True

        with qtbot.waitSignal(service.failed):
            service.capture_region()

        assert not service.is_selecting

    def test_repeat_region_recaptures_same_area_without_overlay(
        self, service: CaptureService, desktop: FakeDesktop, qtbot: QtBot
    ) -> None:
        """Repeat after a region capture grabs the same area from a fresh screen.

        UX-CAP-09.
        """
        service.capture_region()
        with qtbot.waitSignal(service.captured) as first:
            Overlay(service).drag((90, 90), (160, 140))

        with qtbot.waitSignal(service.captured) as second:
            service.repeat_last()

        a = CaptureOf.signal(first)
        b = CaptureOf.signal(second)
        assert b.area == a.area
        assert not service.is_selecting
        assert desktop.frames == 2
        assert b.image.pixelColor(2, 2) != a.image.pixelColor(2, 2)

    def test_repeat_with_nothing_to_repeat_opens_overlay(
        self, service: CaptureService
    ) -> None:
        """UX-CAP-09: with no prior capture, repeat behaves like the region hotkey."""
        service.repeat_last()

        assert service.is_selecting

    def test_repeat_after_active_window_follows_new_foreground(
        self, service: CaptureService, windows: FakeWindows, qtbot: QtBot
    ) -> None:
        """Repeating an active-window capture captures whichever window is now active.

        UX-CAP-09.
        """
        with qtbot.waitSignal(service.captured):
            service.capture_active_window()
        windows.active = windows.windows[0]

        with qtbot.waitSignal(service.captured) as signal:
            service.repeat_last()

        assert CaptureOf.signal(signal).title == "Dialog"

    def test_repeat_fullscreen(self, service: CaptureService, qtbot: QtBot) -> None:
        """UX-CAP-09: repeating a full-screen capture captures the full screen again."""
        with qtbot.waitSignal(service.captured):
            service.capture_fullscreen()

        with qtbot.waitSignal(service.captured) as signal:
            service.repeat_last()

        assert CaptureOf.signal(signal).mode is CaptureMode.FULLSCREEN
