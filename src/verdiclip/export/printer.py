"""Printer export for printing screenshots."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrintPreviewDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class PrinterExporter:
    """Handles printing images."""

    @staticmethod
    def print_pixmap(pixmap: QPixmap, parent: QWidget | None = None) -> bool:
        """Show a print dialog and print the image.

        Returns:
            True if printing was initiated.
        """
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(
            _best_orientation(pixmap.width(), pixmap.height())
        )

        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print Screenshot")

        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            logger.info("Print cancelled.")
            return False

        _render_to_printer(pixmap, printer)
        logger.info("Image sent to printer.")
        return True

    @staticmethod
    def print_preview(pixmap: QPixmap, parent: QWidget | None = None) -> None:
        """Show a print preview dialog."""
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(
            _best_orientation(pixmap.width(), pixmap.height())
        )

        def render(p: QPrinter) -> None:
            _render_to_printer(pixmap, p)

        preview = QPrintPreviewDialog(printer, parent)
        preview.paintRequested.connect(render)
        preview.exec()


def _best_orientation(width: int, height: int):
    """Return the best page orientation for the given image dimensions."""
    from PySide6.QtGui import QPageLayout
    if width > height:
        return QPageLayout.Orientation.Landscape
    return QPageLayout.Orientation.Portrait


def _render_to_printer(pixmap: QPixmap, printer: QPrinter) -> None:
    """Render a pixmap to a printer, scaled to fit the page."""
    painter = QPainter()
    if not painter.begin(printer):
        logger.error("Failed to begin painting to printer.")
        return

    page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
    scaled = pixmap.scaled(
        int(page_rect.width()),
        int(page_rect.height()),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    x = int((page_rect.width() - scaled.width()) / 2)
    y = int((page_rect.height() - scaled.height()) / 2)
    painter.drawPixmap(x, y, scaled)
    painter.end()
