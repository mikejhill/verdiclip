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


def scaled_perimeter_frame(source: QImage, offset: int, percent: int) -> QImage:
    """Model scaled 2px outlines and 3px borders, not a native DPI observation."""
    result = source.copy(0, offset, source.width(), 120)
    border = (3 * percent + 99) // 100
    outline = (2 * percent + 99) // 100
    top = (percent + 99) // 100
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
    source = document(width=120)
    first = scaled_perimeter_frame(source, 0, percent)
    second = scaled_perimeter_frame(source, 60, percent)
    for frame in (first, second):
        for y in range(120):
            frame.setPixelColor(0, y, QColor(255, y, 255))
    with pytest.raises(CaptureError, match="cannot be joined"):
        VerticalStitch(first).append(second, 60.0)


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
