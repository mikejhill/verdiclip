"""Fixed EDIT pixel slots and publication, without a native/Qt qualification claim."""

from __future__ import annotations

import gc
import hashlib
import threading
from dataclasses import replace
from functools import partial
from unittest.mock import Mock

import pytest

from verdiclip.capture import moving_edit_frame as owned
from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity, FrameStream

N = 610_827
OBSERVATION = b"o" * 32_768
RGB = b"r" * N
IDENTITY = FrameIdentity(
    b"s" * 16, 3, 1, -100, -200, 493, 413, hashlib.sha256(OBSERVATION).digest()
)
HEADER = FrameHeader(IDENTITY, hashlib.sha256(RGB).digest())
RECEIPT = b'{"passed":true}'


def filled(slots: owned.ParentPixelSlots | None = None) -> owned.FrameSink:
    """Use the ten exact bounded credits, never one full-raster feed."""
    sink = owned.FrameSink(IDENTITY, lambda: None, slots=slots)
    for offset in range(0, N, 65_536):
        sink.feed(RGB[offset : offset + 65_536])
    return sink


def candidate() -> owned.VerifiedCandidate:
    """Pair complete bytes with a caller-verified real EOF boundary."""
    stream = FrameStream(HEADER.encode(), IDENTITY)
    for offset in range(0, N, 65_536):
        stream.feed(RGB[offset : offset + 65_536])
    return filled().freeze(stream.finish(), OBSERVATION)


def test_fixed_pixel_ledger_and_profile_caps() -> None:
    """Capacity describes listed payload slots, not process RSS or escaped aliases."""
    assert owned.MAX_FRAME_BYTES == N
    assert owned.MAX_OBSERVATION_BYTES == 32_768
    assert owned.MAX_FRAME_PROOF_BYTES == 49_152
    assert owned.MAX_MESSAGE_BYTES == 65_536
    assert owned.PARENT_KNOWN_PIXEL_BYTES == 7_530_969 < owned.PARENT_PIXEL_RESERVATION
    assert owned.PEER_KNOWN_PIXEL_BYTES == 22_140_517 < owned.PEER_PIXEL_RESERVATION
    assert owned.PARENT_PIXEL_RESERVATION == 12 * 1024 * 1024
    assert owned.PEER_PIXEL_RESERVATION == 32 * 1024 * 1024
    assert sum(owned.PARENT_PIXEL_SLOTS.values()) == owned.PARENT_KNOWN_PIXEL_BYTES
    assert sum(owned.PEER_PIXEL_SLOTS.values()) == owned.PEER_KNOWN_PIXEL_BYTES


def test_freeze_reserves_both_slots_and_drops_only_owned_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen candidate aliases keep their slot until the final owning object dies."""
    slots = owned.ParentPixelSlots()
    sink = filled(slots)
    original = owned._freeze

    def freeze(buffer: bytearray) -> bytes:
        assert slots.reserved_bytes == 2 * N
        return original(buffer)

    monkeypatch.setattr(owned, "_freeze", freeze)
    result = sink.freeze(HEADER, OBSERVATION)
    assert result.rgb == RGB
    assert result.observation == OBSERVATION
    assert sink.allocated_bytes == 0
    assert slots.reserved_bytes == N
    frame = owned.VerifiedFrame(result, b"d" * 32)
    del result
    gc.collect()
    assert slots.reserved_bytes == N
    assert frame.rgb == RGB
    del frame
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("shape", [(492, 413), (493, 412), (494, 413), (493, 414)])
def test_wrong_geometry_rejects_before_allocation(
    monkeypatch: pytest.MonkeyPatch, shape: tuple[int, int]
) -> None:
    """The new profile accepts the exact full client, not a fitted crop."""
    allocation = Mock(side_effect=AssertionError("unexpected allocation"))
    monkeypatch.setattr(owned, "_allocate", allocation)
    with pytest.raises(ValueError, match=r".+"):
        owned.FrameSink(
            replace(IDENTITY, width=shape[0], height=shape[1]), lambda: None
        )
    allocation.assert_not_called()


@pytest.mark.parametrize("operation", ["_allocate", "_copy", "_freeze"])
def test_storage_operation_failure_releases_all_slots(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Exceptional allocation/copy/freeze retains no owned pixel storage."""
    slots = owned.ParentPixelSlots()
    sink = None if operation == "_allocate" else filled(slots)
    monkeypatch.setattr(owned, operation, Mock(side_effect=MemoryError(operation)))
    if sink is None:
        action = partial(owned.FrameSink, IDENTITY, lambda: None, slots=slots)
    elif operation == "_copy":
        sink.discard()
        sink = owned.FrameSink(IDENTITY, lambda: None, slots=slots)
        action = partial(sink.feed, b"x")
    else:
        action = partial(sink.freeze, HEADER, OBSERVATION)
    with pytest.raises(MemoryError, match=operation):
        action()
    gc.collect()
    assert slots.reserved_bytes == 0


def test_one_current_sink_and_no_future_allocation_after_failed_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second current slot or a canceled check cannot start pixel allocation."""
    slots = owned.ParentPixelSlots()
    sink = filled(slots)
    allocation = Mock(side_effect=AssertionError("unexpected allocation"))
    monkeypatch.setattr(owned, "_allocate", allocation)
    with pytest.raises(ValueError, match="slot"):
        owned.FrameSink(IDENTITY, lambda: None, slots=slots)
    sink.discard()
    with pytest.raises(TimeoutError):
        owned.FrameSink(
            IDENTITY, Mock(side_effect=TimeoutError("expired")), slots=slots
        )
    allocation.assert_not_called()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize("operation", ["_allocate", "_copy", "_freeze"])
def test_cancel_after_heavy_operation_does_not_leave_storage(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Original deadline/cancel admission is rechecked after every pixel operation."""
    cancelled = threading.Event()
    slots = owned.ParentPixelSlots()

    def check() -> None:
        if cancelled.is_set():
            raise TimeoutError("cancelled")

    sink = (
        None
        if operation == "_allocate"
        else owned.FrameSink(IDENTITY, check, slots=slots)
    )
    if operation == "_freeze":
        assert sink is not None
        for offset in range(0, N, 65_536):
            sink.feed(RGB[offset : offset + 65_536])
    original = getattr(owned, operation)

    def fail_after(*args: object) -> object:
        result = original(*args)
        cancelled.set()
        return result

    monkeypatch.setattr(owned, operation, fail_after)
    if sink is None:
        action = partial(owned.FrameSink, IDENTITY, check, slots=slots)
    elif operation == "_copy":
        action = partial(sink.feed, b"x")
    else:
        action = partial(sink.freeze, HEADER, OBSERVATION)
    with pytest.raises(TimeoutError, match="cancelled"):
        action()
    gc.collect()
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize(
    "bad", [b"", b"x" * 32_769, bytearray(b"x")], ids=["empty", "oversized", "mutable"]
)
def test_profile_metadata_bound_is_enforced_before_freeze(
    monkeypatch: pytest.MonkeyPatch, bad: bytes, request: pytest.FixtureRequest
) -> None:
    """Actual excess or mutable metadata never starts a full-raster freeze."""
    assert len(request.node.nodeid) < 200
    sink = filled()
    freeze = Mock(side_effect=AssertionError("unexpected freeze"))
    monkeypatch.setattr(owned, "_freeze", freeze)
    with pytest.raises(ValueError, match=r".+"):
        sink.freeze(HEADER, bad)
    freeze.assert_not_called()
    assert sink.allocated_bytes == 0


def test_single_use_publication_and_late_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seal is not authorization; take transfers once and late cancel cannot revoke."""
    monkeypatch.setattr(owned.time, "monotonic", lambda: 1.0)
    event = threading.Event()
    vault = owned.FrameVault(7, event)
    vault.publish_stamp("starting", 10.0)
    vault.begin(IDENTITY)
    vault.publish_stamp("retiring", 10.0)
    vault.seal(candidate(), RECEIPT)
    vault.authorize(RECEIPT)
    vault.check_authorized(RECEIPT)
    frame = vault.take(7, 3, RECEIPT)
    assert frame.rgb == RGB
    event.set()
    vault.invalidate()
    assert vault.state == owned.FrameState.TAKEN
    with pytest.raises(ValueError, match=r".+"):
        vault.take(7, 3, RECEIPT)
    assert frame.rgb == RGB


@pytest.mark.parametrize(
    "failure", ["unapproved", "cancel", "generation", "receipt", "expiry"]
)
def test_publication_failures_are_permanent(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Terminal metadata, stale generation or expiry cannot release pixels."""
    now = [1.0]
    monkeypatch.setattr(owned.time, "monotonic", lambda: now[0])
    event = threading.Event()
    vault = owned.FrameVault(7, event)
    first = vault.publish_stamp("starting", 9.0)
    vault.begin(IDENTITY)
    current = vault.publish_stamp("retiring", 10.0)
    vault.seal(candidate(), RECEIPT)
    assert not vault.expire(first.revision, 100.0)
    if failure != "unapproved":
        vault.authorize(RECEIPT)
    if failure == "cancel":
        event.set()
    if failure == "expiry":
        assert vault.expire(current.revision, 10.0)
    with pytest.raises(ValueError, match=r".+"):
        vault.take(
            8 if failure == "generation" else 7,
            3,
            b"wrong" if failure == "receipt" else RECEIPT,
        )
    assert vault.state == owned.FrameState.INVALID
    event.clear()
    with pytest.raises(ValueError, match=r".+"):
        vault.take(7, 3, RECEIPT)


@pytest.mark.parametrize("pool", [owned.ParentPixelSlots, owned.PeerPixelSlots])
def test_every_named_slot_reservation_and_exception_disposal(
    pool: type[owned.ParentPixelSlots] | type[owned.PeerPixelSlots],
) -> None:
    """All listed slots are enforceable and independent; headroom is not allocatable."""
    slots = pool()
    limits = (
        owned.PARENT_PIXEL_SLOTS
        if pool is owned.ParentPixelSlots
        else owned.PEER_PIXEL_SLOTS
    )
    live = [slots.reserve(name) for name in limits]
    assert slots.reserved_bytes == sum(limits.values())
    assert slots.peak_bytes == sum(limits.values())
    for name in limits:
        with pytest.raises(ValueError, match="slot"):
            slots.reserve(name)
    with pytest.raises(ValueError, match="slot"):
        slots.reserve("unlisted_headroom", size=1)
    for lease in live:
        lease.release()
        lease.release()
    assert slots.reserved_bytes == 0
    for name in limits:
        with pytest.raises(RuntimeError, match="failed operation"), slots.reserve(name):
            raise RuntimeError("failed operation")
        assert slots.reserved_bytes == 0
    slots.invalidate()
    with pytest.raises(ValueError, match="slot"):
        slots.reserve(next(iter(limits)))


def test_lease_binding_move_and_stale_release_cannot_free_a_new_owner() -> None:
    """Reservations reject mutable lists, excess payload and duplicate transfers."""
    slots = owned.ParentPixelSlots()
    lease = slots.reserve("current_frozen", size=3)
    with pytest.raises(ValueError, match="payload"):
        lease.bind([b"x"])  # ty: ignore[invalid-argument-type] - rejection probe
    with pytest.raises(ValueError, match="exceeds"):
        lease.bind(b"long")
    lease.bind(b"rgb")
    with pytest.raises(ValueError, match="unavailable"):
        lease.bind(b"rgb")
    lease.move("source_1")
    assert lease.name == "source_1"
    assert lease.payload == b"rgb"
    with pytest.raises(ValueError, match="transfer"):
        lease.move("source_1")
    lease.release()
    newer = slots.reserve("source_1")
    lease.release()
    assert slots.reserved_bytes == N
    with pytest.raises(ValueError, match="transfer"):
        lease.move("source_2")
    newer.release()
    with slots.reserve("assembly_chunks") as chunks:
        chunks.bind((b"one", b"two"))
        assert chunks.payload == (b"one", b"two")
    assert slots.reserved_bytes == 0


@pytest.mark.parametrize(
    "chunk",
    [b"", bytearray(b"x"), b"x" * 65_537],
    ids=["empty", "mutable", "oversized"],
)
def test_bad_credit_chunk_invalidates_the_sink(
    chunk: bytes, request: pytest.FixtureRequest
) -> None:
    """Invalid payloads cannot grow storage, freeze or reenter the same pool."""
    assert len(request.node.nodeid) < 200
    sink = owned.FrameSink(IDENTITY, lambda: None)
    with pytest.raises(ValueError, match="chunk"):
        sink.feed(chunk)
    assert sink.allocated_bytes == 0
    with pytest.raises(ValueError, match="chunk"):
        sink.feed(b"x")
    with pytest.raises(ValueError, match="slot"):
        sink.slots.reserve("current_mutable")


@pytest.mark.parametrize(
    "case", ["partial", "header", "hash", "observation", "trailing"]
)
def test_incomplete_or_corrupt_frame_cannot_freeze(case: str) -> None:
    """Full byte count alone proves neither matching hashes nor correlated EOF."""
    sink = owned.FrameSink(IDENTITY, lambda: None) if case == "partial" else filled()
    header = replace(HEADER, image_sha256=b"x" * 32) if case == "hash" else HEADER
    if case == "header":
        header = replace(HEADER, identity=replace(IDENTITY, x=999))
    canonical = b"wrong" if case == "observation" else OBSERVATION
    action = (
        partial(sink.feed, b"x")
        if case == "trailing"
        else partial(sink.freeze, header, canonical)
    )
    with pytest.raises(ValueError, match=r".+"):
        action()
    assert sink.allocated_bytes == 0
    assert sink.slots.reserved_bytes == 0
