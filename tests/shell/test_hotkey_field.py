"""Tests for recording a hotkey by pressing it."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from verdiclip.platform.hotkeys import Hotkey, Modifier
from verdiclip.platform.keyboard_hook import KeyCallback
from verdiclip.shell import hotkey_field
from verdiclip.shell.hotkey_field import (
    RECORD_TEXT,
    RECORDING_HINT,
    RECORDING_TEXT,
    UNSUPPORTED_HINT,
    HotkeyField,
    hotkey_from_event,
)

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
META = Qt.KeyboardModifier.MetaModifier
NONE = Qt.KeyboardModifier.NoModifier


def key_event(
    key: Qt.Key, modifiers: Qt.KeyboardModifier = NONE, vk: int = 0
) -> QKeyEvent:
    """A key press carrying an optional Win32 virtual key."""
    return QKeyEvent(QEvent.Type.KeyPress, key, modifiers, 0, vk, 0)


KEYPAD = Qt.KeyboardModifier.KeypadModifier
VK_CTRL, VK_LSHIFT, VK_RSHIFT, VK_LWIN = 0xA2, 0xA0, 0xA1, 0x5B
VK_ESCAPE, VK_PRTSC, VK_NUM_5, VK_A, VK_VOLUME_UP = 0x1B, 0x2C, 0x65, 0x41, 0xAF


class FakeCapture:
    """A keyboard hook stand-in that the test presses keys through."""

    def __init__(self) -> None:
        self.callback: KeyCallback | None = None
        self.starts = 0
        self.accept = True
        self.held: frozenset[int] = frozenset()

    def start(self, callback: KeyCallback) -> bool:
        """Remember the callback."""
        self.starts += 1
        self.callback = callback if self.accept else None
        return self.accept

    def stop(self) -> None:
        """Forget the callback."""
        self.callback = None

    def held_modifiers(self) -> frozenset[int]:
        """Return the modifiers set as already held."""
        return self.held

    def press(self, vk: int) -> None:
        """Send a key down."""
        assert self.callback is not None
        self.callback(vk, True)  # noqa: FBT003 - the hook callback signature

    def release(self, vk: int) -> None:
        """Send a key up."""
        assert self.callback is not None
        self.callback(vk, False)  # noqa: FBT003 - the hook callback signature

    def tap(self, *vks: int) -> None:
        """Press keys in order, then release them in reverse."""
        for vk in vks:
            self.press(vk)
        for vk in reversed(vks):
            if self.callback is not None:
                self.release(vk)


@pytest.fixture
def field(qtbot: QtBot) -> HotkeyField:
    """A shown field holding Ctrl+PrtSc."""
    created = HotkeyField()
    qtbot.addWidget(created)
    created.edit.setText("Ctrl+PrtSc")
    created.show()
    qtbot.waitExposed(created)
    return created


class TestHotkeyFromEvent:
    """Qt key events map to hotkeys."""

    @pytest.mark.parametrize(
        ("key", "modifiers", "expected"),
        [
            (Qt.Key.Key_A, CTRL | SHIFT, "ctrl+shift+a"),
            (Qt.Key.Key_Z, NONE, "z"),
            (Qt.Key.Key_0, ALT, "alt+0"),
            (Qt.Key.Key_9, NONE, "9"),
            (Qt.Key.Key_F1, NONE, "f1"),
            (Qt.Key.Key_F24, META, "win+f24"),
            (Qt.Key.Key_Print, CTRL, "ctrl+print_screen"),
            (Qt.Key.Key_SysReq, ALT, "alt+print_screen"),
            (Qt.Key.Key_Backtab, SHIFT, "shift+tab"),
            (Qt.Key.Key_Enter, NONE, "enter"),
            (Qt.Key.Key_PageDown, CTRL, "ctrl+page_down"),
            (Qt.Key.Key_5, CTRL | KEYPAD, "ctrl+num_5"),
            (Qt.Key.Key_Asterisk, KEYPAD, "num_multiply"),
            (Qt.Key.Key_Slash, KEYPAD, "num_divide"),
            (Qt.Key.Key_Slash, CTRL, "ctrl+slash"),
            (Qt.Key.Key_BracketLeft, ALT, "alt+left_bracket"),
            (Qt.Key.Key_Menu, SHIFT, "shift+menu"),
        ],
    )
    def test_qt_keys(
        self, key: Qt.Key, modifiers: Qt.KeyboardModifier, expected: str
    ) -> None:
        """Without a native key, Qt's key and modifiers decide."""
        hotkey = hotkey_from_event(key_event(key, modifiers))

        assert str(hotkey) == expected

    def test_native_virtual_key_wins(self) -> None:
        """Shift+1 reports "!" in Qt, but the virtual key is the 1 key."""
        hotkey = hotkey_from_event(key_event(Qt.Key.Key_Exclam, SHIFT, vk=0x31))

        assert hotkey == Hotkey(frozenset({Modifier.SHIFT}), "1")

    def test_unsupported_native_key_is_rejected(self) -> None:
        """A native key VerdiClip can't bind isn't swapped for Qt's key."""
        assert hotkey_from_event(key_event(Qt.Key.Key_1, NONE, vk=0xAF)) is None

    @pytest.mark.parametrize("key", [Qt.Key.Key_Exclam, Qt.Key.Key_VolumeUp])
    def test_unsupported_keys(self, key: Qt.Key) -> None:
        """Keys Windows hotkeys don't support map to nothing."""
        assert hotkey_from_event(key_event(key)) is None


class TestHotkeyField:
    """Recording replaces typing when the user wants it to."""

    def test_record_captures_the_next_combination(
        self, field: HotkeyField, qtbot: QtBot
    ) -> None:
        """Pressing Ctrl+Shift+F9 while recording fills the field."""
        with qtbot.waitSignal(field.recording_changed) as started:
            field.record_button.click()

        assert started.args == [True]
        assert field.recording
        assert field.record_button.text() == RECORDING_TEXT
        assert field.edit.text() == ""
        assert field.edit.placeholderText() == RECORDING_HINT
        assert field.edit.isReadOnly()

        with qtbot.waitSignal(field.recording_changed) as stopped:
            QTest.keyClick(field.edit, Qt.Key.Key_F9, CTRL | SHIFT)

        assert stopped.args == [False]
        assert not field.recording
        assert field.edit.text() == "Ctrl+Shift+F9"
        assert field.record_button.text() == RECORD_TEXT
        assert field.edit.placeholderText() == ""
        assert not field.edit.isReadOnly()

    def test_keys_reach_the_field_wherever_focus_is(
        self, field: HotkeyField, qtbot: QtBot
    ) -> None:
        """Tab is recorded, not used to move focus, from any widget."""
        other = QWidget()
        qtbot.addWidget(other)
        field.start_recording()

        QTest.keyClick(other, Qt.Key.Key_Tab, ALT)

        assert field.edit.text() == "Alt+Tab"

    def test_modifiers_preview_while_held(self, field: HotkeyField) -> None:
        """Holding modifiers shows them; releasing all clears the preview."""
        field.start_recording()

        QTest.keyPress(field.edit, Qt.Key.Key_Control, CTRL)
        QTest.keyPress(field.edit, Qt.Key.Key_Shift, CTRL | SHIFT)
        held = field.edit.text()
        QTest.keyRelease(field.edit, Qt.Key.Key_Shift, NONE)

        assert held == "Ctrl+Shift+…"
        assert field.edit.text() == ""
        assert field.recording

    def test_escape_cancels_and_keeps_the_old_value(
        self, field: HotkeyField, qtbot: QtBot
    ) -> None:
        """Esc alone stops recording without changing the hotkey."""
        field.start_recording()

        with qtbot.waitSignal(field.recording_changed) as stopped:
            QTest.keyClick(field.edit, Qt.Key.Key_Escape)

        assert stopped.args == [False]
        assert field.edit.text() == "Ctrl+PrtSc"

    def test_escape_with_modifiers_is_recorded(self, field: HotkeyField) -> None:
        """Ctrl+Esc is a combination, not a cancel."""
        field.start_recording()

        QTest.keyClick(field.edit, Qt.Key.Key_Escape, CTRL)

        assert field.edit.text() == "Ctrl+Escape"

    def test_clicking_record_again_cancels(self, field: HotkeyField) -> None:
        """The button toggles recording off, restoring the previous value."""
        field.start_recording()
        QTest.keyPress(field.edit, Qt.Key.Key_Control, CTRL)

        field.record_button.click()

        assert not field.recording
        assert field.edit.text() == "Ctrl+PrtSc"

    def test_print_screen_records_on_release(self, field: HotkeyField) -> None:
        """Windows sends PrtSc only as a key release."""
        field.start_recording()

        QTest.keyRelease(field.edit, Qt.Key.Key_Print, CTRL)

        assert field.edit.text() == "Ctrl+PrtSc"
        assert not field.recording

    def test_other_releases_are_ignored(self, field: HotkeyField) -> None:
        """Releasing the Space that pressed Record doesn't record Space."""
        field.start_recording()

        QTest.keyRelease(field.edit, Qt.Key.Key_Space)

        assert field.recording
        assert field.edit.text() == ""

    def test_unsupported_key_asks_for_another(self, field: HotkeyField) -> None:
        """A key Windows can't register keeps recording and says why."""
        field.start_recording()

        QTest.keyClick(field.edit, Qt.Key.Key_VolumeUp)

        assert field.recording
        assert field.edit.placeholderText() == UNSUPPORTED_HINT
        QTest.keyClick(field.edit, Qt.Key.Key_F2)
        assert field.edit.text() == "F2"

    def test_keys_after_recording_are_typed_normally(self, field: HotkeyField) -> None:
        """Once recorded, the field stops listening and accepts typing."""
        field.start_recording()
        QTest.keyClick(field.edit, Qt.Key.Key_F2)

        QTest.keyClicks(field.edit, "+x")

        assert field.edit.text() == "F2+x"

    def test_shortcuts_do_not_fire_while_recording(self, field: HotkeyField) -> None:
        """The field claims shortcut overrides so dialog shortcuts stay quiet."""
        field.start_recording()
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, NONE)

        handled = QApplication.sendEvent(field.edit, override)

        assert handled
        assert override.isAccepted()
        assert field.recording

    def test_window_deactivation_cancels(self, field: HotkeyField) -> None:
        """Switching away stops recording so hotkeys don't stay paused."""
        field.start_recording()

        QApplication.sendEvent(field, QEvent(QEvent.Type.WindowDeactivate))

        assert not field.recording
        assert field.edit.text() == "Ctrl+PrtSc"

    def test_other_window_deactivation_is_ignored(
        self, field: HotkeyField, qtbot: QtBot
    ) -> None:
        """Only this field's window losing focus cancels."""
        other = QWidget()
        qtbot.addWidget(other)
        field.start_recording()

        QApplication.sendEvent(other, QEvent(QEvent.Type.WindowDeactivate))

        assert field.recording


class TestNativeCapture:
    """With a keyboard hook, keys Windows would take are recorded too."""

    @pytest.fixture
    def capture(self) -> FakeCapture:
        """The hook the field records through."""
        return FakeCapture()

    @pytest.fixture
    def hooked(self, qtbot: QtBot, capture: FakeCapture) -> HotkeyField:
        """A field holding Ctrl+PrtSc that records through ``capture``."""
        created = HotkeyField(capture=capture)
        qtbot.addWidget(created)
        created.edit.setText("Ctrl+PrtSc")
        created.show()
        qtbot.waitExposed(created)
        return created

    def test_hook_runs_only_while_recording(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """Recording installs the hook; finishing removes it."""
        idle = capture.callback
        hooked.start_recording()
        during = capture.callback

        capture.tap(VK_CTRL, VK_A)

        assert idle is None
        assert during is not None
        assert capture.callback is None
        assert hooked.edit.text() == "Ctrl+A"
        assert not hooked.recording

    def test_print_screen_is_recorded(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """PrtSc, which the Snipping Tool or a hotkey takes, records."""
        hooked.start_recording()

        capture.tap(VK_LSHIFT, VK_PRTSC)

        assert hooked.edit.text() == "Shift+PrtSc"

    def test_numpad_is_recorded(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """Numpad keys keep their own identity."""
        hooked.start_recording()

        capture.tap(VK_LWIN, VK_NUM_5)

        assert hooked.edit.text() == "Win+Num 5"

    def test_combination_is_kept_when_its_key_comes_up(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """The key's release is swallowed too, so recording ends on it."""
        hooked.start_recording()
        capture.press(VK_CTRL)
        capture.press(VK_A)
        shown = hooked.edit.text()
        still = hooked.recording

        capture.release(VK_A)

        assert (shown, still) == ("Ctrl+A", True)
        assert not hooked.recording
        assert hooked.edit.text() == "Ctrl+A"

    def test_modifiers_preview_and_release(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """Both Shift keys count once; releasing all clears the preview."""
        hooked.start_recording()
        capture.press(VK_LSHIFT)
        capture.press(VK_RSHIFT)
        capture.press(VK_CTRL)
        held = hooked.edit.text()
        capture.release(VK_LSHIFT)
        one_shift = hooked.edit.text()
        capture.release(VK_RSHIFT)
        capture.release(VK_CTRL)

        assert held == "Ctrl+Shift+…"
        assert one_shift == "Ctrl+Shift+…"
        assert hooked.edit.text() == ""
        assert hooked.recording

    def test_print_screen_release_alone_records(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """A keyboard that reports only PrtSc's release still records it."""
        hooked.start_recording()

        capture.release(VK_PRTSC)

        assert hooked.edit.text() == "PrtSc"
        assert not hooked.recording

    def test_stray_release_is_ignored(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """A release whose press came before recording records nothing."""
        hooked.start_recording()

        capture.release(VK_A)

        assert hooked.recording

    def test_escape_cancels(self, hooked: HotkeyField, capture: FakeCapture) -> None:
        """Esc alone stops recording and keeps the old value."""
        hooked.start_recording()

        capture.press(VK_ESCAPE)

        assert not hooked.recording
        assert capture.callback is None
        assert hooked.edit.text() == "Ctrl+PrtSc"

    def test_unsupported_key_asks_for_another(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """A key Windows hotkeys can't use keeps recording."""
        hooked.start_recording()

        capture.tap(VK_VOLUME_UP)

        assert hooked.recording
        assert hooked.edit.placeholderText() == UNSUPPORTED_HINT

    def test_idle_recording_gives_the_keyboard_back(
        self,
        hooked: HotkeyField,
        capture: FakeCapture,
        qtbot: QtBot,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Recording that sees no key for a while stops by itself."""
        monkeypatch.setattr(hotkey_field, "IDLE_TIMEOUT_MS", 20)
        hooked.start_recording()

        qtbot.waitUntil(lambda: not hooked.recording, timeout=2000)

        assert capture.callback is None
        assert hooked.edit.text() == "Ctrl+PrtSc"

    def test_qt_keys_still_record_if_the_hook_fails(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """Without the hook, keys Qt receives still record."""
        capture.accept = False
        hooked.start_recording()

        QTest.keyClick(hooked.edit, Qt.Key.Key_F7, CTRL)

        assert capture.starts == 1
        assert hooked.edit.text() == "Ctrl+F7"

    def test_modifiers_held_before_recording_count(
        self, hooked: HotkeyField, capture: FakeCapture
    ) -> None:
        """Ctrl held while clicking Record is part of the combination."""
        capture.held = frozenset({VK_CTRL})
        hooked.start_recording()
        shown = hooked.edit.text()

        capture.tap(VK_ESCAPE)

        assert shown == "Ctrl+…"
        assert hooked.edit.text() == "Ctrl+Escape"
