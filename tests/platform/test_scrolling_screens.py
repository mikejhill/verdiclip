"""Whether a scrolling panel is fully on the displays (portable)."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any, cast

import pytest

from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, on_screen, scrollbar_width

LAPTOP = {"left": 0, "top": 0, "width": 2560, "height": 1600}
LEFT = {"left": -2560, "top": 0, "width": 2560, "height": 1440}


def test_panel_on_one_display_is_on_screen() -> None:
    """A panel inside a display is captured."""
    assert on_screen(Rect(200, 100, 1000, 800), [LAPTOP])


def test_panel_across_two_displays_is_on_screen() -> None:
    """Displays side by side together show a panel that spans them."""
    assert on_screen(Rect(-500, 100, 1000, 800), [LEFT, LAPTOP])


def test_panel_where_a_display_used_to_be_is_off_screen() -> None:
    """After undocking, a window left on the old monitor is refused."""
    assert not on_screen(Rect(-2360, 100, 1000, 800), [LAPTOP])


def test_panel_partly_off_the_edge_is_off_screen() -> None:
    """Part of a panel past the edge would be black in every frame."""
    assert not on_screen(Rect(2000, 100, 1000, 800), [LAPTOP])


PANEL = Rect(534, 257, 658, 685)


def test_scrollbar_at_the_right_edge_is_left_out() -> None:
    """A tall, narrow bar against the right edge is trimmed (Windows Settings)."""
    assert scrollbar_width(PANEL, [Rect(1180, 257, 12, 685)]) == 12


def test_bars_that_are_not_a_vertical_scrollbar_inside_are_kept() -> None:
    """Horizontal, outside, short or wide bars leave the panel whole."""
    assert scrollbar_width(PANEL, []) == 0
    assert scrollbar_width(PANEL, [Rect(534, 930, 658, 12)]) == 0  # Horizontal
    assert scrollbar_width(PANEL, [Rect(1192, 257, 17, 685)]) == 0  # Outside
    assert scrollbar_width(PANEL, [Rect(1180, 257, 12, 300)]) == 0  # Short
    assert scrollbar_width(PANEL, [Rect(1100, 257, 92, 685)]) == 0  # Wide
    assert scrollbar_width(PANEL, [Rect(900, 257, 12, 685)]) == 0  # Not at the edge


class Node:
    """A fake UI Automation element: bounds, children, and whether it scrolls."""

    def __init__(self, bounds: Rect, *children: Node, scrolls: bool = False) -> None:
        """Remember the shape; ``_pointer`` is the node itself."""
        self._bounds, self.children, self.scrolls = bounds, list(children), scrolls
        self._pointer = self
        self.parent: Node | None = None
        for child in children:
            child.parent = self

    def bounds(self) -> Rect:
        """Bounds."""
        return self._bounds

    def nullable(self, slot: int, _owners: object, *_args: object) -> object:
        """GetCurrentPatternAs (14): a pattern only when the element scrolls."""
        assert slot == 14
        return Pattern() if self.scrolls else None


class Pattern:
    """A scroll pattern that is always vertically scrollable."""

    def flag(self, slot: int) -> bool:
        """VerticallyScrollable (10)."""
        return slot == 10


class Walker:
    """GetParent (3), GetFirstChild (4) and GetNextSibling (6) over nodes."""

    def nullable(
        self, slot: int, _owners: object, argument: tuple[object, Node]
    ) -> Node | None:
        """Walk."""
        node = argument[1]
        if slot == 3:
            return node.parent
        if slot == 4:
            return node.children[0] if node.children else None
        siblings = node.parent.children if node.parent is not None else [node]
        index = siblings.index(node) + 1
        return siblings[index] if index < len(siblings) else None


def find(document: Node, point: Point) -> object:
    """Run the lookup from ``document`` with the fakes standing in for COM."""
    element, walker = cast(Any, document), cast(Any, Walker())
    return target()._find(element, walker, point)


def target() -> UiaScrollTarget:
    """A target with only what the lookup needs (no COM, any platform)."""
    instance = object.__new__(UiaScrollTarget)
    instance._owners = ExitStack()
    return instance


def test_document_answer_is_searched_for_the_panel_under_the_point() -> None:
    """Chromium answering with the whole page still finds the scrolled panel."""
    inner = Node(Rect(100, 100, 200, 200), Node(Rect(110, 110, 50, 20)), scrolls=True)
    outer = Node(Rect(50, 50, 400, 400), Node(Rect(0, 0, 10, 10)), inner, scrolls=True)
    document = Node(Rect(0, 0, 800, 600), Node(Rect(500, 0, 100, 100)), outer)
    element, _pattern = cast("tuple[object, object]", find(document, Point(120, 115)))
    assert element is inner  # The innermost panel, not the outer one


def test_point_without_a_panel_is_still_refused() -> None:
    """Nothing scrollable under the point, above or below it, is an error."""
    document = Node(Rect(0, 0, 800, 600), Node(Rect(0, 0, 100, 100), scrolls=True))
    with pytest.raises(CaptureError, match="Nothing at that point scrolls"):
        find(document, Point(400, 400))
