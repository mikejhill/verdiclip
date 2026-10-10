"""Whole-image changes: rotate, flip, and resize, with annotations following.

Rotations and flips act on the full image so the non-destructive crop maps
exactly. Resizing works on what the user sees: the cropped area is scaled to
the requested size and becomes the new image.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, override

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QTransform

from verdiclip.document.document import DocumentState
from verdiclip.geometry import Affine, Rect

MAX_DIMENSION: Final = 20_000


class FlipAxis(StrEnum):
    """Which way a flip mirrors the image."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"


class ImageTransform(ABC):
    """A change applied to the whole document state."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Return the user-facing name shown as "Undo <description>"."""

    @abstractmethod
    def apply(self, state: DocumentState) -> DocumentState:
        """Return the transformed state."""

    @staticmethod
    def _map_state(
        state: DocumentState, image: QImage, transform: Affine, crop: Rect
    ) -> DocumentState:
        """Return ``image`` with ``crop`` and every annotation mapped."""
        return DocumentState(
            image,
            crop,
            tuple(a.transformed(transform) for a in state.annotations),
        )


@dataclass(frozen=True, slots=True)
class Rotate(ImageTransform):
    """Turn the image a quarter turn."""

    clockwise: bool

    @property
    @override
    def description(self) -> str:
        """Return "Rotate right" or "Rotate left"."""
        return "Rotate right" if self.clockwise else "Rotate left"

    @override
    def apply(self, state: DocumentState) -> DocumentState:
        """Rotate pixels, crop, and annotations by 90 degrees."""
        width, height = state.image.width(), state.image.height()
        if self.clockwise:
            transform = Affine(0, -1, height, 1, 0, 0)
        else:
            transform = Affine(0, 1, 0, -1, 0, width)
        angle = 90 if self.clockwise else -90
        image = state.image.transformed(QTransform().rotate(angle))
        return self._map_state(state, image, transform, transform.map_rect(state.crop))


@dataclass(frozen=True, slots=True)
class Flip(ImageTransform):
    """Mirror the image."""

    axis: FlipAxis

    @property
    @override
    def description(self) -> str:
        """Return "Flip horizontally" or "Flip vertically"."""
        return f"Flip {self.axis.value}ly"

    @override
    def apply(self, state: DocumentState) -> DocumentState:
        """Mirror pixels, crop, and annotations."""
        width, height = state.image.width(), state.image.height()
        if self.axis is FlipAxis.HORIZONTAL:
            transform = Affine(-1, 0, width, 0, 1, 0)
            orientation = Qt.Orientation.Horizontal
        else:
            transform = Affine(1, 0, 0, 0, -1, height)
            orientation = Qt.Orientation.Vertical
        image = state.image.flipped(orientation)
        return self._map_state(state, image, transform, transform.map_rect(state.crop))


@dataclass(frozen=True, slots=True)
class Resize(ImageTransform):
    """Scale the visible (cropped) image to an exact size in pixels."""

    width: int
    height: int

    def __post_init__(self) -> None:
        """Reject sizes outside 1..MAX_DIMENSION."""
        for value in (self.width, self.height):
            if not 1 <= value <= MAX_DIMENSION:
                msg = f"Size must be 1-{MAX_DIMENSION} pixels, got {value}"
                raise ValueError(msg)

    @property
    @override
    def description(self) -> str:
        """Return "Resize"."""
        return "Resize"

    @override
    def apply(self, state: DocumentState) -> DocumentState:
        """Scale the crop to the target size; annotations scale with it."""
        crop = state.crop.rounded()
        source = state.image.copy(
            QRect(int(crop.x), int(crop.y), int(crop.width), int(crop.height))
        )
        image = source.scaled(
            self.width,
            self.height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = self.width / crop.width
        sy = self.height / crop.height
        transform = Affine(sx, 0, -crop.x * sx, 0, sy, -crop.y * sy)
        return self._map_state(
            state, image, transform, Rect(0, 0, self.width, self.height)
        )
