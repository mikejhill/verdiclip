"""Tests for the low-level keyboard hook used to record hotkeys."""

from __future__ import annotations

import ctypes
import sys

import pytest

from verdiclip.exceptions import PlatformError
from verdiclip.platform.keyboard_hook import (
    MODIFIER_VKS,
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

        started = hook.start(lambda vk, down: seen.append((vk, down)))
        assert started
        try:
            assert hook.active
            swallowed = hook._proc(0, WM_KEYDOWN, ctypes.addressof(info))
        finally:
            hook.stop()

        assert swallowed == 1
        assert seen == [(VK_SNAPSHOT, True)]
        assert not hook.active

    def test_release_of_a_key_held_before_start_passes_through(self) -> None:
        """Windows saw Ctrl go down before the hook, so it must see it come up."""
        hook = LowLevelKeyboardHook()
        seen: list[tuple[int, bool]] = []
        ctrl, a, prtsc = key_info(0xA2), key_info(0x41), key_info(VK_SNAPSHOT)

        started = hook.start(lambda vk, down: seen.append((vk, down)))
        assert started
        try:
            ctrl_up = hook._proc(0, WM_KEYUP, ctypes.addressof(ctrl))
            a_down = hook._proc(0, WM_KEYDOWN, ctypes.addressof(a))
            a_up = hook._proc(0, WM_KEYUP, ctypes.addressof(a))
            prtsc_up = hook._proc(0, WM_KEYUP, ctypes.addressof(prtsc))
        finally:
            hook.stop()

        assert ctrl_up != 1
        assert (a_down, a_up, prtsc_up) == (1, 1, 1)
        assert seen == [
            (0xA2, False),
            (0x41, True),
            (0x41, False),
            (VK_SNAPSHOT, False),
        ]

    def test_held_modifiers_are_modifier_keys(self) -> None:
        """Only modifier keys are reported as held."""
        held = LowLevelKeyboardHook().held_modifiers()

        assert held <= set(MODIFIER_VKS)

    def test_failing_callback_releases_the_keyboard(self) -> None:
        """An error in the callback removes the hook and lets the key through."""
        hook = LowLevelKeyboardHook()
        info = key_info(VK_SNAPSHOT)

        def fail(vk: int, down: bool) -> None:  # noqa: FBT001
            del vk, down
            raise RuntimeError

        started = hook.start(fail)
        assert started
        result = hook._proc(0, WM_KEYDOWN, ctypes.addressof(info))

        assert result != 1
        assert not hook.active

    def test_non_keys_and_idle_hook_pass_through(self) -> None:
        """Other messages, other codes and a stopped hook never swallow."""
        hook = LowLevelKeyboardHook()
        info = key_info(VK_SNAPSHOT)
        address = ctypes.addressof(info)
        seen: list[int] = []

        started = hook.start(lambda vk, _down: seen.append(vk))
        assert started
        other_message = hook._proc(0, 0x0200, address)
        other_code = hook._proc(-1, WM_KEYDOWN, address)
        hook.stop()
        stopped = hook._proc(0, WM_KEYDOWN, address)
        hook.stop()

        assert 1 not in {other_message, other_code, stopped}
        assert seen == []
