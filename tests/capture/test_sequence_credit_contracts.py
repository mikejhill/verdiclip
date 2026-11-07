"""Portable strict sequence credit2 capacity and cancellation ordering contracts."""

from dataclasses import FrozenInstanceError, replace
from threading import Event, Thread

import pytest

from verdiclip.capture.sequence_credit import (
    CHUNK_BYTES,
    MAX_FRAME_BYTES,
    CreditCancellationWitness,
    CreditError,
    CreditGate,
    CreditToken,
    ParentCredit,
    PeerCredit,
)

EPOCH = "ab" * 16


def token(index: int = 0, total: int = CHUNK_BYTES + 3) -> CreditToken:
    """Create the exact token for a test frame."""
    return CreditToken.for_chunk(EPOCH, 2, 1, total, index)


def test_token_round_trip_and_frozen() -> None:
    """Token round trip and frozen."""
    credit = token()
    assert CreditToken.from_payload(2, credit.payload()) == credit
    assert credit.payload() == {
        "credit_schema": 2,
        "epoch": EPOCH,
        "frame_id": 1,
        "chunk_index": 0,
        "offset": 0,
        "count": CHUNK_BYTES,
    }
    assert token(1).count == 3
    assert token(23, MAX_FRAME_BYTES).count == CHUNK_BYTES
    with pytest.raises(FrozenInstanceError):
        credit.count = 1  # ty: ignore[invalid-assignment]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("epoch", "0" * 32),
        ("epoch", "AB" * 16),
        ("epoch", 12),
        ("request_id", 0),
        ("request_id", 129),
        ("frame_id", 9),
        ("chunk_index", -1),
        ("chunk_index", 24),
        ("offset", 1),
        ("offset", MAX_FRAME_BYTES),
        ("count", 0),
        ("count", CHUNK_BYTES + 1),
        ("count", True),
        ("count", 1.0),
    ],
)
def test_token_rejects_invalid_fields(field: str, value: object) -> None:
    """Token rejects invalid fields."""
    with pytest.raises(CreditError):
        replace(token(), **{field: value})


@pytest.mark.parametrize("payload", [None, [], {}, {"extra": 1}])
def test_token_rejects_invalid_payload(payload: object) -> None:
    """Token rejects invalid payload."""
    with pytest.raises(CreditError):
        CreditToken.from_payload(2, payload)


@pytest.mark.parametrize(
    ("field", "value"), [("credit_schema", True), ("epoch", 1), ("count", 0)]
)
def test_token_payload_validation(field: str, value: object) -> None:
    """Token payload validation."""
    payload = token().payload()
    payload[field] = value
    with pytest.raises(CreditError):
        CreditToken.from_payload(2, payload)


@pytest.mark.parametrize(
    ("total", "index"), [(0, 0), (MAX_FRAME_BYTES + 1, 0), (1, 1), (True, 0)]
)
def test_derived_token_bounds(total: int, index: int) -> None:
    """Derived token bounds."""
    with pytest.raises(CreditError):
        CreditToken.for_chunk(EPOCH, 2, 1, total, index)


@pytest.mark.parametrize("split", range(1, 33))
def test_fragmentation_and_early_next_wait(split: int) -> None:
    """Fragmentation and early next wait."""
    total = CHUNK_BYTES + 3
    parent = ParentCredit(EPOCH, 2, 1, total)
    peer = PeerCredit(EPOCH, 2, 1, total)
    first = peer.announce_wait()
    assert not peer.complete
    assert parent.grantable is None
    parent.receive_wait(first)
    assert parent.grantable == first
    parent.reserve_grant(first)
    peer.consume_credit(first)
    peer.wrote_body(CHUNK_BYTES)
    second = peer.announce_wait()
    parent.receive_wait(second)
    assert parent.grantable is None
    parent.accept_body(split)
    assert parent.grantable is None
    parent.accept_body(CHUNK_BYTES - split)
    assert parent.grantable == second
    parent.reserve_grant(second)
    peer.consume_credit(second)
    peer.wrote_body(1)
    peer.wrote_body(2)
    parent.accept_body(3)
    parent.finish()
    assert peer.complete
    assert parent.snapshot().accepted_bytes == total
    assert parent.snapshot().granted_bytes == total


@pytest.mark.parametrize(
    "operation",
    [
        "before",
        "duplicate_wait",
        "wrong_wait",
        "wrong_grant",
        "early_next_grant",
        "excess",
        "zero",
        "bool",
        "duplicate_grant",
        "finish",
        "observe",
    ],
)
def test_parent_failure_is_irreversible(operation: str) -> None:
    """Parent failure is irreversible."""
    parent = ParentCredit(EPOCH, 2, 1, CHUNK_BYTES + 3)
    first, second = token(), token(1)

    def attempt() -> None:
        if operation == "before":
            parent.reserve_grant(first)
        elif operation == "observe":
            parent.observe_cancel()
        else:
            parent.receive_wait(first)
            if operation == "duplicate_wait":
                parent.receive_wait(first)
            elif operation == "wrong_wait":
                parent.receive_wait(replace(first, epoch="cd" * 16))
            elif operation == "wrong_grant":
                parent.reserve_grant(replace(first, request_id=3))
            elif operation == "finish":
                parent.finish()
            else:
                parent.reserve_grant(first)
                if operation == "early_next_grant":
                    parent.receive_wait(second)
                    parent.reserve_grant(second)
                elif operation == "duplicate_grant":
                    parent.reserve_grant(first)
                else:
                    parent.accept_body(
                        {"excess": CHUNK_BYTES + 1, "zero": 0, "bool": True}[operation]
                    )

    with pytest.raises(CreditError):
        attempt()
    assert parent.snapshot().failed
    assert parent.grantable is None
    with pytest.raises(CreditError):
        parent.receive_wait(first)


@pytest.mark.parametrize(
    "change",
    [
        {"epoch": "cd" * 16},
        {"request_id": 3},
        {"count": 1},
        {"chunk_index": 1, "offset": CHUNK_BYTES},
    ],
)
def test_wrong_correlation(change: dict[str, object]) -> None:
    """Wrong correlation."""
    parent = ParentCredit(EPOCH, 2, 1, CHUNK_BYTES + 3)
    with pytest.raises(CreditError):
        parent.receive_wait(replace(token(), **change))
    assert parent.snapshot().failed


def test_surplus_wait_and_gate_reuse() -> None:
    """Surplus wait and gate reuse."""
    gate = CreditGate(Event())
    parent = ParentCredit(EPOCH, 2, 1, 1, gate)
    first = token(total=1)
    parent.receive_wait(first)
    parent.reserve_grant(first)
    parent.accept_body(1)
    with pytest.raises(CreditError):
        parent.receive_wait(first)
    with pytest.raises(CreditError):
        ParentCredit(EPOCH, 2, 1, 1, gate)


@pytest.mark.parametrize("interior", [False, True])
def test_held_cancellation_witness(*, interior: bool) -> None:
    """Held cancellation witness."""
    gate = CreditGate(Event())
    parent = ParentCredit(EPOCH, 2, 1, CHUNK_BYTES + 3, gate)
    first, second = token(), token(1)
    parent.receive_wait(first)
    if interior:
        parent.reserve_grant(first)
        parent.accept_body(CHUNK_BYTES)
        parent.receive_wait(second)
    held = second if interior else first
    gate.cancel()
    latched = parent.cancel()
    assert parent.cancel() == latched
    observed = parent.observe_cancel()
    assert parent.observe_cancel() == observed
    witness = parent.witness()
    witness.validate_held(held)
    assert witness.latched.pending == held
    assert parent.grantable is None
    with pytest.raises(CreditError):
        parent.reserve_grant(held)
    parent.witness().validate_held(held)
    for field, value in [
        ("pending", None),
        ("accepted_bytes", 1),
        ("granted_bytes", 1),
        ("last_grant_sequence", 999),
        ("cancel_sequence", 0),
        ("observed_sequence", 0),
        ("failed", False),
        ("events", ()),
    ]:
        with pytest.raises(CreditError):
            CreditCancellationWitness(
                latched, replace(witness.final, **{field: value})
            ).validate_held(held)


@pytest.mark.parametrize("grant_first", [False, True])
def test_forced_cancel_reservation_order(*, grant_first: bool) -> None:
    """Forced cancel reservation order."""
    gate = CreditGate(Event())
    parent = ParentCredit(EPOCH, 2, 1, 1, gate)
    held = token(total=1)
    parent.receive_wait(held)
    first_done = Event()
    errors: list[CreditError] = []

    def grant() -> None:
        if not grant_first:
            assert first_done.wait(2)
        try:
            parent.reserve_grant(held)
        except CreditError as error:
            errors.append(error)
        finally:
            first_done.set()

    def cancel() -> None:
        if grant_first:
            assert first_done.wait(2)
        gate.cancel()
        first_done.set()

    workers = [Thread(target=grant), Thread(target=cancel)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    parent.observe_cancel()
    if grant_first:
        assert not errors
        assert parent.snapshot().granted_bytes == 1
        with pytest.raises(CreditError):
            parent.witness().validate_held(held)
    else:
        assert len(errors) == 1
        parent.witness().validate_held(held)


def test_cancel_before_initialization_and_incomplete_witness() -> None:
    """Cancel before initialization and incomplete witness."""
    gate = CreditGate(Event())
    gate.cancel()
    parent = ParentCredit(EPOCH, 2, 1, 1, gate)
    assert parent.snapshot().failed
    with pytest.raises(CreditError):
        parent.witness()
    parent.observe_cancel()
    with pytest.raises(CreditError):
        parent.witness().validate_held(token(total=1))
    fresh = ParentCredit(EPOCH, 2, 1, 1)
    with pytest.raises(CreditError):
        fresh.witness()


@pytest.mark.parametrize("operation", ["before", "duplicate", "early_wait", "surplus"])
def test_peer_failure(operation: str) -> None:
    """Peer failure."""
    peer = PeerCredit(EPOCH, 2, 1, 1)
    held = token(total=1)

    def attempt() -> None:
        if operation == "before":
            peer.consume_credit(held)
        else:
            assert peer.announce_wait() == held
            peer.consume_credit(held)
            if operation == "duplicate":
                peer.consume_credit(held)
            elif operation == "early_wait":
                peer.announce_wait()
            else:
                peer.wrote_body(1)
                peer.announce_wait()

    with pytest.raises(CreditError):
        attempt()
    assert not peer.complete


def test_every_partial_chunk_split() -> None:
    """Every proper split preserves the allowance until the final accepted byte."""
    first, second = token(), token(1)
    for split in range(1, CHUNK_BYTES):
        parent = ParentCredit(EPOCH, 2, 1, CHUNK_BYTES + 3)
        parent.receive_wait(first)
        parent.reserve_grant(first)
        parent.receive_wait(second)
        parent.accept_body(split)
        assert parent.grantable is None
        parent.accept_body(CHUNK_BYTES - split)
        assert parent.grantable == second


@pytest.mark.parametrize(
    "mutation",
    [
        "sequence",
        "time",
        "token",
        "wait_order",
        "grant_order",
        "kind",
        "terminal_token",
        "terminal_sequence",
    ],
)
def test_reject_forged_event_history(mutation: str) -> None:
    """Witness validation rejects altered worker event ordering and identities."""
    parent = ParentCredit(EPOCH, 2, 1, CHUNK_BYTES + 3)
    first, held = token(), token(1)
    parent.receive_wait(first)
    parent.reserve_grant(first)
    parent.accept_body(CHUNK_BYTES)
    parent.receive_wait(held)
    parent.cancel()
    parent.observe_cancel()
    witness = parent.witness()
    events = list(witness.final.events)
    if mutation == "sequence":
        events[0] = replace(events[0], sequence=2)
    elif mutation == "time":
        events[1] = replace(events[1], time_ns=events[0].time_ns - 1)
    elif mutation == "token":
        events[0] = replace(events[0], token=held)
    elif mutation == "wait_order":
        events[1] = replace(events[1], kind="wait", token=held)
    elif mutation == "grant_order":
        events[0] = replace(events[0], kind="grant")
    elif mutation == "kind":
        events[-1] = replace(events[-1], kind="alien")
    elif mutation == "terminal_token":
        events[-1] = replace(events[-1], token=held)
    else:
        events[-1] = replace(events[-1], time_ns=events[-1].time_ns + 1)
    latched = replace(witness.latched, events=tuple(events[:-1]))
    final = replace(witness.final, events=tuple(events))
    with pytest.raises(CreditError):
        CreditCancellationWitness(latched, final).validate_held(held)


def test_duplicate_finish_is_terminal_failure() -> None:
    """Completion cannot be replayed to revive a completed byte transaction."""
    parent = ParentCredit(EPOCH, 2, 1, 1)
    first = token(total=1)
    parent.receive_wait(first)
    parent.reserve_grant(first)
    parent.accept_body(1)
    parent.finish()
    with pytest.raises(CreditError):
        parent.finish()
    assert parent.snapshot().failed
