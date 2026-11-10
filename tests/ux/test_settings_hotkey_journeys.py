"""Settings hotkey validation remains visible and survives recording cancellation."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTabWidget
from pytestqt.qtbot import QtBot

from verdiclip.settings import OutputSettings, Settings
from verdiclip.shell.settings_dialog import SettingsDialog

pytestmark = pytest.mark.ux


@pytest.fixture
def hotkeys_dialog(qtbot: QtBot, tmp_path: Path) -> Iterator[SettingsDialog]:
    """Open Hotkeys through its visible tab, with no real keyboard hook."""
    dialog = SettingsDialog(Settings(output=OutputSettings(directory=tmp_path)))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitUntil(dialog.isVisible)
    try:
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "Hotkeys")
        bar = tabs.tabBar()
        QTest.mouseClick(
            bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(index).center()
        )
        assert dialog.hotkey_edit("region").isVisible()
        yield dialog
    finally:
        dialog.reject()


@pytest.mark.parametrize(
    ("text", "status", "can_save"),
    [
        pytest.param("Ctrl+F8", "Valid", True, id="valid"),
        pytest.param(
            "Ctrl+banana", "Unknown hotkey key: 'banana'", False, id="invalid"
        ),
    ],
)
def test_hotkey_status_and_record_cancel_are_visible(
    hotkeys_dialog: SettingsDialog, text: str, status: str, *, can_save: bool
) -> None:
    """UX-TRY-04/09: typing shows validation; Esc restores it after Record."""
    dialog = hotkeys_dialog
    field = dialog.hotkey_field("region")
    assert dialog.hotkey_status("region") == "Valid"
    QTest.mouseClick(field.edit, Qt.MouseButton.LeftButton)
    QTest.keyClick(field.edit, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(field.edit, Qt.Key.Key_Backspace)
    QTest.keyClicks(field.edit, text)
    assert dialog.hotkey_status("region") == status
    assert dialog.ok_enabled() is can_save

    QTest.mouseClick(field.record_button, Qt.MouseButton.LeftButton)
    assert field.edit.placeholderText() == "Press a combination; Esc cancels"
    assert field.edit.isReadOnly()
    assert dialog.hotkey_status("region") == ""
    assert not dialog.ok_enabled()
    QTest.keyClick(field.edit, Qt.Key.Key_Escape)

    assert dialog.isVisible()
    assert field.record_button.text() == "Record"
    assert not field.edit.isReadOnly()
    assert field.edit.text() == text
    assert dialog.hotkey_status("region") == status
    assert dialog.ok_enabled() is can_save


def test_clearing_a_hotkey_visibly_disables_it(hotkeys_dialog: SettingsDialog) -> None:
    """UX-TRY-04: an empty shortcut says Off and can be saved."""
    field = hotkeys_dialog.hotkey_edit("region")
    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(field, Qt.Key.Key_Backspace)
    assert field.text() == ""
    assert hotkeys_dialog.hotkey_status("region") == "Off"
    assert hotkeys_dialog.ok_enabled()
