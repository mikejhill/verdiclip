"""Explicit Raw Input pointer semantics; no device guessing from hook flags."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from verdiclip.capture.input_routing import MoveMode
from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point, Rect

WM_RAW_INPUT: Final = 0x00FF


class RawInputDevice(ctypes.Structure):
    """One process-local RAWINPUTDEVICE registration."""

    _fields_ = (
        ("page", w.USHORT),
        ("usage", w.USHORT),
        ("flags", w.DWORD),
        ("target", w.HWND),
    )


class RawHeader(ctypes.Structure):
    """RAWINPUTHEADER; device identity is never persisted or logged."""

    _fields_ = (
        ("kind", w.DWORD),
        ("size", w.DWORD),
        ("device", w.HANDLE),
        ("parameter", w.WPARAM),
    )


class RawButtons(ctypes.Structure):
    """The named USHORT pair in RAWMOUSE's aligned union."""

    _fields_ = (("flags", w.USHORT), ("data", w.USHORT))


class RawButtonUnion(ctypes.Union):
    """Preserve Win32's DWORD alignment around the button flags."""

    _fields_ = (("combined", w.ULONG), ("parts", RawButtons))


class RawMouse(ctypes.Structure):
    """Raw relative/absolute flags and delta, with independent X-button bits."""

    _fields_ = (
        ("flags", w.USHORT),
        ("buttons", RawButtonUnion),
        ("raw_buttons", w.ULONG),
        ("x", w.LONG),
        ("y", w.LONG),
        ("extra", w.ULONG),
    )


class RawMouseMessage(ctypes.Structure):
    """The fixed mouse variant of RAWINPUT."""

    _fields_ = (("header", RawHeader), ("mouse", RawMouse))


@dataclass(frozen=True, slots=True)
class RawPointer:
    """Authoritative movement interpretation and button transitions in one report."""

    point: Point
    mode: MoveMode
    buttons: tuple[tuple[int, int], ...]
    wheel: bool


class RawPointerDecoder:
    """Pure conversion used by the native source and hardware-free fixtures."""

    ABSOLUTE: Final = 1
    VIRTUAL: Final = 2
    NORMALIZED_MAX: Final = 65535
    # Raw bit, low-level equivalent, independent extra-button identity.
    BUTTONS: Final = (
        (1, 0x201, 0),
        (2, 0x202, 0),
        (4, 0x204, 0),
        (8, 0x205, 0),
        (16, 0x207, 0),
        (32, 0x208, 0),
        (64, 0x20B, 1),
        (128, 0x20C, 1),
        (256, 0x20B, 2),
        (512, 0x20C, 2),
    )

    @classmethod
    def decode(cls, mouse: RawMouse, *, primary: Rect, virtual: Rect) -> RawPointer:
        """Absolute packets map normalized coordinates; relative packets stay deltas."""
        mode = MoveMode.RELATIVE_DELTA
        point = Point(mouse.x, mouse.y)
        if mouse.flags & cls.ABSOLUTE:
            if not (
                0 <= mouse.x <= cls.NORMALIZED_MAX
                and 0 <= mouse.y <= cls.NORMALIZED_MAX
            ):
                raise PlatformError("Absolute pointer coordinates are unavailable")
            area = virtual if mouse.flags & cls.VIRTUAL else primary
            if area.width < 1 or area.height < 1:
                raise PlatformError("Pointer display geometry is unavailable")
            point = Point(
                area.x + round(mouse.x * (area.width - 1) / cls.NORMALIZED_MAX),
                area.y + round(mouse.y * (area.height - 1) / cls.NORMALIZED_MAX),
            )
            mode = MoveMode.ABSOLUTE
        flags = int(mouse.buttons.parts.flags)
        buttons = tuple(
            (message, identity) for bit, message, identity in cls.BUTTONS if flags & bit
        )
        return RawPointer(point, mode, buttons, wheel=bool(flags & 0xC00))


type RawPointerCallback = Callable[[RawPointer], None]


class RawPointerSource:
    """Own an invisible message-only window and one temporary mouse registration.

    Refuse an existing mouse registration in this process. Never use NOLEGACY,
    overwrite another component's registration, or read keyboard Raw Input.
    """

    MAX_REGISTRATIONS = 64
    MAX_BYTES = 65_536
    ERROR_RESULT = 0xFFFFFFFF
    INPUT_COMMAND = 0x10000003
    MOUSE_USAGE = 2

    def __init__(self, api: ctypes.CDLL, module: int) -> None:
        """Bind APIs without registering or creating a window."""
        self._api, self._module = api, module
        self._window = 0
        self._owned = False
        self._callback: RawPointerCallback | None = None
        api.GetRegisteredRawInputDevices.argtypes = [
            ctypes.POINTER(RawInputDevice),
            ctypes.POINTER(w.UINT),
            w.UINT,
        ]
        api.GetRegisteredRawInputDevices.restype = w.UINT
        api.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(RawInputDevice),
            w.UINT,
            w.UINT,
        ]
        api.RegisterRawInputDevices.restype = w.BOOL
        api.GetRawInputData.argtypes = [
            w.HANDLE,
            w.UINT,
            w.LPVOID,
            ctypes.POINTER(w.UINT),
            w.UINT,
        ]
        api.GetRawInputData.restype = w.UINT
        api.CreateWindowExW.argtypes = [
            w.DWORD,
            w.LPCWSTR,
            w.LPCWSTR,
            w.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            w.HWND,
            w.HMENU,
            w.HINSTANCE,
            w.LPVOID,
        ]
        api.CreateWindowExW.restype = w.HWND
        api.DestroyWindow.argtypes = [w.HWND]
        api.DestroyWindow.restype = w.BOOL
        api.GetSystemMetrics.argtypes = [ctypes.c_int]
        api.GetSystemMetrics.restype = ctypes.c_int

    def _registrations(self) -> list[RawInputDevice]:
        count = w.UINT()
        size = ctypes.sizeof(RawInputDevice)
        if (
            self._api.GetRegisteredRawInputDevices(None, ctypes.byref(count), size)
            == self.ERROR_RESULT
            or count.value > self.MAX_REGISTRATIONS
        ):
            raise PlatformError("Pointer registration state is unavailable")
        if not count.value:
            return []
        values = (RawInputDevice * count.value)()
        result = int(
            self._api.GetRegisteredRawInputDevices(values, ctypes.byref(count), size)
        )
        if result == self.ERROR_RESULT or result > len(values):
            raise PlatformError("Pointer registration state changed")
        return [values[index] for index in range(result)]

    def start(self, callback: RawPointerCallback) -> None:
        """Reserve only an unused process-local mouse class, with no visible window."""
        if any(
            value.page == 1 and value.usage == self.MOUSE_USAGE
            for value in self._registrations()
        ):
            raise PlatformError("Another component already owns pointer input")
        window = self._api.CreateWindowExW(
            0, "STATIC", None, 0, 0, 0, 0, 0, -3, None, self._module, None
        )
        if not window:
            raise ctypes.WinError(ctypes.get_last_error())
        self._window = int(window)
        registration = RawInputDevice(1, 2, 0x100, window)
        if not self._api.RegisterRawInputDevices(
            ctypes.byref(registration), 1, ctypes.sizeof(registration)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        self._owned = True
        self._callback = callback

    def receive(self, handle: int) -> None:
        """Read a bounded mouse-only report; no device names or identifiers leave."""
        size = w.UINT()
        header_size = ctypes.sizeof(RawHeader)
        result = int(
            self._api.GetRawInputData(
                handle, self.INPUT_COMMAND, None, ctypes.byref(size), header_size
            )
        )
        if (
            result == self.ERROR_RESULT
            or not ctypes.sizeof(RawMouseMessage) <= size.value <= self.MAX_BYTES
        ):
            raise PlatformError("Pointer report is unavailable")
        buffer = ctypes.create_string_buffer(size.value)
        result = int(
            self._api.GetRawInputData(
                handle, self.INPUT_COMMAND, buffer, ctypes.byref(size), header_size
            )
        )
        if result == self.ERROR_RESULT or result != size.value:
            raise PlatformError("Pointer report changed during reading")
        packet = RawMouseMessage.from_buffer_copy(buffer)
        if packet.header.kind != 0 or packet.header.size != result:
            raise PlatformError("Unsupported pointer report")
        primary = Rect(
            0, 0, self._api.GetSystemMetrics(0), self._api.GetSystemMetrics(1)
        )
        virtual = Rect(*(self._api.GetSystemMetrics(code) for code in (76, 77, 78, 79)))
        event = RawPointerDecoder.decode(packet.mouse, primary=primary, virtual=virtual)
        if self._callback is not None:
            self._callback(event)

    def discard_pending(self) -> None:
        """Discard pre-lease notifications before installing input suppression."""
        message = w.MSG()
        for _index in range(256):
            if not self._api.PeekMessageW(
                ctypes.byref(message), self._window, 0xFF, 0xFF, 1
            ):
                return
            self._api.DefWindowProcW(
                message.hWnd, message.message, message.wParam, message.lParam
            )
        raise PlatformError("Pointer input is busy; try capture again")

    def owns_window(self, handle: int) -> bool:
        """Only messages for the newly created hidden receiver belong to this source."""
        return bool(self._window) and handle == self._window

    def close(self) -> bool:
        """Remove only this source's registration and its newly created receiver."""
        if self._owned:
            ours = any(
                value.page == 1
                and value.usage == self.MOUSE_USAGE
                and int(value.target or 0) == self._window
                for value in self._registrations()
            )
            if ours:
                removal = RawInputDevice(1, 2, 1, None)
                if not self._api.RegisterRawInputDevices(
                    ctypes.byref(removal), 1, ctypes.sizeof(removal)
                ):
                    return False
            self._owned = False
        if self._window:
            if not self._api.DestroyWindow(self._window):
                return False
            self._window = 0
        self._callback = None
        return True
