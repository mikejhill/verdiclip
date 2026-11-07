"""Bounded private sequence ownership; no native or scrolling qualification."""

import hashlib
import json
import threading
from dataclasses import replace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from verdiclip.capture import sequence_retention as retention
from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.capture.observe_protocol import Observation, ObserveTarget
from verdiclip.capture.retained_frame import FrameState, VerifiedCandidate
from verdiclip.platform.sequence_protocol import SequenceFrameProof, SequenceObservation

EPOCH = "a" * 32
RGB = bytes(range(36))
RECEIPT = b'{"passed":true}'


def frame(
    index: int, *, hwnd: int = 99, left: int = -2
) -> tuple[VerifiedCandidate, SequenceFrameProof]:
    """Pass a real provider-local observation through its sequence wire wrapper."""
    target = ObserveTarget(7, 1234, hwnd, (-1, 2))
    inner = Observation.parse(
        json.dumps(
            {
                "observation_schema": 1,
                "epoch": EPOCH,
                "request_id": index + 1,
                "frame_id": 1,
                "target": {
                    **target.value(),
                    "runtime_id": [42, -1],
                    "framework": "WPF",
                },
                "guard": {
                    "foreground_hwnd": hwnd,
                    "foreground_pid": 7,
                    "input_tick": 123,
                    "root_client_rect": [-10, -10, 100, 100],
                    "target_rect": [left, 1, left + 4, 4],
                    "capture_rect": [left, 1, left + 4, 4],
                    "dpi": [96, 96],
                    "monitors": [[1, -100, -100, 100, 100, True]],
                    "visibility_evidence": {
                        "scope": "owned-wpf-fixture-external-check-v1",
                        "root_visible": True,
                        "root_minimized": False,
                        "root_cloaked": False,
                        "external_intersection_count": 0,
                        "point_root_checks": 6,
                        "internal_overlay_assessment": "fixture-assumption-only",
                    },
                },
                "scroll": {
                    "horizontal": -1,
                    "vertical": 0,
                    "horizontal_view": 100,
                    "vertical_view": 25,
                    "horizontal_scrollable": False,
                    "vertical_scrollable": True,
                },
                "settled": {"samples": 3, "quiet_ns": 200_000_000},
            }
        ).encode()
    )
    observation = SequenceObservation.from_observation(inner, index)
    header = FrameHeader(observation.frame_identity(), hashlib.sha256(RGB).digest())
    candidate = VerifiedCandidate(RGB, header, observation.canonical)
    proof = SequenceFrameProof.parse(
        json.dumps(
            {
                "proof_schema": 1,
                "epoch": EPOCH,
                "request_id": index + 1,
                "frame_id": index,
                "requested_ns": 1_000_000_000,
                "deadline_ns": 3_000_000_000,
                "verified_ns": 1_500_000_000,
                "header_sha256": hashlib.sha256(header.encode()).hexdigest(),
                "observation_sha256": observation.digest.hex(),
                "image_sha256": header.image_sha256.hex(),
                "bytes": len(RGB),
                "credit_chunks": 1,
                "granted_bytes": len(RGB),
                "accepted_bytes": len(RGB),
                "credit_events": [
                    [1, 1_100_000_000, "wait", 0],
                    [2, 1_200_000_000, "grant", 0],
                ],
                "observation": json.loads(observation.canonical),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return candidate, proof


def test_two_frames_transfer_once_and_preserve_source_bytes() -> None:
    """Two frames transfer once and preserve source bytes."""
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    for index in (1, 2):
        candidate, proof = frame(index)
        collector.reserve(candidate.header.identity)
        collector.accept(candidate, proof)
        collector.commit(proof.digest)
    result = collector.finish()
    assert [x.candidate.header.identity.frame_id for x in result.frames] == [1, 2]
    assert all(x.candidate.rgb is RGB for x in result.frames)
    assert collector.retained_bytes == 0
    with pytest.raises(ValueError, match=r".+"):
        collector.finish()


@pytest.mark.parametrize("count", [0, 1, 7, 8])
def test_frame_bound_precedes_next_allocation(count: int) -> None:
    """Frame bound precedes next allocation."""
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    for index in range(1, count + 1):
        candidate, proof = frame(index)
        collector.reserve(candidate.header.identity)
        collector.accept(candidate, proof)
        collector.commit(proof.digest)
    if count == 8:
        candidate, _ = frame(1)
        with pytest.raises(ValueError, match=r".+"):
            collector.reserve(
                replace(candidate.header.identity, frame_id=9, request_id=10)
            )
        assert collector.retained_bytes == 0
    else:
        collector.discard()
        assert collector.retained_bytes == 0


def test_wrong_proof_discards_every_prior_frame() -> None:
    """Wrong proof discards every prior frame."""
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    one, proof_one = frame(1)
    collector.reserve(one.header.identity)
    collector.accept(one, proof_one)
    collector.commit(proof_one.digest)
    two, _ = frame(2)
    collector.reserve(two.header.identity)
    with pytest.raises(ValueError, match=r".+"):
        collector.accept(two, proof_one)
    assert collector.retained_bytes == 0
    with pytest.raises(ValueError, match=r".+"):
        collector.finish()


def test_cancelled_collection_disposes_private_frames() -> None:
    """Cancelled collection disposes private frames."""
    check = Mock()
    collector = retention.SequenceCollector(EPOCH, check)
    candidate, proof = frame(1)
    collector.reserve(candidate.header.identity)
    collector.accept(candidate, proof)
    check.side_effect = TimeoutError("original frame deadline")
    with pytest.raises(TimeoutError):
        collector.commit(proof.digest)
    assert collector.retained_bytes == 0


def test_vault_authorization_cancel_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vault authorization cancel and single use."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    candidate, proof = frame(1)
    collector.reserve(candidate.header.identity)
    collector.accept(candidate, proof)
    collector.commit(proof.digest)
    vault = retention.SequenceVault(1, threading.Event())
    vault.publish_stamp("active", 5.0)
    vault.begin(EPOCH)
    vault.publish_stamp("retiring", 6.0)
    vault.seal(collector.finish(), RECEIPT)
    vault.authorize(RECEIPT)
    vault.check_authorized(RECEIPT)
    result = vault.take(1, RECEIPT)
    assert result.frames[0].rgb is RGB
    vault.invalidate()
    assert vault.state == FrameState.TAKEN
    assert result.frames[0].rgb == RGB
    with pytest.raises(ValueError, match=r".+"):
        vault.take(1, RECEIPT)


def complete_collector() -> retention.SequenceCollector:
    """Build a committed single-frame collector through its normal boundaries."""
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    candidate, proof = frame(1)
    collector.reserve(candidate.header.identity)
    collector.accept(candidate, proof)
    collector.commit(proof.digest)
    return collector


def test_foreign_thread_failure_permanently_invalidates_collector() -> None:
    """Foreign thread failure permanently invalidates collector."""
    collector = complete_collector()
    errors: list[Exception] = []

    def foreign_finish() -> None:
        try:
            collector.finish()
        except ValueError as error:
            errors.append(error)

    worker = threading.Thread(target=foreign_finish)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert len(errors) == 1
    with pytest.raises(ValueError, match=r".+"):
        collector.finish()
    assert collector.retained_bytes == 0


def test_collected_frame_cannot_pair_different_image_proof() -> None:
    """Collected frame cannot pair different image proof."""
    candidate, proof = frame(1)
    other_rgb = RGB[::-1]
    changed = VerifiedCandidate(
        other_rgb,
        FrameHeader(candidate.header.identity, hashlib.sha256(other_rgb).digest()),
        candidate.observation,
    )
    with pytest.raises(ValueError, match=r".+"):
        retention.CollectedFrame(changed, proof)


def test_collector_rejects_changed_target_with_identical_geometry() -> None:
    """Collector rejects changed target with identical geometry."""
    collector = complete_collector()
    candidate, proof = frame(2, hwnd=100)
    collector.reserve(candidate.header.identity)
    with pytest.raises(ValueError, match=r".+"):
        collector.accept(candidate, proof)
    assert collector.retained_bytes == 0


@pytest.mark.parametrize("change", ["target", "geometry", "chain-type"])
def test_direct_candidate_has_same_binding_as_collector(change: str) -> None:
    """Direct candidate has same binding as collector."""
    one, proof_one = frame(1)
    two, proof_two = frame(
        2,
        hwnd=100 if change == "target" else 99,
        left=-1 if change == "geometry" else -2,
    )
    chain = hashlib.sha256(
        hashlib.sha256(bytes(32) + proof_one.digest).digest() + proof_two.digest
    ).digest()
    with pytest.raises(ValueError, match=r".+"):
        retention.SequenceCandidate(
            EPOCH,
            (
                retention.CollectedFrame(one, proof_one),
                retention.CollectedFrame(two, proof_two),
            ),
            cast("Any", bytearray(chain)) if change == "chain-type" else chain,
        )


def prepared_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[retention.SequenceVault, threading.Event]:
    """Seal through normal collector and retirement transitions with a fixed clock."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    cancel = threading.Event()
    vault = retention.SequenceVault(1, cancel)
    vault.publish_stamp("active", 5.0)
    vault.begin(EPOCH)
    vault.publish_stamp("retiring", 6.0)
    vault.seal(complete_collector().finish(), RECEIPT)
    return vault, cancel


@pytest.mark.parametrize("boundary", ["authorize", "delivery", "take"])
@pytest.mark.parametrize(
    "failure", ["cancel", "expired", "clock-backwards", "deleted", "receipt"]
)
def test_vault_rechecks_every_publication_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: str, failure: str
) -> None:
    """Vault rechecks every publication boundary."""
    vault, cancel = prepared_vault(monkeypatch)
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
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Vault bad order is terminal."""
    vault, _ = prepared_vault(monkeypatch)
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
) -> None:
    """Only current stamp can expire a sequence."""
    vault, _ = prepared_vault(monkeypatch)
    stamp = vault.stamp()
    assert stamp is not None
    assert vault.publish_stamp("retiring", 6.0) is stamp
    assert not vault.expire(stamp.revision - 1, 100.0)
    assert not vault.expire(stamp.revision, 5.999)
    assert vault.state == FrameState.SEALED
    assert vault.expire(stamp.revision, 6.0)
    assert vault.state == FrameState.INVALID


@pytest.mark.parametrize("operation", ["reserve", "accept", "commit", "finish"])
def test_postoperation_deadline_failure_discards_all(operation: str) -> None:
    """Postoperation deadline failure discards all."""
    check = Mock()
    collector = retention.SequenceCollector(EPOCH, check)
    candidate, proof = frame(1)
    if operation != "reserve":
        collector.reserve(candidate.header.identity)
    if operation in ("commit", "finish"):
        collector.accept(candidate, proof)
    if operation == "finish":
        collector.commit(proof.digest)
    check.side_effect = [None, TimeoutError("post-operation equality")]
    actions = {
        "reserve": lambda: collector.reserve(candidate.header.identity),
        "accept": lambda: collector.accept(candidate, proof),
        "commit": lambda: collector.commit(proof.digest),
        "finish": collector.finish,
    }
    with pytest.raises(TimeoutError, match="post-operation equality"):
        actions[operation]()
    assert collector.retained_bytes == 0
    with pytest.raises(ValueError, match=r".+"):
        collector.finish()


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate-reserve",
        "skipped",
        "geometry",
        "commit-digest",
        "commit-empty",
        "duplicate-accept",
    ],
)
def test_collector_order_errors_invalidate_all_sources(failure: str) -> None:
    """Collector order errors invalidate all sources."""
    collector = complete_collector()
    candidate, proof = frame(2, left=-1 if failure == "geometry" else -2)
    if failure in ("duplicate-reserve", "commit-digest", "duplicate-accept"):
        collector.reserve(candidate.header.identity)
    if failure in ("commit-digest", "duplicate-accept"):
        collector.accept(candidate, proof)
    actions = {
        "commit-empty": lambda: collector.commit(proof.digest),
        "skipped": lambda: collector.reserve(
            replace(candidate.header.identity, frame_id=3)
        ),
        "duplicate-reserve": lambda: collector.reserve(candidate.header.identity),
        "geometry": lambda: collector.reserve(candidate.header.identity),
        "duplicate-accept": lambda: collector.accept(candidate, proof),
        "commit-digest": lambda: collector.commit(bytes(32)),
    }
    with pytest.raises(ValueError, match=r".+"):
        actions[failure]()
    assert collector.retained_bytes == 0
    with pytest.raises(ValueError, match=r".+"):
        collector.finish()


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
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Sealing cannot bypass type, identity, phase or metadata bounds."""
    monkeypatch.setattr(retention.time, "monotonic", lambda: 1.0)
    vault = retention.SequenceVault(1, threading.Event())
    vault.publish_stamp("ready", 5)
    vault.begin("b" * 32 if failure == "identity" else EPOCH)
    if failure != "phase":
        vault.publish_stamp("retiring", 6)
    candidate = complete_collector().finish()
    with pytest.raises(ValueError, match=r".+"):
        vault.seal(
            cast("Any", object()) if failure == "candidate" else candidate,
            b"" if failure == "receipt" else RECEIPT,
        )
    assert vault.state == FrameState.INVALID


@pytest.mark.parametrize("failure", ["empty", "element", "order", "chain"])
def test_direct_candidate_rejects_incomplete_sources(failure: str) -> None:
    """Direct immutable construction enforces the same full-sequence shape."""
    candidate, proof = frame(2 if failure == "order" else 1)
    collected = retention.CollectedFrame(candidate, proof)
    frames = () if failure == "empty" else (collected,)
    if failure == "element":
        frames = (cast("Any", object()),)
    with pytest.raises(ValueError, match=r".+"):
        retention.SequenceCandidate(EPOCH, frames, bytes(32))


def test_collected_frame_checks_exact_types() -> None:
    """A structurally similar object is not verified source evidence."""
    _, proof = frame(1)
    with pytest.raises(ValueError, match="source or proof"):
        retention.CollectedFrame(cast("Any", object()), proof)


def test_collector_metadata_never_exposes_pixels() -> None:
    """Only finite counters and a digest are available before final transfer."""
    collector = complete_collector()
    _, proof = frame(1)
    assert collector.count == 1
    assert collector.chain == hashlib.sha256(bytes(32) + proof.digest).digest()
    assert collector.retained_bytes == len(RGB)


def test_reserved_frame_cannot_finish_before_acceptance() -> None:
    """A received or pending source is not a committed sequence."""
    collector = retention.SequenceCollector(EPOCH, lambda: None)
    candidate, _ = frame(1)
    collector.reserve(candidate.header.identity)
    with pytest.raises(ValueError, match="committed"):
        collector.finish()
    assert collector.retained_bytes == 0


@pytest.mark.parametrize("field", ["frames", "proofs", "chain", "receipt_sha256"])
def test_result_fields_are_immutable(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """The public capsule cannot contain mutable containers or digest aliases."""
    vault, _ = prepared_vault(monkeypatch)
    vault.authorize(RECEIPT)
    result = vault.take(1, RECEIPT)
    changes = {field: list(getattr(result, field))}
    with pytest.raises(ValueError, match="immutable"):
        replace(result, **changes)


def changed_observation(
    field: str,
    value: object,
) -> tuple[VerifiedCandidate, SequenceFrameProof]:
    """Rebuild real frame2 wire and all digests after one inner evidence change."""
    original, proof = frame(2)
    inner = proof.value["observation"]["observation"]
    section, key = field.split(".")
    inner[section][key] = value
    observation = SequenceObservation.from_observation(
        Observation.parse(json.dumps(inner).encode()), 2
    )
    header = FrameHeader(observation.frame_identity(), original.header.image_sha256)
    candidate = VerifiedCandidate(original.rgb, header, observation.canonical)
    data = proof.value
    data.update(
        observation=json.loads(observation.canonical),
        observation_sha256=observation.digest.hex(),
        header_sha256=hashlib.sha256(header.encode()).hexdigest(),
    )
    return candidate, SequenceFrameProof.parse(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target.runtime_id", [43, -1]),
        ("guard.input_tick", 124),
        ("guard.monitors", [[2, -100, -100, 100, 100, True]]),
        ("guard.root_client_rect", [-11, -10, 100, 100]),
        ("scroll.vertical", 1),
        ("scroll.vertical_view", 26),
        ("scroll.horizontal_view", 99),
    ],
)
def test_readonly_sequence_rejects_changed_inner_source_state(
    field: str,
    value: object,
) -> None:
    """Same descriptor and rectangle cannot conceal changed read-only evidence."""
    collector = complete_collector()
    first, first_proof = frame(1)
    candidate, proof = changed_observation(field, value)
    assert proof.observation.target == first_proof.observation.target
    before, after = first.header.identity, candidate.header.identity
    assert (before.x, before.y, before.width, before.height) == (
        after.x,
        after.y,
        after.width,
        after.height,
    )
    collector.reserve(candidate.header.identity)
    with pytest.raises(ValueError, match="changed"):
        collector.accept(candidate, proof)
    assert collector.retained_bytes == 0
    assert collector.count == 1  # No second frame_accept can be committed.
    with pytest.raises(ValueError, match="unavailable"):
        collector.commit(proof.digest)
    with pytest.raises(ValueError, match="unavailable"):
        collector.finish()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target.framework", "Chromium"),
        ("guard.dpi", [144, 144]),
        ("scroll.vertical_scrollable", False),
        ("guard.foreground_hwnd", 100),
        ("guard.foreground_pid", 8),
    ],
)
def test_unqualified_framework_and_foreground_fail_provider_schema(
    field: str,
    value: object,
) -> None:
    """The unchanged provider parser rejects unsupported evidence before retention."""
    with pytest.raises(ValueError, match=r".+"):
        changed_observation(field, value)


def test_readonly_continuity_excludes_only_invocation_timing_and_correlation() -> None:
    """Quiet duration and proof acquisition stamps may differ between equal reads."""
    collector = complete_collector()
    candidate, proof = changed_observation("settled.quiet_ns", 300_000_000)
    data = proof.value
    data["verified_ns"] += 100_000_000
    proof = SequenceFrameProof.parse(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    )
    collector.reserve(candidate.header.identity)
    collector.accept(candidate, proof)
    collector.commit(proof.digest)
    assert len(collector.finish().frames) == 2
