"""Pure, replayable input ownership for one explicit capture selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import ClassVar

from verdiclip.capture.input_routing import MoveMode, PointerTracker
from verdiclip.geometry import Point, Rect


class InputKind(Enum):
    """Only selection commands cross the input boundary; no arbitrary key text."""

    MOVE = auto()
    PRESS = auto()
    RELEASE = auto()
    ENTER = auto()
    CANCEL = auto()
    NEXT = auto()


@dataclass(frozen=True, slots=True)
class SelectionInput:
    """One virtual physical-desktop point and a semantic selection action."""

    kind: InputKind
    point: Point
    reason: str = ""
    amount: int = 1


@dataclass(frozen=True, slots=True)
class InputDecision:
    """Whether Windows receives this packet and what capture receives instead."""

    consume: bool
    events: tuple[SelectionInput, ...] = ()


class CaptureInputPolicy:
    """Retain matched input pairs and never reposition from button packets.

    The source must supply a qualified movement interpretation. Low-level
    injected flags alone do not distinguish absolute from relative injection;
    unknown movement cancels rather than guessing or forwarding a click.
    """

    LEFT_DOWN = 0x0201
    TAB = 0x09
    LEFT_UP = 0x0202
    RIGHT_DOWN = 0x0204
    RIGHT_UP = 0x0205
    MOVE = 0x0200
    WHEEL = 0x020A
    X_DOWN = 0x020B
    X_UP = 0x020C
    ESCAPE = 0x1B
    ENTER = 0x0D
    CONTROL = frozenset({0x11, 0xA2, 0xA3})
    SHIFT = frozenset({0x10, 0xA0, 0xA1})
    BUTTON_DOWN = frozenset({LEFT_DOWN, RIGHT_DOWN, 0x0207, X_DOWN})
    BUTTON_UP = frozenset({LEFT_UP, RIGHT_UP, 0x0208, X_UP})
    ARROWS: ClassVar[dict[int, Point]] = {
        0x25: Point(-1, 0),
        0x26: Point(0, -1),
        0x27: Point(1, 0),
        0x28: Point(0, 1),
    }

    def __init__(self, initial: Point, bounds: Rect) -> None:
        """Start with no key/button ownership from before capture began."""
        self.pointer = PointerTracker(initial, bounds)
        self._buttons: set[tuple[int, int]] = set()
        self._keys: set[int] = set()
        self._passthrough_keys: set[int] = set()
        self._initial_keys: set[int] = set()
        self._cancelled = False

    @property
    def owns_pairs(self) -> bool:
        """A bounded shutdown drain can consume releases of swallowed presses."""
        return bool(self._buttons or self._keys)

    def cancel(self, reason: str) -> tuple[SelectionInput, ...]:
        """Invalidate once; retain only pending pair ownership for safe draining."""
        if self._cancelled:
            return ()
        self._cancelled = True
        return (SelectionInput(InputKind.CANCEL, self.pointer.point, reason),)

    def prime_keys(self, held: frozenset[int]) -> None:
        """Keep pre-existing downs with their original recipient through release."""
        self._passthrough_keys.update(held)
        self._initial_keys.update(held)

    def mouse(
        self,
        message: int,
        proposed: Point,
        *,
        mode: MoveMode | None,
        anchor: Point,
        button: int = 0,
    ) -> InputDecision:
        """Route qualified moves and owned left/right pairs without click forwarding."""
        if message in self.BUTTON_UP:
            return self._button_up(message, button)
        if self._cancelled:
            if message in self.BUTTON_DOWN:
                self._buttons.add((message, button))
            return InputDecision(consume=True)
        if message == self.MOVE:
            return self._move(proposed, mode, anchor)
        if message in self.BUTTON_DOWN:
            return self._button_down(message, button)
        if message == self.WHEEL:
            return InputDecision(consume=True, events=self.cancel("wheel-interference"))
        return InputDecision(
            consume=False, events=self.cancel("unsupported-pointer-action")
        )

    def _move(
        self, proposed: Point, mode: MoveMode | None, anchor: Point
    ) -> InputDecision:
        if mode is None or not self.pointer.move(proposed, mode=mode, anchor=anchor):
            return InputDecision(
                consume=True, events=self.cancel("unsupported-pointer-source")
            )
        return InputDecision(
            consume=True, events=(SelectionInput(InputKind.MOVE, self.pointer.point),)
        )

    def _button_down(self, message: int, button: int) -> InputDecision:
        self._buttons.add((message, button))
        if message not in {self.LEFT_DOWN, self.RIGHT_DOWN}:
            return InputDecision(
                consume=True, events=self.cancel("unsupported-pointer-action")
            )
        events = (
            ()
            if message == self.RIGHT_DOWN
            else (SelectionInput(InputKind.PRESS, self.pointer.point),)
        )
        return InputDecision(consume=True, events=events)

    def _button_up(self, message: int, button: int) -> InputDecision:
        down = (message - 1, button)
        owned = down in self._buttons
        self._buttons.discard(down)
        if not owned or self._cancelled:
            return InputDecision(consume=owned)
        if message != self.LEFT_UP:
            return InputDecision(consume=True, events=self.cancel("right-button"))
        return InputDecision(
            consume=True,
            events=(SelectionInput(InputKind.RELEASE, self.pointer.point),),
        )

    def key(
        self,
        code: int,
        *,
        pressed: bool,
        control: bool = False,
        shift: bool = False,
        controls: bool = False,
    ) -> InputDecision:
        """Capture semantic keys only; Escape/Enter act on their owned release."""
        if code in self._passthrough_keys:
            if not pressed:
                self._passthrough_keys.discard(code)
                self._initial_keys.discard(code)
            events = (
                self.cancel("keyboard-interference")
                if pressed and code not in self._initial_keys
                else ()
            )
            return InputDecision(consume=False, events=events)
        if not pressed:
            return self._key_up(code, shift=shift)
        if self._cancelled:
            consume = code not in self.CONTROL | self.SHIFT
            if consume:
                self._keys.add(code)
            return InputDecision(consume=consume)
        if code in self.CONTROL | self.SHIFT:
            return InputDecision(consume=False)
        if code in {self.ESCAPE, self.ENTER, self.TAB}:
            self._keys.add(code)
            return InputDecision(consume=True)
        return self._navigation(code, control=control, controls=controls)

    def _key_up(self, code: int, *, shift: bool) -> InputDecision:
        owned = code in self._keys
        self._keys.discard(code)
        if not owned or self._cancelled:
            return InputDecision(consume=owned)
        if code == self.ESCAPE:
            return InputDecision(consume=True, events=self.cancel("escape"))
        if code == self.TAB:
            return InputDecision(
                consume=True,
                events=(
                    SelectionInput(
                        InputKind.NEXT, self.pointer.point, "previous" if shift else ""
                    ),
                ),
            )
        events = (
            (SelectionInput(InputKind.ENTER, self.pointer.point),)
            if (code == self.ENTER)
            else ()
        )
        return InputDecision(consume=True, events=events)

    def _navigation(self, code: int, *, control: bool, controls: bool) -> InputDecision:
        delta = self.ARROWS.get(code)
        if delta is not None:
            self._keys.add(code)
            if not controls:
                point = self.pointer.point + delta.scaled(10 if control else 1)
                self.pointer.move(point, mode=MoveMode.ABSOLUTE, anchor=point)
            return InputDecision(
                consume=True,
                events=(
                    SelectionInput(
                        InputKind.MOVE,
                        self.pointer.point,
                        {
                            0x25: "arrow:left",
                            0x26: "arrow:up",
                            0x27: "arrow:right",
                            0x28: "arrow:down",
                        }[code],
                        10 if control else 1,
                    ),
                ),
            )
        self._passthrough_keys.add(code)
        return InputDecision(consume=False, events=self.cancel("keyboard-interference"))
