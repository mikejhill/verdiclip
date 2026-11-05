"""A bounded Windows tab-drag input lease, independent of document ownership."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from typing import Protocol, override

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QCoreApplication,
    QPoint,
)
from PySide6.QtGui import QCursor, QGuiApplication

WM_HOTKEY = 0x0312
WM_MOUSEMOVE = 0x0200
WM_LBUTTONUP = 0x0202
WM_KEYDOWN = 0x0100
VK_ESCAPE = 0x1B


class DragReceiver(Protocol):
    """Normalized gesture events consumed by the tab controller."""

    def moved(self, position: QPoint) -> None:
        """Update a preview, without transferring ownership."""

    def released(self, position: QPoint) -> None:
        """Complete a positively observed native button release."""

    def cancelled(self) -> None:
        """Restore the source after cancellation or input loss."""


class CaptureApi(Protocol):
    """The small native boundary, injectable for deterministic tests."""

    def acquire(self, handle: int) -> bool:
        """Capture pointer input for a window on this GUI thread."""

    def release(self, handle: int) -> None:
        """Release only a lease still owned by this window."""

    def position(self, handle: int, parameter: int) -> QPoint:
        """Map the native event's signed client coordinates to Qt global units."""


class Win32CaptureApi:
    """SetCapture without hooks, polling, global registrations, or permissions."""

    def __init__(self) -> None:
        self._api = ctypes.WinDLL("user32", use_last_error=True)
        self._api.SetCapture.argtypes = (ctypes.wintypes.HWND,)
        self._api.SetCapture.restype = ctypes.wintypes.HWND
        self._api.GetCapture.argtypes = ()
        self._api.GetCapture.restype = ctypes.wintypes.HWND
        self._api.ReleaseCapture.argtypes = ()
        self._api.ReleaseCapture.restype = ctypes.wintypes.BOOL
        self._api.ClientToScreen.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.wintypes.POINT),
        )
        self._api.ClientToScreen.restype = ctypes.wintypes.BOOL

    def acquire(self, handle: int) -> bool:
        """Return success only after confirming actual capture ownership."""
        self._api.SetCapture(handle)
        return int(self._api.GetCapture() or 0) == handle

    def release(self, handle: int) -> None:
        """Never release capture acquired by a different input owner."""
        if int(self._api.GetCapture() or 0) == handle:
            self._api.ReleaseCapture()

    def position(self, handle: int, parameter: int) -> QPoint:
        """Use the release event rather than a later, possibly moved cursor."""
        point = ctypes.wintypes.POINT(
            ctypes.c_short(parameter & 0xFFFF).value,
            ctypes.c_short((parameter >> 16) & 0xFFFF).value,
        )
        if not self._api.ClientToScreen(handle, ctypes.byref(point)):
            return QCursor.pos()
        return NativeTabDrag.logical_position(int(point.x), int(point.y))


class NativeTabDrag(QAbstractNativeEventFilter):
    """Emit one terminal event, releasing input before invoking the receiver."""

    def __init__(self, receiver: DragReceiver, api: CaptureApi) -> None:
        super().__init__()
        self._receiver = receiver
        self._api = api
        self._handle = 0
        self._application: QCoreApplication | None = None

    @property
    def active(self) -> bool:
        """True only while this driver owns a gesture lease."""
        return self._handle != 0

    def begin(self, handle: int) -> bool:
        """Acquire and observe one drag, without running a nested event loop."""
        if self.active or not handle:
            return False
        application = QCoreApplication.instance()
        if application is None or not self._api.acquire(handle):
            return False
        self._handle = handle
        self._application = application
        application.installNativeEventFilter(self)
        return True

    def cancel(self) -> None:
        """Cancel idempotently; capture requests may call this synchronously."""
        if self.active:
            self._finish()
            self._receiver.cancelled()

    def _finish(self) -> None:
        """Invalidate before ReleaseCapture's synchronous capture-change event."""
        handle, self._handle = self._handle, 0
        application, self._application = self._application, None
        if application is not None:
            application.removeNativeEventFilter(self)
        self._api.release(handle)

    def dispatch(
        self,
        handle: int,
        message: int,
        parameter: int = 0,
        position: QPoint | None = None,
    ) -> bool:
        """Handle native messages; exposed independently of ctypes decoding."""
        if not self.active:
            return False
        # WM_HOTKEY must yield the lease before the existing hotkey filter runs.
        if message == WM_HOTKEY:
            self.cancel()
            return False
        if handle != self._handle:
            return False
        return self._dispatch_owned(message, parameter, position)

    def _dispatch_owned(
        self, message: int, parameter: int, position: QPoint | None
    ) -> bool:
        point = position if position is not None else QCursor.pos()
        if message == WM_MOUSEMOVE:  # WM_MOUSEMOVE
            self._receiver.moved(point)
            return True
        if message == WM_LBUTTONUP:  # WM_LBUTTONUP: the only successful terminal event
            self._finish()
            self._receiver.released(point)
            # Qt must also observe release to clear its implicit press/grab state.
            return False
        if message in {0x0215, 0x001F, 0x0204}:  # capture loss, cancel mode, right down
            self.cancel()
            return False
        if message == WM_KEYDOWN and parameter == VK_ESCAPE:  # Escape
            self.cancel()
            return True
        return False

    @staticmethod
    def logical_position(x: int, y: int) -> QPoint:
        """Normalize the event's physical point using its own monitor's scale."""
        for screen in QGuiApplication.screens():
            geometry = screen.geometry()
            ratio = screen.devicePixelRatio()
            if geometry.x() <= x < geometry.x() + round(
                geometry.width() * ratio
            ) and geometry.y() <= y < geometry.y() + round(geometry.height() * ratio):
                return QPoint(
                    geometry.x() + round((x - geometry.x()) / ratio),
                    geometry.y() + round((y - geometry.y()) / ratio),
                )
        return QPoint(x, y)

    @override
    def nativeEventFilter(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int
    ) -> tuple[bool, int]:
        """Normalize native messages without consuming unrelated app input."""
        event_bytes = (
            event_type.data()
            if isinstance(event_type, QByteArray)
            else bytes(event_type)
        )
        address = int(message)
        if (
            event_bytes not in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}
            or not address
        ):
            return False, 0
        native = ctypes.wintypes.MSG.from_address(address)
        position = (
            self._api.position(self._handle, int(native.lParam))
            if self.active
            and int(native.hWnd or 0) == self._handle
            and native.message in {WM_MOUSEMOVE, WM_LBUTTONUP}
            else None
        )
        return self.dispatch(
            int(native.hWnd or 0), int(native.message), int(native.wParam), position
        ), 0
