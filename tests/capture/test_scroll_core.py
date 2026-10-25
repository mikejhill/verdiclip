"""Offscreen goldens for bounded, conservative scrolling-image composition."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from verdiclip.capture.reviewed import CaptureCompleteness, CaptureKind
from verdiclip.capture.scroll.matching import MatchStatus, OverlapMatcher
from verdiclip.capture.scroll.models import (
    ScrollFrame,
    ScrollLimits,
    ScrollTarget,
    StopReason,
)
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class Documents:
    """Known pixels let goldens detect any missing, duplicated or altered row."""

    @staticmethod
    def image(width: int = 96, height: int = 480) -> QImage:
        """Return a reproducible opaque image with unique row and column detail."""
        data = bytearray(
            hashlib.shake_256(b"scroll-golden-2401").digest(width * height * 4)
        )
        data[3::4] = bytes([255]) * (width * height)
        return QImage(data, width, height, QImage.Format.Format_ARGB32).copy()

    @staticmethod
    def frame(
        session: ScrollSession, image: QImage, sequence: int, *, at_end: bool = False
    ) -> ScrollFrame:
        """Give each observation an ordered identity and monotonic timestamp."""
        return ScrollFrame(
            session.token, sequence, "fixture", 0, float(sequence), image, at_end
        )

    @staticmethod
    def start(
        tmp_path: Path, *, limits: ScrollLimits | None = None
    ) -> tuple[ScrollSession, QImage]:
        """Begin with the first known 120-row viewport in a private spool."""
        image = Documents.image()
        session = ScrollSession(tmp_path, limits=limits)
        session.start(
            ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)),
            image.copy(0, 0, 96, 120),
            now=0.0,
            taken_at=datetime(2026, 10, 25, tzinfo=UTC),
        )
        return session, image


@pytest.mark.parametrize("shift", [1, 7, 29, 60, 90])
def test_unique_overlap_preserves_exact_displacement(shift: int) -> None:
    """A verified displacement identifies exactly the novel source rows."""
    image = Documents.image()
    match = OverlapMatcher().compare(
        image.copy(0, 0, 96, 120), image.copy(0, shift, 96, 120)
    )
    assert match.status is MatchStatus.VERIFIED
    assert match.shift == shift


def test_no_overlap_is_not_invented() -> None:
    """A skipped viewport cannot be stitched by picking the least bad seam."""
    image = Documents.image()
    match = OverlapMatcher().compare(
        image.copy(0, 0, 96, 120), image.copy(0, 200, 96, 120)
    )
    assert match.status is MatchStatus.UNMATCHED
    assert match.shift is None


def test_periodic_rows_are_ambiguous() -> None:
    """Repeated data must not be globally deduplicated into a guessed offset."""
    source = Documents.image(height=16)
    image = QImage(96, 240, QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            image.setPixel(x, y, source.pixel(x, y % 16))
    match = OverlapMatcher().compare(
        image.copy(0, 0, 96, 120), image.copy(0, 60, 96, 120)
    )
    assert match.status is MatchStatus.AMBIGUOUS
    assert match.shift is None


def test_stationary_frame_is_not_proof_of_end() -> None:
    """An unchanged screen gives no independently measured document extent."""
    image = Documents.image(height=120)
    match = OverlapMatcher().compare(image, image)
    assert match.status is MatchStatus.STATIONARY
    assert match.shift is None


def test_stitched_image_equals_source_without_seams(tmp_path: Path) -> None:
    """Two verified movements produce one exact contiguous source range."""
    session, source = Documents.start(tmp_path)
    try:
        for sequence, offset in enumerate((60, 120), start=1):
            session.submit(
                Documents.frame(session, source.copy(0, offset, 96, 120), sequence)
            )
        session.stop()
        result = session.accept()
        assert result.image == source.copy(0, 0, 96, 240)
        assert result.source_kind is CaptureKind.SCROLLING
        assert result.completeness is CaptureCompleteness.PARTIAL
        assert result.stop_reason == StopReason.USER_STOP.value
    finally:
        session.close()


def test_uncertain_candidate_keeps_only_verified_prefix(tmp_path: Path) -> None:
    """A missing-overlap candidate remains outside the accepted image."""
    session, source = Documents.start(tmp_path)
    try:
        session.submit(Documents.frame(session, source.copy(0, 220, 96, 120), 1))
        assert session.state is ScrollState.PAUSED
        session.stop()
        assert session.accept().image == source.copy(0, 0, 96, 120)
    finally:
        session.close()


def test_cancelled_ticket_cannot_append_or_deliver(tmp_path: Path) -> None:
    """Late worker results cannot revive a cancelled capture."""
    session, source = Documents.start(tmp_path)
    stale = Documents.frame(session, source.copy(0, 60, 96, 120), 1)
    session.cancel()
    assert session.submit(stale) is MatchStatus.STALE
    assert session.state is ScrollState.CANCELLED
    with pytest.raises(CaptureError, match="review"):
        session.accept()
    assert not list(tmp_path.iterdir())


def test_repeated_callback_does_not_duplicate_rows(tmp_path: Path) -> None:
    """The same sequence can commit at most once, even if repeated later."""
    session, source = Documents.start(tmp_path)
    try:
        frame = Documents.frame(session, source.copy(0, 60, 96, 120), 1)
        session.submit(frame)
        assert session.submit(frame) is MatchStatus.STALE
        session.stop()
        assert session.accept().image.height() == 180
    finally:
        session.close()


def test_resize_or_target_change_cannot_merge_epochs(tmp_path: Path) -> None:
    """A stale target geometry ends acquisition without altering the prefix."""
    session, source = Documents.start(tmp_path)
    try:
        changed = ScrollFrame(
            session.token, 1, "fixture", 1, 1.0, source.copy(0, 60, 96, 120)
        )
        session.submit(changed)
        assert session.state is ScrollState.REVIEW
        assert session.result is not None
        assert session.result.stop_reason is StopReason.TARGET_CHANGED
        assert session.accept().image.height() == 120
    finally:
        session.close()


def test_two_stationary_end_observations_are_required(tmp_path: Path) -> None:
    """An end hint alone never implies the entire source document was captured."""
    session, source = Documents.start(tmp_path)
    try:
        viewport = source.copy(0, 0, 96, 120)
        session.submit(Documents.frame(session, viewport, 1, at_end=True))
        assert session.state is ScrollState.PAUSED
        session.submit(Documents.frame(session, viewport, 2, at_end=True))
        assert session.state is ScrollState.REVIEW
        assert session.accept().completeness is CaptureCompleteness.REPORTED_END
    finally:
        session.close()


def test_frame_budget_preserves_last_verified_image(tmp_path: Path) -> None:
    """Capacity is checked before writing any rows from an over-budget frame."""
    session, source = Documents.start(tmp_path, limits=ScrollLimits(max_frames=1))
    try:
        session.submit(Documents.frame(session, source.copy(0, 60, 96, 120), 1))
        assert session.result is not None
        assert session.result.stop_reason is StopReason.LIMIT
        assert session.accept().image.height() == 120
    finally:
        session.close()


def test_large_result_remains_reviewable_when_flatten_is_unsafe(tmp_path: Path) -> None:
    """Editor size refusal leaves the spool available for bounded page slices."""
    session, source = Documents.start(
        tmp_path, limits=ScrollLimits(max_flatten_pixels=96 * 100)
    )
    try:
        session.stop()
        with pytest.raises(CaptureError, match="page"):
            session.accept()
        assert session.state is ScrollState.REVIEW
        assert session.page(0, 80) == source.copy(0, 0, 96, 80)
    finally:
        session.close()


def test_same_timestamp_cannot_count_as_second_end_observation(tmp_path: Path) -> None:
    """Renumbering one observation cannot turn it into end-of-range evidence."""
    session, source = Documents.start(tmp_path)
    try:
        viewport = source.copy(0, 0, 96, 120)
        session.submit(Documents.frame(session, viewport, 1, at_end=True))
        same_observation = ScrollFrame(
            session.token, 2, "fixture", 0, 1.0, viewport, at_end=True
        )
        assert session.submit(same_observation) is MatchStatus.STALE
        assert session.state is ScrollState.PAUSED
    finally:
        session.close()


def test_duration_limit_does_not_reset_on_resume(tmp_path: Path) -> None:
    """Retry cannot reset the session budget and turn a finite capture infinite."""
    session, source = Documents.start(tmp_path, limits=ScrollLimits(max_seconds=2))
    try:
        session.submit(Documents.frame(session, source.copy(0, 0, 96, 120), 1))
        session.resume(ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)))
        late = ScrollFrame(
            session.token, 1, "fixture", 0, 2.0, source.copy(0, 60, 96, 120)
        )
        session.submit(late)
        assert session.result is not None
        assert session.result.stop_reason is StopReason.LIMIT
        assert session.accept().image.height() == 120
    finally:
        session.close()


def test_acceptance_requires_explicit_review(tmp_path: Path) -> None:
    """Acquiring pixels must never implicitly trigger ordinary after-capture actions."""
    session, _ = Documents.start(tmp_path)
    try:
        with pytest.raises(CaptureError, match="review"):
            session.accept()
        assert session.state is ScrollState.CAPTURING
    finally:
        session.close()


def test_renumbered_old_sequence_with_new_time_is_ignored(tmp_path: Path) -> None:
    """A retry cannot replace sequence identity merely by updating its timestamp."""
    session, source = Documents.start(tmp_path)
    try:
        session.submit(Documents.frame(session, source.copy(0, 60, 96, 120), 1))
        duplicate = ScrollFrame(
            session.token, 1, "fixture", 0, 2.0, source.copy(0, 120, 96, 120)
        )
        assert session.submit(duplicate) is MatchStatus.STALE
        session.stop()
        assert session.accept().image == source.copy(0, 0, 96, 180)
    finally:
        session.close()


def test_resume_rejects_late_results_from_previous_generation(tmp_path: Path) -> None:
    """A result arriving after a revalidated retry still belongs to the old lease."""
    session, source = Documents.start(tmp_path)
    try:
        session.submit(Documents.frame(session, source.copy(0, 0, 96, 120), 1))
        old = Documents.frame(session, source.copy(0, 60, 96, 120), 2)
        session.resume(ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)))
        assert session.submit(old) is MatchStatus.STALE
        session.stop()
        assert session.accept().image.height() == 120
    finally:
        session.close()


@pytest.mark.parametrize("seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_capture_duration_must_be_bounded(seconds: float) -> None:
    """Invalid duration settings cannot disable the capture safety deadline."""
    with pytest.raises(ValueError, match="duration"):
        ScrollLimits(max_seconds=seconds)


def test_zero_resource_limit_is_invalid() -> None:
    """A disabled frame count is an explicit configuration error."""
    with pytest.raises(ValueError, match="positive"):
        ScrollLimits(max_frames=0)


@pytest.mark.parametrize(
    "viewport",
    [Rect(0, 0, 0, 120), Rect(0.5, 0, 96, 120), Rect(0, 0, float("inf"), 120)],
)
def test_invalid_physical_viewport_is_rejected(viewport: Rect) -> None:
    """Empty, fractional or nonfinite screen rectangles cannot become a target."""
    with pytest.raises(ValueError, match=r"viewport|coordinates"):
        ScrollTarget("fixture", "Example", viewport)


def test_nonfinite_frame_timestamp_is_rejected() -> None:
    """NaN must not bypass stale-result and deadline comparisons."""
    with pytest.raises(ValueError, match="finite"):
        ScrollFrame(1, 1, "fixture", 0, float("nan"), QImage())


def test_image_size_change_is_an_explicit_registration_outcome() -> None:
    """Resizing is never concealed by scaling pixels to fit another frame."""
    result = OverlapMatcher().compare(
        Documents.image(height=120), Documents.image(height=121)
    )
    assert result.status is MatchStatus.GEOMETRY_CHANGED


def test_empty_viewport_cannot_be_registered() -> None:
    """An empty frame supplies no registration evidence."""
    with pytest.raises(CaptureError, match="empty"):
        OverlapMatcher().compare(QImage(), QImage())


def test_untextured_bands_cannot_verify_a_seam() -> None:
    """One detailed column region cannot certify a mostly blank document."""
    source = Documents.image()
    for y in range(source.height()):
        for x in range(64):
            source.setPixel(x, y, 0xFFFFFFFF)
    result = OverlapMatcher().compare(
        source.copy(0, 0, 96, 120), source.copy(0, 60, 96, 120)
    )
    assert result.status is MatchStatus.AMBIGUOUS


def test_low_row_diversity_cannot_verify_a_seam() -> None:
    """A single small marker in an otherwise blank viewport is insufficient."""
    source = QImage(96, 240, QImage.Format.Format_ARGB32)
    source.fill(0xFFFFFFFF)
    for y in range(70, 74):
        for x in range(96):
            source.setPixel(x, y, 0xFF000000 + y)
    result = OverlapMatcher().compare(
        source.copy(0, 0, 96, 120), source.copy(0, 60, 96, 120)
    )
    assert result.status is MatchStatus.AMBIGUOUS


def test_session_cannot_restart_after_acquisition(tmp_path: Path) -> None:
    """A new request cannot replace a session's already committed first frame."""
    session, source = Documents.start(tmp_path)
    try:
        with pytest.raises(CaptureError, match="already started"):
            session.start(
                ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)),
                source.copy(0, 0, 96, 120),
                now=1.0,
                taken_at=datetime(2026, 10, 25, tzinfo=UTC),
            )
    finally:
        session.close()


def test_mismatched_frame_dimensions_preserve_reviewable_prefix(tmp_path: Path) -> None:
    """Even a provider that forgets its epoch must not merge resized frames."""
    session, source = Documents.start(tmp_path)
    try:
        frame = Documents.frame(session, source.copy(0, 60, 96, 119), 1)
        assert session.submit(frame) is MatchStatus.GEOMETRY_CHANGED
        assert session.accept().image.height() == 120
    finally:
        session.close()


def test_review_page_is_unavailable_during_acquisition(tmp_path: Path) -> None:
    """Review consumers cannot race a mutable acquisition manifest."""
    session, _ = Documents.start(tmp_path)
    try:
        with pytest.raises(CaptureError, match="review"):
            session.page(0, 100)
    finally:
        session.close()


def test_resume_requires_same_target_identity(tmp_path: Path) -> None:
    """Retry cannot turn a preserved prefix into content from another window."""
    session, source = Documents.start(tmp_path)
    try:
        session.submit(Documents.frame(session, source.copy(0, 0, 96, 120), 1))
        with pytest.raises(CaptureError, match="unchanged"):
            session.resume(ScrollTarget("other", "Other", Rect(0, 0, 96, 120)))
    finally:
        session.close()


def test_stop_is_not_allowed_before_acquisition(tmp_path: Path) -> None:
    """An idle session cannot manufacture an empty successful review."""
    session = ScrollSession(tmp_path)
    try:
        with pytest.raises(CaptureError, match="not active"):
            session.stop()
    finally:
        session.close()


def test_invalid_start_time_does_not_allocate_storage(tmp_path: Path) -> None:
    """A malformed clock value cannot start a session without a working deadline."""
    session = ScrollSession(tmp_path)
    try:
        with pytest.raises(CaptureError, match="finite"):
            session.start(
                ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)),
                Documents.image(height=120),
                now=float("nan"),
                taken_at=datetime(2026, 10, 25, tzinfo=UTC),
            )
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_overlarge_initial_viewport_is_rejected_before_spooling(tmp_path: Path) -> None:
    """Frame memory limits apply before the first PNG or registration copy."""
    session = ScrollSession(tmp_path, limits=ScrollLimits(max_frame_pixels=100))
    try:
        with pytest.raises(CaptureError, match="working-memory"):
            session.start(
                ScrollTarget("fixture", "Example", Rect(0, 0, 96, 120)),
                Documents.image(height=120),
                now=0.0,
                taken_at=datetime(2026, 10, 25, tzinfo=UTC),
            )
        assert not list(tmp_path.iterdir())
    finally:
        session.close()


def test_disk_quota_stops_with_existing_prefix_available(tmp_path: Path) -> None:
    """A new strip exceeding the quota cannot destroy already verified rows."""
    session, source = Documents.start(
        tmp_path, limits=ScrollLimits(max_disk_bytes=60_000)
    )
    try:
        session.submit(Documents.frame(session, source.copy(0, 60, 96, 120), 1))
        assert session.result is not None
        assert session.result.stop_reason is StopReason.LIMIT
        assert session.accept().image.height() == 120
    finally:
        session.close()
