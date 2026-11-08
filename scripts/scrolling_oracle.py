"""Offline pixel coverage check for owned native diagnostic sequences only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from PySide6.QtGui import QImage
from scripts.scrolling_item_witness import header_layout, raw_bounds

from verdiclip.capture.scrolling import MAX_FRAMES, MAX_PIXELS
from verdiclip.geometry import Rect

_FULL_PERCENT = 100.0
_PAIR_SIZE = 2


class _HeaderOracle:
    """Map raw Header geometry and independent retained-item displacement evidence."""

    def __init__(self, path: Path) -> None:
        self.area, self.header = header_layout(
            json.loads(path.read_text(encoding="utf-8"))
        )
        self.height = round(self.header.height)
        self._body = Rect(
            self.area.left,
            self.header.bottom,
            self.header.width,
            self.area.bottom - self.header.bottom,
        )
        self._prefix: list[bytes] | None = None

    def offset(self, position: dict[str, object], index: int, previous: int) -> int:
        """Derive pixel offsets from two agreeing raw geometry deltas, never seams."""
        value = position.get("native_item_witness")
        if value is None:
            raise RuntimeError("Whole-image oracle lacks native item witnesses")
        witness = cast("dict[str, object]", value)
        items = cast("list[dict[str, object]]", witness["items"])
        if index == 0:
            if witness["from_frame"] is not None or items:
                raise RuntimeError("Whole-image oracle has an invalid first witness")
            return 0
        if witness["from_frame"] != index - 1 or len(items) != _PAIR_SIZE:
            raise RuntimeError("Whole-image oracle has an incomplete witness pair")
        before = []
        deltas = []
        for item in items:
            first, last = raw_bounds(item["before"]), raw_bounds(item["after"])
            if (
                item["same_identity"] is not True
                or item["same_parent"] is not True
                or first.is_empty
                or first.intersected(self._body) != first
                or last.intersected(self._body) != last
                or (first.x, first.width, first.height)
                != (last.x, last.width, last.height)
            ):
                raise RuntimeError(
                    "Whole-image oracle has invalid retained-item geometry"
                )
            before.append(first)
            deltas.append(first.y - last.y)
        before.sort(key=lambda rect: rect.top)
        if (
            before[0].bottom > before[1].top
            or deltas[0] != deltas[1]
            or not 0 < deltas[0] < self._body.height
            or deltas[0] != round(deltas[0])
        ):
            raise RuntimeError("Whole-image oracle witnesses disagree on displacement")
        return previous + round(deltas[0])

    def rows(
        self, frame: QImage, result: list[bytes], matched: bytearray
    ) -> list[bytes]:
        """Verify the original prefix exactly once and every actual Header pixel."""
        if frame.width() != self.area.width or frame.height() != self.area.height:
            raise RuntimeError("Whole-image oracle disagrees with raw Header viewport")
        rows = _rows(frame)
        prefix = [row[: round(self.header.width) * 4] for row in rows[: self.height]]
        if self._prefix is None:
            if result[: self.height] != rows[: self.height]:
                raise RuntimeError(
                    "Whole-image oracle did not preserve the original prefix"
                )
            matched[: self.height] = b"\1" * self.height
            self._prefix = prefix
        elif prefix != self._prefix:
            raise RuntimeError("Whole-image oracle Header changed during acquisition")
        return rows[self.height :]


def _rows(image: QImage) -> list[bytes]:
    converted = image.convertToFormat(QImage.Format.Format_RGB32)
    data = bytes(converted.constBits())
    stride = converted.bytesPerLine()
    return [data[y * stride : (y + 1) * stride] for y in range(image.height())]


def verify_sequence(image: QImage, output: Path) -> dict[str, object]:
    """Check every output row against acquired pixels at UIA-derived positions.

    This deliberately does not call the stitcher, use its seam or inferred
    displacement, or treat image acceptance as proof. It can reject a sequence
    whose provider geometry is imprecise. It does not prove hidden semantics or
    make repeated native scrollbars disappear.
    """
    if image.isNull() or image.width() * image.height() > MAX_PIXELS:
        raise RuntimeError("Whole-image oracle output exceeds the image limit")
    positions = json.loads((output / "frames.json").read_text(encoding="utf-8"))
    if not 1 < len(positions) <= MAX_FRAMES:
        raise RuntimeError("Whole-image oracle needs the bounded complete sequence")
    if positions[0]["vertical"] != 0.0 or positions[-1]["vertical"] != _FULL_PERCENT:
        raise RuntimeError("Whole-image oracle did not observe both document ends")
    result = _rows(image)
    matched = bytearray(image.height())
    total_pixels, last_top, viewport = 0, -1, 0
    first = positions[0]
    geometry = output / "header-geometry.json"
    header = _HeaderOracle(geometry) if geometry.is_file() else None
    for index, position in enumerate(positions):
        frame = QImage(str(output / f"frame-{index}.png"))
        viewport = viewport or frame.height()
        total_pixels += frame.width() * frame.height()
        if (
            frame.isNull()
            or frame.width() != image.width()
            or frame.height() != viewport
            or total_pixels > MAX_PIXELS
            or position["view_size"] != first["view_size"]
            or position["horizontal"] != first["horizontal"]
            or not 0 < first["view_size"] < _FULL_PERCENT
            or (index and position["vertical"] <= positions[index - 1]["vertical"])
        ):
            raise RuntimeError("Whole-image oracle frame geometry is inconsistent")
        # Independent absolute mapping, not accumulated stitch displacements.
        top = (
            header.offset(position, index, last_top)
            if header is not None
            else round(
                frame.height()
                * (_FULL_PERCENT / position["view_size"] - 1.0)
                * position["vertical"]
                / _FULL_PERCENT
            )
        )
        if top <= last_top or top + frame.height() > image.height():
            raise RuntimeError("Whole-image oracle positions disagree with output")
        last_top = top
        rows = (
            header.rows(frame, result, matched) if header is not None else _rows(frame)
        )
        for row, pixels in enumerate(
            rows, start=top + (header.height if header else 0)
        ):
            if pixels == result[row]:
                matched[row] = 1
    if last_top + viewport != image.height() or not all(matched):
        missing = len(matched) - sum(matched)
        raise RuntimeError(f"Whole-image oracle found {missing} unverified output rows")
    return {
        "oracle": (
            "all output rows match native frames at retained-item UIA positions"
            if header is not None
            else "all output rows match native frames at UIA-derived positions"
        ),
        "oracle_frames": len(positions),
        "oracle_verified_rows": len(matched),
    }
