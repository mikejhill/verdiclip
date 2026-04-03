"""Repeat last capture functionality."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from verdiclip.capture.screen import ScreenCapture
from verdiclip.capture.window import WindowCapture

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class CaptureType(Enum):
    """Types of screenshot capture."""

    REGION = auto()
    FULLSCREEN = auto()
    ACTIVE_WINDOW = auto()
    WINDOW_PICK = auto()


@dataclass
class LastCapture:
    """Stores information about the last capture for repeat functionality."""

    capture_type: CaptureType
    region: QRect | None = None


class RepeatCapture:
    """Manages repeating the last capture action."""

    def __init__(self) -> None:
        self._last: LastCapture | None = None

    def record(self, capture_type: CaptureType, region: QRect | None = None) -> None:
        """Record the last capture type and optional region."""
        self._last = LastCapture(capture_type=capture_type, region=region)
        logger.debug("Recorded last capture: %s", capture_type.name)

    def can_repeat(self) -> bool:
        """Return True if there is a previous capture to repeat."""
        return self._last is not None

    def repeat(
        self,
        on_region_needed: Callable | None = None,
    ) -> QPixmap | None:
        """Repeat the last capture.

        Args:
            on_region_needed: Callback if region selection UI is needed.

        Returns:
            QPixmap for immediate captures, or None if async (region).
        """
        if self._last is None:
            logger.warning("No previous capture to repeat.")
            return None

        match self._last.capture_type:
            case CaptureType.FULLSCREEN:
                logger.info("Repeating fullscreen capture.")
                return ScreenCapture.capture_all_monitors()

            case CaptureType.ACTIVE_WINDOW:
                logger.info("Repeating active window capture.")
                return WindowCapture.capture_active_window()

            case CaptureType.REGION:
                if self._last.region:
                    logger.info("Repeating region capture at saved region.")
                    return ScreenCapture.capture_region(self._last.region)
                if on_region_needed:
                    logger.info("Repeating region capture (prompting for region).")
                    on_region_needed()
                    return None
                logger.warning("Cannot repeat region capture without UI callback.")
                return None

            case CaptureType.WINDOW_PICK:
                if on_region_needed:
                    on_region_needed()
                return None

    @property
    def last_capture_type(self) -> CaptureType | None:
        """Return the type of the last capture, or None."""
        return self._last.capture_type if self._last else None
