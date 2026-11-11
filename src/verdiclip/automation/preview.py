"""Render a document's current state to a PNG an AI tool can look at."""

from __future__ import annotations

import logging
import math
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QImageWriter,
    QPainter,
    QPen,
)

from verdiclip.automation.protocol import (
    DEFAULT_MAX_SIDE,
    AutomationError,
    ErrorCode,
)
from verdiclip.document.document import Document
from verdiclip.geometry import Rect
from verdiclip.render.renderer import Renderer

logger = logging.getLogger(__name__)

MIN_GRID_SPACING_PX: Final = 40.0
PREVIEW_MAX_AGE_S: Final = 24 * 60 * 60
GRID_LINE: Final = QColor(255, 0, 170, 150)
LABEL_TEXT: Final = QColor(255, 255, 255)
LABEL_BACK: Final = QColor(20, 20, 20, 190)
LABEL_PIXELS: Final = 11
LABEL_PADDING: Final = 2.0


@dataclass(frozen=True, slots=True)
class PreviewOptions:
    """What part of the document to render, and how."""

    region: Rect | None = None
    max_side: int = DEFAULT_MAX_SIDE
    grid: int = 0
    base: bool = False


@dataclass(frozen=True, slots=True)
class Preview:
    """A rendered image plus how its pixels map back to image coordinates.

    Image coordinate = ``origin`` + preview pixel / ``scale``.
    """

    image: QImage
    origin: tuple[int, int]
    scale: float
    grid: int


class PreviewRenderer:
    """Produce previews of the live document, including unsaved edits."""

    def __init__(self, renderer: Renderer | None = None) -> None:
        self._renderer = renderer or Renderer()

    def render(self, document: Document, options: PreviewOptions) -> Preview:
        """Return the visible document, optionally cut, scaled, and gridded.

        Raises:
            AutomationError: If the options are invalid for this document.
        """
        if options.max_side < 0:
            msg = f"max_side must be 0 (no limit) or positive, got {options.max_side}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if options.grid < 0:
            msg = f"grid must be 0 (off) or positive, got {options.grid}"
            raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        crop = document.crop.rounded()
        area = crop
        if options.region is not None:
            area = options.region.rounded().intersected(crop)
            if area.is_empty:
                msg = f"Region {_rect_text(options.region)} is outside the visible "
                msg += f"image {_rect_text(crop)}"
                raise AutomationError(ErrorCode.BAD_REQUEST, msg)
        if options.base:
            image = document.image.copy(*_ints(area))
        else:
            flat = self._renderer.flatten(document)
            image = flat.copy(
                int(area.x - crop.x), int(area.y - crop.y), *_ints(area)[2:]
            )
        scale = 1.0
        longest = max(image.width(), image.height())
        if options.max_side and longest > options.max_side:
            scale = options.max_side / longest
            image = image.scaled(
                max(1, round(image.width() * scale)),
                max(1, round(image.height() * scale)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        grid = self._grid_step(options.grid, scale)
        if grid:
            image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            self._draw_grid(image, area, scale, grid)
        return Preview(image, (int(area.x), int(area.y)), scale, grid)

    @staticmethod
    def _grid_step(requested: int, scale: float) -> int:
        """Widen the grid until its lines are far enough apart to label."""
        if not requested:
            return 0
        step = requested
        while step * scale < MIN_GRID_SPACING_PX:
            step *= 2
        return step

    @staticmethod
    def _draw_grid(image: QImage, area: Rect, scale: float, step: int) -> None:
        """Draw lines every ``step`` image pixels, labelled in image coordinates."""
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, on=False)
            font = QFont()
            font.setPixelSize(LABEL_PIXELS)
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            painter.setPen(QPen(GRID_LINE, 1))
            xs = range(math.ceil(area.left / step) * step, int(area.right), step)
            ys = range(math.ceil(area.top / step) * step, int(area.bottom), step)
            for x in xs:
                px = (x - area.x) * scale
                painter.drawLine(QPointF(px, 0), QPointF(px, image.height()))
            for y in ys:
                py = (y - area.y) * scale
                painter.drawLine(QPointF(0, py), QPointF(image.width(), py))
            for x in xs:
                _label(painter, metrics, str(x), QPointF((x - area.x) * scale + 2, 0))
            for y in ys:
                _label(painter, metrics, str(y), QPointF(0, (y - area.y) * scale + 2))
        finally:
            painter.end()


def _label(painter: QPainter, metrics: QFontMetricsF, text: str, at: QPointF) -> None:
    """Draw ``text`` on a dark chip with its top-left corner at ``at``."""
    width = metrics.horizontalAdvance(text) + 2 * LABEL_PADDING
    height = metrics.height() + LABEL_PADDING
    box = QRectF(at.x(), at.y(), width, height)
    painter.fillRect(box, LABEL_BACK)
    painter.setPen(LABEL_TEXT)
    painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
    painter.setPen(QPen(GRID_LINE, 1))


def _ints(rect: Rect) -> tuple[int, int, int, int]:
    """Return ``rect`` as whole-pixel x, y, width, height."""
    return int(rect.x), int(rect.y), int(rect.width), int(rect.height)


def _rect_text(rect: Rect) -> str:
    """Format ``rect`` as X,Y,W,H for messages."""
    return ",".join(f"{v:g}" for v in (rect.x, rect.y, rect.width, rect.height))


class PreviewFolder:
    """Where previews go when the caller names no file."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or self.default_root()

    @property
    def root(self) -> Path:
        """Return the folder previews are written to."""
        return self._root

    @staticmethod
    def default_root() -> Path:
        """Return ``%LOCALAPPDATA%/VerdiClip/automation`` (temp fallback)."""
        base = os.environ.get("LOCALAPPDATA")
        if base is not None and base:
            return Path(base) / "VerdiClip" / "automation"
        return Path(tempfile.gettempdir()) / "VerdiClip-automation"

    def new_path(self, document_id: str, state: int) -> Path:
        """Return a fresh file name for one render of ``document_id``."""
        # A random suffix: two renders in the same instant must not share a file
        return self._root / f"{document_id[:8]}-s{state}-{uuid.uuid4().hex[:12]}.png"

    def prune(self, now: float | None = None) -> None:
        """Delete previews older than a day; never fail the request over it."""
        cutoff = (now if now is not None else time.time()) - PREVIEW_MAX_AGE_S
        try:
            entries = list(self._root.glob("*.png"))
        except OSError:
            return
        for entry in entries:
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError as err:
                logger.debug("Could not prune %s: %s", entry, err)


def write_png(image: QImage, path: Path) -> Path:
    """Save ``image`` as PNG at ``path`` and return the absolute path.

    Raises:
        AutomationError: If the file cannot be written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        msg = f"Cannot create {path.parent}: {err}"
        raise AutomationError(ErrorCode.FAILED, msg) from err
    writer = QImageWriter(str(path), b"png")
    if not writer.write(image):
        msg = f"Cannot write {path}: {writer.errorString()}"
        raise AutomationError(ErrorCode.FAILED, msg)
    return path.resolve()
