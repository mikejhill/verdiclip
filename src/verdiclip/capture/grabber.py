"""Grab screen pixels at physical resolution with mss."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

import mss
from PySide6.QtGui import QImage

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FrozenScreen:
    """A snapshot of the whole virtual desktop.

    ``bounds`` is the desktop rectangle in physical pixels; ``image`` pixel
    (0, 0) corresponds to ``bounds.top_left``.
    """

    image: QImage
    bounds: Rect

    def crop(self, area: Rect) -> QImage:
        """Return the pixels of ``area`` (physical desktop coordinates).

        Raises:
            CaptureError: If ``area`` does not overlap the desktop.
        """
        clipped = area.rounded().intersected(self.bounds)
        if clipped.is_empty:
            msg = f"Area {area} is outside the desktop {self.bounds}"
            raise CaptureError(msg)
        return self.image.copy(
            int(clipped.x - self.bounds.x),
            int(clipped.y - self.bounds.y),
            int(clipped.width),
            int(clipped.height),
        )


class ScreenSource(Protocol):
    """Anything that can snapshot the desktop."""

    def freeze(self) -> FrozenScreen:
        """Return a snapshot of every monitor."""


class MssScreenSource:
    """Snapshot the desktop with mss (physical pixels, all monitors).

    Each freeze opens its own mss handle: mss caches the monitor layout for the
    life of a handle, so a reused one keeps the old desktop size after a
    display change (a resized remote session, a new resolution or monitor)
    and grabs only part of the screen. Call from the GUI thread.
    """

    def freeze(self) -> FrozenScreen:
        """Return a snapshot of every monitor, as laid out right now.

        Raises:
            CaptureError: If the screen cannot be read.
        """
        try:
            with mss.MSS() as grabber:
                desktop = grabber.monitors[0]
                shot = grabber.grab(desktop)
        except mss.ScreenShotError as err:
            msg = f"Could not capture the screen: {err}"
            raise CaptureError(msg) from err
        width, height = shot.width, shot.height
        # Raw BGRA bytes are exactly QImage's little-endian RGB32 layout
        image = QImage(
            shot.raw, width, height, width * 4, QImage.Format.Format_RGB32
        ).copy()
        bounds = Rect(desktop["left"], desktop["top"], width, height)
        logger.debug("Froze desktop %s", bounds)
        return FrozenScreen(image=image, bounds=bounds)
