"""Settling must refuse animation, stale work and unbounded acquisition."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from verdiclip.capture.scroll.models import ScrollFrame, ScrollTarget
from verdiclip.capture.scroll.settling import FrameSettler, SettleStatus
from verdiclip.geometry import Rect


class Viewport:
    """Small deterministic observations without desktop interaction."""

    TARGET = ScrollTarget("fixture", "Example", Rect(0, 0, 12, 20))

    @staticmethod
    def frame(
        sequence: int, *, color: int = 0xFF123456, end: bool = False
    ) -> ScrollFrame:
        """Give each sample an independent mutable source image."""
        image = QImage(12, 20, QImage.Format.Format_ARGB32)
        image.fill(color)
        return ScrollFrame(1, sequence, "fixture", 0, sequence / 10, image, end)

    @staticmethod
    def begin(**kwargs: int) -> tuple[FrameSettler, int]:
        """Start one request at a known monotonic origin."""
        gate = FrameSettler(**kwargs)
        return gate, gate.begin(Viewport.TARGET, token=1, now=0)


def test_stable_pixels_transfer_once_without_implying_end() -> None:
    """Three frames admit one isolated value; repeated delivery is impossible."""
    gate, request = Viewport.begin()
    frame = Viewport.frame(3)
    assert gate.take(request) is None
    assert gate.submit(request, Viewport.frame(1)) is SettleStatus.WAITING
    assert gate.submit(request, Viewport.frame(2)) is SettleStatus.WAITING
    assert gate.submit(request, frame) is SettleStatus.STABLE
    frame.image.fill(0)
    assert gate.submit(request, Viewport.frame(4)) is SettleStatus.STALE
    ready = gate.take(request)
    assert ready is not None
    assert not ready.at_end
    assert ready.image.pixel(0, 0) == 0xFF123456
    assert ready.sequence == 3
    assert gate.take(request) is None


def test_animation_resets_evidence_then_exhausts_samples() -> None:
    """Repeated near-matches must never be mistaken for a settled viewport."""
    gate, request = Viewport.begin(max_samples=5)
    for sequence in range(1, 5):
        assert (
            gate.submit(request, Viewport.frame(sequence, color=sequence))
            is SettleStatus.WAITING
        )
    assert gate.submit(request, Viewport.frame(5)) is SettleStatus.EXHAUSTED
    assert gate.take(request) is None


def test_semantic_end_must_settle_with_the_pixels() -> None:
    """A changing provider end flag cannot piggyback on old pixel stability."""
    gate, request = Viewport.begin()
    for sequence, end in [(1, False), (2, False), (3, True), (4, True)]:
        assert (
            gate.submit(request, Viewport.frame(sequence, end=end))
            is SettleStatus.WAITING
        )
    assert gate.submit(request, Viewport.frame(5, end=True)) is SettleStatus.STABLE
    ready = gate.take(request)
    assert ready is not None
    assert ready.at_end


def test_duplicate_and_out_of_order_observations_do_not_build_confidence() -> None:
    """Retrying one callback cannot manufacture three independent samples."""
    gate, request = Viewport.begin()
    frame = Viewport.frame(1)
    assert gate.submit(request, frame) is SettleStatus.WAITING
    assert gate.submit(request, frame) is SettleStatus.STALE
    old_time = ScrollFrame(1, 2, "fixture", 0, 0.1, frame.image)
    assert gate.submit(request, old_time) is SettleStatus.STALE
    wrong_token = ScrollFrame(2, 2, "fixture", 0, 0.2, frame.image)
    assert gate.submit(request, wrong_token) is SettleStatus.STALE
    assert gate.submit(request, Viewport.frame(2)) is SettleStatus.WAITING
    assert gate.submit(request, Viewport.frame(3)) is SettleStatus.STABLE


def test_new_request_and_cancellation_reject_late_callbacks() -> None:
    """The same session token does not authorize work from a previous step."""
    gate, old = Viewport.begin()
    request = gate.begin(Viewport.TARGET, token=1, now=0)
    assert gate.submit(old, Viewport.frame(1)) is SettleStatus.STALE
    assert gate.expire(old, now=5) is SettleStatus.STALE
    assert gate.submit(request, Viewport.frame(1)) is SettleStatus.WAITING
    gate.cancel()
    assert gate.submit(request, Viewport.frame(2)) is SettleStatus.STALE
    assert gate.expire(request, now=5) is SettleStatus.STALE


@pytest.mark.parametrize("kind", ["identity", "epoch", "size", "null"])
def test_geometry_or_identity_change_invalidates_request(kind: str) -> None:
    """Never settle pixels from another window, resize, or failed capture."""
    gate, request = Viewport.begin()
    source = Viewport.frame(1)
    image = QImage() if kind == "null" else source.image
    if kind == "size":
        image = image.copy(0, 0, 6, 20)
    frame = ScrollFrame(
        1,
        1,
        "other" if kind == "identity" else "fixture",
        int(kind == "epoch"),
        0.1,
        image,
    )
    assert gate.submit(request, frame) is SettleStatus.TARGET_CHANGED
    assert gate.submit(request, Viewport.frame(2)) is SettleStatus.STALE


def test_deadline_fires_without_a_producer_callback() -> None:
    """A hung backend cannot leave the UI waiting indefinitely."""
    gate, request = Viewport.begin()
    assert gate.expire(request, now=1.99) is SettleStatus.WAITING
    assert gate.expire(request, now=2) is SettleStatus.EXHAUSTED
    assert gate.take(request) is None


def test_late_frame_and_large_frame_are_refused_before_stability() -> None:
    """Time and memory bounds apply even when identical samples arrive."""
    gate, request = Viewport.begin(max_frame_pixels=239)
    assert gate.submit(request, Viewport.frame(1)) is SettleStatus.EXHAUSTED
    gate, request = Viewport.begin()
    assert gate.submit(request, Viewport.frame(20)) is SettleStatus.EXHAUSTED


@pytest.mark.parametrize(("observations", "max_samples"), [(1, 16), (3, 2)])
def test_invalid_evidence_budget(observations: int, max_samples: int) -> None:
    """No configuration can reduce settling to a single unverified frame."""
    with pytest.raises(ValueError, match="bounded repeated"):
        FrameSettler(observations=observations, max_samples=max_samples)


@pytest.mark.parametrize("seconds", [0.0, float("inf"), float("nan")])
def test_invalid_time_budget(seconds: float) -> None:
    """A backend deadline is always finite and positive."""
    with pytest.raises(ValueError, match="finite and positive"):
        FrameSettler(max_seconds=seconds)
