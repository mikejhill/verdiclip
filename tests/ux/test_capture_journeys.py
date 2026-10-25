"""Capture journeys: the frozen overlay, window picking, and repeat."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from pytestqt.wait_signal import SignalBlocker
from tests.ux.conftest import Stopwatch

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import Capture, CaptureMode, ScreenGeometry
from verdiclip.capture.overlay import SelectionOverlay, SharedSelection
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

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


@dataclass(frozen=True, slots=True)
class WideDesktop:
    """The Qt screen plus a monitor ``extra`` that Qt doesn't know about."""

    extra: Rect

    def freeze(self) -> FrozenScreen:
        """Return a desktop image covering the Qt screen and ``extra``."""
        geo = QGuiApplication.primaryScreen().virtualGeometry()
        bounds = Rect(geo.x(), geo.y(), geo.width(), geo.height()).united(self.extra)
        image = QImage(
            int(bounds.width), int(bounds.height), QImage.Format.Format_RGB32
        )
        image.fill(QColor("gray"))
        return FrozenScreen(image=image, bounds=bounds)


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
        QApplication.processEvents()  # Deliver the queued capture request.
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
        qtbot.waitUntil(lambda: bool(service.overlays))

        overlays = service.overlays
        assert len(overlays) == len(QGuiApplication.screens())
        assert all(o.isVisible() for o in overlays)
        assert clock.elapsed_ms < 150
        assert desktop.frames == 1
        qtbot.wait(20)
        assert desktop.frames == 1

    def test_guides_and_highlight_appear_without_moving_the_mouse(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """Pressing the hotkey with the mouse resting shows guides and highlight.

        UX-CAP-01, UX-CAP-04.
        """
        QCursor.setPos(QPoint(200, 200))

        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))

        overlay = service.overlays[0]
        hovered = overlay.hovered_window()
        assert hovered is not None
        assert hovered.title == "Dialog"
        shown = overlay.grab().toImage()
        idle = QColor(shown.pixelColor(200, 20))  # On the vertical guide line
        beside = QColor(shown.pixelColor(260, 20))  # Same row, off the guides
        assert idle != beside, "the vertical guide line should be drawn already"

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

    def test_enter_starts_and_finishes_a_selection(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-15: Enter presses at the cursor, and Enter again releases."""
        service.capture_region()
        overlay = Overlay(service)
        overlay.hover((90, 90))
        QTest.keyClick(overlay.widget, Qt.Key.Key_Return)
        overlay.hover((160, 140))

        assert overlay.widget.selection() == Rect(90, 90, 70, 50)
        with qtbot.waitSignal(service.captured) as signal:
            QTest.keyClick(overlay.widget, Qt.Key.Key_Enter)

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.REGION
        assert (capture.image.width(), capture.image.height()) == (70, 50)

    def test_enter_twice_without_moving_captures_window(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-15: like a click, Enter-Enter in place picks the window beneath."""
        service.capture_region()
        overlay = Overlay(service)
        overlay.hover((200, 200))
        QTest.keyClick(overlay.widget, Qt.Key.Key_Return)

        with qtbot.waitSignal(service.captured) as signal:
            QTest.keyClick(overlay.widget, Qt.Key.Key_Return)

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.WINDOW
        assert capture.title == "Dialog"

    def test_click_finishes_a_selection_started_with_enter(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """UX-CAP-15: a click ends an Enter selection there, like a mouse release."""
        service.capture_region()
        overlay = Overlay(service)
        overlay.hover((90, 90))
        QTest.keyClick(overlay.widget, Qt.Key.Key_Return)
        overlay.hover((160, 140))

        with qtbot.waitSignal(service.captured) as signal:
            overlay.click((160, 140))

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.REGION
        assert (capture.image.width(), capture.image.height()) == (70, 50)


class TestMagnifier:
    """The loupe that follows the cursor."""

    def test_reticle_is_one_image_pixel_wide_around_the_cursor_pixel(self) -> None:
        """UX-CAP-06: bands one zoomed pixel wide frame the unobscured cursor pixel."""
        desktop = QImage(800, 600, QImage.Format.Format_RGB32)
        desktop.fill(QColor(128, 128, 128))
        desktop.setPixelColor(100, 100, QColor("white"))
        bounds = Rect(0, 0, 800, 600)
        overlay = SelectionOverlay(
            ScreenGeometry("only", bounds, bounds, 1.0),
            QGuiApplication.primaryScreen(),
            FrozenScreen(desktop, bounds),
            [],
            show_magnifier=True,
        )
        overlay.show()
        QTest.mouseMove(overlay, QPoint(100, 100))

        shown = overlay.grab().toImage()
        overlay.close()

        # Loupe at cursor + 24; the cursor pixel is cell 15 of 31, 4 px per cell
        cell = 124 + 15 * 4
        gray = QColor(128, 128, 128)

        def reddish(x: int, y: int) -> bool:
            color = shown.pixelColor(x, y)
            return color.red() > 150 and color.green() < 100

        for dx in range(4):
            for dy in range(4):
                assert shown.pixelColor(cell + dx, cell + dy) == QColor("white")
        assert reddish(cell - 1, cell + 1)  # Outline just outside the cell
        assert shown.pixelColor(cell - 3, cell + 1) == gray  # Gap before the band
        for offset in range(4):  # Bands are exactly one cell thick
            assert reddish(130, cell + offset)
            assert reddish(cell + offset, 130)
        assert shown.pixelColor(130, cell - 1) == gray
        assert shown.pixelColor(130, cell + 4) == gray
        assert shown.pixelColor(cell - 1, 130) == gray
        assert shown.pixelColor(cell + 4, 130) == gray


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

    def test_fullscreen_captures_only_the_cursor_monitor(
        self, windows: FakeWindows, qtbot: QtBot
    ) -> None:
        """UX-CAP-07: only the monitor under the cursor, immediately, no overlay."""
        screen = QGuiApplication.primaryScreen().geometry()
        wide = WideDesktop(extra=Rect(screen.width(), 0, 500, screen.height()))
        service = CaptureService(wide, windows)
        QCursor.setPos(screen.center())

        with qtbot.waitSignal(service.captured) as signal:
            service.capture_fullscreen()

        capture = CaptureOf.signal(signal)
        assert capture.mode is CaptureMode.FULLSCREEN
        assert (capture.image.width(), capture.image.height()) == (
            screen.width(),
            screen.height(),
        )
        assert capture.area == Rect(0, 0, screen.width(), screen.height())
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

    def test_repeat_clicked_window_keeps_its_title(
        self, service: CaptureService, qtbot: QtBot
    ) -> None:
        """Repeating a clicked-window capture keeps the window's title for naming.

        UX-CAP-09.
        """
        service.capture_region()
        with qtbot.waitSignal(service.captured) as first:
            Overlay(service).click((110, 110))

        with qtbot.waitSignal(service.captured) as second:
            service.repeat_last()

        assert CaptureOf.signal(first).mode is CaptureMode.WINDOW
        assert CaptureOf.signal(second).title == "Editor"

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


class TwoMonitors:
    """Overlays for two side-by-side 400 x 300 monitors sharing one selection."""

    def __init__(self, windows: Sequence[FakeWindow] = ()) -> None:
        image = QImage(800, 300, QImage.Format.Format_RGB32)
        image.fill(QColor(200, 200, 200))
        self.frozen = FrozenScreen(image, Rect(0, 0, 800, 300))
        screens = [
            ScreenGeometry(name, bounds, bounds, 1.0)
            for name, bounds in (
                ("left", Rect(0, 0, 400, 300)),
                ("right", Rect(400, 0, 400, 300)),
            )
        ]
        self.shared = SharedSelection(screens)
        self.left, self.right = (
            SelectionOverlay(
                geometry,
                QGuiApplication.primaryScreen(),
                self.frozen,
                windows,
                show_magnifier=False,
                shared=self.shared,
            )
            for geometry in screens
        )
        self.regions: list[Rect] = []
        for overlay in (self.left, self.right):
            overlay.region_chosen.connect(self.regions.append)
            overlay.show()

    def close(self) -> None:
        """Close both overlays."""
        for overlay in (self.left, self.right):
            overlay.close()


@pytest.fixture
def two_monitors() -> Iterator[TwoMonitors]:
    """Two monitors with nothing on them."""
    monitors = TwoMonitors()
    yield monitors
    monitors.close()


class TestAcrossMonitors:
    """One selection that spans monitors, driven from either of them."""

    def test_drag_continues_onto_the_next_monitor(
        self, two_monitors: TwoMonitors
    ) -> None:
        """UX-CAP-02, UX-CAP-03: a drag can cross from one monitor to the next."""
        left = two_monitors.left
        no_mods = Qt.KeyboardModifier.NoModifier
        QTest.mousePress(left, Qt.MouseButton.LeftButton, no_mods, QPoint(300, 50))
        # The pressed overlay keeps the mouse, so its positions run off its edge
        for x in (340, 420, 500):
            QTest.mouseMove(left, QPoint(x, 150))

        assert left.selection() == Rect(300, 50, 100, 100)
        assert two_monitors.right.selection() == Rect(0, 50, 100, 100)
        QTest.mouseRelease(left, Qt.MouseButton.LeftButton, no_mods, QPoint(500, 150))

        assert two_monitors.regions == [Rect(300, 50, 200, 100)]

    def test_enter_starts_where_the_cursor_is_not_where_focus_is(
        self, two_monitors: TwoMonitors
    ) -> None:
        """UX-CAP-15: Enter works on every monitor, whichever overlay has focus."""
        left, right = two_monitors.left, two_monitors.right
        QTest.mouseMove(right, QPoint(20, 30))  # Pointer on the right monitor

        QTest.keyClick(left, Qt.Key.Key_Return)  # Focus still on the left one
        QTest.mouseMove(right, QPoint(120, 130))
        QTest.keyClick(left, Qt.Key.Key_Return)

        assert two_monitors.regions == [Rect(420, 30, 100, 100)]

    def test_enter_selection_can_cross_monitors_and_end_with_a_click(
        self, two_monitors: TwoMonitors
    ) -> None:
        """UX-CAP-15: start with Enter on one monitor, click to finish on another."""
        left, right = two_monitors.left, two_monitors.right
        QTest.mouseMove(left, QPoint(350, 40))
        QTest.keyClick(left, Qt.Key.Key_Return)
        QTest.mouseMove(right, QPoint(60, 90))

        QTest.mouseClick(
            right,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(60, 90),
        )

        assert two_monitors.regions == [Rect(350, 40, 110, 50)]

    def test_click_on_empty_desktop_captures_the_clicked_monitor(
        self, two_monitors: TwoMonitors
    ) -> None:
        """UX-CAP-04: a click where no window is captures that monitor."""
        right = two_monitors.right
        QTest.mouseMove(right, QPoint(60, 90))

        QTest.mouseClick(
            right,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(60, 90),
        )

        assert two_monitors.regions == [Rect(400, 0, 400, 300)]


class TestMixedScaling:
    """Monitors with different scale factors map to the right physical pixels."""

    def test_positions_map_per_monitor(self) -> None:
        """A 200% monitor beside a 100% one: each scales from its own corner."""
        hidpi = ScreenGeometry(
            "laptop", Rect(0, 0, 1920, 1080), Rect(0, 0, 3840, 2160), 2.0
        )
        normal = ScreenGeometry(
            "monitor", Rect(3840, 0, 1920, 1080), Rect(3840, 0, 1920, 1080), 1.0
        )
        shared = SharedSelection([hidpi, normal])

        assert shared.to_physical(Point(1000, 500)) == Point(2000, 1000)
        assert shared.to_physical(Point(3900, 10)) == Point(3900, 10)
        # Between the two in Qt's coordinates: snap to the nearer monitor's edge
        assert shared.to_physical(Point(2000, 10)) == Point(3840, 20)

    def test_shared_edge_belongs_to_the_monitor_it_starts(self) -> None:
        """The first column of a scaled monitor maps with that monitor's scale."""
        normal = ScreenGeometry(
            "monitor", Rect(0, 0, 1920, 1080), Rect(0, 0, 1920, 1080), 1.0
        )
        hidpi = ScreenGeometry(
            "laptop", Rect(1920, 0, 1920, 1080), Rect(1920, 0, 3840, 2160), 2.0
        )
        shared = SharedSelection([normal, hidpi])

        assert shared.to_physical(Point(1920, 200)) == Point(1920, 400)
        assert shared.to_physical(Point(1919, 200)) == Point(1919, 200)

    def test_drag_from_scaled_monitor_onto_unscaled_one(self) -> None:
        """UX-CAP-03: the area spans both monitors in physical pixels."""
        hidpi = ScreenGeometry(
            "laptop", Rect(0, 0, 1920, 1080), Rect(0, 0, 3840, 2160), 2.0
        )
        normal = ScreenGeometry(
            "monitor", Rect(3840, 0, 1920, 1080), Rect(3840, 0, 1920, 1080), 1.0
        )
        shared = SharedSelection([hidpi, normal])

        shared.press(shared.to_physical(Point(1800, 100)))
        area = shared.release(shared.to_physical(Point(4000, 300)))

        assert area == Rect(3600, 200, 400, 100)


class TestSpanningWindows:
    """A window that spans monitors is highlighted on every monitor it covers."""

    def test_hovering_lights_up_the_whole_window_across_monitors(self) -> None:
        """UX-CAP-04: the part of the window on the other monitor is undimmed too."""
        spanning = FakeWindow("Spreadsheet", Rect(300, 50, 200, 100))
        monitors = TwoMonitors([spanning])
        left, right = monitors.left, monitors.right

        QTest.mouseMove(left, QPoint(350, 100))

        undimmed = QColor(200, 200, 200)
        right_pixels = right.grab().toImage()
        assert right_pixels.pixelColor(50, 100) == undimmed  # Inside, on the right
        assert right_pixels.pixelColor(150, 100) != undimmed  # Outside stays dim
        QTest.mouseMove(left, QPoint(50, 250))
        assert right.grab().toImage().pixelColor(50, 100) != undimmed
        monitors.close()
