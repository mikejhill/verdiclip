"""Tests for verdiclip.export.clipboard.ClipboardExporter."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from verdiclip.export.clipboard import ClipboardExporter


class TestCopyToClipboard:
    def test_copy_to_clipboard(self, qapp) -> None:
        pixmap = QPixmap(100, 100)
        pixmap.fill(Qt.GlobalColor.red)

        result = ClipboardExporter.copy(pixmap)
        assert result is True
        assert ClipboardExporter.has_image() is True
