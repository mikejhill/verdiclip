"""Tests for the Settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QTabWidget

from verdiclip.ui.settings_dialog import SettingsDialog


class TestSettingsDialogCreation:
    def test_creates_with_config_and_has_five_tabs(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)
        assert len(tabs) == 1
        assert tabs[0].count() == 5

    def test_window_title(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        assert dialog.windowTitle() == "VerdiClip Settings"

    def test_minimum_size(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        assert dialog.minimumWidth() == 500
        assert dialog.minimumHeight() == 400

    def test_tab_names(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)[0]
        names = [tabs.tabText(i) for i in range(tabs.count())]
        assert names == ["Capture", "Save", "Editor", "Hotkeys", "General"]

    def test_has_ok_and_cancel_buttons(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        button_boxes = dialog.findChildren(QDialogButtonBox)
        assert len(button_boxes) == 1
        box = button_boxes[0]
        assert box.button(QDialogButtonBox.StandardButton.Ok) is not None
        assert box.button(QDialogButtonBox.StandardButton.Cancel) is not None


class TestLoadValues:
    def test_capture_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.default_action", "clipboard")
        tmp_config.set("capture.include_cursor", True)
        tmp_config.set("capture.region_magnifier", False)
        tmp_config.set("capture.window_decorations", False)

        dialog = SettingsDialog(tmp_config)
        assert dialog._capture_action.currentIndex() == 2
        assert dialog._include_cursor.isChecked() is True
        assert dialog._region_magnifier.isChecked() is False
        assert dialog._window_decorations.isChecked() is False

    def test_save_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.default_directory", r"C:\Screenshots")
        tmp_config.set("save.default_format", "jpg")
        tmp_config.set("save.jpg_quality", 75)
        tmp_config.set("save.auto_save_enabled", True)
        tmp_config.set("save.filename_pattern", "Img_{date}")

        dialog = SettingsDialog(tmp_config)
        assert dialog._save_dir.text() == r"C:\Screenshots"
        assert dialog._save_format.currentText() == "JPG"
        assert dialog._jpg_quality.value() == 75
        assert dialog._auto_save.isChecked() is True
        assert dialog._filename_pattern.text() == "Img_{date}"

    def test_editor_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("editor.default_stroke_width", 7)
        tmp_config.set("editor.default_font_family", "Consolas")
        tmp_config.set("editor.default_font_size", 20)

        dialog = SettingsDialog(tmp_config)
        assert dialog._stroke_width.value() == 7
        assert dialog._font_family.currentText() == "Consolas"
        assert dialog._font_size.value() == 20

    def test_hotkey_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.region", "ctrl+r")
        tmp_config.set("hotkeys.fullscreen", "ctrl+f")
        tmp_config.set("hotkeys.window", "ctrl+w")
        tmp_config.set("hotkeys.window_pick", "ctrl+p")
        tmp_config.set("hotkeys.repeat", "ctrl+l")

        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_region.text() == "ctrl+r"
        assert dialog._hotkey_fullscreen.text() == "ctrl+f"
        assert dialog._hotkey_window.text() == "ctrl+w"
        assert dialog._hotkey_window_pick.text() == "ctrl+p"
        assert dialog._hotkey_repeat.text() == "ctrl+l"

    def test_general_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("startup.run_at_login", True)
        tmp_config.set("startup.minimize_to_tray", False)

        dialog = SettingsDialog(tmp_config)
        assert dialog._run_at_login.isChecked() is True
        assert dialog._minimize_to_tray.isChecked() is False


class TestSaveAndClose:
    def test_capture_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._capture_action.setCurrentIndex(1)  # file
        dialog._include_cursor.setChecked(True)
        dialog._region_magnifier.setChecked(False)
        dialog._window_decorations.setChecked(False)

        dialog._save_and_close()

        assert tmp_config.get("capture.default_action") == "file"
        assert tmp_config.get("capture.include_cursor") is True
        assert tmp_config.get("capture.region_magnifier") is False
        assert tmp_config.get("capture.window_decorations") is False

    def test_save_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._save_dir.setText(r"D:\Captures")
        dialog._save_format.setCurrentText("JPG")
        dialog._jpg_quality.setValue(60)
        dialog._auto_save.setChecked(True)
        dialog._filename_pattern.setText("Cap_{counter}")

        dialog._save_and_close()

        assert tmp_config.get("save.default_directory") == r"D:\Captures"
        assert tmp_config.get("save.default_format") == "jpg"
        assert tmp_config.get("save.jpg_quality") == 60
        assert tmp_config.get("save.auto_save_enabled") is True
        assert tmp_config.get("save.filename_pattern") == "Cap_{counter}"

    def test_editor_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._stroke_width.setValue(10)
        dialog._font_family.setCurrentText("Consolas")
        dialog._font_size.setValue(24)

        dialog._save_and_close()

        assert tmp_config.get("editor.default_stroke_width") == 10
        assert tmp_config.get("editor.default_font_family") == "Consolas"
        assert tmp_config.get("editor.default_font_size") == 24

    def test_hotkey_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_region.setText("alt+1")
        dialog._hotkey_fullscreen.setText("alt+2")
        dialog._hotkey_window.setText("alt+3")
        dialog._hotkey_window_pick.setText("alt+4")
        dialog._hotkey_repeat.setText("alt+5")

        dialog._save_and_close()

        assert tmp_config.get("hotkeys.region") == "alt+1"
        assert tmp_config.get("hotkeys.fullscreen") == "alt+2"
        assert tmp_config.get("hotkeys.window") == "alt+3"
        assert tmp_config.get("hotkeys.window_pick") == "alt+4"
        assert tmp_config.get("hotkeys.repeat") == "alt+5"

    def test_general_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._run_at_login.setChecked(True)
        dialog._minimize_to_tray.setChecked(False)

        dialog._save_and_close()

        assert tmp_config.get("startup.run_at_login") is True
        assert tmp_config.get("startup.minimize_to_tray") is False
