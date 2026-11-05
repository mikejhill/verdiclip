"""Native driver terminal-event and ownership contracts without a desktop."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from verdiclip.platform.tab_drag import NativeTabDrag, Win32CaptureApi


class Receiver:
    """Observe outcomes and assert input was released before every callback."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def moved(self, position: QPoint) -> None:
        """Record movement independently of structural ownership."""
        del position
        self.events.append("move")

    def released(self, position: QPoint) -> None:
        """Record an actual release."""
        del position
        self.events.append("release")

    def cancelled(self) -> None:
        """Record a cancellation rather than inferring a release."""
        self.events.append("cancel")


class Api:
    """Model a single current-thread native mouse-capture owner."""

    def __init__(self) -> None:
        self.owner = 0
        self.available = True
        self.releases: list[int] = []

    def acquire(self, handle: int) -> bool:
        """Fail without pretending a lease was acquired."""
        self.owner = handle if self.available else 0
        return self.available

    def release(self, handle: int) -> None:
        """Record release even when external capture has replaced the owner."""
        self.releases.append(handle)
        if self.owner == handle:
            self.owner = 0

    def position(self, handle: int, parameter: int) -> QPoint:
        """Return a deterministic event point independently of the live cursor."""
        del handle, parameter
        return QPoint(10, 20)


@pytest.mark.parametrize(
    ("message", "parameter", "outcome", "consumed"),
    [
        (0x0202, 0, "release", False),
        (0x0100, 0x1B, "cancel", True),
        (0x0215, 0, "cancel", False),
        (0x001F, 0, "cancel", False),
        (0x0204, 0, "cancel", False),
        (0x0312, 0, "cancel", False),
    ],
)
def test_native_terminal_outcome_is_exactly_once(
    qapp: QApplication,
    message: int,
    parameter: int,
    outcome: str,
    consumed: bool,  # noqa: FBT001 - parameterized receipt
) -> None:
    """Duplicate and late native events cannot create a second outcome."""
    del qapp
    receiver, api = Receiver(), Api()
    driver = NativeTabDrag(receiver, api)
    assert driver.begin(42)
    assert driver.dispatch(42, message, parameter) is consumed
    assert api.owner == 0
    assert api.releases == [42]
    assert receiver.events == [outcome]
    driver.dispatch(42, 0x0202)
    driver.cancel()
    assert receiver.events == [outcome]
    assert not driver.active


def test_native_filter_handles_real_msg_layout_and_leaves_other_messages(
    qapp: QApplication,
) -> None:
    """Decode the Windows hWnd member and keep unrelated events untouched."""
    del qapp
    receiver, api = Receiver(), Api()
    driver = NativeTabDrag(receiver, api)
    assert not driver.begin(0)
    assert driver.begin(42)
    assert not driver.begin(99)
    assert not driver.dispatch(99, 0x0202)
    assert not driver.dispatch(42, 0x0100, 65)
    assert not driver.dispatch(42, 0x9999)
    assert driver.dispatch(42, 0x0200)
    assert receiver.events == ["move"]
    message = ctypes.wintypes.MSG()
    message.hWnd = 42
    message.message = 0x0202
    assert driver.nativeEventFilter(b"other", ctypes.addressof(message)) == (False, 0)
    assert driver.nativeEventFilter(b"windows_generic_MSG", 0) == (False, 0)
    assert driver.nativeEventFilter(
        b"windows_generic_MSG", ctypes.addressof(message)
    ) == (False, 0)
    assert receiver.events == ["move", "release"]
    assert api.owner == 0


def test_acquisition_failure_and_foreign_hotkey_cancel(qapp: QApplication) -> None:
    """Thread hotkeys cancel even though they have no source-window HWND."""
    del qapp
    receiver, api = Receiver(), Api()
    api.available = False
    driver = NativeTabDrag(receiver, api)
    assert not driver.begin(42)
    assert not driver.active
    api.available = True
    assert driver.begin(42)
    assert not driver.dispatch(0, 0x0312)
    assert receiver.events == ["cancel"]


def test_win32_release_never_releases_another_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate the ctypes wrapper's ownership checks with a fake DLL."""

    class Function:
        def __init__(self, result: int = 0) -> None:
            self.argtypes: object = None
            self.restype: object = None
            self.result = result
            self.calls: list[tuple[object, ...]] = []

        def __call__(self, *arguments: object) -> int:
            self.calls.append(arguments)
            return self.result

    class Dll:
        def __init__(self) -> None:
            self.SetCapture = Function()
            self.GetCapture = Function(42)
            self.ReleaseCapture = Function(1)
            self.ClientToScreen = Function(1)

    dll = Dll()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: dll, raising=False)
    api = Win32CaptureApi()
    assert api.acquire(42)
    assert not api.acquire(99)
    api.release(99)
    assert dll.ReleaseCapture.calls == []
    api.release(42)
    assert dll.ReleaseCapture.calls == [()]
