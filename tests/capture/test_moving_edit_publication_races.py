"""Requalify profile-local publication locks, deadlines and cancellation races."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from functools import partial
from unittest.mock import Mock

import pytest
from tests.capture.test_moving_edit_pixel_ownership import (
    IDENTITY,
    RECEIPT,
    RGB,
    candidate,
)

from verdiclip.capture import moving_edit_frame as retained

MAX_MESSAGE_BYTES = retained.MAX_RECEIPT_BYTES


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Keep every admission on one unchanged absolute timeline."""
    value = [1.0]
    monkeypatch.setattr(retained.time, "monotonic", lambda: value[0])
    return value


@pytest.fixture
def vault(clock: list[float]) -> retained.FrameVault:
    """Start with authoritative worker startup evidence, not a fabricated clock."""
    assert clock == [1.0]
    result = retained.FrameVault(7, threading.Event())
    result.publish_stamp("starting", 5.0)
    return result


def sealed(vault: retained.FrameVault) -> retained.VerifiedCandidate:
    """Seal independently owned moving-profile pixels under retirement gates."""
    result = candidate()
    vault.begin(IDENTITY)
    vault.publish_stamp("retiring", 10.0)
    vault.seal(result, RECEIPT)
    return result


def test_constructor_creates_no_clock_or_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-started worker has no invented startup or missing-receipt budget."""
    clock = Mock(side_effect=AssertionError("no constructor clock"))
    monkeypatch.setattr(retained.time, "monotonic", clock)
    vault = retained.FrameVault(1, threading.Event())
    assert vault.stamp() is None
    assert vault.state == retained.FrameState.EMPTY
    assert not vault.expire(1, 100.0)
    clock.assert_not_called()


@pytest.mark.parametrize("generation", [0, -1, True, 1.0])
def test_invalid_generation_is_not_an_alias(generation: int) -> None:
    """The generation key is an exact positive integer, not a numeric lookalike."""
    with pytest.raises(retained.RetainedFrameError, match="generation"):
        retained.FrameVault(generation, threading.Event())


def test_stamp_revision_changes_only_with_authoritative_phase_or_deadline(
    vault: retained.FrameVault,
) -> None:
    """Identical publications do not obsolete timers or renew the deadline."""
    first = vault.stamp()
    assert first is not None
    assert first == retained.DeadlineStamp("starting", 1, 5.0)
    assert vault.publish_stamp("starting", 5) is first
    second = vault.publish_stamp("observing", 3.0)
    assert second.revision == 2
    third = vault.publish_stamp("retiring", 10.0)
    assert third.revision == 3
    assert not vault.expire(second.revision, 3.0)
    assert vault.stamp() is third
    with pytest.raises(FrozenInstanceError):
        third.deadline = 100.0  # ty: ignore[invalid-assignment] - frozen contract


@pytest.mark.parametrize("deadline", [0.0, -1.0, float("nan"), float("inf"), True])
def test_stamp_rejects_invalid_original_deadline(
    vault: retained.FrameVault, deadline: float
) -> None:
    """Invalid timestamps never become renewed publication allowances."""
    before = vault.stamp()
    with pytest.raises(retained.RetainedFrameError, match="stamp"):
        vault.publish_stamp("observing", deadline)
    assert vault.stamp() is before


@pytest.mark.parametrize("phase", ["", "x" * 33, None])
def test_stamp_rejects_unbounded_phase(vault: retained.FrameVault, phase: str) -> None:
    """Cross-thread phase metadata remains bounded and immutable."""
    with pytest.raises(retained.RetainedFrameError, match="stamp"):
        vault.publish_stamp(phase, 10.0)


@pytest.mark.parametrize("state", ["EMPTY", "BUILDING", "SEALED", "INVALID", "TAKEN"])
def test_expiry_uses_current_revision_without_revoking_taken_bytes(
    vault: retained.FrameVault, state: str
) -> None:
    """Expiry remains observable to lifetime cleanup even for an invalid/taken vault."""
    frame = None
    if state in ("SEALED", "TAKEN"):
        sealed(vault)
        if state == "TAKEN":
            vault.authorize(RECEIPT)
            frame = vault.take(7, 3, RECEIPT)
    elif state == "BUILDING":
        vault.begin(IDENTITY)
    elif state == "INVALID":
        vault.invalidate()
    stamp = vault.stamp()
    assert stamp is not None
    assert not vault.expire(stamp.revision, stamp.deadline - 0.001)
    assert not vault.expire(stamp.revision + 1, stamp.deadline)
    assert vault.expire(stamp.revision, stamp.deadline)
    assert vault.expire(stamp.revision, stamp.deadline + 1)
    assert vault.state == ("TAKEN" if frame is not None else "INVALID")
    if frame is not None:
        assert frame.rgb == RGB


@pytest.mark.parametrize("point", ["begin", "seal", "authorize", "take"])
@pytest.mark.parametrize("failure", ["cancel", "deadline", "regressed", "nonfinite"])
def test_every_vault_admission_rechecks_current_authority(
    vault: retained.FrameVault, clock: list[float], point: str, failure: str
) -> None:
    """Queued delivery never supersedes cancellation or the original clock gates."""
    private = candidate()
    if point != "begin":
        vault.begin(IDENTITY)
        vault.publish_stamp("retiring", 10.0)
    if point in ("authorize", "take"):
        vault.seal(private, RECEIPT)
    if point == "take":
        vault.authorize(RECEIPT)
    if failure == "cancel":
        vault._cancelled.set()
    elif failure == "deadline":
        clock[0] = 5.0 if point == "begin" else 10.0
    elif failure == "nonfinite":
        clock[0] = float("nan")
    elif point == "begin":
        vault.begin(IDENTITY)
        clock[0] = 0.5
    else:
        clock[0] = 0.5
    action = {
        "begin": partial(vault.begin, IDENTITY),
        "seal": partial(vault.seal, private, RECEIPT),
        "authorize": partial(vault.authorize, RECEIPT),
        "take": partial(vault.take, 7, 3, RECEIPT),
    }[point]
    with pytest.raises(retained.RetainedFrameError, match=r".+"):
        action()
    assert vault.state == "INVALID"
    assert vault._frame is None


@pytest.mark.parametrize("operation", ["seal", "authorize", "take"])
@pytest.mark.parametrize(
    "receipt",
    [b"", b"different", bytearray(RECEIPT), b"x" * (MAX_MESSAGE_BYTES + 1)],
    ids=["empty", "different", "mutable", "oversized"],
)
def test_receipt_binding_is_exact_and_bounded(
    vault: retained.FrameVault, operation: str, receipt: bytes
) -> None:
    """Metadata mismatches invalidate rather than borrowing another terminal."""
    private = sealed(vault)
    if operation == "take":
        vault.authorize(RECEIPT)
    action = {
        "seal": partial(vault.seal, private, receipt),
        "authorize": partial(vault.authorize, receipt),
        "take": partial(vault.take, 7, 3, receipt),
    }[operation]
    with pytest.raises(retained.RetainedFrameError, match=r".+"):
        action()
    assert vault.state == "INVALID"


@pytest.mark.parametrize(
    ("generation", "request_id"), [(8, 3), (7, 1), (7.0, 3), (7, 3.0), (True, 3)]
)
def test_take_uses_generation_and_protocol_request_not_caller_id(
    vault: retained.FrameVault, generation: int, request_id: int
) -> None:
    """An unrelated caller request or numerically equal noninteger cannot publish."""
    sealed(vault)
    vault.authorize(RECEIPT)
    with pytest.raises(retained.RetainedFrameError, match="publication"):
        vault.take(generation, request_id, RECEIPT)
    assert vault.state == "INVALID"


def test_no_stamp_no_authorization_and_duplicate_transitions_fail_closed(
    vault: retained.FrameVault,
) -> None:
    """Only the full reserve/seal/authorize/take sequence can publish once."""
    unstamped = retained.FrameVault(1, threading.Event())
    with pytest.raises(retained.RetainedFrameError, match="unavailable"):
        unstamped.begin(IDENTITY)
    sealed(vault)
    with pytest.raises(retained.RetainedFrameError, match="publication"):
        vault.take(7, 3, RECEIPT)
    assert vault.state == "INVALID"


@pytest.mark.parametrize("operation", ["begin", "seal", "authorize"])
def test_duplicate_transition_invalidates_unclaimed_payload(
    vault: retained.FrameVault, operation: str
) -> None:
    """Duplicates are not fresh authorization and cannot renew the original result."""
    private = sealed(vault)
    vault.authorize(RECEIPT)
    action = {
        "begin": partial(vault.begin, IDENTITY),
        "seal": partial(vault.seal, private, RECEIPT),
        "authorize": partial(vault.authorize, RECEIPT),
    }[operation]
    with pytest.raises(retained.RetainedFrameError, match=r".+"):
        action()
    assert vault.state == "INVALID"


def test_metadata_authorization_and_take_do_not_hash_or_copy(
    monkeypatch: pytest.MonkeyPatch, vault: retained.FrameVault
) -> None:
    """Only the worker hashes; the GUI-side operations move the original bytes."""
    private = candidate()
    vault.begin(IDENTITY)
    vault.publish_stamp("retiring", 10.0)
    original_hash = retained.hashlib.sha256

    def hashing(data: bytes = b"") -> object:
        assert not vault._lock.locked()
        return original_hash(data)

    monkeypatch.setattr(retained.hashlib, "sha256", hashing)
    vault.seal(private, RECEIPT)
    forbidden = Mock(side_effect=AssertionError("GUI hash or copy"))
    monkeypatch.setattr(retained.hashlib, "sha256", forbidden)
    monkeypatch.setattr(retained, "_copy", forbidden)
    monkeypatch.setattr(retained, "_freeze", forbidden)
    vault.authorize(RECEIPT)
    assert vault.take(7, 3, RECEIPT).rgb is private.rgb
    forbidden.assert_not_called()


def test_invalidation_does_not_set_cancel_or_revoke_taken_result(
    vault: retained.FrameVault,
) -> None:
    """Disposal is not a new cancellation reason, and consumer ownership is final."""
    sealed(vault)
    vault.authorize(RECEIPT)
    result = vault.take(7, 3, RECEIPT)
    vault.invalidate()
    assert not vault._cancelled.is_set()
    vault._cancelled.set()
    vault.invalidate()
    assert vault.state == "TAKEN"
    assert result.rgb == RGB


@pytest.mark.parametrize("winner", ["cancel", "take"])
def test_cancel_take_race_has_one_locked_ownership_winner(
    vault: retained.FrameVault, winner: str
) -> None:
    """Deterministic thread ordering exercises both sides of the publication point."""
    sealed(vault)
    vault.authorize(RECEIPT)
    first = threading.Event()
    results: list[retained.VerifiedFrame] = []
    failures: list[Exception] = []

    def cancel() -> None:
        if winner != "cancel":
            assert first.wait(2.0)
        vault.invalidate()
        vault._cancelled.set()
        first.set()

    def take() -> None:
        if winner != "take":
            assert first.wait(2.0)
        try:
            results.append(vault.take(7, 3, RECEIPT))
        except retained.RetainedFrameError as error:
            failures.append(error)
        first.set()

    threads = [threading.Thread(target=cancel), threading.Thread(target=take)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)
        assert not thread.is_alive()
    if winner == "take":
        assert [result.rgb for result in results] == [RGB]
        assert not failures
        assert vault.state == "TAKEN"
    else:
        assert not results
        assert len(failures) == 1
        assert vault.state == "INVALID"


@pytest.mark.parametrize("operation", ["invalidate", "expire", "authorize", "take"])
def test_discarded_frame_destructor_runs_only_after_unlock(
    monkeypatch: pytest.MonkeyPatch, vault: retained.FrameVault, operation: str
) -> None:
    """Both explicit disposal and failed admissions release frame references outside."""
    observed: list[bool] = []

    class ObservedFrame(retained.VerifiedFrame):
        __slots__ = ()

        def __del__(self) -> None:
            observed.append(vault._lock.locked())

    monkeypatch.setattr(retained, "VerifiedFrame", ObservedFrame)
    sealed(vault)
    if operation == "invalidate":
        vault.invalidate()
    elif operation == "expire":
        stamp = vault.stamp()
        assert stamp is not None
        assert vault.expire(stamp.revision, stamp.deadline)
    else:
        if operation == "take":
            vault.authorize(RECEIPT)
        action = (
            partial(vault.authorize, b"wrong")
            if operation == "authorize"
            else partial(vault.take, 8, 3, RECEIPT)
        )
        with pytest.raises(retained.RetainedFrameError, match="mismatched"):
            action()
    assert observed == [False]
    assert vault.state == "INVALID"
    assert not vault._cancelled.is_set()


def test_expiry_immediately_after_seal_invalidates_before_return(
    monkeypatch: pytest.MonkeyPatch, vault: retained.FrameVault
) -> None:
    """The second synchronous admission catches a seal that crosses its deadline."""
    private = candidate()
    vault.begin(IDENTITY)
    vault.publish_stamp("retiring", 10.0)
    monkeypatch.setattr(retained.time, "monotonic", Mock(side_effect=[1.0, 10.0]))
    with pytest.raises(retained.RetainedFrameError, match="expired"):
        vault.seal(private, RECEIPT)
    assert vault.state == "INVALID"
    assert vault._frame is None


@pytest.mark.parametrize("failure", ["cancel", "deadline", "hash-error"])
def test_receipt_hash_cannot_hide_changed_admission(
    monkeypatch: pytest.MonkeyPatch,
    vault: retained.FrameVault,
    clock: list[float],
    failure: str,
) -> None:
    """Receipt hashing stays outside the lock and precedes fresh retirement gates."""
    private = candidate()
    vault.begin(IDENTITY)
    vault.publish_stamp("retiring", 10.0)
    original = retained.hashlib.sha256

    def hashing(data: bytes) -> object:
        assert not vault._lock.locked()
        if failure == "cancel":
            vault._cancelled.set()
        elif failure == "deadline":
            clock[0] = 10.0
        else:
            raise MemoryError("receipt hashing")
        return original(data)

    monkeypatch.setattr(retained.hashlib, "sha256", hashing)
    with pytest.raises((retained.RetainedFrameError, MemoryError), match=r".+"):
        vault.seal(private, RECEIPT)
    assert vault.state == "INVALID"
    assert vault._frame is None


def test_vault_rejects_missing_native_free_candidate(
    vault: retained.FrameVault,
) -> None:
    """Unverified or absent candidate metadata cannot be sealed."""
    with pytest.raises(retained.RetainedFrameError, match="candidate"):
        vault.seal(None, RECEIPT)  # ty: ignore[invalid-argument-type] - rejection probe
    assert vault.state == "INVALID"


def test_only_shared_event_is_accepted() -> None:
    """The locked cancellation read must not execute arbitrary user callbacks."""
    with pytest.raises(retained.RetainedFrameError, match="event"):
        retained.FrameVault(1, Mock())


@pytest.mark.parametrize(
    ("revision", "now"), [(True, 1.0), (1.0, 1.0), (1, float("nan")), (1, True)]
)
def test_expiry_rejects_invalid_clock_evidence(
    vault: retained.FrameVault, revision: int, now: float
) -> None:
    """Nonfinite and numeric aliases cannot operate the expiry transition."""
    with pytest.raises(retained.RetainedFrameError, match="evidence"):
        vault.expire(revision, now)
    assert vault.state == "EMPTY"


def test_facade_delivery_check_is_repeatable_metadata_only_and_does_not_take(
    monkeypatch: pytest.MonkeyPatch, vault: retained.FrameVault, clock: list[float]
) -> None:
    """Queued delivery rechecks existing authority without hashing or moving pixels."""
    private = sealed(vault)
    vault.authorize(RECEIPT)
    stamp = vault.stamp()
    forbidden = Mock(side_effect=AssertionError("delivery must not hash or copy"))
    monkeypatch.setattr(retained.hashlib, "sha256", forbidden)
    monkeypatch.setattr(retained, "_copy", forbidden)
    monkeypatch.setattr(retained, "_freeze", forbidden)
    for now in (2.0, 3.0, 9.999):
        clock[0] = now
        assert vault.check_authorized(RECEIPT) is None
        assert vault.state == "SEALED"
        assert vault.stamp() is stamp
    frame = vault.take(7, 3, RECEIPT)
    assert frame.rgb is private.rgb
    assert vault.state == "TAKEN"
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["deadline", "invalidated", "cancelled", "unauthorized", "wrong-receipt", "empty"],
)
def test_facade_delivery_cannot_borrow_earlier_authorization(
    vault: retained.FrameVault, clock: list[float], failure: str
) -> None:
    """Expiry, cancellation, or invalidation between lifetime and facade wins."""
    sealed(vault)
    if failure != "unauthorized":
        vault.authorize(RECEIPT)
    receipt = RECEIPT
    if failure == "deadline":
        clock[0] = 10.0
    elif failure == "invalidated":
        vault.invalidate()
    elif failure == "cancelled":
        vault._cancelled.set()
    elif failure == "wrong-receipt":
        receipt = b"another terminal"
    elif failure == "empty":
        receipt = b""
    with pytest.raises(retained.RetainedFrameError, match=r".+"):
        vault.check_authorized(receipt)
    assert vault.state == "INVALID"
    assert vault._frame is None
    with pytest.raises(retained.RetainedFrameError, match="unavailable"):
        vault.take(7, 3, RECEIPT)


def test_facade_delivery_check_after_take_cannot_revoke_consumer(
    vault: retained.FrameVault,
) -> None:
    """A late repeated delivery has no new authority over already transferred bytes."""
    sealed(vault)
    vault.authorize(RECEIPT)
    frame = vault.take(7, 3, RECEIPT)
    with pytest.raises(retained.RetainedFrameError, match="unavailable"):
        vault.check_authorized(RECEIPT)
    assert vault.state == "TAKEN"
    assert frame.rgb == RGB
