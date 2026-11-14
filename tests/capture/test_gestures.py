"""Test the modifier-to-action table of universal capture."""

from __future__ import annotations

import pytest

from verdiclip.capture.gestures import (
    ClickAction,
    ClickGestures,
    ClickOutput,
    ClickTarget,
    combo_label,
    combo_name,
)


def test_defaults_keep_the_scrolling_modifiers_and_add_delivery_ones() -> None:
    """Ctrl and Ctrl+Alt scroll as before; Alt copies; Shift saves."""
    gestures = ClickGestures()
    assert gestures.resolve(ctrl=False, alt=False, shift=False) == ClickAction()
    assert gestures.resolve(ctrl=True, alt=False, shift=False) == ClickAction(
        ClickTarget.SCROLL_HERE
    )
    assert gestures.resolve(ctrl=True, alt=True, shift=False) == ClickAction(
        ClickTarget.SCROLL_ALL
    )
    assert gestures.resolve(ctrl=False, alt=True, shift=False) == ClickAction(
        output=ClickOutput.CLIPBOARD
    )
    assert gestures.resolve(ctrl=False, alt=False, shift=True) == ClickAction(
        output=ClickOutput.SAVE
    )
    assert gestures.resolve(ctrl=False, alt=True, shift=True) == ClickAction(
        output=ClickOutput.CLIPBOARD_AND_SAVE
    )
    assert gestures.resolve(ctrl=True, alt=False, shift=True) == ClickAction(
        ClickTarget.SCROLL_HERE, ClickOutput.SAVE
    )
    assert gestures.resolve(ctrl=True, alt=True, shift=True) == ClickAction(
        ClickTarget.SCROLL_ALL, ClickOutput.CLIPBOARD
    )


def test_every_combination_has_a_field() -> None:
    """The seven non-empty combinations map onto the seven fields."""
    names = {
        combo_name(ctrl=c, alt=a, shift=s)
        for c in (False, True)
        for a in (False, True)
        for s in (False, True)
    } - {""}
    assert names == set(ClickGestures.combos())
    assert combo_label("ctrl_alt_shift") == "Ctrl+Alt+Shift"


def test_action_rejects_unknown_names() -> None:
    """Only combination fields name actions."""
    with pytest.raises(KeyError):
        ClickGestures().action("meta")


@pytest.mark.parametrize(
    ("output", "copies", "saves", "edits"),
    [
        (ClickOutput.USUAL, False, False, False),
        (ClickOutput.CLIPBOARD, True, False, False),
        (ClickOutput.SAVE, False, True, False),
        (ClickOutput.CLIPBOARD_AND_SAVE, True, True, False),
        (ClickOutput.EDITOR, False, False, True),
    ],
)
def test_outputs_say_what_they_do(
    output: ClickOutput,
    copies: bool,  # noqa: FBT001 - parametrized
    saves: bool,  # noqa: FBT001 - parametrized
    edits: bool,  # noqa: FBT001 - parametrized
) -> None:
    """Each output turns on exactly its deliveries."""
    assert (output.copies, output.saves, output.opens_editor) == (copies, saves, edits)


def test_describe_names_only_what_differs_from_a_plain_click() -> None:
    """The overlay hint leaves out the defaults, and a drag has no target."""
    assert ClickAction().describe() == ""
    action = ClickAction(ClickTarget.SCROLL_ALL, ClickOutput.SAVE)
    assert action.describe() == "Scrolling, whole pane · Save only"
    assert action.describe(dragging=True) == "Save only"
    assert not action.is_plain
    assert ClickTarget.WINDOW.scrolls is False
