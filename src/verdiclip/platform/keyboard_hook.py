"""See every key press before Windows acts on it, for recording hotkeys.

A window only receives keys Windows didn't take first: PrtSc goes to the
Snipping Tool or another app's hotkey, Win+key to the shell. A low-level
keyboard hook sees them all, and swallowing them keeps those actions quiet
while the user records a combination.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import sys
from collections.abc import Callable
from typing import Final, Protocol

from verdiclip.exceptions import PlatformError

logger = logging.getLogger(__name__)

KeyCallback = Callable[[int, bool], None]
"""Receives a Win32 virtual key and whether it went down (False: up)."""

_WH_KEYBOARD_LL: Final = 13
_HC_ACTION: Final = 0
_KEY_DOWN: Final = frozenset({0x0100, 0x0104})  # WM_KEYDOWN, WM_SYSKEYDOWN
_KEY_UP: Final = frozenset({0x0101, 0x0105})  # WM_KEYUP, WM_SYSKEYUP
_VK_SNAPSHOT: Final = 0x2C
# Left and right Shift, Ctrl, Alt, then Win
MODIFIER_VKS: Final = (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C)


class KbdLlHookStruct(ctypes.Structure):
    """KBDLLHOOKSTRUCT: what a low-level keyboard hook is told."""

    _fields_ = (
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


def decode(message: int, address: int) -> tuple[int, bool] | None:
    """Return (virtual key, pressed) for a hook message, or None to ignore it."""
    if message not in _KEY_DOWN | _KEY_UP:
        return None
    info = KbdLlHookStruct.from_address(address)
    return int(info.vkCode), message in _KEY_DOWN


class KeyCapture(Protocol):
    """Deliver every key press to one callback until stopped."""

    def start(self, callback: KeyCallback) -> bool:
        """Start capturing; return False if the keys can't be captured."""

    def stop(self) -> None:
        """Stop capturing and let keys through again."""

    def held_modifiers(self) -> frozenset[int]:
        """Return the modifier keys (``MODIFIER_VKS``) physically down now."""


class LowLevelKeyboardHook:
    """``KeyCapture`` through ``SetWindowsHookExW(WH_KEYBOARD_LL)``.

    The hook runs on the thread that installs it, inside its message loop,
    so callbacks arrive on the Qt GUI thread. Captured keys are swallowed.
    """

    def __init__(self) -> None:
        """Load the Windows entry points; fail off Windows."""
        if sys.platform != "win32":
            msg = "Keyboard hooks require Windows"
            raise PlatformError(msg)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        )
        self._user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int,
            self._proc_type,
            ctypes.wintypes.HINSTANCE,
            ctypes.wintypes.DWORD,
        ]
        self._user32.SetWindowsHookExW.restype = ctypes.wintypes.HHOOK
        self._user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HHOOK]
        self._user32.UnhookWindowsHookEx.restype = ctypes.wintypes.BOOL
        self._user32.CallNextHookEx.argtypes = [
            ctypes.wintypes.HHOOK,
            ctypes.c_int,
            ctypes.wintypes.WPARAM,
            ctypes.wintypes.LPARAM,
        ]
        self._user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.argtypes = [ctypes.wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = ctypes.wintypes.HMODULE
        self._module = kernel32.GetModuleHandleW(None)
        self._hook: int | None = None
        self._callback: KeyCallback | None = None
        # Keys whose press this hook swallowed; only their releases are too
        self._down: set[int] = set()
        # Windows calls this pointer; keep it alive while hooked
        self._proc = self._proc_type(self._on_hook)

    @property
    def active(self) -> bool:
        """True while the hook is installed."""
        return self._hook is not None

    def start(self, callback: KeyCallback) -> bool:
        """Install the hook; return False if Windows refuses it."""
        self.stop()
        hook = self._user32.SetWindowsHookExW(
            _WH_KEYBOARD_LL, self._proc, self._module, 0
        )
        if not hook:
            logger.warning(
                "Keyboard hook unavailable (error %d)", ctypes.get_last_error()
            )
            return False
        self._hook = hook
        self._callback = callback
        self._down.clear()
        return True

    def stop(self) -> None:
        """Remove the hook if it is installed."""
        hook, self._hook, self._callback = self._hook, None, None
        if hook is not None and not self._user32.UnhookWindowsHookEx(hook):
            logger.warning("Windows did not remove the keyboard hook")

    def held_modifiers(self) -> frozenset[int]:
        """Return the modifier keys down now, so a held Ctrl isn't missed."""
        return frozenset(
            vk for vk in MODIFIER_VKS if self._user32.GetAsyncKeyState(vk) & 0x8000
        )

    def _on_hook(self, code: int, message: int, address: int) -> int:
        """Hand a key to the callback and swallow it; pass anything else on.

        A release whose press reached Windows before the hook (a Ctrl held
        while clicking Record) passes through, or Windows would think the
        key stays down. PrtSc's release is always swallowed: some keyboards
        send only that.
        """
        if code == _HC_ACTION and self._callback is not None:
            key = decode(message, address)
            if key is not None:
                vk, pressed = key
                swallow = pressed or vk in self._down or vk == _VK_SNAPSHOT
                if pressed:
                    self._down.add(vk)
                else:
                    self._down.discard(vk)
                try:
                    self._callback(vk, pressed)
                except Exception:
                    # An escaping error would leave every key swallowed
                    logger.exception("Key callback failed; releasing the keyboard")
                    self.stop()
                    return self._pass_on(code, message, address)
                return 1 if swallow else self._pass_on(code, message, address)
        return self._pass_on(code, message, address)

    def _pass_on(self, code: int, message: int, address: int) -> int:
        """Let the next hook (and Windows) handle the message."""
        return int(self._user32.CallNextHookEx(None, code, message, address))
