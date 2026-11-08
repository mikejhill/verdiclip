"""Portable contracts for the isolated scrolling feasibility slice."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import override

import pytest
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from scripts.simple_scroll_native import _trace_helper

from verdiclip.capture.scrolling import (
    MAX_SECONDS,
    ScrollBarState,
    ScrollPosition,
    ScrollTransaction,
    VerticalStitch,
    _HeaderFrames,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


def document(height: int = 600, *, width: int = 80) -> QImage:
    """Make unique rows whose exact final pixels can be compared."""
    result = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(result.width()):
            result.setPixelColor(x, y, QColor((y + x) % 251, y // 251, x % 251))
    return result


class Target:
    """Finite document with a viewport and user-observable scroll state."""

    area = Rect(10, 20, 80, 120)
    header: Rect | None = None
    registration_dpi: int | None = None

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

    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Record writes, including failed writes with uncertain side effects."""
        if before_write is not None:
            before_write()
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


class HeaderTarget(Target):
    """A verified25px Header with separately identified17px scrollbar corner."""

    area = Rect(10, 20, 80, 145)
    header: Rect | None = Rect(10, 20, 63, 25)

    def __init__(self) -> None:
        super().__init__()
        self.change_header = False

    @override
    def frame(self) -> QImage:
        """Expose full-width body pixels and a moving scrollbar beside the Header."""
        body = super().frame()
        image = QImage(80, 145, QImage.Format.Format_RGB32)
        image.fill(QColor(0, 80, 160))
        painter = QPainter(image)
        painter.fillRect(63, 0, 17, 25, QColor(self.offset % 251, 80, 160))
        painter.drawImage(0, 25, body)
        painter.end()
        if self.change_header and self.offset:
            image.setPixelColor(5, 5, QColor("red"))
        return image


def test_verified_header_is_retained_once_with_every_full_width_body_pixel() -> None:
    """Verified geometry changes displacement/body extent, never source width."""
    target = HeaderTarget()
    initial = target.position()
    target.offset = 0
    first = target.frame()
    target.offset = 173
    result = transaction(target).run()
    expected = QImage(80, 625, QImage.Format.Format_RGB32)
    painter = QPainter(expected)
    painter.drawImage(0, 0, first.copy(0, 0, 80, 25))
    painter.drawImage(0, 25, target.document)
    painter.end()
    assert result.image == expected
    assert result.frames == 9
    assert result.restoration == "restored"
    assert target.position() == initial
    assert target.moves[-2:] == [100.0, initial.vertical]


def test_any_changed_header_pixel_rejects_and_restores() -> None:
    """Even a single changed actual Header pixel cannot be silently frozen."""
    target = HeaderTarget()
    target.change_header = True
    initial = target.position()
    result = transaction(target).run()
    assert result.image is None
    assert "header changed" in result.message
    assert result.restoration == "restored"
    assert target.position() == initial


def test_fixed_pixels_without_header_metadata_are_not_cropped() -> None:
    """The old conservative path still rejects unproved fixed chrome."""
    target = HeaderTarget()
    target.header = None
    result = transaction(target).run()
    assert result.image is None
    assert result.restoration == "restored"


def test_header_counts_against_existing_output_pixel_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body fitting the budget cannot hide a prefix that exceeds the limit."""
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_PIXELS", 80 * 620)
    target = HeaderTarget()
    result = transaction(target).run()
    assert result.image is None
    assert "size limit" in result.message
    assert result.restoration == "restored"


@pytest.mark.parametrize(
    "header",
    [
        Rect(11, 20, 63, 25),
        Rect(10, 21, 63, 25),
        Rect(10, 20, 81, 25),
        Rect(10, 20, 0, 25),
        Rect(10, 20, 63, 0),
        Rect(10, 20, 63, 145),
        Rect(10, 20, 63.5, 25),
        Rect(10, 20, 63, 25.5),
    ],
)
def test_header_frame_boundary_rejects_inconsistent_geometry(header: Rect) -> None:
    """Only exact integer pixel rectangles can establish the retained prefix."""
    first = QImage(80, 145, QImage.Format.Format_RGB32)
    with pytest.raises(CaptureError, match="geometry is inconsistent"):
        _HeaderFrames(first, Rect(10, 20, 80, 145), header)


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
        now = operation._deadline

    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now, pause=expire
    )
    result = operation.run()
    assert result.image is None
    assert target.moves == [0.0]
    assert result.restoration == "not restored: time limit"


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("restoring", [False, True])
@pytest.mark.parametrize("interrupt", ["none", "cancel", "deadline"])
def test_poll_extraction_preserves_generic_guard_order(
    monkeypatch: pytest.MonkeyPatch,
    interrupt: str,
    *,
    full: bool,
    restoring: bool,
) -> None:
    """The cheap callback adds no target read; full guards keep their exact order."""
    events: list[str] = []

    def cancelled() -> bool:
        events.append("cancel")
        return interrupt == "cancel"

    def clock() -> float:
        events.append("clock")
        return now

    target, now = Target(), 0.0
    operation = ScrollTransaction(target, cancelled, clock=clock)
    monkeypatch.setattr(target, "check", lambda: events.append("target"))
    events.clear()
    if interrupt == "deadline":
        now = operation._deadline
    guard = operation._check if full else operation._poll
    if interrupt == "cancel" and not restoring:
        with pytest.raises(CaptureError, match="cancelled"):
            guard(restoring=restoring)
        assert events == ["cancel"]
    elif interrupt == "deadline":
        with pytest.raises(CaptureError, match="time limit"):
            guard(restoring=restoring)
        assert events == ([] if restoring else ["cancel"]) + ["clock"]
    else:
        guard(restoring=restoring)
        assert events == (
            ([] if restoring else ["cancel"])
            + ["clock"]
            + (
                ["target"] + ([] if restoring else ["cancel"]) + ["clock"]
                if full
                else []
            )
        )
    assert not target.moves


def test_full_guard_keeps_original_target_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling does not swallow, replace or retry failed target validation."""
    target = Target()
    calls: list[str] = []
    failure = CaptureError("Target validation failed")

    def reject() -> None:
        calls.append("target")
        raise failure

    monkeypatch.setattr(target, "check", reject)
    operation = transaction(target)
    with pytest.raises(CaptureError) as raised:
        operation._check()
    assert raised.value is failure
    assert calls == ["target"]
    assert not target.moves


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


@pytest.mark.parametrize("restoring", [False, True])
@pytest.mark.parametrize("changed", ["horizontal", "view_size"])
def test_settle_logs_exact_state_change_without_more_reads(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    changed: str,
    *,
    restoring: bool,
) -> None:
    """An endpoint rejection identifies H or extent without accepting the write."""
    target = Target()
    before = ScrollPosition(-1.0, 94.24470555125589, 11.169448090505657)
    observed = replace(before, vertical=100.0, **{changed: 0.0})
    reads: list[ScrollPosition] = []
    pauses: list[float] = []

    def position() -> ScrollPosition:
        reads.append(observed)
        return observed

    monkeypatch.setattr(target, "position", position)
    operation = ScrollTransaction(target, lambda: False, pause=pauses.append)
    with pytest.raises(CaptureError, match="moved unexpectedly"):
        operation._settle(
            before,
            100.0,
            restoring=replace(before, vertical=100.0) if restoring else None,
        )
    assert reads == [observed]
    assert pauses == [0.08]
    assert target.moves == []
    assert "stage=state-change" in caplog.text
    assert f"restoring={restoring} requested_percent=100.0" in caplog.text
    assert f"before={before} observed={observed}" in caplog.text


@pytest.mark.parametrize("overshoot", [False, True])
def test_settle_logs_overshoot_or_unsettled_with_unchanged_poll_budget(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    overshoot: bool,
) -> None:
    """Diagnostic warnings distinguish rejected movement from a pending write."""
    target = Target()
    before = ScrollPosition(-1.0, 20.0, 20.0)
    observed = replace(before, vertical=40.0) if overshoot else before
    monkeypatch.setattr(target, "position", lambda: observed)
    pauses: list[float] = []
    operation = ScrollTransaction(target, lambda: False, pause=pauses.append)
    with pytest.raises(CaptureError):
        operation._settle(before, 30.0)
    assert pauses == [0.08] * (1 if overshoot else 8)
    assert target.moves == []
    assert f"stage={'overshoot' if overshoot else 'unsettled'}" in caplog.text
    assert "restoring=False requested_percent=30.0" in caplog.text
    assert f"before={before} observed={observed}" in caplog.text


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

    def refuse_restore(
        percent: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
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
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        """Queue one mutation; a second pending write is always a test failure."""
        if before_write is not None:
            before_write()
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
            now = operation._deadline

    operation = ScrollTransaction(
        target, lambda: cancelled, clock=lambda: now, pause=pause
    )
    result = operation.run()
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


def outlined_frame(
    source: QImage, offset: int, *, left: int = 2, right: int = 2
) -> QImage:
    """Model the native panel's uniform changing focus outline, including corners."""
    result = source.copy(0, offset, source.width(), 120)
    color = QColor("gray") if offset == 0 else QColor("black")
    for y in range(result.height()):
        for x in (*range(left), *range(result.width() - right, result.width())):
            result.setPixelColor(x, y, color)
    return result


@pytest.mark.parametrize(("left", "right"), [(1, 0), (0, 1), (2, 1), (2, 2)])
def test_changed_uniform_edges_preserve_entire_image_at_exact_seam(
    left: int, right: int, caplog: pytest.LogCaptureFixture
) -> None:
    """Registration ignores only measured edge columns; output retains them all."""
    caplog.set_level("INFO")
    source = document(180, width=120)
    first = outlined_frame(source, 0, left=left, right=right)
    second = outlined_frame(source, 60, left=left, right=right)
    # Corner deviations are confined to the same measured edge columns.
    for frame in (first, second):
        for y in (*range(8), *range(112, 120)):
            for x in (*range(left), *range(120 - right, 120)):
                frame.setPixelColor(x, y, QColor(y, x, 255))
    stitch = VerticalStitch(first)
    stitch.append(second, 60.0)
    expected = QImage(120, 180, QImage.Format.Format_RGB32)
    painter = QPainter(expected)
    painter.drawImage(0, 0, first.copy(0, 0, 120, 90))
    painter.drawImage(0, 90, second.copy(0, 30, 120, 90))
    painter.end()
    assert stitch.finish() == expected
    assert f"left_columns={left} right_columns={right}" in caplog.text
    assert "measured_advance=60 expected_advance=60.000" in caplog.text
    assert "matching_band=(0,60)" in caplog.text


def test_edge_fallback_keeps_all_interior_pixels_through_short_final_join() -> None:
    """Repeated joins retain all source rows and columns, without padding a seam."""
    source = document(367, width=120)
    first = outlined_frame(source, 0)
    stitch = VerticalStitch(first)
    previous = 0
    for offset in (60, 120, 180, 240, 247):
        stitch.append(outlined_frame(source, offset), offset - previous)
        previous = offset
    expected = source.copy()
    painter = QPainter(expected)
    for x in (0, 118):
        painter.fillRect(x, 0, 2, 90, QColor("gray"))
        painter.fillRect(x, 90, 2, 277, QColor("black"))
    painter.end()
    assert stitch.finish() == expected


def test_unchanged_uniform_adjacent_column_is_not_part_of_edge_mask(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The native panel's third gray border column stays in registration."""
    caplog.set_level("INFO")
    source = document(180, width=120)
    painter = QPainter(source)
    for x in (0, 117):
        painter.fillRect(x, 0, 3, 180, QColor("gray"))
    painter.end()
    first, second = outlined_frame(source, 0), outlined_frame(source, 60)
    stitch = VerticalStitch(first)
    stitch.append(second, 60.0)
    expected = source.copy()
    painter = QPainter(expected)
    for x in (0, 118):
        painter.fillRect(x, 90, 2, 90, QColor("black"))
    painter.end()
    assert stitch.finish() == expected
    assert "left_columns=2 right_columns=2" in caplog.text


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("width", [3, 16])
def test_wide_changed_solid_edges_are_not_partially_masked(
    side: str, width: int
) -> None:
    """The hard cap rejects the entire wider strip, not just its first columns."""
    source = document(width=120)
    left, right = (width, 0) if side == "left" else (0, width)
    first = outlined_frame(source, 0, left=left, right=right)
    second = outlined_frame(source, 60, left=left, right=right)
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first).append(second, 60.0)


@pytest.mark.parametrize("column", [0, 1, 2, 117, 118, 119])
@pytest.mark.parametrize("frame_number", [0, 1])
def test_textured_edge_or_adjacent_column_is_never_discarded(
    column: int, frame_number: int
) -> None:
    """Meaningful edge pixels and the first interior column must still agree."""
    source = document(width=120)
    frames = [outlined_frame(source, 0), outlined_frame(source, 60)]
    for y in range(120):
        frames[frame_number].setPixelColor(column, y, QColor(255, y, 255))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(frames[0]).append(frames[1], 60.0)


@pytest.mark.parametrize("row", [8, 111])
def test_edge_corner_exception_does_not_extend_into_interior(row: int) -> None:
    """Even one nonuniform pixel beyond the bounded corners invalidates a strip."""
    source = document(width=120)
    first, second = outlined_frame(source, 0), outlined_frame(source, 60)
    second.setPixelColor(0, row, QColor("red"))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first).append(second, 60.0)


def test_changed_edges_do_not_hide_a_broad_fixed_header() -> None:
    """The top guard still examines original full-width pixels after fallback."""
    source = document(width=120)
    first, second = outlined_frame(source, 0), outlined_frame(source, 60)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(2, 0, 116, 8, QColor("white"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)


def test_top_border_without_matching_side_evidence_is_rejected(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Uniform top rows alone are not evidence of a connected narrow perimeter."""
    caplog.set_level("INFO")
    source = document(width=771)
    first, second = outlined_frame(source, 0), outlined_frame(source, 60)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(0, 1, 771, 2, QColor("black"))
        painter.fillRect(0, 3, 771, 1, QColor("gray"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)
    assert "left_columns=2 right_columns=2" in caplog.text
    assert "measured_advance=60 expected_advance=60.000" in caplog.text


def perimeter_frame(source: QImage, offset: int) -> QImage:
    """Model the observed three-column border with a two-pixel focus outline."""
    result = source.copy(0, offset, source.width(), 120)
    painter = QPainter(result)
    for x in (0, source.width() - 3):
        painter.fillRect(x, 0, 3, 120, QColor("gray"))
    painter.fillRect(0, 1, source.width(), 3, QColor("gray"))
    if offset:
        for x in (0, source.width() - 2):
            painter.fillRect(x, 0, 2, 120, QColor("black"))
        painter.fillRect(0, 1, source.width(), 2, QColor("black"))
    painter.end()
    return result


def scaled_perimeter_frame(
    source: QImage, offset: int, percent: int, *, inset: int | None = None
) -> QImage:
    """Model scaled 2px outlines and 3px borders, not a native DPI observation."""
    result = source.copy(0, offset, source.width(), 120)
    border = (3 * percent + 99) // 100
    outline = (2 * percent + 99) // 100
    top = (percent + 99) // 100 if inset is None else inset
    gray, black = QColor(177, 181, 186), QColor(16, 16, 16)
    painter = QPainter(result)
    for x in (0, source.width() - border):
        painter.fillRect(x, 0, border, 120, gray)
    painter.fillRect(0, top, source.width(), border, gray)
    if offset:
        for x in (0, source.width() - outline):
            painter.fillRect(x, 0, outline, 120, black)
        painter.fillRect(0, top, source.width(), outline, black)
        # At 125%, model the 2.5px outline's last changed column at half coverage.
        # This is an illustrative rasterization, not a browser rendering oracle.
        fraction = 2 * percent % 100
        if fraction:
            blend = QColor(
                *[
                    round((component * (100 - fraction) + 16 * fraction) / 100)
                    for component in (177, 181, 186)
                ]
            )
            for x in (outline - 1, source.width() - outline):
                painter.fillRect(x, 0, 1, 120, blend)
            painter.fillRect(0, top + outline - 1, source.width(), 1, blend)
    painter.end()
    return result


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
def test_synthetic_scaled_outline_bounds_are_explicit(percent: int) -> None:
    """Only the 2-column model passes; wider scaled outlines remain unsupported."""
    source = document(180, width=120)
    first = scaled_perimeter_frame(source, 0, percent)
    second = scaled_perimeter_frame(source, 60, percent)
    stitch = VerticalStitch(first)
    if percent != 100:
        with pytest.raises(CaptureError, match="cannot be joined"):
            stitch.append(second, 60.0)
        return
    stitch.append(second, 60.0)
    expected = QImage(120, 180, QImage.Format.Format_RGB32)
    painter = QPainter(expected)
    painter.drawImage(0, 0, first.copy(0, 0, 120, 92))
    painter.drawImage(0, 92, second.copy(0, 32, 120, 88))
    painter.end()
    assert stitch.finish() == expected


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
def test_synthetic_scaled_outline_does_not_hide_textured_edge(percent: int) -> None:
    """Scale-labelled fixtures still reject meaningful fixed outer-column pixels."""
    source = document(width=120 * percent // 100)
    first = scaled_perimeter_frame(source, 0, percent, inset=percent // 100)
    second = scaled_perimeter_frame(source, 60, percent, inset=percent // 100)
    for frame in (first, second):
        for y in range(120):
            frame.setPixelColor(0, y, QColor(255, y, 255))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first, dpi=96 * percent // 100).append(second, 60.0)


class ScaledPerimeterTarget(Target):
    """A verified-DPI provider whose content and chrome have a complete pixel oracle."""

    def __init__(self, percent: int) -> None:
        super().__init__()
        self.percent = percent
        self.registration_dpi = 96 * percent // 100
        self.area = Rect(10, 20, 120 * percent // 100, 120)
        self.document = document(width=round(self.area.width))

    @override
    def frame(self) -> QImage:
        return scaled_perimeter_frame(
            self.document, self.offset, self.percent, inset=self.percent // 100
        )


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
def test_verified_dpi_scales_bounds_without_changing_complete_output(
    percent: int,
) -> None:
    """Every output pixel is checked across the entire DPI-aware transaction."""
    target = ScaledPerimeterTarget(percent)
    initial = target.position()
    result = transaction(target).run()
    width = target.document.width()
    border = (3 * percent + 99) // 100
    top_end = percent // 100 + border
    first_end = 60 + (top_end + 60) // 2
    first = scaled_perimeter_frame(target.document, 0, percent, inset=percent // 100)
    focused = scaled_perimeter_frame(target.document, 60, percent, inset=percent // 100)
    expected = target.document.copy()
    painter = QPainter(expected)
    painter.drawImage(0, 0, first.copy(0, 0, width, first_end))
    for x in (*range(border), *range(width - border, width)):
        painter.fillRect(x, first_end, 1, 600 - first_end, focused.pixelColor(x, 60))
    painter.end()
    assert result.image == expected
    assert result.frames == 9
    assert result.restoration == "restored"
    assert target.position() == initial


@pytest.mark.parametrize("dpi", [0, 95, 193, 240])
def test_unqualified_dpi_cannot_expand_a_stitch_bound(dpi: int) -> None:
    """Native unverified values must be normalized to unknown, not passed through."""
    with pytest.raises(CaptureError, match="DPI is unsupported"):
        VerticalStitch(document(), dpi=dpi)


@pytest.mark.parametrize("percent", [125, 150])
def test_fractional_top_placement_beyond_proportional_cap_stays_unsupported(
    percent: int,
) -> None:
    """Separate inset/border rounding is not permission to add another pixel."""
    source = document(width=120 * percent // 100)
    first = scaled_perimeter_frame(source, 0, percent)
    second = scaled_perimeter_frame(source, 60, percent)
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first, dpi=96 * percent // 100).append(second, 60.0)


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
@pytest.mark.parametrize("side", ["left", "right"])
def test_scaled_registration_cap_rejects_one_extra_uniform_column(
    percent: int, side: str
) -> None:
    """Ceil(bound * scale), not bound * ceil(scale), limits every fallback."""
    source = document(width=120 * percent // 100)
    too_wide = (2 * percent + 99) // 100 + 1
    left, right = (too_wide, 0) if side == "left" else (0, too_wide)
    first = outlined_frame(source, 0, left=left, right=right)
    second = outlined_frame(source, 60, left=left, right=right)
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first, dpi=96 * percent // 100).append(second, 60.0)


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
@pytest.mark.parametrize("frame_number", [0, 1])
def test_scaled_fractional_edge_or_adjacent_color_is_not_discarded(
    percent: int, frame_number: int
) -> None:
    """The last changed/antialiased edge column must be uniform at every DPI."""
    source = document(width=120 * percent // 100)
    frames = [
        scaled_perimeter_frame(source, offset, percent, inset=percent // 100)
        for offset in (0, 60)
    ]
    column = (2 * percent + 99) // 100 - 1
    for y in range(120):
        frames[frame_number].setPixelColor(column, y, QColor(255, y, 255))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(frames[0], dpi=96 * percent // 100).append(frames[1], 60.0)


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
def test_scaled_top_corner_allowance_does_not_hide_meaningful_pixels(
    percent: int,
) -> None:
    """A single textured pixel beyond the scaled corner cap rejects the top mask."""
    source = document(width=120 * percent // 100)
    first = scaled_perimeter_frame(source, 0, percent, inset=percent // 100)
    second = scaled_perimeter_frame(source, 60, percent, inset=percent // 100)
    second.setPixelColor((8 * percent + 99) // 100, percent // 100, QColor("red"))
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first, dpi=96 * percent // 100).append(second, 60.0)


@pytest.mark.parametrize("percent", [100, 125, 150, 200])
def test_scaled_side_perimeter_does_not_recategorize_a_wide_sidebar(
    percent: int,
) -> None:
    """An unchanged solid side beyond the scaled cap is not top-perimeter proof."""
    source = document(width=120 * percent // 100)
    first = scaled_perimeter_frame(source, 0, percent, inset=percent // 100)
    second = scaled_perimeter_frame(source, 60, percent, inset=percent // 100)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect((3 * percent + 99) // 100, 0, 1, 120, QColor(177, 181, 186))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first, dpi=96 * percent // 100).append(second, 60.0)


def test_bounded_perimeter_retains_every_output_pixel_over_complete_sequence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The complete oracle covers all rows, side columns and every interior seam."""
    caplog.set_level("INFO")
    source = document(367, width=120)
    stitch = VerticalStitch(perimeter_frame(source, 0))
    previous = 0
    for offset in (60, 120, 180, 240, 247):
        stitch.append(perimeter_frame(source, offset), offset - previous)
        previous = offset
    expected = source.copy()
    painter = QPainter(expected)
    for x in (0, 117):
        painter.fillRect(x, 0, 3, 367, QColor("gray"))
    painter.fillRect(0, 1, 120, 3, QColor("gray"))
    for x in (0, 118):
        painter.fillRect(x, 92, 2, 275, QColor("black"))
    painter.end()
    assert stitch.finish() == expected
    assert "Scrolling top perimeter: rows=[1, 2, 3]" in caplog.text


@pytest.mark.parametrize("column", [8, 111])
@pytest.mark.parametrize("frame_number", [0, 1])
def test_textured_top_perimeter_is_not_masked(column: int, frame_number: int) -> None:
    """A meaningful pixel outside the corner allowance invalidates the top mask."""
    source = document(width=120)
    frames = [perimeter_frame(source, 0), perimeter_frame(source, 60)]
    frames[frame_number].setPixelColor(column, 1, QColor("red"))
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(frames[0]).append(frames[1], 60.0)


@pytest.mark.parametrize("frame_number", [0, 1])
def test_top_perimeter_corner_pixels_remain_in_the_output(frame_number: int) -> None:
    """The corner allowance never deletes original corner or other output pixels."""
    source = document(180, width=120)
    frames = [perimeter_frame(source, 0), perimeter_frame(source, 60)]
    for y in (1, 2, 3):
        for x in (*range(8), *range(112, 120)):
            frames[frame_number].setPixelColor(x, y, QColor(x, y, 255))
    stitch = VerticalStitch(frames[0])
    stitch.append(frames[1], 60.0)
    expected = QImage(120, 180, QImage.Format.Format_RGB32)
    painter = QPainter(expected)
    painter.drawImage(0, 0, frames[0].copy(0, 0, 120, 92))
    painter.drawImage(0, 92, frames[1].copy(0, 32, 120, 88))
    painter.end()
    assert stitch.finish() == expected


@pytest.mark.parametrize("height", [4, 8])
def test_top_perimeter_does_not_partially_mask_a_taller_solid_header(
    height: int,
) -> None:
    """A matching uniform row beyond the absolute four-row depth rejects the mask."""
    source = document(width=120)
    first, second = perimeter_frame(source, 0), perimeter_frame(source, 60)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(3, 1, 114, height, QColor("gray"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)


@pytest.mark.parametrize("side", ["left", "right"])
def test_top_perimeter_cannot_use_a_wider_solid_sidebar(side: str) -> None:
    """A side color continuing beyond the three-column cap is not perimeter proof."""
    source = document(width=120)
    first, second = perimeter_frame(source, 0), perimeter_frame(source, 60)
    x = 3 if side == "left" else 116
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(x, 0, 1, 120, QColor("gray"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)


def test_perimeter_retry_still_rejects_a_header_inside_the_border() -> None:
    """A real border cannot excuse broad fixed content immediately below it."""
    source = document(width=120)
    first, second = perimeter_frame(source, 0), perimeter_frame(source, 60)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(3, 4, 114, 8, QColor("white"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)


def test_perimeter_retry_keeps_five_percent_of_remaining_pixels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Omitted rows cannot increase the general mismatch budget or denominator."""
    source = document(width=120)
    first, second = perimeter_frame(source, 0), perimeter_frame(source, 60)
    for x in range(3, 62):
        second.setPixelColor(x, 4, QColor("red"))
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)
    assert "changed_pixels=175 compared_pixels=3480 limit_fraction=0.05" in caplog.text
    # One fewer changed pixel is exactly 5% of the remaining comparison.
    second.setPixelColor(61, 4, source.pixelColor(61, 64))
    VerticalStitch(first).append(second, 60.0)


def test_narrow_image_cannot_supply_top_perimeter_evidence() -> None:
    """No interior remains to prove uniformity inside the allowed corner widths."""
    source = document(width=16)
    first, second = source.copy(0, 0, 16, 120), source.copy(0, 60, 16, 120)
    for frame in (first, second):
        painter = QPainter(frame)
        painter.fillRect(0, 0, 16, 3, QColor("gray"))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first).append(second, 60.0)


def test_changed_edges_do_not_override_uia_displacement() -> None:
    """A matching interior cannot change the independently reported movement."""
    source = document(width=120)
    with pytest.raises(CaptureError, match="disagree with its scroll position"):
        VerticalStitch(outlined_frame(source, 0)).append(
            outlined_frame(source, 60), 70.0
        )


@pytest.mark.parametrize("change_edges", [False, True])
def test_edge_registration_preserves_global_ambiguity_rejection(
    caplog: pytest.LogCaptureFixture, *, change_edges: bool
) -> None:
    """Neither exact-first nor fallback matching can choose a repeated band."""
    caplog.set_level("INFO")
    source = document(180, width=120)
    painter = QPainter(source)
    for y in (40, 80, 120):
        painter.drawImage(0, y, source.copy(0, 0, 120, 40))
    painter.end()
    left = 2 if change_edges else 0
    first = outlined_frame(source, 0, left=left, right=0)
    second = outlined_frame(source, 60, left=left, right=0)
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(first).append(second, 60.0)
    assert "Scrolling registration edges" not in caplog.text


def test_exact_full_width_match_never_uses_edge_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Existing exact registration stays the first choice."""
    caplog.set_level("INFO")
    source = document(180)
    stitch = VerticalStitch(source.copy(0, 0, 80, 120))
    stitch.append(source.copy(0, 60, 80, 120), 60.0)
    assert stitch.finish() == source
    assert "Scrolling registration edges" not in caplog.text


def test_tiny_unmatchable_frame_is_rejected_without_edge_guessing() -> None:
    """A viewport shorter than the corner allowance cannot supply edge evidence."""
    source = document()
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(source.copy(0, 0, 80, 16)).append(source.copy(0, 8, 80, 16), 8.0)


@pytest.mark.parametrize("expected_advance", [60.0, None])
def test_repeated_textured_bands_have_no_unique_displacement(
    expected_advance: float | None,
) -> None:
    """Do not use UIA proximity to select one arbitrary repeated-content shift."""
    period = document(40)
    source = QImage(80, 240, QImage.Format.Format_RGB32)
    painter = QPainter(source)
    for y in range(0, 240, 40):
        painter.drawImage(0, y, period)
    painter.end()
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(source.copy(0, 0, 80, 120)).append(
            source.copy(0, 60, 80, 120), expected_advance
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
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
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
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
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


class EndpointTarget(HeaderTarget):
    """A fixed Header whose provider normalizes its view size only at the bottom."""

    def __init__(self, *, total: int = 600, terminal_size: float = 20.3) -> None:
        super().__init__()
        self.document = document(total)
        self.percent = 0.0
        self.offset = 0
        self.terminal_size = terminal_size

    @override
    def position(self) -> ScrollPosition:
        view = self.terminal_size if self.percent == 100.0 else 20.0
        return ScrollPosition(self.horizontal, self.percent, view)

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        self.percent = vertical
        # Individually admissible 1px rounding accumulates. The final jump is 67px,
        # not the old percentage prediction 60px; complete source extent is 600px.
        self.offset = (
            self.document.height() - 120
            if vertical == 100.0
            else round(480 * vertical / 100) - int(vertical / 12.5)
        )


@pytest.mark.parametrize("start", [0.0, 50.0])
def test_verified_terminal_normalization_restores_exact_initial_state(
    start: float,
) -> None:
    """Top/middle synthetic starts keep all rows, Header once and exact restoration."""
    target = EndpointTarget()
    first = target.frame()
    target.move(start)
    target.moves.clear()
    initial, pixels = target.position(), target.frame()
    result = transaction(target).run()
    assert result.message == ""
    assert result.restoration == "restored"
    assert result.image is not None
    assert result.image.copy(0, 0, 80, 25) == first.copy(0, 0, 80, 25)
    assert result.image.copy(0, 25, 80, 600) == target.document
    assert result.image.height() == 625
    assert target.position() == initial
    assert target.frame() == pixels
    assert target.moves[-2:] == [100.0, start]


def test_header_bottom_start_with_unchanged_metrics_captures_and_restores() -> None:
    """An ordinary bottom start needs no special initialization or extra restore."""
    target = HeaderTarget()
    target.offset = 480
    initial, pixels = target.position(), target.frame()
    result = transaction(target).run()
    assert result.image is not None
    assert result.image.height() == 625
    assert result.image.copy(0, 25, 80, 600) == target.document
    assert result.message == ""
    assert result.restoration == "restored"
    assert target.position() == initial
    assert target.frame() == pixels
    assert target.moves == [0.0, 12.5, 25.0, 37.5, 50.0, 62.5, 75.0, 87.5, 100.0]


def test_bottom_to_top_unexpected_metric_change_stays_unresolved() -> None:
    """Removing the draft preflight block does not accept changed start metrics."""
    target = EndpointTarget()
    target.move(100.0)
    target.moves.clear()
    result = transaction(target).run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert result.frames == 0
    assert target.moves == [0.0]


@pytest.mark.parametrize(
    "terminal_size", [0.0, -1.0, 100.0, float("nan"), float("inf"), 21.0]
)
def test_invalid_or_large_terminal_normalization_stays_unresolved(
    terminal_size: float,
) -> None:
    """Invalid metrics and out-of-budget extents never gain restore permission."""
    target = EndpointTarget(terminal_size=terminal_size)
    result = transaction(target).run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert target.moves[-1] == 100.0


@pytest.mark.parametrize("change", ["no-header", "interior", "horizontal"])
def test_endpoint_exception_does_not_apply_to_other_state_changes(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Only the verified Header's terminal V/read may change the view metric."""
    target = EndpointTarget()
    before = ScrollPosition(27.0, 87.5, 20.0)
    observed = ScrollPosition(28.0 if change == "horizontal" else 27.0, 100.0, 20.3)
    percent = 93.0 if change == "interior" else 100.0
    if change == "no-header":
        target.header = None
    monkeypatch.setattr(target, "position", lambda: observed)
    with pytest.raises(CaptureError, match="moved unexpectedly"):
        transaction(target)._settle(before, percent)
    assert target.moves == []


def test_unstable_terminal_metric_keeps_same_bounded_poll_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint candidate must become identical twice, not merely close."""
    target = EndpointTarget()
    before = ScrollPosition(27.0, 87.5, 20.0)
    reads = iter([ScrollPosition(27.0, 100.0, view) for view in (20.2, 20.3) * 4])
    monkeypatch.setattr(target, "position", lambda: next(reads))
    pauses: list[float] = []
    with pytest.raises(CaptureError, match="did not settle"):
        ScrollTransaction(target, lambda: False, pause=pauses.append)._settle(
            before, 100.0
        )
    assert pauses == [0.08] * 8
    assert target.moves == []


@pytest.mark.parametrize("failure", ["header", "content", "geometry", "input"])
def test_terminal_candidate_needs_unchanged_pixels_and_target(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A changed metric is not permission to accept changed content or ownership."""
    target = EndpointTarget()
    original_frame = target.frame
    original_check = target.check

    def frame() -> QImage:
        image = original_frame()
        if target.percent == 100.0:
            if failure == "header":
                image.setPixelColor(5, 5, QColor("red"))
            elif failure == "content":
                painter = QPainter(image)
                painter.fillRect(0, 25, 80, 120, QColor("white"))
                painter.end()
        return image

    def check() -> None:
        if target.percent == 100.0 and failure in ("geometry", "input"):
            raise CaptureError(f"Changed {failure}")
        original_check()

    monkeypatch.setattr(target, "frame", frame)
    monkeypatch.setattr(target, "check", check)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert result.restoration == "not restored: the provider write is unresolved"
    assert target.moves[-1] == 100.0
    assert operation._terminal is None
    assert operation._expected is None


@pytest.mark.parametrize(("total", "terminal_size"), [(614, 19.7), (610, 20.3)])
def test_terminal_pixels_must_agree_with_both_independent_extents(
    total: int, terminal_size: float
) -> None:
    """Each case passes one total bound and fails the other despite a unique join."""
    target = EndpointTarget(total=total, terminal_size=terminal_size)
    result = transaction(target).run()
    assert result.frames == 9
    assert result.image is None
    assert "disagree with the panel's extent" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert target.moves[-1] == 100.0


def test_final_eligibility_is_checked_before_confirming_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Input after extent proof still cannot create restoration ownership."""
    target = EndpointTarget()
    operation = transaction(target)
    validate = operation._validate_extent

    def interrupt(image: QImage, frames: _HeaderFrames, view: float) -> None:
        validate(image, frames, view)
        if view != 20.0:
            target.input_changed = True

    monkeypatch.setattr(operation, "_validate_extent", interrupt)
    result = operation.run()
    assert result.image is None
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._terminal is None
    assert operation._expected is None
    assert target.moves[-1] == 100.0


@pytest.mark.parametrize("change", ["horizontal", "view_size", "vertical"])
def test_normalized_restore_requires_exact_original_tuple(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A single restore write never succeeds with a different original metric."""
    target = EndpointTarget()
    read = target.position
    restoring = False
    move = target.move

    def incorrect_restore(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        nonlocal restoring
        restoring = target.percent == 100.0
        move(vertical)

    def position() -> ScrollPosition:
        current = read()
        return replace(current, **{change: 1.0}) if restoring else current

    monkeypatch.setattr(target, "move", incorrect_restore)
    monkeypatch.setattr(target, "position", position)
    result = transaction(target).run()
    assert result.image is None
    assert result.restoration == "not restored: the target or user state changed"
    assert len(target.moves) == 9
    assert target.moves[-2:] == [100.0, 0.0]


def test_normalized_restore_waits_for_pending_terminal_then_exact_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale terminal reads do not cause a second write or count as restored."""
    target = EndpointTarget()
    move = target.move
    pending: float | None = None
    polls = 0

    def delayed_restore(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        nonlocal pending
        if target.percent == 100.0:
            target.moves.append(vertical)
            pending = vertical
        else:
            move(vertical)

    def pause(_seconds: float) -> None:
        nonlocal polls, pending
        if pending is not None:
            polls += 1
            if polls == 3:
                target.percent = pending
                target.offset = 0
                pending = None

    monkeypatch.setattr(target, "move", delayed_restore)
    result = ScrollTransaction(target, lambda: False, pause=pause).run()
    assert result.image is not None
    assert result.restoration == "restored"
    assert polls == 3
    assert len(target.moves) == 9
    assert target.position() == ScrollPosition(27.0, 0.0, 20.0)


def test_final_position_is_read_again_before_confirming_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Programmatic movement during composition cannot gain restore ownership."""
    target = EndpointTarget()
    operation = transaction(target)
    validate = operation._validate_extent

    def change_position(image: QImage, frames: _HeaderFrames, view: float) -> None:
        validate(image, frames, view)
        if view != 20.0:
            target.percent = 99.0

    monkeypatch.setattr(operation, "_validate_extent", change_position)
    result = operation.run()
    assert result.image is None
    assert "before terminal confirmation" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._terminal is None
    assert operation._expected is None
    assert target.moves[-1] == 100.0


def test_unverified_terminal_tuple_cannot_enable_restoration() -> None:
    """Merely observing changed metrics is insufficient for another provider write."""
    target = EndpointTarget()
    initial = target.position()
    target.move(100.0)
    operation = transaction(target)
    operation._moved = True
    operation._expected = target.position()
    assert (
        operation._restore(initial)
        == "not restored: scroll position changed unexpectedly"
    )
    assert target.moves == [100.0]


def test_interior_metric_change_still_leaves_provider_write_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The full transaction cannot turn a mid-document reflow into normalization."""
    target = EndpointTarget()
    read = target.position

    def position() -> ScrollPosition:
        current = read()
        return replace(current, view_size=20.3) if target.percent == 25.0 else current

    monkeypatch.setattr(target, "position", position)
    result = transaction(target).run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert target.moves == [12.5, 25.0]


class NativeEndpointTarget(HeaderTarget):
    """Native units deliberately differ from pixels, with persistent normalization."""

    vertical_scrollbar: Rect | None = Rect(73, 20, 17, 145)

    def __init__(self, start: int = 2400) -> None:
        super().__init__()
        self.native_position = start
        self.page = 1200
        self.offset = round(start / 10)

    def scrollbar_state(self) -> ScrollBarState:
        """Return exact separate native units, not a position inferred from pixels."""
        return ScrollBarState(
            101, 102, 103, 104, 0, 5999, self.page, self.native_position
        )

    @override
    def position(self) -> ScrollPosition:
        state = NativeEndpointTarget.scrollbar_state(self)
        return ScrollPosition(
            self.horizontal, state.percent(state.position), 100 * self.page / 5999
        )

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        if vertical == 100.0:
            self.page = 1218
        self.native_position = round((6000 - self.page) * vertical / 100)
        self.offset = 480 if vertical == 100.0 else round(self.native_position / 10)


@pytest.mark.parametrize("start", [0, 2400])
def test_native_inverse_restores_absolute_position_and_nonrail_pixels(
    start: int,
) -> None:
    """Normalized view size may persist; native position and content may not drift."""
    target = NativeEndpointTarget(start)
    original, pixels = target.position(), target.frame()
    operation = transaction(target)
    result = operation.run()
    assert result.image is not None
    assert result.restoration == "restored"
    assert target.native_position == start
    assert target.frame().copy(0, 0, 63, 145) == pixels.copy(0, 0, 63, 145)
    assert target.position().view_size != original.view_size
    assert target.moves[-2:] == [100.0, 100 * start / 4782]
    assert operation._expected == target.position()
    assert result.frames == 9  # The two original-middle anchors are not capture frames.


def prepared_native_restore() -> tuple[
    NativeEndpointTarget, ScrollTransaction, ScrollPosition
]:
    """Prepare only an already extent-confirmed endpoint for focused race tests."""
    target = NativeEndpointTarget()
    operation = transaction(target)
    initial = target.position()
    operation._save_anchor(initial)
    target.move(100.0)
    operation._moved = True
    operation._terminal = operation._expected = target.position()
    return target, operation, initial


@pytest.mark.parametrize(
    "field", ["rail", "parent", "root", "pid", "minimum", "maximum", "page"]
)
def test_native_restore_rejects_changed_endpoint_before_writing(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Stale native identities, ranges and pages cannot authorize any restoration."""
    target, operation, initial = prepared_native_restore()
    read = target.scrollbar_state
    monkeypatch.setattr(
        target,
        "scrollbar_state",
        lambda: replace(read(), **{field: getattr(read(), field) + 1}),
    )
    assert operation._restore(initial).startswith("not restored")
    assert target.moves == [100.0]


@pytest.mark.parametrize(
    "change",
    ["unit", "header", "body", "horizontal", "page", "input", "missing", "unstable"],
)
def test_native_restore_never_retries_or_acknowledges_inexact_write(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """One unit/pixel, changed H or lost ownership leaves the write unresolved."""
    target, operation, initial = prepared_native_restore()
    move, frame, read = target.move, target.frame, target.scrollbar_state

    def corrupt_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        move(vertical)
        if change == "unit":
            target.native_position += 1
        if change == "horizontal":
            target.horizontal += 1
        if change == "page":
            target.page += 1
        if change == "input":
            target.input_changed = True

    def corrupt_frame() -> QImage:
        image = frame()
        if change in ("header", "body", "unstable"):
            y = 0 if change == "header" else 30
            image.setPixelColor(
                0, y, QColor(255, target.count % 255 if change == "unstable" else 0, 0)
            )
        return image

    monkeypatch.setattr(target, "move", corrupt_move)
    monkeypatch.setattr(target, "frame", corrupt_frame)
    if change == "missing":
        monkeypatch.setattr(
            target, "scrollbar_state", lambda: None if len(target.moves) > 1 else read()
        )
    assert operation._restore(initial).startswith("not restored")
    assert len(target.moves) == 2
    assert operation._expected is None
    assert (
        operation._restore(initial) == "not restored: the provider write is unresolved"
    )
    assert len(target.moves) == 2


@pytest.mark.parametrize("race", ["frame", "read", "final"])
def test_native_restore_detects_late_input(
    monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    """Input after final pixels/read/eligibility must invalidate the result."""
    target, operation, initial = prepared_native_restore()
    frame, read, check = target.frame, target.scrollbar_state, target.check
    samples = 0

    def changed_frame() -> QImage:
        image = frame()
        if len(target.moves) > 1 and race == "frame":
            target.input_changed = True
        return image

    def changed_read() -> ScrollBarState:
        state = read()
        if len(target.moves) > 1 and race == "read":
            target.input_changed = True
        return state

    def changed_check() -> None:
        nonlocal samples
        if len(target.moves) > 1:
            samples += 1
            if race == "final" and samples == 7:
                target.input_changed = True
        check()

    monkeypatch.setattr(target, "frame", changed_frame)
    monkeypatch.setattr(target, "scrollbar_state", changed_read)
    monkeypatch.setattr(target, "check", changed_check)
    assert operation._restore(initial).startswith("not restored")
    assert len(target.moves) == 2
    assert operation._expected is None


def test_native_mapping_accepts_only_adjacent_arithmetic() -> None:
    """Two representable floats are already outside the allowed rounding ordering."""
    state = NativeEndpointTarget().scrollbar_state()
    vertical = state.percent(state.position)
    adjacent = math.nextafter(vertical, math.inf)
    assert state.matches(ScrollPosition(0, vertical, 100 * 1200 / 5999))
    assert state.matches(ScrollPosition(0, adjacent, 100 * 1200 / 5999))
    assert not state.matches(
        ScrollPosition(0, math.nextafter(adjacent, math.inf), 100 * 1200 / 5999)
    )


def test_native_cancel_before_anchor_never_moves() -> None:
    """Original-position pixel observation obeys cooperative cancellation."""
    target = NativeEndpointTarget()
    operation = ScrollTransaction(target, lambda: True)
    result = operation.run()
    assert result.image is None
    assert result.restoration == "unchanged"
    assert target.moves == []


def test_native_cancel_still_restores_owned_endpoint() -> None:
    """Cancellation never stops safe cleanup of an already confirmed owned write."""
    target, operation, initial = prepared_native_restore()
    operation._cancelled = lambda: True
    assert operation._restore(initial) == "restored"
    assert len(target.moves) == 2
    assert target.native_position == 2400


def test_native_deadline_never_starts_restore() -> None:
    """Native restoration must not expand the transaction's time budget."""
    target, operation, initial = prepared_native_restore()
    operation._clock = lambda: operation._deadline
    assert operation._restore(initial) == "not restored: time limit"
    assert target.moves == [100.0]


@pytest.mark.parametrize("change", ["input", "position", "page", "pixels"])
def test_native_initial_anchor_must_be_stable_before_first_move(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """The original-middle anchor never acknowledges a changing view or state."""
    target = NativeEndpointTarget()
    frame = target.frame

    def changed_frame() -> QImage:
        image = frame()
        if change == "input":
            target.input_changed = True
        elif change == "position":
            target.native_position += 1
        elif change == "page":
            target.page += 1
        else:
            image.setPixelColor(0, 30, QColor(255, target.count, 0))
        return image

    monkeypatch.setattr(target, "frame", changed_frame)
    result = transaction(target).run()
    assert result.image is None
    assert result.restoration == "unchanged"
    assert result.frames == 0
    assert target.moves == []


@pytest.mark.parametrize("change", ["null", "size", "header"])
def test_native_anchor_rejects_invalid_pixels(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Missing pixels or unsupported geometry cannot provide a restore anchor."""
    target = NativeEndpointTarget()
    if change == "header":
        target.header = None
    else:
        monkeypatch.setattr(
            target,
            "frame",
            lambda: (
                QImage()
                if change == "null"
                else QImage(10, 10, QImage.Format.Format_RGB32)
            ),
        )
    result = transaction(target).run()
    assert result.image is None
    assert target.moves == []


@pytest.mark.parametrize("change", ["type", "mapping", "before-frame"])
def test_native_anchor_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Optional capability means absent, never accepting corrupt native evidence."""
    target = NativeEndpointTarget()
    operation = transaction(target)
    initial = target.position()
    if change == "type":
        monkeypatch.setattr(target, "scrollbar_state", dict)
    elif change == "mapping":
        initial = replace(initial, vertical=50.01)
    else:
        monkeypatch.setattr(target, "position", lambda: replace(initial, horizontal=28))
    with pytest.raises(CaptureError):
        operation._save_anchor(initial)
    assert target.moves == []


def test_native_endpoint_is_rechecked_before_single_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh range does not authorize writing after the terminal tuple changes."""
    target, operation, initial = prepared_native_restore()
    position = target.position()
    calls = 0

    def read() -> ScrollPosition:
        nonlocal calls
        calls += 1
        return position if calls == 1 else replace(position, horizontal=28)

    monkeypatch.setattr(target, "position", read)
    assert operation._restore(initial).startswith("not restored")
    assert target.moves == [100.0]


def test_native_deadline_after_write_keeps_it_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed provider call cannot claim restored state after the deadline."""
    target, operation, initial = prepared_native_restore()
    move = target.move

    def delayed_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        move(vertical)
        operation._clock = lambda: operation._deadline

    monkeypatch.setattr(target, "move", delayed_move)
    assert operation._restore(initial).startswith("not restored")
    assert len(target.moves) == 2
    assert operation._expected is None


@pytest.mark.parametrize(
    "state",
    [
        ScrollBarState(1, 2, 3, 4, 0, 10, 0, 1),
        ScrollBarState(1, 2, 3, 4, 0, 10, 11, 0),
        ScrollBarState(1, 2, 3, 4, 0, 10, 5, 7),
        ScrollBarState(1, 2, 3, 4, 0, 10, 5, -1),
    ],
)
def test_native_inverse_rejects_unrepresentable_units(state: ScrollBarState) -> None:
    """No clamping or guessed mapping is permitted outside the native interval."""
    with pytest.raises(CaptureError):
        state.percent(state.position)


def test_native_already_normalized_bottom_stays_on_strict_restore() -> None:
    """No terminal change means the original UIA tuple/full pixels still match."""
    target = NativeEndpointTarget()
    target.move(100.0)
    target.moves.clear()
    initial, pixels = target.position(), target.frame()
    operation = transaction(target)
    operation._save_anchor(initial)
    target.move(0.0)
    operation._moved = True
    operation._expected = target.position()
    assert operation._restore(initial) == "restored"
    assert target.position() == initial
    assert target.frame() == pixels
    assert operation._terminal is None
    assert target.moves == [0.0, initial.vertical]


def exterior_panel_frame(
    source: QImage, offset: int, *, exterior: int = 1, focused: bool = False
) -> QImage:
    """Synthetic asymmetric 200% outline with a retained physical exterior pixel."""
    result = source.copy(0, offset, source.width(), 120)
    width = source.width()
    painter = QPainter(result)
    gray = QColor(177, 181, 186)
    painter.fillRect(0, 0, 5, 120, gray)
    painter.fillRect(width - exterior - 6, 0, 6, 120, gray)
    painter.fillRect(0, 1, width - exterior, 6, gray)
    if offset or focused:
        painter.fillRect(0, 0, 3, 120, QColor("black"))
        painter.fillRect(width - exterior - 4, 0, 4, 120, QColor("black"))
        painter.fillRect(0, 1, width - exterior, 4, QColor("black"))
    painter.fillRect(width - exterior, 0, exterior, 120, QColor("white"))
    painter.end()
    return result


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("focused_start", [False, True])
def test_exterior_outline_complete_sequence_keeps_every_column(
    caplog: pytest.LogCaptureFixture,
    *,
    mirror: bool,
    focused_start: bool,
) -> None:
    """All pixels, including the exterior and short terminal join, remain intact."""
    caplog.set_level("INFO")
    source = document(367, width=240)
    first = exterior_panel_frame(source, 0, focused=focused_start)
    transform = QTransform.fromScale(-1 if mirror else 1, 1)
    stitch = VerticalStitch(first.transformed(transform), dpi=192)
    previous = 0
    for offset in (60, 120, 180, 240, 247):
        stitch.append(
            exterior_panel_frame(source, offset).transformed(transform),
            offset - previous,
        )
        previous = offset
    expected = source.copy()
    painter = QPainter(expected)
    painter.drawImage(0, 0, first.copy(0, 0, 240, 93))
    for x, width, color in (
        (0, 5, QColor(177, 181, 186)),
        (233, 6, QColor(177, 181, 186)),
        (0, 3, QColor("black")),
        (235, 4, QColor("black")),
        (239, 1, QColor("white")),
    ):
        painter.fillRect(x, 93, width, 274, color)
    painter.end()
    assert stitch.finish() == expected.transformed(transform)
    if focused_start:
        assert "Scrolling registration edges" not in caplog.text
    else:
        assert (
            "left_columns=4 right_columns=3"
            if mirror
            else "left_columns=3 right_columns=4"
        ) in caplog.text
    assert "rows=[1, 2, 3, 4, 5, 6]" in caplog.text


@pytest.mark.parametrize(
    "invalid",
    [
        "two_exterior",
        "textured_exterior",
        "changed_exterior",
        "textured_adjacent",
        "unchanged_adjacent",
        "over_cap",
        "beyond_strip",
        "wide_sidebar",
        "sticky_header",
    ],
)
def test_exterior_outline_never_searches_or_hides_content(invalid: str) -> None:
    """The one-pixel paired proof cannot expand any existing chrome bound."""
    source = document(width=240)
    frames = [
        exterior_panel_frame(
            source, offset, exterior=2 if invalid == "two_exterior" else 1
        )
        for offset in (0, 60)
    ]
    for index, frame in enumerate(frames):
        painter = QPainter(frame)
        if invalid == "changed_exterior" and index:
            painter.fillRect(239, 0, 1, 120, QColor("red"))
        elif invalid == "unchanged_adjacent":
            painter.fillRect(238, 0, 1, 120, QColor("gray"))
        elif invalid == "over_cap":
            painter.fillRect(234, 0, 1, 120, QColor("black" if index else "gray"))
        elif invalid == "wide_sidebar":
            painter.fillRect(232, 0, 1, 120, QColor(177, 181, 186))
        elif invalid == "sticky_header":
            painter.fillRect(10, 7, 220, 10, QColor("red"))
        painter.end()
    if invalid in ("textured_exterior", "textured_adjacent", "beyond_strip"):
        x = {"textured_exterior": 239, "textured_adjacent": 238, "beyond_strip": 234}[
            invalid
        ]
        for y in range(120):
            frames[1].setPixelColor(x, y, QColor(255, y, 255))
    with pytest.raises(CaptureError):
        VerticalStitch(frames[0], dpi=192).append(frames[1], 60.0)


def test_exterior_outline_keeps_global_ambiguity_fatal() -> None:
    """A proved exterior pixel does not select among repeated interior bands."""
    source = document(180, width=240)
    painter = QPainter(source)
    for y in (40, 80, 120):
        painter.drawImage(0, y, source.copy(0, 0, 240, 40))
    painter.end()
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(exterior_panel_frame(source, 0), dpi=192).append(
            exterior_panel_frame(source, 60), 60.0
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "exterior_color",
        "exterior_pixel",
        "adjacent_texture",
        "adjacent_exterior",
        "wide_side",
        "interior",
        "header",
    ],
)
def test_full_width_exterior_proof_is_recomputed_on_every_pair(invalid: str) -> None:
    """A previous successful append never excuses changed structure or content."""
    source = document(width=240)
    stitch = VerticalStitch(exterior_panel_frame(source, 0), dpi=192)
    stitch.append(exterior_panel_frame(source, 60), 60.0)
    candidate = exterior_panel_frame(source, 120)
    painter = QPainter(candidate)
    if invalid == "exterior_color":
        painter.fillRect(239, 0, 1, 120, QColor("red"))
    elif invalid == "adjacent_exterior":
        painter.fillRect(238, 0, 1, 120, QColor("white"))
    elif invalid == "wide_side":
        painter.fillRect(232, 0, 1, 120, QColor(177, 181, 186))
    elif invalid == "interior":
        painter.fillRect(20, 0, 20, 120, QColor("red"))
    elif invalid == "header":
        painter.fillRect(10, 7, 220, 10, QColor("red"))
    painter.end()
    if invalid in ("exterior_pixel", "adjacent_texture"):
        candidate.setPixelColor(
            239 if invalid == "exterior_pixel" else 238, 20, QColor("red")
        )
    with pytest.raises(CaptureError):
        stitch.append(candidate, 60.0)
    # A rejected pair does not change the last accepted pixels.
    stitch.append(exterior_panel_frame(source, 120), 60.0)
    assert stitch.finish().height() == 240


def test_failed_append_keeps_previous_pixels_for_next_exact_registration() -> None:
    """A rejected movement cannot change the next pair's alignment inputs."""
    source = document(width=240)
    stitch = VerticalStitch(exterior_panel_frame(source, 0), dpi=192)
    with pytest.raises(CaptureError, match="disagree with its scroll position"):
        stitch.append(exterior_panel_frame(source, 60), 70.0)
    assert stitch.finish() == exterior_panel_frame(source, 0)
    stitch.append(exterior_panel_frame(source, 60), 60.0)
    stitch.append(exterior_panel_frame(source, 120), 60.0)
    assert stitch.finish().height() == 240


def test_exterior_outline_allows_a_new_proven_adjacent_color_change() -> None:
    """A later outline transition must pass the same fresh paired proof."""
    source = document(width=240)
    stitch = VerticalStitch(exterior_panel_frame(source, 0), dpi=192)
    stitch.append(exterior_panel_frame(source, 60), 60.0)
    unfocused = exterior_panel_frame(source.copy(0, 120, 240, 120), 0)
    stitch.append(unfocused, 60.0)
    stitch.append(exterior_panel_frame(source, 180), 60.0)
    assert stitch.finish().height() == 300


def test_masked_only_alignment_cannot_unlock_static_opposite_exterior() -> None:
    """A changed left side cannot authorize static right-side perimeter evidence."""
    source = document(width=240)
    first = exterior_panel_frame(source, 0, focused=True)
    second = exterior_panel_frame(source, 60)
    painter = QPainter(second)
    painter.fillRect(0, 0, 1, 120, QColor("red"))
    painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(first, dpi=192).append(second, 60.0)


@pytest.mark.parametrize(
    "invalid",
    [
        "two_exterior",
        "textured_exterior",
        "changed_exterior",
        "textured_adjacent",
        "outer_adjacent",
        "wide_side",
        "top_cap",
        "unequal_rows",
    ],
)
def test_already_focused_start_keeps_all_perimeter_guards(invalid: str) -> None:
    """An exact full-width match is necessary but does not replace bounded proof."""
    source = document(width=240)
    frames = [
        exterior_panel_frame(
            source, offset, focused=True, exterior=2 if invalid == "two_exterior" else 1
        )
        for offset in (0, 60)
    ]
    for index, frame in enumerate(frames):
        painter = QPainter(frame)
        if invalid == "changed_exterior" and index:
            painter.fillRect(239, 0, 1, 120, QColor("red"))
        elif invalid == "outer_adjacent":
            painter.fillRect(238, 0, 1, 120, QColor("white"))
        elif invalid == "wide_side":
            painter.fillRect(232, 0, 1, 120, QColor(177, 181, 186))
        elif invalid == "top_cap":
            painter.fillRect(0, 8, 239, 1, QColor(177, 181, 186))
        elif invalid == "unequal_rows" and index:
            painter.drawImage(5, 6, source.copy(5, 66, 228, 1))
        painter.end()
        if invalid in ("textured_exterior", "textured_adjacent"):
            for y in range(120):
                frame.setPixelColor(
                    239 if invalid == "textured_exterior" else 238,
                    y,
                    QColor(255, y, 255),
                )
    with pytest.raises(CaptureError):
        VerticalStitch(frames[0], dpi=192).append(frames[1], 60.0)


@pytest.mark.parametrize("focused", [False, True])
def test_exterior_proof_never_bypasses_missing_or_ambiguous_alignment(
    *, focused: bool
) -> None:
    """Repeated and wholly unrelated content cannot unlock static perimeter rows."""
    source = document(180, width=240)
    painter = QPainter(source)
    for y in (40, 80, 120):
        painter.drawImage(0, y, source.copy(0, 0, 240, 40))
    painter.end()
    first = exterior_panel_frame(source, 0, focused=focused)
    second = exterior_panel_frame(source, 60)
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(first, dpi=192).append(second, 60.0)
    painter = QPainter(second)
    painter.fillRect(5, 7, 228, 113, QColor("red"))
    painter.end()
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first, dpi=192).append(second, 60.0)


def test_already_focused_start_rejects_textured_sticky_header() -> None:
    """A thin legitimate border cannot excuse fixed textured content beneath it."""
    source = document(width=240)
    frames = [exterior_panel_frame(source, offset, focused=True) for offset in (0, 60)]
    for frame in frames:
        painter = QPainter(frame)
        for y in range(7, 17):
            painter.fillRect(10, y, 220, 1, QColor(255, y, 255))
        painter.end()
    with pytest.raises(CaptureError, match="top of the scroll panel"):
        VerticalStitch(frames[0], dpi=192).append(frames[1], 60.0)


class FooterTarget(NativeEndpointTarget):
    """Known horizontal rail/corner below the full-width moving content."""

    area = Rect(10, 20, 80, 162)
    footer: Rect | None = Rect(10, 165, 63, 17)

    @override
    def frame(self) -> QImage:
        first = super().frame()
        result = QImage(80, 162, QImage.Format.Format_RGB32)
        result.fill(QColor(42, 75, 111))
        painter = QPainter(result)
        painter.drawImage(0, 0, first)
        painter.end()
        return result


@pytest.mark.parametrize("start", [0, 2400])
def test_footer_kept_once_with_exact_normalized_restoration(start: int) -> None:
    """Both fixed bands surround the complete full-width document exactly once."""
    target = FooterTarget(start)
    before = target.frame()
    result = transaction(target).run()
    assert result.image is not None
    assert result.image.size() == QImage(80, 642, QImage.Format.Format_RGB32).size()
    assert result.image.copy(0, 0, 80, 25) == FooterTarget(0).frame().copy(0, 0, 80, 25)
    assert result.image.copy(0, 25, 80, 600) == target.document
    assert result.image.copy(0, 625, 80, 17) == before.copy(0, 145, 80, 17)
    assert result.restoration == "restored"
    assert target.native_position == start
    assert target.frame().copy(0, 145, 80, 17) == before.copy(0, 145, 80, 17)
    assert target.moves[-2:] == [100.0, 100 * start / 4782]
    assert result.frames == 9


def test_measured_200_percent_header_footer_extent_uses_only_moving_body() -> None:
    """Native37's339px body is separate from52px Header and34px footer."""
    area = Rect(343, 282, 654, 425)
    header = Rect(343, 282, 620, 52)
    footer = Rect(343, 673, 620, 34)
    image = QImage(654, 425, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 80, 160))
    frames = _HeaderFrames(image, area, header, footer)
    assert frames.body_height == 339
    assert frames.body(image).width() == 654
    assert frames.extent(20) == 52 + 339 * 5 + 34
    body = document(1695, width=654)
    result = frames.finish(body)
    assert result.height() == 1781
    assert result.copy(0, 52, 654, 1695) == body
    assert result.copy(0, 1747, 654, 34) == image.copy(0, 391, 654, 34)


@pytest.mark.parametrize("column", [0, 62, 63, 79])
def test_footer_and_corner_must_remain_stable_during_capture(
    monkeypatch: pytest.MonkeyPatch, column: int
) -> None:
    """One changed horizontal scrollbar or corner pixel rejects capture."""
    target = FooterTarget(0)
    frame = target.frame

    def changed_frame() -> QImage:
        image = frame()
        if target.offset:
            image.setPixelColor(column, 150, QColor(255, 0, 0))
        return image

    monkeypatch.setattr(target, "frame", changed_frame)
    result = transaction(target).run()
    assert result.image is None
    assert "footer changed" in result.message
    assert result.restoration == "restored"
    assert target.native_position == 0


@pytest.mark.parametrize("column", [0, 62, 63, 79])
def test_native_footer_restore_proof_includes_entire_footer_and_corner(
    monkeypatch: pytest.MonkeyPatch, column: int
) -> None:
    """The vertical rail exclusion never hides a changed bottom-right corner."""
    target = FooterTarget()
    operation = transaction(target)
    initial = target.position()
    operation._save_anchor(initial)
    target.move(100.0)
    operation._moved = True
    operation._expected = operation._terminal = target.position()
    frame = target.frame

    def changed_frame() -> QImage:
        image = frame()
        image.setPixelColor(column, 150, QColor(255, 0, 0))
        return image

    monkeypatch.setattr(target, "frame", changed_frame)
    assert operation._restore(initial).startswith("not restored")
    assert operation._expected is None
    assert len(target.moves) == 2
    assert target.native_position == 2400


def test_native_footer_restore_excludes_only_verified_vertical_rail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rail changes above the footer do not alter any compared content pixels."""
    target = FooterTarget()
    operation = transaction(target)
    initial = target.position()
    operation._save_anchor(initial)
    target.move(100.0)
    operation._moved = True
    operation._expected = operation._terminal = target.position()
    frame = target.frame

    def changed_frame() -> QImage:
        image = frame()
        image.setPixelColor(79, 144, QColor(255, target.count % 255, 0))
        return image

    monkeypatch.setattr(target, "frame", changed_frame)
    assert operation._restore(initial) == "restored"
    assert target.native_position == 2400
    assert len(target.moves) == 2


@pytest.mark.parametrize(
    "footer",
    [
        Rect(11, 165, 62, 17),
        Rect(10, 165, 80, 17),
        Rect(10, 165, 62, 17),
        Rect(10, 164, 63, 17),
        Rect(10, 45, 63, 137),
        Rect(10, 182, 63, 0),
        Rect(10, 164.5, 63, 17.5),
    ],
)
def test_footer_partition_rejects_unproven_geometry(footer: Rect) -> None:
    """No approximate edges, gaps, overlapping bands or fractional crop budget."""
    target = FooterTarget()
    target.footer = footer
    result = transaction(target).run()
    assert result.image is None
    assert target.moves == []
    assert result.frames == 0


def test_footer_without_header_cannot_enable_fixed_band() -> None:
    """A footer alone is not evidence of the proven standard list layout."""
    target = FooterTarget()
    with pytest.raises(CaptureError, match="footer geometry"):
        _HeaderFrames(target.frame(), target.area, None, target.footer)


@pytest.mark.parametrize("rail", [None, Rect(73, 20, 17, 162), Rect(73, 20, 17, 144)])
def test_native_footer_anchor_rejects_missing_or_wrong_vertical_rail(
    rail: Rect | None,
) -> None:
    """A full-height exclusion would omit corner pixels and must be rejected."""
    target = FooterTarget()
    target.vertical_scrollbar = rail
    result = transaction(target).run()
    assert result.image is None
    assert target.moves == []


def test_footer_counts_against_existing_output_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Appending the fixed footer cannot exceed the unchanged pixel cap."""
    target = FooterTarget()
    frames = _HeaderFrames(target.frame(), target.area, target.header, target.footer)
    monkeypatch.setattr("verdiclip.capture.scrolling.MAX_PIXELS", 80 * 630)
    with pytest.raises(CaptureError, match="size limit"):
        frames.finish(target.document)


class ListEvidence:
    """Portable capability double; platform tests own raw item/native validation."""

    body = Rect(10, 45, 63, 120)

    def __init__(self, target: ListTarget) -> None:
        self.target = target
        self.before = 0
        self.begins: list[int] = []
        self.finishes: list[int] = []
        self.confirmed = False
        self.closed = False

    def begin(self, position: ScrollPosition) -> None:
        """Bind independently selected items to the last accepted frame position."""
        assert self.target.position() == position
        self.before = self.target.offset
        self.begins.append(self.before)

    def finish(self, position: ScrollPosition, *, terminal: bool = False) -> int:
        """Return measured displacement without knowing the pixel candidate."""
        assert position == self.target.position()
        assert terminal == (position.vertical == 100.0)
        delta = self.target.offset - self.before
        self.finishes.append(delta)
        return delta

    def confirm_terminal(self, position: ScrollPosition) -> None:
        """Require a fresh native maximum independently of UIA endpoint claims."""
        assert position == self.target.position()
        state = self.target.scrollbar_state()
        assert state.position == state.maximum - state.page + 1
        self.confirmed = True

    def close(self) -> None:
        """Record cleanup independently of capture success."""
        self.closed = True


class ListTarget(FooterTarget):
    """Standard layout with native units independent of exact body displacement."""

    def __init__(self, *, start: int = 0, total: int = 592) -> None:
        super().__init__(start)
        self.horizontal = 0.0
        self.document = document(total)
        self.steps = ListEvidence(self)
        self.selected = 0

    def list_steps(
        self,
        initial: ScrollPosition,
        check: Callable[[], None],
        *,
        poll: Callable[[], None],
    ) -> ListEvidence:
        """Expose optional geometry once, before any traversal write."""
        assert initial == self.position()
        assert callable(check)
        assert callable(poll)
        assert not self.moves
        self.selected += 1
        return self.steps

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        self.moves.append(vertical)
        if vertical == 100.0:
            self.page = 1236
        self.native_position = round((6000 - self.page) * vertical / 100)
        self.offset = (
            self.document.height() - 120
            if vertical == 100.0
            else round(self.native_position / 10)
        )


def test_list_entire_overlap_filters_partial_aliases_before_global_uniqueness() -> None:
    """A repeated40row band cannot trump the entire100row nonrail overlap."""
    source = document(260)
    repeated = source.copy(0, 60, 80, 40)
    painter = QPainter(source)
    painter.drawImage(0, 100, repeated)
    painter.end()
    first, second = source.copy(0, 0, 80, 160), source.copy(0, 60, 80, 160)
    with pytest.raises(CaptureError, match="ambiguous"):
        VerticalStitch(first).append(second, 60)
    verified: list[int] = []
    stitch = VerticalStitch(first, nonrail_width=63)
    stitch.append(second, None, verify=verified.append)
    assert verified == [60]
    assert stitch.finish() == source.copy(0, 0, 80, 220)


def test_list_global_whole_overlap_ambiguity_precedes_item_evidence() -> None:
    """Even an item witness favoring one candidate cannot break a pixel tie."""
    source = QImage(80, 240, QImage.Format.Format_RGB32)
    painter = QPainter(source)
    tile = document(40)
    for y in range(0, 240, 40):
        painter.drawImage(0, y, tile)
    painter.end()
    first = source.copy(0, 0, 80, 160)
    stitch = VerticalStitch(first, nonrail_width=63)
    verified: list[int] = []
    with pytest.raises(CaptureError, match="ambiguous"):
        stitch.append(source.copy(0, 60, 80, 160), None, verify=verified.append)
    assert not verified
    assert stitch.finish() == first


@pytest.mark.parametrize("kind", ["zero", "uniform", "untextured"])
def test_list_overlap_still_requires_positive_textured_band(kind: str) -> None:
    """Whole matching rows cannot bypass positive movement or8distinct rows."""
    first, second = document(120), document(180).copy(0, 60, 80, 120)
    if kind == "zero":
        second = first.copy()
    elif kind == "uniform":
        first.fill(QColor("white"))
        second.fill(QColor("white"))
    else:
        for image in (first, second):
            for y in range(image.height()):
                for x in range(image.width()):
                    image.setPixelColor(x, y, QColor(y % 4, x, 0))
    stitch = VerticalStitch(first, nonrail_width=63)
    with pytest.raises(CaptureError):
        stitch.append(second, None)
    assert stitch.finish() == first


def periodic_list_body(*, rail_offset: int = 0) -> QImage:
    """Repeat sixty textured rows, with a separately moving scrollbar thumb."""
    image = QImage(80, 120, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    tile = document(60)
    painter.drawImage(0, 0, tile)
    painter.drawImage(0, 60, tile)
    painter.fillRect(63, 0, 17, 120, QColor("gray"))
    painter.fillRect(63, rail_offset, 17, 2, QColor("black"))
    painter.end()
    return image


@pytest.mark.parametrize("rail_changed", [False, True])
def test_list_unchanged_periodic_body_rejects_unique_positive_alias(
    *,
    rail_changed: bool,
) -> None:
    """A positive alias and agreeing item callback cannot prove rendered motion."""
    first = periodic_list_body()
    second = periodic_list_body(rail_offset=2 if rail_changed else 0)
    assert first.copy(0, 0, 63, 120) == second.copy(0, 0, 63, 120)
    assert (first != second) == rail_changed
    stitch = VerticalStitch(first, nonrail_width=63)
    match = stitch._alignment(
        stitch._rows(first), stitch._rows(second), nonrail_width=63
    )
    assert match is not None
    assert match[0] == 60
    verified: list[int] = []

    def verify(advance: int) -> None:
        assert advance == 60
        verified.append(advance)

    with pytest.raises(CaptureError, match="rendered body did not move"):
        stitch.append(second, None, verify=verify)
    assert not verified
    assert stitch.finish() == first


@pytest.mark.parametrize("rail_changed", [False, True])
def test_list_unchanged_post_write_body_never_gains_restore_authority(
    monkeypatch: pytest.MonkeyPatch, *, rail_changed: bool
) -> None:
    """Native movement and consistent raw-item deltas cannot accept frozen pixels."""
    target = ListTarget(total=600)
    original = target.frame()

    def move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        target.moves.append(vertical)
        target.native_position = round((6000 - target.page) * vertical / 100)
        target.offset = round(target.native_position / 10)

    def frame() -> QImage:
        image = original.copy()
        painter = QPainter(image)
        painter.drawImage(
            0,
            25,
            periodic_list_body(rail_offset=target.offset // 30 if rail_changed else 0),
        )
        painter.end()
        return image

    monkeypatch.setattr(target, "move", move)
    monkeypatch.setattr(target, "frame", frame)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "rendered body did not move" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert len(target.moves) == 1
    assert target.native_position == 600
    assert target.offset - target.steps.before == 60
    assert target.steps.begins == [0]
    assert not target.steps.finishes
    assert not target.steps.confirmed
    assert target.steps.closed
    assert operation._expected is None
    assert operation._terminal is None


def test_list_stable_pre_write_frames_allow_genuine_motion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary equal anchor and top samples are not post-write motion evidence."""
    target = ListTarget()
    frame = target.frame
    before_write: list[QImage] = []

    def observe() -> QImage:
        image = frame()
        if not target.moves:
            before_write.append(image.copy())
        return image

    monkeypatch.setattr(target, "frame", observe)
    result = transaction(target).run()
    assert len(before_write) >= 2
    assert all(image == before_write[0] for image in before_write)
    assert result.image is not None
    assert result.image.copy(0, 25, 80, 592) == target.document
    assert result.restoration == "restored"
    assert target.steps.finishes == [60] * 7 + [52]
    assert target.steps.confirmed
    assert target.steps.closed


@pytest.mark.parametrize("column", [0, 1, 61, 62])
def test_list_one_nonrail_pixel_cannot_hide_outside_exact_band(column: int) -> None:
    """The existing32row band is insufficient when one other overlap pixel differs."""
    source = document(180)
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 60, 80, 120)
    second.setPixelColor(column, 59, QColor("red"))
    stitch = VerticalStitch(first, nonrail_width=63)
    with pytest.raises(CaptureError, match="complete overlapping pixels"):
        stitch.append(second, None)
    assert stitch.finish() == first


@pytest.mark.parametrize("width", [0, -1, 80, 81, 63.5, True])
def test_list_width_never_authorizes_an_arbitrary_or_empty_crop(width: object) -> None:
    """Only an integer proper prefix is usable even at the internal stitch boundary."""
    with pytest.raises(CaptureError, match="geometry"):
        VerticalStitch(document(120), nonrail_width=width)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("start", [0, 2400])
def test_list_defers_endpoint_estimate_gate_but_proves_both_actual_extents(
    start: int,
) -> None:
    """3% estimate disagreement can bracket the exact image within each2% bound."""
    target = ListTarget(start=start)
    original = target.frame()
    operation = transaction(target)
    result = operation.run()
    assert result.image is not None
    assert result.image.height() == 634
    assert result.image.copy(0, 25, 80, 592) == target.document
    assert result.image.copy(0, 617, 80, 17) == original.copy(0, 145, 80, 17)
    assert target.selected == 1
    assert target.steps.begins == list(range(0, 480, 60))
    assert target.steps.finishes == [60] * 7 + [52]
    assert target.steps.confirmed
    assert target.steps.closed
    assert target.native_position == start
    assert target.moves[-2:] == [100.0, 100 * start / 4764]
    assert result.restoration == "restored"
    assert operation._terminal is not None


def test_list_step_uses_raw_pixels_not_percentage_prediction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single large initial offset is measured, never encoded as an inset."""
    target = ListTarget()
    move = target.move

    def offset_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        move(vertical)
        if 0 < vertical < 100:
            target.offset += 8

    monkeypatch.setattr(target, "move", offset_move)
    result = transaction(target).run()
    assert result.image is not None
    assert result.image.copy(0, 25, 80, 592) == target.document
    assert target.steps.finishes == [68] + [60] * 6 + [44]


@pytest.mark.parametrize("delta", [0, -60, 59, 61, 60.0, 60.5, True, None])
def test_list_item_delta_must_be_the_exact_positive_integer_pixel_delta(
    monkeypatch: pytest.MonkeyPatch, delta: object
) -> None:
    """No rounding, nearest candidate, percentage allowance or fallback applies."""
    target = ListTarget()
    monkeypatch.setattr(target.steps, "finish", lambda *_args, **_kwargs: delta)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "items disagree" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert len(target.moves) == 1
    assert operation._expected is None
    assert operation._terminal is None
    assert target.steps.closed


@pytest.mark.parametrize("stage", ["begin", "finish", "confirm_terminal"])
def test_list_evidence_failure_never_falls_back_to_generic_restore(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Lost capability/identity/layout/range evidence revokes write authority."""
    target = ListTarget(start=2400)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise CaptureError("List evidence was lost")

    monkeypatch.setattr(target.steps, stage, fail)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert result.message == "List evidence was lost"
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert operation._terminal is None
    assert target.steps.closed
    assert target.moves[-1] != 100 * 2400 / 4764


@pytest.mark.parametrize("total", [580, 610])
def test_list_both_final_actual_height_bounds_remain_unchanged(total: int) -> None:
    """One total bound passes and the other fails; no terminal restore is authorized."""
    target = ListTarget(total=total)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "disagree with the panel's extent" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert operation._terminal is None
    assert target.moves[-1] == 100.0
    assert not target.steps.confirmed


@pytest.mark.parametrize("change", ["body", "rail", "header", "horizontal"])
def test_list_selected_geometry_cannot_be_forged_before_traversal(change: str) -> None:
    """Only the exact verified nonrail body/right rail layout can select this route."""
    target = ListTarget()
    if change == "body":
        target.steps.body = Rect(10, 45, 62, 120)
    elif change == "rail":
        target.vertical_scrollbar = Rect(72, 20, 18, 145)
    elif change == "header":
        target.header = Rect(10, 20, 62, 25)
    else:
        target.horizontal = 1.0
    result = transaction(target).run()
    assert result.image is None
    assert not target.moves
    assert result.restoration == "unchanged"


def test_list_missing_before_selection_keeps_strict_generic_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported is distinct from failed evidence after the route was selected."""
    target = ListTarget()
    monkeypatch.setattr(target, "list_steps", lambda *_args, **_kwargs: None)
    result = transaction(target).run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert target.moves[-1] == 100.0
    assert not target.steps.begins
    assert not target.steps.closed


@pytest.mark.parametrize("stage", ["finish", "confirm_terminal"])
@pytest.mark.parametrize("interrupt", ["input", "focus", "deadline", "position"])
def test_list_late_state_change_cannot_commit_or_restore(
    monkeypatch: pytest.MonkeyPatch, stage: str, interrupt: str
) -> None:
    """Final item/native observations still require current eligibility and position."""
    target = ListTarget()
    elapsed = [0.0]
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: elapsed[0], pause=lambda _: None
    )
    observe = getattr(target.steps, stage)

    def changed(*args: object, **kwargs: object) -> object:
        result = observe(*args, **kwargs)
        if interrupt in ("input", "focus"):
            target.input_changed = True
        elif interrupt == "deadline":
            elapsed[0] = operation._deadline
        else:
            target.horizontal = 1.0
        return result

    monkeypatch.setattr(target.steps, stage, changed)
    result = operation.run()
    assert result.image is None
    assert result.restoration.startswith("not restored")
    assert operation._expected is None
    assert operation._terminal is None
    assert len(target.moves) == (1 if stage == "finish" else 8)
    assert target.steps.closed


def test_list_rejected_raw_item_proof_leaves_stitch_append_uncommitted() -> None:
    """A pixel candidate does not change previous frames before item/late guards."""
    source = document(180)
    first = source.copy(0, 0, 80, 120)
    stitch = VerticalStitch(first, nonrail_width=63)

    def reject(_advance: int) -> None:
        raise CaptureError("The two raw items disagree")

    with pytest.raises(CaptureError, match="two raw items"):
        stitch.append(source.copy(0, 60, 80, 120), None, verify=reject)
    assert stitch.finish() == first
    stitch.append(source.copy(0, 60, 80, 120), None)
    assert stitch.finish() == source


@pytest.mark.parametrize("exact_rows", [0, 31, 32])
def test_list_nonrail_overlap_still_needs_original_full_width_band(
    exact_rows: int,
) -> None:
    """Excluding the actual rail does not replace the full-width32row criterion."""
    source = document(180)
    first, second = source.copy(0, 0, 80, 120), source.copy(0, 60, 80, 120)
    painter = QPainter(second)
    painter.fillRect(63, exact_rows, 17, 120 - exact_rows, QColor("red"))
    painter.end()
    stitch = VerticalStitch(first, nonrail_width=63)
    if exact_rows < 32:
        with pytest.raises(CaptureError, match="complete overlapping pixels"):
            stitch.append(second, None)
        assert stitch.finish() == first
    else:
        stitch.append(second, None)
        image = stitch.finish()
        assert image.width() == 80
        assert image.height() == 180
        assert image.copy(0, 0, 63, 180) == source.copy(0, 0, 63, 180)
        assert image.copy(0, 76, 80, 104) == second.copy(0, 16, 80, 104)


@pytest.mark.parametrize("limit", ["frames", "pixels"])
def test_list_route_keeps_global_capture_limits(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    """Item geometry cannot enlarge the existing frame or output pixel budget."""
    target = ListTarget()
    if limit == "frames":
        monkeypatch.setattr("verdiclip.capture.scrolling.MAX_FRAMES", 2)
    else:
        monkeypatch.setattr("verdiclip.capture.scrolling.MAX_PIXELS", 80 * 200)
    result = transaction(target).run()
    assert result.image is None
    assert "limit" in result.message
    assert target.steps.closed
    assert not target.steps.confirmed


def test_list_cleanup_failure_does_not_escape_or_authorize_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uncertain COM release after success still withholds pixels and writes."""
    target = ListTarget()

    def fail() -> None:
        raise OSError("Could not release the retained items")

    monkeypatch.setattr(target.steps, "close", fail)
    result = transaction(target).run()
    assert result.image is None
    assert "release" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert target.moves[-1] == 100.0


def test_list_normalized_bottom_start_seeks_top_outside_item_lineage() -> None:
    """A known bottom start uses no cross-document item proof or extra restore write."""
    target = ListTarget()
    target.move(100.0)
    target.moves.clear()
    initial, original = target.position(), target.frame()
    operation = transaction(target)
    result = operation.run()
    assert result.image is not None
    assert result.image.copy(0, 25, 80, 592) == target.document
    assert target.position() == initial
    assert target.frame() == original
    assert target.steps.begins[0] == 0
    assert all(delta > 0 for delta in target.steps.finishes)
    assert target.moves[0] == 0.0
    assert target.moves[-1] == 100.0
    assert len(target.moves) == len(target.steps.finishes) + 1
    assert result.restoration == "restored"
    assert operation._terminal is None


def test_list_changed_top_seek_metric_cannot_start_forward_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting geometry never permits initial bottom-to-top metric changes."""
    target = ListTarget(start=2400)
    move = target.move

    def changed(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        move(vertical)
        if vertical == 0.0:
            target.page += 1

    monkeypatch.setattr(target, "move", changed)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert target.moves == [0.0]
    assert not target.steps.begins
    assert operation._expected is None
    assert operation._terminal is None
    assert result.restoration == "not restored: the provider write is unresolved"


def test_list_cancel_before_first_write_changes_nothing() -> None:
    """Cancellation before selection never acquires items or writes native state."""
    target = ListTarget(start=2400)
    result = ScrollTransaction(target, lambda: True, pause=lambda _: None).run()
    assert result.image is None
    assert "cancelled" in result.message
    assert result.restoration == "unchanged"
    assert not target.moves
    assert not target.selected


def test_list_cancel_during_unaccepted_write_does_not_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settled native write without pixel/item acceptance remains unresolved."""
    target = ListTarget()
    cancelled = [False]
    move = target.move

    def cancel_after_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        move(vertical)
        cancelled[0] = True

    monkeypatch.setattr(target, "move", cancel_after_move)
    operation = ScrollTransaction(target, lambda: cancelled[0], pause=lambda _: None)
    result = operation.run()
    assert result.image is None
    assert "cancelled" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert len(target.moves) == 1
    assert target.steps.closed


def test_list_cooperative_cancel_between_accepted_steps_can_restore() -> None:
    """Cancelling a proved interior frame still allows the existing single restore."""
    target = ListTarget()
    operation = ScrollTransaction(
        target,
        lambda: target.offset == 60 and operation._expected is not None,
        pause=lambda _: None,
    )
    result = operation.run()
    assert result.image is None
    assert "cancelled" in result.message
    assert result.restoration == "restored"
    assert len(target.moves) == 2
    assert target.moves[-1] == 0.0
    assert target.native_position == 0
    assert target.steps.finishes == [60]
    assert target.steps.closed


@pytest.mark.parametrize("reason", ["User input", "Focus changed"])
def test_list_input_or_focus_between_accepted_steps_refuses_restore(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    """Unlike cooperative cancellation, real eligibility loss never permits writing."""
    target = ListTarget()
    operation = transaction(target)

    def lost() -> None:
        if target.offset == 60 and operation._expected is not None:
            raise CaptureError(reason)

    monkeypatch.setattr(target, "check", lost)
    result = operation.run()
    assert result.image is None
    assert result.message == reason
    assert result.restoration == "not restored: the target or user state changed"
    assert len(target.moves) == 1
    assert target.steps.finishes == [60]


@pytest.mark.parametrize("change", ["maximum", "rail", "page", "missing"])
def test_list_top_seek_requires_fresh_unchanged_native_state(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A top screenshot/UIA tuple alone cannot grant permission to restore or move."""
    target = ListTarget(start=2400)
    read = target.scrollbar_state

    def changed() -> ScrollBarState | None:
        state = read()
        if not target.moves:
            return state
        return (
            None
            if change == "missing"
            else replace(state, **{change: getattr(state, change) + 1})
        )

    monkeypatch.setattr(target, "scrollbar_state", changed)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "seeking the top" in result.message
    assert target.moves == [0.0]
    assert not target.steps.begins
    assert operation._expected is None
    assert result.restoration == "not restored: the provider write is unresolved"


@pytest.mark.parametrize("pixel", [(0, 0), (0, 150), (79, 150), (0, 25), (62, 84)])
def test_list_any_changed_fixed_or_overlapping_exterior_pixel_rejects(
    monkeypatch: pytest.MonkeyPatch, pixel: tuple[int, int]
) -> None:
    """Exact Header/footer/corner and whole nonrail overlap precede raw item proof."""
    target = ListTarget()
    frame = target.frame

    def corrupted() -> QImage:
        image = frame()
        if target.offset:
            image.setPixelColor(*pixel, QColor("red"))
        return image

    monkeypatch.setattr(target, "frame", corrupted)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert result.restoration == "not restored: the provider write is unresolved"
    assert len(target.moves) == 1
    assert not target.steps.finishes
    assert operation._expected is None
    assert operation._terminal is None


@pytest.mark.parametrize("stage", ["begin", "finish", "confirm_terminal"])
@pytest.mark.parametrize("interrupt", ["cancel", "deadline"])
def test_list_factory_poll_detects_interruption_during_full_validation(
    monkeypatch: pytest.MonkeyPatch, stage: str, interrupt: str
) -> None:
    """A fresh supplied poll after a blocking full boundary cannot grant authority."""
    target = ListTarget(start=2400)
    elapsed, cancelled, blocking = [0.0], [False], [False]
    checked: list[str] = []
    returned: list[str] = []
    operation = ScrollTransaction(
        target,
        lambda: cancelled[0],
        clock=lambda: elapsed[0],
        pause=lambda _: None,
    )
    select, validate = target.list_steps, target.check

    def blocking_check() -> None:
        validate()
        if blocking[0]:
            checked.append(stage)
            if interrupt == "cancel":
                cancelled[0] = True
            else:
                elapsed[0] = operation._deadline

    def select_callbacks(
        initial: ScrollPosition,
        check: Callable[[], None],
        *,
        poll: Callable[[], None],
    ) -> ListEvidence:
        assert check == operation._check
        assert poll == operation._poll
        steps = select(initial, check, poll=poll)
        observe = getattr(steps, stage)

        def boundary(*args: object, **kwargs: object) -> object:
            result = observe(*args, **kwargs)
            blocking[0] = True
            check()
            poll()
            returned.append(stage)
            return result

        monkeypatch.setattr(steps, stage, boundary)
        return steps

    monkeypatch.setattr(target, "check", blocking_check)
    monkeypatch.setattr(target, "list_steps", select_callbacks)
    result = operation.run()
    assert result.image is None
    assert result.message == (
        "Scrolling capture cancelled"
        if interrupt == "cancel"
        else "Scrolling capture reached its time limit"
    )
    assert result.restoration == (
        "not restored: the provider write is unresolved"
        if interrupt == "cancel"
        else "not restored: time limit"
    )
    assert len(target.moves) == {"begin": 1, "finish": 2, "confirm_terminal": 9}[stage]
    assert target.moves[0] == 0.0
    assert checked == [stage]
    assert not returned
    assert operation._expected is None
    assert operation._terminal is None
    assert target.selected == 1
    assert target.steps.closed


@pytest.mark.parametrize("elapsed", [32.999, 33.0, 33.001, 34.999, 35.0, 35.001])
def test_traversal_admission_preserves_unissued_proven_state(elapsed: float) -> None:
    """The immutable 35s budget reserves its last 2s without revoking safe recovery."""
    now = [100.0]
    target = Target()
    operation = ScrollTransaction(target, lambda: False, clock=lambda: now[0])
    original = target.position()
    operation._expected = original
    now[0] += elapsed
    if elapsed < 33.0:
        operation._admit_traversal()
    else:
        with pytest.raises(CaptureError, match="time limit"):
            operation._admit_traversal()
    assert operation._deadline == 135.0
    assert operation._expected == original
    assert not operation._moved
    assert not target.moves


@pytest.mark.parametrize("elapsed", [34.999, 35.0, 35.001])
@pytest.mark.parametrize("restoring", [False, True])
def test_full_check_rejects_blocking_target_return_at_deadline(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, *, restoring: bool
) -> None:
    """A full provider validation returning late cannot count as a current guard."""
    now = [0.0]
    target = Target()
    operation = ScrollTransaction(target, lambda: False, clock=lambda: now[0])
    monkeypatch.setattr(target, "check", lambda: now.__setitem__(0, elapsed))
    if elapsed < MAX_SECONDS:
        operation._check(restoring=restoring)
    else:
        with pytest.raises(CaptureError, match="time limit"):
            operation._check(restoring=restoring)
    assert not target.moves


@pytest.mark.parametrize("start", [0, 2400])
@pytest.mark.parametrize("elapsed", [33.0, 34.999, 35.0])
def test_cutoff_during_list_begin_keeps_only_previous_proof(
    monkeypatch: pytest.MonkeyPatch, start: int, elapsed: float
) -> None:
    """A slow pre-write item read does not make an unissued setter uncertain."""
    now = [0.0]
    target = ListTarget(start=start)
    initial = target.position()
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now[0], pause=lambda _: None
    )
    begin = target.steps.begin

    def slow_begin(position: ScrollPosition) -> None:
        begin(position)
        now[0] = elapsed

    monkeypatch.setattr(target.steps, "begin", slow_begin)
    result = operation.run()
    assert result.image is None
    assert "time limit" in result.message
    assert target.steps.closed
    assert target.steps.begins == [0]
    assert not target.steps.finishes
    if start and elapsed < MAX_SECONDS:
        assert target.moves == [0.0, initial.vertical]
        assert result.restoration == "restored"
        assert target.position() == initial
    else:
        assert target.moves == ([0.0] if start else [])
        assert result.restoration == (
            "not restored: time limit" if start else "unchanged"
        )
    assert operation._expected is not None


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("elapsed", [33.0, 34.999, 35.0])
def test_admitted_list_write_may_finish_proof_in_reserve(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, *, terminal: bool
) -> None:
    """Existing proof may finish, but the reserve admits no new forward setter."""
    now = [0.0]
    target = ListTarget()
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now[0], pause=lambda _: None
    )
    move = target.move

    def crossing_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        # Arrive just before the cutoff, then emulate a blocking admitted setter.
        crosses = vertical == 100.0 if terminal else not target.moves
        if crosses:
            now[0] = 32.999
        move(vertical, before_write=before_write)
        if crosses:
            now[0] = elapsed

    monkeypatch.setattr(target, "move", crossing_move)
    result = operation.run()
    if elapsed >= MAX_SECONDS:
        assert result.image is None
        assert operation._expected is None
        assert result.restoration == "not restored: time limit"
        assert target.moves[-1] == (100.0 if terminal else target.moves[0])
    else:
        assert result.restoration == "restored"
        assert target.native_position == 0
        assert operation._expected is not None
        if terminal:
            assert result.image is not None
            assert target.steps.confirmed
            assert target.steps.finishes == [60] * 7 + [52]
        else:
            assert result.image is None
            assert len(target.moves) == 2
            assert 0.0 < target.moves[0] < 100.0
            assert target.moves[-1] == 0.0
            assert target.steps.begins == [0]
            assert target.steps.finishes == [60]
            assert not target.steps.confirmed
    assert target.steps.closed


@pytest.mark.parametrize("stage", ["composition", "terminal", "cleanup", "restore"])
def test_late_terminal_work_cannot_restore_or_publish(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """No final-image work or cleanup can reset the transaction's hard deadline."""
    now = [0.0]
    target = ListTarget()
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now[0], pause=lambda _: None
    )
    if stage == "composition":
        compose = VerticalStitch.finish

        def late_compose(stitch: VerticalStitch) -> QImage:
            image = compose(stitch)
            now[0] = MAX_SECONDS
            return image

        monkeypatch.setattr(VerticalStitch, "finish", late_compose)
    elif stage == "terminal":
        confirm = target.steps.confirm_terminal

        def late_terminal(position: ScrollPosition) -> None:
            confirm(position)
            now[0] = MAX_SECONDS

        monkeypatch.setattr(target.steps, "confirm_terminal", late_terminal)
    elif stage == "cleanup":
        close = target.steps.close

        def late_cleanup() -> None:
            close()
            now[0] = MAX_SECONDS

        monkeypatch.setattr(target.steps, "close", late_cleanup)
    else:
        move = target.move

        def late_restore(
            vertical: float, *, before_write: Callable[[], None] | None = None
        ) -> None:
            move(vertical, before_write=before_write)
            if vertical == 0.0:
                now[0] = MAX_SECONDS

        monkeypatch.setattr(target, "move", late_restore)
    result = operation.run()
    assert result.image is None
    assert result.restoration.startswith("not restored")
    assert len(target.moves) == (9 if stage == "restore" else 8)
    assert target.moves[-1] == (0.0 if stage == "restore" else 100.0)
    if stage in ("composition", "terminal"):
        assert operation._terminal is None


@pytest.mark.parametrize("issued", [False, True])
def test_native_trace_counts_only_admitted_setter_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, issued: bool
) -> None:
    """A rejected preflight call is not movement evidence; an uncertain setter is."""
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    def fake_move(
        _target: UiaScrollTarget,
        _percent: float,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        assert before_write is not None
        before_write()
        raise OSError("Setter failed after admission")

    def admission() -> None:
        if not issued:
            raise CaptureError("Traversal cutoff")

    def helper() -> None:
        with pytest.raises((CaptureError, OSError)):
            UiaScrollTarget.move(
                object.__new__(UiaScrollTarget), 50.0, before_write=admission
            )

    monkeypatch.setattr(UiaScrollTarget, "move", fake_move)
    monkeypatch.setattr("scripts.simple_scroll_native.helper_main", helper)
    monkeypatch.setattr("sys.argv", ["-c", str(tmp_path), "edge"])
    _trace_helper()
    assert json.loads((tmp_path / "movement.json").read_text()) == {
        "writes": int(issued),
        "last_vertical": 50.0 if issued else None,
    }


@pytest.mark.parametrize("elapsed", [32.999, 33.0, 33.001, 35.0])
@pytest.mark.parametrize("percent", [0.0, 50.0])
def test_top_and_forward_setters_recheck_slow_position_admission(
    monkeypatch: pytest.MonkeyPatch, elapsed: float, percent: float
) -> None:
    """A blocking read never turns a missed admission into an uncertain write."""
    target = Target()
    now = [0.0]
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now[0], pause=lambda _: None
    )
    original = target.position()
    operation._expected = original
    position = target.position

    def slow_position() -> ScrollPosition:
        now[0] = elapsed
        return position()

    monkeypatch.setattr(target, "position", slow_position)
    if elapsed < 33.0:
        observed = operation._move(percent)
        assert operation._expected == observed
        assert target.moves == [percent]
    else:
        with pytest.raises(CaptureError, match="time limit"):
            operation._move(percent)
        assert operation._expected == original
        assert not operation._moved
        assert not target.moves


def test_late_initial_position_read_does_not_acknowledge_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late initial discovery has no image, write, or restoration authority."""
    target = Target()
    now = [0.0]
    operation = ScrollTransaction(target, lambda: False, clock=lambda: now[0])
    position = target.position

    def late_position() -> ScrollPosition:
        now[0] = operation._deadline
        return position()

    monkeypatch.setattr(target, "position", late_position)
    result = operation.run()
    assert result.image is None
    assert "time limit" in result.message
    assert result.restoration == "unchanged"
    assert operation._expected is None
    assert not target.moves


class NormalizingListTarget(ListTarget):
    """Change native page during forward writes without changing content pixels."""

    def __init__(
        self,
        pages: tuple[int, ...] = (1206, 1194, 1212),
        *,
        start: int = 0,
        total: int = 592,
    ) -> None:
        super().__init__(start=start, total=total)
        self.pages = iter(pages)

    @override
    def move(
        self, vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if before_write is not None:
            before_write()
        if 0 < vertical < 100 and self.moves[-1:] != [100.0]:
            self.page = next(self.pages, self.page)
        super().move(vertical)
        if 0 < vertical < 100:
            # Native units are not pixels. Keep a visible final tail even when
            # a smaller page requires one additional interior request.
            self.offset = min(self.offset, self.document.height() - 140)


@pytest.mark.parametrize("pages", [(1200, 1206), (1200, 1194), (1206, 1194, 1212)])
@pytest.mark.parametrize("start", [0, 2400])
def test_list_verified_interior_normalization_continues_without_rebasing(
    monkeypatch: pytest.MonkeyPatch, pages: tuple[int, ...], start: int
) -> None:
    """Both page directions and repeated changes require every exact pixel proof."""
    target = NormalizingListTarget(pages, start=start)
    original = target.scrollbar_state()
    operation = transaction(target)
    finish = target.steps.finish
    proven = []

    def verify(position: ScrollPosition, *, terminal: bool = False) -> int:
        assert operation._expected is None
        assert operation._terminal is None
        assert operation._native == original
        result = finish(position, terminal=terminal)
        proven.append(position)
        return result

    monkeypatch.setattr(target.steps, "finish", verify)
    result = operation.run()
    assert result.image is not None
    assert result.image.copy(0, 25, 80, 592) == target.document
    assert {position.view_size for position in proven[:-1]} >= {
        100 * page / 5999 for page in pages
    }
    assert all(0 < position.vertical < 100 for position in proven[:-1])
    assert target.steps.confirmed
    assert target.steps.closed
    assert target.native_position == original.position
    assert operation._native == original
    assert operation._terminal == proven[-1]
    assert result.restoration == "restored"


@pytest.mark.parametrize("phase", ["before-begin", "begin-return", "setter-admission"])
def test_list_normalized_cutoff_keeps_confirmed_tuple_without_restoration(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """A no-write 33s rejection preserves completed proof but permits no inverse."""
    now = [0.0]
    target = NormalizingListTarget()
    operation = ScrollTransaction(
        target, lambda: False, clock=lambda: now[0], pause=lambda _: None
    )
    begin, move = target.steps.begin, target.move

    def cutoff_begin(position: ScrollPosition) -> None:
        begin(position)
        if target.moves and phase == "begin-return":
            now[0] = 33.0

    def cutoff_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        if target.moves and phase == "setter-admission":
            now[0] = 33.0
        move(vertical, before_write=before_write)

    def clock() -> float:
        if phase == "before-begin" and target.moves and operation._expected is not None:
            now[0] = 33.0
        return now[0]

    monkeypatch.setattr(operation, "_clock", clock)
    monkeypatch.setattr(target.steps, "begin", cutoff_begin)
    monkeypatch.setattr(target, "move", cutoff_move)
    result = operation.run()
    assert result.image is None
    assert "traversal time limit" in result.message
    assert result.restoration == (
        "not restored: confirmed List normalization has no restoration proof"
    )
    assert len(target.moves) == 1
    assert operation._expected == target.position()
    assert operation._expected.view_size == 100 * 1206 / 5999
    assert operation._terminal is None
    assert not target.steps.confirmed
    assert len(target.steps.finishes) == 1
    assert target.steps.closed


def test_list_normalized_cancellation_keeps_only_confirmed_interior_tuple() -> None:
    """Cancellation after append is confirmed normalization, never an inverse right."""
    target = NormalizingListTarget()
    operation = ScrollTransaction(
        target,
        lambda: bool(target.moves) and operation._expected is not None,
        pause=lambda _: None,
    )
    result = operation.run()
    assert result.image is None
    assert "cancelled" in result.message
    assert result.restoration == (
        "not restored: confirmed List normalization has no restoration proof"
    )
    assert len(target.moves) == 1
    assert operation._expected == target.position()
    assert operation._terminal is None
    assert not target.steps.confirmed


@pytest.mark.parametrize("phase", ["begin", "issued", "spontaneous", "guard"])
def test_list_normalized_later_failure_never_gains_inverse_authority(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Rejected writes stay unresolved; current-state failures cannot authorize undo."""
    target = NormalizingListTarget()
    operation = transaction(target)
    begin, move = target.steps.begin, target.move

    def fail_begin(position: ScrollPosition) -> None:
        if target.moves:
            if phase == "begin":
                raise CaptureError("retained pair lost")
            if phase == "spontaneous":
                target.page += 1
                raise CaptureError("native page changed before begin")
        begin(position)

    def fail_move(
        vertical: float, *, before_write: Callable[[], None] | None = None
    ) -> None:
        issued = bool(target.moves)
        move(vertical, before_write=before_write)
        if issued and phase == "issued":
            raise CaptureError("issued setter failed")

    def guard() -> None:
        if target.moves and operation._expected is not None and phase == "guard":
            raise CaptureError("lost current state")

    monkeypatch.setattr(target.steps, "begin", fail_begin)
    monkeypatch.setattr(target, "move", fail_move)
    monkeypatch.setattr(target, "check", guard)
    result = operation.run()
    assert result.image is None
    assert result.restoration == (
        "not restored: the target or user state changed"
        if phase == "guard"
        else "not restored: the provider write is unresolved"
    )
    assert len(target.moves) == (2 if phase == "issued" else 1)
    assert (operation._expected is None) == (phase != "guard")
    assert operation._terminal is None
    assert not target.steps.confirmed
    assert target.steps.closed


@pytest.mark.parametrize("view", [0.0, -1.0, 100.0, 101.0, float("nan"), float("inf")])
def test_list_interior_changed_view_must_remain_finite_and_scrollable(
    monkeypatch: pytest.MonkeyPatch, view: float
) -> None:
    """Forward List admission never treats invalid provider fractions as candidates."""
    target = NormalizingListTarget()
    read = target.position
    monkeypatch.setattr(
        target,
        "position",
        lambda: replace(read(), view_size=view) if target.moves else read(),
    )
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "moved unexpectedly" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert operation._terminal is None
    assert len(target.moves) == 1
    assert not target.steps.finishes


@pytest.mark.parametrize("failure", ["delta", "zero", "pixels", "header", "footer"])
def test_list_normalization_does_not_bypass_physical_proof(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Changed pages still need item agreement, true motion and exact fixed pixels."""
    target = NormalizingListTarget()
    operation = transaction(target)
    frame = target.frame
    if failure == "delta":
        monkeypatch.setattr(target.steps, "finish", lambda *_args, **_kwargs: 1)

    def changed_frame() -> QImage:
        if failure == "zero":
            offset = target.offset
            target.offset = 0
            image = frame()
            target.offset = offset
        else:
            image = frame()
        if target.moves and failure in ("pixels", "header", "footer"):
            row = {"pixels": 60, "header": 0, "footer": 161}[failure]
            image.setPixelColor(20, row, QColor("red"))
        return image

    monkeypatch.setattr(target, "frame", changed_frame)
    result = operation.run()
    assert result.image is None
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert operation._terminal is None
    assert len(target.moves) == 1
    assert not target.steps.confirmed


@pytest.mark.parametrize("total", [580, 610])
def test_list_interior_normalization_keeps_both_final_height_bounds(total: int) -> None:
    """Neither verified interior motion nor a native endpoint relaxes either2% gate."""
    target = NormalizingListTarget(total=total)
    operation = transaction(target)
    result = operation.run()
    assert result.image is None
    assert "disagree with the panel's extent" in result.message
    assert result.restoration == "not restored: the provider write is unresolved"
    assert operation._expected is None
    assert operation._terminal is None
    assert target.moves[-1] == 100.0
    assert not target.steps.confirmed


@pytest.mark.parametrize(
    ("requested", "observed"), [(0.0, 0.0), (20.0, 20.0), (30.0, 35.0)]
)
def test_list_changed_view_does_not_admit_backward_requests_or_overshoot(
    monkeypatch: pytest.MonkeyPatch, requested: float, observed: float
) -> None:
    """Only forward candidates within the existing direction/overshoot gate survive."""
    target = ListTarget()
    operation = transaction(target)
    operation._list_steps = target.steps
    before = ScrollPosition(0, 25, 20)
    current = ScrollPosition(0, observed, 20.1)
    monkeypatch.setattr(target, "position", lambda: current)
    with pytest.raises(CaptureError, match="moved unexpectedly"):
        operation._settle(before, requested)
    assert not target.moves
    assert operation._expected is None
    assert operation._terminal is None
