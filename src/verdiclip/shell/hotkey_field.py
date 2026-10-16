"""A hotkey editor: type a combination, or record it by pressing it."""

from __future__ import annotations

from typing import Final, cast, override

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLineEdit, QPushButton, QWidget

from verdiclip.platform.hotkeys import Hotkey, Modifier

RECORD_TEXT: Final = "Record"
RECORDING_TEXT: Final = "Press keys…"
RECORDING_HINT: Final = "Press a combination; Esc cancels"
UNSUPPORTED_HINT: Final = "That key can't be a hotkey; press another"
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
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
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
        else:
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
