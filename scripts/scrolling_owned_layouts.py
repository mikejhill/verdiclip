"""Native owned layouts with an independently tiled expected screenshot.

These authored pages are regression fixtures, never evidence of live Google or
signed-in To Do support. The oracle uses known CSS dimensions and independently
observed UIA positions, not product registration offsets or accepted seams.
Scrollbar-free authored panels make the full viewport width comparable without
silently ignoring a rail. The production adapter is not modified.
"""

from __future__ import annotations

import html
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage, QPainter
from scripts import simple_scroll_native as native
from scripts.scrolling_header_probe import window_dpi
from scripts.scrolling_live_sites import require_hosted

from verdiclip.platform.scrolling import UiaScrollTarget

_HEADER = 64
_ROW = 60
_ROWS = 40
_GROUP = 4
_CSS_DPI = 96
_GEOMETRY_TOLERANCE = 0.01
_CASES = ("edge-owned-search", "edge-owned-settings")


def document(kind: str) -> str:
    """Author realistic plain text and repeated settings with stable geometry."""
    native._require(kind in _CASES, "Unknown owned layout")
    search = kind == "edge-owned-search"
    rows = []
    for index in range(_ROWS):
        if search:
            title = f"Result {index + 1}: Windows application accessibility"
            detail = "Learn how native controls support accessible scrolling and input."
        else:
            title = (
                f"Notification group {index // _GROUP + 1}"
                if index % _GROUP == 0
                else "Send activity notifications"
            )
            detail = "Notify me when a shared task changes.                         On"
        rows.append(
            f'<div class="row"><strong>{html.escape(title)}</strong>'
            f"<span>{html.escape(detail)}</span></div>"
        )
    header = "Search results for native scrolling" if search else "Task settings"
    return (
        '<!doctype html><meta charset="utf-8">'
        "<title>VerdiClip scrolling check</title><style>"
        "body{margin:0;background:#f5f5f5;font:14px Arial;color:#222}"
        "section{position:absolute;left:20px;top:20px;"
        "width:calc(100vw - 40px);height:calc(100vh - 40px);"
        "overflow-y:scroll;scrollbar-width:none;background:white}"
        f"header{{position:sticky;top:0;height:{_HEADER}px;box-sizing:border-box;"
        "background:#eef2f6;border-bottom:1px solid #b0b5bc;padding:20px;"
        "font-size:20px;font-weight:bold;z-index:1}"
        f".row{{height:{_ROW}px;box-sizing:border-box;"
        "padding:8px 20px;border-bottom:1px solid #eee}"
        "strong,span{display:block;line-height:22px}"
        "span{color:#606060}"
        '</style><section tabindex=0 aria-label="Owned scrolling fixture">'
        f"<header>{header}</header>{''.join(rows)}</section>"
    )


def tile_plan(height: int, viewport: int, header: int) -> list[tuple[int, int, int]]:
    """Return (scroll offset, source row, row count) covering each output row once."""
    if not 0 < header < viewport < height:
        raise ValueError("Invalid authored layout geometry")
    plan = [(0, 0, viewport)]
    filled = viewport
    while filled < height:
        offset = min(filled - header, height - viewport)
        source = filled - offset
        count = min(viewport - source, height - filled)
        if count <= 0:
            raise ValueError("Reference tiles cannot make progress")
        plan.append((offset, source, count))
        filled += count
    return plan


def _settle(target: UiaScrollTarget, percent: float) -> None:
    target.move(percent)
    previous = None
    for _ in range(20):
        time.sleep(0.08)
        target.check()
        position = target.position()
        if position == previous and math.isclose(
            position.vertical, percent, abs_tol=0.1
        ):
            return
        previous = position
    raise RuntimeError("Reference UIA position did not settle")


def reference(target: UiaScrollTarget, output: Path) -> QImage:
    """Acquire independent native tiles at authored, measured pixel positions."""
    dpi = window_dpi(target)
    native._require(dpi in (96, 120, 192), "Unsupported reference DPI")
    scale = dpi / _CSS_DPI
    header, total = round(_HEADER * scale), round((_HEADER + _ROWS * _ROW) * scale)
    viewport, width = round(target.area.height), round(target.area.width)
    plan = tile_plan(total, viewport, header)
    result = QImage(width, total, QImage.Format.Format_RGB32)
    result.fill(0)
    painter = QPainter(result)
    receipt = []
    destination = 0
    try:
        for index, (offset, source, count) in enumerate(plan):
            _settle(target, offset / (total - viewport) * 100)
            position = target.position()
            native._require(
                math.isclose(
                    position.view_size,
                    viewport / total * 100,
                    abs_tol=_GEOMETRY_TOLERANCE,
                )
                and abs(position.vertical / 100 * (total - viewport) - offset)
                < _GEOMETRY_TOLERANCE,
                "UIA extent/position does not match authored CSS geometry",
            )
            first = target.frame()
            time.sleep(0.08)
            native._require(first == target.frame(), "Reference pixels did not settle")
            native._require(
                first.save(str(output / f"reference-{index}.png")), "Save failed"
            )
            painter.drawImage(0, destination, first.copy(0, source, width, count))
            receipt.append(
                {
                    "offset": offset,
                    "source": source,
                    "count": count,
                    "destination": destination,
                    "position": asdict(position),
                }
            )
            destination += count
    finally:
        painter.end()
    (output / "reference-tiles.json").write_text(
        json.dumps({"dpi": dpi, "total": total, "tiles": receipt}, indent=2),
        encoding="utf-8",
    )
    native._require(result.save(str(output / "expected.png")), "Save failed")
    return result


def case(kind: str, output: Path, report: dict[str, object]) -> None:
    """Compare the complete produced bitmap with the independent expected bitmap."""
    report.update(
        oracle="independent authored-geometry native screenshot tiles, full width",
        live_site_qualification=False,
        fixture_kind=kind,
    )
    window = native._prepare(native._window())
    report["window"] = asdict(window)
    native._desktop_evidence(output, report)
    point = window.bounds.center
    with UiaScrollTarget(point, window=window.handle) as target:
        expected = reference(target, output)
        start = native._position_start(target, report, nested=False)
        before = target.frame()
        tick = target._input_tick
    result = native._capture(point, output, kind, window=window.handle, input_tick=tick)
    report.update(
        message=result.message, frames=result.frames, restoration=result.restoration
    )
    if result.image is not None:
        native._require(result.image.save(str(output / "result.png")), "Save failed")
    with UiaScrollTarget(point, window=window.handle) as target:
        native._require(target.position() == start, "Original position not restored")
        native._require(target.frame() == before, "Original full viewport not restored")
    native._require(
        result.image is not None and result.restoration == "restored",
        result.message or result.restoration,
    )
    native._require(
        result.image == expected, "Full output differs from native reference"
    )
    report.update(success=True, independent_full_image_equal=True)


def main() -> None:
    """Run one owned regression without changing existing fixture source hashes."""
    require_hosted()
    native._require(
        len(sys.argv[1:]) == 1 and sys.argv[1] in _CASES, "Unknown owned case"
    )
    kind = sys.argv[1]
    with patch.object(native, "_document", lambda **_kwargs: document(kind)):
        native.main(case=case)


if __name__ == "__main__":
    main()
