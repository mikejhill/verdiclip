"""Exact owned-fixture paint support, never acquisition or origin correction."""

from __future__ import annotations

from dataclasses import dataclass

from scripts.panel_probe_oracle import Pixels

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


def rectangle(value: Rect) -> None:
    """Require signed integral physical half-open coordinates."""
    if (
        len(value) != RECT_FIELDS
        or any(type(x) is not int or not MIN_COORD <= x <= MAX_COORD for x in value)
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError("Invalid physical rectangle")


def paint_support(  # noqa: C901 - single bounded pixel pass
    visible: Pixels, transparent: Pixels, restored: Pixels, root: Rect, selected: Rect
) -> PaintSupport:
    """Reject any observed paint outside fixed R; require every edge observable.

    Four one-pixel inset negative controls are equivalent to requiring nonempty
    support on each of R's four boundary scanlines. No mask, color tolerance,
    fitted origin, discarded antialiasing or inferred invisible pixels.
    """
    rectangle(root)
    rectangle(selected)
    if not (
        root[0] <= selected[0] < selected[2] <= root[2]
        and root[1] <= selected[1] < selected[3] <= root[3]
    ):
        raise ValueError("Selected rectangle outside root")
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
        for i, hit in enumerate((x == left, y == top, x == right - 1, y == bottom - 1)):
            edges[i] += int(hit)
        for i, point in enumerate(
            ((left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1))
        ):
            corners[i] |= (x, y) == point
    if not count:
        raise ValueError("Empty paint support")
    if not all(edges):
        raise ValueError("An edge is unobservable; inset negative control not proved")
    if not all(corners):
        raise ValueError("Authored corner control is unobservable")
    return PaintSupport(
        count,
        (bounds[0], bounds[1], bounds[2], bounds[3]),
        (edges[0], edges[1], edges[2], edges[3]),
        (corners[0], corners[1], corners[2], corners[3]),
    )
