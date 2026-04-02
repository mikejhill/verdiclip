"""Tool properties panel for the editor toolbar."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QFontComboBox,
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
    """Panel for editing tool properties (stroke, fill, font, line caps, etc.)."""

    stroke_color_changed = Signal(QColor)
    fill_color_changed = Signal(QColor)
    stroke_width_changed = Signal(int)
    font_changed = Signal(QFont)
    start_cap_changed = Signal(str)
    end_cap_changed = Signal(str)

    _CAP_OPTIONS = [("None", "none"), ("Round", "round"), ("Arrow", "arrow")]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        # --- Stroke color ---
        self._stroke_label = QLabel("Stroke:")
        layout.addWidget(self._stroke_label)
        self._stroke_btn = ColorButton(QColor("#FF0000"))
        self._stroke_btn.color_changed.connect(self.stroke_color_changed.emit)
        layout.addWidget(self._stroke_btn)

        # --- Fill color ---
        self._fill_label = QLabel("Fill:")
        layout.addWidget(self._fill_label)
        self._fill_btn = ColorButton(QColor(0, 0, 0, 0))
        self._fill_btn.color_changed.connect(self._on_fill_color_picked)
        layout.addWidget(self._fill_btn)
        self._no_fill_btn = QPushButton("∅")
        self._no_fill_btn.setToolTip("No fill (transparent)")
        self._no_fill_btn.setFixedSize(28, 28)
        self._no_fill_btn.setCheckable(True)
        self._no_fill_btn.setChecked(True)
        self._no_fill_btn.clicked.connect(self._on_no_fill_clicked)
        layout.addWidget(self._no_fill_btn)

        # --- Stroke width ---
        self._width_label = QLabel("Width:")
        layout.addWidget(self._width_label)
        self._width_slider = QSlider(Qt.Orientation.Horizontal)
        self._width_slider.setRange(1, 20)
        self._width_slider.setValue(3)
        self._width_slider.setFixedWidth(100)
        self._width_slider.valueChanged.connect(self.stroke_width_changed.emit)
        layout.addWidget(self._width_slider)
        self._width_value_label = QLabel("3")
        self._width_value_label.setFixedWidth(20)
        self._width_slider.valueChanged.connect(
            lambda v: self._width_value_label.setText(str(v))
        )
        layout.addWidget(self._width_value_label)

        # --- Font family (each entry rendered in its own face) ---
        self._font_label = QLabel("Font:")
        layout.addWidget(self._font_label)
        self._font_combo = QFontComboBox()
        self._font_combo.setCurrentFont(QFont("Arial"))
        self._font_combo.setFixedWidth(140)
        self._font_combo.currentFontChanged.connect(self._on_font_changed)
        layout.addWidget(self._font_combo)

        # --- Font size ---
        self._font_size = QSpinBox()
        self._font_size.setRange(8, 72)
        self._font_size.setValue(14)
        self._font_size.valueChanged.connect(self._on_font_changed)
        layout.addWidget(self._font_size)

        # --- Line caps (hidden by default, shown for Line/Arrow tools) ---
        self._start_cap_label = QLabel("Start:")
        layout.addWidget(self._start_cap_label)
        self._start_cap_combo = QComboBox()
        for label, _val in self._CAP_OPTIONS:
            self._start_cap_combo.addItem(label)
        self._start_cap_combo.setFixedWidth(70)
        self._start_cap_combo.currentIndexChanged.connect(
            lambda i: self.start_cap_changed.emit(self._CAP_OPTIONS[i][1])
        )
        layout.addWidget(self._start_cap_combo)

        self._end_cap_label = QLabel("End:")
        layout.addWidget(self._end_cap_label)
        self._end_cap_combo = QComboBox()
        for label, _val in self._CAP_OPTIONS:
            self._end_cap_combo.addItem(label)
        self._end_cap_combo.setCurrentIndex(2)  # Arrow by default for Line tool
        self._end_cap_combo.setFixedWidth(70)
        self._end_cap_combo.currentIndexChanged.connect(
            lambda i: self.end_cap_changed.emit(self._CAP_OPTIONS[i][1])
        )
        layout.addWidget(self._end_cap_combo)

        # Hide caps by default
        self._set_caps_visible(False)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Internal signal handlers
    # ------------------------------------------------------------------

    def _on_fill_color_picked(self, color: QColor) -> None:
        """When user picks a fill color, uncheck the 'no fill' button."""
        self._no_fill_btn.blockSignals(True)
        self._no_fill_btn.setChecked(False)
        self._no_fill_btn.blockSignals(False)
        self.fill_color_changed.emit(color)

    def _on_no_fill_clicked(self, checked: bool) -> None:
        """Emit transparent fill when the 'no fill' button is toggled."""
        if checked:
            transparent = QColor(0, 0, 0, 0)
            self._fill_btn.color = transparent
            self.fill_color_changed.emit(transparent)

    def _on_font_changed(self) -> None:
        font = self._font_combo.currentFont()
        font.setPointSize(self._font_size.value())
        self.font_changed.emit(font)

    def _set_caps_visible(self, visible: bool) -> None:
        for w in (self._start_cap_label, self._start_cap_combo,
                  self._end_cap_label, self._end_cap_combo):
            w.setVisible(visible)

    # ------------------------------------------------------------------
    # Visibility control
    # ------------------------------------------------------------------

    def set_visible_properties(
        self,
        stroke: bool = True,
        fill: bool = True,
        width: bool = True,
        font: bool = False,
        caps: bool = False,
    ) -> None:
        """Show or hide property groups based on the active tool or selected item."""
        for w in (self._stroke_label, self._stroke_btn):
            w.setVisible(stroke)
        for w in (self._fill_label, self._fill_btn, self._no_fill_btn):
            w.setVisible(fill)
        for w in (self._width_label, self._width_slider, self._width_value_label):
            w.setVisible(width)
        for w in (self._font_label, self._font_combo, self._font_size):
            w.setVisible(font)
        self._set_caps_visible(caps)

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def stroke_color(self) -> QColor:
        return self._stroke_btn.color

    @property
    def fill_color(self) -> QColor:
        if self._no_fill_btn.isChecked():
            return QColor(0, 0, 0, 0)
        return self._fill_btn.color

    @property
    def stroke_width(self) -> int:
        return self._width_slider.value()

    @property
    def current_font(self) -> QFont:
        font = self._font_combo.currentFont()
        font.setPointSize(self._font_size.value())
        return font

    @property
    def start_cap(self) -> str:
        return self._CAP_OPTIONS[self._start_cap_combo.currentIndex()][1]

    @property
    def end_cap(self) -> str:
        return self._CAP_OPTIONS[self._end_cap_combo.currentIndex()][1]

    # ------------------------------------------------------------------
    # Setters for context-sensitive updates (do not re-emit signals)
    # ------------------------------------------------------------------

    def set_stroke_color(self, color: QColor) -> None:
        """Update the stroke color display without emitting ``stroke_color_changed``."""
        self._stroke_btn.color = color

    def set_fill_color(self, color: QColor) -> None:
        """Update the fill color display without emitting ``fill_color_changed``."""
        self._fill_btn.color = color
        self._no_fill_btn.blockSignals(True)
        self._no_fill_btn.setChecked(color.alpha() == 0)
        self._no_fill_btn.blockSignals(False)

    def set_stroke_width(self, width: int) -> None:
        """Update the stroke width display without emitting ``stroke_width_changed``."""
        self._width_slider.blockSignals(True)
        self._width_slider.setValue(max(1, min(20, width)))
        self._width_slider.blockSignals(False)
        self._width_value_label.setText(str(self._width_slider.value()))

    def set_font(self, font: QFont) -> None:
        """Update the font controls without emitting ``font_changed``."""
        self._font_combo.blockSignals(True)
        self._font_combo.setCurrentFont(font)
        self._font_combo.blockSignals(False)
        self._font_size.blockSignals(True)
        self._font_size.setValue(max(8, min(72, font.pointSize())))
        self._font_size.blockSignals(False)

