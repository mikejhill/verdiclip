"""Exact owned-fixture paint support, never acquisition or origin correction."""

from __future__ import annotations

from dataclasses import dataclass

from scripts.panel_probe_oracle import Pixels

CONTROL_REVISION = "fixed-two-pixel-strips-v2"
BOUNDARY_PIXELS = 2
MIN_SELECTED_SIZE = 4
RECT_FIELDS = 4
MIN_COORD = -(2**31)
MAX_COORD = 2**31 - 1
MAX_PIXELS = 2_000_000
Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class PaintSupport:
    """Observed changes only; equal-to-background pixels are not proved."""

    changed_pixels: int
    bounds: Rect
    edge_counts: tuple[int, int, int, int]
    corner_observed: tuple[bool, bool, bool, bool]
    inset_rejected: tuple[bool, bool, bool, bool]
    revision: str = CONTROL_REVISION
    boundary_pixels: int = BOUNDARY_PIXELS


def rectangle(value: Rect) -> None:
    """Require signed integral physical half-open coordinates."""
    if (
        len(value) != RECT_FIELDS
        or any(type(x) is not int or not MIN_COORD <= x <= MAX_COORD for x in value)
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError("Invalid physical rectangle")


def paint_support(  # noqa: C901, PLR0912 - single bounded pixel pass
    visible: Pixels, transparent: Pixels, restored: Pixels, root: Rect, selected: Rect
) -> PaintSupport:
    """Exact full-R containment with predeclared two-pixel observability controls.

    Require support in each two-pixel strip and 2x2 corner. Independently test
    containment in four rectangles with one edge inset by two pixels. This
    detects those cuts, NOT every one-pixel geometric reduction. R is never
    chosen, shrunk or expanded from observed support; invisible pixels remain
    unproved. No color threshold, fitted origin, adaptive band or retry.
    """
    rectangle(root)
    rectangle(selected)
    if not (
        root[0] <= selected[0] < selected[2] <= root[2]
        and root[1] <= selected[1] < selected[3] <= root[3]
    ):
        raise ValueError("Selected rectangle outside root")
    if min(selected[2] - selected[0], selected[3] - selected[1]) < MIN_SELECTED_SIZE:
        raise ValueError("Selected rectangle is smaller than fixed control geometry")
    shape = root[2] - root[0], root[3] - root[1]
    if shape[0] * shape[1] > MAX_PIXELS:
        raise ValueError("Paint pixel budget exceeded")
    if any((x.width, x.height) != shape for x in (visible, transparent, restored)):
        raise ValueError("Paint raster geometry changed")
    if visible.rgb != restored.rgb:
        raise ValueError("Restored full-root pixels differ")
    count = 0
    edges = [0, 0, 0, 0]
    corners = [False] * 4
    left, top, right, bottom = selected
    bounds = [root[2], root[3], root[0], root[1]]
    inset_rects = (
        (left + BOUNDARY_PIXELS, top, right, bottom),
        (left, top + BOUNDARY_PIXELS, right, bottom),
        (left, top, right - BOUNDARY_PIXELS, bottom),
        (left, top, right, bottom - BOUNDARY_PIXELS),
    )
    inset_rejected = [False] * 4
    corner_rects = (
        (left, top, left + BOUNDARY_PIXELS, top + BOUNDARY_PIXELS),
        (right - BOUNDARY_PIXELS, top, right, top + BOUNDARY_PIXELS),
        (left, bottom - BOUNDARY_PIXELS, left + BOUNDARY_PIXELS, bottom),
        (right - BOUNDARY_PIXELS, bottom - BOUNDARY_PIXELS, right, bottom),
    )
    for index in range(shape[0] * shape[1]):
        start = index * 3
        if visible.rgb[start : start + 3] == transparent.rgb[start : start + 3]:
            continue
        x, y = root[0] + index % shape[0], root[1] + index // shape[0]
        if not left <= x < right or not top <= y < bottom:
            raise ValueError("Observed panel paint outside selected rectangle")
        count += 1
        bounds = [
            min(bounds[0], x),
            min(bounds[1], y),
            max(bounds[2], x + 1),
            max(bounds[3], y + 1),
        ]
        for i, hit in enumerate(
            (
                x < left + BOUNDARY_PIXELS,
                y < top + BOUNDARY_PIXELS,
                x >= right - BOUNDARY_PIXELS,
                y >= bottom - BOUNDARY_PIXELS,
            )
        ):
            edges[i] += int(hit)
        for i, (a, b, c, d) in enumerate(corner_rects):
            corners[i] |= a <= x < c and b <= y < d
        # A separate containment check, not a copy of the edge-count result.
        for i, (a, b, c, d) in enumerate(inset_rects):
            inset_rejected[i] |= not (a <= x < c and b <= y < d)
    if not count:
        raise ValueError("Empty paint support")
    if not all(edges):
        raise ValueError("An edge is unobservable; inset negative control not proved")
    if not all(inset_rejected):
        raise ValueError("Two-pixel inset containment negative control not proved")
    if not all(corners):
        raise ValueError("Authored corner control is unobservable")
    return PaintSupport(
        count,
        (bounds[0], bounds[1], bounds[2], bounds[3]),
        (edges[0], edges[1], edges[2], edges[3]),
        (corners[0], corners[1], corners[2], corners[3]),
        (inset_rejected[0], inset_rejected[1], inset_rejected[2], inset_rejected[3]),
    )
