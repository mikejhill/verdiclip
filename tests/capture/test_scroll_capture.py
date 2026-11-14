"""The top-to-bottom sequence against a simulated scrolling panel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

import pytest
from PySide6.QtGui import QImage
from tests.capture.test_stitch import VIEW, WIDTH, expected, page, view

from verdiclip.capture import scrolling
from verdiclip.capture.scrolling import ScrollCapture, ScrollPosition
from verdiclip.capture.stitch import StitchResult, find_offset
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


def run(
    panel: Panel,
    cancel_after: int | None = None,
    *,
    stop_after: int | None = None,
    from_top: bool = True,
) -> scrolling.ScrollResult:
    """Run."""
    clock = Clock()
    return ScrollCapture(
        panel,
        lambda: cancel_after is not None and panel.grabs >= cancel_after,
        stopped=lambda: stop_after is not None and panel.grabs >= stop_after,
        from_top=from_top,
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
    """Moving pixels are trusted over a stuck position; still pixels confirm it."""
    panel = Stuck(900, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""
    assert panel.writes[-2:] == [100.0, 0.0]  # Asked for the end again


class ShortOfTheEnd(Stuck):
    """The first request for the end stops short; a second one gets there."""

    asked: int = 0

    @override
    def scroll_to(self, percent: float) -> None:
        """Stop at 90% the first time the end is asked for."""
        if percent == 100.0:
            self.asked += 1
            if self.asked == 1:
                percent = 90.0
        super().scroll_to(percent)


def test_stuck_position_short_of_the_end_is_followed_by_the_pixels() -> None:
    """When the end is not reached, the pixels say so and the end is asked again."""
    panel = ShortOfTheEnd(900, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert panel.asked >= 2


def test_stuck_position_short_of_the_end_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pixels, if asking for the end again moves the panel, say so."""
    monkeypatch.setattr(scrolling, "pixel_offset", lambda *_args: None)
    result = run(ShortOfTheEnd(900, start=0.0))
    assert "reached the end" in result.warning


class Creeping(Panel):
    """Once, answers a full step with a position that barely moved (Edge)."""

    creep: int = 3
    answer: ScrollPosition | None = None

    @override
    def scroll_to(self, percent: float) -> None:
        """Scroll; on the third request, remember a barely moved answer."""
        before = super().position()
        super().scroll_to(percent)
        self.creep -= 1
        self.answer = (
            ScrollPosition(before.percent + 0.25, before.view_size)
            if self.creep == 0
            else None
        )

    @override
    def position(self) -> ScrollPosition:
        """The barely moved answer for that step, the real one otherwise."""
        return self.answer or super().position()


def test_answer_that_barely_moved_after_a_full_step_is_overruled() -> None:
    """The next step starts from where the pixels went, not back up the page."""
    panel = Creeping(2000, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""
    assert panel.writes == sorted(panel.writes[:-1]) + panel.writes[-1:]


def test_position_that_lags_the_pixels_is_waited_for() -> None:
    """A provider that reports the new position late is not taken as the end."""
    panel = Lagging(1400, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""


def test_position_that_moves_backwards_is_overruled_by_the_pixels() -> None:
    """A position answer that goes backwards does not undo a step that moved."""
    panel = Backwards(1400, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""


def test_position_that_moves_backwards_without_pixels_stops_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With nothing to align, a position going backwards ends with a warning."""
    monkeypatch.setattr(scrolling, "pixel_offset", lambda *_args: None)
    result = run(Backwards(1400, start=0.0))
    assert result.image is not None
    assert "reached the end" in result.warning


def test_pixel_limit_stops_early_with_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pixel limit stops early with a note."""
    monkeypatch.setattr(scrolling, "MAX_PIXELS", WIDTH * VIEW * 5 // 2)
    result = run(Panel(3000))
    assert result.frames == 2
    assert result.image is not None
    assert result.image.width() * result.image.height() <= WIDTH * VIEW * 5 // 2
    assert "image size limit" in result.warning


def test_estimated_seams_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Estimated seams become a warning."""
    real = scrolling.stitch
    monkeypatch.setattr(
        scrolling,
        "stitch",
        lambda frames, *_: StitchResult(real(frames).image, 2),
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


def test_lagging_position_after_the_move_to_the_top_is_waited_for() -> None:
    """A late answer to the move to the top does not skip content."""
    for start in (40.0, 100.0):
        panel = Lagging(1400, start=start)
        result = run(panel)
        assert result.image == panel.document


class Late(Panel):
    """Ignores the first request to go to the top, like a busy app."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Drop the first request."""
        if self.writes:
            super().scroll_to(percent)
        else:
            self.writes.append(percent)


def test_ignored_move_to_the_top_is_asked_again() -> None:
    """The start view is not kept as the top frame."""
    panel = Late(1400, start=100.0)
    result = run(panel)
    assert result.image == panel.document
    assert panel.writes[:2] == [0.0, 0.0]


def test_start_near_the_top_is_not_asked_again() -> None:
    """A start a few rows down may look like the top: one request is enough."""
    panel = Late(1400, start=2.0)
    run(panel)
    assert panel.writes[1] != 0.0


def test_stale_top_frame_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the stitcher leaves out stale frames, the user is told."""
    monkeypatch.setattr(
        scrolling, "stitch", lambda frames, *_: StitchResult(frames[0].image, 0, 1)
    )
    assert "top of the image" in run(Panel(900)).warning


class Spinning(Panel):
    """A spinner in a few rows changes on every grab."""

    @override
    def grab(self) -> QImage:
        """The view with a few rows that never settle."""
        image = super().grab()
        for y in range(4):
            image.setPixel(0, y, self.grabs)
        return image


def test_spinner_does_not_hold_up_or_fake_a_move() -> None:
    """Small animations neither count as the view moving nor stop it settling."""
    panel = Spinning(900)
    clock = Clock()
    result = ScrollCapture(panel, lambda: False, clock=clock, sleep=clock.sleep).run()
    assert result.image is not None
    assert result.image.height() == panel.content
    assert clock.now < 2.0


class Banded(Panel):
    """Fixed header and footer bands that cover more than a quarter of the view."""

    header = 40
    footer = 30

    @property
    @override
    def range(self) -> int:
        """Scroll range in rows: the content minus the visible body."""
        return max(0, self.content - (VIEW - self.header - self.footer))

    @override
    def position(self) -> ScrollPosition:
        """View size over the whole page, bands included."""
        total = self.content + self.header + self.footer
        percent = 100.0 if self.range == 0 else self.top * 100 / self.range
        return ScrollPosition(percent, VIEW * 100 / total)

    @override
    def grab(self) -> QImage:
        """The body between the two bands."""
        super().grab()
        return view(self.document, self.shown, header=self.header, footer=self.footer)


class LaggingBlank(Lagging, Blank):
    """A blank stretch read through a provider that answers late."""


def test_tall_fixed_bands_do_not_hide_content() -> None:
    """Steps shrink when fixed bands leave too little overlap."""
    panel = Banded(700, start=0.0)
    result = run(panel)
    assert result.image == expected(panel.document, header=40, footer=30)


def test_blank_stretch_with_a_lagging_position_reaches_the_end() -> None:
    """A late position answer during a blank stretch is waited for."""
    panel = LaggingBlank(1200, start=0.0)
    result = run(panel)
    assert result.image is not None
    assert result.image.height() == panel.content


def test_overlay_in_some_frames_does_not_reject_a_good_join() -> None:
    """A band that appears in only some frames still allows pixel alignment."""
    previous = list(range(200))
    following = [*range(5000, 5060), *range(150, 290)]  # 60 overlay rows
    assert find_offset(previous, following, 90.0) == 90


class Animated(Panel):
    """Never settles: every grab differs."""

    @override
    def grab(self) -> QImage:
        """A fresh image each time."""
        image = super().grab()
        image.setPixel(0, 0, self.grabs)
        return image


def test_cancel_while_settling_is_seen_promptly() -> None:
    """Esc during a long settle reaches the restore well inside the grace period."""
    panel = Animated(900)
    clock = Clock()
    result = ScrollCapture(
        panel, lambda: panel.grabs >= 3, clock=clock, sleep=clock.sleep
    ).run()
    assert result.message == "Scrolling capture cancelled"
    assert clock.now < 0.5
    assert panel.writes[-1] == pytest.approx(40.0)


def test_render_slower_than_the_change_wait_is_still_awaited() -> None:
    """A panel that takes a second to repaint is not captured stale."""
    panel = Panel(900, lag=20)
    assert run(panel).image == panel.document


class Ignoring(Panel):
    """Accepts the move to the top, then ignores every scroll request."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Only the first request and the restore take effect."""
        if not self.writes or percent == self.start:
            super().scroll_to(percent)
        else:
            self.writes.append(percent)


def test_last_fraction_of_a_percent_is_captured() -> None:
    """A position just short of 100% still requests the very bottom."""
    panel = Panel(14760, start=0.0)
    assert run(panel).image == panel.document


def test_stalled_panel_is_flagged() -> None:
    """A panel that stops moving well before its end gets a warning."""
    result = run(Ignoring(900, start=0.0))
    assert result.frames == 1
    assert "reached the end" in result.warning


class Repaints(Panel):
    """Like Edge: a small overlay change first, the position only after the paint."""

    @override
    def position(self) -> ScrollPosition:
        """Time passes while polling; report what is on screen."""
        if self.pending:
            self.pending -= 1
        else:
            self.shown = self.top
        percent = 100.0 if self.range == 0 else self.shown * 100 / self.range
        return ScrollPosition(percent, min(100.0, VIEW * 100 / self.content))

    @override
    def grab(self) -> QImage:
        """While the paint is pending, the old page with one changed pixel."""
        image = super().grab()
        if self.pending:
            image.setPixel(0, 0, 0x123456)
        return image


def test_small_change_before_the_paint_is_not_taken_as_the_top() -> None:
    """The top frame waits for the provider, not for any changed pixel."""
    panel = Repaints(1400, start=60.0, lag=6)
    assert run(panel).image == panel.document


class Smooth(Panel):
    """Smooth scrolling: the first read after a scroll is a midway position."""

    midway: ScrollPosition | None = None

    @override
    def scroll_to(self, percent: float) -> None:
        """Scroll and remember a midway answer."""
        before = super().position()
        super().scroll_to(percent)
        self.midway = ScrollPosition(
            (before.percent + super().position().percent) / 2, before.view_size
        )

    @override
    def position(self) -> ScrollPosition:
        """The midway answer once, then the real one."""
        midway = self.midway
        if midway is not None:
            self.midway = None
            return midway
        return super().position()


def test_midway_position_during_smooth_scrolling_is_not_kept() -> None:
    """The position is read again once the pixels settle."""
    for start in (0.0, 60.0):
        panel = Smooth(900, start=start)
        assert run(panel).image == panel.document


def test_unaligned_frames_with_a_stuck_position_use_the_requested_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without pixel alignment, the requested step places a frame, not a view."""
    from verdiclip.capture import stitch  # noqa: PLC0415

    monkeypatch.setattr(stitch, "find_offset", lambda *_args: None)
    panel = Stuck(900, start=0.0)
    result = run(panel)
    assert result.image is not None
    assert abs(result.image.height() - panel.content) <= result.frames


class LateAtTheEnd(Lagging):
    """Answers the request for the end only after many reads, like Chromium."""

    @override
    def scroll_to(self, percent: float) -> None:
        """Hold the old answer for sixteen reads after asking for the end."""
        super().scroll_to(percent)
        if percent == 100.0:
            self.stale = 16


def test_late_answer_at_the_end_is_waited_for() -> None:
    """The final check waits for a late answer before asking again."""
    panel = LateAtTheEnd(900, start=0.0)
    result = run(panel)
    assert result.image == panel.document
    assert result.warning == ""
    assert panel.writes.count(100.0) == 1


class Tiled(Panel):
    """Paints the top half first and pauses for a poll before the rest."""

    torn: int = 0

    @override
    def scroll_to(self, percent: float) -> None:
        """Scroll; the next two grabs are half painted."""
        self.before = self.shown
        super().scroll_to(percent)
        self.torn = 2

    @override
    def grab(self) -> QImage:
        """The new top half over the old bottom half while torn."""
        image = super().grab()
        if self.torn:
            self.torn -= 1
            old = self.document.copy(0, self.before, WIDTH, VIEW)
            for y in range(VIEW // 2, VIEW):
                for x in range(WIDTH):
                    image.setPixel(x, y, old.pixel(x, y))
        return image


def test_half_painted_view_is_not_kept() -> None:
    """A pause halfway through painting does not count as settled."""
    panel = Tiled(900)
    assert run(panel).image == panel.document


def test_esc_keeps_what_was_captured() -> None:
    """Stopping part way delivers the top of the page, says so, and restores."""
    panel = Panel(2000)
    result = run(panel, stop_after=12)
    assert result.image is not None
    assert result.frames > 1
    assert VIEW < result.image.height() < panel.content
    assert result.image == panel.document.copy(0, 0, WIDTH, result.image.height())
    assert scrolling.STOPPED_NOTE in result.warning
    assert panel.writes[-1] == pytest.approx(40.0)


def test_esc_before_the_first_frame_delivers_nothing() -> None:
    """Stopping before anything was captured is a plain cancel."""
    panel = Panel(2000)
    result = run(panel, stop_after=0)
    assert result.image is None
    assert result.message == "Scrolling capture cancelled"


def test_capture_from_here_starts_at_the_current_view() -> None:
    """From the current position: no scroll to the top, down to the end."""
    panel = Panel(900, start=40.0)
    top = panel.top
    result = run(panel, from_top=False)
    assert 0.0 not in panel.writes[:-1]
    assert result.image == panel.document.copy(0, top, WIDTH, panel.content - top)
    assert result.warning == ""
    assert panel.writes[-1] == pytest.approx(40.0)


def test_capture_from_the_bottom_is_one_frame() -> None:
    """From a panel already at its end, the current view is the image."""
    panel = Panel(900, start=100.0)
    result = run(panel, from_top=False)
    assert result.frames == 1
    assert result.image == panel.document.copy(0, panel.range, WIDTH, VIEW)


class LoadingTop(Banded):
    """The top still finishing a redraw in the first frame taken there."""

    @override
    def grab(self) -> QImage:
        """The first grabs show a spinner in the top rows of the body."""
        image = super().grab()
        if self.grabs <= 3:  # The settle and the first frame
            for y in range(self.header, self.header + 10):
                for x in range(WIDTH):
                    image.setPixel(x, y, 0x123456 + self.grabs)
        return image


def test_step_back_keeps_the_view_it_measures_from() -> None:
    """After stepping back from an overshoot, the first frame is grabbed afresh."""
    panel = LoadingTop(700, start=0.0)
    result = run(panel)
    assert result.image == expected(panel.document, header=40, footer=30)


class ThinBanded(Banded):
    """A one-row header and footer, like a border."""

    header = 1
    footer = 1


def test_thin_fixed_bands_do_not_force_a_step_back() -> None:
    """A border a row or two tall takes too little overlap to step back for."""
    panel = ThinBanded(700, start=0.0)
    result = run(panel)
    assert result.image == expected(panel.document, header=1, footer=1)
    assert panel.writes.count(0.0) == 2  # The top and the restore: no step back
