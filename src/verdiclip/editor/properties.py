"""Tool properties panel for the editor toolbar."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QWidget,
)

logger = logging.getLogger(__name__)


class ColorButton(QPushButton):
    """Button that shows a color and opens a color picker on click."""

    color_changed = Signal(QColor)

    def __init__(self, initial_color: QColor, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = initial_color
        self.setFixedSize(28, 28)
        self._update_style()
        self.clicked.connect(self._pick_color)

    def _update_style(self) -> None:
        self.setStyleSheet(
            f"background-color: {self._color.name(QColor.NameFormat.HexArgb)}; "
            f"border: 1px solid #666; border-radius: 3px;"
        )

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(
            self._color, self.parentWidget(), "Choose Color",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if color.isValid():
            self._color = color
            self._update_style()
            self.color_changed.emit(color)

    @property
    def color(self) -> QColor:
        return self._color

    @color.setter
    def color(self, value: QColor) -> None:
        self._color = value
        self._update_style()


class PropertiesPanel(QWidget):
    """Panel for editing tool properties (stroke, fill, font, etc.)."""

    stroke_color_changed = Signal(QColor)
    fill_color_changed = Signal(QColor)
    stroke_width_changed = Signal(int)
    font_changed = Signal(QFont)
    obfuscation_strength_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # Stroke color
        layout.addWidget(QLabel("Stroke:"))
        self._stroke_btn = ColorButton(QColor("#FF0000"))
        self._stroke_btn.color_changed.connect(self.stroke_color_changed.emit)
        layout.addWidget(self._stroke_btn)

        # Fill color
        layout.addWidget(QLabel("Fill:"))
        self._fill_btn = ColorButton(QColor(0, 0, 0, 0))
        self._fill_btn.color_changed.connect(self.fill_color_changed.emit)
        layout.addWidget(self._fill_btn)

        # Stroke width
        layout.addWidget(QLabel("Width:"))
        self._width_slider = QSlider(Qt.Orientation.Horizontal)
        self._width_slider.setRange(1, 20)
        self._width_slider.setValue(3)
        self._width_slider.setFixedWidth(100)
        self._width_slider.valueChanged.connect(self.stroke_width_changed.emit)
        layout.addWidget(self._width_slider)

        self._width_label = QLabel("3")
        self._width_label.setFixedWidth(20)
        self._width_slider.valueChanged.connect(lambda v: self._width_label.setText(str(v)))
        layout.addWidget(self._width_label)

        # Font family
        layout.addWidget(QLabel("Font:"))
        self._font_combo = QComboBox()
        self._font_combo.addItems(["Arial", "Segoe UI", "Consolas", "Times New Roman", "Calibri"])
        self._font_combo.setFixedWidth(120)
        self._font_combo.currentTextChanged.connect(self._on_font_changed)
        layout.addWidget(self._font_combo)

        # Font size
        self._font_size = QSpinBox()
        self._font_size.setRange(8, 72)
        self._font_size.setValue(14)
        self._font_size.valueChanged.connect(self._on_font_changed)
        layout.addWidget(self._font_size)

        # Obfuscation strength
        layout.addWidget(QLabel("Obfuscation:"))
        self._obfuscation_slider = QSlider(Qt.Orientation.Horizontal)
        self._obfuscation_slider.setRange(4, 32)
        self._obfuscation_slider.setValue(12)
        self._obfuscation_slider.setFixedWidth(80)
        self._obfuscation_slider.valueChanged.connect(self.obfuscation_strength_changed.emit)
        layout.addWidget(self._obfuscation_slider)

        layout.addStretch()

    def _on_font_changed(self) -> None:
        font = QFont(self._font_combo.currentText(), self._font_size.value())
        self.font_changed.emit(font)

    @property
    def stroke_color(self) -> QColor:
        return self._stroke_btn.color

    @property
    def fill_color(self) -> QColor:
        return self._fill_btn.color

    @property
    def stroke_width(self) -> int:
        return self._width_slider.value()

    @property
    def current_font(self) -> QFont:
        return QFont(self._font_combo.currentText(), self._font_size.value())

    @property
    def obfuscation_strength(self) -> int:
        return self._obfuscation_slider.value()

    def set_stroke_color(self, color: QColor) -> None:
        self._stroke_btn.color = color

    def set_fill_color(self, color: QColor) -> None:
        self._fill_btn.color = color

    def set_stroke_width(self, width: int) -> None:
        self._width_slider.setValue(width)
