"""Tests for the Settings dialog."""

from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QLabel, QTabWidget

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
    def test_capture_default_action_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.default_action", "clipboard")
        dialog = SettingsDialog(tmp_config)
        assert dialog._capture_action.currentIndex() == 2, (
            f"Expected dialog._capture_action.currentIndex() to equal 2,"
            f" got {dialog._capture_action.currentIndex()}"
        )

    def test_capture_include_cursor_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.include_cursor", True)
        dialog = SettingsDialog(tmp_config)
        assert dialog._include_cursor.isChecked() is True, (
            f"Expected dialog._include_cursor.isChecked() to be True,"
            f" got {dialog._include_cursor.isChecked()}"
        )

    def test_capture_region_magnifier_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.region_magnifier", False)
        dialog = SettingsDialog(tmp_config)
        assert dialog._region_magnifier.isChecked() is False, (
            f"Expected dialog._region_magnifier.isChecked() to be False,"
            f" got {dialog._region_magnifier.isChecked()}"
        )

    def test_capture_window_decorations_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("capture.window_decorations", False)
        dialog = SettingsDialog(tmp_config)
        assert dialog._window_decorations.isChecked() is False, (
            f"Expected dialog._window_decorations.isChecked() to be False,"
            f" got {dialog._window_decorations.isChecked()}"
        )

    def test_save_directory_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.default_directory", r"C:\Screenshots")
        dialog = SettingsDialog(tmp_config)
        assert dialog._save_dir.text() == r"C:\Screenshots", (
            f"Expected dialog._save_dir.text() to equal 'C:\\Screenshots',"
            f" got {dialog._save_dir.text()}"
        )

    def test_save_format_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.default_format", "jpg")
        dialog = SettingsDialog(tmp_config)
        assert dialog._save_format.currentText() == "JPG", (
            f"Expected dialog._save_format.currentText() to equal 'JPG',"
            f" got {dialog._save_format.currentText()}"
        )

    def test_save_jpg_quality_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.jpg_quality", 75)
        dialog = SettingsDialog(tmp_config)
        assert dialog._jpg_quality.value() == 75, (
            f"Expected dialog._jpg_quality.value() to equal 75, got {dialog._jpg_quality.value()}"
        )

    def test_save_auto_save_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.auto_save_enabled", True)
        dialog = SettingsDialog(tmp_config)
        assert dialog._auto_save.isChecked() is True, (
            f"Expected dialog._auto_save.isChecked() to be True,"
            f" got {dialog._auto_save.isChecked()}"
        )

    def test_save_filename_pattern_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("save.filename_pattern", "Img_{date}")
        dialog = SettingsDialog(tmp_config)
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

    def test_hotkey_region_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.region", "ctrl+r")
        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_region.text() == "ctrl+r", (
            f"Expected dialog._hotkey_region.text() to equal 'ctrl+r',"
            f" got {dialog._hotkey_region.text()}"
        )

    def test_hotkey_fullscreen_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.fullscreen", "ctrl+f")
        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_fullscreen.text() == "ctrl+f", (
            f"Expected dialog._hotkey_fullscreen.text() to equal 'ctrl+f',"
            f" got {dialog._hotkey_fullscreen.text()}"
        )

    def test_hotkey_window_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.window", "ctrl+w")
        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_window.text() == "ctrl+w", (
            f"Expected dialog._hotkey_window.text() to equal 'ctrl+w',"
            f" got {dialog._hotkey_window.text()}"
        )

    def test_hotkey_window_pick_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.window_pick", "ctrl+p")
        dialog = SettingsDialog(tmp_config)
        assert dialog._hotkey_window_pick.text() == "ctrl+p", (
            f"Expected dialog._hotkey_window_pick.text() to equal 'ctrl+p',"
            f" got {dialog._hotkey_window_pick.text()}"
        )

    def test_hotkey_repeat_loaded(self, qapp, tmp_config) -> None:
        tmp_config.set("hotkeys.repeat", "ctrl+l")
        dialog = SettingsDialog(tmp_config)
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
    def test_capture_default_action_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._capture_action.setCurrentIndex(1)  # file
        dialog._save_and_close()
        assert tmp_config.get("capture.default_action") == "file", (
            f"Expected tmp_config.get('capture.default_action') to equal 'file',"
            f" got {tmp_config.get('capture.default_action')}"
        )

    def test_capture_include_cursor_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._include_cursor.setChecked(True)
        dialog._save_and_close()
        assert tmp_config.get("capture.include_cursor") is True, (
            f"Expected tmp_config.get('capture.include_cursor') to be True,"
            f" got {tmp_config.get('capture.include_cursor')}"
        )

    def test_capture_region_magnifier_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._region_magnifier.setChecked(False)
        dialog._save_and_close()
        assert tmp_config.get("capture.region_magnifier") is False, (
            f"Expected tmp_config.get('capture.region_magnifier') to be False,"
            f" got {tmp_config.get('capture.region_magnifier')}"
        )

    def test_capture_window_decorations_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._window_decorations.setChecked(False)
        dialog._save_and_close()
        assert tmp_config.get("capture.window_decorations") is False, (
            f"Expected tmp_config.get('capture.window_decorations') to be False,"
            f" got {tmp_config.get('capture.window_decorations')}"
        )

    def test_save_directory_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._save_dir.setText(r"D:\Captures")
        dialog._save_and_close()
        assert tmp_config.get("save.default_directory") == r"D:\Captures", (
            f"Expected tmp_config.get('save.default_directory') to equal 'D:\\Captures',"
            f" got {tmp_config.get('save.default_directory')}"
        )

    def test_save_format_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._save_format.setCurrentText("JPG")
        dialog._save_and_close()
        assert tmp_config.get("save.default_format") == "jpg", (
            f"Expected tmp_config.get('save.default_format') to equal 'jpg',"
            f" got {tmp_config.get('save.default_format')}"
        )

    def test_save_jpg_quality_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._jpg_quality.setValue(60)
        dialog._save_and_close()
        assert tmp_config.get("save.jpg_quality") == 60, (
            f"Expected tmp_config.get('save.jpg_quality') to equal 60,"
            f" got {tmp_config.get('save.jpg_quality')}"
        )

    def test_save_auto_save_enabled_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._auto_save.setChecked(True)
        dialog._save_and_close()
        assert tmp_config.get("save.auto_save_enabled") is True, (
            f"Expected tmp_config.get('save.auto_save_enabled') to be True,"
            f" got {tmp_config.get('save.auto_save_enabled')}"
        )

    def test_save_filename_pattern_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._filename_pattern.setText("Cap_{counter}")
        dialog._save_and_close()
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

    def test_hotkey_region_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_region.setText("alt+1")
        dialog._save_and_close()
        assert tmp_config.get("hotkeys.region") == "alt+1", (
            f"Expected tmp_config.get('hotkeys.region') to equal 'alt+1',"
            f" got {tmp_config.get('hotkeys.region')}"
        )

    def test_hotkey_fullscreen_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_fullscreen.setText("alt+2")
        dialog._save_and_close()
        assert tmp_config.get("hotkeys.fullscreen") == "alt+2", (
            f"Expected tmp_config.get('hotkeys.fullscreen') to equal 'alt+2',"
            f" got {tmp_config.get('hotkeys.fullscreen')}"
        )

    def test_hotkey_window_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_window.setText("alt+3")
        dialog._save_and_close()
        assert tmp_config.get("hotkeys.window") == "alt+3", (
            f"Expected tmp_config.get('hotkeys.window') to equal 'alt+3',"
            f" got {tmp_config.get('hotkeys.window')}"
        )

    def test_hotkey_window_pick_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_window_pick.setText("alt+4")
        dialog._save_and_close()
        assert tmp_config.get("hotkeys.window_pick") == "alt+4", (
            f"Expected tmp_config.get('hotkeys.window_pick') to equal 'alt+4',"
            f" got {tmp_config.get('hotkeys.window_pick')}"
        )

    def test_hotkey_repeat_saved(self, qapp, tmp_config) -> None:
        dialog = SettingsDialog(tmp_config)
        dialog._hotkey_repeat.setText("alt+5")
        dialog._save_and_close()
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


class TestJpgQualityVisibility:
    """Tests that JPG quality row visibility tracks the selected format."""

    def test_jpg_quality_hidden_when_format_is_png(self, qapp, tmp_config) -> None:
        """JPG quality row is hidden when the format is PNG."""
        tmp_config.set("save.default_format", "png")
        dialog = SettingsDialog(tmp_config)
        assert dialog._jpg_quality_row_label.isHidden(), (
            f"Expected JPG quality row label to be hidden for PNG,"
            f" got isHidden={dialog._jpg_quality_row_label.isHidden()}"
        )
        assert dialog._jpg_quality_row_widget.isHidden(), (
            f"Expected JPG quality row widget to be hidden for PNG,"
            f" got isHidden={dialog._jpg_quality_row_widget.isHidden()}"
        )

    def test_jpg_quality_visible_when_format_is_jpg(self, qapp, tmp_config) -> None:
        """JPG quality row is not hidden when the format is JPG."""
        tmp_config.set("save.default_format", "jpg")
        dialog = SettingsDialog(tmp_config)
        assert not dialog._jpg_quality_row_label.isHidden(), (
            f"Expected JPG quality row label to NOT be hidden for JPG,"
            f" got isHidden={dialog._jpg_quality_row_label.isHidden()}"
        )
        assert not dialog._jpg_quality_row_widget.isHidden(), (
            f"Expected JPG quality row widget to NOT be hidden for JPG,"
            f" got isHidden={dialog._jpg_quality_row_widget.isHidden()}"
        )

    def test_jpg_quality_hidden_for_bmp(self, qapp, tmp_config) -> None:
        """JPG quality row is hidden when the format is BMP."""
        tmp_config.set("save.default_format", "bmp")
        dialog = SettingsDialog(tmp_config)
        assert dialog._jpg_quality_row_label.isHidden(), (
            f"Expected JPG quality row label to be hidden for BMP,"
            f" got isHidden={dialog._jpg_quality_row_label.isHidden()}"
        )


class TestJpgQualityLabel:
    """Tests that the JPG quality slider value label updates dynamically."""

    def test_quality_label_exists(self, qapp, tmp_config) -> None:
        """JPG quality slider has an associated value label."""
        dialog = SettingsDialog(tmp_config)
        assert hasattr(dialog, "_jpg_quality_label"), (
            "Expected dialog to have a _jpg_quality_label attribute"
        )
        assert isinstance(dialog._jpg_quality_label, QLabel), (
            f"Expected _jpg_quality_label to be QLabel,"
            f" got {type(dialog._jpg_quality_label)}"
        )

    def test_quality_label_matches_initial_value(self, qapp, tmp_config) -> None:
        """Value label shows the slider's initial value."""
        tmp_config.set("save.default_format", "jpg")
        tmp_config.set("save.jpg_quality", 75)
        dialog = SettingsDialog(tmp_config)
        assert dialog._jpg_quality_label.text() == "75", (
            f"Expected _jpg_quality_label text to equal '75',"
            f" got '{dialog._jpg_quality_label.text()}'"
        )

    def test_quality_label_updates_on_slider_change(self, qapp, tmp_config) -> None:
        """Value label updates when the slider value changes."""
        dialog = SettingsDialog(tmp_config)
        dialog._jpg_quality.setValue(42)
        assert dialog._jpg_quality_label.text() == "42", (
            f"Expected _jpg_quality_label text to equal '42' after slider change,"
            f" got '{dialog._jpg_quality_label.text()}'"
        )


class TestSettingsSavedSignal:
    """Tests that settings_saved signal is emitted on save."""

    def test_settings_saved_signal_emitted_on_save(self, qapp, tmp_config) -> None:
        """_save_and_close emits the settings_saved signal."""
        dialog = SettingsDialog(tmp_config)
        emitted = []
        dialog.settings_saved.connect(lambda: emitted.append(True))
        dialog._save_and_close()
        assert len(emitted) == 1, (
            f"Expected settings_saved signal to be emitted exactly once,"
            f" got {len(emitted)} emissions"
        )

    def test_settings_saved_signal_emitted_after_config_written(self, qapp, tmp_config) -> None:
        """Config values are persisted before the signal fires."""
        dialog = SettingsDialog(tmp_config)
        dialog._stroke_width.setValue(15)

        config_at_signal_time = []

        def on_saved() -> None:
            config_at_signal_time.append(tmp_config.get("editor.default_stroke_width"))

        dialog.settings_saved.connect(on_saved)
        dialog._save_and_close()
        assert len(config_at_signal_time) == 1, (
            f"Expected signal handler to be called once, got {len(config_at_signal_time)}"
        )
        assert config_at_signal_time[0] == 15, (
            f"Expected config value at signal time to be 15, got {config_at_signal_time[0]}"
        )


class TestHotkeyExampleGrammar:
    """Tests that the hotkeys tab contains the correct example text."""

    def test_hotkeys_tab_contains_example_prefix(self, qapp, tmp_config) -> None:
        """Hotkeys tab should say 'Example:' not 'Use format like:'."""
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)[0]
        hotkeys_tab = None
        for i in range(tabs.count()):
            if tabs.tabText(i) == "Hotkeys":
                hotkeys_tab = tabs.widget(i)
                break
        assert hotkeys_tab is not None, (
            "Expected to find a tab named 'Hotkeys' in the settings dialog"
        )

        labels = hotkeys_tab.findChildren(QLabel)
        label_texts = [lbl.text() for lbl in labels]
        has_example = any("Example:" in t for t in label_texts)
        assert has_example, (
            f"Expected at least one QLabel containing 'Example:' in Hotkeys tab,"
            f" found labels: {label_texts}"
        )

    def test_hotkeys_tab_does_not_contain_old_phrasing(self, qapp, tmp_config) -> None:
        """Hotkeys tab must NOT contain the old 'Use format like:' phrasing."""
        dialog = SettingsDialog(tmp_config)
        tabs = dialog.findChildren(QTabWidget)[0]
        hotkeys_tab = None
        for i in range(tabs.count()):
            if tabs.tabText(i) == "Hotkeys":
                hotkeys_tab = tabs.widget(i)
                break
        assert hotkeys_tab is not None, (
            "Expected to find a tab named 'Hotkeys' in the settings dialog"
        )

        labels = hotkeys_tab.findChildren(QLabel)
        label_texts = [lbl.text() for lbl in labels]
        has_old_phrasing = any("Use format like:" in t for t in label_texts)
        assert not has_old_phrasing, (
            f"Expected no QLabel containing 'Use format like:' in Hotkeys tab,"
            f" but found labels: {label_texts}"
        )
