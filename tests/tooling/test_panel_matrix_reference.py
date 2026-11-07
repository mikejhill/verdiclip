"""Independent oracle planning covers endpoints and fractional edge pixels."""

from __future__ import annotations

from itertools import pairwise
from typing import cast

import mss
import pytest
from scripts import panel_matrix_reference as reference
from scripts.panel_matrix_reference import (
    assemble_reference_body,
    body_bounds,
    compare_body,
    reference_offsets,
)
from scripts.panel_probe_oracle import Pixels
from scripts.panel_uia_probe import NativeGuard


def test_reference_plan_uses_fixed_dom_tiles() -> None:
    """Known DOM dimensions choose tiles without capture offsets or seams."""
    offsets = reference_offsets(1000, 300, 1.25)
    assert offsets[0] == 0
    assert offsets[-1] == 700
    assert offsets[1] == 343 / 1.25
    assert all(b - a <= 300 - 32 / 1.25 for a, b in pairwise(offsets))


@pytest.mark.parametrize(
    "args",
    [
        (float("inf"), 300, 1),
        (1000, 0, 1),
        (1000, 300, 0),
        (1000, 300, 3),
        (9000, 300, 1),
        (8000, 40, 1),
    ],
)
def test_unbounded_or_invalid_reference_rejected(
    args: tuple[float, float, float],
) -> None:
    """No unbounded/blank/invalid reference sequence is generated."""
    with pytest.raises(ValueError, match=r"Nonfinite|Unsupported|budget"):
        reference_offsets(*args)


def test_fractional_edge_pixels_included() -> None:
    """Floor/ceil includes partially covered pixels rather than hiding them."""
    rect = body_bounds([124.5, 162.5, 518.25, 468.75], [122, 160, 539, 489])
    assert rect == (2, 2, 397, 309)


def test_outside_reference_fails() -> None:
    """A body extending outside the retained outer frame cannot be certified."""
    with pytest.raises(ValueError, match="outside"):
        body_bounds([0, 0, 20, 20], [1, 1, 19, 19])


def test_full_edge_mosaic_and_exact_pixel_difference() -> None:
    """A one-pixel fractional-edge change cannot disappear in a crop/tolerance."""
    rgb = b"".join(bytes((row, 20, 30)) * 4 for row in range(10))
    whole = Pixels(4, 10, rgb)
    mosaic = assemble_reference_body(
        [(0, Pixels(4, 6, rgb[:72])), (4, Pixels(4, 6, rgb[48:]))], 10
    )
    assert mosaic == whole
    changed = Pixels(4, 10, b"bad" + rgb[3:])
    result = compare_body(changed, mosaic)
    assert result["mismatched_pixels"] == 1
    assert result["exact"] is False
    assert result["fractional_edge_columns_included"] is True


def test_missing_and_blank_reference_fail() -> None:
    """Neither uncovered content nor a blank renderer is reference evidence."""
    tile = Pixels(4, 6, bytes((20, 30, 40)) * 24)
    with pytest.raises(ValueError, match="coverage"):
        assemble_reference_body([(0, tile)], 10)
    with pytest.raises(ValueError, match="blank"):
        assemble_reference_body([(0, tile)], 6)


def test_reference_cleanup_keeps_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup is watched without an ownership guard, and cannot mask capture."""
    events = []

    class Source:
        def close(self) -> None:
            raise OSError("cleanup failure")

    monkeypatch.setattr(reference.mss, "MSS", Source)
    with (
        pytest.raises(RuntimeError, match="original capture failure"),
        reference.logged_source(
            lambda event, operation: events.append((event, operation))
        ),
    ):
        raise RuntimeError("original capture failure")
    assert ("call_start", "reference.source.close") in events
    assert ("call_end", "reference.source.close") in events
    assert any(e[0] == "cleanup_error" for e in events)


def test_monitor_enumeration_is_guarded_and_watched() -> None:
    """Native monitor discovery is inside common watchdog boundaries."""
    calls = []

    class Source:
        @property
        def monitors(self) -> list[dict[str, int]]:
            calls.append("monitors")
            return [{}, {"left": 0, "top": 0, "width": 100, "height": 100}]

    class Guard:
        def check(self) -> None:
            calls.append("guard")

    reference.check_monitor(
        cast("mss.MSS", Source()),
        cast("NativeGuard", Guard()),
        [1, 1, 20, 20],
        lambda event, operation: calls.append((event, operation)),
    )
    assert calls == [
        ("call_start", "reference.monitors"),
        "guard",
        "monitors",
        "guard",
        ("call_end", "reference.monitors"),
    ]
