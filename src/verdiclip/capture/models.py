"""Capture results and the screen layout they are taken from."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from PySide6.QtGui import QGuiApplication, QImage, QScreen

from verdiclip.geometry import Point, Rect


class CaptureMode(StrEnum):
    """How a capture was taken."""

    REGION = "region"
    WINDOW = "window"
    FULLSCREEN = "fullscreen"


@dataclass(frozen=True, slots=True)
class Capture:
    """A captured image and where it came from.

    ``area`` is in physical virtual-desktop pixels so it can be re-captured.
    """

    image: QImage
    mode: CaptureMode
    area: Rect
    title: str
    taken_at: datetime


@dataclass(frozen=True, slots=True)
class ScreenGeometry:
    """One monitor in both Qt logical and physical pixel coordinates."""

    name: str
    logical: Rect
    physical: Rect
    scale: float

    @classmethod
    def from_screen(cls, screen: QScreen) -> Self:
        """Describe ``screen``.

        Qt on Windows keeps each screen's top-left at its native (physical)
        position and scales only the size, so physical size = logical size * scale.
        """
        geo = screen.geometry()
        scale = screen.devicePixelRatio()
        return cls(
            name=screen.name(),
            logical=Rect(geo.x(), geo.y(), geo.width(), geo.height()),
            physical=Rect(
                geo.x(),
                geo.y(),
                round(geo.width() * scale),
                round(geo.height() * scale),
            ),
            scale=scale,
        )

    def to_physical(self, local: Point) -> Point:
        """Map a widget-local logical point on this screen to physical pixels."""
        return Point(
            self.physical.x + local.x * self.scale,
            self.physical.y + local.y * self.scale,
        )

    def to_local(self, physical: Point) -> Point:
        """Map a physical point to widget-local logical coordinates."""
        return Point(
            (physical.x - self.physical.x) / self.scale,
            (physical.y - self.physical.y) / self.scale,
        )

    def local_rect(self, physical: Rect) -> Rect:
        """Map a physical rectangle to widget-local logical coordinates."""
        top_left = self.to_local(physical.top_left)
        return Rect(
            top_left.x,
            top_left.y,
            physical.width / self.scale,
            physical.height / self.scale,
        )


class ScreenLayout:
    """The monitors currently attached."""

    @staticmethod
    def current() -> list[ScreenGeometry]:
        """Return every screen known to Qt."""
        return [ScreenGeometry.from_screen(s) for s in QGuiApplication.screens()]
