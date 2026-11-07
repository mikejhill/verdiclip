"""Baseline real captures plus tabs; no broker or pointer-prototype dependencies."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import time
from collections.abc import Sequence
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot
from tests.capture.test_capture_native import NativeRun, RealDesktop
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.models import Capture, ScreenGeometry
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo
from verdiclip.settings import CaptureDestination, Settings

__all__ = ["make_app"]


class OwnedOrdinaryWindow:
    """Supply only an actual owned non-editor window, never enumerate other apps."""

    def __init__(self, window: QWidget) -> None:
        self.window = window

    def foreground(self) -> WindowInfo:
        """Map the real owned native frame to physical pixels."""
        frame = self.window.frameGeometry()
        geometry = ScreenGeometry.from_screen(self.window.screen())
        origin = geometry.physical.top_left + (
            Point(frame.x(), frame.y()) - geometry.logical.top_left
        ).scaled(geometry.scale)
        return WindowInfo(
            int(self.window.winId()),
            "Owned baseline source",
            Rect(
                origin.x,
                origin.y,
                frame.width() * geometry.scale,
                frame.height() * geometry.scale,
            ),
        )

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[WindowInfo]:
        """Baseline service sees only the explicitly owned fixture."""
        window = self.foreground()
        return () if window.handle in exclude else (window,)


@pytest.mark.ux
@pytest.mark.parametrize(
    "destination", [CaptureDestination.TABS, CaptureDestination.WINDOWS]
)
@pytest.mark.parametrize("minimized", [False, True])
def test_real_baseline_capture_destination_and_focus(
    qtbot: QtBot,
    request: pytest.FixtureRequest,
    make_app: AppFactory,
    destination: CaptureDestination,
    *,
    minimized: bool,
) -> None:
    """Actual window/fullscreen/repeat capture selects and focuses the right host."""
    NativeRun.require_desktop(request)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = w.HWND
    rival = QWidget()
    qtbot.addWidget(rival)
    rival.setWindowTitle("Owned baseline capture source")
    rival.resize(360, 240)
    rival.show()
    source = RealDesktop()
    defaults = Settings()
    settings = replace(
        defaults, capture=replace(defaults.capture, destination=destination)
    )
    app = make_app(
        settings=settings, screens=source, windows=OwnedOrdinaryWindow(rival)
    )
    captures: list[Capture] = []
    app.controller.capture.captured.connect(captures.append)
    first = None
    try:
        for index, action in enumerate(("window", "fullscreen", "repeat")):
            if first is not None and minimized:
                first.showMinimized()
            rival.raise_()
            rival.activateWindow()
            qtbot.waitUntil(
                lambda: user32.GetForegroundWindow() == int(rival.winId()), timeout=2000
            )
            started = time.perf_counter()
            app.press_hotkey(action)
            qtbot.waitUntil(
                lambda index=index: len(captures) == index + 1, timeout=5000
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            hosts = app.controller.editors
            assert len(hosts) == (
                1 if destination is CaptureDestination.TABS else index + 1
            )
            target = hosts[0] if destination is CaptureDestination.TABS else hosts[-1]
            if first is None:
                first = target
            qtbot.waitUntil(
                lambda target=target: (
                    user32.GetForegroundWindow() == int(target.winId())
                ),
                timeout=2000,
            )
            assert QApplication.activeWindow() is target
            assert not target.isMinimized()
            assert len(target.pages) == (
                index + 1 if destination is CaptureDestination.TABS else 1
            )
            assert target.tab_bar.currentIndex() == len(target.pages) - 1
            assert not captures[-1].image.isNull()
            assert not app.controller.capture.is_selecting
            print(  # noqa: T201 - fixed result fields
                "BASELINE_TABS_CAPTURE="
                + json.dumps(
                    {
                        "action": action,
                        "destination": destination.value,
                        "existing_minimized": minimized,
                        "elapsed_ms": elapsed_ms,
                        "selected_new_page": True,
                        "os_and_qt_focused": True,
                        "simulated_hotkey_dispatch": True,
                    },
                    sort_keys=True,
                )
            )
    finally:
        try:
            app.controller.capture.cancel()
        finally:
            source.close()
