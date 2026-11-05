"""Transactionally spool lossless strips and materialize only bounded ranges."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PySide6.QtGui import QImage, QImageWriter

from verdiclip.capture.scroll.models import ScrollLimitError, ScrollLimits
from verdiclip.exceptions import CaptureError
from verdiclip.startup_lifetime import StartupLifetime


class StripWriter(Protocol):
    """One bounded image write; implementations must not mutate their input."""

    def write(self, image: QImage, path: Path) -> None:
        """Write lossless pixels or raise CaptureError/OSError."""


class PngStripWriter:
    """Write an explicitly PNG-encoded temporary file regardless of suffix."""

    def write(self, image: QImage, path: Path) -> None:
        """Surface encoder errors instead of committing an incomplete strip."""
        writer = QImageWriter(str(path), b"png")
        # Transient spool files favor fast lossless encoding, not export size.
        # Keep actual-byte quotas and the conservative raw-pixel estimate below.
        writer.setCompression(20)
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


@dataclass(frozen=True, slots=True)
class AppendRequest:
    """One immutable reservation; staging cannot mutate its owning store."""

    token: object
    generation: int
    image: QImage
    parent: Path
    limits: ScrollLimits
    writer: StripWriter
    top: int
    used_bytes: int
    estimate: int


@dataclass(slots=True)
class SpoolDirectory:
    """Transferable cleanup ownership, separate from immutable pixel metadata."""

    parent: Path
    temporary: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False)

    def close(self) -> None:
        """Retain the exact directory owner when cleanup needs a retry."""
        if self.temporary is not None:
            try:
                target = Path(self.temporary.name).resolve()
                if target.parent != self.parent or not target.name.startswith(
                    "verdiclip-scroll-"
                ):
                    raise CaptureError(
                        "Refusing cleanup outside this capture's storage parent"
                    )
                self.temporary.cleanup()
            except OSError as error:
                raise CaptureError(
                    "Could not remove temporary capture storage"
                ) from error
            self.temporary = None


@dataclass(frozen=True, slots=True)
class StagedAppend:
    """Immutable transaction evidence plus its independently retained directory."""

    token: object
    generation: int
    top: int
    used_bytes: int
    width: int
    height: int
    directory: SpoolDirectory
    strips: tuple[StoredStrip, ...] = ()
    error: BaseException | None = None

    def close(self) -> None:
        """Release only the directory owned by this transaction."""
        self.directory.close()


class StripStore:
    """Own private transaction directories and an atomic in-memory strip manifest."""

    TILE_ROWS = 512
    FREE_RESERVE = 32 * 1024**2

    def __init__(
        self, parent: Path, limits: ScrollLimits, *, writer: StripWriter | None = None
    ) -> None:
        """Store dependencies; create no directory until the first append."""
        self._parent = parent.resolve()
        self._limits = limits
        self._writer = writer if writer is not None else PngStripWriter()
        self._transactions: list[StagedAppend] = []
        self._pending: AppendRequest | None = None
        self._generation = 0
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
        request = self.prepare(image)
        receipt = self.stage(request)
        try:
            self.admit(receipt)
        except BaseException:
            self.abandon()
            with StartupLifetime() as lifetime:
                lifetime.callback(self.discard, receipt)
                raise

    def discard(self, receipt: StagedAppend) -> None:
        """Retain synchronous failed cleanup ownership until an explicit retry."""
        self._transactions.append(receipt)
        receipt.close()
        self._transactions.remove(receipt)

    def prepare(self, image: QImage) -> AppendRequest:
        """Reserve bounded pixels without doing filesystem work on the owner."""
        if self._pending is not None:
            raise CaptureError("Capture storage already has an append in flight")
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
        request = AppendRequest(
            object(),
            self._generation,
            QImage(image),
            self._parent,
            self._limits,
            self._writer,
            self._height,
            self._bytes,
            estimate,
        )
        self._pending = request
        return request

    @classmethod
    def stage(cls, request: AppendRequest) -> StagedAppend:
        """Stage in an isolated directory; never mutate the store or its prefix."""
        image = request.image
        owner = SpoolDirectory(request.parent)
        strips: tuple[StoredStrip, ...] = ()
        failure: BaseException | None = None
        try:
            request.parent.mkdir(parents=True, exist_ok=True)
            owner.temporary = tempfile.TemporaryDirectory(
                prefix="verdiclip-scroll-", dir=request.parent
            )
            directory = Path(owner.temporary.name)
            cls._ensure_free(directory, request.estimate)
            strips = tuple(cls._write_tiles(request, directory))
        except ScrollLimitError as error:
            failure = error
        except Exception as error:  # noqa: BLE001 - return directory ownership even on writer bugs
            failure = CaptureError(f"Could not commit capture pixels: {error}")
        except BaseException as error:  # noqa: BLE001 - caller propagates controls after owning cleanup
            failure = error
        return StagedAppend(
            request.token,
            request.generation,
            request.top,
            request.used_bytes,
            image.width(),
            image.height(),
            owner,
            strips,
            failure,
        )

    def admit(self, receipt: StagedAppend) -> None:
        """Atomically admit only the current reservation using metadata alone."""
        pending = self._pending
        if (
            self._closed
            or pending is None
            or receipt.token is not pending.token
            or receipt.generation != self._generation
            or receipt.top != self._height
            or receipt.used_bytes != self._bytes
            or receipt.width != pending.image.width()
            or receipt.height != pending.image.height()
        ):
            raise CaptureError("Capture storage receipt is stale")
        if receipt.error is not None:
            raise receipt.error
        size = sum(strip.size_bytes for strip in receipt.strips)
        if (
            receipt.width * (self._height + receipt.height) > self._limits.max_pixels
            or self._bytes + size > self._limits.max_disk_bytes
        ):
            raise ScrollLimitError("Capture storage resource limit reached")
        self._transactions.append(receipt)
        self._strips += receipt.strips
        self._width = receipt.width
        self._height += receipt.height
        self._bytes += size
        self.abandon()

    def abandon(self) -> None:
        """Invalidate pending admission without touching its worker-owned files."""
        self._pending = None
        self._generation += 1

    @classmethod
    def _ensure_free(cls, directory: Path, estimate: int) -> None:
        """Keep room for the staged transaction without filling the user's disk."""
        if shutil.disk_usage(directory).free < estimate + cls.FREE_RESERVE:
            raise ScrollLimitError("Not enough free disk space for capture")

    @classmethod
    def _write_tiles(cls, request: AppendRequest, directory: Path) -> list[StoredStrip]:
        """Stage tiles within this transaction's pessimistic storage budget."""
        image = request.image
        additions: list[StoredStrip] = []
        for top in range(0, image.height(), cls.TILE_ROWS):
            height = min(cls.TILE_ROWS, image.height() - top)
            path = directory / f"{len(additions):06d}.png.part"
            request.writer.write(image.copy(0, top, image.width(), height), path)
            size = path.stat().st_size
            if (
                request.used_bytes + sum(s.size_bytes for s in additions) + size
                > request.limits.max_disk_bytes
            ):
                raise ScrollLimitError("Encoded capture exceeds the storage limit")
            final = path.with_suffix("")
            path.replace(final)
            additions.append(
                StoredStrip(
                    final,
                    request.top + top,
                    image.width(),
                    height,
                    size,
                    cls._digest(final),
                )
            )
        return additions

    @staticmethod
    def _digest(path: Path) -> str:
        """Read only one bounded encoded tile when verifying integrity."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def materialize(self, top: int = 0, height: int | None = None) -> QImage:
        """Assemble a size-checked range without flattening the whole spool."""
        height = self._height - top if height is None else height
        self._validate_range(top, height)
        result = QImage(self._width, height, QImage.Format.Format_ARGB32)
        if result.isNull():
            raise CaptureError("Could not allocate this capture page")
        for strip in self._strips:
            if strip.top < top + height and strip.top + strip.height > top:
                self._copy_strip(result, strip, top, height)
        return result

    def _copy_strip(
        self, result: QImage, strip: StoredStrip, top: int, height: int
    ) -> None:
        """Copy canonical rows exactly; alpha must never blend with old memory."""
        image = self._read(strip).convertToFormat(QImage.Format.Format_ARGB32)
        if image.isNull():
            raise CaptureError("Could not decode this capture page")
        first, last = max(top, strip.top), min(top + height, strip.top + strip.height)
        source = (first - strip.top) * image.bytesPerLine()
        destination = (first - top) * result.bytesPerLine()
        count = (last - first) * result.bytesPerLine()
        pixels = memoryview(result.bits())
        if pixels.readonly:
            raise CaptureError("Could not access writable capture pixels")
        pixels[destination : destination + count] = image.constBits()[
            source : source + count
        ]

    def _validate_range(self, top: int, height: int) -> None:
        """Check coordinate, pixel, dimension and conservative copy budgets."""
        if self._closed or top < 0 or height < 1 or top + height > self._height:
            raise CaptureError("Capture page is outside the stored range")
        if height > self.page_capacity:
            raise CaptureError(
                "This capture is too large to flatten; choose smaller page slices"
            )

    @property
    def page_capacity(self) -> int:
        """Return safe page rows using the actual committed decoded-tile budget."""
        if self._closed or self._width < 1 or self._width > self._limits.max_dimension:
            return 0
        tile_pixels = max((s.width * s.height for s in self._strips), default=0)
        available = self._limits.max_materialize_bytes - tile_pixels * 4 * 2
        return max(
            0,
            min(
                self._height,
                self._limits.max_dimension,
                self._limits.max_flatten_pixels // self._width,
                available // (self._width * 4 * 6),
            ),
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
        self.abandon()
        for transaction in self._transactions:
            transaction.close()
        self._transactions.clear()
        self._strips = ()
        self._closed = True

    def release(self) -> tuple[StagedAppend, ...]:
        """Transfer cleanup ownership without touching files or leaving live pixels."""
        self.abandon()
        transactions = tuple(self._transactions)
        self._transactions.clear()
        self._strips = ()
        self._closed = True
        return transactions
