"""Fractional synthetic-fixture crops preserve pixel containment and row phase."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import panel_feasibility
from scripts.panel_feasibility import oracle, oracle_crop_geometry
from scripts.panel_probe_oracle import Pixels, row_color


@pytest.mark.parametrize(
    ("content", "target", "expected", "phase"),
    [
        ([124.5, 162.5, 518.25, 468.75], [122, 160, 539, 489], [3, 3, 396, 308], 0.5),
        ([101.0, 131.0, 416.0, 376.0], [99, 129, 433, 393], [2, 2, 317, 247], 0.0),
        ([-10.5, -20.25, 20.75, 30.5], [-12, -22, 22, 32], [2, 2, 32, 52], 0.25),
        ([1.25, 2.75, 19.75, 39.25], [0, 0, 20, 40], [2, 3, 19, 39], 0.25),
    ],
)
def test_only_fully_contained_pixels(
    content: list[float],
    target: list[int],
    expected: list[int],
    phase: float,
) -> None:
    """Ceil/floor applies in physical space, including negative monitor origins."""
    crop, actual_phase = oracle_crop_geometry(content, target)
    assert crop == expected
    assert actual_phase == phase
    assert crop[0] + target[0] >= content[0]
    assert crop[1] + target[1] >= content[1]
    assert crop[2] + target[0] <= content[2]
    assert crop[3] + target[1] <= content[3]


@pytest.mark.parametrize(
    "content", [[0.1, 0.1, 0.9, 30], [0, 0.1, 30, 0.9], [3, 2, 1, 4]]
)
def test_empty_contained_pixel_rectangle_fails(content: list[float]) -> None:
    """A tiny or inverted fixture rectangle is not expanded to one pixel."""
    with pytest.raises(ValueError, match="no complete pixel"):
        oracle_crop_geometry(content, [0, 0, 40, 40])


@pytest.mark.parametrize("alteration", ["none", "outside_crop", "interior"])
def test_row_phase_and_full_frame_restore_remain_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alteration: str,
) -> None:
    """Crop phase corrects row expectations without hiding raw restoration drift."""
    output = tmp_path / "probe"
    output.mkdir()
    (output / "manifest.json").write_text(
        json.dumps({"target": {"rect": [0, 0, 20, 50]}, "first_failure": None}),
        encoding="utf-8",
    )
    content_top = 1.25
    scale = 1.25
    offset = 2.0
    # Pixels represent their physical centers in the independently known content.
    rgb = b"".join(
        row_color(int(((y + 0.5 - content_top) / scale + offset) // 31)) * 20
        for y in range(50)
    )
    changed = bytearray(rgb)
    if alteration == "outside_crop":
        changed[0] ^= 1
    elif alteration == "interior":
        # Crop begins at (2, 2); the row oracle samples crop x=16-12=4.
        changed[(7 * 20 + 6) * 3] ^= 1
    frames = {"before": Pixels(20, 50, rgb), "restore": Pixels(20, 50, bytes(changed))}
    monkeypatch.setattr(
        panel_feasibility, "load_pixels", lambda path: frames[path.stem]
    )
    monkeypatch.setattr(
        panel_feasibility,
        "events",
        lambda _: [
            {"event": "stable_frame", "name": name, "state": {"vertical": 10.0}}
            for name in frames
        ],
    )
    state = {
        "target": {
            "vertical": offset,
            "horizontal": 0,
            "extent_height": 120,
            "viewport_height": 100,
        },
        "sibling": {},
        "outer": None,
    }
    fixture = {
        "content_rect": [1.25, content_top, 18.75, 47.75],
        "dpr": scale,
        "state": state,
    }
    report = oracle(fixture, [{"state": state}], tmp_path)
    assert report["content_oracle_relative_crop"] == [2, 2, 18, 47]
    assert report["content_oracle_vertical_phase_pixels"] == 0.75
    expected_checked = sum(
        3 <= ((y + 0.5 - content_top) / scale + offset) % 31 <= 28 for y in range(2, 47)
    )
    assert report["frame_checks"]["before"]["checked_scanlines"] == expected_checked
    assert report["frame_checks"]["before"]["matches"] is True
    assert report["frame_checks"]["restore"]["matches"] is (alteration != "interior")
    assert report["original_pixels_restored"] is (alteration == "none")
    assert report["product_viewport_qualified"] is False
