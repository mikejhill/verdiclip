"""Independent full-width references reject label and seam corruption."""

from copy import deepcopy
from typing import Any

import pytest
from scripts.panel_complete_reference import (
    ReferenceFrame,
    associate_frames,
    compare_reference,
)
from scripts.panel_probe_oracle import ROW_COUNT, ROW_HEIGHT, Pixels

WIDTH = 6
HEIGHT = 124
TOTAL = ROW_COUNT * ROW_HEIGHT


def canvas() -> Pixels:
    """Unique pixels identify both a row and its label-like horizontal position."""
    return Pixels(
        WIDTH,
        TOTAL,
        b"".join(
            bytes((y % 256, y // 256, x * 31))
            for y in range(TOTAL)
            for x in range(WIDTH)
        ),
    )


def frames() -> list[ReferenceFrame]:
    """Independent known offsets cover the whole authored document."""
    source = canvas()
    offsets = [*range(0, TOTAL - HEIGHT, 62), TOTAL - HEIGHT]
    return [
        ReferenceFrame(str(y), y, source.crop((0, y, WIDTH, y + HEIGHT)))
        for y in offsets
    ]


def test_complete_reference() -> None:
    """Every full-width pixel and all complete row bands are checked."""
    result = compare_reference(frames(), canvas())
    assert result["passed"]
    assert result["all_pixels_checked"] == WIDTH * TOTAL
    assert len(result["bands"]) == ROW_COUNT


@pytest.mark.parametrize("mutation", ["pixel", "label", "duplicate", "seam"])
def test_composite_corruption(mutation: str) -> None:
    """Reject label edits, substitutions, duplicate/delete pairs and seam damage."""
    original = canvas()
    rgb = bytearray(original.rgb)
    stride = WIDTH * 3
    if mutation == "pixel":
        rgb[40 * stride + 4] ^= 1
    elif mutation == "label":
        rgb[35 * stride : 40 * stride] = rgb[66 * stride : 71 * stride]
    elif mutation == "duplicate":
        rgb[62 * stride : 64 * stride] = rgb[61 * stride : 63 * stride]
    else:
        rgb[93 * stride - 1] ^= 1
        rgb[93 * stride] ^= 1
    result = compare_reference(frames(), Pixels(WIDTH, TOTAL, bytes(rgb)))
    assert not result["passed"]


def test_conflicting_raw_witness() -> None:
    """An agreeing output cannot resolve contradictory interior raw observations."""
    observations = frames()
    first = observations[0]
    rgb = bytearray(first.pixels.rgb)
    rgb[90 * WIDTH * 3] ^= 1
    observations.append(
        ReferenceFrame("conflict", 0, Pixels(WIDTH, HEIGHT, bytes(rgb)))
    )
    assert not compare_reference(observations, canvas())["passed"]


def test_missing_coverage() -> None:
    """No padding or partial-row reconstruction fills absent reference coverage."""
    with pytest.raises(ValueError, match="coverage"):
        compare_reference(frames()[1:], canvas())


def inputs() -> tuple[
    dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Pixels]
]:
    """Build fixture snapshots independently of any stitcher or alignment output."""
    fixture = {
        "rect": [0, 0, WIDTH, HEIGHT],
        "content_rect": [0, 0, WIDTH, HEIGHT],
        "dpi_x": 96,
        "dpi_y": 96,
    }
    states, events, images = [], [], {}
    for frame in frames():
        state = {
            **fixture,
            "state": {
                "target": {
                    "vertical": frame.offset,
                    "horizontal": 0,
                    "extent_height": TOTAL,
                    "viewport_height": HEIGHT,
                }
            },
        }
        states.append(state)
        events.append(
            {
                "event": "stable_frame",
                "name": frame.name,
                "state": {"vertical": frame.offset * 100 / (TOTAL - HEIGHT)},
            }
        )
        images[frame.name] = frame.pixels
    return fixture, states, events, images


def test_fixture_state_association() -> None:
    """Matched fixture states supply offsets; identical snapshots are safe."""
    fixture, states, events, images = inputs()
    states.append(deepcopy(states[0]))
    reference = associate_frames(fixture, states, events, images)
    assert compare_reference(reference, canvas())["passed"]


@pytest.mark.parametrize(
    "problem", ["ambiguous", "missing", "image", "duplicate", "dpi"]
)
def test_association_rejects_bad_evidence(problem: str) -> None:
    """Do not silently choose conflicting states or incomplete frame evidence."""
    fixture, states, events, images = inputs()
    if problem == "ambiguous":
        duplicate = deepcopy(states[0])
        duplicate["state"]["target"]["horizontal"] = 1
        states.append(duplicate)
    elif problem == "missing":
        states.pop(0)
    elif problem == "image":
        images.pop(events[0]["name"])
    elif problem == "duplicate":
        events.append(events[0])
    else:
        fixture["dpi_x"] = 120
    with pytest.raises(ValueError, match=r"association|raw frame|96 DPI"):
        associate_frames(fixture, states, events, images)


@pytest.mark.parametrize(
    "problem", ["geometry", "extent", "fractional", "percent", "empty"]
)
def test_invalid_reference_metadata(problem: str) -> None:
    """Geometry, offset and metric failures are never repaired by pixel matching."""
    fixture, states, events, images = inputs()
    if problem == "geometry":
        states[0]["rect"] = [1, 0, WIDTH + 1, HEIGHT]
    elif problem == "extent":
        states[0]["state"]["target"]["extent_height"] += 1
    elif problem == "fractional":
        fixture["content_rect"] = [0.5, 0, WIDTH, HEIGHT]
    elif problem == "percent":
        events[0]["state"]["vertical"] = float("nan")
    else:
        events.clear()
    with pytest.raises(
        ValueError, match=r"geometry|extent|integral|percentage|No stable"
    ):
        associate_frames(fixture, states, events, images)


def test_wrong_composite_size() -> None:
    """A missing output line is rejected before band comparisons."""
    image = canvas().crop((0, 0, WIDTH, TOTAL - 1))
    with pytest.raises(ValueError, match="extent"):
        compare_reference(frames(), image)
