"""Owned Qt popup with deterministic pixels; never opens or reads another app."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final, override

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QWidgetAction
from scripts.frozen_menu_hmenu import OwnedNativeMenu

COLORS: Final = ("#d72b91", "#19bd83")
BACKGROUND: Final = "#18314d"


class MenuPatch(QWidget):
    """Two opaque flat colors make an exact, theme-independent pixel oracle."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(240, 100)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint no text, antialiasing, animation, or sampled private content."""
        del event
        painter = QPainter(self)
        painter.fillRect(0, 0, 120, 100, QColor(COLORS[0]))
        painter.fillRect(120, 0, 120, 100, QColor(COLORS[1]))
        painter.end()


def add_patch(menu: QMenu) -> MenuPatch:
    """Place synthetic marker pixels inside an actual native Qt popup."""
    patch = MenuPatch()
    action = QWidgetAction(menu)
    action.setDefaultWidget(patch)
    menu.addAction(action)
    return patch


def physical_box(widget: QWidget) -> list[int]:
    """Map the marker interior to physical pixels, away from all borders."""
    screen = widget.screen()
    origin = screen.geometry().topLeft()
    position = widget.mapToGlobal(QPoint(8, 8))
    scale = screen.devicePixelRatio()
    return [
        round(origin.x() + (position.x() - origin.x()) * scale),
        round(origin.y() + (position.y() - origin.y()) * scale),
        round((widget.width() - 16) * scale),
        round((widget.height() - 16) * scale),
    ]


class MenuFixture:
    """A full-screen owned backdrop and real popup in this process only."""

    def __init__(self, receipt: Path | None = None, *, native: bool = False) -> None:
        self.receipt = receipt
        self.background = QWidget()
        self.background.setWindowTitle("Owned freeze-first menu fixture")
        self.background.setStyleSheet(f"background: {BACKGROUND};")
        self.background.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.menu = QMenu(self.background)
        self.menu.setStyleSheet("QMenu { margin: 0; padding: 0; border: 0; }")
        self.patch = add_patch(self.menu)
        self.menu.aboutToHide.connect(self._changed)
        self.native = OwnedNativeMenu(int(self.background.winId())) if native else None
        self.last: dict[str, object] = {}
        self.timer = QTimer()
        self.timer.setInterval(10)
        self.timer.timeout.connect(self.publish)

    def show(self) -> None:
        """Expose only the synthetic fixture before opening its popup."""
        self.background.showFullScreen()
        self.background.raise_()
        self.background.activateWindow()
        QTimer.singleShot(0, self.open_menu)
        self.timer.start()

    def open_menu(self) -> None:
        """Open the popup at a stable interior point on the primary monitor."""
        point = self.background.mapToGlobal(QPoint(180, 160))
        if self.native is None:
            self.menu.popup(point)
        else:
            scale = self.background.devicePixelRatioF()
            self.native.show(round(point.x() * scale), round(point.y() * scale))

    def _changed(self) -> None:
        """Make live content visibly different once the popup actually closes."""
        self.background.setStyleSheet("background: #463519;")

    def state(self) -> dict[str, object]:
        """Return only owned fixture identities and geometry, never pixels."""
        handle = self.menu.windowHandle()
        native_window, native_visible, native_patch = (
            self.native.state() if self.native is not None else (0, False, [])
        )
        return {
            "pid": os.getpid(),
            "background": int(self.background.winId()),
            "menu": native_window
            if self.native is not None
            else int(self.menu.winId()),
            "visible": native_visible
            if self.native is not None
            else self.menu.isVisible(),
            "exposed": bool(native_visible and native_patch)
            if self.native is not None
            else handle is not None and handle.isExposed(),
            "patch": native_patch
            if self.native is not None
            else physical_box(self.patch),
            "native_hmenu": self.native is not None,
        }

    def publish(self) -> None:
        """Atomically replace an owned temporary receipt only on state change."""
        state = self.state()
        if self.receipt is not None and state != self.last:
            pending = self.receipt.with_suffix(".pending")
            pending.write_text(json.dumps(state), encoding="utf-8")
            pending.replace(self.receipt)
        self.last = state

    def close(self) -> None:
        """Retire only this fixture's timer, popup and backdrop."""
        self.timer.stop()
        if self.native is not None:
            self.native.close()
        self.menu.close()
        self.background.close()


def main() -> None:
    """Run a disposable fixture until its owning qualification process exits."""
    app = QApplication([])
    fixture = MenuFixture(Path(sys.argv[1]), native="--hmenu" in sys.argv[2:])
    fixture.show()
    # A crashed owner cannot leave the disposable fixture alive indefinitely.
    QTimer.singleShot(60000, app.quit)
    try:
        app.exec()
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
