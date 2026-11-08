"""Offline pixel coverage check for owned native diagnostic sequences only."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtGui import QImage

from verdiclip.capture.scrolling import MAX_FRAMES, MAX_PIXELS

_FULL_PERCENT = 100.0


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
        ):
            raise RuntimeError("Whole-image oracle frame geometry is inconsistent")
        # Independent absolute mapping, not accumulated stitch displacements.
        top = round(
            frame.height()
            * (_FULL_PERCENT / position["view_size"] - 1.0)
            * position["vertical"]
            / _FULL_PERCENT
        )
        if top <= last_top or top + frame.height() > image.height():
            raise RuntimeError("Whole-image oracle positions disagree with output")
        last_top = top
        for row, pixels in enumerate(_rows(frame), start=top):
            if pixels == result[row]:
                matched[row] = 1
    if not all(matched):
        missing = len(matched) - sum(matched)
        raise RuntimeError(f"Whole-image oracle found {missing} unverified output rows")
    return {
        "oracle": "all output rows match native frames at UIA-derived positions",
        "oracle_frames": len(positions),
        "oracle_verified_rows": len(matched),
    }
