"""Clipboard export for copying screenshots."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class ClipboardExporter:
    """Handles copying images to the system clipboard."""

    @staticmethod
    def copy(pixmap: QPixmap) -> bool:
        """Copy a QPixmap to the system clipboard.

        Returns:
            True if the copy succeeded.
        """
        clipboard = QApplication.clipboard()
        if clipboard is None:
            logger.error("Could not access system clipboard.")
            return False

        clipboard.setPixmap(pixmap)
        logger.info("Image copied to clipboard (%dx%d)", pixmap.width(), pixmap.height())
        return True

    @staticmethod
    def has_image() -> bool:
        """Check if the clipboard currently contains an image."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return False
        mime = clipboard.mimeData()
        return mime is not None and mime.hasImage()

    @staticmethod
    def get_image() -> QPixmap | None:
        """Get an image from the clipboard, if available."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return None
        pixmap = clipboard.pixmap()
        if pixmap.isNull():
            return None
        return pixmap
