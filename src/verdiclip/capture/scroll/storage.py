"""Transactionally spool lossless strips and materialize only bounded ranges."""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QImageWriter, QPainter

from verdiclip.capture.scroll.models import ScrollLimitError, ScrollLimits
from verdiclip.exceptions import CaptureError

logger = logging.getLogger(__name__)


class StripWriter(Protocol):
    """One bounded image write; implementations must not mutate their input."""

    def write(self, image: QImage, path: Path) -> None:
        """Write lossless pixels or raise CaptureError/OSError."""


class PngStripWriter:
    """Write an explicitly PNG-encoded temporary file regardless of suffix."""

    def write(self, image: QImage, path: Path) -> None:
        """Surface encoder errors instead of committing an incomplete strip."""
        writer = QImageWriter(str(path), b"png")
        if not writer.write(image):
            raise CaptureError(
                f"Could not store capture pixels: {writer.errorString()}"
            )


@dataclass(frozen=True, slots=True)
class StoredStrip:
    """One committed file plus exact row location and integrity checksum."""

    path: Path
    top: int
    width: int
    height: int
    size_bytes: int
    sha256: str


class StripStore:
    """Own one private temporary folder and an atomic in-memory strip manifest."""

    TILE_ROWS = 512
    FREE_RESERVE = 32 * 1024**2

    def __init__(
        self, parent: Path, limits: ScrollLimits, *, writer: StripWriter | None = None
    ) -> None:
        """Store dependencies; create no directory until the first append."""
        self._parent = parent.resolve()
        self._limits = limits
        self._writer = writer if writer is not None else PngStripWriter()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._strips: tuple[StoredStrip, ...] = ()
        self._width = 0
        self._height = 0
        self._bytes = 0
        self._closed = False

    @property
    def height(self) -> int:
        """Return only rows from fully committed transactions."""
        return self._height

    @property
    def width(self) -> int:
        """Return the fixed strip width, or zero before acquisition."""
        return self._width

    @property
    def strips(self) -> tuple[StoredStrip, ...]:
        """Return a read-only manifest valid until this store closes."""
        return self._strips

    def append(self, image: QImage) -> None:
        """Commit every bounded tile or leave the prior manifest unchanged."""
        if self._closed or image.isNull():
            raise CaptureError("Capture storage is closed or pixels are empty")
        if self._width and image.width() != self._width:
            raise CaptureError("Capture width changed")
        pixels = image.width() * (self._height + image.height())
        estimate = (
            image.width() * image.height() * 4
            + image.height()
            + 4096 * ((image.height() + self.TILE_ROWS - 1) // self.TILE_ROWS)
        )
        if (
            pixels > self._limits.max_pixels
            or self._bytes + estimate > self._limits.max_disk_bytes
        ):
            raise ScrollLimitError("Capture storage resource limit reached")
        created: list[Path] = []
        try:
            directory = self._directory()
            self._ensure_free(directory, estimate)
            additions = self._write_tiles(image, directory, created)
        except ScrollLimitError:
            self._rollback(created)
            raise
        except (OSError, CaptureError) as error:
            self._rollback(created)
            raise CaptureError(f"Could not commit capture pixels: {error}") from error
        self._strips += tuple(additions)
        self._width = image.width()
        self._height += image.height()
        self._bytes += sum(strip.size_bytes for strip in additions)

    @classmethod
    def _ensure_free(cls, directory: Path, estimate: int) -> None:
        """Keep room for the staged transaction without filling the user's disk."""
        if shutil.disk_usage(directory).free < estimate + cls.FREE_RESERVE:
            raise ScrollLimitError("Not enough free disk space for capture")

    def _directory(self) -> Path:
        """Create an isolated application-owned folder under the explicit parent."""
        if self._temporary is None:
            self._parent.mkdir(parents=True, exist_ok=True)
            self._temporary = tempfile.TemporaryDirectory(
                prefix="verdiclip-scroll-", dir=self._parent
            )
        return Path(self._temporary.name)

    def _write_tiles(
        self, image: QImage, directory: Path, created: list[Path]
    ) -> list[StoredStrip]:
        """Stage tiles within this transaction's pessimistic storage budget."""
        additions: list[StoredStrip] = []
        for top in range(0, image.height(), self.TILE_ROWS):
            height = min(self.TILE_ROWS, image.height() - top)
            path = directory / f"{len(self._strips) + len(additions):06d}.png.part"
            created.append(path)
            self._writer.write(image.copy(0, top, image.width(), height), path)
            size = path.stat().st_size
            if (
                self._bytes + sum(s.size_bytes for s in additions) + size
                > self._limits.max_disk_bytes
            ):
                raise ScrollLimitError("Encoded capture exceeds the storage limit")
            final = path.with_suffix("")
            path.replace(final)
            created[-1] = final
            additions.append(
                StoredStrip(
                    final,
                    self._height + top,
                    image.width(),
                    height,
                    size,
                    self._digest(final),
                )
            )
        return additions

    @staticmethod
    def _digest(path: Path) -> str:
        """Read only one bounded encoded tile when verifying integrity."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _rollback(paths: Sequence[Path]) -> None:
        """Remove only transaction-owned files, retaining any prior prefix."""
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove an uncommitted capture tile")

    def materialize(self, top: int = 0, height: int | None = None) -> QImage:
        """Assemble a size-checked range without flattening the whole spool."""
        height = self._height - top if height is None else height
        self._validate_range(top, height)
        result = QImage(self._width, height, QImage.Format.Format_ARGB32)
        if result.isNull():
            raise CaptureError("Could not allocate this capture page")
        painter = QPainter(result)
        try:
            for strip in self._strips:
                if strip.top < top + height and strip.top + strip.height > top:
                    image = self._read(strip)
                    first, last = (
                        max(top, strip.top),
                        min(top + height, strip.top + strip.height),
                    )
                    painter.drawImage(
                        QPoint(0, first - top),
                        image.copy(0, first - strip.top, self._width, last - first),
                    )
        finally:
            painter.end()
        return result

    def _validate_range(self, top: int, height: int) -> None:
        """Check coordinate, pixel, dimension and conservative copy budgets."""
        if self._closed or top < 0 or height < 1 or top + height > self._height:
            raise CaptureError("Capture page is outside the stored range")
        pixels = self._width * height
        if (
            pixels > self._limits.max_flatten_pixels
            or max(self._width, height) > self._limits.max_dimension
            or pixels * 4 * 6 > self._limits.max_materialize_bytes
        ):
            raise CaptureError(
                "This capture is too large to flatten; choose smaller page slices"
            )

    def _read(self, strip: StoredStrip) -> QImage:
        """Refuse missing, changed or incorrectly sized stored pixels."""
        try:
            if self._digest(strip.path) != strip.sha256:
                raise CaptureError("Stored capture pixels failed their integrity check")
            image = QImage(str(strip.path))
        except OSError as error:
            raise CaptureError("Stored capture pixels are unavailable") from error
        if (
            image.isNull()
            or image.width() != strip.width
            or image.height() != strip.height
        ):
            raise CaptureError("Stored capture pixels have invalid dimensions")
        return image

    def close(self) -> None:
        """Idempotently delete only this store's verified private directory."""
        temporary = self._temporary
        if temporary is not None:
            target = Path(temporary.name).resolve()
            if target.parent != self._parent or not target.name.startswith(
                "verdiclip-scroll-"
            ):
                raise CaptureError(
                    "Refusing cleanup outside this capture's storage parent"
                )
            try:
                temporary.cleanup()
            except OSError as error:
                raise CaptureError(
                    "Could not remove temporary capture storage"
                ) from error
            self._temporary = None
        self._strips = ()
        self._closed = True
