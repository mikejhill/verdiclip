"""Accessible Qt tab presentation with one explicit, cancellable drag owner."""

from __future__ import annotations

import sys
from typing import override

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QApplication, QTabBar

from verdiclip.platform.tab_drag import NativeTabDrag, Win32CaptureApi


class DocumentTabBar(QTabBar):
    """Use native styling while keeping gesture completion separate from moves."""

    drag_started = Signal(str)
    drag_moved = Signal(QPoint)
    drag_released = Signal(QPoint)
    drag_cancelled = Signal()
    context_requested = Signal(int, QPoint)

    def __init__(self) -> None:
        super().__init__()
        self.setAccessibleName("Capture tabs")
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setTabsClosable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pressed = -1
        self._origin = QPoint()
        self._dragging = False
        self._insertion = -1
        self._driver = (
            NativeTabDrag(self, Win32CaptureApi())
            if sys.platform == "win32" and QGuiApplication.platformName() == "windows"
            else None
        )

    def insertion_at(self, global_position: QPoint) -> int:
        """Return an insertion slot in current visual order."""
        local = self.mapFromGlobal(global_position)
        for index in range(self.count()):
            if local.x() < self.tabRect(index).center().x():
                return index
        return self.count()

    def show_insertion(self, index: int = -1) -> None:
        """Show an indicator without modifying document/tab ownership."""
        self._insertion = index
        self.update()

    def moved(self, position: QPoint) -> None:
        """Receive normalized driver movement in Qt global coordinates."""
        if self._dragging:
            self.drag_moved.emit(position)

    def released(self, position: QPoint) -> None:
        """Finish once; the driver has already released its input lease."""
        if self._dragging:
            self._dragging = False
            self._pressed = -1
            self.drag_released.emit(position)

    def cancelled(self) -> None:
        """Clear a preview without closing any popup, page or window."""
        if self._dragging:
            self._dragging = False
            self._pressed = -1
            self.drag_cancelled.emit()

    def cancel_drag(self) -> None:
        """Yield input non-destructively, including before screenshot capture."""
        if self._driver is not None:
            self._driver.cancel()
        self.cancelled()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Arm a body press; standard Qt owns close buttons and middle clicks."""
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self.tabAt(event.position().toPoint())
            self._origin = event.position().toPoint()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancel_drag()
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.context_requested.emit(index, event.globalPosition().toPoint())

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Begin one gesture after the system drag-distance threshold."""
        if self._dragging:
            self.moved(event.globalPosition().toPoint())
            return
        if self._pressed < 0 or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        if (
            event.position().toPoint() - self._origin
        ).manhattanLength() < QApplication.startDragDistance():
            return
        identifier = self.tabData(self._pressed)
        if not isinstance(identifier, str):
            return
        self._dragging = True
        if self._driver is not None and not self._driver.begin(
            int(self.window().winId())
        ):
            self.cancelled()
            return
        self.drag_started.emit(identifier)
        self.moved(event.globalPosition().toPoint())

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Use Qt release only on non-native platforms used by widget tests."""
        if self._dragging and self._driver is None:
            self.released(event.globalPosition().toPoint())
        self._pressed = -1
        super().mouseReleaseEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Escape cancels tab movement before reaching document shortcuts."""
        if event.key() == Qt.Key.Key_Escape and self._dragging:
            self.cancel_drag()
            return
        super().keyPressEvent(event)

    @override
    def changeEvent(self, event: QEvent) -> None:
        """A disabled strip cannot retain an input lease."""
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.cancel_drag()
        super().changeEvent(event)

    @override
    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Expose tab actions through the keyboard context-menu key."""
        if (
            event.reason() == QContextMenuEvent.Reason.Keyboard
            and self.currentIndex() >= 0
        ):
            self.context_requested.emit(
                self.currentIndex(),
                self.mapToGlobal(self.tabRect(self.currentIndex()).center()),
            )
            event.accept()
            return
        super().contextMenuEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Keep standard tab drawing and add a palette-aware insertion line."""
        super().paintEvent(event)
        if self._insertion < 0:
            return
        rectangle = self.tabRect(min(self._insertion, self.count() - 1))
        x = rectangle.left() if self._insertion < self.count() else rectangle.right()
        painter = QPainter(self)
        painter.setPen(QPen(self.palette().highlight().color(), 3))
        painter.drawLine(x, 3, x, self.height() - 3)
