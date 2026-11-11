"""Hosted native proof for a bottom-pinned component entering document flow."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from PySide6.QtGui import QImage, QPainter
from scripts import scrolling_owned_layouts as owned
from scripts import simple_scroll_native as native
from scripts.scrolling_header_probe import window_dpi
from scripts.scrolling_live_sites import require_hosted

from verdiclip.capture.scrolling import AmbiguousScrollError, VerticalStitch
from verdiclip.platform.scrolling import UiaScrollTarget


class MotionFixture:
    """One authored sticky transition with full-width, unique rendered row detail."""

    TOTAL = 3856
    VIEWPORT = 608
    PINNED_TOP = 466
    FLOW_TOP = 2164
    TILE_HEIGHT = 108
    GEOMETRY_TOLERANCE = 0.01

    @staticmethod
    def _pattern(height: int, *, inverse: bool = False) -> str:
        """Render deterministic unique rows without runtime browser scripts."""
        rows = []
        for row in range(height):
            red, green, blue = row % 251, row // 251, 90
            if inverse:
                red, green, blue = 255 - red, 255 - green, 255 - blue
            rows.append(
                f'<path d="M0 {row}.5H1000" stroke="rgb({red},{green},{blue})"/>'
            )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="{height}" '
            f'viewBox="0 0 1000 {height}" preserveAspectRatio="none">'
            f"{''.join(rows)}</svg>"
        )

    @classmethod
    def document(cls, **_kwargs: object) -> str:
        """Use native CSS sticky layout for the observed pinned-to-flow geometry."""
        return (
            '<!doctype html><meta charset="utf-8">'
            "<title>VerdiClip scrolling check</title><style>"
            "body{margin:0}section{position:absolute;left:20px;top:0;"
            "width:calc(100vw - 40px);height:608px;overflow-y:scroll;"
            "scrollbar-width:none;background:white}"
            ".pattern{position:absolute;top:0;left:0;width:100%;height:3856px;"
            "pointer-events:none}.before{height:2164px}"
            "aside{position:sticky;bottom:34px;height:108px;z-index:1}"
            ".after{height:1584px}svg{display:block}"
            '</style><section tabindex="0" aria-label="Owned motion fixture">'
            f'<div class="pattern">{cls._pattern(cls.TOTAL)}</div>'
            '<div class="before"></div>'
            f"<aside>{cls._pattern(cls.TILE_HEIGHT, inverse=True)}</aside>"
            '<div class="after"></div></section>'
        )

    @classmethod
    def plan(cls, scale: float) -> list[tuple[int, int]]:
        """Sample above the pinned overlay until the final native viewport."""
        total, viewport = round(cls.TOTAL * scale), round(cls.VIEWPORT * scale)
        safe = round(cls.PINNED_TOP * scale)
        result = []
        filled = 0
        while filled < total:
            offset = min(filled, total - viewport)
            count = min(safe, total - filled)
            if offset == total - viewport:
                count = total - filled
            result.append((offset, count))
            filled += count
        return result

    @classmethod
    def reference(cls, target: UiaScrollTarget, output: Path) -> QImage:
        """Build the oracle from native tiles chosen only by authored CSS geometry."""
        dpi = window_dpi(target)
        native._require(dpi in (96, 120, 192), "Unsupported reference DPI")
        scale = dpi / 96
        total, viewport = round(cls.TOTAL * scale), round(cls.VIEWPORT * scale)
        native._require(target.area.height == viewport, "Fixture viewport is clipped")
        width = round(target.area.width)
        result = QImage(width, total, QImage.Format.Format_RGB32)
        result.fill(0)
        painter = QPainter(result)
        receipt = []
        filled = 0
        try:
            for index, (offset, count) in enumerate(cls.plan(scale)):
                owned._settle(target, offset / (total - viewport) * 100)
                position = target.position()
                native._require(
                    math.isclose(
                        position.view_size,
                        viewport / total * 100,
                        abs_tol=cls.GEOMETRY_TOLERANCE,
                    )
                    and abs(position.vertical / 100 * (total - viewport) - offset)
                    < cls.GEOMETRY_TOLERANCE,
                    "Native geometry differs from authored geometry",
                )
                first = target.frame()
                native._require(first == target.frame(), "Reference pixels changed")
                native._require(
                    first.save(str(output / f"reference-{index}.png")), "Save failed"
                )
                source = filled - offset
                painter.drawImage(0, filled, first.copy(0, source, width, count))
                receipt.append(
                    {
                        "offset": offset,
                        "source": source,
                        "count": count,
                        "position": asdict(position),
                    }
                )
                filled += count
        finally:
            painter.end()
        (output / "reference-tiles.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
        native._require(result.save(str(output / "expected.png")), "Save failed")
        # Establish actual mixed motion before testing product recovery.
        owned._settle(target, 1672 / 3248 * 100)
        anchor = target.frame()
        owned._settle(target, 1710 / 3248 * 100)
        rejected = target.frame()
        anchor.save(str(output / "ambiguous-anchor.png"))
        rejected.save(str(output / "ambiguous-trial.png"))
        try:
            VerticalStitch(anchor).append(rejected, 38 * scale)
        except AmbiguousScrollError as error:
            native._require(
                error.probe_advance == round(26 * scale),
                "Missing rival-motion evidence",
            )
        else:
            raise RuntimeError("Native fixture did not reproduce competing motion")
        return result

    @classmethod
    def main(cls) -> None:
        """Run solely in the disposable hosted desktop with the ordinary helper."""
        require_hosted()
        native._require(sys.argv[1:] == ["edge-owned-motion"], "Unknown motion case")
        with (
            patch.object(native, "_document", cls.document),
            patch.object(owned, "reference", cls.reference),
        ):
            native.main(case=owned.case)


if __name__ == "__main__":
    MotionFixture.main()
