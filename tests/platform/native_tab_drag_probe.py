"""Read-only native feasibility probe; creates and cleans up only test windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, qVersion
from PySide6.QtWidgets import QApplication, QMenu, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from verdiclip.platform.tab_drag import NativeTabDrag, Win32CaptureApi


class Receiver:
    """Collect completion receipts for native-message and lease checks."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def moved(self, position: QPoint) -> None:
        """Record movement through the native adapter."""
        del position
        self.events.append("move")

    def released(self, position: QPoint) -> None:
        """Record explicit release through the native adapter."""
        del position
        self.events.append("release")

    def cancelled(self) -> None:
        """Record native cancellation without detaching."""
        self.events.append("cancel")


def main() -> None:
    """Exercise Win32-to-Qt native dispatch without changing installed settings."""
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = QWidget()
    window.setWindowTitle("VerdiClip tab drag feasibility test")
    window.resize(450, 180)
    window.show()
    window.raise_()
    window.activateWindow()
    app.processEvents()
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.PostMessageW.argtypes = (
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )
    api.PostMessageW.restype = ctypes.wintypes.LPARAM
    api.GetCapture.restype = ctypes.wintypes.HWND
    handle = int(window.winId())
    receiver = Receiver()
    driver = NativeTabDrag(receiver, Win32CaptureApi())
    records: list[dict[str, object]] = []
    try:
        for name, message, parameter, expected in [
            ("native_release", 0x0202, 0, "release"),
            ("native_escape", 0x0100, 0x1B, "cancel"),
            ("capture_loss", 0x0215, 0, "cancel"),
            ("hotkey_handoff", 0x0312, 0, "cancel"),
        ]:
            before = len(receiver.events)
            assert driver.begin(handle), "Native capture acquisition failed"
            assert int(api.GetCapture() or 0) == handle
            api.PostMessageW(handle, message, parameter, 0)
            app.processEvents()
            assert receiver.events[before:] == [expected], (name, receiver.events)
            assert not driver.active
            assert int(api.GetCapture() or 0) == 0
            # A late release must never resurrect a cancelled operation.
            api.PostMessageW(handle, 0x0202, 0, 0)
            app.processEvents()
            assert receiver.events[before:] == [expected]
            records.append(
                {"scenario": name, "outcome": expected, "lease_released": True}
            )
        menu = QMenu(window)
        menu.addAction("This test popup must stay open")
        menu.popup(window.mapToGlobal(QPoint(30, 50)))
        app.processEvents()
        assert menu.isVisible()
        assert driver.begin(handle)
        driver.cancel()
        assert menu.isVisible(), "Cancelling tab input must not dismiss a popup"
        records.append({"scenario": "non_destructive_cancel", "popup_retained": True})
        menu.close()  # Explicit test teardown only; never capture behavior.
        sys.stdout.write(
            json.dumps(
                {
                    "Qt": qVersion(),
                    "platform": app.platformName(),
                    "records": records,
                    "limits": (
                        "Native HWND messages and actual SetCapture; "
                        "no physical mouse, mixed-DPI or external-app UX claim."
                    ),
                },
                indent=2,
            )
        )
    finally:
        driver.cancel()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
