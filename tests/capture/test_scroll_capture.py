"""The top-to-bottom sequence against a simulated scrolling panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

import pytest
from PySide6.QtGui import QImage
from tests.capture.test_stitch import VIEW, WIDTH, page

from verdiclip.capture import scrolling
from verdiclip.capture.scrolling import ScrollCapture, ScrollPosition
from verdiclip.capture.stitch import StitchResult
from verdiclip.exceptions import CaptureError


@dataclass
class Panel:
    """A document of ``content`` rows shown through a ``VIEW``-row viewport."""

    content: int
    start: float = 40.0
    lag: int = 0  # grabs that still show the old pixels after a scroll
    fail_restore: bool = False
    gone_after: int | None = None
    document: QImage = field(init=False)
    top: int = field(init=False)
    pending: int = field(init=False, default=0)
    grabs: int = field(init=False, default=0)
    shown: int = field(init=False)
    writes: list[float] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        """Post init."""
        self.document = page(self.content)
        self.top = round(self.start / 100 * self.range)
        self.shown = self.top

    @property
    def range(self) -> int:
        """Range."""
        return max(0, self.content - VIEW)

    @property
    def height(self) -> int:
        """Height."""
        return VIEW

    def position(self) -> ScrollPosition:
        """Position."""
        percent = 100.0 if self.range == 0 else self.top * 100 / self.range
        return ScrollPosition(percent, min(100.0, VIEW * 100 / self.content))

    def scroll_to(self, percent: float) -> None:
        """Scroll to."""
        if self.fail_restore and self.writes:
            self.writes.append(percent)
            if percent == self.start:
                raise CaptureError("blocked")
        self.writes.append(percent)
        self.top = round(percent / 100 * self.range)
        self.pending = self.lag

    def grab(self) -> QImage:
        """Grab."""
        self.grabs += 1
        if self.pending:
            self.pending -= 1
        else:
            self.shown = self.top
        return self.document.copy(0, self.shown, WIDTH, VIEW)

    def check(self) -> None:
        """Check."""
        if self.gone_after is not None and self.grabs >= self.gone_after:
            raise CaptureError("The window being captured was closed or minimized")


class Clock:
    """Clock."""

    def __init__(self) -> None:
        """Init."""
        self.now = 0.0

    def __call__(self) -> float:
        """Call."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Sleep."""
        self.now += seconds


def run(panel: Panel, cancel_after: int | None = None) -> scrolling.ScrollResult:
    """Run."""
    clock = Clock()
    return ScrollCapture(
        panel,
        lambda: cancel_after is not None and panel.grabs >= cancel_after,
        clock=clock,
        sleep=clock.sleep,
    ).run()


def test_whole_document_is_captured_and_position_restored() -> None:
    """Whole document is captured and position restored."""
    panel = Panel(900)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""
    assert panel.writes[0] == 0.0
    assert panel.writes[-1] == pytest.approx(40.0)


def test_slow_rendering_waits_for_the_new_pixels() -> None:
    """Slow rendering waits for the new pixels."""
    panel = Panel(700, lag=3)
    result = run(panel)
    assert result.image == panel.document


def test_panel_that_shows_everything_is_one_frame() -> None:
    """Panel that shows everything is one frame."""
    panel = Panel(VIEW)
    result = run(panel)
    assert result.frames == 1
    assert result.image == panel.document


def test_cancel_restores_and_delivers_nothing() -> None:
    """Cancel restores and delivers nothing."""
    panel = Panel(2000)
    result = run(panel, cancel_after=6)
    assert result.image is None
    assert result.message == "Scrolling capture cancelled"
    assert panel.writes[-1] == pytest.approx(40.0)


def test_closed_window_stops_with_its_reason() -> None:
    """Closed window stops with its reason."""
    panel = Panel(2000, gone_after=6)
    result = run(panel)
    assert result.image is None
    assert "closed" in result.message


def test_failed_restore_still_delivers_with_a_note() -> None:
    """Failed restore still delivers with a note."""
    panel = Panel(600, fail_restore=True)
    result = run(panel)
    assert result.image == panel.document
    assert "scrolled back" in result.warning


def test_frame_limit_stops_early_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Frame limit stops early with a note."""
    monkeypatch.setattr(scrolling, "MAX_FRAMES", 3)
    panel = Panel(3000)
    result = run(panel)
    assert result.frames == 3
    assert result.image is not None
    assert "limit" in result.warning


def test_unknown_view_size_still_advances() -> None:
    """Unknown view size still advances."""
    assert ScrollPosition(10.0, 0.0).pixels_per_percent(VIEW) is None
    assert ScrollPosition(10.0, 50.0).pixels_per_percent(VIEW) == pytest.approx(2.0)


class Flicker(Panel):
    """Shows one in-between image after each scroll before the real one."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Scroll and arm one in-between grab."""
        super().scroll_to(percent)
        self.flicker = True

    @override
    def grab(self) -> QImage:
        """Return an inverted frame once after a scroll."""
        image = super().grab()
        if getattr(self, "flicker", False):
            self.flicker = False
            image.invertPixels()
        return image


class Stuck(Panel):
    """Pixels move but the reported position never does."""

    @override
    def position(self) -> ScrollPosition:
        """Always the top."""
        return ScrollPosition(0.0, super().position().view_size)


class Lagging(Panel):
    """Reports the previous position for a few reads after each scroll."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Scroll and hold the old position for three reads."""
        self.reported = super().position()
        self.stale = 3
        super().scroll_to(percent)

    @override
    def position(self) -> ScrollPosition:
        """The old position while stale, then the real one."""
        if getattr(self, "stale", 0):
            self.stale -= 1
            return self.reported
        return super().position()


class Backwards(Panel):
    """Reports a lower position after the second scroll."""

    @override
    def position(self) -> ScrollPosition:
        """Real position, except it drops once the panel is past a third."""
        real = super().position()
        if real.percent > 30.0:
            return ScrollPosition(1.0, real.view_size)
        return real


def test_in_between_frames_are_not_kept() -> None:
    """A frame that changes during settling is replaced by the settled one."""
    panel = Flicker(700)
    result = run(panel)
    assert result.image == panel.document


def test_position_that_never_moves_still_reaches_the_end() -> None:
    """Moving pixels are trusted over a stuck position, with a warning."""
    panel = Stuck(900, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert "reached the end" in result.warning


def test_position_that_lags_the_pixels_is_waited_for() -> None:
    """A provider that reports the new position late is not taken as the end."""
    panel = Lagging(1400, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""


def test_position_that_moves_backwards_stops_with_a_warning() -> None:
    """A position that goes backwards ends the capture with a warning."""
    panel = Backwards(1400, start=0.0)
    result = run(panel)
    assert result.image is not None
    assert "reached the end" in result.warning


def test_pixel_limit_stops_early_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pixel limit stops early with a note."""
    monkeypatch.setattr(scrolling, "MAX_PIXELS", WIDTH * VIEW)
    result = run(Panel(3000))
    assert result.frames == 2
    assert "image size limit" in result.warning


def test_estimated_seams_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estimated seams become a warning."""
    real = scrolling.stitch
    monkeypatch.setattr(
        scrolling,
        "stitch",
        lambda frames: StitchResult(real(frames).image, 2),
    )
    result = run(Panel(700))
    assert result.warning == "2 seam(s) were estimated; check the image."


class Blank(Panel):
    """A document with a blank stretch taller than the viewport."""

    @override
    def __post_init__(self) -> None:
        """Paint rows 300 to 700 white."""
        super().__post_init__()
        for y in range(300, 700):
            for x in range(WIDTH):
                self.document.setPixel(x, y, 0xFFFFFF)


class Failing(Panel):
    """A native provider call fails after the panel has moved."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Fail on the second scroll request only."""
        super().scroll_to(percent)
        if len(self.writes) == 2:  # The step after the top
            raise OSError("provider failed")


def test_blank_stretch_is_not_taken_as_the_end() -> None:
    """Identical frames with an advancing position keep the capture going."""
    panel = Blank(1200, start=0.0)
    result = run(panel)
    assert result.image is not None
    assert result.image.height() == panel.content
    assert result.image.copy(0, 900, WIDTH, 300) == panel.document.copy(
        0, 900, WIDTH, 300
    )


def test_native_failure_restores_the_start() -> None:
    """A failed COM call is reported and the start position is restored."""
    panel = Failing(1200)
    result = run(panel)
    assert result.image is None
    assert result.message == "provider failed"
    assert panel.writes[-1] == pytest.approx(40.0)
