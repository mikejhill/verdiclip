"""Tests for the settings dialog's validation and round-tripping."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit
from pytestqt.qtbot import QtBot

from verdiclip.document.style import Color
from verdiclip.settings import ImageFormat, Settings, Theme
from verdiclip.shell.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot: QtBot, tmp_path: Path) -> SettingsDialog:
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
    return created


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
