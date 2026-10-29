"""Qt tab gestures distinguish clicks, release, cancellation and keyboard menus."""

from __future__ import annotations

from typing import override

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import delete
from tests.platform.test_tab_drag import Api

from verdiclip.editor.tab_bar import DocumentTabBar
from verdiclip.editor.tab_preview import TabDragPreview
from verdiclip.platform.tab_drag import NativeTabDrag


def mouse_move(bar: DocumentTabBar, offset: int, *, held: bool = True) -> None:
    """Send a real Qt move carrying the current button state."""
    point = bar.tabRect(0).center() + QPoint(offset, 0)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(point),
        QPointF(bar.mapToGlobal(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(bar, event)


def test_drag_threshold_cancel_release_and_paint(qtbot: QtBot) -> None:
    """A preview has exactly one terminal outcome and never closes a tab."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("First")
    bar.setTabData(0, "document-first")
    bar.show()
    started: list[str] = []
    released: list[QPoint] = []
    cancelled: list[bool] = []
    bar.drag_started.connect(started.append)
    bar.drag_released.connect(released.append)
    bar.drag_cancelled.connect(lambda: cancelled.append(True))
    point = bar.tabRect(0).center()
    mouse_move(bar, 0, held=False)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=point)
    mouse_move(bar, 1)
    assert not started
    mouse_move(bar, QApplication.startDragDistance() + 2)
    assert started == ["document-first"]
    mouse_move(bar, 30)
    bar.show_insertion(0)
    assert not bar.grab().isNull()
    bar.show_insertion(1)
    assert not bar.grab().isNull()
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=point)
    bar.released(point)
    assert len(released) == 1
    assert bar.count() == 1
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=point)
    mouse_move(bar, 30)
    QTest.keyClick(bar, Qt.Key.Key_Escape)
    bar.cancel_drag()
    assert cancelled == [True]
    assert bar.count() == 1
    assert bar.insertion_at(bar.mapToGlobal(QPoint(0, 0))) == 0
    assert bar.insertion_at(bar.mapToGlobal(QPoint(999, 0))) == 1


def test_keyboard_menu_and_disabled_drag(qtbot: QtBot) -> None:
    """Keyboard users get the same context actions without needing a drag."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("First")
    bar.setTabData(0, "document-first")
    bar.show()
    contexts: list[int] = []
    bar.context_requested.connect(lambda index, _point: contexts.append(index))
    QApplication.sendEvent(
        bar, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint())
    )
    assert contexts == [0]
    QTest.mouseClick(bar, Qt.MouseButton.RightButton, pos=bar.tabRect(0).center())
    assert contexts == [0, 0]
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, 30)
    cancelled: list[bool] = []
    bar.drag_cancelled.connect(lambda: cancelled.append(True))
    bar.setEnabled(False)
    assert cancelled == [True]
    QApplication.sendEvent(
        bar,
        QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier
        ),
    )


def test_non_document_tab_cannot_start_transfer(qtbot: QtBot) -> None:
    """A missing/stale document ID is a click, never an exported payload."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Not registered")
    bar.show()
    started: list[str] = []
    bar.drag_started.connect(started.append)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, 30)
    assert started == []


def test_late_callbacks_and_lost_button_state_cannot_arm_or_finish_drag(
    qtbot: QtBot,
) -> None:
    """A late movement/release after cancellation never invents a drop."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("First")
    bar.setTabData(0, "first")
    bar.show()
    events: list[str] = []
    bar.drag_started.connect(lambda _id: events.append("start"))
    bar.drag_released.connect(lambda _point: events.append("release"))
    bar.drag_cancelled.connect(lambda: events.append("cancel"))
    bar.moved(QPoint(5, 5))
    bar.released(QPoint(5, 5))
    bar.cancelled()
    QTest.keyClick(bar, Qt.Key.Key_Escape)
    QTest.mousePress(bar, Qt.MouseButton.MiddleButton, pos=bar.tabRect(0).center())
    mouse_move(bar, 30)
    QTest.mouseRelease(bar, Qt.MouseButton.MiddleButton, pos=bar.tabRect(0).center())
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, 30, held=False)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    assert events == []
    assert bar.count() == 1


def test_native_lease_failure_cancels_without_publishing_drag(qtbot: QtBot) -> None:
    """Failed input acquisition retains the tab and cannot later become a drop."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("First")
    bar.setTabData(0, "first")
    bar.show()
    api = Api()
    api.available = False
    driver = NativeTabDrag(bar, api)
    bar._driver = driver
    events: list[str] = []
    bar.drag_started.connect(lambda _id: events.append("start"))
    bar.drag_released.connect(lambda _point: events.append("release"))
    bar.drag_cancelled.connect(lambda: events.append("cancel"))
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, 30)
    driver.dispatch(42, 0x0202, position=QPoint(500, 500))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    assert events == ["cancel"]
    assert not driver.active
    assert api.owner == 0
    assert bar.count() == 1


@pytest.mark.parametrize("terminal", ["release", "cancel", "hide", "disable"])
def test_carried_tab_follows_pointer_and_retires_before_terminal(
    qtbot: QtBot,
    terminal: str,
) -> None:
    """The actual tab header travels outside its host without moving its document."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Edited capture with a descriptive title")
    bar.setTabData(0, "kept-document")
    bar.show()
    rect = bar.tabRect(0)
    origin = rect.center()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=origin)
    mouse_move(bar, QApplication.startDragDistance() + 2)
    preview = bar._preview
    assert preview is not None
    assert preview.isVisible()
    assert preview.size() == preview.pixmap().deviceIndependentSize().toSize()
    assert not preview.pixmap().isNull()
    cache = preview.pixmap().cacheKey()
    hotspot = origin - rect.topLeft()
    for point in (QPoint(-500, 200), QPoint(1500, -300), QPoint(50, 50)):
        bar.moved(point)
        assert preview.pos() == point - hotspot
        assert bar._preview is preview
        assert preview.pixmap().cacheKey() == cache
        assert bar.count() == 1
        assert bar.tabData(0) == "kept-document"
    observed: list[object] = []
    bar.drag_released.connect(lambda _point: observed.append(bar._preview))
    bar.drag_cancelled.connect(lambda: observed.append(bar._preview))
    if terminal == "release":
        bar.released(QPoint(50, 50))
    elif terminal == "cancel":
        bar.cancel_drag()
    elif terminal == "hide":
        bar.hide()
    else:
        bar.setEnabled(False)
    assert observed == [None]
    assert not preview.isVisible()
    assert bar._preview is None
    bar.moved(QPoint(100, 100))
    bar.released(QPoint(100, 100))
    assert observed == [None]
    assert bar.count() == 1


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75, 2.0, 2.5])
def test_drag_preview_retains_dpr_and_does_not_accept_focus_or_input(
    qtbot: QtBot,
    scale: float,
) -> None:
    """Supplied pixel ratios verify logical geometry, not native mixed-DPI behavior."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    pixmap = QPixmap(round(160 * scale), round(40 * scale))
    pixmap.fill(Qt.GlobalColor.red)
    pixmap.setDevicePixelRatio(scale)
    preview = TabDragPreview(bar, pixmap, QPoint(20, 10))
    qtbot.addWidget(preview)
    preview.follow(QPoint(-300, 500))
    assert preview.size().width() == 160
    assert preview.size().height() == 40
    assert preview.pos() == QPoint(-320, 490)
    assert preview.pixmap().devicePixelRatio() == scale
    rendered = preview.grab().toImage()
    assert rendered.pixelColor(rendered.rect().center()) == Qt.GlobalColor.red
    assert preview.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert preview.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert preview.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert preview.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert preview.focusPolicy() == Qt.FocusPolicy.NoFocus


@pytest.mark.parametrize("stage", ["construction", "movement"])
def test_preview_allocation_failure_releases_lease_and_keeps_document(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """A visual failure cannot strand input or publish a document transfer."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Kept")
    bar.setTabData(0, "kept")
    bar.show()
    api = Api()
    driver = NativeTabDrag(bar, api)
    bar._driver = driver
    events: list[str] = []
    bar.drag_started.connect(lambda _identifier: events.append("start"))
    bar.drag_cancelled.connect(lambda: events.append("cancel"))

    def fail(*_args: object) -> None:
        raise MemoryError("Controlled visual allocation failure")

    if stage == "construction":
        monkeypatch.setattr("verdiclip.editor.tab_bar.TabDragPreview", fail)
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, QApplication.startDragDistance() + 2)
    if stage == "movement":
        assert bar._preview is not None
        monkeypatch.setattr(bar._preview, "follow", fail)
        bar.moved(QPoint(-100, 200))
    assert events == (["cancel"] if stage == "construction" else ["start", "cancel"])
    assert bar._preview is None
    assert not driver.active
    assert api.owner == 0
    assert bar.tabData(0) == "kept"
    assert bar.count() == 1


def test_destroyed_preview_cancels_without_stranding_native_input(qtbot: QtBot) -> None:
    """A retired C++ visual cannot escape cleanup through a stale wrapper."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Retained")
    bar.setTabData(0, "retained")
    bar.show()
    api = Api()
    driver = NativeTabDrag(bar, api)
    bar._driver = driver
    cancelled: list[bool] = []
    bar.drag_cancelled.connect(lambda: cancelled.append(True))
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, QApplication.startDragDistance() + 2)
    preview = bar._preview
    assert preview is not None
    delete(preview)
    bar.moved(QPoint(-100, 200))
    assert cancelled == [True]
    assert bar._preview is None
    assert not driver.active
    assert api.owner == 0
    assert bar.tabData(0) == "retained"


@pytest.mark.parametrize("terminal", ["release", "cancel"])
def test_preview_hide_reentrancy_cannot_publish_two_terminal_events(
    qtbot: QtBot,
    terminal: str,
) -> None:
    """Observers of visual retirement cannot cancel an already terminal gesture."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Retained")
    bar.setTabData(0, "retained")
    bar.show()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, QApplication.startDragDistance() + 2)
    preview = bar._preview
    assert preview is not None

    class Observer(QObject):
        """Exercise a synchronous visual-hide observer."""

        @override
        def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
            """Cancel while retirement is still on the same GUI-thread stack."""
            if event.type() == QEvent.Type.Hide:
                bar.cancelled()
            return False

    observer = Observer()
    preview.installEventFilter(observer)
    events: list[str] = []
    bar.drag_released.connect(lambda _point: events.append("release"))
    bar.drag_cancelled.connect(lambda: events.append("cancel"))
    if terminal == "release":
        bar.released(QPoint(100, 100))
    else:
        bar.cancel_drag()
    assert events == [terminal]
    assert bar._preview is None
    assert bar.tabData(0) == "retained"


def test_reentrant_preview_follow_cancel_does_not_emit_late_movement(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving/showing a visual may cancel synchronously without moving a new drag."""
    bar = DocumentTabBar()
    qtbot.addWidget(bar)
    bar.addTab("Retained")
    bar.setTabData(0, "retained")
    bar.show()
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(0).center())
    mouse_move(bar, QApplication.startDragDistance() + 2)
    preview = bar._preview
    assert preview is not None
    moved: list[QPoint] = []
    bar.drag_moved.connect(moved.append)
    monkeypatch.setattr(preview, "follow", lambda _point: bar.cancel_drag())
    bar.moved(QPoint(-100, 200))
    assert moved == []
    assert bar._preview is None
