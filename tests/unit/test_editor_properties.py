"""Tests for verdiclip.editor.properties — ColorButton and PropertiesPanel."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtGui import QColor

from verdiclip.editor.properties import ColorButton, PropertiesPanel

# ---------------------------------------------------------------------------
# ColorButton
# ---------------------------------------------------------------------------


class TestColorButtonInit:
    def test_initial_color_is_set(self, qapp) -> None:
        btn = ColorButton(QColor("#00FF00"))
        assert btn.color.name() == "#00ff00"

    def test_fixed_size_28x28(self, qapp) -> None:
        btn = ColorButton(QColor("#000000"))
        assert btn.width() == 28
        assert btn.height() == 28


class TestColorButtonProperty:
    def test_getter_returns_current_color(self, qapp) -> None:
        color = QColor("#ABCDEF")
        btn = ColorButton(color)
        assert btn.color.name() == color.name()

    def test_setter_updates_color(self, qapp) -> None:
        btn = ColorButton(QColor("#000000"))
        new_color = QColor("#FF00FF")
        btn.color = new_color
        assert btn.color.name() == new_color.name()


# ---------------------------------------------------------------------------
# PropertiesPanel
# ---------------------------------------------------------------------------


class TestPropertiesPanelDefaults:
    def test_stroke_color_default(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.stroke_color.name() == QColor("#FF0000").name()

    def test_fill_color_default_is_transparent(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.fill_color.alpha() == 0

    def test_stroke_width_default(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.stroke_width == 3

    def test_current_font_default(self, qapp) -> None:
        panel = PropertiesPanel()
        font = panel.current_font
        assert font.family() == "Arial"
        assert font.pointSize() == 14

    def test_obfuscation_strength_default(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.obfuscation_strength == 12


class TestPropertiesPanelSetStrokeColor:
    def test_updates_stroke_color(self, qapp) -> None:
        panel = PropertiesPanel()
        new_color = QColor("#00FF00")
        panel.set_stroke_color(new_color)
        assert panel.stroke_color.name() == new_color.name()


class TestPropertiesPanelSetFillColor:
    def test_updates_fill_color(self, qapp) -> None:
        panel = PropertiesPanel()
        new_color = QColor("#0000FF")
        panel.set_fill_color(new_color)
        assert panel.fill_color.name() == new_color.name()


class TestPropertiesPanelSetStrokeWidth:
    def test_updates_width(self, qapp) -> None:
        panel = PropertiesPanel()
        panel.set_stroke_width(10)
        assert panel.stroke_width == 10


class TestPropertiesPanelStrokeWidthSignal:
    def test_emits_on_slider_change(self, qapp) -> None:
        panel = PropertiesPanel()
        handler = MagicMock()
        panel.stroke_width_changed.connect(handler)
        panel.set_stroke_width(7)
        handler.assert_called_once_with(7)


class TestPropertiesPanelStrokeColorSignal:
    def test_signal_connection_exists(self, qapp) -> None:
        panel = PropertiesPanel()
        handler = MagicMock()
        panel.stroke_color_changed.connect(handler)
        # Verify the signal is connectable (the actual color dialog
        # cannot be triggered without user interaction, so we verify
        # the internal wiring by emitting from the color button directly).
        panel._stroke_btn.color_changed.emit(QColor("#123456"))
        handler.assert_called_once()
