"""Tests for the low-level keyboard hook used to record hotkeys."""

from __future__ import annotations

import ctypes
import sys

import pytest

from verdiclip.exceptions import PlatformError
from verdiclip.platform.keyboard_hook import (
    KbdLlHookStruct,
    LowLevelKeyboardHook,
    decode,
)

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
VK_SNAPSHOT = 0x2C
windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="needs Windows keyboard hooks"
)


def key_info(vk: int) -> KbdLlHookStruct:
    """A hook message's key data."""
    return KbdLlHookStruct(vkCode=vk, scanCode=0, flags=0, time=0, dwExtraInfo=0)


class TestDecode:
    """Hook messages become (virtual key, pressed)."""

    @pytest.mark.parametrize(
        ("message", "pressed"),
        [
            (WM_KEYDOWN, True),
            (WM_SYSKEYDOWN, True),
            (WM_KEYUP, False),
            (WM_SYSKEYUP, False),
        ],
    )
    def test_key_messages(self, message: int, *, pressed: bool) -> None:
        """Downs and ups, including Alt's system variants, decode."""
        info = key_info(VK_SNAPSHOT)

        assert decode(message, ctypes.addressof(info)) == (VK_SNAPSHOT, pressed)

    def test_other_messages_are_ignored(self) -> None:
        """Anything else isn't a key."""
        info = key_info(VK_SNAPSHOT)

        assert decode(0x0200, ctypes.addressof(info)) is None


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows guard")
def test_requires_windows() -> None:
    """Off Windows the hook can't be created."""
    with pytest.raises(PlatformError, match="require Windows"):
        LowLevelKeyboardHook()


@windows_only
class TestLowLevelKeyboardHook:
    """The real hook installs, delivers keys and swallows them."""

    def test_start_deliver_stop(self) -> None:
        """A key reaches the callback and is swallowed until stop."""
        hook = LowLevelKeyboardHook()
        seen: list[tuple[int, bool]] = []
        info = key_info(VK_SNAPSHOT)

        assert hook.start(lambda vk, down: seen.append((vk, down)))
        try:
            assert hook.active
            swallowed = hook._proc(0, WM_KEYDOWN, ctypes.addressof(info))
        finally:
            hook.stop()

        assert swallowed == 1
        assert seen == [(VK_SNAPSHOT, True)]
        assert not hook.active

    def test_failing_callback_releases_the_keyboard(self) -> None:
        """An error in the callback removes the hook and lets the key through."""
        hook = LowLevelKeyboardHook()
        info = key_info(VK_SNAPSHOT)

        def fail(vk: int, down: bool) -> None:  # noqa: FBT001
            del vk, down
            raise RuntimeError

        assert hook.start(fail)
        result = hook._proc(0, WM_KEYDOWN, ctypes.addressof(info))

        assert result != 1
        assert not hook.active

    def test_non_keys_and_idle_hook_pass_through(self) -> None:
        """Other messages, other codes and a stopped hook never swallow."""
        hook = LowLevelKeyboardHook()
        info = key_info(VK_SNAPSHOT)
        address = ctypes.addressof(info)
        seen: list[int] = []

        assert hook.start(lambda vk, _down: seen.append(vk))
        other_message = hook._proc(0, 0x0200, address)
        other_code = hook._proc(-1, WM_KEYDOWN, address)
        hook.stop()
        stopped = hook._proc(0, WM_KEYDOWN, address)
        hook.stop()

        assert 1 not in {other_message, other_code, stopped}
        assert seen == []
