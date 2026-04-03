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
        assert btn.color.name() == "#00ff00", (
            f"Expected initial color '#00ff00', got '{btn.color.name()}'"
        )

    def test_fixed_size_28x28(self, qapp) -> None:
        btn = ColorButton(QColor("#000000"))
        assert btn.width() == 28, f"Expected button width 28, got {btn.width()}"
        assert btn.height() == 28, f"Expected button height 28, got {btn.height()}"


class TestColorButtonProperty:
    def test_getter_returns_current_color(self, qapp) -> None:
        color = QColor("#ABCDEF")
        btn = ColorButton(color)
        assert btn.color.name() == color.name(), (
            f"Expected color '{color.name()}', got '{btn.color.name()}'"
        )

    def test_setter_updates_color(self, qapp) -> None:
        btn = ColorButton(QColor("#000000"))
        new_color = QColor("#FF00FF")
        btn.color = new_color
        assert btn.color.name() == new_color.name(), (
            f"Expected color '{new_color.name()}' after set, got '{btn.color.name()}'"
        )


# ---------------------------------------------------------------------------
# PropertiesPanel
# ---------------------------------------------------------------------------


class TestPropertiesPanelDefaults:
    def test_stroke_color_default(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.stroke_color.name() == QColor("#FF0000").name(), (
            f"Expected default stroke color '#ff0000', got '{panel.stroke_color.name()}'"
        )

    def test_fill_color_default_is_transparent(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.fill_color.alpha() == 0, (
            f"Expected default fill color alpha 0, got {panel.fill_color.alpha()}"
        )

    def test_stroke_width_default(self, qapp) -> None:
        panel = PropertiesPanel()
        assert panel.stroke_width == 3, f"Expected default stroke width 3, got {panel.stroke_width}"

    def test_current_font_default(self, qapp) -> None:
        panel = PropertiesPanel()
        font = panel.current_font
        assert font.family() == "Arial", (
            f"Expected default font family 'Arial', got '{font.family()}'"
        )
        assert font.pointSize() == 14, f"Expected default font size 14, got {font.pointSize()}"


class TestPropertiesPanelSetStrokeColor:
    def test_updates_stroke_color(self, qapp) -> None:
        panel = PropertiesPanel()
        new_color = QColor("#00FF00")
        panel.set_stroke_color(new_color)
        assert panel.stroke_color.name() == new_color.name(), (
            f"Expected stroke color '{new_color.name()}', got '{panel.stroke_color.name()}'"
        )


class TestPropertiesPanelSetFillColor:
    def test_updates_fill_color(self, qapp) -> None:
        panel = PropertiesPanel()
        new_color = QColor("#0000FF")
        panel.set_fill_color(new_color)
        assert panel.fill_color.name() == new_color.name(), (
            f"Expected fill color '{new_color.name()}', got '{panel.fill_color.name()}'"
        )


class TestPropertiesPanelSetStrokeWidth:
    def test_updates_width(self, qapp) -> None:
        panel = PropertiesPanel()
        panel.set_stroke_width(10)
        assert panel.stroke_width == 10, f"Expected stroke width 10, got {panel.stroke_width}"


class TestPropertiesPanelStrokeWidthSignal:
    def test_emits_on_slider_change(self, qapp) -> None:
        """Direct slider interaction emits stroke_width_changed."""
        panel = PropertiesPanel()
        handler = MagicMock()
        panel.stroke_width_changed.connect(handler)
        # Simulate user slider interaction — setValue on the slider widget
        # emits valueChanged, which is wired to stroke_width_changed.
        panel._width_slider.setValue(7)
        handler.assert_called_once_with(7)

    def test_set_stroke_width_does_not_emit(self, qapp) -> None:
        """set_stroke_width is silent — it updates the display without emitting."""
        panel = PropertiesPanel()
        handler = MagicMock()
        panel.stroke_width_changed.connect(handler)
        panel.set_stroke_width(12)
        assert panel.stroke_width == 12
        handler.assert_not_called()


class TestPropertiesPanelStrokeColorSignal:
    def test_signal_connection_exists(self, qapp) -> None:
        panel = PropertiesPanel()
        handler = MagicMock()
        panel.stroke_color_changed.connect(handler)
        # Verify the signal is connectable (the actual color dialog
        # cannot be triggered without user interaction, so we verify
        # the internal wiring by emitting from the color button directly).
        panel._stroke_btn.color_changed.emit(QColor("#123456"))
        assert handler.call_count == 1, (
            f"Expected signal to fire once, fired {handler.call_count} times"
        )
        actual_color = handler.call_args[0][0]
        assert actual_color.name() == QColor("#123456").name(), (
            f"Expected stroke color '#123456', got '{actual_color.name()}'"
        )
