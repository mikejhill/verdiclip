"""Global hotkey manager using pynput."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pynput import keyboard

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from verdiclip.config import Config

logger = logging.getLogger(__name__)

_KEY_MAP: dict[str, keyboard.Key | keyboard.KeyCode] = {
    "print_screen": keyboard.Key.print_screen,
    "f1": keyboard.Key.f1,
    "f2": keyboard.Key.f2,
    "f3": keyboard.Key.f3,
    "f4": keyboard.Key.f4,
    "f5": keyboard.Key.f5,
    "f6": keyboard.Key.f6,
    "f7": keyboard.Key.f7,
    "f8": keyboard.Key.f8,
    "f9": keyboard.Key.f9,
    "f10": keyboard.Key.f10,
    "f11": keyboard.Key.f11,
    "f12": keyboard.Key.f12,
}


def _parse_hotkey(hotkey_str: str) -> set[keyboard.Key | keyboard.KeyCode]:
    """Parse a hotkey string like 'ctrl+shift+print_screen' into a set of keys."""
    parts = hotkey_str.lower().strip().split("+")
    keys: set[keyboard.Key | keyboard.KeyCode] = set()

    for part in parts:
        part = part.strip()
        if part in ("ctrl", "control"):
            keys.add(keyboard.Key.ctrl_l)
        elif part in ("alt",):
            keys.add(keyboard.Key.alt_l)
        elif part in ("shift",):
            keys.add(keyboard.Key.shift)
        elif part in ("win", "super", "cmd"):
            keys.add(keyboard.Key.cmd)
        elif part in _KEY_MAP:
            keys.add(_KEY_MAP[part])
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            logger.warning("Unknown key in hotkey string: '%s'", part)

    return keys


class HotkeyManager:
    """Manages global hotkey registration and dispatch."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._listener: keyboard.Listener | None = None
        self._callbacks: dict[frozenset, Callable] = {}
        self._pressed_keys: set[keyboard.Key | keyboard.KeyCode] = set()
        self._thread: threading.Thread | None = None

    def register(self, hotkey_str: str, callback: Callable) -> None:
        """Register a callback for a hotkey string.

        Args:
            hotkey_str: Hotkey like 'ctrl+print_screen'.
            callback: Function to call when hotkey is pressed.
        """
        keys = _parse_hotkey(hotkey_str)
        if keys:
            self._callbacks[frozenset(keys)] = callback
            logger.info("Registered hotkey: %s", hotkey_str)

    def unregister(self, hotkey_str: str) -> None:
        """Remove a registered hotkey."""
        keys = _parse_hotkey(hotkey_str)
        frozen = frozenset(keys)
        if frozen in self._callbacks:
            del self._callbacks[frozen]
            logger.info("Unregistered hotkey: %s", hotkey_str)

    def _normalize_key(
        self, key: keyboard.Key | keyboard.KeyCode
    ) -> keyboard.Key | keyboard.KeyCode:
        """Normalize left/right modifier variants to a canonical key."""
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            return keyboard.Key.ctrl_l
        if key in (keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
            return keyboard.Key.alt_l
        if key in (keyboard.Key.shift_l, keyboard.Key.shift_r):
            return keyboard.Key.shift
        if key in (keyboard.Key.cmd_l, keyboard.Key.cmd_r):
            return keyboard.Key.cmd
        return key

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        """Handle key press events."""
        normalized = self._normalize_key(key)
        self._pressed_keys.add(normalized)

        frozen_pressed = frozenset(self._pressed_keys)
        for hotkey_keys, callback in self._callbacks.items():
            if hotkey_keys.issubset(frozen_pressed):
                logger.debug("Hotkey triggered: %s", hotkey_keys)
                try:
                    callback()
                except Exception:
                    logger.exception("Error in hotkey callback")

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        """Handle key release events."""
        normalized = self._normalize_key(key)
        self._pressed_keys.discard(normalized)

    def start(self) -> None:
        """Start listening for global hotkeys."""
        if self._listener is not None:
            logger.warning("Hotkey listener already running.")
            return

        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("Global hotkey listener started.")

    def stop(self) -> None:
        """Stop the hotkey listener."""
        if self._listener:
            self._listener.stop()
            self._listener = None
            self._pressed_keys.clear()
            logger.info("Global hotkey listener stopped.")

    def reload_from_config(self) -> None:
        """Reload hotkey bindings from config."""
        self._callbacks.clear()
        logger.info("Hotkey bindings cleared for reload.")
