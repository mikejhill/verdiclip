"""Settings dialog; every control maps to a setting the app honors."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Final

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from verdiclip.editor.style_bar import ColorButton
from verdiclip.exceptions import HotkeyError
from verdiclip.output.naming import FilenamePattern
from verdiclip.platform.hotkeys import Hotkey
from verdiclip.settings import (
    AppearanceSettings,
    CaptureSettings,
    HotkeySettings,
    ImageFormat,
    IntegrationSettings,
    OutputSettings,
    Settings,
    StartupSettings,
    Theme,
)

HOTKEY_FIELDS: Final = (
    ("region", "Region or window"),
    ("window", "Active window"),
    ("fullscreen", "Full screen"),
    ("repeat", "Repeat last capture"),
)
THEME_LABELS: Final = {
    Theme.SYSTEM: "Match Windows",
    Theme.LIGHT: "Light",
    Theme.DARK: "Dark",
}
ERROR_STYLE: Final = "color: #c62828;"


class SettingsDialog(QDialog):
    """Edit ``Settings``; ``result_settings`` holds the accepted values."""

    make_default_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VerdiClip settings")
        self._initial = settings
        self._open_editor = QCheckBox("Open in the editor")
        self._copy = QCheckBox("Copy to the clipboard")
        self._save = QCheckBox("Save to the output folder")
        self._after_status = QLabel()
        self._theme = QComboBox()
        self._magnifier = QCheckBox("Show a magnifier")
        self._hotkeys: dict[str, QLineEdit] = {}
        self._hotkey_status: dict[str, QLabel] = {}
        self._folder = QLineEdit()
        self._pattern = QLineEdit()
        self._pattern_preview = QLabel()
        self._format = QComboBox()
        self._quality = QSpinBox()
        self._stroke = ColorButton(allow_none=False)
        self._width = QDoubleSpinBox()
        self._font = QFontComboBox()
        self._font_size = QSpinBox()
        self._confirm_close = QCheckBox(
            "Ask before closing an image that hasn't been saved or copied"
        )
        self._run_at_login = QCheckBox("Start VerdiClip when I sign in to Windows")
        self._open_with = QCheckBox("Show VerdiClip in “Open with” for image files")
        self._make_default = QPushButton("Make VerdiClip the default image app…")
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._build()
        self._load(settings)
        self._validate()

    def result_settings(self) -> Settings:
        """Return the settings as currently entered."""
        return Settings(
            capture=CaptureSettings(
                open_editor=self._open_editor.isChecked(),
                copy_to_clipboard=self._copy.isChecked(),
                save_to_file=self._save.isChecked(),
                show_magnifier=self._magnifier.isChecked(),
            ),
            hotkeys=HotkeySettings(
                **{
                    name: self._canonical_hotkey(edit.text())
                    for name, edit in self._hotkeys.items()
                }
            ),
            output=OutputSettings(
                directory=Path(
                    self._folder.text().strip() or str(self._initial.output.directory)
                ),
                filename_pattern=self._pattern.text(),
                image_format=ImageFormat(self._format.currentData()),
                jpeg_quality=self._quality.value(),
            ),
            editor=replace(
                self._initial.editor,
                stroke_color=self._stroke.color,
                stroke_width=self._width.value(),
                font_family=self._font.currentFont().family(),
                font_size=self._font_size.value(),
                confirm_unsaved_close=self._confirm_close.isChecked(),
            ),
            appearance=AppearanceSettings(theme=Theme(self._theme.currentData())),
            startup=StartupSettings(run_at_login=self._run_at_login.isChecked()),
            integration=IntegrationSettings(open_with=self._open_with.isChecked()),
        )

    def hotkey_edit(self, name: str) -> QLineEdit:
        """Return the editor for hotkey ``name`` (for tests)."""
        return self._hotkeys[name]

    def make_default_button(self) -> QPushButton:
        """Return the "make default" button (for tests)."""
        return self._make_default

    def ok_enabled(self) -> bool:
        """True when every field is valid."""
        return self._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    # Building

    def _build(self) -> None:
        """Lay out the tabs."""
        tabs = QTabWidget()
        tabs.addTab(self._capture_tab(), "Capture")
        tabs.addTab(self._hotkey_tab(), "Hotkeys")
        tabs.addTab(self._output_tab(), "Output")
        tabs.addTab(self._editor_tab(), "Editor")
        tabs.addTab(self._general_tab(), "General")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(self._buttons)

    def _capture_tab(self) -> QWidget:
        """Return the Capture tab."""
        actions = QVBoxLayout()
        for box in (self._open_editor, self._copy, self._save):
            box.toggled.connect(self._validate)
            actions.addWidget(box)
        actions.addWidget(self._after_status)
        tab = QWidget()
        form = QFormLayout(tab)
        form.addRow("After capturing:", actions)
        form.addRow("While selecting:", self._magnifier)
        return tab

    def _general_tab(self) -> QWidget:
        """Return the General tab."""
        for value, label in THEME_LABELS.items():
            self._theme.addItem(label, value.value)
        tab = QWidget()
        form = QFormLayout(tab)
        form.addRow("Theme:", self._theme)
        form.addRow(self._run_at_login)
        self._make_default.setToolTip(
            "Adds VerdiClip to Open with, then opens Windows Settings, "
            "where you confirm it as the default."
        )
        self._make_default.clicked.connect(self._request_default)
        files = QVBoxLayout()
        files.addWidget(self._open_with)
        files.addWidget(self._make_default)
        form.addRow("Image files:", files)
        return tab

    def _hotkey_tab(self) -> QWidget:
        """Return the Hotkeys tab with live validation."""
        tab = QWidget()
        form = QFormLayout(tab)
        form.addRow(
            QLabel(
                "Type a combination such as Ctrl+Shift+PrtSc. Leave empty to disable."
            )
        )
        for name, label in HOTKEY_FIELDS:
            edit = QLineEdit()
            status = QLabel()
            edit.textChanged.connect(self._validate)
            self._hotkeys[name] = edit
            self._hotkey_status[name] = status
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            row.addWidget(status)
            form.addRow(f"{label}:", row)
        return tab

    def _output_tab(self) -> QWidget:
        """Return the Output tab."""
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self._folder, 1)
        folder_row.addWidget(browse)
        for fmt in ImageFormat:
            self._format.addItem(fmt.value.upper(), fmt.value)
        self._quality.setRange(1, 100)
        self._quality.setSuffix(" %")
        self._pattern.textChanged.connect(self._validate)
        self._format.currentIndexChanged.connect(self._validate)
        tab = QWidget()
        form = QFormLayout(tab)
        form.addRow("Folder:", folder_row)
        form.addRow("File name:", self._pattern)
        form.addRow("", self._pattern_preview)
        form.addRow("Format:", self._format)
        form.addRow("JPEG quality:", self._quality)
        return tab

    def _editor_tab(self) -> QWidget:
        """Return the Editor tab."""
        self._width.setRange(1, 60)
        self._width.setDecimals(0)
        self._font_size.setRange(6, 200)
        tab = QWidget()
        form = QFormLayout(tab)
        form.addRow("Color:", self._stroke)
        form.addRow("Line width:", self._width)
        form.addRow("Font:", self._font)
        form.addRow("Font size:", self._font_size)
        form.addRow("Closing:", self._confirm_close)
        return tab

    # Behavior

    def _load(self, settings: Settings) -> None:
        """Fill the controls from ``settings``."""
        self._open_editor.setChecked(settings.capture.open_editor)
        self._copy.setChecked(settings.capture.copy_to_clipboard)
        self._save.setChecked(settings.capture.save_to_file)
        self._theme.setCurrentIndex(
            self._theme.findData(settings.appearance.theme.value)
        )
        self._magnifier.setChecked(settings.capture.show_magnifier)
        for name, edit in self._hotkeys.items():
            text = getattr(settings.hotkeys, name)
            edit.setText(self._display_hotkey(text))
        self._folder.setText(str(settings.output.directory))
        self._pattern.setText(settings.output.filename_pattern)
        self._format.setCurrentIndex(
            self._format.findData(settings.output.image_format.value)
        )
        self._quality.setValue(settings.output.jpeg_quality)
        self._stroke.set_color(settings.editor.stroke_color)
        self._width.setValue(settings.editor.stroke_width)
        self._font.setCurrentFont(QFont(settings.editor.font_family))
        self._font_size.setValue(settings.editor.font_size)
        self._confirm_close.setChecked(settings.editor.confirm_unsaved_close)
        self._run_at_login.setChecked(settings.startup.run_at_login)
        self._open_with.setChecked(settings.integration.open_with)

    def _request_default(self) -> None:
        """Turn on Open with and ask the app to open the default-apps page."""
        self._open_with.setChecked(True)
        self.make_default_requested.emit()

    def _validate(self) -> None:
        """Show per-field problems and enable OK only when all fields are valid."""
        valid = True
        seen: dict[str, str] = {}
        for name, edit in self._hotkeys.items():
            status = self._hotkey_status[name]
            try:
                canonical = self._canonical_hotkey(edit.text())
            except HotkeyError as err:
                status.setText(str(err))
                status.setStyleSheet(ERROR_STYLE)
                valid = False
                continue
            if canonical and canonical in seen:
                status.setText(f"Also used by {seen[canonical]}")
                status.setStyleSheet(ERROR_STYLE)
                valid = False
                continue
            seen[canonical] = dict(HOTKEY_FIELDS)[name]
            status.setText("✓" if canonical else "Off")
            status.setStyleSheet("")
        valid = self._validate_pattern() and valid
        valid = self._validate_actions() and valid
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid)

    def _validate_actions(self) -> bool:
        """Require at least one after-capture action."""
        chosen = any(b.isChecked() for b in (self._open_editor, self._copy, self._save))
        self._after_status.setText("" if chosen else "Choose at least one action.")
        self._after_status.setStyleSheet(ERROR_STYLE)
        return chosen

    def _validate_pattern(self) -> bool:
        """Preview the filename pattern; return False if it is invalid."""
        try:
            pattern = FilenamePattern(self._pattern.text())
        except ValueError as err:
            self._pattern_preview.setText(str(err))
            self._pattern_preview.setStyleSheet(ERROR_STYLE)
            return False
        fmt = ImageFormat(self._format.currentData() or ImageFormat.PNG.value)
        example = pattern.resolve(
            Path(), fmt, moment=datetime.now().astimezone(), title="Notepad"
        )
        self._pattern_preview.setText(f"Example: {example.name}")
        self._pattern_preview.setStyleSheet("color: palette(mid);")
        return True

    def _browse(self) -> None:
        """Choose the output folder."""
        chosen = QFileDialog.getExistingDirectory(
            self, "Output folder", self._folder.text()
        )
        if chosen:
            self._folder.setText(chosen)

    @staticmethod
    def _canonical_hotkey(text: str) -> str:
        """Return the canonical form of ``text`` ("" when empty)."""
        return str(Hotkey.parse(text)) if text.strip() else ""

    @staticmethod
    def _display_hotkey(text: str) -> str:
        """Return a friendly form of a stored hotkey."""
        try:
            return Hotkey.parse(text).display_text if text.strip() else ""
        except HotkeyError:
            return text
