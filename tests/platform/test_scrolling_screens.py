"""Whether a scrolling panel is fully on the displays (portable)."""

from __future__ import annotations

from verdiclip.geometry import Rect
from verdiclip.platform.scrolling import on_screen

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
