"""Native Windows release/cancel routing through the real tab workspace."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
import traceback
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession

from verdiclip.document.annotations import RectangleShape
from verdiclip.document.commands import AddAnnotations
from verdiclip.document.document import Document
from verdiclip.editor.session import EditorSession
from verdiclip.editor.window import EditorOptions
from verdiclip.editor.workspace import Workspace
from verdiclip.geometry import Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.platform.tab_drag import NativeTabDrag, Win32CaptureApi
from verdiclip.settings import EditorSettings, OutputSettings


@pytest.mark.parametrize("outcome", ["merge", "detach", "escape", "hotkey"])
def test_native_tab_gesture_keeps_document_and_releases_input(  # noqa: C901, PLR0913, PLR0917, PLR0915 - native acceptance matrix
    qtbot: QtBot,
    qapp: QApplication,
    request: pytest.FixtureRequest,
    sample_image: QImage,
    tmp_path: Path,
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Win32 input lease and native queue preserve every edited session."""
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        pytest.skip("Requires QT_QPA_PLATFORM=windows")
    session = DesktopSession.current()
    reason = session.unavailable_reason() if session is not None else None
    if reason is not None:
        if request.config.getoption("--require-interactive-capture"):
            pytest.fail(reason)
        pytest.skip(reason)
    owner = Workspace()
    delivery = ImageDelivery(OutputSettings(directory=tmp_path))
    first = owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        delivery,
        EditorOptions(title="First"),
        new_window=True,
    )
    second = owner.open_document(
        EditorSession(Document(sample_image), EditorSettings()),
        delivery,
        EditorOptions(title="Second"),
        new_window=True,
    )
    page = first.pages[0]
    page.record.session.history.execute(
        AddAnnotations([RectangleShape(rect=Rect(20, 20, 40, 40))], "Rectangle")
    )
    expected = first.flattened()
    first.resize(760, 500)
    second.resize(760, 500)
    first.move(30, 30)
    second.move(850, 30)
    qtbot.waitExposed(first)
    qtbot.waitExposed(second)
    # Exposure and native activation are asynchronous. Drain show/IME activation
    # events before arming, or a legitimate queued WindowDeactivate cancels drag.
    qapp.processEvents()
    first.raise_()
    first.activateWindow()
    qtbot.waitUntil(first.isActiveWindow, timeout=3000)
    bar = first.tab_bar
    point = bar.tabRect(0).center()
    releases: list[tuple[int, int]] = []
    trace: list[dict[str, object]] = []
    original_dispatch = NativeTabDrag.dispatch
    original_cancel = NativeTabDrag.cancel

    def traced_dispatch(
        driver: NativeTabDrag,
        handle: int,
        message: int,
        parameter: int = 0,
        position: QPoint | None = None,
    ) -> bool:
        if len(trace) < 200:
            trace.append(
                {
                    "event": "native",
                    "handle": handle,
                    "message": hex(message),
                    "parameter": parameter,
                    "active": driver.active,
                }
            )
        return original_dispatch(driver, handle, message, parameter, position)

    def traced_cancel(driver: NativeTabDrag) -> None:
        if driver.active:
            trace.append(
                {
                    "event": "cancel",
                    "stack": traceback.format_stack(limit=6),
                    "foreground": int(api.GetForegroundWindow() or 0),
                    "first_active": first.isActiveWindow(),
                    "second_active": second.isActiveWindow(),
                }
            )
        original_cancel(driver)

    monkeypatch.setattr(NativeTabDrag, "dispatch", traced_dispatch)
    monkeypatch.setattr(NativeTabDrag, "cancel", traced_cancel)
    bar.drag_released.connect(lambda point: releases.append((point.x(), point.y())))
    bar.drag_started.connect(lambda _id: trace.append({"event": "started"}))
    bar.drag_cancelled.connect(lambda: trace.append({"event": "cancelled"}))
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.PostMessageW.argtypes = (
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    )
    api.PostMessageW.restype = ctypes.wintypes.BOOL
    api.GetCapture.restype = ctypes.wintypes.HWND
    api.GetForegroundWindow.restype = ctypes.wintypes.HWND
    try:
        qapp.processEvents()
        qtbot.waitUntil(
            lambda: (
                first.isActiveWindow()
                and int(api.GetForegroundWindow() or 0) == int(first.winId())
            ),
            timeout=3000,
        )
        trace.append(
            {
                "event": "before_press",
                "foreground": int(api.GetForegroundWindow() or 0),
                "expected": int(first.winId()),
                "first_active": first.isActiveWindow(),
            }
        )
        QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=point)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(point + QPoint(20, 0)),
            QPointF(bar.mapToGlobal(point + QPoint(20, 0))),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(bar, move)
        handle = int(first.winId())
        assert int(api.GetCapture() or 0) == handle
        if outcome == "merge":
            target = second.tab_bar.mapToGlobal(second.tab_bar.tabRect(0).center())
        elif outcome == "detach":
            target = first.canvas.mapToGlobal(first.canvas.rect().center())
        else:
            target = bar.mapToGlobal(point)
        message, parameter = (
            (0x0100, 0x1B)
            if outcome == "escape"
            else (0x0312, 0)
            if outcome == "hotkey"
            else (0x0202, 0)
        )
        hit = QApplication.widgetAt(target)
        local = first.mapFromGlobal(target)
        ratio = first.devicePixelRatioF()
        location = (round(local.x() * ratio) & 0xFFFF) | (
            (round(local.y() * ratio) & 0xFFFF) << 16
        )
        normalized = Win32CaptureApi().position(handle, location)
        assert normalized == target, (normalized, target, ratio, local)
        assert api.PostMessageW(handle, message, parameter, location)
        qtbot.waitUntil(lambda: int(api.GetCapture() or 0) == 0)
        qtbot.wait(20)
        if outcome == "merge":
            assert page in second.pages, (
                type(hit).__name__,
                target,
                releases,
                len(owner.windows),
            )
            assert first not in owner.windows
        elif outcome == "detach":
            assert len(owner.windows) == 2
            assert first not in owner.windows
            assert any(page in window.pages for window in owner.windows)
        else:
            assert first.pages == (page,)
            assert len(second.pages) == 1
        host = next(window for window in owner.windows if page in window.pages)
        host.activate_page(page)
        assert host.flattened() == expected
        assert page.record.session.history.can_undo
        assert not page.record.session.history.is_delivered
        assert len(owner.documents) == 2
        assert page.record.session.document.image == Document(sample_image).image
        if first in owner.windows:
            assert api.PostMessageW(handle, 0x0202, 0, 0)
        qtbot.wait(10)
        assert len(owner.documents) == 2
    finally:
        owner.cancel_drag()
        owner.close_all()
        owner.parking.deleteLater()
        qapp.processEvents()
        (tmp_path / "native-trace.json").write_text(
            json.dumps(trace, indent=2), encoding="utf-8"
        )
