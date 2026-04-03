"""Full-screen screenshot capture using mss."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import mss
import mss.screenshot
import mss.tools
from PIL import Image
from PySide6.QtGui import QImage, QPixmap

if TYPE_CHECKING:
    from PySide6.QtCore import QRect

logger = logging.getLogger(__name__)


def _mss_to_pixmap(sct_img: mss.screenshot.ScreenShot) -> QPixmap:
    """Convert an mss screenshot to a QPixmap.

    The resulting pixmap always has ``devicePixelRatio == 1`` because mss
    captures at physical resolution regardless of display scaling.
    """
    img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    qimage = QImage(
        img.tobytes("raw", "RGB"),
        img.width,
        img.height,
        3 * img.width,
        QImage.Format.Format_RGB888,
    )
    pixmap = QPixmap.fromImage(qimage)
    pixmap.setDevicePixelRatio(1)
    return pixmap


class ScreenCapture:
    """Captures full-screen screenshots using mss for performance."""

    @staticmethod
    def capture_all_monitors() -> QPixmap:
        """Capture the entire virtual screen (all monitors stitched)."""
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # Virtual screen (all monitors)
            raw = sct.grab(monitor)
            pixmap = _mss_to_pixmap(raw)
            logger.info("Captured all monitors: %dx%d", pixmap.width(), pixmap.height())
            return pixmap

    @staticmethod
    def capture_primary_monitor() -> QPixmap:
        """Capture only the primary monitor."""
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # Primary monitor
            raw = sct.grab(monitor)
            pixmap = _mss_to_pixmap(raw)
            logger.info("Captured primary monitor: %dx%d", pixmap.width(), pixmap.height())
            return pixmap

    @staticmethod
    def capture_monitor(index: int) -> QPixmap:
        """Capture a specific monitor by index (1-based)."""
        with mss.mss() as sct:
            if index < 1 or index >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {index} out of range. Available: 1-{len(sct.monitors) - 1}"
                )
            monitor = sct.monitors[index]
            raw = sct.grab(monitor)
            pixmap = _mss_to_pixmap(raw)
            logger.info("Captured monitor %d: %dx%d", index, pixmap.width(), pixmap.height())
            return pixmap

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
            pixmap = _mss_to_pixmap(raw)
            logger.info(
                "Captured region (%d,%d %dx%d)",
                rect.x(),
                rect.y(),
                rect.width(),
                rect.height(),
            )
            return pixmap

    @staticmethod
    def get_monitor_count() -> int:
        """Return the number of physical monitors."""
        with mss.mss() as sct:
            return len(sct.monitors) - 1  # Exclude virtual screen at index 0
