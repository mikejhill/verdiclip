"""Capture requests are checked before the app touches the screen; Deferred."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from verdiclip.automation.capturing import CaptureKind, CaptureSpec
from verdiclip.automation.protocol import (
    AutomationError,
    Deferred,
    ErrorCode,
    ExitCode,
    JsonObject,
)
from verdiclip.geometry import Point, Rect

ABSOLUTE = str(Path.cwd().resolve() / "shot.png")


def spec(**params: object) -> CaptureSpec:
    """Parse ``params`` as a capture request."""
    return CaptureSpec.from_params(dict(params))  # ty: ignore[invalid-argument-type]


def refused(**params: object) -> AutomationError:
    """Return the error for an invalid request."""
    with pytest.raises(AutomationError) as caught:
        spec(**params)
    assert caught.value.code is ErrorCode.BAD_REQUEST
    return caught.value


class TestCaptureSpec:
    """Which options each capture type takes."""

    def test_universal_and_region(self) -> None:
        """Universal lets the user choose; a region names its rectangle."""
        chosen = spec(mode="universal")
        given = spec(mode="region", region=[-5, 0, 10, 20], output=ABSOLUTE, copy=True)

        assert chosen.kind is CaptureKind.UNIVERSAL
        assert chosen.interactive
        assert not chosen.names_window
        assert given.region == Rect(-5, 0, 10, 20)
        assert not given.interactive
        assert given.output == Path(ABSOLUTE)
        assert given.copy
        assert given.editor
        assert not spec(mode="universal", copy=True, editor=False).editor

    def test_window_targets(self) -> None:
        """A window capture finds its window by title, handle or point."""
        assert spec(mode="window").title is None
        assert spec(mode="window", title="Edge").names_window
        assert spec(mode="window", handle=42).handle == 42
        assert spec(mode="window", at=[3, 4]).at == Point(3, 4)
        assert not spec(mode="window", at=[3, 4]).names_window
        assert not spec(mode="window").interactive

    def test_fullscreen_options(self) -> None:
        """Fullscreen takes a monitor number or every monitor."""
        assert spec(mode="fullscreen", monitor=2).monitor == 2
        assert spec(mode="fullscreen", all=True).all_screens
        assert not spec(mode="fullscreen").interactive

    def test_scrolling_targets_and_start(self) -> None:
        """Scrolling without a target lets the user click; here needs a target."""
        assert spec(mode="scrolling").interactive
        assert not spec(mode="scrolling", at=[1, 2]).interactive
        assert not spec(mode="scrolling", title="Edge").interactive
        assert spec(mode="scrolling", at=[1, 2], **{"from": "here"}).from_top is False
        assert spec(mode="scrolling", handle=7, **{"from": "top"}).from_top

    @pytest.mark.parametrize(
        ("params", "message"),
        [
            ({"mode": "movie"}, "mode must be one of universal, region"),
            ({}, "mode must be one of"),
            ({"mode": "region", "title": "x"}, "region capture doesn't take title"),
            ({"mode": "region"}, "region capture needs region; universal"),
            ({"mode": "universal", "region": [0, 0, 1, 1]}, "doesn't take region"),
            ({"mode": "fullscreen", "region": [0, 0, 1, 1]}, "doesn't take region"),
            ({"mode": "window", "monitor": 1}, "window capture doesn't take monitor"),
            ({"mode": "window", "from": "here"}, "doesn't take from"),
            ({"mode": "window", "title": "a", "at": [1, 1]}, "not title and at"),
            ({"mode": "scrolling", "from": "here"}, "from here needs at"),
            ({"mode": "fullscreen", "monitor": 1, "all": True}, "monitor or all"),
            ({"mode": "region", "region": [0, 0, 0, 5]}, "must be positive"),
            ({"mode": "region", "region": [0, 0, 5]}, "region must be [x, y, w"),
            ({"mode": "region", "region": [0, 0, 5, True]}, "region must be"),
            ({"mode": "window", "at": "1,2"}, "at must be [x, y]"),
            ({"mode": "region", "region": [math.inf, 0, 5, 5]}, "region must be"),
            ({"mode": "window", "at": [math.nan, 1]}, "at must be [x, y]"),
            ({"mode": "window", "title": "  "}, "title must be non-empty"),
            ({"mode": "window", "handle": 0}, "handle must be an integer of at"),
            ({"mode": "window", "handle": True}, "handle must be an integer"),
            ({"mode": "fullscreen", "all": "yes"}, "all must be true or false"),
            ({"mode": "scrolling", "at": [1, 1], "from": "middle"}, "'top' or"),
            ({"mode": "universal", "output": "shot.png"}, "output must be an absol"),
            ({"mode": "universal", "output": 5}, "output must be an absolute"),
            ({"mode": "universal", "editor": False}, "editor false needs output"),
            ({"mode": "universal", "editor": "no"}, "editor must be true or false"),
        ],
    )
    def test_refusals(self, params: JsonObject, message: str) -> None:
        """Options that are malformed or don't fit the type are bad requests."""
        assert message in refused(**params).message


class TestDeferred:
    """Results that arrive later settle once."""

    def test_listeners_before_and_after_settling(self) -> None:
        """Listeners added before or after resolving see the value once."""
        later: Deferred[int] = Deferred()
        seen: list[tuple[int | None, AutomationError | None]] = []
        later.then(lambda v, e: seen.append((v, e)))

        assert not later.done
        later.resolve(3)
        later.resolve(4)
        later.reject(AutomationError(ErrorCode.FAILED, "late"))
        later.then(lambda v, e: seen.append((v, e)))

        assert later.done
        assert seen == [(3, None), (3, None)]

    def test_map_converts_values_and_passes_errors(self) -> None:
        """Map converts the value, keeps an error, and catches raised errors."""
        source: Deferred[int] = Deferred()
        doubled = source.map(lambda v: v * 2)
        failing: Deferred[int] = Deferred()
        failure = AutomationError(ErrorCode.CANCELLED, "no")
        mapped_error = failing.map(lambda v: v)
        raising: Deferred[int] = Deferred()

        def boom(_value: int) -> int:
            raise AutomationError(ErrorCode.NOT_FOUND, "gone")

        converted = raising.map(boom)
        results: list[object] = []
        for deferred in (doubled, mapped_error, converted):
            deferred.then(lambda v, e: results.append(e.code if e else v))

        source.resolve(21)
        failing.reject(failure)
        raising.resolve(1)

        assert results == [42, ErrorCode.CANCELLED, ErrorCode.NOT_FOUND]

    def test_cancelled_has_its_own_exit_code(self) -> None:
        """A user cancelling a capture exits 7."""
        assert ExitCode.for_error(ErrorCode.CANCELLED) is ExitCode.CANCELLED
        assert int(ExitCode.CANCELLED) == 7
