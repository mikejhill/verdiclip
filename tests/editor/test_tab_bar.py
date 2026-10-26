"""Qt tab gestures distinguish clicks, release, cancellation and keyboard menus."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.platform.test_tab_drag import Api

from verdiclip.editor.tab_bar import DocumentTabBar
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
