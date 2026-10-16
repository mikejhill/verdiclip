"""A hotkey editor: type a combination, or record it by pressing it."""

from __future__ import annotations

from typing import Final, cast, override

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QPushButton, QWidget

from verdiclip.platform.hotkeys import Hotkey, Modifier
from verdiclip.platform.keyboard_hook import KeyCapture

RECORD_TEXT: Final = "Record"
RECORDING_TEXT: Final = "Press keys…"
RECORDING_HINT: Final = "Press a combination; Esc cancels"
UNSUPPORTED_HINT: Final = "That key can't be a hotkey; press another"
# Recording swallows every key on the PC; give the keyboard back if left
IDLE_TIMEOUT_MS: Final = 20_000
_VK_ESCAPE: Final = 0x1B
_VK_SNAPSHOT: Final = 0x2C
_MODIFIER_VKS: Final = {
    0x10: Modifier.SHIFT,
    0xA0: Modifier.SHIFT,
    0xA1: Modifier.SHIFT,
    0x11: Modifier.CTRL,
    0xA2: Modifier.CTRL,
    0xA3: Modifier.CTRL,
    0x12: Modifier.ALT,
    0xA4: Modifier.ALT,
    0xA5: Modifier.ALT,
    0x5B: Modifier.WIN,
    0x5C: Modifier.WIN,
}
_MODIFIER_KEYS: Final = {
    Qt.Key.Key_Control: Modifier.CTRL,
    Qt.Key.Key_Shift: Modifier.SHIFT,
    Qt.Key.Key_Alt: Modifier.ALT,
    Qt.Key.Key_AltGr: Modifier.ALT,
    Qt.Key.Key_Meta: Modifier.WIN,
    Qt.Key.Key_Super_L: Modifier.WIN,
    Qt.Key.Key_Super_R: Modifier.WIN,
}
_MODIFIERS: Final = (
    (Qt.KeyboardModifier.ControlModifier, Modifier.CTRL),
    (Qt.KeyboardModifier.AltModifier, Modifier.ALT),
    (Qt.KeyboardModifier.ShiftModifier, Modifier.SHIFT),
    (Qt.KeyboardModifier.MetaModifier, Modifier.WIN),
)
# Qt keys whose Win32 virtual-key code isn't their own value (letters and
# digits share ASCII codes; F-keys are a run)
_QT_TO_VK: Final = {
    Qt.Key.Key_Print: 0x2C,
    Qt.Key.Key_SysReq: 0x2C,  # Alt+PrtSc
    Qt.Key.Key_Space: 0x20,
    Qt.Key.Key_Insert: 0x2D,
    Qt.Key.Key_Delete: 0x2E,
    Qt.Key.Key_Home: 0x24,
    Qt.Key.Key_End: 0x23,
    Qt.Key.Key_Pause: 0x13,
    Qt.Key.Key_ScrollLock: 0x91,
    Qt.Key.Key_PageUp: 0x21,
    Qt.Key.Key_PageDown: 0x22,
    Qt.Key.Key_Left: 0x25,
    Qt.Key.Key_Up: 0x26,
    Qt.Key.Key_Right: 0x27,
    Qt.Key.Key_Down: 0x28,
    Qt.Key.Key_Tab: 0x09,
    Qt.Key.Key_Backtab: 0x09,  # Shift+Tab
    Qt.Key.Key_Return: 0x0D,
    Qt.Key.Key_Enter: 0x0D,
    Qt.Key.Key_Escape: 0x1B,
    Qt.Key.Key_Backspace: 0x08,
    Qt.Key.Key_CapsLock: 0x14,
    Qt.Key.Key_NumLock: 0x90,
    Qt.Key.Key_Menu: 0x5D,
    Qt.Key.Key_Semicolon: 0xBA,
    Qt.Key.Key_Equal: 0xBB,
    Qt.Key.Key_Comma: 0xBC,
    Qt.Key.Key_Minus: 0xBD,
    Qt.Key.Key_Period: 0xBE,
    Qt.Key.Key_Slash: 0xBF,
    Qt.Key.Key_QuoteLeft: 0xC0,
    Qt.Key.Key_BracketLeft: 0xDB,
    Qt.Key.Key_Backslash: 0xDC,
    Qt.Key.Key_BracketRight: 0xDD,
    Qt.Key.Key_Apostrophe: 0xDE,
}
_QT_KEYPAD_TO_VK: Final = {
    **{Qt.Key(int(Qt.Key.Key_0) + n): 0x60 + n for n in range(10)},
    Qt.Key.Key_Asterisk: 0x6A,
    Qt.Key.Key_Plus: 0x6B,
    Qt.Key.Key_Minus: 0x6D,
    Qt.Key.Key_Period: 0x6E,
    Qt.Key.Key_Slash: 0x6F,
}


def modifiers_of(event: QKeyEvent) -> frozenset[Modifier]:
    """Return the hotkey modifiers held during ``event``."""
    held = event.modifiers()
    return frozenset(m for flag, m in _MODIFIERS if held & flag)


def hotkey_from_event(event: QKeyEvent) -> Hotkey | None:
    """Return the combination ``event`` presses, or None if it isn't supported."""
    modifiers = modifiers_of(event)
    # Windows reports the layout-independent virtual key; prefer it
    # Its absence means no fallback: numpad 1 must not become the top-row 1
    if native := event.nativeVirtualKey():
        return Hotkey.from_virtual_key(modifiers, native)
    key = event.key()
    keypad = bool(event.modifiers() & Qt.KeyboardModifier.KeypadModifier)
    if keypad and (pad := _QT_KEYPAD_TO_VK.get(Qt.Key(key))) is not None:
        vk = pad
    elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        vk = int(key)
    elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        vk = 0x70 + int(key) - int(Qt.Key.Key_F1)
    elif (mapped := _QT_TO_VK.get(Qt.Key(key))) is not None:
        vk = mapped
    else:
        return None
    return Hotkey.from_virtual_key(modifiers, vk)


def _modifier_prefix(modifiers: frozenset[Modifier]) -> str:
    """Return "Ctrl+Shift+…" for the modifiers held so far."""
    return "".join(f"{m.value.title()}+" for m in Modifier if m in modifiers) + "…"


class HotkeyField(QWidget):
    """A text field for a hotkey plus a Record button that captures one."""

    recording_changed = Signal(bool)

    def __init__(
        self, parent: QWidget | None = None, *, capture: KeyCapture | None = None
    ) -> None:
        super().__init__(parent)
        # Without a capture (or if it fails) only keys Qt receives are seen
        self._capture = capture
        self._held_vks: set[int] = set()
        self._pending: tuple[int, Hotkey] | None = None
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.cancel_recording)
        self.edit = QLineEdit()
        self.record_button = QPushButton(RECORD_TEXT)
        self.record_button.setCheckable(True)
        self.record_button.setToolTip("Press the combination instead of typing it")
        self.record_button.toggled.connect(self._on_toggled)
        self._before = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.record_button)

    @property
    def recording(self) -> bool:
        """True while the next key combination will be captured."""
        return self.record_button.isChecked()

    def start_recording(self) -> None:
        """Capture the next combination pressed anywhere in the app."""
        self.record_button.setChecked(True)

    def cancel_recording(self) -> None:
        """Stop recording and restore the text from before it started."""
        self.record_button.setChecked(False)

    def _on_toggled(self, checked: bool) -> None:  # noqa: FBT001 - Qt signal
        """Start or stop listening to every key the app receives."""
        # A widget exists, so the application does too
        application = cast("QApplication", QApplication.instance())
        if checked:
            self._before = self.edit.text()
            self.edit.setReadOnly(True)
            self.edit.clear()
            self.edit.setPlaceholderText(RECORDING_HINT)
            self.record_button.setText(RECORDING_TEXT)
            application.installEventFilter(self)
            self._held_vks.clear()
            self._pending = None
            if self._capture is not None:
                self._capture.start(self._on_native_key)
            self._idle.start(IDLE_TIMEOUT_MS)
        else:
            self._idle.stop()
            if self._capture is not None:
                self._capture.stop()
            application.removeEventFilter(self)
            self.edit.setText(self._before)
            self.edit.setPlaceholderText("")
            self.edit.setReadOnly(False)
            self.record_button.setText(RECORD_TEXT)
        self.recording_changed.emit(checked)

    def _finish(self, hotkey: Hotkey) -> None:
        """Keep ``hotkey`` and stop recording."""
        self._before = hotkey.display_text
        self.record_button.setChecked(False)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Turn key presses into a hotkey; let nothing else see them."""
        kind = event.type()
        if not isinstance(event, QKeyEvent):
            if kind == QEvent.Type.WindowDeactivate and watched is self.window():
                self.cancel_recording()
            return False
        if kind == QEvent.Type.ShortcutOverride:
            # Accepting it stops dialog shortcuts (Esc, Alt mnemonics) firing
            event.accept()
            return True
        self._on_key(event, pressed=kind == QEvent.Type.KeyPress)
        return True

    def _on_native_key(self, vk: int, pressed: bool) -> None:  # noqa: FBT001
        """Record from a Win32 key seen before Windows acted on it.

        A combination is kept when its key comes back up, so neither half of
        the press reaches Windows (PrtSc would open the Snipping Tool).
        """
        self._idle.start(IDLE_TIMEOUT_MS)
        if vk in _MODIFIER_VKS:
            if pressed:
                self._held_vks.add(vk)
            else:
                self._held_vks.discard(vk)
            held = frozenset(_MODIFIER_VKS[v] for v in self._held_vks)
            self.edit.setText(_modifier_prefix(held) if held else "")
            return
        held = frozenset(_MODIFIER_VKS[v] for v in self._held_vks)
        if pressed:
            if vk == _VK_ESCAPE and not held:
                self.cancel_recording()
                return
            hotkey = Hotkey.from_virtual_key(held, vk)
            if hotkey is None:
                self.edit.clear()
                self.edit.setPlaceholderText(UNSUPPORTED_HINT)
                return
            self._pending = (vk, hotkey)
            self.edit.setText(hotkey.display_text)
        elif self._pending is not None and self._pending[0] == vk:
            self._finish(self._pending[1])
        elif vk == _VK_SNAPSHOT:
            # Some keyboards report PrtSc only as a release
            self._finish(Hotkey(held, "print_screen"))

    def _on_key(self, event: QKeyEvent, *, pressed: bool) -> None:
        """Preview modifiers, cancel on Esc, or keep a full combination."""
        key = event.key()
        modifiers = modifiers_of(event)
        if (changed := _MODIFIER_KEYS.get(Qt.Key(key))) is not None:
            # Whether a modifier's own event includes it varies by platform
            held = modifiers | {changed} if pressed else modifiers - {changed}
            self.edit.setText(_modifier_prefix(held) if held else "")
            return
        # Windows sends PrtSc only as a release; other releases end a press
        # that may have begun before recording (Space on the button)
        if not pressed and key not in {Qt.Key.Key_Print, Qt.Key.Key_SysReq}:
            return
        if key == Qt.Key.Key_Escape and not modifiers:
            self.cancel_recording()
            return
        hotkey = hotkey_from_event(event)
        if hotkey is None:
            self.edit.clear()
            self.edit.setPlaceholderText(UNSUPPORTED_HINT)
            return
        self._finish(hotkey)
