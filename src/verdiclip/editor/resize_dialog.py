"""Dialog for choosing a new image size in pixels or percent."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Final

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from verdiclip.document.transform import MAX_DIMENSION

MAX_PERCENT: Final = 1000


class ResizeDialog(QDialog):
    """Pick a width and height; aspect ratio is kept unless unticked (UX-IMG-03)."""

    def __init__(self, width: int, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resize image")
        self._original = (width, height)
        self._width = self._spin_box(" px", MAX_DIMENSION, width)
        self._height = self._spin_box(" px", MAX_DIMENSION, height)
        self._percent = self._spin_box(" %", MAX_PERCENT, 100)
        self._keep_aspect = QCheckBox("Keep aspect ratio")
        self._keep_aspect.setChecked(True)
        self._syncing = False
        self._build()

    # Accessors (also used by tests)

    @property
    def width_box(self) -> QSpinBox:
        """Return the width field."""
        return self._width

    @property
    def height_box(self) -> QSpinBox:
        """Return the height field."""
        return self._height

    @property
    def percent_box(self) -> QSpinBox:
        """Return the percent field."""
        return self._percent

    @property
    def keep_aspect_box(self) -> QCheckBox:
        """Return the keep-aspect checkbox."""
        return self._keep_aspect

    def result_size(self) -> tuple[int, int]:
        """Return the chosen width and height."""
        return self._width.value(), self._height.value()

    # Building

    def _build(self) -> None:
        """Lay out the fields and connect them."""
        width, height = self._original
        form = QFormLayout()
        current = f"Current size: {width} × {height} px"  # noqa: RUF001
        form.addRow(QLabel(current))
        form.addRow("Width:", self._width)
        form.addRow("Height:", self._height)
        form.addRow("Scale:", self._percent)
        form.addRow(self._keep_aspect)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._width.valueChanged.connect(self._on_width)
        self._height.valueChanged.connect(self._on_height)
        self._percent.valueChanged.connect(self._on_percent)
        self._keep_aspect.toggled.connect(self._on_keep_aspect)
        self._width.setFocus()
        self._width.selectAll()

    @staticmethod
    def _spin_box(suffix: str, maximum: int, value: int) -> QSpinBox:
        """Return a spin box with ``suffix`` from 1 to ``maximum``."""
        box = QSpinBox()
        box.setRange(1, maximum)
        box.setSuffix(suffix)
        box.setValue(min(value, maximum))
        box.setAccelerated(True)
        return box

    # Linking

    def _on_width(self, width: int) -> None:
        """Follow a new width with the height (if linked) and percent."""
        if self._syncing:
            return
        original_w, original_h = self._original
        with self._sync():
            if self._keep_aspect.isChecked():
                self._height.setValue(max(1, round(width * original_h / original_w)))
            self._percent.setValue(max(1, round(width * 100 / original_w)))

    def _on_height(self, height: int) -> None:
        """Follow a new height with the width (if linked) and percent."""
        if self._syncing:
            return
        original_w, original_h = self._original
        with self._sync():
            if self._keep_aspect.isChecked():
                self._width.setValue(max(1, round(height * original_w / original_h)))
            self._percent.setValue(max(1, round(height * 100 / original_h)))

    def _on_percent(self, percent: int) -> None:
        """Scale both sides from the original size."""
        if self._syncing:
            return
        original_w, original_h = self._original
        with self._sync():
            self._width.setValue(max(1, round(original_w * percent / 100)))
            self._height.setValue(max(1, round(original_h * percent / 100)))

    def _on_keep_aspect(self, keep: bool) -> None:  # noqa: FBT001 — Qt signal argument
        """Re-link the height to the width when the box is ticked again."""
        if keep:
            self._on_width(self._width.value())

    @contextmanager
    def _sync(self) -> Generator[None]:
        """Suppress feedback while the dialog updates its own fields."""
        self._syncing = True
        try:
            yield
        finally:
            self._syncing = False
