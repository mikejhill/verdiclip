"""Tests for the settings dialog's validation and round-tripping."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QTabWidget
from pytestqt.qtbot import QtBot

from verdiclip.document.style import Color
from verdiclip.settings import ImageFormat, Settings, Theme, UpdateSettings
from verdiclip.shell.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot: QtBot, tmp_path: Path) -> Iterator[SettingsDialog]:
    """A dialog over settings that save into ``tmp_path``."""
    base = Settings()
    settings = replace(
        base,
        output=replace(base.output, directory=tmp_path, image_format=ImageFormat.JPG),
        capture=replace(base.capture, open_editor=False, copy_to_clipboard=True),
        appearance=replace(base.appearance, theme=Theme.DARK),
    )
    created = SettingsDialog(settings)
    qtbot.addWidget(created)
    yield created
    # A recording left running would take every key from later tests
    created.reject()


class TestSettingsDialog:
    """Tests for SettingsDialog."""

    def test_unchanged_dialog_returns_same_settings(
        self, dialog: SettingsDialog, tmp_path: Path
    ) -> None:
        """Opening and accepting without edits changes nothing."""
        result = dialog.result_settings()

        assert result.output.directory == tmp_path
        assert result.output.image_format is ImageFormat.JPG
        assert (result.capture.open_editor, result.capture.copy_to_clipboard) == (
            False,
            True,
        )
        assert result.appearance.theme is Theme.DARK
        assert result.hotkeys == Settings().hotkeys
        assert result.editor.stroke_color == Settings().editor.stroke_color

    def test_hotkeys_are_shown_friendly_and_stored_canonical(
        self, dialog: SettingsDialog
    ) -> None:
        """Hotkeys display as "Ctrl+PrtSc" and save as "ctrl+print_screen"."""
        edit = dialog.hotkey_edit("fullscreen")
        shown = edit.text()

        edit.setText("Shift + Ctrl + F9")

        assert shown == "Ctrl+PrtSc"
        assert dialog.result_settings().hotkeys.fullscreen == "ctrl+shift+f9"

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("ctrl+", id="no-key"),
            pytest.param("ctrl+banana", id="unknown-key"),
            pytest.param("alt+print_screen", id="duplicate-of-window"),
        ],
    )
    def test_invalid_hotkey_disables_ok(
        self, dialog: SettingsDialog, text: str
    ) -> None:
        """Invalid or duplicate hotkeys block OK until fixed."""
        dialog.hotkey_edit("region").setText(text)

        assert not dialog.ok_enabled()

    def test_recorded_hotkey_is_validated_and_stored(
        self, dialog: SettingsDialog, qtbot: QtBot
    ) -> None:
        """Recording fills the field, then it validates like typed text."""
        field = dialog.hotkey_field("region")
        with qtbot.waitSignal(dialog.hotkey_recording_changed) as started:
            field.start_recording()
        status_while_recording = dialog.hotkey_status("region")

        with qtbot.waitSignal(dialog.hotkey_recording_changed) as stopped:
            QTest.keyClick(field.edit, Qt.Key.Key_F8, Qt.KeyboardModifier.AltModifier)

        assert (started.args, stopped.args) == ([True], [False])
        assert status_while_recording == ""
        assert dialog.hotkey_status("region") == "✓"
        assert dialog.result_settings().hotkeys.region == "alt+f8"

    def test_ok_is_off_while_recording(self, dialog: SettingsDialog) -> None:
        """An invalid value can't be accepted by recording, then cancelling."""
        dialog.hotkey_edit("region").setText("ctrl+banana")
        dialog.hotkey_field("region").start_recording()
        recording_ok = dialog.ok_enabled()

        QTest.keyClick(dialog.hotkey_edit("region"), Qt.Key.Key_Escape)

        assert not recording_ok
        assert dialog.hotkey_edit("region").text() == "ctrl+banana"
        assert not dialog.ok_enabled()

    def test_recorded_duplicate_is_flagged(self, dialog: SettingsDialog) -> None:
        """A recorded combination already in use blocks OK."""
        field = dialog.hotkey_field("region")
        field.start_recording()

        QTest.keyClick(field.edit, Qt.Key.Key_Print, Qt.KeyboardModifier.AltModifier)

        assert dialog.hotkey_status("window") == "Also used by Region or window"
        assert not dialog.ok_enabled()

    def test_fields_record_through_the_given_capture(self, qtbot: QtBot) -> None:
        """The app's keyboard hook reaches every hotkey field."""
        started: list[object] = []

        class Capture:
            def start(self, callback: object) -> bool:
                started.append(callback)
                return True

            def stop(self) -> None:
                started.append(None)

            def held_modifiers(self) -> frozenset[int]:
                return frozenset[int]()

        hooked = SettingsDialog(Settings(), key_capture=Capture())
        qtbot.addWidget(hooked)

        hooked.hotkey_field("window").start_recording()
        hooked.reject()

        assert len(started) == 2
        assert callable(started[0])
        assert started[1] is None

    def test_switching_fields_keeps_the_hook(self, qtbot: QtBot) -> None:
        """Recording a second field leaves the shared hook with that field."""
        owners: list[object] = []

        class Capture:
            def start(self, callback: object) -> bool:
                owners.append(callback)
                return True

            def stop(self) -> None:
                owners.append(None)

            def held_modifiers(self) -> frozenset[int]:
                return frozenset[int]()

        hooked = SettingsDialog(Settings(), key_capture=Capture())
        qtbot.addWidget(hooked)
        hooked.hotkey_field("region").start_recording()

        hooked.hotkey_field("window").start_recording()

        assert owners[-1] is not None
        assert getattr(owners[-1], "__self__", None) is hooked.hotkey_field("window")
        hooked.reject()

    def test_one_field_records_at_a_time(self, dialog: SettingsDialog) -> None:
        """Starting another recording cancels the first."""
        dialog.hotkey_field("region").start_recording()

        dialog.hotkey_field("window").start_recording()

        assert not dialog.hotkey_field("region").recording
        assert dialog.hotkey_edit("region").text() == "PrtSc"
        assert dialog.hotkey_field("window").recording

    def test_closing_stops_recording(
        self, dialog: SettingsDialog, qtbot: QtBot
    ) -> None:
        """Cancel during a recording keeps the old value and resumes hotkeys."""
        dialog.hotkey_field("repeat").start_recording()

        with qtbot.waitSignal(dialog.hotkey_recording_changed) as stopped:
            dialog.reject()

        assert stopped.args == [False]
        assert not dialog.hotkey_field("repeat").recording
        assert dialog.hotkey_edit("repeat").text() == "Shift+PrtSc"

    def test_switching_tabs_stops_recording(self, dialog: SettingsDialog) -> None:
        """Leaving the Hotkeys tab cancels a recording there."""
        tabs = dialog.findChild(QTabWidget)
        assert tabs is not None
        tabs.setCurrentIndex(1)
        dialog.hotkey_field("region").start_recording()

        tabs.setCurrentIndex(0)

        assert not dialog.hotkey_field("region").recording

    def test_empty_hotkey_disables_binding(self, dialog: SettingsDialog) -> None:
        """An empty hotkey is allowed and turns that binding off."""
        dialog.hotkey_edit("repeat").setText("")

        assert dialog.ok_enabled()
        assert dialog.result_settings().hotkeys.repeat == ""

    def test_bad_filename_pattern_disables_ok(self, dialog: SettingsDialog) -> None:
        """Unknown tokens in the file name pattern block OK."""
        pattern = next(
            e
            for e in dialog.findChildren(QLineEdit)
            if e.text() == Settings().output.filename_pattern
        )

        pattern.setText("Shot {nope}")

        assert not dialog.ok_enabled()

    def test_stored_odd_hotkey_text_is_shown_verbatim(self, qtbot: QtBot) -> None:
        """A hand-edited, unparseable stored hotkey is shown as-is for fixing."""
        base = Settings()
        odd = SettingsDialog(
            replace(base, hotkeys=replace(base.hotkeys, repeat="hyper+q"))
        )
        qtbot.addWidget(odd)

        assert odd.hotkey_edit("repeat").text() == "hyper+q"
        assert not odd.ok_enabled()

    def test_editor_defaults_round_trip(self, qtbot: QtBot) -> None:
        """Editor defaults load into the controls and come back unchanged."""
        base = Settings()
        custom = replace(
            base,
            editor=replace(
                base.editor, stroke_color=Color(0, 128, 0), stroke_width=7, font_size=22
            ),
        )
        created = SettingsDialog(custom)
        qtbot.addWidget(created)

        result = created.result_settings().editor

        assert (result.stroke_color, result.stroke_width, result.font_size) == (
            Color(0, 128, 0),
            7,
            22,
        )

    def test_after_capture_actions_combine_but_not_all_off(
        self, dialog: SettingsDialog
    ) -> None:
        """UX-CAP-14: any combination is allowed except none at all."""
        boxes = {b.text(): b for b in dialog.findChildren(QCheckBox)}
        boxes["Open in the editor"].setChecked(True)
        boxes["Save to the output folder"].setChecked(True)
        combined = dialog.result_settings().capture

        for label in (
            "Open in the editor",
            "Copy to the clipboard",
            "Save to the output folder",
        ):
            boxes[label].setChecked(False)

        assert (
            combined.open_editor,
            combined.copy_to_clipboard,
            combined.save_to_file,
        ) == (
            True,
            True,
            True,
        )
        assert not dialog.ok_enabled()

    def test_theme_choice_is_returned(self, dialog: SettingsDialog) -> None:
        """UX-G-08: the theme picker offers system, light, and dark."""
        combo = next(c for c in dialog.findChildren(QComboBox) if c.count() == 3)
        combo.setCurrentIndex(combo.findData(Theme.LIGHT.value))

        assert dialog.result_settings().appearance.theme is Theme.LIGHT

    def test_close_prompt_setting_round_trips(self, dialog: SettingsDialog) -> None:
        """UX-G-06: the close prompt is on by default and can be turned off."""
        box = next(
            b
            for b in dialog.findChildren(QCheckBox)
            if b.text().startswith("Ask before")
        )
        default_on = box.isChecked()

        box.setChecked(False)

        assert default_on
        assert dialog.result_settings().editor.confirm_unsaved_close is False

    def test_make_default_turns_on_open_with(
        self, dialog: SettingsDialog, qtbot: QtBot
    ) -> None:
        """UX-TRY-07: the button checks Open with and asks the app to act."""
        with qtbot.waitSignal(dialog.make_default_requested):
            dialog.make_default_button().click()

        assert dialog.result_settings().integration.open_with is True

    def test_start_menu_checkbox_round_trips(self, dialog: SettingsDialog) -> None:
        """UX-TRY-08: the Start menu checkbox is on by default and can be cleared."""
        box = next(
            b for b in dialog.findChildren(QCheckBox) if "Start menu" in b.text()
        )
        default_on = box.isChecked()

        box.setChecked(False)

        assert default_on
        assert dialog.result_settings().integration.start_menu is False


class TestUpdateSettings:
    """Settings → General: checking for and installing updates."""

    @staticmethod
    def boxes(dialog: SettingsDialog) -> tuple[QCheckBox, QCheckBox]:
        """Return the check and install checkboxes."""
        found = {b.text(): b for b in dialog.findChildren(QCheckBox)}
        return (
            found["Check for updates automatically"],
            found["Install updates automatically when no image is open"],
        )

    def test_both_on_by_default(self, dialog: SettingsDialog) -> None:
        """New installs check for and install updates."""
        check, install = self.boxes(dialog)

        assert check.isChecked()
        assert install.isChecked()
        assert install.isEnabled()

    def test_install_needs_checking(self, dialog: SettingsDialog) -> None:
        """Installing is off the table while checking is off."""
        check, install = self.boxes(dialog)

        check.setChecked(False)

        assert not install.isEnabled()
        check.setChecked(True)
        assert install.isEnabled()

    def test_choices_round_trip(self, qtbot: QtBot) -> None:
        """Stored choices show, and edits come back in the result."""
        stored = replace(Settings(), updates=UpdateSettings(check=True, install=False))
        dialog = SettingsDialog(stored)
        qtbot.addWidget(dialog)
        check, install = self.boxes(dialog)
        assert not install.isChecked()

        check.setChecked(False)

        assert dialog.result_settings().updates == UpdateSettings(
            check=False, install=False
        )

    def test_install_starts_disabled_when_checking_is_off(self, qtbot: QtBot) -> None:
        """A stored "don't check" greys out installing from the start."""
        stored = replace(Settings(), updates=UpdateSettings(check=False))
        dialog = SettingsDialog(stored)
        qtbot.addWidget(dialog)

        assert not self.boxes(dialog)[1].isEnabled()
