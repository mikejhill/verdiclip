"""Public storage guard failures preserve the fixed pixel ownership contract."""

from __future__ import annotations

import threading
from typing import Any, cast

import pytest
from tests.capture.test_moving_edit_pixel_ownership import (
    HEADER,
    IDENTITY,
    OBSERVATION,
    RGB,
    candidate,
)

from verdiclip.capture import moving_edit_frame as owned


def test_candidate_rejects_wrong_header_before_accessing_storage() -> None:
    """Untrusted header shape cannot cause access to an unrelated pixel owner."""
    with pytest.raises(ValueError, match="immutable frame header"):
        owned.VerifiedCandidate(cast("Any", None), cast("Any", object()), OBSERVATION)


def test_candidate_rejects_short_owned_raster() -> None:
    """A real reservation does not authorize fewer bytes than the full frame."""
    slots = owned.ParentPixelSlots()
    storage = slots.reserve("current_frozen")
    storage.bind(RGB[:-1])
    try:
        with pytest.raises(ValueError, match="exact frame size"):
            owned.VerifiedCandidate(storage, HEADER, OBSERVATION)
    finally:
        storage.release()
    assert slots.reserved_bytes == 0


def test_released_candidate_has_no_readable_pixels() -> None:
    """Releasing the real owner cannot leave a usable candidate alias."""
    value = candidate()
    value.storage.release()
    with pytest.raises(ValueError, match="storage is unavailable"):
        _ = value.rgb


@pytest.mark.parametrize("digest", [b"", bytearray(32), b"x" * 31])
def test_capsule_rejects_noncanonical_receipt_digest(digest: object) -> None:
    """A verified raster still needs an exact immutable receipt binding."""
    value = candidate()
    try:
        with pytest.raises(ValueError, match="immutable frame capsule"):
            owned.VerifiedFrame(value, cast("Any", digest))
    finally:
        value.storage.release()


def test_capsule_exposes_the_same_bounded_observation() -> None:
    """The consumer accessor does not copy or replace canonical metadata."""
    value = candidate()
    frame = owned.VerifiedFrame(value, b"d" * 32)
    assert frame.observation is value.observation
    assert frame.identity is value.header.identity
    value.storage.release()


def test_sink_rejects_peer_allocation_domain() -> None:
    """Peer slots cannot be substituted for the parent collector reservation."""
    slots = owned.PeerPixelSlots()
    with pytest.raises(ValueError, match="parent pixel slot domain"):
        owned.FrameSink(IDENTITY, lambda: None, slots=cast("Any", slots))
    assert slots.reserved_bytes == 0


def test_wrong_thread_feed_rejects_without_copying_bytes() -> None:
    """Actual cross-thread use fails before changing the sink byte counter."""
    sink = owned.FrameSink(IDENTITY, lambda: None)
    errors: list[BaseException] = []

    def feed() -> None:
        try:
            sink.feed(b"x")
        except BaseException as error:  # noqa: BLE001 - retain the exact worker failure
            errors.append(error)

    thread = threading.Thread(target=feed)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "wrong owner" in str(errors[0])
    assert sink._received == 0
    sink.discard()
    assert sink.allocated_bytes == 0
