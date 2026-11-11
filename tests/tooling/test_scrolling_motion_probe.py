"""Independent native motion-fixture tiles retain every authored document row."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from scripts import scrolling_motion_probe as probe
from scripts import scrolling_owned_layouts as owned
from tests.capture.test_scrolling_motion_probe import StickyTarget

from verdiclip.platform.scrolling import UiaScrollTarget


@pytest.mark.parametrize("scale", [1.0, 1.25, 2.0])
def test_motion_reference_tiles_cover_all_rows(scale: float) -> None:
    """The reference samples above pinned pixels and keeps every output row once."""
    fixture = probe.MotionFixture
    plan = fixture.plan(scale)
    filled = 0
    seen = []
    for offset, count in plan:
        source = filled - offset
        assert source >= 0, "A reference tile cannot precede its viewport"
        if offset < round((fixture.TOTAL - fixture.VIEWPORT) * scale):
            assert source + count <= round(fixture.PINNED_TOP * scale)
        seen.extend(range(offset + source, offset + source + count))
        filled += count
    assert seen == list(range(round(fixture.TOTAL * scale))), (
        "Every row must appear once"
    )


def test_motion_reference_matches_independently_authored_pixels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The native oracle's tile plan preserves the ordinary-flow golden image."""
    target = StickyTarget()
    monkeypatch.setattr(probe, "window_dpi", lambda _: 96)
    monkeypatch.setattr(owned, "_settle", lambda target, percent: target.move(percent))
    # This fake implements the capture boundary used by the native oracle.
    actual = probe.MotionFixture.reference(cast(UiaScrollTarget, target), tmp_path)
    assert actual == target.document, "The independent oracle must retain exact pixels"
    assert (tmp_path / "ambiguous-trial.png").exists(), (
        "Mixed-motion evidence must remain"
    )
