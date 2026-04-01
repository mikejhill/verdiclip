"""Tests for verdiclip.hotkeys.manager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from pynput import keyboard

from verdiclip.hotkeys.manager import HotkeyManager, _parse_hotkey

if TYPE_CHECKING:
    from verdiclip.config import Config


# ---------------------------------------------------------------------------
# _parse_hotkey
# ---------------------------------------------------------------------------


class TestParseHotkey:
    def test_ctrl_print_screen(self) -> None:
        keys = _parse_hotkey("ctrl+print_screen")
        assert keys == {keyboard.Key.ctrl_l, keyboard.Key.print_screen}

    def test_shift_print_screen(self) -> None:
        keys = _parse_hotkey("shift+print_screen")
        assert keys == {keyboard.Key.shift, keyboard.Key.print_screen}

    def test_alt_print_screen(self) -> None:
        keys = _parse_hotkey("alt+print_screen")
        assert keys == {keyboard.Key.alt_l, keyboard.Key.print_screen}

    def test_ctrl_s(self) -> None:
        keys = _parse_hotkey("ctrl+s")
        assert keys == {keyboard.Key.ctrl_l, keyboard.KeyCode.from_char("s")}

    def test_bare_print_screen(self) -> None:
        keys = _parse_hotkey("print_screen")
        assert keys == {keyboard.Key.print_screen}

    def test_ctrl_shift_print_screen(self) -> None:
        keys = _parse_hotkey("ctrl+shift+print_screen")
        assert keys == {
            keyboard.Key.ctrl_l,
            keyboard.Key.shift,
            keyboard.Key.print_screen,
        }

    def test_control_alias_same_as_ctrl(self) -> None:
        assert _parse_hotkey("control+s") == _parse_hotkey("ctrl+s")

    def test_win_alias_returns_cmd(self) -> None:
        keys = _parse_hotkey("win+s")
        assert keyboard.Key.cmd in keys

    @pytest.mark.parametrize("char", list("abcdefghijklmnopqrstuvwxyz"))
    def test_single_char_keys(self, char: str) -> None:
        keys = _parse_hotkey(char)
        assert keys == {keyboard.KeyCode.from_char(char)}

    @pytest.mark.parametrize("n", range(1, 13))
    def test_function_keys_f1_through_f12(self, n: int) -> None:
        keys = _parse_hotkey(f"f{n}")
        expected_key = getattr(keyboard.Key, f"f{n}")
        assert keys == {expected_key}

    def test_empty_string_returns_empty_set(self) -> None:
        keys = _parse_hotkey("")
        assert keys == set()

    def test_unknown_key_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="verdiclip.hotkeys.manager"):
            keys = _parse_hotkey("boguskey")
        assert len(keys) == 0
        assert "Unknown key" in caplog.text

    def test_whitespace_around_parts_is_stripped(self) -> None:
        keys = _parse_hotkey(" ctrl + s ")
        assert keys == {keyboard.Key.ctrl_l, keyboard.KeyCode.from_char("s")}


# ---------------------------------------------------------------------------
# HotkeyManager.register
# ---------------------------------------------------------------------------


class TestRegister:
    def test_registers_callback(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        manager.register("ctrl+print_screen", MagicMock())
        assert len(manager._callbacks) == 1

    def test_same_hotkey_overwrites_previous(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        first = MagicMock()
        second = MagicMock()
        manager.register("ctrl+s", first)
        manager.register("ctrl+s", second)

        assert len(manager._callbacks) == 1
        stored = next(iter(manager._callbacks.values()))
        assert stored is second

    def test_empty_hotkey_does_not_register(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        manager.register("", MagicMock())
        assert len(manager._callbacks) == 0


# ---------------------------------------------------------------------------
# HotkeyManager.unregister
# ---------------------------------------------------------------------------


class TestUnregister:
    def test_unregisters_existing_hotkey(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        manager.register("ctrl+print_screen", MagicMock())
        assert len(manager._callbacks) == 1
        manager.unregister("ctrl+print_screen")
        assert len(manager._callbacks) == 0

    def test_unregister_nonexistent_does_not_raise(
        self, tmp_config: Config
    ) -> None:
        manager = HotkeyManager(tmp_config)
        manager.unregister("ctrl+shift+x")


# ---------------------------------------------------------------------------
# HotkeyManager._normalize_key
# ---------------------------------------------------------------------------


class TestNormalizeKey:
    @pytest.fixture()
    def manager(self, tmp_config: Config) -> HotkeyManager:
        return HotkeyManager(tmp_config)

    def test_ctrl_l_unchanged(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.ctrl_l) == keyboard.Key.ctrl_l

    def test_ctrl_r_to_ctrl_l(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.ctrl_r) == keyboard.Key.ctrl_l

    def test_alt_l_unchanged(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.alt_l) == keyboard.Key.alt_l

    def test_alt_r_to_alt_l(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.alt_r) == keyboard.Key.alt_l

    def test_alt_gr_to_alt_l(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.alt_gr) == keyboard.Key.alt_l

    def test_shift_l_to_shift(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.shift_l) == keyboard.Key.shift

    def test_shift_r_to_shift(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.shift_r) == keyboard.Key.shift

    def test_cmd_l_to_cmd(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.cmd_l) == keyboard.Key.cmd

    def test_cmd_r_to_cmd(self, manager: HotkeyManager) -> None:
        assert manager._normalize_key(keyboard.Key.cmd_r) == keyboard.Key.cmd

    def test_regular_key_unchanged(self, manager: HotkeyManager) -> None:
        assert (
            manager._normalize_key(keyboard.Key.print_screen)
            == keyboard.Key.print_screen
        )

    def test_keycode_unchanged(self, manager: HotkeyManager) -> None:
        kc = keyboard.KeyCode.from_char("a")
        assert manager._normalize_key(kc) == kc


# ---------------------------------------------------------------------------
# HotkeyManager._on_press
# ---------------------------------------------------------------------------


class TestOnPress:
    @pytest.fixture()
    def manager(self, tmp_config: Config) -> HotkeyManager:
        return HotkeyManager(tmp_config)

    def test_adds_normalized_key_to_pressed(self, manager: HotkeyManager) -> None:
        manager._on_press(keyboard.Key.ctrl_r)
        assert keyboard.Key.ctrl_l in manager._pressed_keys

    def test_triggers_callback_when_combo_pressed(
        self, manager: HotkeyManager
    ) -> None:
        cb = MagicMock()
        manager.register("ctrl+print_screen", cb)
        manager._on_press(keyboard.Key.ctrl_l)
        manager._on_press(keyboard.Key.print_screen)
        cb.assert_called_once()

    def test_does_not_trigger_on_partial_combo(
        self, manager: HotkeyManager
    ) -> None:
        cb = MagicMock()
        manager.register("ctrl+print_screen", cb)
        manager._on_press(keyboard.Key.ctrl_l)
        cb.assert_not_called()

    def test_callback_exception_is_caught(
        self, manager: HotkeyManager
    ) -> None:
        cb = MagicMock(side_effect=RuntimeError("boom"))
        manager.register("print_screen", cb)
        # Should not raise
        manager._on_press(keyboard.Key.print_screen)
        cb.assert_called_once()


# ---------------------------------------------------------------------------
# HotkeyManager._on_release
# ---------------------------------------------------------------------------


class TestOnRelease:
    @pytest.fixture()
    def manager(self, tmp_config: Config) -> HotkeyManager:
        return HotkeyManager(tmp_config)

    def test_removes_normalized_key(self, manager: HotkeyManager) -> None:
        manager._pressed_keys.add(keyboard.Key.ctrl_l)
        manager._on_release(keyboard.Key.ctrl_l)
        assert keyboard.Key.ctrl_l not in manager._pressed_keys

    def test_key_not_in_set_does_not_raise(self, manager: HotkeyManager) -> None:
        manager._on_release(keyboard.Key.ctrl_l)


# ---------------------------------------------------------------------------
# HotkeyManager.start
# ---------------------------------------------------------------------------


class TestStart:
    @patch("verdiclip.hotkeys.manager.keyboard.Listener")
    def test_creates_and_starts_listener(
        self, mock_listener_cls: MagicMock, tmp_config: Config
    ) -> None:
        manager = HotkeyManager(tmp_config)
        manager.start()

        mock_listener_cls.assert_called_once_with(
            on_press=manager._on_press,
            on_release=manager._on_release,
        )
        mock_listener_cls.return_value.start.assert_called_once()

    @patch("verdiclip.hotkeys.manager.keyboard.Listener")
    def test_start_twice_logs_warning(
        self,
        mock_listener_cls: MagicMock,
        tmp_config: Config,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        manager = HotkeyManager(tmp_config)
        manager.start()

        with caplog.at_level(logging.WARNING, logger="verdiclip.hotkeys.manager"):
            manager.start()

        mock_listener_cls.assert_called_once()
        assert "already running" in caplog.text


# ---------------------------------------------------------------------------
# HotkeyManager.stop
# ---------------------------------------------------------------------------


class TestStop:
    @patch("verdiclip.hotkeys.manager.keyboard.Listener")
    def test_stops_listener_and_clears_pressed(
        self, mock_listener_cls: MagicMock, tmp_config: Config
    ) -> None:
        manager = HotkeyManager(tmp_config)
        manager.start()
        manager._pressed_keys.add(keyboard.Key.ctrl_l)

        manager.stop()

        mock_listener_cls.return_value.stop.assert_called_once()
        assert manager._listener is None
        assert len(manager._pressed_keys) == 0

    def test_stop_when_not_started_is_safe(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        manager.stop()


# ---------------------------------------------------------------------------
# HotkeyManager.reload_from_config
# ---------------------------------------------------------------------------


class TestReloadFromConfig:
    def test_clears_all_callbacks(self, tmp_config: Config) -> None:
        manager = HotkeyManager(tmp_config)
        manager.register("ctrl+s", MagicMock())
        manager.register("alt+print_screen", MagicMock())
        assert len(manager._callbacks) == 2

        manager.reload_from_config()
        assert len(manager._callbacks) == 0
