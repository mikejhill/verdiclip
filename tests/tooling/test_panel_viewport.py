"""Native rectangle evidence and fractional-step regression checks."""

from __future__ import annotations

from dataclasses import replace

import pytest
from scripts.panel_uia import Structure
from scripts.panel_viewport import midpoint_percent, viewport_rect

TARGET = Structure(12, 0, 50033, "WPF", (72, 95, 582, 525), offscreen=False)
VERTICAL = replace(TARGET, control_type=50014, rect=(565, 95, 582, 508))
HORIZONTAL = replace(TARGET, control_type=50014, rect=(72, 508, 565, 525))


def test_actual_wpf_first_run_rectangles() -> None:
    """Both edge bars define the independently observed 493x413 content area."""
    assert viewport_rect(TARGET, [VERTICAL, HORIZONTAL]) == (72, 95, 565, 508)
    assert viewport_rect(TARGET, [HORIZONTAL, VERTICAL]) == (72, 95, 565, 508)


def test_fractional_half_viewport_does_not_request_half_pixel() -> None:
    """The first experiment requested206.5px, changing all text rasterization."""
    view = 413 / 1457 * 100
    percent = midpoint_percent(view, 413)
    assert percent / 100 * (1457 - 413) == pytest.approx(206)
    assert percent < 0.5 * view * 100 / (100 - view)


@pytest.mark.parametrize("height", [254, 413, 619, 826])
def test_step_guidance_is_integral_and_not_completion_evidence(height: int) -> None:
    """Physical viewport scales preserve integral guide distance, not proven motion."""
    view = 25.0
    percent = midpoint_percent(view, height)
    assert percent / 100 * height * (100 - view) / view == pytest.approx(height // 2)


@pytest.mark.parametrize(
    "bars",
    [
        [VERTICAL, VERTICAL, HORIZONTAL],
        [replace(VERTICAL, rect=(565, 95, 582, 507)), HORIZONTAL],
        [VERTICAL, replace(HORIZONTAL, rect=(72, 508, 564, 525))],
        [replace(VERTICAL, rect=(80, 95, 90, 525))],
    ],
)
def test_ambiguous_or_partial_strips_fail(bars: list[Structure]) -> None:
    """Never guess a viewport from unqualified scrollbar geometry."""
    with pytest.raises(ValueError, match=r"scrollbar|viewport"):
        viewport_rect(TARGET, bars)


@pytest.mark.parametrize(("view", "height"), [(0, 413), (100, 413), (25, 1)])
def test_invalid_step_guidance_fails(view: float, height: int) -> None:
    """No division by zero or zero step."""
    with pytest.raises(ValueError, match="Invalid viewport"):
        midpoint_percent(view, height)


def test_retained_wpf_percent_roundtrip_preserves_integral_request() -> None:
    """Avoid extra rounding through distance/height before computing percent."""
    view = 28.345916266300616
    percent = midpoint_percent(view, 413)
    assert percent * 1044 / 100 == 206.0
    assert percent / 100 * 1044 == 206.0
