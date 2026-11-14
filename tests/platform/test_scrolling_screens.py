"""Whether a scrolling panel is fully on the displays (portable)."""

from __future__ import annotations

from verdiclip.geometry import Rect
from verdiclip.platform.scrolling import on_screen, scrollbar_width

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
