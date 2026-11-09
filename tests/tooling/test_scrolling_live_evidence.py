"""Diagnostic evidence never approves a refused capture or restores uncertain writes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from scripts import scrolling_live_evidence as evidence
from scripts import scrolling_live_sites as live

from verdiclip.capture.scrolling import ScrollPosition
from verdiclip.geometry import Rect
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo

_WINDOW = WindowInfo(1234, "Google Search", Rect(0, 0, 800, 600))
_START = ScrollPosition(-1, 50, 25)
_GEOMETRY: dict[str, object] = {
    "document": {
        "runtime_id": [42, 1],
        "parent_runtime_id": [42],
        "role": 50030,
        "bounds": [0, 0, 800, 600],
        "pid": 12,
        "hwnd": 1234,
    },
    "nodes": [],
}


class Target:
    """Model a guarded provider and its ordinary full viewport screenshots."""

    def __init__(self) -> None:
        self.current = _START
        self.area = _WINDOW.bounds
        self.moves: list[float] = []
        self.fail_move = False

    def check(self) -> None:
        """Keep the modeled window and input guards unchanged."""

    def position(self) -> ScrollPosition:
        """Read the model without writing provider state."""
        return self.current

    def move(self, percent: float) -> None:
        """Record writes including a possible uncertain completion."""
        self.moves.append(percent)
        self.current = replace(self.current, vertical=percent)
        if self.fail_move:
            raise RuntimeError("Uncertain setter")

    def frame(self) -> QImage:
        """Return a stable synthetic image, not live-site evidence."""
        image = QImage(3, 3, QImage.Format.Format_RGB32)
        image.fill(0xFF123456)
        return image


@pytest.fixture
def observations(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Portable tests model metadata only; they claim no native site acceptance."""
    geometry = Mock(return_value=_GEOMETRY)
    monkeypatch.setattr(evidence, "_geometry", geometry)
    monkeypatch.setattr(evidence.time, "sleep", lambda _seconds: None)
    return geometry


def test_four_paired_frames_and_restore(observations: Mock, tmp_path: Path) -> None:
    """Collect all samples despite a separate product failure and keep verdict false."""
    target = Target()
    result = evidence.collect(
        cast("UiaScrollTarget", target), _WINDOW, tmp_path, live._observation_script
    )
    assert result["success"] is True
    assert result["product_qualification"] is False
    assert result["restoration"] == "restored"
    assert target.current == _START
    assert len(list(tmp_path.glob("f*-*.png"))) == 8
    assert observations.call_count == 10
    assert target.moves == [*evidence.positions(_START.view_size), _START.vertical]


def test_uncertain_setter_is_not_followed_by_restore(
    observations: Mock, tmp_path: Path
) -> None:
    """A timed-out/failed provider write never authorizes a recovery write."""
    target = Target()
    target.fail_move = True
    result = evidence.collect(
        cast("UiaScrollTarget", target), _WINDOW, tmp_path, live._observation_script
    )
    assert result["success"] is False
    assert result["restoration"] == "unproven"
    assert target.moves == [0.0]
    assert observations.call_count == 1


def test_changed_document_prevents_restoration(
    observations: Mock, tmp_path: Path
) -> None:
    """A replacement document with the same title is not the original target."""
    changed = {
        **_GEOMETRY,
        "document": {
            **cast("dict[str, object]", _GEOMETRY["document"]),
            "runtime_id": [99],
        },
    }
    observations.side_effect = [_GEOMETRY, changed, changed]
    target = Target()
    result = evidence.collect(
        cast("UiaScrollTarget", target), _WINDOW, tmp_path, live._observation_script
    )
    assert result["success"] is False
    assert result["restoration"] == "unproven"
    assert target.moves == [0.0]


def test_native_raw_role_script_is_bounded_and_read_only() -> None:
    """Record observed roles/ranges rather than inferred chrome rectangles."""
    script = evidence.geometry_script(live._observation_script(_WINDOW))
    for marker in (
        "::FromPoint($point)",
        "::RawViewWalker",
        "::Compare($node, $root)",
        "$nodes.Count -lt 1200",
        "$depth -ge 48",
        "GetRuntimeId()",
        "parent_runtime_id",
        "BoundingRectangle",
        "Orientation",
        "RangeValuePattern",
    ):
        assert marker in script
    assert "SetValue" not in script
    assert "SetScrollPercent" not in script
    assert "javascript" not in script


@pytest.mark.parametrize("view", [0, 100, 80])
def test_unusable_extent_is_not_a_four_frame_pass(view: float) -> None:
    """A short page cannot fabricate the requested distinct F0-F3 observations."""
    with pytest.raises(RuntimeError):
        evidence.positions(view)
