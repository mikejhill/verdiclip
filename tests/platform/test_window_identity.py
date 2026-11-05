"""Native window generation queries against fake DLLs, with no desktop access."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from typing import cast

import pytest
from tests.capture.input_dll_fixture import InputDllFixture

from verdiclip.exceptions import PlatformError
from verdiclip.platform.window_identity import (
    FILETIME_TO_DATETIME_TICKS,
    FileTime,
    Win32WindowIdentity,
)


class IdentityFixture(InputDllFixture):
    """Write exact native output buffers and record read-only handle ownership."""

    TICKS = 129_000_000_000_000_001

    def __init__(self) -> None:
        super().__init__()
        self.processes = [101, 101]
        self.closed: list[int] = []
        self.handlers.update(
            {
                "GetWindowThreadProcessId": self._pid,
                "OpenProcess": self._open,
                "GetProcessTimes": self._times,
                "CloseHandle": self._close,
            }
        )

    def _pid(self, *args: object) -> int:
        process = self.processes.pop(0)
        ctypes.cast(
            self._address(args[1]), ctypes.POINTER(w.DWORD)
        ).contents.value = process
        return 1

    @staticmethod
    def _open(*args: object) -> int:
        assert args == (0x1000, 0, 101)
        return 77

    def _times(self, *args: object) -> int:
        created = ctypes.cast(self._address(args[1]), ctypes.POINTER(FileTime)).contents
        created.low, created.high = self.TICKS & 0xFFFFFFFF, self.TICKS >> 32
        return 1

    def _close(self, *args: object) -> int:
        self.closed.append(cast(int, args[0]))
        return 1


@pytest.fixture
def identity(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Win32WindowIdentity, IdentityFixture]:
    """Replace every DLL before constructing any platform adapter."""
    dll = IdentityFixture()
    monkeypatch.setattr("verdiclip.platform.window_identity.sys.platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: dll, raising=False)
    return Win32WindowIdentity(), dll


def test_generation_preserves_start_tick_precision_and_limited_access(
    identity: tuple[Win32WindowIdentity, IdentityFixture],
) -> None:
    """The schema exactly matches the helper's HWND/PID/.NET-ticks identity."""
    reader, dll = identity
    assert reader.read(55) == f"55:101:{dll.TICKS + FILETIME_TO_DATETIME_TICKS}"
    assert dll.closed == [77]
    assert dll.calls.count("GetWindowThreadProcessId") == 2


def test_reused_handle_during_generation_query_is_refused(
    identity: tuple[Win32WindowIdentity, IdentityFixture],
) -> None:
    """A PID change between native reads cannot produce an authoritative identity."""
    reader, dll = identity
    dll.processes = [101, 102]
    assert reader.read(55) is None
    assert dll.closed == [77]


@pytest.mark.parametrize(
    "failure",
    ["GetWindowThreadProcessId", "OpenProcess", "GetProcessTimes", "CloseHandle"],
)
def test_unknown_generation_does_not_enable_scrolling(
    identity: tuple[Win32WindowIdentity, IdentityFixture], failure: str
) -> None:
    """Denied or failed reads remain ordinary-capture-only, with no elevation."""
    reader, dll = identity
    dll.fail = failure
    assert reader.read(55) is None
    if failure == "GetProcessTimes":
        assert dll.closed == [77]


def test_absent_window_never_opens_a_process(
    identity: tuple[Win32WindowIdentity, IdentityFixture],
) -> None:
    """Missing handles stay unavailable without a fabricated PID or timestamp."""
    reader, dll = identity
    assert reader.read(0) is None
    assert "OpenProcess" not in dll.calls


def test_generation_adapter_is_explicitly_windows_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-Windows use fails before native DLL loading."""
    monkeypatch.setattr("verdiclip.platform.window_identity.sys.platform", "linux")
    with pytest.raises(PlatformError, match="Windows"):
        Win32WindowIdentity()
