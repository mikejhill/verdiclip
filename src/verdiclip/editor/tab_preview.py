"""One visual-only, nonactivating tab image; it never owns a document or input."""

from typing import override

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import QWidget


class TabDragPreview(QWidget):
    """Carry the rendered tab at its original logical-pixel pointer hotspot."""

    def __init__(self, parent: QWidget, pixmap: QPixmap, hotspot: QPoint) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Moving capture tab")
        self._pixmap = QPixmap(pixmap)
        self.resize(pixmap.deviceIndependentSize().toSize())
        self._hotspot = QPoint(hotspot)

    def pixmap(self) -> QPixmap:
        """Return an implicit-sharing copy without normalizing its pixel ratio."""
        return QPixmap(self._pixmap)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the immutable high-DPI tab image in logical widget coordinates."""
        del event
        painter = QPainter(self)
        try:
            painter.drawPixmap(QPoint(), self._pixmap)
        finally:
            painter.end()

    def follow(self, position: QPoint) -> None:
        """Move only the visual; the source tab keeps the gesture/input lease."""
        self.move(position - self._hotspot)
        if not self.isVisible():
            self.show()
