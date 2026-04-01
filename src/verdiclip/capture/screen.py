"""Full-screen screenshot capture using mss."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import mss
import mss.tools
from PIL import Image
from PySide6.QtGui import QImage, QPixmap

if TYPE_CHECKING:
    from PySide6.QtCore import QRect

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Captures full-screen screenshots using mss for performance."""

    @staticmethod
    def capture_all_monitors() -> QPixmap:
        """Capture the entire virtual screen (all monitors stitched)."""
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # Virtual screen (all monitors)
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            qimage = QImage(
                img.tobytes("raw", "RGB"),
                img.width,
                img.height,
                3 * img.width,
                QImage.Format.Format_RGB888,
            )
            logger.info("Captured all monitors: %dx%d", img.width, img.height)
            return QPixmap.fromImage(qimage)

    @staticmethod
    def capture_primary_monitor() -> QPixmap:
        """Capture only the primary monitor."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            qimage = QImage(
                img.tobytes("raw", "RGB"),
                img.width,
                img.height,
                3 * img.width,
                QImage.Format.Format_RGB888,
            )
            logger.info("Captured primary monitor: %dx%d", img.width, img.height)
            return QPixmap.fromImage(qimage)

    @staticmethod
    def capture_monitor(index: int) -> QPixmap:
        """Capture a specific monitor by index (1-based)."""
        with mss.mss() as sct:
            if index < 1 or index >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {index} out of range. "
                    f"Available: 1-{len(sct.monitors) - 1}"
                )
            monitor = sct.monitors[index]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            qimage = QImage(
                img.tobytes("raw", "RGB"),
                img.width,
                img.height,
                3 * img.width,
                QImage.Format.Format_RGB888,
            )
            logger.info("Captured monitor %d: %dx%d", index, img.width, img.height)
            return QPixmap.fromImage(qimage)

    @staticmethod
    def capture_region(rect: QRect) -> QPixmap:
        """Capture a specific rectangular region of the screen."""
        region = {
            "left": rect.x(),
            "top": rect.y(),
            "width": rect.width(),
            "height": rect.height(),
        }
        with mss.mss() as sct:
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            qimage = QImage(
                img.tobytes("raw", "RGB"),
                img.width,
                img.height,
                3 * img.width,
                QImage.Format.Format_RGB888,
            )
            logger.info(
                "Captured region (%d,%d %dx%d)",
                rect.x(), rect.y(), rect.width(), rect.height(),
            )
            return QPixmap.fromImage(qimage)

    @staticmethod
    def get_monitor_count() -> int:
        """Return the number of physical monitors."""
        with mss.mss() as sct:
            return len(sct.monitors) - 1  # Exclude virtual screen at index 0
