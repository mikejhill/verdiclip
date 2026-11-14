"""What each modifier does when universal capture finishes a drag or a click."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Final


class ClickTarget(StrEnum):
    """What a click (not a drag) captures."""

    WINDOW = "window"
    SCROLL_HERE = "scroll_here"
    SCROLL_ALL = "scroll_all"

    @property
    def label(self) -> str:
        """Return the name shown in Settings and in the overlay."""
        return _TARGET_LABELS[self]

    @property
    def scrolls(self) -> bool:
        """True for the scrolling targets."""
        return self is not ClickTarget.WINDOW


class ClickOutput(StrEnum):
    """Where the image goes; USUAL follows the after-capture settings."""

    USUAL = "usual"
    CLIPBOARD = "clipboard"
    SAVE = "save"
    CLIPBOARD_AND_SAVE = "clipboard_save"
    EDITOR = "editor"

    @property
    def label(self) -> str:
        """Return the name shown in Settings and in the overlay."""
        return _OUTPUT_LABELS[self]

    @property
    def copies(self) -> bool:
        """True if the image goes to the clipboard."""
        return self in {ClickOutput.CLIPBOARD, ClickOutput.CLIPBOARD_AND_SAVE}

    @property
    def saves(self) -> bool:
        """True if the image is saved to the output folder."""
        return self in {ClickOutput.SAVE, ClickOutput.CLIPBOARD_AND_SAVE}

    @property
    def opens_editor(self) -> bool:
        """True if the image opens in the editor."""
        return self is ClickOutput.EDITOR


_TARGET_LABELS: Final = {
    ClickTarget.WINDOW: "Window",
    ClickTarget.SCROLL_HERE: "Scroll: from here down",
    ClickTarget.SCROLL_ALL: "Scroll: whole pane",
}
_OUTPUT_LABELS: Final = {
    ClickOutput.USUAL: "Usual (Capture tab)",
    ClickOutput.CLIPBOARD: "Clipboard only",
    ClickOutput.SAVE: "File only",
    ClickOutput.CLIPBOARD_AND_SAVE: "Clipboard and file",
    ClickOutput.EDITOR: "Editor only",
}


@dataclass(frozen=True, slots=True)
class ClickAction:
    """What a click captures, and where a click's or a drag's image goes."""

    target: ClickTarget = ClickTarget.WINDOW
    output: ClickOutput = ClickOutput.USUAL

    @property
    def is_plain(self) -> bool:
        """True if this does what a click without modifiers does."""
        return self == ClickAction()

    def describe(self, *, dragging: bool = False) -> str:
        """Return a short label for the overlay ("" for a plain action).

        A drag always captures its region, so only the output applies to it.
        """
        parts = []
        if not dragging and self.target.scrolls:
            parts.append(self.target.label)
        if self.output is not ClickOutput.USUAL:
            parts.append(self.output.label)
        return " · ".join(parts)


@dataclass(frozen=True, slots=True)
class ClickGestures:
    """The action for each combination of Ctrl, Alt and Shift.

    Field names list the held keys in the order Ctrl, Alt, Shift. With no
    modifier held, a click captures the window and follows the after-capture
    settings.
    """

    ctrl: ClickAction = ClickAction(ClickTarget.SCROLL_HERE)
    ctrl_alt: ClickAction = ClickAction(ClickTarget.SCROLL_ALL)
    alt: ClickAction = ClickAction(output=ClickOutput.CLIPBOARD)
    shift: ClickAction = ClickAction(output=ClickOutput.SAVE)
    alt_shift: ClickAction = ClickAction(output=ClickOutput.CLIPBOARD_AND_SAVE)
    ctrl_shift: ClickAction = ClickAction(ClickTarget.SCROLL_HERE, ClickOutput.SAVE)
    ctrl_alt_shift: ClickAction = ClickAction(
        ClickTarget.SCROLL_ALL, ClickOutput.CLIPBOARD
    )

    def resolve(self, *, ctrl: bool, alt: bool, shift: bool) -> ClickAction:
        """Return the action for the held modifiers."""
        name = combo_name(ctrl=ctrl, alt=alt, shift=shift)
        return ClickAction() if not name else self.action(name)

    def action(self, name: str) -> ClickAction:
        """Return the action for the combination field ``name``.

        Raises:
            KeyError: If ``name`` is not a combination field.
        """
        value = getattr(self, name, None) if name in self.combos() else None
        if not isinstance(value, ClickAction):
            raise KeyError(name)
        return value

    @classmethod
    def combos(cls) -> tuple[str, ...]:
        """Return the field names, in Settings order."""
        return tuple(f.name for f in fields(cls))


def combo_name(*, ctrl: bool, alt: bool, shift: bool) -> str:
    """Return the ``ClickGestures`` field for the held keys ("" for none)."""
    held = (("ctrl", ctrl), ("alt", alt), ("shift", shift))
    return "_".join(name for name, on in held if on)


def combo_label(name: str) -> str:
    """Return "Ctrl+Alt" for ``ctrl_alt``."""
    return "+".join(part.capitalize() for part in name.split("_"))
