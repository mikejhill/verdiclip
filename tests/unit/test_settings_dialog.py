"""Tests for the Settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QTabWidget

from verdiclip.ui.settings_dialog import SettingsDialog


class TestSettingsDialogCreation:
    def test_creates_with_config_and_has_five_tabs(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)
        assert len(tabs) == 1, f"Expected len(tabs) to equal 1, got {len(tabs)}"
        assert tabs[0].count() == 5, f"Expected tabs[0].count() to equal 5, got {tabs[0].count()}"

    def test_window_title(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        assert dialog.windowTitle() == "VerdiClip Settings", (
            f"Expected dialog.windowTitle() to equal 'VerdiClip Settings',"
            f" got {dialog.windowTitle()}"
        )

    def test_minimum_size(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        assert dialog.minimumWidth() == 500, (
            f"Expected dialog.minimumWidth() to equal 500, got {dialog.minimumWidth()}"
        )
        assert dialog.minimumHeight() == 400, (
            f"Expected dialog.minimumHeight() to equal 400, got {dialog.minimumHeight()}"
        )

    def test_tab_names(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)[0]
        names = [tabs.tabText(i) for i in range(tabs.count())]
        assert names == ["Capture", "Save", "Editor", "Hotkeys", "General"], (
            f"Expected names to equal ['Capture', 'Save', 'Editor', 'Hotkeys', 'General'],"
            f" got {names}"
        )

    def test_has_ok_and_cancel_buttons(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        button_boxes = dialog.findChildren(QDialogButtonBox)
        assert len(button_boxes) == 1, (
            f"Expected len(button_boxes) to equal 1, got {len(button_boxes)}"
        )
        box = button_boxes[0]
        assert box.button(QDialogButtonBox.StandardButton.Ok) is not None, (
            f"Expected box.button(QDialogButtonBox.StandardButton.Ok) to not be None,"
            f" got {box.button(QDialogButtonBox.StandardButton.Ok)}"
        )
        assert box.button(QDialogButtonBox.StandardButton.Cancel) is not None, (
            f"Expected box.button(QDialogButtonBox.StandardButton.Cancel) to not be None,"
            f" got {box.button(QDialogButtonBox.StandardButton.Cancel)}"
        )


class TestLoadValues:
    def test_capture_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.default_action", "clipboard")
        tmp_config.set("capture.include_cursor", True)
        tmp_config.set("capture.region_magnifier", False)
        tmp_config.set("capture.window_decorations", False)

        dialog = SettingsDialog(tmp_config)
        assert dialog._capture_action.currentIndex() == 2, (
            f"Expected dialog._capture_action.currentIndex() to equal 2,"
            f" got {dialog._capture_action.currentIndex()}"
        )
        assert dialog._include_cursor.isChecked() is True, (
            f"Expected dialog._include_cursor.isChecked() to be True,"
            f" got {dialog._include_cursor.isChecked()}"
        )
        assert dialog._region_magnifier.isChecked() is False, (
            f"Expected dialog._region_magnifier.isChecked() to be False,"
            f" got {dialog._region_magnifier.isChecked()}"
        )
        assert dialog._window_decorations.isChecked() is False, (
            f"Expected dialog._window_decorations.isChecked() to be False,"
            f" got {dialog._window_decorations.isChecked()}"
        )

    def test_save_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.default_directory", r"C:\Screenshots")
        tmp_config.set("save.default_format", "jpg")
        tmp_config.set("save.jpg_quality", 75)
        tmp_config.set("save.auto_save_enabled", True)
        tmp_config.set("save.filename_pattern", "Img_{date}")

        dialog = SettingsDialog(tmp_config)
        assert dialog._save_dir.text() == r"C:\Screenshots", (
            f"Expected dialog._save_dir.text() to equal 'C:\\Screenshots',"
            f" got {dialog._save_dir.text()}"
        )
        assert dialog._save_format.currentText() == "JPG", (
            f"Expected dialog._save_format.currentText() to equal 'JPG',"
            f" got {dialog._save_format.currentText()}"
        )
        assert dialog._jpg_quality.value() == 75, (
            f"Expected dialog._jpg_quality.value() to equal 75, got {dialog._jpg_quality.value()}"
        )
        assert dialog._auto_save.isChecked() is True, (
            f"Expected dialog._auto_save.isChecked() to be True,"
            f" got {dialog._auto_save.isChecked()}"
        )
        assert dialog._filename_pattern.text() == "Img_{date}", (
            f"Expected dialog._filename_pattern.text() to equal 'Img_{{date}}',"
            f" got {dialog._filename_pattern.text()}"
        )

    def test_editor_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("editor.default_stroke_width", 7)
        tmp_config.set("editor.default_font_family", "Consolas")
        tmp_config.set("editor.default_font_size", 20)

        dialog = SettingsDialog(tmp_config)
        assert dialog._stroke_width.value() == 7, (
            f"Expected dialog._stroke_width.value() to equal 7, got {dialog._stroke_width.value()}"
        )
        assert dialog._font_family.currentText() == "Consolas", (
            f"Expected dialog._font_family.currentText() to equal 'Consolas',"
            f" got {dialog._font_family.currentText()}"
        )
        assert dialog._font_size.value() == 20, (
            f"Expected dialog._font_size.value() to equal 20, got {dialog._font_size.value()}"
        )

    def test_hotkey_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.region", "ctrl+r")
        tmp_config.set("hotkeys.fullscreen", "ctrl+f")
        tmp_config.set("hotkeys.window", "ctrl+w")
        tmp_config.set("hotkeys.window_pick", "ctrl+p")
        tmp_config.set("hotkeys.repeat", "ctrl+l")

        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_region.text() == "ctrl+r", (
            f"Expected dialog._hotkey_region.text() to equal 'ctrl+r',"
            f" got {dialog._hotkey_region.text()}"
        )
        assert dialog._hotkey_fullscreen.text() == "ctrl+f", (
            f"Expected dialog._hotkey_fullscreen.text() to equal 'ctrl+f',"
            f" got {dialog._hotkey_fullscreen.text()}"
        )
        assert dialog._hotkey_window.text() == "ctrl+w", (
            f"Expected dialog._hotkey_window.text() to equal 'ctrl+w',"
            f" got {dialog._hotkey_window.text()}"
        )
        assert dialog._hotkey_window_pick.text() == "ctrl+p", (
            f"Expected dialog._hotkey_window_pick.text() to equal 'ctrl+p',"
            f" got {dialog._hotkey_window_pick.text()}"
        )
        assert dialog._hotkey_repeat.text() == "ctrl+l", (
            f"Expected dialog._hotkey_repeat.text() to equal 'ctrl+l',"
            f" got {dialog._hotkey_repeat.text()}"
        )

    def test_general_settings_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("startup.run_at_login", True)
        tmp_config.set("startup.minimize_to_tray", False)

        dialog = SettingsDialog(tmp_config)
        assert dialog._run_at_login.isChecked() is True, (
            f"Expected dialog._run_at_login.isChecked() to be True,"
            f" got {dialog._run_at_login.isChecked()}"
        )
        assert dialog._minimize_to_tray.isChecked() is False, (
            f"Expected dialog._minimize_to_tray.isChecked() to be False,"
            f" got {dialog._minimize_to_tray.isChecked()}"
        )


class TestSaveAndClose:
    def test_capture_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._capture_action.setCurrentIndex(1)  # file
        dialog._include_cursor.setChecked(True)
        dialog._region_magnifier.setChecked(False)
        dialog._window_decorations.setChecked(False)

        dialog._save_and_close()

        assert tmp_config.get("capture.default_action") == "file", (
            f"Expected tmp_config.get('capture.default_action') to equal 'file',"
            f" got {tmp_config.get('capture.default_action')}"
        )
        assert tmp_config.get("capture.include_cursor") is True, (
            f"Expected tmp_config.get('capture.include_cursor') to be True,"
            f" got {tmp_config.get('capture.include_cursor')}"
        )
        assert tmp_config.get("capture.region_magnifier") is False, (
            f"Expected tmp_config.get('capture.region_magnifier') to be False,"
            f" got {tmp_config.get('capture.region_magnifier')}"
        )
        assert tmp_config.get("capture.window_decorations") is False, (
            f"Expected tmp_config.get('capture.window_decorations') to be False,"
            f" got {tmp_config.get('capture.window_decorations')}"
        )

    def test_save_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._save_dir.setText(r"D:\Captures")
        dialog._save_format.setCurrentText("JPG")
        dialog._jpg_quality.setValue(60)
        dialog._auto_save.setChecked(True)
        dialog._filename_pattern.setText("Cap_{counter}")

        dialog._save_and_close()

        assert tmp_config.get("save.default_directory") == r"D:\Captures", (
            f"Expected tmp_config.get('save.default_directory') to equal 'D:\\Captures',"
            f" got {tmp_config.get('save.default_directory')}"
        )
        assert tmp_config.get("save.default_format") == "jpg", (
            f"Expected tmp_config.get('save.default_format') to equal 'jpg',"
            f" got {tmp_config.get('save.default_format')}"
        )
        assert tmp_config.get("save.jpg_quality") == 60, (
            f"Expected tmp_config.get('save.jpg_quality') to equal 60,"
            f" got {tmp_config.get('save.jpg_quality')}"
        )
        assert tmp_config.get("save.auto_save_enabled") is True, (
            f"Expected tmp_config.get('save.auto_save_enabled') to be True,"
            f" got {tmp_config.get('save.auto_save_enabled')}"
        )
        assert tmp_config.get("save.filename_pattern") == "Cap_{counter}", (
            f"Expected tmp_config.get('save.filename_pattern') to equal 'Cap_{{counter}}',"
            f" got {tmp_config.get('save.filename_pattern')}"
        )

    def test_editor_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._stroke_width.setValue(10)
        dialog._font_family.setCurrentText("Consolas")
        dialog._font_size.setValue(24)

        dialog._save_and_close()

        assert tmp_config.get("editor.default_stroke_width") == 10, (
            f"Expected tmp_config.get('editor.default_stroke_width') to equal 10,"
            f" got {tmp_config.get('editor.default_stroke_width')}"
        )
        assert tmp_config.get("editor.default_font_family") == "Consolas", (
            f"Expected tmp_config.get('editor.default_font_family') to equal 'Consolas',"
            f" got {tmp_config.get('editor.default_font_family')}"
        )
        assert tmp_config.get("editor.default_font_size") == 24, (
            f"Expected tmp_config.get('editor.default_font_size') to equal 24,"
            f" got {tmp_config.get('editor.default_font_size')}"
        )

    def test_hotkey_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_region.setText("alt+1")
        dialog._hotkey_fullscreen.setText("alt+2")
        dialog._hotkey_window.setText("alt+3")
        dialog._hotkey_window_pick.setText("alt+4")
        dialog._hotkey_repeat.setText("alt+5")

        dialog._save_and_close()

        assert tmp_config.get("hotkeys.region") == "alt+1", (
            f"Expected tmp_config.get('hotkeys.region') to equal 'alt+1',"
            f" got {tmp_config.get('hotkeys.region')}"
        )
        assert tmp_config.get("hotkeys.fullscreen") == "alt+2", (
            f"Expected tmp_config.get('hotkeys.fullscreen') to equal 'alt+2',"
            f" got {tmp_config.get('hotkeys.fullscreen')}"
        )
        assert tmp_config.get("hotkeys.window") == "alt+3", (
            f"Expected tmp_config.get('hotkeys.window') to equal 'alt+3',"
            f" got {tmp_config.get('hotkeys.window')}"
        )
        assert tmp_config.get("hotkeys.window_pick") == "alt+4", (
            f"Expected tmp_config.get('hotkeys.window_pick') to equal 'alt+4',"
            f" got {tmp_config.get('hotkeys.window_pick')}"
        )
        assert tmp_config.get("hotkeys.repeat") == "alt+5", (
            f"Expected tmp_config.get('hotkeys.repeat') to equal 'alt+5',"
            f" got {tmp_config.get('hotkeys.repeat')}"
        )

    def test_general_settings_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._run_at_login.setChecked(True)
        dialog._minimize_to_tray.setChecked(False)

        dialog._save_and_close()

        assert tmp_config.get("startup.run_at_login") is True, (
            f"Expected tmp_config.get('startup.run_at_login') to be True,"
            f" got {tmp_config.get('startup.run_at_login')}"
        )
        assert tmp_config.get("startup.minimize_to_tray") is False, (
            f"Expected tmp_config.get('startup.minimize_to_tray') to be False,"
            f" got {tmp_config.get('startup.minimize_to_tray')}"
        )
