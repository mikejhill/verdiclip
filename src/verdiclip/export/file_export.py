"""File export for saving screenshots to disk."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog, QWidget

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap

    from verdiclip.config import Config

logger = logging.getLogger(__name__)

_FORMAT_FILTERS = {
    "png": "PNG Image (*.png)",
    "jpg": "JPEG Image (*.jpg *.jpeg)",
    "bmp": "Bitmap Image (*.bmp)",
    "gif": "GIF Image (*.gif)",
    "tiff": "TIFF Image (*.tiff *.tif)",
}

_ALL_FILTER = "All Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.tif)"


class FileExporter:
    """Handles saving images to file."""

    @staticmethod
    def save_with_dialog(
        pixmap: QPixmap, config: Config, parent: QWidget | None = None
    ) -> str | None:
        """Save the image, using auto-save settings or showing a dialog."""
        if config.get("save.auto_save_enabled", False):
            path = FileExporter.auto_save(pixmap, config)
            if path:
                return path

        return FileExporter.save_as(pixmap, parent, config)

    @staticmethod
    def save_as(
        pixmap: QPixmap,
        parent: QWidget | None = None,
        config: Config | None = None,
    ) -> str | None:
        """Show a Save As dialog and save the image."""
        default_dir = ""
        default_fmt = "png"
        if config:
            default_dir = config.get("save.default_directory", "")
            default_fmt = config.get("save.default_format", "png")

        filter_list = [_ALL_FILTER, *list(_FORMAT_FILTERS.values())]
        selected_filter = _FORMAT_FILTERS.get(default_fmt, _FORMAT_FILTERS["png"])

        file_path, _chosen_filter = QFileDialog.getSaveFileName(
            parent,
            "Save Screenshot",
            default_dir,
            ";;".join(filter_list),
            selected_filter,
        )
        if not file_path:
            return None

        # Ensure file extension
        path = Path(file_path)
        if not path.suffix:
            ext = default_fmt if default_fmt else "png"
            path = path.with_suffix(f".{ext}")

        quality = -1
        fmt_str = path.suffix.lstrip(".").upper()
        if fmt_str in ("JPG", "JPEG"):
            quality = config.get("save.jpg_quality", 90) if config else 90

        success = pixmap.save(str(path), fmt_str if fmt_str != "JPEG" else "JPG", quality)
        if success:
            logger.info("Image saved to %s", path)
            return str(path)
        logger.error("Failed to save image to %s", path)
        return None

    @staticmethod
    def auto_save(pixmap: QPixmap, config: Config) -> str | None:
        """Auto-save the image based on configuration."""
        default_dir = str(Path.home() / "Pictures" / "VerdiClip")
        save_dir = Path(config.get("save.default_directory", default_dir))
        save_dir.mkdir(parents=True, exist_ok=True)

        pattern = config.get("save.filename_pattern", "Screenshot_{datetime}")
        fmt = config.get("save.default_format", "png")

        now = datetime.now()
        filename = pattern.format(
            datetime=now.strftime("%Y%m%d_%H%M%S"),
            date=now.strftime("%Y%m%d"),
            time=now.strftime("%H%M%S"),
            counter=FileExporter._get_next_counter(save_dir, fmt),
            title="Screenshot",
        )

        file_path = save_dir / f"{filename}.{fmt}"

        quality = -1
        if fmt in ("jpg", "jpeg"):
            quality = config.get("save.jpg_quality", 90)

        success = pixmap.save(str(file_path), fmt.upper(), quality)
        if success:
            logger.info("Auto-saved to %s", file_path)
            return str(file_path)
        logger.error("Auto-save failed: %s", file_path)
        return None

    @staticmethod
    def _get_next_counter(directory: Path, extension: str) -> int:
        """Get the next counter value based on existing files in the directory."""
        existing = list(directory.glob(f"*.{extension}"))
        return len(existing) + 1
