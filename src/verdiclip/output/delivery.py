"""Deliver finished images to the clipboard, a file, or a printer."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QClipboard,
    QGuiApplication,
    QImage,
    QImageWriter,
    QPageLayout,
    QPainter,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import QWidget

from verdiclip.exceptions import DeliveryError
from verdiclip.output.naming import FilenamePattern
from verdiclip.settings import ImageFormat, OutputSettings

logger = logging.getLogger(__name__)


class ImageDelivery:
    """Copy, save, and print images according to output settings."""

    def __init__(self, settings: OutputSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> OutputSettings:
        """Return the output settings in use."""
        return self._settings

    # Clipboard

    def copy(self, image: QImage) -> None:
        """Put ``image`` on the system clipboard.

        Raises:
            DeliveryError: If no clipboard is available.
        """
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            msg = "The system clipboard is not available"
            raise DeliveryError(msg)
        clipboard.setImage(image, QClipboard.Mode.Clipboard)
        logger.info("Copied %dx%d image to clipboard", image.width(), image.height())

    # Files

    def auto_path(self, *, title: str = "", moment: datetime | None = None) -> Path:
        """Return the next free path from the filename pattern."""
        when = moment if moment is not None else datetime.now().astimezone()
        pattern = FilenamePattern(self._settings.filename_pattern)
        return pattern.resolve(
            self._settings.directory,
            self._settings.image_format,
            moment=when,
            title=title,
        )

    def save(self, image: QImage, path: Path) -> Path:
        """Write ``image`` to ``path``; the format follows the file extension.

        Raises:
            DeliveryError: If the format is unsupported or the write fails.
        """
        image_format = self.format_for(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            msg = f"Could not create folder {path.parent}: {err.strerror}"
            raise DeliveryError(msg) from err
        writer = QImageWriter(str(path), image_format.value.encode())
        if image_format is ImageFormat.JPG:
            writer.setQuality(self._settings.jpeg_quality)
        to_write = image
        if image_format in (ImageFormat.JPG, ImageFormat.BMP):
            to_write = self._flatten_alpha(image)
        if not writer.write(to_write):
            msg = f"Could not save {path.name}: {writer.errorString()}"
            raise DeliveryError(msg)
        logger.info("Saved image to %s", path)
        return path

    @staticmethod
    def format_for(path: Path) -> ImageFormat:
        """Return the image format implied by ``path``'s extension.

        Raises:
            DeliveryError: If the extension is not a supported format.
        """
        ext = path.suffix.lower().lstrip(".")
        aliases = {"jpeg": "jpg", "tif": "tiff"}
        try:
            return ImageFormat(aliases.get(ext, ext))
        except ValueError as err:
            supported = ", ".join(f".{f.value}" for f in ImageFormat)
            msg = f"Unsupported file type {path.suffix!r}; use one of {supported}"
            raise DeliveryError(msg) from err

    @staticmethod
    def _flatten_alpha(image: QImage) -> QImage:
        """Composite ``image`` onto white for formats without transparency."""
        result = QImage(image.size(), QImage.Format.Format_RGB32)
        result.fill(Qt.GlobalColor.white)
        painter = QPainter(result)
        painter.drawImage(QPointF(0, 0), image)
        painter.end()
        return result

    # Printing

    def print_image(self, image: QImage, parent: QWidget | None = None) -> bool:
        """Show the print dialog and print ``image``; return True if printed."""
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        landscape = image.width() > image.height()
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
            if landscape
            else QPageLayout.Orientation.Portrait
        )
        dialog = QPrintDialog(printer, parent)
        dialog.setWindowTitle("Print image")
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return False
        self.render_to_printer(image, printer)
        return True

    @staticmethod
    def render_to_printer(image: QImage, printer: QPrinter) -> None:
        """Paint ``image`` centered and scaled to fit the printable page."""
        painter = QPainter()
        if not painter.begin(printer):
            msg = "Could not start printing"
            raise DeliveryError(msg)
        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            size = (
                image.size()
                .toSizeF()
                .scaled(page.size(), Qt.AspectRatioMode.KeepAspectRatio)
            )
            x = (page.width() - size.width()) / 2
            y = (page.height() - size.height()) / 2
            painter.drawImage(QRectF(QPointF(x, y), size), image)
        finally:
            painter.end()
