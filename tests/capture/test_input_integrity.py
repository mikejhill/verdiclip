"""Read-only input eligibility executes against fake DLLs, never real tokens."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import sys
from typing import cast

import pytest
from tests.capture.input_dll_fixture import InputDllFixture

from verdiclip.capture.input_integrity import (
    GuiThreadInfo,
    InputIntegrityGuard,
    MandatoryLabel,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows ABI signatures"
)


class IntegrityDllFixture(InputDllFixture):
    """Caller-owned token label buffers and explicit GUI recipients."""

    def __init__(self) -> None:
        super().__init__()
        self.own_level = 0x2000
        self.levels = {10: 0x2000, 20: 0x3000, 30: 0x1000}
        self.windows = {1: 10, 2: 20, 3: 30}
        self.hit = 1
        self.capture = self.menu = self.focus = 0
        self.closed: list[int] = []
        self.handlers.update(
            {
                "GetCurrentProcess": lambda *_args: 1,
                "GetGUIThreadInfo": self._gui,
                "WindowFromPhysicalPoint": lambda *_args: self.hit,
                "GetWindowThreadProcessId": self._pid,
                "OpenProcess": lambda *args: cast(int, args[2]) + 100,
                "OpenProcessToken": self._token,
                "GetTokenInformation": self._label,
                "IsValidSid": lambda *_args: 1,
                "GetSidSubAuthorityCount": self._count,
                "GetSidSubAuthority": self._authority,
                "CloseHandle": self._close,
            }
        )

    def _gui(self, *args: object) -> int:
        info = ctypes.cast(
            self._address(args[1]), ctypes.POINTER(GuiThreadInfo)
        ).contents
        info.active, info.focus, info.capture, info.menu = (
            1,
            self.focus,
            self.capture,
            self.menu,
        )
        return 1

    def _pid(self, *args: object) -> int:
        process = self.windows.get(cast(int, args[0]), 0)
        self._uint(args[1]).contents.value = process
        return int(bool(process))

    def _token(self, *args: object) -> int:
        handle = ctypes.cast(self._address(args[2]), ctypes.POINTER(w.HANDLE))
        handle.contents.value = cast(int, args[0]) + 1000
        return 1

    def _label(self, *args: object) -> int:
        token = cast(w.HANDLE, args[0]).value
        level = self.own_level if token == 1001 else self.levels[int(token or 0) - 1100]
        address = ctypes.cast(self._address(args[2]), ctypes.c_void_p).value
        assert address is not None
        sid = address + ctypes.sizeof(MandatoryLabel)
        label = MandatoryLabel.from_address(address)
        label.sid = sid
        ctypes.c_ubyte.from_address(sid + 7).value = 1
        w.DWORD.from_address(sid + 8).value = level
        self._uint(args[4]).contents.value = ctypes.sizeof(MandatoryLabel) + 12
        return 1

    @staticmethod
    def _count(*args: object) -> ctypes._Pointer[w.BYTE]:
        return ctypes.cast(cast(int, args[0]) + 7, ctypes.POINTER(w.BYTE))

    @staticmethod
    def _authority(*args: object) -> ctypes._Pointer[w.DWORD]:
        return ctypes.cast(cast(int, args[0]) + 8, ctypes.POINTER(w.DWORD))

    def _close(self, *args: object) -> int:
        handle = args[0]
        self.closed.append(
            int(handle.value or 0)
            if isinstance(handle, w.HANDLE)
            else cast(int, handle)
        )
        return 1


@pytest.fixture
def guard(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InputIntegrityGuard, IntegrityDllFixture]:
    """Replace DLL loading before constructing the production read adapter."""
    dll = IntegrityDllFixture()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: dll)
    return InputIntegrityGuard(), dll


@pytest.mark.parametrize(
    ("hit", "expected"), [(1, True), (2, False), (3, True), (0, False), (999, False)]
)
def test_uncaptured_pointer_recipient_is_checked(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture], hit: int, *, expected: bool
) -> None:
    """A medium foreground cannot hide a higher-integrity mouse recipient."""
    api, dll = guard
    dll.hit = hit
    assert api.permits(1) is expected
    assert dll.closed


@pytest.mark.parametrize("recipient", ["capture", "focus", "menu"])
def test_each_input_owner_has_its_own_integrity_check(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture], recipient: str
) -> None:
    """Capture, keyboard focus and menu ownership need independent eligibility."""
    api, dll = guard
    setattr(dll, recipient, 2)
    assert not api.permits(1)
    setattr(dll, recipient, 3)
    assert api.permits(1)


def test_owned_transparent_overlay_cannot_mask_unknown_underlying_recipient(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture],
) -> None:
    """Decline an ambiguous hit instead of assuming our overlay receives input."""
    api, dll = guard
    dll.handlers["GetCurrentProcessId"] = lambda *_args: 10
    dll.handlers["GetWindowLongW"] = lambda *_args: 0x20
    assert not api.permits(1)


def test_foreign_transparent_window_cannot_mask_underlying_recipient(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture],
) -> None:
    """Mouse pass-through is independent of which process owns the window."""
    api, dll = guard
    dll.hit = 3
    dll.handlers["GetCurrentProcessId"] = lambda *_args: 999
    dll.handlers["GetWindowLongW"] = lambda *_args: 0x20
    assert not api.permits(1)


def test_unknown_extended_style_is_not_permission(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture],
) -> None:
    """A failed zero-valued style query is distinguished from a valid zero style."""
    api, dll = guard

    def unknown(*_args: object) -> int:
        ctypes.set_last_error(5)
        return 0

    dll.handlers["GetWindowLongW"] = unknown
    assert not api.permits(1)
    ctypes.set_last_error(0)


def test_existing_capture_owner_takes_precedence_over_uncaptured_hit(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture],
) -> None:
    """Do not reject an irrelevant high-integrity window beneath captured input."""
    api, dll = guard
    dll.hit, dll.capture = 2, 3
    assert api.permits(1)
    assert "WindowFromPhysicalPoint" not in dll.calls


@pytest.mark.parametrize(
    "failure",
    [
        "GetGUIThreadInfo",
        "GetPhysicalCursorPos",
        "OpenProcess",
        "OpenProcessToken",
        "GetTokenInformation",
        "IsValidSid",
        "GetWindowThreadProcessId",
        "CloseHandle",
    ],
)
def test_unknown_recipient_or_failed_handle_cleanup_refuses_ownership(
    guard: tuple[InputIntegrityGuard, IntegrityDllFixture], failure: str
) -> None:
    """No failure becomes permission, and no privilege change API is called."""
    api, dll = guard
    dll.fail = failure
    assert not api.permits(1)
    assert not any(
        name in dll.calls for name in ("SetTokenInformation", "AdjustTokenPrivileges")
    )
