"""Portable contracts for the isolated scrolling feasibility slice."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import override

import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from scripts.simple_scroll_native import _trace_helper

from verdiclip.capture.scrolling import (
    ScrollPosition,
    ScrollTransaction,
    VerticalStitch,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def document(height: int = 600) -> QImage:
    """Make unique rows whose exact final pixels can be compared."""
    result = QImage(80, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(result.width()):
            result.setPixelColor(x, y, QColor((y + x) % 251, y // 251, x))
    return result


class Target:
    """Finite document with a viewport and user-observable scroll state."""

    area = Rect(10, 20, 80, 120)

    def __init__(self, *, total: int = 600, offset: int = 173) -> None:
        self.document = document(total)
        self.offset = offset
        self.horizontal = 27.0
        self.input_changed = False
        self.moves: list[float] = []
        self.fail_move = False
        self.ignore_move = False
        self.jump = False
        self.mutate_horizontal = False
        self.mutate_extent = False
        self.animate = False
        self.late_movement = False
        self.fail_frame = False
        self.count = 0

    def check(self) -> None:
        """Reject input rather than trying to regain ownership."""
        if self.input_changed:
            raise CaptureError("User input")

    def position(self) -> ScrollPosition:
        """Model quantized UIA percentages for finite pixels."""
        return ScrollPosition(self.horizontal, self.offset / 480 * 100, 20.0)

    def move(self, vertical: float) -> None:
        """Record writes, including failed writes with uncertain side effects."""
        self.moves.append(vertical)
        if not self.ignore_move:
            self.offset = round(480 * vertical / 100)
        if self.fail_move:
            raise OSError("Provider failed")
        if self.jump and len(self.moves) > 1:
            self.offset = min(480, self.offset + 120)
        if self.mutate_horizontal and len(self.moves) > 1:
            self.horizontal = 40.0

    def frame(self) -> QImage:
        """Return the visible viewport with optional corruption."""
        if self.fail_frame:
            raise CaptureError("Capture failed")
        self.count += 1
        result = self.document.copy(0, self.offset, 80, 120)
        if self.animate:
            result.setPixelColor(0, 0, QColor(self.count % 255, 0, 0))
        if self.late_movement:
            self.offset = min(480, self.offset + 1)
        return result


def transaction(target: Target) -> ScrollTransaction:
    """Avoid wall-clock sleeps in deterministic tests."""
    return ScrollTransaction(target, lambda: False, pause=lambda _seconds: None)


def test_complete_pixels_and_restore_horizontal_and_vertical() -> None:
    """A top-to-bottom result exactly matches the source, starting mid-document."""
    target = Target()
    initial = target.position()
    result = transaction(target).run()
    assert result.image == target.document
    assert result.frames == 9
    assert result.restoration == "restored"
    assert result.message == ""
    assert target.position() == initial
    assert target.moves[0] == 0.0
    assert target.moves[-2] == 100.0


def test_partial_bottom_overlap() -> None:
    """The final movement may be shorter than half a viewport."""
    source = document(287)
    stitch = VerticalStitch(source.copy(0, 0, 80, 120))
    previous = 0
    for y in (60, 120, 167):
        stitch.append(source.copy(0, y, 80, 120), y - previous)
        previous = y
    assert stitch.finish() == source


@pytest.mark.parametrize("failure", ["repeat", "gap", "size", "fixed_sidebar"])
def test_ambiguous_or_incomplete_pixels_are_rejected(failure: str) -> None:
    """Reject guesses and preserve every column instead of trimming sidebars."""
    source = document()
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 60, 80, 120)
    if failure == "repeat":
        first.fill(QColor("white"))
        second.fill(QColor("white"))
    elif failure == "gap":
        second = source.copy(0, 180, 80, 120)
    elif failure == "size":
        second = second.copy(0, 0, 70, 120)
    else:
        for y in range(120):
            first.setPixelColor(0, y, QColor(y, 0, 0))
            second.setPixelColor(0, y, QColor(y, 0, 0))
    with pytest.raises(CaptureError):
        VerticalStitch(first).append(second, 60.0)


def test_null_frame_rejected() -> None:
    """A failed pixel capture cannot become an empty success."""
    with pytest.raises(CaptureError):
        VerticalStitch(QImage())


@pytest.mark.parametrize(
    "failure",
    ["fail_move", "ignore_move", "mutate_horizontal", "animate", "fail_frame"],
)
def test_failure_never_returns_partial_image(failure: str) -> None:
    """Native/provider failures publish no partial screenshot."""
    target = Target()
    setattr(target, failure, True)
    result = transaction(target).run()
    assert result.image is None
    assert result.message


def test_user_input_stops_without_restoring_over_user() -> None:
    """Input after the first page prevents any later scroll, including restore."""
    target = Target()

    def interrupt(_seconds: float) -> None:
        target.input_changed = True

    result = ScrollTransaction(target, lambda: False, pause=interrupt).run()
    assert result.image is None
    assert target.moves == [0.0]
    assert result.restoration.startswith("not restored")


def test_cooperative_cancel_restores_when_user_state_unchanged() -> None:
    """An explicit parent cancellation can restore without reclaiming input."""
    target = Target()
    initial = target.position()
    cancelled = False

    def interrupt(_seconds: float) -> None:
        nonlocal cancelled
        cancelled = target.count > 0

    result = ScrollTransaction(target, lambda: cancelled, pause=interrupt).run()
    assert result.image is None
    assert result.restoration == "restored"
    assert target.position() == initial
    assert len(target.moves) == 2


def test_cancel_before_start_changes_nothing() -> None:
    """Cancellation before discovery makes no provider mutation."""
    target = Target()
    result = ScrollTransaction(target, lambda: True).run()
    assert result.image is None
    assert result.restoration == "unchanged"
    assert target.moves == []


def test_deadline_does_not_attempt_late_restore() -> None:
    """An expired deadline does not start another potentially blocking call."""
    target = Target()
    now = 0.0

    def expire(_seconds: float) -> None:
        nonlocal now
        now = 30.0

    result = ScrollTransaction(
        target, lambda: False, clock=lambda: now, pause=expire
    ).run()
    assert result.image is None
    assert target.moves == [0.0]
    assert result.restoration == "not restored: time limit"


def test_non_scrolling_target_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported targets are rejected before movement."""
    target = Target()
    initial = replace(target.position(), view_size=100.0)
    monkeypatch.setattr(target, "position", lambda: initial)
    result = transaction(target).run()
    assert result.image is None
    assert target.moves == []


def test_provider_jump_is_rejected_before_stitching() -> None:
    """A provider cannot advance much farther than the requested half viewport."""
    target = Target()
    target.jump = True
    result = transaction(target).run()
    assert result.image is None
    assert "unexpectedly" in result.message


def test_spontaneous_movement_during_capture_prevents_restore() -> None:
    """Don't reset a target whose position changed independently of our writes."""
    target = Target()
    target.late_movement = True
    result = transaction(target).run()
    assert result.image is None
    assert "during capture" in result.message
    assert target.moves == [0.0]
    assert result.restoration.startswith("not restored")


def test_output_pixel_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop before allocating an oversized composite."""
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_PIXELS", 10_000)
    target = Target()
    result = transaction(target).run()
    assert result.image is None
    assert "size limit" in result.message
    assert result.restoration == "restored"


def test_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A huge or effectively infinite panel cannot consume unlimited frames."""
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 2)
    result = transaction(Target()).run()
    assert result.image is None
    assert "page limit" in result.message
    assert result.frames == 2


def test_restore_failure_withholds_complete_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completing pixels alone is not success when restoration is uncertain."""
    target = Target()
    move = target.move

    def refuse_restore(percent: float) -> None:
        if len(target.moves) >= 9:
            target.moves.append(percent)
        else:
            move(percent)

    monkeypatch.setattr(target, "move", refuse_restore)
    result = transaction(target).run()
    assert result.frames == 9
    assert result.image is None
    assert result.restoration.startswith("not restored")


def test_input_after_complete_restore_invalidates_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A result pending final publication still belongs to the cancelled request."""
    target = Target()
    operation = transaction(target)
    restore = operation._restore

    def interrupt_after_restore(initial: ScrollPosition) -> str:
        outcome = restore(initial)
        target.input_changed = True
        return outcome

    monkeypatch.setattr(operation, "_restore", interrupt_after_restore)
    result = operation.run()
    assert result.image is None
    assert "User input" in result.message


class DelayedTarget(Target):
    """A public UIA action returns before the provider's pixels/state change."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: float | None = None
        self.polls = 0

    @override
    def move(self, vertical: float) -> None:
        """Queue one mutation; a second pending write is always a test failure."""
        assert self.pending is None
        self.moves.append(vertical)
        self.pending = vertical
        self.polls = 0

    def pause(self, _seconds: float) -> None:
        """Resolve after several stale samples, including during restoration."""
        if self.pending is not None:
            self.polls += 1
            if self.polls == 3:
                self.offset = round(480 * self.pending / 100)
                self.pending = None


def test_delayed_scroll_and_restore_are_observed_without_reissuing() -> None:
    """Real-app style asynchronous writes still produce one complete image."""
    target = DelayedTarget()
    initial = target.position()
    result = ScrollTransaction(target, lambda: False, pause=target.pause).run()
    assert result.image == target.document
    assert result.restoration == "restored"
    assert target.position() == initial
    assert len(target.moves) == 10


@pytest.mark.parametrize("interruption", ["never", "cancel", "input", "deadline"])
def test_unresolved_move_never_becomes_restore_eligible(interruption: str) -> None:
    """Timeout or cancellation cannot send a second write over an unresolved one."""
    target = DelayedTarget()
    cancelled = False
    now = 0.0

    def pause(_seconds: float) -> None:
        nonlocal cancelled, now
        cancelled = interruption == "cancel"
        target.input_changed = interruption == "input"
        if interruption == "deadline":
            now = 30.0

    result = ScrollTransaction(
        target, lambda: cancelled, clock=lambda: now, pause=pause
    ).run()
    assert result.image is None
    assert target.moves == [0.0]
    assert target.pending == 0.0
    assert result.restoration.startswith("not restored")


def test_input_during_delayed_restore_withholds_image() -> None:
    """A restore in progress must stop observing success after user interference."""
    target = DelayedTarget()

    def pause(seconds: float) -> None:
        if len(target.moves) == 10:
            target.input_changed = True
        else:
            target.pause(seconds)

    result = ScrollTransaction(target, lambda: False, pause=pause).run()
    assert result.image is None
    assert result.restoration.startswith("not restored")
    assert len(target.moves) == 10


def test_programmatic_movement_between_frames_prevents_next_write() -> None:
    """An unexpected position after a frame is not attributed to the next action."""
    target = Target()
    operation = transaction(target)
    operation._expected = target.position()
    target.offset += 1
    with pytest.raises(CaptureError, match="changed position"):
        operation._move(0.0)
    assert target.moves == []


@pytest.mark.parametrize("offset", [0, 480])
def test_noop_endpoint_writes_are_never_sent(offset: int) -> None:
    """Already-top starts and already-restored ends need no pending no-op action."""
    target = DelayedTarget()
    target.offset = offset
    initial = target.position()
    result = ScrollTransaction(target, lambda: False, pause=target.pause).run()
    assert result.image == target.document
    assert result.restoration == "restored"
    assert target.position() == initial
    assert len(target.moves) == 9
    assert target.moves[0] != initial.vertical
    assert target.moves[-1] == initial.vertical


def test_input_during_final_restore_read_is_not_reported_as_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a failed capture rechecks eligibility after its final provider read."""
    target = Target()
    target.fail_frame = True
    position = target.position
    restore_reads = 0

    def interrupt() -> ScrollPosition:
        nonlocal restore_reads
        current = position()
        if len(target.moves) == 2:
            restore_reads += 1
            if restore_reads == 2:
                target.input_changed = True
        return current

    monkeypatch.setattr(target, "position", interrupt)
    result = transaction(target).run()
    assert result.image is None
    assert result.message == "Capture failed"
    assert result.restoration.startswith("not restored")
    assert len(target.moves) == 2


def test_already_restored_state_still_rechecks_user_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping a no-op write must not skip the last ownership observation."""
    target = Target()
    initial = target.position()
    operation = transaction(target)
    operation._moved = True
    operation._expected = initial

    def interrupt() -> ScrollPosition:
        target.input_changed = True
        return initial

    monkeypatch.setattr(target, "position", interrupt)
    assert operation._restore(initial).startswith("not restored")
    assert target.moves == []


def test_native_diagnostics_keep_only_two_accepted_frames(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The test wrapper observes the same transaction without changing its result."""
    target = Target()
    results = []
    original = ScrollTransaction._frame
    monkeypatch.setattr(
        "sys.argv", ["-c", "10", "20", "scratch", str(tmp_path), "edge"]
    )
    monkeypatch.setattr(
        "scripts.simple_scroll_native.helper_main",
        lambda: results.append(transaction(target).run()),
    )
    _trace_helper()
    assert len(results) == 1
    assert results[0].image == target.document
    assert results[0].restoration == "restored"
    assert ScrollTransaction._frame is original
    assert QImage(str(tmp_path / "frame-0.png")) == target.document.copy(0, 0, 80, 120)
    assert QImage(str(tmp_path / "frame-1.png")) == target.document.copy(0, 60, 80, 120)
    assert len(list(tmp_path.glob("*.png"))) == 2
    observations = json.loads((tmp_path / "frames.json").read_text())
    assert [item["vertical"] for item in observations] == [0.0, 12.5]


def test_interior_seams_recover_content_under_bottom_chrome() -> None:
    """Every content pixel survives several joins and a short final movement."""
    source = document(367)
    bar = QImage(80, 8, QImage.Format.Format_RGB32)
    bar.fill(QColor("gray"))

    def frame(offset: int) -> QImage:
        result = QImage(80, 120, QImage.Format.Format_RGB32)
        painter = QPainter(result)
        painter.drawImage(0, 0, source.copy(0, offset, 80, 112))
        painter.drawImage(0, 112, bar)
        painter.end()
        return result

    stitch = VerticalStitch(frame(0))
    previous = 0
    for offset in (56, 112, 168, 224, 255):
        stitch.append(frame(offset), offset - previous)
        previous = offset
    result = stitch.finish()
    assert result.size().width() == source.width()
    assert result.height() == source.height() + bar.height()
    assert result.copy(0, 0, 80, 367) == source
    assert result.copy(0, 367, 80, 8) == bar


def test_broad_fixed_header_is_rejected_despite_interior_match(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An exact interior seam cannot recover first rows hidden at every position."""
    source = document()
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 60, 80, 120)
    for frame in (first, second):
        for y in range(8):
            for x in range(80):
                frame.setPixelColor(x, y, QColor("white"))
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)
    assert "changed_pixels=640 compared_pixels=2560" in caplog.text


def test_one_meaningful_edge_column_is_not_masked_for_alignment() -> None:
    """A narrow changing or fixed sidebar cannot be discarded to find a shift."""
    source = document()
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 60, 80, 120)
    for y in range(120):
        first.setPixelColor(0, y, QColor(y, 255, 255))
        second.setPixelColor(0, y, QColor(y, 255, 255))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first).append(second, 60.0)


def test_repeated_textured_bands_have_no_unique_displacement() -> None:
    """Do not use UIA proximity to select one arbitrary repeated-content shift."""
    period = document(40)
    source = QImage(80, 240, QImage.Format.Format_RGB32)
    painter = QPainter(source)
    for y in range(0, 240, 40):
        painter.drawImage(0, y, period)
    painter.end()
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(source.copy(0, 0, 80, 120)).append(
            source.copy(0, 60, 80, 120), 60.0
        )


class ReportedTarget(Target):
    """A provider with self-consistent percentages but incompatible pixel extent."""

    def __init__(self, total: int, view: float) -> None:
        super().__init__(total=total, offset=0)
        self.percent = 0.0
        self.view = view

    @override
    def position(self) -> ScrollPosition:
        return ScrollPosition(self.horizontal, self.percent, self.view)

    @override
    def move(self, vertical: float) -> None:
        self.moves.append(vertical)
        self.percent = vertical
        self.offset = round((self.document.height() - 120) * vertical / 100)


def test_registered_advance_must_agree_with_uia_extent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The review's reported 240px / observed 296px reproduction must fail."""
    caplog.set_level("INFO")
    result = transaction(ReportedTarget(296, 50.0)).run()
    assert result.image is None
    assert "disagree with its scroll position" in result.message
    assert result.restoration == "restored"
    assert "viewport_height=120 view_size=50.000000" in caplog.text
    assert "from_vertical=0.000000 to_vertical=50.000000" in caplog.text
    assert "measured_advance=88 expected_advance=60.000" in caplog.text


def test_accumulated_small_displacements_must_agree_with_total_extent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Several tolerated 2px differences cannot silently expand the document."""
    result = transaction(ReportedTarget(616, 20.0)).run()
    assert result.frames == 9
    assert result.image is None
    assert "disagree with the panel's extent" in result.message
    assert result.restoration == "restored"
    assert "measured_height=616 expected_height=600.000" in caplog.text


class LineQuantizedTarget(Target):
    """The measured Notepad geometry: 601px viewport, 33 of 120 visible lines."""

    area = Rect(10, 20, 80, 601)

    def __init__(self) -> None:
        super().__init__(total=2167, offset=576)

    @override
    def position(self) -> ScrollPosition:
        return ScrollPosition(self.horizontal, self.offset / 1566 * 100, 27.5)

    @override
    def move(self, vertical: float) -> None:
        self.moves.append(vertical)
        self.offset = 18 * round(87 * vertical / 100)

    @override
    def frame(self) -> QImage:
        return self.document.copy(0, self.offset, 80, 601)


def test_observed_notepad_line_rounding_preserves_all_pixels() -> None:
    """A small UIA line-count quantization is accepted without fitting the image."""
    target = LineQuantizedTarget()
    initial = target.position()
    result = transaction(target).run()
    assert result.image == target.document
    assert result.restoration == "restored"
    assert target.position() == initial
