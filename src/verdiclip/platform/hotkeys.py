"""Parse exact hotkeys and dispatch Win32 registrations through Qt."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, Self, override

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QByteArray,
    QCoreApplication,
    QObject,
    Signal,
)

from verdiclip.exceptions import HotkeyError, PlatformError

logger = logging.getLogger(__name__)
_WM_HOTKEY: Final = 0x0312


class Modifier(StrEnum):
    """Supported global shortcut modifiers."""

    CTRL = "ctrl"
    ALT = "alt"
    SHIFT = "shift"
    WIN = "win"


@dataclass(frozen=True, slots=True)
class Hotkey:
    """A canonical key and an unordered set of modifiers."""

    modifiers: frozenset[Modifier]
    key: str
    _KEYS: ClassVar[Mapping[str, int]] = MappingProxyType(
        {
            "print_screen": 0x2C,
            "space": 0x20,
            "insert": 0x2D,
            "delete": 0x2E,
            "home": 0x24,
            "end": 0x23,
            "pause": 0x13,
            "scroll_lock": 0x91,
            "page_up": 0x21,
            "page_down": 0x22,
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
            "tab": 0x09,
            "enter": 0x0D,
            "escape": 0x1B,
            "backspace": 0x08,
            "caps_lock": 0x14,
            "num_lock": 0x90,
            **{chr(code).lower(): code for code in range(ord("A"), ord("Z") + 1)},
            **{str(number): ord(str(number)) for number in range(10)},
            **{f"f{number}": 0x6F + number for number in range(1, 25)},
        }
    )
    _ALIASES: ClassVar[Mapping[str, str]] = MappingProxyType(
        {
            "control": "ctrl",
            "cmd": "win",
            "super": "win",
            "prtsc": "print_screen",
            "printscreen": "print_screen",
            "print": "print_screen",
            "esc": "escape",
            "return": "enter",
            "del": "delete",
            "ins": "insert",
            "pgup": "page_up",
            "pgdn": "page_down",
            "scrolllock": "scroll_lock",
        }
    )

    def __post_init__(self) -> None:
        """Reject noncanonical keys at direct construction boundaries."""
        if self.key not in self._KEYS:
            msg = f"Unknown hotkey key: {self.key!r}"
            raise HotkeyError(msg)

    @classmethod
    def parse(cls, text: str) -> Self:
        """Parse a shortcut containing exactly one non-modifier key."""
        if not text.strip():
            msg = "Hotkey cannot be empty; specify one key"
            raise HotkeyError(msg)
        modifiers: set[Modifier] = set()
        keys: list[str] = []
        for raw_token in text.lower().split("+"):
            token = "".join(raw_token.split())
            token = cls._ALIASES.get(token, token)
            if token in Modifier:
                modifiers.add(Modifier(token))
            elif token in cls._KEYS:
                keys.append(token)
            else:
                msg = f"Unknown hotkey key: {token!r}"
                raise HotkeyError(msg)
        if len(keys) != 1:
            msg = f"Hotkey requires exactly one key; found {len(keys)}"
            raise HotkeyError(msg)
        return cls(frozenset(modifiers), keys[0])

    @override
    def __str__(self) -> str:
        """Return the stable configuration representation."""
        return "+".join([m.value for m in Modifier if m in self.modifiers] + [self.key])

    @property
    def display_text(self) -> str:
        """Return a compact shortcut label for menus."""
        key = (
            "PrtSc"
            if self.key == "print_screen"
            else self.key.replace("_", " ").title()
        )
        return "+".join(
            [m.value.title() for m in Modifier if m in self.modifiers] + [key]
        )

    @property
    def win32_modifiers(self) -> int:
        """Return the exact Win32 modifier mask with repeat disabled."""
        masks = {Modifier.CTRL: 2, Modifier.ALT: 1, Modifier.SHIFT: 4, Modifier.WIN: 8}
        return 0x4000 | sum(masks[m] for m in self.modifiers)

    @property
    def virtual_key(self) -> int:
        """Return the Win32 virtual-key code."""
        return self._KEYS[self.key]


class HotkeyApi(Protocol):
    """The minimal OS registration boundary."""

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
        """Register an exact shortcut."""
        ...

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        """Release a shortcut registration."""
        ...


class Win32HotkeyApi:
    """Typed RegisterHotKey and UnregisterHotKey adapter."""

    def __init__(self) -> None:
        """Load Windows entry points only on Windows."""
        if sys.platform != "win32":
            msg = "Global hotkeys require Windows"
            raise PlatformError(msg)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL

    def register(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
        """Register a window or thread shortcut."""
        return bool(self._user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk))

    def unregister(self, hwnd: int, hotkey_id: int) -> bool:
        """Release a window or thread shortcut."""
        return bool(self._user32.UnregisterHotKey(hwnd, hotkey_id))


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    """Forward Windows hotkey messages to the service."""

    def __init__(self, service: HotkeyService) -> None:
        """Store the owning dispatcher."""
        super().__init__()
        self._service = service

    @override
    def nativeEventFilter(
        self, event_type: QByteArray | bytes | bytearray | memoryview, message: int
    ) -> tuple[bool, int]:
        """Consume recognized Windows generic hotkey messages."""
        event_bytes = (
            event_type.data()
            if isinstance(event_type, QByteArray)
            else bytes(event_type)
        )
        # Qt passes a shiboken VoidPtr, whose truth test raises; compare as int
        address = int(message)
        if event_bytes != b"windows_generic_MSG" or address == 0:
            return False, 0
        native_message = wintypes.MSG.from_address(address)
        if native_message.message != _WM_HOTKEY:
            return False, 0
        return self._service.handle_hotkey_message(int(native_message.wParam)), 0


class HotkeyService(QObject):
    """Own global registrations and emit action identifiers through Qt."""

    triggered = Signal(str)

    def __init__(self, api: HotkeyApi | None = None) -> None:
        """Install the native filter on the existing application."""
        super().__init__()
        self._api = api if api is not None else Win32HotkeyApi()
        self._registered: dict[str, Hotkey] = {}
        self._actions: dict[int, str] = {}
        self._failed: set[str] = set()
        self._next_id = 1
        application = QCoreApplication.instance()
        if application is None:
            msg = "HotkeyService requires a QCoreApplication"
            raise HotkeyError(msg)
        self._application = application
        self._filter = _NativeHotkeyFilter(self)
        self._application.installNativeEventFilter(self._filter)
        self._closed = False

    def register(self, action_id: str, hotkey: Hotkey) -> bool:
        """Replace an action shortcut and report OS conflicts."""
        if self._closed:
            msg = "Cannot register hotkeys after close"
            raise HotkeyError(msg)
        for identifier, action in list(self._actions.items()):
            if action == action_id:
                self._unregister(identifier)
        identifier = self._next_id
        self._next_id += 1
        if not self._api.register(
            0, identifier, hotkey.win32_modifiers, hotkey.virtual_key
        ):
            self._failed.add(action_id)
            logger.warning("Hotkey %s unavailable for action %s", hotkey, action_id)
            return False
        self._failed.discard(action_id)
        self._registered[action_id] = hotkey
        self._actions[identifier] = action_id
        return True

    def _unregister(self, identifier: int) -> None:
        """Release an identifier before removing its dispatch state."""
        if not self._api.unregister(0, identifier):
            # Already gone (e.g. never registered by Windows); just forget it
            logger.warning("Windows did not unregister hotkey %d", identifier)
        action = self._actions.pop(identifier)
        self._registered.pop(action)

    def unregister_all(self) -> None:
        """Release every shortcut owned by this service."""
        for identifier in list(self._actions):
            self._unregister(identifier)

    @property
    def registered(self) -> Mapping[str, Hotkey]:
        """Return a read-only snapshot of active shortcuts."""
        return MappingProxyType(dict(self._registered))

    @property
    def failed(self) -> frozenset[str]:
        """Return actions whose most recent registration failed."""
        return frozenset(self._failed)

    def handle_hotkey_message(self, hotkey_id: int) -> bool:
        """Emit the registered action and report whether it was recognized."""
        action = self._actions.get(hotkey_id)
        if action is None:
            return False
        self.triggered.emit(action)
        return True

    def close(self) -> None:
        """Release shortcuts and remove the native filter exactly once."""
        if self._closed:
            return
        self.unregister_all()
        self._application.removeNativeEventFilter(self._filter)
        self._closed = True
