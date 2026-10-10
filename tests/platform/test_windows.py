"""Verify window eligibility, z-order selection, and native discovery calls."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from dataclasses import dataclass, field
from unittest.mock import Mock

import pytest

from verdiclip.exceptions import PlatformError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import Win32WindowApi, WindowInfo, WindowLocator


@dataclass(frozen=True, slots=True)
class FakeWindowApi:
    """Configurable window primitives without desktop dependencies."""

    hidden: frozenset[int] = frozenset()
    iconic: frozenset[int] = frozenset()
    cloaked: frozenset[int] = frozenset()
    styles: dict[int, int] = field(default_factory=dict)
    titles: dict[int, str] = field(default_factory=dict)
    bounds: dict[int, Rect] = field(default_factory=dict)
    foreground: int = 1

    def enumerate_handles(self) -> list[int]:
        """Return a stable topmost-first sequence."""
        return [1, 2, 3]

    def is_visible(self, handle: int) -> bool:
        """Return the configured visibility state."""
        return handle not in self.hidden

    def is_iconic(self, handle: int) -> bool:
        """Return the configured minimized state."""
        return handle in self.iconic

    def is_cloaked(self, handle: int) -> bool:
        """Return the configured DWM cloaking state."""
        return handle in self.cloaked

    def ex_style(self, handle: int) -> int:
        """Return configured extended styles."""
        return self.styles.get(handle, 0)

    def title(self, handle: int) -> str:
        """Return the configured caption."""
        return self.titles.get(handle, f"Window {handle}")

    def frame_bounds(self, handle: int) -> Rect:
        """Return physical bounds, including negative screen coordinates."""
        return self.bounds.get(handle, Rect(-100, -100, 200, 200))

    def foreground_handle(self) -> int:
        """Return the configured foreground handle."""
        return self.foreground


class TestWindowLocator:
    """Capture eligibility and topmost point lookup."""

    @pytest.mark.parametrize(
        "api",
        [
            FakeWindowApi(hidden=frozenset({2})),
            FakeWindowApi(iconic=frozenset({2})),
            FakeWindowApi(cloaked=frozenset({2})),
            FakeWindowApi(styles={2: 0x80}),
            FakeWindowApi(titles={2: ""}),
            FakeWindowApi(bounds={2: Rect(0, 0, 19, 20)}),
            FakeWindowApi(bounds={2: Rect(0, 0, 20, 19)}),
        ],
        ids=["hidden", "minimized", "cloaked", "tool", "untitled", "narrow", "short"],
    )
    def test_filters(self, api: FakeWindowApi) -> None:
        """Ineligible windows disappear without reordering surviving handles."""
        locator = WindowLocator(api)

        windows = locator.visible_windows()

        assert [window.handle for window in windows] == [1, 3]

    def test_exclusions_and_appwindow(self) -> None:
        """Explicit exclusions win and app-window flags permit tool windows."""
        api = FakeWindowApi(styles={2: 0x40080}, bounds={2: Rect(0, 0, 20, 20)})

        windows = WindowLocator(api).visible_windows(exclude=frozenset({1, 3}))

        assert windows == [WindowInfo(2, "Window 2", Rect(0, 0, 20, 20))]

    @pytest.mark.parametrize(
        "api",
        [FakeWindowApi(foreground=0), FakeWindowApi(hidden=frozenset({1}))],
        ids=["absent", "hidden"],
    )
    def test_no_foreground(self, api: FakeWindowApi) -> None:
        """Absent or ineligible foreground windows cannot be captured."""
        locator = WindowLocator(api)

        assert locator.foreground() is None

    def test_foreground(self) -> None:
        """The foreground result retains its title and physical bounds."""
        locator = WindowLocator(FakeWindowApi(foreground=2))

        assert locator.foreground() == WindowInfo(
            2, "Window 2", Rect(-100, -100, 200, 200)
        )

    @pytest.mark.parametrize("point", [Point(0, 0), Point(-100, -100), Point(100, 100)])
    def test_topmost(self, point: Point) -> None:
        """Overlapping windows select the first match even when it is larger."""
        top = WindowInfo(1, "Top", Rect(-100, -100, 200, 200))
        small = WindowInfo(2, "Small", Rect(-10, -10, 20, 20))

        assert WindowLocator.window_at(point, [top, small]) == top

    def test_no_point_match(self) -> None:
        """Empty sequences and points outside all windows have no match."""
        window = WindowInfo(1, "Top", Rect(0, 0, 20, 20))

        assert WindowLocator.window_at(Point(100, 100), [window]) is None
        assert WindowLocator.window_at(Point(0, 0), []) is None

    def test_default_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default constructor selects the native adapter."""
        monkeypatch.setattr("verdiclip.platform.windows.Win32WindowApi", FakeWindowApi)

        assert len(WindowLocator().visible_windows()) == 3


class NativeWindowBoundary:
    """Fake ctypes callbacks and output buffers at the native boundary."""

    @staticmethod
    def enumerate_windows(callback: object, _parameter: int) -> int:
        """Invoke a ctypes callback in native z-order."""
        if not callable(callback):
            msg = "Expected callable EnumWindows callback"
            raise TypeError(msg)
        callback(12, 0)
        callback(7, 0)
        return 1

    @staticmethod
    def dwm_attribute(
        _handle: int, attribute: int, output: ctypes.c_void_p, _size: int
    ) -> int:
        """Write a frame or cloaking flag through the native output pointer."""
        value = (
            ctypes.wintypes.DWORD(1)
            if attribute == 14
            else ctypes.wintypes.RECT(-20, -10, 80, 90)
        )
        ctypes.memmove(output, ctypes.byref(value), ctypes.sizeof(value))
        return 0

    @staticmethod
    def window_rect(_handle: int, output: ctypes.c_void_p) -> int:
        """Write fallback window bounds through the native output pointer."""
        value = ctypes.wintypes.RECT(1, 2, 31, 42)
        ctypes.memmove(output, ctypes.byref(value), ctypes.sizeof(value))
        return 1

    @staticmethod
    def window_text(
        _handle: int, buffer: ctypes.Array[ctypes.c_wchar], _size: int
    ) -> int:
        """Populate the caller's Unicode caption buffer."""
        buffer.value = "Native"
        return 6


@pytest.fixture
def native_window_library(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Provide fake user32 and DWM functions with native output semantics."""
    library = Mock()
    library.EnumWindows.side_effect = NativeWindowBoundary.enumerate_windows
    library.DwmGetWindowAttribute.side_effect = NativeWindowBoundary.dwm_attribute
    library.GetWindowRect.side_effect = NativeWindowBoundary.window_rect
    library.GetWindowTextLengthW.return_value = 6
    library.GetWindowTextW.side_effect = NativeWindowBoundary.window_text
    library.GetWindowLongW.return_value = 0x40000
    library.GetForegroundWindow.return_value = 12
    library.IsWindowVisible.return_value = 1
    library.IsIconic.return_value = 0
    monkeypatch.setattr("verdiclip.platform.windows.sys.platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=library), raising=False)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    return library


class TestWin32WindowApi:
    """Native signatures, output buffers, and error handling."""

    def test_discovery(self, native_window_library: Mock) -> None:
        """Enumeration, captions, DWM frames, and status use native primitives."""
        api = Win32WindowApi()

        assert api.enumerate_handles() == [12, 7]
        assert api.is_visible(12)
        assert not api.is_iconic(12)
        assert api.is_cloaked(12)
        assert api.ex_style(12) == 0x40000
        assert api.title(12) == "Native"
        assert api.frame_bounds(12) == Rect(-20, -10, 100, 100)
        assert api.foreground_handle() == 12
        assert native_window_library.GetForegroundWindow.restype is ctypes.wintypes.HWND
        native_window_library.GetWindowRect.assert_not_called()

    def test_failures_and_fallback(self, native_window_library: Mock) -> None:
        """Unavailable DWM frames fall back while native failures raise errors."""
        api = Win32WindowApi()
        native_window_library.DwmGetWindowAttribute.side_effect = None
        native_window_library.DwmGetWindowAttribute.return_value = -1

        assert api.frame_bounds(12) == Rect(1, 2, 30, 40)
        assert api.is_cloaked(12)
        native_window_library.GetWindowRect.side_effect = None
        native_window_library.GetWindowRect.return_value = 0
        with pytest.raises(PlatformError, match="frame bounds"):
            api.frame_bounds(12)
        native_window_library.EnumWindows.side_effect = None
        native_window_library.EnumWindows.return_value = 0
        with pytest.raises(PlatformError, match="EnumWindows"):
            api.enumerate_handles()
        native_window_library.GetForegroundWindow.return_value = None
        assert api.foreground_handle() == 0
        native_window_library.DwmGetWindowAttribute.return_value = 0
        assert not api.is_cloaked(12)

    def test_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-Windows construction fails without loading native libraries."""
        monkeypatch.setattr("verdiclip.platform.windows.sys.platform", "linux")

        with pytest.raises(PlatformError, match="Windows"):
            Win32WindowApi()

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop smoke test")
    def test_real_discovery(self) -> None:
        """Read-only desktop discovery returns a window list."""
        assert isinstance(WindowLocator().visible_windows(), list)
