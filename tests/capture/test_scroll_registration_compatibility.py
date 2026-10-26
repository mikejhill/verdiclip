"""Synthetic rendering compatibility proves exact joins and honest abstention."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.reviewed import CaptureCompleteness
from verdiclip.capture.scroll.matching import MatchStatus, OverlapMatcher
from verdiclip.capture.scroll.models import ScrollTarget, StopReason
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.geometry import Rect


class RenderingFixtures:
    """Model rendering phases explicitly, without claiming a native app trace."""

    @staticmethod
    def half_row_phase(source: QImage, offset: int, rows: int) -> QImage:
        """Average neighboring rows to model a changed half-pixel sampling phase."""
        first = source.copy(0, offset, source.width(), rows)
        second = source.copy(0, offset + 1, source.width(), rows)
        samples = bytearray(
            (left + right) // 2
            for left, right in zip(
                bytes(first.constBits()), bytes(second.constBits()), strict=True
            )
        )
        return QImage(
            samples, first.width(), rows, first.bytesPerLine(), first.format()
        ).copy()

    @staticmethod
    def sticky_header(source: QImage, offset: int, rows: int) -> QImage:
        """Keep twelve viewport rows fixed while the document beneath moves."""
        frame = source.copy(0, offset, source.width(), rows)
        painter = QPainter(frame)
        painter.drawImage(0, 0, source.copy(0, 0, source.width(), 12))
        painter.end()
        return frame


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 1.75, 2.0])
def test_stable_physical_sampling_stitches_exactly_at_common_display_scales(
    tmp_path: Path, scale: float
) -> None:
    """DPI alone is supported when settled frames translate by whole pixel rows."""
    width, rows, shift = round(96 * scale), round(120 * scale), round(60 * scale)
    source = Documents.image().scaled(
        width,
        round(480 * scale),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    session = ScrollSession(tmp_path)
    try:
        session.start(
            ScrollTarget("fixture", "Scaled document", Rect(0, 0, width, rows)),
            source.copy(0, 0, width, rows),
            now=0,
            taken_at=datetime(2026, 10, 26, tzinfo=UTC),
        )
        for sequence in (1, 2):
            status = session.submit(
                Documents.frame(
                    session, source.copy(0, sequence * shift, width, rows), sequence
                )
            )
            assert status is MatchStatus.VERIFIED
        session.stop()
        result = session.accept()
        assert result.image == source.copy(0, 0, width, rows + 2 * shift)
        assert result.completeness is CaptureCompleteness.PARTIAL
        assert result.stop_reason == StopReason.USER_STOP.value
    finally:
        session.close()


@pytest.mark.parametrize("change", ["fractional_phase", "sticky_header", "animation"])
def test_unverified_rendering_change_cannot_claim_complete_or_append_rows(
    tmp_path: Path, change: str
) -> None:
    """Even reported end cannot certify a seam whose pixels have changed."""
    session, source = Documents.start(tmp_path)
    if change == "fractional_phase":
        candidate = RenderingFixtures.half_row_phase(source, 60, 120)
    elif change == "sticky_header":
        candidate = RenderingFixtures.sticky_header(source, 60, 120)
    else:
        candidate = source.copy(0, 60, 96, 120)
        painter = QPainter(candidate)
        painter.fillRect(candidate.rect(), Qt.GlobalColor.magenta)
        painter.end()
    try:
        status = session.submit(Documents.frame(session, candidate, 1, at_end=True))
        assert status is MatchStatus.UNMATCHED
        assert session.state is ScrollState.PAUSED
        session.stop()
        result = session.accept()
        assert result.image == source.copy(0, 0, 96, 120)
        assert result.completeness is CaptureCompleteness.PARTIAL
        assert result.stop_reason == StopReason.UNCERTAIN.value
    finally:
        session.close()


def test_explicit_content_crop_excludes_fixed_header_without_seam_guessing() -> None:
    """A user-selected content rectangle can remove unsupported fixed chrome."""
    source = Documents.image()
    previous = RenderingFixtures.sticky_header(source, 0, 120)
    current = RenderingFixtures.sticky_header(source, 60, 120)
    result = OverlapMatcher().compare(
        previous.copy(0, 12, 96, 108), current.copy(0, 12, 96, 108)
    )
    assert result.status is MatchStatus.VERIFIED
    assert result.shift == 60
