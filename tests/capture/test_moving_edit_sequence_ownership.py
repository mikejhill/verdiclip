"""Actual moving proof to retained bytes, assembly and final single-use publication."""

from __future__ import annotations

import gc
import hashlib
import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial

import pytest
from tests.capture.moving_wire_fixtures import WIRE_EPOCH, make_proof
from tests.capture.test_moving_edit_retained_assembly import LINES, SCENE

from verdiclip.capture import moving_edit_frame as owned
from verdiclip.capture import moving_edit_retention as retention
from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.platform.moving_edit_protocol import MovingFrameProof

RECEIPT = b'{"passed":true,"retirement":"natural"}'


def put_frame(
    collector: retention.SequenceCollector,
    frame_id: int,
    *,
    commit: bool = True,
    changed: bool = False,
) -> tuple[owned.VerifiedCandidate, MovingFrameProof]:
    """Run an actual producer proof through sink, reservation and accept commit."""
    start = LINES[frame_id - 1] * 16 * 1479
    rgb = SCENE[start : start + 610827]
    if changed:
        rgb = b"!" + rgb[1:]
    proof = make_proof(frame_id, hashlib.sha256(rgb).hexdigest())
    observation = proof.observation
    identity = observation.frame_identity()
    collector.reserve(identity)
    sink = owned.FrameSink(identity, lambda: None, slots=collector.slots)
    for offset in range(0, len(rgb), 65536):
        sink.feed(rgb[offset : offset + 65536])
    candidate = sink.freeze(
        FrameHeader(identity, hashlib.sha256(rgb).digest()), observation.canonical
    )
    collector.accept(candidate, proof)
    if commit:
        collector.commit(proof.digest)
    return candidate, proof


def complete() -> tuple[retention.SequenceCollector, retention.SequenceCandidate]:
    """Retain all seven accepted frames and independently assemble only2/3/4."""
    collector = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
    for frame_id in range(1, 8):
        put_frame(collector, frame_id)
    return collector, collector.finish()


def test_actual_wire_collector_final_capsule_and_alias_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All70credits and seven fullproofs precede final authority and one take."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    collector, candidate = complete()
    assert collector.count == 7
    assert collector.retained_bytes == 0
    assert candidate.assembly.rgb == SCENE
    chain = bytes(32)
    for index in range(7):
        chain = hashlib.sha256(chain + candidate.frames[index].proof.digest).digest()
    assert chain == candidate.chain == collector.chain
    vault = retention.SequenceVault(1, threading.Event())
    vault.publish_stamp("starting", 30)
    vault.begin(WIRE_EPOCH)
    vault.publish_stamp("retiring", 30)
    vault.seal(candidate, RECEIPT)
    assert vault.state == owned.FrameState.SEALED
    vault.authorize(RECEIPT)
    vault.check_authorized(RECEIPT)
    capsule = vault.take(1, RECEIPT)
    assert capsule.assembly is candidate.assembly
    assert capsule.frames[0].rgb is candidate.frames[0].candidate.rgb
    assert len(capsule.proofs) == 7
    assert capsule.chain == chain
    del candidate
    gc.collect()
    assert collector.slots.reserved_bytes == 7 * 610827 + 918459
    vault.invalidate()
    assert capsule.assembly.rgb == SCENE
    with pytest.raises(ValueError, match="unavailable"):
        vault.take(1, RECEIPT)
    del capsule
    gc.collect()
    assert collector.slots.reserved_bytes == 0


@pytest.mark.parametrize("accepted", range(7))
def test_no_partial_frame_count_can_finish(accepted: int) -> None:
    """A successful frame or terminal cannot turn a partial collector into a result."""
    collector = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
    for frame_id in range(1, accepted + 1):
        put_frame(collector, frame_id)
    with pytest.raises(ValueError, match="committed"):
        collector.finish()
    assert collector.retained_bytes == 0
    assert collector.slots.reserved_bytes == 0
    with pytest.raises(ValueError, match="unavailable"):
        collector.finish()


@pytest.mark.parametrize("operation", ["reserve", "commit", "finish"])
def test_uncommitted_frame_cannot_advance(operation: str) -> None:
    """Accepted private bytes are not counted until their exact accept write returns."""
    collector = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
    candidate, _ = put_frame(collector, 1, commit=False)
    action: Callable[[], object] = {
        "reserve": partial(
            collector.reserve, make_proof(2).observation.frame_identity()
        ),
        "commit": partial(collector.commit, b"wrong"),
        "finish": collector.finish,
    }[operation]
    with pytest.raises(ValueError, match=r".+"):
        action()
    assert collector.count == 0
    assert collector.retained_bytes == 0
    # The caller's candidate alias still owns its payload, even after collector discard.
    assert collector.slots.reserved_bytes == 610827
    del candidate
    gc.collect()
    assert collector.slots.reserved_bytes == 0


@pytest.mark.parametrize("frame_id", [5, 6, 7])
def test_repeated_returned_restored_pixels_cannot_change(frame_id: int) -> None:
    """The actual proof can validate yet full repeated pixels must still match."""
    collector = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
    for previous in range(1, frame_id):
        put_frame(collector, previous)
    with pytest.raises(ValueError, match="frame bytes changed"):
        put_frame(collector, frame_id, changed=True)
    assert collector.retained_bytes == 0
    with pytest.raises(ValueError, match="unavailable"):
        collector.finish()


@pytest.mark.parametrize(
    "failure", ["cancel", "generation", "receipt", "not_finished", "expiry"]
)
def test_sequence_publication_has_no_terminal_only_bypass(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A matching receipt still requires explicit lifecycle-finished authorization."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    _, candidate = complete()
    event = threading.Event()
    vault = retention.SequenceVault(4, event)
    vault.publish_stamp("retiring", 30)
    vault.begin(WIRE_EPOCH)
    vault.seal(candidate, RECEIPT)
    if failure != "not_finished":
        vault.authorize(RECEIPT)
    if failure == "cancel":
        event.set()
    if failure == "expiry":
        stamp = vault.stamp()
        assert stamp is not None
        assert vault.expire(stamp.revision, stamp.deadline)
    with pytest.raises(ValueError, match=r".+"):
        vault.take(
            5 if failure == "generation" else 4,
            b"other" if failure == "receipt" else RECEIPT,
        )
    assert vault.state == owned.FrameState.INVALID


def test_collector_wrong_owner_and_reservation_identity_fail_closed() -> None:
    """No foreign thread can become a second collector or source-allocation owner."""
    collector = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
    failures = []

    def reserve() -> None:
        try:
            collector.reserve(make_proof().observation.frame_identity())
        except ValueError as error:
            failures.append(str(error))

    thread = threading.Thread(target=reserve)
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert len(failures) == 1
    with pytest.raises(ValueError, match="unavailable"):
        collector.reserve(make_proof().observation.frame_identity())
    for field, value in (("request_id", 2), ("session_id", b"z" * 16), ("width", 492)):
        other = retention.SequenceCollector(WIRE_EPOCH, lambda: None)
        identity = replace(make_proof().observation.frame_identity(), **{field: value})
        with pytest.raises(ValueError, match="reservation"):
            other.reserve(identity)
