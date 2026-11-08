"""Independent moving sequence terminal-versus-finished publication gates."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Any, cast

import pytest
from tests.capture.moving_wire_fixtures import WIRE_EPOCH as EPOCH
from tests.capture.test_moving_edit_sequence_ownership import RECEIPT, complete

from verdiclip.capture import moving_edit_retention as retention
from verdiclip.capture.moving_edit_frame import FrameState


@pytest.fixture(scope="module")
def sequence() -> retention.SequenceCandidate:
    """Hold one immutable actual proof chain solely for metadata gate probes."""
    return complete()[1]


def prepared_vault(
    monkeypatch: pytest.MonkeyPatch, sequence: retention.SequenceCandidate
) -> tuple[retention.SequenceVault, threading.Event]:
    """Seal a real complete result, without granting lifecycle-finished authority."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    cancel = threading.Event()
    vault = retention.SequenceVault(1, cancel)
    vault.publish_stamp("ready", 5)
    vault.begin(EPOCH)
    vault.publish_stamp("retiring", 6)
    vault.seal(sequence, RECEIPT)
    return vault, cancel


@pytest.mark.parametrize("boundary", ["authorize", "delivery", "take"])
@pytest.mark.parametrize(
    "failure", ["cancel", "expired", "clock-backwards", "deleted", "receipt"]
)
def test_vault_rechecks_every_publication_boundary(
    monkeypatch: pytest.MonkeyPatch,
    sequence: retention.SequenceCandidate,
    boundary: str,
    failure: str,
) -> None:
    """Vault rechecks every publication boundary."""
    vault, cancel = prepared_vault(monkeypatch, sequence)
    if boundary != "authorize":
        vault.authorize(RECEIPT)
    if failure == "cancel":
        cancel.set()
    elif failure == "expired":
        monkeypatch.setattr(retention.time, "monotonic", lambda: 6.0)
    elif failure == "clock-backwards":
        monkeypatch.setattr(retention.time, "monotonic", lambda: 0.5)
    elif failure == "deleted":
        vault.invalidate()
    receipt = b"wrong" if failure == "receipt" else RECEIPT
    actions = {
        "authorize": lambda: vault.authorize(receipt),
        "delivery": lambda: vault.check_authorized(receipt),
        "take": lambda: vault.take(1, receipt),
    }
    with pytest.raises(ValueError, match=r".+"):
        actions[boundary]()
    assert vault.state == FrameState.INVALID
    with pytest.raises(ValueError, match=r".+"):
        vault.take(1, RECEIPT)


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate-authorize",
        "unapproved-take",
        "generation",
        "delivery-before-authorize",
    ],
)
def test_vault_bad_order_is_terminal(
    monkeypatch: pytest.MonkeyPatch, sequence: retention.SequenceCandidate, failure: str
) -> None:
    """Vault bad order is terminal."""
    vault, _ = prepared_vault(monkeypatch, sequence)
    if failure in ("duplicate-authorize", "generation"):
        vault.authorize(RECEIPT)
    actions = {
        "duplicate-authorize": lambda: vault.authorize(RECEIPT),
        "generation": lambda: vault.take(2, RECEIPT),
        "delivery-before-authorize": lambda: vault.check_authorized(RECEIPT),
        "unapproved-take": lambda: vault.take(1, RECEIPT),
    }
    with pytest.raises(ValueError, match=r".+"):
        actions[failure]()
    assert vault.state == FrameState.INVALID


def test_only_current_stamp_can_expire_a_sequence(
    monkeypatch: pytest.MonkeyPatch,
    sequence: retention.SequenceCandidate,
) -> None:
    """Only current stamp can expire a sequence."""
    vault, _ = prepared_vault(monkeypatch, sequence)
    stamp = vault.stamp()
    assert stamp is not None
    assert vault.publish_stamp("retiring", 6.0) is stamp
    assert not vault.expire(stamp.revision - 1, 100.0)
    assert not vault.expire(stamp.revision, 5.999)
    assert vault.state == FrameState.SEALED
    assert vault.expire(stamp.revision, 6.0)
    assert vault.state == FrameState.INVALID


@pytest.mark.parametrize("value", ["", "0" * 32, "A" * 32, "a" * 33])
def test_epoch_is_fixed_nonzero_lowercase_hex(value: str) -> None:
    """Unqualified session identifiers cannot allocate collector state."""
    with pytest.raises(ValueError, match="epoch"):
        retention.SequenceCollector(value, lambda: None)


@pytest.mark.parametrize("value", [0, -1, True])
def test_vault_generation_is_positive_exact_integer(value: int) -> None:
    """A bool is not an authorized generation."""
    with pytest.raises(ValueError, match="generation"):
        retention.SequenceVault(value, threading.Event())


def test_vault_requires_actual_cancellation_event() -> None:
    """A duck-typed event cannot stand in for cancellation coordination."""
    with pytest.raises(ValueError, match="cancellation event"):
        retention.SequenceVault(1, cast("Any", object()))


@pytest.mark.parametrize(
    ("phase", "deadline"),
    [("", 1), ("a" * 33, 1), ("ready", 0), ("ready", float("nan")), ("ready", True)],
)
def test_bad_stamp_is_rejected(phase: str, deadline: float) -> None:
    """Malformed deadline evidence does not become an authoritative stamp."""
    vault = retention.SequenceVault(1, threading.Event())
    assert vault.stamp() is None
    assert not vault.expire(1, 20)
    with pytest.raises(ValueError, match="stamp"):
        vault.publish_stamp(phase, deadline)


@pytest.mark.parametrize(
    ("revision", "now"), [(True, 1.0), (1, float("inf")), (1, True)]
)
def test_bad_expiry_evidence_is_rejected(revision: int, now: float) -> None:
    """Timer validation excludes booleans and nonfinite clocks."""
    vault = retention.SequenceVault(1, threading.Event())
    with pytest.raises(ValueError, match="expiry"):
        vault.expire(revision, now)


@pytest.mark.parametrize("failure", ["duplicate", "epoch", "no-stamp"])
def test_begin_failure_is_permanent(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Malformed and duplicate reservations cannot resume a private sequence."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    vault = retention.SequenceVault(1, threading.Event())
    if failure != "no-stamp":
        vault.publish_stamp("ready", 5)
    if failure == "duplicate":
        vault.begin(EPOCH)
    with pytest.raises(ValueError, match=r".+"):
        vault.begin("bad" if failure == "epoch" else EPOCH)
    assert vault.state == FrameState.INVALID


@pytest.mark.parametrize("failure", ["candidate", "phase", "receipt", "identity"])
def test_seal_failure_does_not_retain_capsule(
    monkeypatch: pytest.MonkeyPatch, sequence: retention.SequenceCandidate, failure: str
) -> None:
    """Sealing cannot bypass type, identity, phase or metadata bounds."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    vault = retention.SequenceVault(1, threading.Event())
    vault.publish_stamp("ready", 5)
    vault.begin("b" * 32 if failure == "identity" else EPOCH)
    if failure != "phase":
        vault.publish_stamp("retiring", 6)
    candidate = sequence
    with pytest.raises(ValueError, match=r".+"):
        vault.seal(
            cast("Any", object()) if failure == "candidate" else candidate,
            b"" if failure == "receipt" else RECEIPT,
        )
    assert vault.state == FrameState.INVALID


@pytest.mark.parametrize("field", ["frames", "proofs", "chain", "receipt_sha256"])
def test_result_fields_are_immutable(
    monkeypatch: pytest.MonkeyPatch, sequence: retention.SequenceCandidate, field: str
) -> None:
    """The public capsule cannot contain mutable containers or digest aliases."""
    vault, _ = prepared_vault(monkeypatch, sequence)
    vault.authorize(RECEIPT)
    result = vault.take(1, RECEIPT)
    changes = {field: list(getattr(result, field))}
    with pytest.raises(ValueError, match="immutable"):
        replace(result, **changes)


@pytest.mark.parametrize(
    "change", ["count", "element", "order", "epoch", "chain", "assembly"]
)
def test_direct_candidate_cannot_skip_collector_checks(
    sequence: retention.SequenceCandidate,
    change: str,
) -> None:
    """Immutable construction rejects partial, substituted or mismatched evidence."""
    updates: dict[str, Any] = {
        "count": {"frames": sequence.frames[:-1]},
        "element": {"frames": (object(), *sequence.frames[1:])},
        "order": {"frames": tuple(reversed(sequence.frames))},
        "epoch": {"epoch": "b" * 32},
        "chain": {"chain": b"x" * 32},
        "assembly": {"assembly": object()},
    }[change]
    with pytest.raises(ValueError, match=r".+"):
        replace(sequence, **updates)


def test_collected_frame_rejects_other_actual_frame_proof(
    sequence: retention.SequenceCandidate,
) -> None:
    """A valid proof for another original source cannot bind this candidate."""
    first, second = sequence.frames[:2]
    with pytest.raises(ValueError, match="disagree"):
        retention.CollectedFrame(first.candidate, second.proof)
    with pytest.raises(ValueError, match="source or proof"):
        retention.CollectedFrame(cast("Any", object()), first.proof)
