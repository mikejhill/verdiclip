"""Tests for the About dialog."""

from __future__ import annotations

from PySide6.QtWidgets import QDialogButtonBox, QLabel

from verdiclip import __app_name__, __version__
from verdiclip.ui.about_dialog import AboutDialog


class TestAboutDialog:
    def test_creates_without_parent(self, qapp) -> None:
        dialog = AboutDialog()
        assert dialog is not None

    def test_window_title_contains_about_and_app_name(self, qapp) -> None:
        dialog = AboutDialog()
        title = dialog.windowTitle()
        assert "About" in title
        assert __app_name__ in title

    def test_fixed_size(self, qapp) -> None:
        dialog = AboutDialog()
        assert dialog.width() == 420
        assert dialog.height() == 320

    def test_contains_app_name_label(self, qapp) -> None:
        dialog = AboutDialog()
        labels = dialog.findChildren(QLabel)
        texts = [label.text() for label in labels]
        assert any("VerdiClip" in t for t in texts)

    def test_contains_version_label(self, qapp) -> None:
        dialog = AboutDialog()
        labels = dialog.findChildren(QLabel)
        texts = [label.text() for label in labels]
        assert any(__version__ in t for t in texts)

    def test_contains_greenshot_attribution(self, qapp) -> None:
        dialog = AboutDialog()
        labels = dialog.findChildren(QLabel)
        texts = [label.text() for label in labels]
        assert any("Greenshot" in t for t in texts)

    def test_contains_mit_license(self, qapp) -> None:
        dialog = AboutDialog()
        labels = dialog.findChildren(QLabel)
        texts = [label.text() for label in labels]
        assert any("MIT License" in t for t in texts)

    def test_has_close_button(self, qapp) -> None:
        dialog = AboutDialog()
        button_boxes = dialog.findChildren(QDialogButtonBox)
        assert len(button_boxes) == 1
        buttons = button_boxes[0].buttons()
        assert len(buttons) == 1
        assert buttons[0].text().replace("&", "") == "Close"
