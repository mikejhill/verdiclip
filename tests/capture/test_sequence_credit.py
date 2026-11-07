"""Independent credit2 profile rejects cross-frame tokens."""

from threading import Event

import pytest

from verdiclip.capture.sequence_credit import (
    CreditError,
    CreditGate,
    CreditToken,
    ParentCredit,
    PeerCredit,
)

EPOCH = "a" * 32


@pytest.mark.parametrize("frame_id", range(1, 9))
def test_real_producer_consumer_credit(frame_id: int) -> None:
    """Test real producer consumer credit."""
    producer = PeerCredit(EPOCH, frame_id + 1, frame_id, 65539)
    consumer = ParentCredit(EPOCH, frame_id + 1, frame_id, 65539)
    for count in (65536, 3):
        token = producer.announce_wait()
        parsed = CreditToken.from_payload(token.request_id, token.payload())
        consumer.receive_wait(parsed)
        consumer.reserve_grant(parsed)
        producer.consume_credit(parsed)
        producer.wrote_body(count)
        consumer.accept_body(count)
    consumer.finish()
    assert producer.complete
    assert consumer.snapshot().accepted_bytes == 65539


def test_stale_frame_and_cancel_fail_closed() -> None:
    """Test stale frame and cancel fail closed."""
    gate = CreditGate(Event())
    consumer = ParentCredit(EPOCH, 3, 2, 3, gate)
    stale = CreditToken.for_chunk(EPOCH, 3, 1, 3, 0)
    with pytest.raises(CreditError):
        consumer.receive_wait(stale)
    with pytest.raises(CreditError):
        consumer.receive_wait(CreditToken.for_chunk(EPOCH, 3, 2, 3, 0))


def test_cancel_held_frame_two() -> None:
    """Test cancel held frame two."""
    consumer = ParentCredit(EPOCH, 3, 2, 3)
    token = CreditToken.for_chunk(EPOCH, 3, 2, 3, 0)
    consumer.receive_wait(token)
    consumer.cancel()
    consumer.observe_cancel()
    consumer.witness().validate_held(token)
    with pytest.raises(CreditError):
        consumer.reserve_grant(token)


@pytest.mark.parametrize("frame_id", [True, 0, 9, 1.0])
def test_frame_bound(frame_id: int) -> None:
    """Test frame bound."""
    with pytest.raises(CreditError):
        CreditToken.for_chunk(EPOCH, 2, frame_id, 3, 0)


def test_shared_gate_requires_finished_previous_frame() -> None:
    """One shared cancel lock permits only consecutive completed credit owners."""
    gate = CreditGate(Event())
    first = ParentCredit(EPOCH, 2, 1, 3, gate)
    with pytest.raises(CreditError):
        ParentCredit(EPOCH, 3, 2, 3, gate)
    token = CreditToken.for_chunk(EPOCH, 2, 1, 3, 0)
    first.receive_wait(token)
    first.reserve_grant(token)
    first.accept_body(3)
    first.finish()
    with pytest.raises(CreditError):
        ParentCredit(EPOCH, 4, 3, 3, gate)
    second = ParentCredit(EPOCH, 3, 2, 3, gate)
    token = CreditToken.for_chunk(EPOCH, 3, 2, 3, 0)
    second.receive_wait(token)
    gate.cancel()
    with pytest.raises(CreditError):
        second.reserve_grant(token)
    assert second.snapshot().granted_bytes == 0
