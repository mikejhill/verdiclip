"""Bounded private frame-file handoff from the capture helper to QImage."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QImage, QImageReader

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


@dataclass(frozen=True, slots=True)
class FrameDestination:
    """One selected physical viewport and an exact private output file."""

    viewport: Rect
    path: Path


class FrameFiles:
    """Own at most one encoded frame; decode only matching bounded PNG dimensions.

    Register close with the worker scope so cleanup runs after child termination,
    even if its transient UI owner disappeared. This object opens no desktop and
    cannot capture or deliver a screenshot by itself.
    """

    ENCODER_OVERHEAD = 1024 * 1024

    def __init__(self, *, max_pixels: int, parent: Path | None = None) -> None:
        """Delay creating the private directory until an explicitly selected frame."""
        if max_pixels < 1:
            raise ValueError("Frame pixel budget must be positive")
        self._max_pixels = max_pixels
        self._parent = (
            parent if parent is not None else Path(tempfile.gettempdir())
        ).resolve()
        self._directory: Path | None = None
        self._closed = False

    def destination(self, viewport: Rect) -> FrameDestination:
        """Reserve a single file only within the selected frame's pixel budget."""
        if (
            self._closed
            or viewport.is_empty
            or viewport.width * viewport.height > self._max_pixels
        ):
            raise CaptureError(
                "Capture frame is unavailable or exceeds its pixel budget"
            )
        if self._directory is None:
            try:
                self._directory = Path(
                    tempfile.mkdtemp(prefix="verdiclip-frame-", dir=self._parent)
                ).resolve()
            except OSError as error:
                raise CaptureError(
                    "Could not prepare private capture pixels"
                ) from error
        path = self._directory / "frame.png"
        if path.exists():
            raise CaptureError("The previous capture frame has not been consumed")
        return FrameDestination(viewport, path)

    def consume(self, destination: FrameDestination) -> QImage:
        """Refuse substituted paths, oversized files and mismatched encoded images."""
        path = destination.path
        if (
            self._directory is None
            or path != self._directory / "frame.png"
            or path.is_symlink()
        ):
            raise CaptureError("Capture helper returned an unrelated frame file")
        try:
            self._verify_directory()
            size = path.stat().st_size
            if (
                size < 1
                or size
                > destination.viewport.width * destination.viewport.height * 4
                + self.ENCODER_OVERHEAD
            ):
                raise CaptureError("Encoded capture frame exceeds its size budget")
            image = self._decode(destination)
            path.unlink()
        except OSError as error:
            raise CaptureError("Capture frame is unavailable") from error
        else:
            return image

    @staticmethod
    def _decode(destination: FrameDestination) -> QImage:
        """Check dimensions before allocation and close the reader before unlink."""
        reader = QImageReader(str(destination.path), b"png")
        size = reader.size()
        area = destination.viewport
        if size.width() != area.width or size.height() != area.height:
            raise CaptureError("Encoded capture frame dimensions changed")
        image = reader.read()
        if (
            image.isNull()
            or image.width() != area.width
            or image.height() != area.height
        ):
            raise CaptureError("Capture frame could not be decoded")
        return image

    def _verify_directory(self) -> None:
        """Never traverse a replaced/symlinked private directory during read/cleanup."""
        directory = self._directory
        if (
            directory is None
            or directory.resolve() != directory
            or directory.parent != self._parent
            or not directory.name.startswith("verdiclip-frame-")
        ):
            raise CaptureError("Capture frame directory ownership changed")

    def close(self) -> None:
        """Idempotently remove only the verified private frame directory."""
        if self._directory is not None:
            self._verify_directory()
            try:
                shutil.rmtree(self._directory)
            except OSError as error:
                raise CaptureError(
                    "Could not remove temporary capture pixels"
                ) from error
            self._directory = None
        self._closed = True
