"""Test-only point chooser and actual tray-menu handoff on hosted Windows.

This is not product wiring. QApplication stays in the parent; only the existing
owned helper enters UI Automation. Native input is sent only to our own widgets.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPoint, QTimer
from PySide6.QtGui import QIcon, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from scripts.simple_scroll_native import (
    _capture,
    _prepare,
    _real_mouse_input,
    _require,
    _verify_edge_rows,
    _window,
)
from scripts.simple_scroll_native import (
    main as native_main,
)

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.overlay import SharedSelection
from verdiclip.capture.scroll_selection import PointOverlay
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import _LastInput
from verdiclip.platform.windows import WindowInfo, WindowLocator
from verdiclip.shell.tray import TrayCommands, TrayIcon


class Desktop:
    """Small native observation/input boundary, restricted to this hosted test."""

    def __init__(self) -> None:
        self.user = c.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetForegroundWindow": ((), w.HWND),
            "GetLastInputInfo": ((c.POINTER(_LastInput),), w.BOOL),
            "WindowFromPoint": ((w.POINT,), w.HWND),
            "GetAncestor": ((w.HWND, w.UINT), w.HWND),
            "SetCursorPos": ((c.c_int, c.c_int), w.BOOL),
            "mouse_event": ((w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.c_size_t), None),
            "keybd_event": ((w.BYTE, w.BYTE, w.DWORD, c.c_size_t), None),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result

    def input_tick(self) -> int:
        """Observe the latest completed OS input without updating it."""
        info = _LastInput(c.sizeof(_LastInput), 0)
        _require(
            self.user.GetLastInputInfo(c.byref(info)), "Cannot observe native input"
        )
        return int(info.tick)

    def foreground(self) -> int:
        """Return the actual native foreground HWND."""
        return int(self.user.GetForegroundWindow() or 0)

    def click(
        self, widget: QWidget, point: Point, *, enter: bool = False, hold_ms: int = 0
    ) -> None:
        """Deliver a completed native gesture only after verifying owned hit/focus."""
        _require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Native chooser input requires a disposable hosted runner",
        )
        _require(self.foreground() == int(widget.winId()), "Owned widget lacks focus")
        hit = self.user.WindowFromPoint(w.POINT(round(point.x), round(point.y)))
        _require(
            self.user.GetAncestor(hit, 2) == int(widget.winId()),
            "The input point is not covered by the owned widget",
        )
        _require(
            self.user.SetCursorPos(round(point.x), round(point.y)),
            "Could not position the owned test pointer",
        )
        if enter:
            self.user.keybd_event(0x0D, 0, 0, 0)
            if hold_ms:
                QTest.qWait(hold_ms)
            self.user.keybd_event(0x0D, 0, 2, 0)
        else:
            self.user.mouse_event(2, 0, 0, 0, 0)
            if hold_ms:
                QTest.qWait(hold_ms)
            self.user.mouse_event(4, 0, 0, 0, 0)


class Handoff:
    """Own the native test's widgets and bounded callbacks until one result."""

    def __init__(self, kind: str, output: Path, report: dict[str, object]) -> None:
        self.kind, self.output, self.report = kind, output, report
        self.window = _prepare(_window())  # Setup only, never during handoff.
        self.desktop = Desktop()
        self.point = self.window.bounds.center
        report.update(
            window=asdict(self.window), initial_foreground=self.desktop.foreground()
        )
        self.source = MssScreenSource()
        self.loop = QEventLoop()
        self.overlays: list[PointOverlay] = []
        self.windows: list[WindowInfo] = []
        self.failures: list[Exception] = []
        self.other = QWidget()
        self.other.setWindowTitle("Owned handoff interference")
        commands = TrayCommands(
            self._request,
            self._request,
            self._request,
            self._request,
            self._request,
            self._request,
            self._request,
            self._request,
            self.loop.quit,
        )
        self.tray = TrayIcon(QIcon(), commands)

    def _close_selectors(self) -> None:
        for overlay in self.overlays:
            overlay.dismiss()
        self.overlays.clear()

    def _guarded(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as error:  # noqa: BLE001 - re-raised after the Qt loop
            self.failures.append(error)
            self._close_selectors()
            self.loop.quit()

    @staticmethod
    def _fail(message: str) -> None:
        raise RuntimeError(message)

    def _handoff(self, at: Point, selected: WindowInfo, tick: int) -> None:
        _require(
            not self.overlays
            and not any(
                isinstance(widget, PointOverlay) and widget.isVisible()
                for widget in QApplication.topLevelWidgets()
            ),
            "A selector survived the completed click",
        )
        _require(QApplication.activePopupWidget() is None, "A popup still owns input")
        _require(QWidget.mouseGrabber() is None, "A widget still owns the mouse")
        _require(QWidget.keyboardGrabber() is None, "A widget still owns the keyboard")
        _require(
            self.desktop.input_tick() == tick, "Input changed during selector dismissal"
        )
        self.report["handoff_foreground"] = self.desktop.foreground()
        _require(
            self.desktop.foreground() == selected.handle, "Target focus did not return"
        )
        if self.kind == "edge-select-input":
            self._interfere_input(tick)
        elif self.kind == "edge-select-focus":
            self.other.show()
            self.other.raise_()
            self.other.activateWindow()
            QApplication.processEvents()
            _require(
                self.desktop.foreground() == int(self.other.winId()),
                "Focus did not change",
            )
        result = _capture(
            at, self.output, self.kind, window=selected.handle, input_tick=tick
        )
        self.report.update(
            message=result.message, restoration=result.restoration, frames=result.frames
        )
        movement = json.loads(
            (self.output / "movement.json").read_text(encoding="utf-8")
        )
        self.report["movement"] = movement
        if self.kind in ("edge-select-input", "edge-select-focus"):
            _require(
                result.image is None and result.frames == 0,
                "Handoff interruption published pixels",
            )
            _require(movement["writes"] == 0, "Handoff interruption moved the target")
            _require(
                "before scrolling" in result.message or "foreground" in result.message,
                "Handoff interruption was not rejected at discovery",
            )
        else:
            _require(result.image is not None, result.message)
            _require(
                result.restoration == "restored", "Chooser capture did not restore"
            )
            if result.image is not None:
                _require(
                    result.image.save(str(self.output / "result.png")),
                    "Could not save result",
                )
                _verify_edge_rows(result, self.output, self.report)
                self._verify_target(selected.handle, tick)
        self.report["success"] = True
        self.loop.quit()

    def _interfere_input(self, release_tick: int) -> None:
        # GetTickCount typically resolves at 10-16 ms. Separate that input tick.
        QTest.qWait(20)
        _real_mouse_input()
        for _ in range(20):
            QTest.qWait(10)
            if self.desktop.input_tick() != release_tick:
                return
        raise RuntimeError("Input interference was not observed")

    def _verify_target(self, window: int, tick: int) -> None:
        selected = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        _require(
            selected["window"] == window and selected["input_tick"] == tick,
            "Child silently changed the selected target or input baseline",
        )
        self.report["child_target"] = selected
        area = Rect(**selected["area"])
        after = self.source.freeze().crop(area)
        _require(
            after.save(str(self.output / "after.png")), "Could not save restored view"
        )
        _require(
            after == QImage(str(self.output / "frame-0.png")),
            "Chooser capture did not restore the original top pixels",
        )

    def _selected(self, at: Point) -> None:
        # Synchronous release-time snapshot. Never rebase after closing widgets.
        tick = self.desktop.input_tick()
        _require(
            at == Point(round(self.point.x), round(self.point.y)),
            "Selection did not use the actual native pointer coordinates",
        )
        chosen = next((w for w in self.windows if w.bounds.contains(at)), None)
        _require(
            chosen is not None and chosen.handle == self.window.handle,
            "Wrong frozen target",
        )
        if chosen is None:
            raise RuntimeError("No selected target")
        self.report.update(
            selected_point=asdict(at),
            selected_window=chosen.handle,
            release_input_tick=tick,
        )
        self._close_selectors()
        QTimer.singleShot(
            0, lambda: self._guarded(lambda: self._handoff(at, chosen, tick))
        )

    def _show_selectors(self) -> None:
        _require(QApplication.activePopupWidget() is None, "Tray menu has not closed")
        self.windows = WindowLocator().visible_windows()
        frozen = self.source.freeze()
        frozen.image.save(str(self.output / "before-selector.png"))
        shared = SharedSelection()
        for screen in QApplication.screens():
            overlay = PointOverlay(
                ScreenGeometry.from_screen(screen),
                screen,
                frozen,
                self.windows,
                show_magnifier=False,
                shared=shared,
            )
            overlay.chosen.connect(lambda at: self._guarded(lambda: self._selected(at)))
            overlay.cancelled.connect(
                lambda: self._guarded(lambda: self._fail("Selector cancelled"))
            )
            self.overlays.append(overlay)
            overlay.show()
        active = next(
            o for o in self.overlays if o.screen_geometry.physical.contains(self.point)
        )
        active.raise_()
        active.activateWindow()
        active.setFocus()
        QTimer.singleShot(
            150,
            lambda: self._guarded(
                lambda: self.desktop.click(
                    active, self.point, enter=self.kind == "edge-select-enter"
                )
            ),
        )

    def _request(self) -> None:
        QTimer.singleShot(0, lambda: self._guarded(self._show_selectors))

    def _choose_menu(self) -> None:
        menu = self.tray.menu
        _require(
            QApplication.activePopupWidget() is menu, "Actual tray menu is not active"
        )
        self.report.update(
            menu_foreground=self.desktop.foreground(), menu_hwnd=int(menu.winId())
        )
        action = self.tray.action("region")  # Prototype substitutes this callback only.
        position = menu.mapToGlobal(menu.actionGeometry(action).center())
        geometry = ScreenGeometry.from_screen(menu.screen())
        physical = geometry.to_physical(
            Point(position.x() - geometry.logical.x, position.y() - geometry.logical.y)
        )
        self.desktop.click(menu, physical)

    def run(self) -> None:
        """Drive owned UI, preserve failure artifacts, and close every owned widget."""
        menu = self.tray.menu
        menu.popup(QPoint(round(self.point.x), round(self.point.y)))
        menu.raise_()
        menu.activateWindow()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(
            lambda: self._guarded(lambda: self._fail("Chooser handoff timed out"))
        )
        timer.start(35_000)
        QTimer.singleShot(150, lambda: self._guarded(self._choose_menu))
        try:
            self.loop.exec()
            if self.failures:
                raise self.failures[0]
            _require(self.report.get("success"), "Chooser handoff did not finish")
        finally:
            timer.stop()
            self._close_selectors()
            menu.close()
            self.tray.hide()
            self.other.close()
            self.source.close()


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    """Exercise the proof without changing any product controller or capture path."""
    Handoff(kind, output, report).run()


if __name__ == "__main__":
    native_main(gui=True, case=_case)
