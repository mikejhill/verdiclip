"""Settings dialog for VerdiClip preferences."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from verdiclip.config import Config

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Tabbed settings dialog for VerdiClip configuration."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("VerdiClip Settings")
        self.setMinimumSize(500, 400)
        self._setup_ui()
        self._load_values()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        tabs = QTabWidget()
        tabs.addTab(self._create_capture_tab(), "Capture")
        tabs.addTab(self._create_save_tab(), "Save")
        tabs.addTab(self._create_editor_tab(), "Editor")
        tabs.addTab(self._create_hotkeys_tab(), "Hotkeys")
        tabs.addTab(self._create_general_tab(), "General")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_capture_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._capture_action = QComboBox()
        self._capture_action.addItems([
            "Open in Editor", "Save to File", "Copy to Clipboard", "Show Menu",
        ])
        form.addRow("After capture:", self._capture_action)

        self._include_cursor = QCheckBox("Include mouse cursor in captures")
        form.addRow(self._include_cursor)

        self._region_magnifier = QCheckBox("Show magnifier during region selection")
        form.addRow(self._region_magnifier)

        self._window_decorations = QCheckBox("Include window decorations")
        form.addRow(self._window_decorations)

        return widget

    def _create_save_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        dir_layout = QHBoxLayout()
        self._save_dir = QLineEdit()
        dir_layout.addWidget(self._save_dir)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_save_dir)
        dir_layout.addWidget(browse_btn)
        form.addRow("Save directory:", dir_layout)

        self._save_format = QComboBox()
        self._save_format.addItems(["PNG", "JPG", "BMP", "GIF", "TIFF"])
        form.addRow("Default format:", self._save_format)

        self._jpg_quality = QSlider(Qt.Orientation.Horizontal)
        self._jpg_quality.setRange(1, 100)
        self._jpg_quality.setValue(90)
        form.addRow("JPG quality:", self._jpg_quality)

        self._auto_save = QCheckBox("Auto-save screenshots")
        form.addRow(self._auto_save)

        self._filename_pattern = QLineEdit()
        self._filename_pattern.setPlaceholderText("Screenshot_{datetime}")
        form.addRow("Filename pattern:", self._filename_pattern)

        form.addRow(QLabel("Tokens: {datetime}, {date}, {time}, {counter}, {title}"))

        return widget

    def _create_editor_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._stroke_width = QSpinBox()
        self._stroke_width.setRange(1, 20)
        form.addRow("Default stroke width:", self._stroke_width)

        self._font_family = QComboBox()
        self._font_family.addItems(["Arial", "Segoe UI", "Consolas", "Times New Roman", "Calibri"])
        form.addRow("Default font:", self._font_family)

        self._font_size = QSpinBox()
        self._font_size.setRange(8, 72)
        form.addRow("Default font size:", self._font_size)

        return widget

    def _create_hotkeys_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._hotkey_region = QLineEdit()
        form.addRow("Region capture:", self._hotkey_region)

        self._hotkey_fullscreen = QLineEdit()
        form.addRow("Full screen:", self._hotkey_fullscreen)

        self._hotkey_window = QLineEdit()
        form.addRow("Active window:", self._hotkey_window)

        self._hotkey_window_pick = QLineEdit()
        form.addRow("Window picker:", self._hotkey_window_pick)

        self._hotkey_repeat = QLineEdit()
        form.addRow("Repeat last:", self._hotkey_repeat)

        form.addRow(QLabel("Use format like: ctrl+shift+print_screen"))

        return widget

    def _create_general_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self._run_at_login = QCheckBox("Start VerdiClip at Windows login")
        form.addRow(self._run_at_login)

        self._minimize_to_tray = QCheckBox("Minimize to system tray on close")
        form.addRow(self._minimize_to_tray)

        return widget

    def _load_values(self) -> None:
        """Load current config values into the dialog widgets."""
        action_map = {"editor": 0, "file": 1, "clipboard": 2, "menu": 3}
        action = self._config.get("capture.default_action", "editor")
        self._capture_action.setCurrentIndex(action_map.get(action, 0))

        self._include_cursor.setChecked(self._config.get("capture.include_cursor", False))
        self._region_magnifier.setChecked(self._config.get("capture.region_magnifier", True))
        self._window_decorations.setChecked(self._config.get("capture.window_decorations", True))

        self._save_dir.setText(self._config.get("save.default_directory", ""))
        fmt = self._config.get("save.default_format", "png").upper()
        idx = self._save_format.findText(fmt)
        if idx >= 0:
            self._save_format.setCurrentIndex(idx)
        self._jpg_quality.setValue(self._config.get("save.jpg_quality", 90))
        self._auto_save.setChecked(self._config.get("save.auto_save_enabled", False))
        pattern = self._config.get("save.filename_pattern", "Screenshot_{datetime}")
        self._filename_pattern.setText(pattern)

        self._stroke_width.setValue(self._config.get("editor.default_stroke_width", 3))
        font_family = self._config.get("editor.default_font_family", "Arial")
        idx = self._font_family.findText(font_family)
        if idx >= 0:
            self._font_family.setCurrentIndex(idx)
        self._font_size.setValue(self._config.get("editor.default_font_size", 14))

        self._hotkey_region.setText(self._config.get("hotkeys.region", ""))
        self._hotkey_fullscreen.setText(self._config.get("hotkeys.fullscreen", ""))
        self._hotkey_window.setText(self._config.get("hotkeys.window", ""))
        self._hotkey_window_pick.setText(self._config.get("hotkeys.window_pick", ""))
        self._hotkey_repeat.setText(self._config.get("hotkeys.repeat", ""))

        self._run_at_login.setChecked(self._config.get("startup.run_at_login", False))
        self._minimize_to_tray.setChecked(self._config.get("startup.minimize_to_tray", True))

    def _save_and_close(self) -> None:
        """Save all settings and close the dialog."""
        action_map = {0: "editor", 1: "file", 2: "clipboard", 3: "menu"}
        action = action_map.get(self._capture_action.currentIndex(), "editor")
        self._config.set("capture.default_action", action)
        self._config.set("capture.include_cursor", self._include_cursor.isChecked())
        self._config.set("capture.region_magnifier", self._region_magnifier.isChecked())
        self._config.set("capture.window_decorations", self._window_decorations.isChecked())

        self._config.set("save.default_directory", self._save_dir.text())
        self._config.set("save.default_format", self._save_format.currentText().lower())
        self._config.set("save.jpg_quality", self._jpg_quality.value())
        self._config.set("save.auto_save_enabled", self._auto_save.isChecked())
        self._config.set("save.filename_pattern", self._filename_pattern.text())

        self._config.set("editor.default_stroke_width", self._stroke_width.value())
        self._config.set("editor.default_font_family", self._font_family.currentText())
        self._config.set("editor.default_font_size", self._font_size.value())

        self._config.set("hotkeys.region", self._hotkey_region.text())
        self._config.set("hotkeys.fullscreen", self._hotkey_fullscreen.text())
        self._config.set("hotkeys.window", self._hotkey_window.text())
        self._config.set("hotkeys.window_pick", self._hotkey_window_pick.text())
        self._config.set("hotkeys.repeat", self._hotkey_repeat.text())

        self._config.set("startup.run_at_login", self._run_at_login.isChecked())
        self._config.set("startup.minimize_to_tray", self._minimize_to_tray.isChecked())

        logger.info("Settings saved.")
        self.accept()

    def _browse_save_dir(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(self, "Choose Save Directory")
        if dir_path:
            self._save_dir.setText(dir_path)
