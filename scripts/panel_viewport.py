"""Conservative probe viewport geometry and pixel-aligned step guidance."""

from __future__ import annotations

from collections.abc import Sequence

from scripts.panel_uia import Structure

SCROLLBAR_TYPE = 50014
FULL_PERCENT = 100
MIN_HEIGHT = 2


def viewport_rect(
    target: Structure, children: Sequence[Structure]
) -> tuple[int, int, int, int]:
    """Remove only identified contiguous right/bottom scrollbar edge strips."""
    left, top, right, bottom = target.rect
    bars = [
        child.rect
        for child in children
        if child.control_type == SCROLLBAR_TYPE and not child.offscreen
    ]
    vertical = [
        r for r in bars if r[2] == right and r[1] == top and left < r[0] < right
    ]
    horizontal = [
        r for r in bars if r[3] == bottom and r[0] == left and top < r[1] < bottom
    ]
    if len(vertical) > 1 or len(horizontal) > 1:
        raise ValueError("Ambiguous scrollbar strips")
    inner_right = vertical[0][0] if vertical else right
    inner_bottom = horizontal[0][1] if horizontal else bottom
    if vertical and vertical[0][3] != inner_bottom:
        raise ValueError("Vertical scrollbar is not a contiguous viewport edge")
    if horizontal and horizontal[0][2] != inner_right:
        raise ValueError("Horizontal scrollbar is not a contiguous viewport edge")
    if len(bars) != len(vertical) + len(horizontal):
        raise ValueError("Unsupported scrollbar placement")
    return left, top, inner_right, inner_bottom


def midpoint_percent(view_percent: float, physical_height: int) -> float:
    """Guide the request to whole physical pixels; actual overlap still decides."""
    if not 0 < view_percent < FULL_PERCENT or physical_height < MIN_HEIGHT:
        raise ValueError("Invalid viewport for step guidance")
    distance = physical_height // 2
    return min(
        50.0, distance / physical_height * view_percent * 100 / (100 - view_percent)
    )
