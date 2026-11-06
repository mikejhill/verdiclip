"""Service prototype admission/refusal with real Qt but no native system calls."""

import ctypes
import ctypes.wintypes as w
from typing import override

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.input_fixture import ReplayInputBroker
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip.capture.native_pointer import NativePointerRoute, PointerPhase
from verdiclip.capture.native_pointer_win32 import NativePointerSession
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError


class Session:
    """Expose no Win32 API while retaining the production lifetime policy."""

    def __init__(self) -> None:
        self.route = NativePointerRoute()
        self.observations: list[int] = []

    def bind(self, windows: frozenset[int]) -> None:
        """Bind fake Qt native identifiers only."""
        self.route.bind(windows)

    def natural_cursor(self, window: int) -> bool:
        """Record actual handler dispatch without any native ownership claim."""
        self.observations.append(window)
        return self.route.admit(window, lambda: True)

    def close(self) -> None:
        """Latch this session closed."""
        self.route.close()


class Broker(ReplayInputBroker):
    """A replay broker explicitly acknowledging the prototype option."""

    @override
    def set_native_pointer(self, pointer: NativePointerSession | None) -> bool:
        return True


def test_protected_native_context_keeps_existing_passive_path(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No alternate input ownership is used after protected-state refusal."""

    def unavailable() -> None:
        raise ValueError("protected")

    monkeypatch.setattr("verdiclip.capture.service.NativePointerSession", unavailable)
    broker = Broker(qapp)
    service = CaptureService(FakeDesktop(), FakeWindows(), input_broker=broker)
    service.enable_native_pointer()
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        assert service._native_pointer is None
        assert all(
            overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
            for overlay in service.overlays
        )
    finally:
        service.cancel()
        qtbot.waitUntil(lambda: not service.is_selecting)


def test_owned_surface_ignores_duplicate_qt_input_and_closes_generation(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the broker updates selection; stale native callbacks cannot reactivate."""
    monkeypatch.setattr("verdiclip.capture.service.NativePointerSession", Session)
    broker = Broker(qapp)
    service = CaptureService(FakeDesktop(), FakeWindows(), input_broker=broker)
    service.enable_native_pointer()
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        pointer = service._native_pointer
        assert pointer is not None
        overlay = service.overlays[0]
        assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
        handle = int(overlay.winId())
        prior = overlay._shared.pointer
        QTest.mouseMove(overlay, QPoint(80, 70))
        QTest.keyClick(overlay, Qt.Key.Key_Right)
        QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(80, 70))
        assert overlay._shared.pointer == prior
        assert not overlay._shared.pressed
        with pytest.raises(CaptureError, match="routing"):
            service.enable_native_pointer()
        service.cancel()
        qtbot.waitUntil(lambda: not service.is_selecting)
        assert pointer.route.phase is PointerPhase.CLOSED
        assert not pointer.route.admit(handle, lambda: True)
    finally:
        service.cancel()


@pytest.mark.parametrize("fault", ["none", "nonclient", "target", "wparam"])
def test_actual_native_handler_requires_owned_client_message(
    qtbot: QtBot,
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    """Decode the actual message structure, without sending an OS cursor message."""
    session = Session()
    monkeypatch.setattr(
        "verdiclip.capture.service.NativePointerSession", lambda: session
    )
    broker = Broker(qapp)
    service = CaptureService(FakeDesktop(), FakeWindows(), input_broker=broker)
    service.enable_native_pointer()
    try:
        service.capture_region()
        qtbot.waitUntil(lambda: bool(service.overlays))
        overlay = service.overlays[0]
        handle = int(overlay.winId())
        packet = w.MSG()
        packet.message = 0x20
        packet.hWnd = handle + 1 if fault == "target" else handle
        packet.wParam = handle + 1 if fault == "wparam" else handle
        packet.lParam = 2 if fault == "nonclient" else 1
        overlay.nativeEvent(b"windows_generic_MSG", ctypes.addressof(packet))
        overlay.nativeEvent(b"windows_generic_MSG", ctypes.addressof(packet))
        qapp.processEvents()
        assert session.observations == ([handle] if fault == "none" else [])
    finally:
        service.cancel()
        qtbot.waitUntil(lambda: not service.is_selecting)
