"""Tests for verdiclip.hotkeys.manager."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from pynput import keyboard

from verdiclip.hotkeys.manager import HotkeyManager, _parse_hotkey

if TYPE_CHECKING:
    from verdiclip.config import Config


class TestRegisterCallback:
    def test_register_callback(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        callback = MagicMock()
        manager.register("ctrl+print_screen", callback)
        assert len(manager._callbacks) == 1


class TestUnregisterCallback:
    def test_unregister_callback(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        callback = MagicMock()
        manager.register("ctrl+print_screen", callback)
        assert len(manager._callbacks) == 1

        manager.unregister("ctrl+print_screen")
        assert len(manager._callbacks) == 0


class TestParseHotkey:
    def test_parse_ctrl_print_screen(self) -> None:
        keys = _parse_hotkey("ctrl+print_screen")
        assert keyboard.Key.ctrl_l in keys
        assert keyboard.Key.print_screen in keys

    def test_parse_shift_modifier(self) -> None:
        keys = _parse_hotkey("shift+print_screen")
        assert keyboard.Key.shift in keys
        assert keyboard.Key.print_screen in keys

    def test_parse_alt_modifier(self) -> None:
        keys = _parse_hotkey("alt+print_screen")
        assert keyboard.Key.alt_l in keys

    def test_parse_single_char_key(self) -> None:
        keys = _parse_hotkey("ctrl+s")
        assert keyboard.Key.ctrl_l in keys
        assert keyboard.KeyCode.from_char("s") in keys

    def test_parse_bare_print_screen(self) -> None:
        keys = _parse_hotkey("print_screen")
        assert keys == {keyboard.Key.print_screen}

    def test_parse_triple_modifier(self) -> None:
        keys = _parse_hotkey("ctrl+shift+print_screen")
        assert keyboard.Key.ctrl_l in keys
        assert keyboard.Key.shift in keys
        assert keyboard.Key.print_screen in keys
