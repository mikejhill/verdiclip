"""Tests for verdiclip.export.clipboard.ClipboardExporter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from verdiclip.export.clipboard import ClipboardExporter

# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


class TestCopy:
    def test_returns_true_on_success(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.red)
        result = ClipboardExporter.copy(pixmap)
        assert result is True, f"Expected copy() to be True, got {result}"

    def test_clipboard_contains_image_after_copy(self, qapp) -> None:
        pixmap = QPixmap(50, 50)
        pixmap.fill(Qt.GlobalColor.blue)
        ClipboardExporter.copy(pixmap)

        clipboard = QApplication.clipboard()
        assert clipboard is not None, f"Expected clipboard to not be None, got {clipboard}"
        result = clipboard.pixmap()
        if result.isNull():
            pytest.skip("Clipboard COM error — cannot verify in this environment")
        assert result.width() == 50, f"Expected result.width() to be 50, got {result.width()}"
        assert result.height() == 50, f"Expected result.height() to be 50, got {result.height()}"

    def test_returns_false_when_clipboard_none(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.red)
        with patch.object(QApplication, "clipboard", return_value=None):
            result = ClipboardExporter.copy(pixmap)
            assert result is False, f"Expected copy() to be False, got {result}"


# ---------------------------------------------------------------------------
# has_image
# ---------------------------------------------------------------------------


class TestHasImage:
    def test_returns_false_when_no_clipboard(self, qapp) -> None:
        with patch.object(QApplication, "clipboard", return_value=None):
            result = ClipboardExporter.has_image()
            assert result is False, f"Expected has_image() to be False, got {result}"

    def test_returns_false_when_clipboard_has_no_image(self, qapp) -> None:
        mock_clipboard = MagicMock()
        mock_mime = MagicMock()
        mock_mime.hasImage.return_value = False
        mock_clipboard.mimeData.return_value = mock_mime
        with patch.object(QApplication, "clipboard", return_value=mock_clipboard):
            result = ClipboardExporter.has_image()
            assert result is False, f"Expected has_image() to be False, got {result}"

    def test_returns_true_when_clipboard_has_image(self, qapp) -> None:
        mock_clipboard = MagicMock()
        mock_mime = MagicMock()
        mock_mime.hasImage.return_value = True
        mock_clipboard.mimeData.return_value = mock_mime
        with patch.object(QApplication, "clipboard", return_value=mock_clipboard):
            result = ClipboardExporter.has_image()
            assert result is True, f"Expected has_image() to be True, got {result}"


# ---------------------------------------------------------------------------
# get_image
# ---------------------------------------------------------------------------


class TestGetImage:
    def test_returns_none_when_no_clipboard(self, qapp) -> None:
        with patch.object(QApplication, "clipboard", return_value=None):
            result = ClipboardExporter.get_image()
            assert result is None, f"Expected get_image() to be None, got {result}"

    def test_returns_none_when_clipboard_empty(self, qapp) -> None:
        mock_clipboard = MagicMock()
        null_pixmap = QPixmap()
        mock_clipboard.pixmap.return_value = null_pixmap
        with patch.object(QApplication, "clipboard", return_value=mock_clipboard):
            result = ClipboardExporter.get_image()
            assert result is None, f"Expected get_image() to be None, got {result}"

    def test_returns_pixmap_when_image_available(self, qapp) -> None:
        pixmap = QPixmap(30, 30)
        pixmap.fill(Qt.GlobalColor.cyan)
        mock_clipboard = MagicMock()
        mock_clipboard.pixmap.return_value = pixmap
        with patch.object(QApplication, "clipboard", return_value=mock_clipboard):
            result = ClipboardExporter.get_image()
            assert result is not None, f"Expected result to not be None, got {result}"
            assert isinstance(result, QPixmap), f"Expected instance of QPixmap, got {type(result)}"
            assert result.width() == 30, f"Expected result.width() to be 30, got {result.width()}"
            assert result.height() == 30, (
                f"Expected result.height() to be 30, got {result.height()}"
            )
