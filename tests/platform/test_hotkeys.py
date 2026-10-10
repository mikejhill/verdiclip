"""Verify shortcut parsing, dispatch, and native registration boundaries."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import Mock

import pytest
import shiboken6
from PySide6.QtCore import QByteArray, QCoreApplication
from pytestqt.qtbot import QtBot

from verdiclip.exceptions import HotkeyError, PlatformError
from verdiclip.platform.hotkeys import (
    Hotkey,
    HotkeyService,
    Modifier,
    Win32HotkeyApi,
    _NativeHotkeyFilter,
)


class FakeHotkeyApi:
    """Record registration requests without reserving OS shortcuts."""

    def __init__(self) -> None:
        """Initialize controllable boundary results."""
        self.accept = True
        self.release = True
        self.calls: list[tuple[int, int, int, int]] = []
        self.removed: list[tuple[int, int]] = []

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
        """Record the exact shortcut and return the configured result."""
        self.calls.append((hwnd, hotkey_id, modifiers, vk))
        return self.accept

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        """Record release attempts and return the configured result."""
        self.removed.append((hwnd, hotkey_id))
        return self.release


class TestHotkey:
    """Canonical parsing and Win32 key representations."""

    @pytest.mark.parametrize(
        ("text", "canonical", "display"),
        [
            (" Ctrl + PrtSc ", "ctrl+print_screen", "Ctrl+PrtSc"),
            ("alt+printscreen", "alt+print_screen", "Alt+PrtSc"),
            ("shift+control+print", "ctrl+shift+print_screen", "Ctrl+Shift+PrtSc"),
            ("super+cmd+shift+s", "shift+win+s", "Shift+Win+S"),
            ("CTRL+ctrl+A", "ctrl+a", "Ctrl+A"),
            ("page_up", "page_up", "Page Up"),
            ("f24", "f24", "F24"),
        ],
    )
    def test_parse(self, text: str, canonical: str, display: str) -> None:
        """Aliases and whitespace produce stable round-tripping labels."""
        hotkey = Hotkey.parse(text)

        assert str(hotkey) == canonical
        assert hotkey.display_text == display
        assert Hotkey.parse(str(hotkey)) == hotkey

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("", "empty"),
            ("  ", "empty"),
            ("ctrl+shift", "found 0"),
            ("a+b", "found 2"),
            ("unknown", "Unknown"),
            ("ctrl+", "Unknown"),
            ("f25", "Unknown"),
            ("++", "Unknown"),
        ],
    )
    def test_invalid(self, text: str, message: str) -> None:
        """Malformed shortcuts explain why they cannot be parsed."""
        with pytest.raises(HotkeyError, match=message):
            Hotkey.parse(text)

    def test_direct_invalid(self) -> None:
        """Direct values reject noncanonical keys."""
        with pytest.raises(HotkeyError, match="Unknown"):
            Hotkey(frozenset(), "PrtSc")

    @pytest.mark.parametrize(
        ("key", "vk"),
        [
            ("print_screen", 0x2C),
            ("a", 65),
            ("z", 90),
            ("0", 48),
            ("9", 57),
            ("f1", 0x70),
            ("f24", 0x87),
            ("space", 0x20),
            ("insert", 0x2D),
            ("delete", 0x2E),
            ("home", 0x24),
            ("end", 0x23),
            ("pause", 0x13),
            ("scroll_lock", 0x91),
        ],
    )
    def test_virtual_keys(self, key: str, vk: int) -> None:
        """Named, numeric, alphabetic, and function keys use Win32 codes."""
        hotkey = Hotkey.parse(key)

        assert hotkey.virtual_key == vk
        assert hotkey.win32_modifiers == 0x4000

    def test_all_modifiers(self) -> None:
        """All modifier bits combine with MOD_NOREPEAT in canonical order."""
        hotkey = Hotkey(frozenset(Modifier), "a")

        assert hotkey.win32_modifiers == 0x400F
        assert str(hotkey) == "ctrl+alt+shift+win+a"


class TestHotkeyService:
    """Exact registration and action signal lifecycles."""

    def test_dispatch_replace_close(self, qtbot: QtBot) -> None:
        """Replacement removes old dispatch and closing releases all shortcuts."""
        api = FakeHotkeyApi()
        service = HotkeyService(api)
        hotkey = Hotkey.parse("ctrl+a")

        try:
            assert service.register("capture", hotkey)
            with qtbot.waitSignal(service.triggered) as signal:
                assert service.handle_hotkey_message(1)
            assert signal.args == ["capture"]
            assert api.calls == [(0, 1, 0x4002, 65)]
            assert service.register("capture", Hotkey.parse("b"))
            assert not service.handle_hotkey_message(1)
            assert service.register("other", hotkey)
            assert service.registered == {"capture": Hotkey.parse("b"), "other": hotkey}
            service.unregister_all()
            assert not service.registered
            assert api.removed == [(0, 1), (0, 2), (0, 3)]
        finally:
            service.close()
        service.close()
        with pytest.raises(HotkeyError, match="after close"):
            service.register("capture", hotkey)

    def test_failure_recovery(
        self, qtbot: QtBot, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Conflicts are exposed and a subsequent success clears failure state."""
        api = FakeHotkeyApi()
        service = HotkeyService(api)

        try:
            assert service.register("capture", Hotkey.parse("a"))
            api.accept = False
            assert not service.register("capture", Hotkey.parse("b"))
            assert service.failed == frozenset({"capture"})
            assert not service.registered
            assert not service.handle_hotkey_message(2)
            assert "unavailable" in caplog.text
            api.accept = True
            assert service.register("capture", Hotkey.parse("b"))
            assert not service.failed
            with qtbot.waitSignal(service.triggered):
                assert service.handle_hotkey_message(3)
        finally:
            service.close()

    @pytest.mark.usefixtures("qtbot")
    def test_release_failure_is_logged_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failed unregister is logged and the binding is still forgotten."""
        api = FakeHotkeyApi()
        service = HotkeyService(api)
        assert service.register("capture", Hotkey.parse("a"))
        api.release = False

        service.close()

        assert "capture" not in service.registered
        assert "did not unregister" in caplog.text

    def test_native_filter(self, qtbot: QtBot) -> None:
        """Only recognized generic WM_HOTKEY messages dispatch a signal."""
        service = HotkeyService(FakeHotkeyApi())
        event_filter = _NativeHotkeyFilter(service)
        message = ctypes.wintypes.MSG()
        address = ctypes.addressof(message)

        try:
            assert service.register("capture", Hotkey.parse("a"))
            assert event_filter.nativeEventFilter(b"other", address) == (False, 0)
            assert event_filter.nativeEventFilter(b"windows_generic_MSG", 0) == (
                False,
                0,
            )
            assert event_filter.nativeEventFilter(b"windows_generic_MSG", address) == (
                False,
                0,
            )
            message.message = 0x0312
            message.wParam = 999
            assert event_filter.nativeEventFilter(b"windows_generic_MSG", address) == (
                False,
                0,
            )
            message.wParam = 1
            with qtbot.waitSignal(service.triggered) as signal:
                assert event_filter.nativeEventFilter(
                    QByteArray(b"windows_generic_MSG"), address
                ) == (True, 0)
            assert signal.args == ["capture"]
        finally:
            service.close()

    def test_native_filter_accepts_qt_voidptr(self, qtbot: QtBot) -> None:
        """Qt passes the MSG pointer as a shiboken VoidPtr; it must still dispatch."""
        service = HotkeyService(FakeHotkeyApi())
        event_filter = _NativeHotkeyFilter(service)
        message = ctypes.wintypes.MSG()
        message.message = 0x0312
        message.wParam = 1
        pointer = shiboken6.VoidPtr(ctypes.addressof(message))

        try:
            assert service.register("capture", Hotkey.parse("a"))
            with qtbot.waitSignal(service.triggered):
                handled = event_filter.nativeEventFilter(
                    QByteArray(b"windows_generic_MSG"),
                    pointer,  # ty: ignore[invalid-argument-type]  # Qt's real runtime type
                )
            assert handled == (True, 0)
        finally:
            service.close()

    def test_application_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing application produces an actionable initialization error."""
        monkeypatch.setattr(QCoreApplication, "instance", Mock(return_value=None))

        with pytest.raises(HotkeyError, match="QCoreApplication"):
            HotkeyService(FakeHotkeyApi())

    def test_default_adapter(
        self, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The service constructs its native adapter when none is injected."""
        monkeypatch.setattr("verdiclip.platform.hotkeys.Win32HotkeyApi", FakeHotkeyApi)

        service = HotkeyService()
        try:
            assert service.register("capture", Hotkey.parse("a"))
            with qtbot.waitSignal(service.triggered):
                assert service.handle_hotkey_message(1)
        finally:
            service.close()


class TestWin32HotkeyApi:
    """Native registration signatures without real global key reservations."""

    def test_boundary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Native functions receive thread identifiers and exact masks."""
        library = Mock()
        library.RegisterHotKey.return_value = 1
        library.UnregisterHotKey.return_value = 0
        monkeypatch.setattr("verdiclip.platform.hotkeys.sys.platform", "win32")
        monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=library), raising=False)

        api = Win32HotkeyApi()

        assert api.register(0, 1, 0x4002, 65)
        assert not api.unregister(0, 1)
        library.RegisterHotKey.assert_called_once_with(0, 1, 0x4002, 65)
        library.UnregisterHotKey.assert_called_once_with(0, 1)
        assert library.RegisterHotKey.restype is ctypes.wintypes.BOOL
        assert library.UnregisterHotKey.argtypes == [ctypes.wintypes.HWND, ctypes.c_int]

    def test_non_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-Windows construction fails without accessing native libraries."""
        monkeypatch.setattr("verdiclip.platform.hotkeys.sys.platform", "linux")

        with pytest.raises(PlatformError, match="Windows"):
            Win32HotkeyApi()
