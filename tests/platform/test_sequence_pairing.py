"""Actual bounded pairing errors and deadlines, without native socket claims."""

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from tests.capture.test_sequence_retention import EPOCH, frame

from verdiclip.capture.frame_envelope import FrameHeader
from verdiclip.capture.retained_frame import VerifiedCandidate
from verdiclip.platform.sequence_channel import SequencePairing
from verdiclip.platform.sequence_protocol import SequenceFrameProof


def pair(
    check: Callable[[], None] = lambda: None, *, index: int = 1
) -> tuple[SequencePairing, VerifiedCandidate, SequenceFrameProof]:
    """Use actual canonical observation and RGB header production."""
    candidate, proof = frame(index)
    pairing = SequencePairing(proof.observation.target, EPOCH, index + 1, index, check)
    return pairing, candidate, proof


def assert_terminal(pairing: SequencePairing, candidate: VerifiedCandidate) -> None:
    """Every API fails after a rejected operation; no recovery is permitted."""
    assert pairing.terminal
    assert not pairing.payload_ready
    for action in (
        lambda: pairing.metadata(candidate.observation),
        lambda: pairing.header(candidate.header.encode()),
        lambda: pairing.feed(candidate.rgb),
        pairing.eof,
    ):
        with pytest.raises(ValueError, match=r".+"):
            action()


@pytest.mark.parametrize("index", [1, 2, 8])
@pytest.mark.parametrize("metadata_first", [False, True])
@pytest.mark.parametrize("fragment", [1, 17, 128])
def test_real_wrapper_header_and_fragmented_body_require_actual_eof(
    index: int, fragment: int, *, metadata_first: bool
) -> None:
    """Both delivery orders preserve exact correlation and source-byte digest."""
    pairing, candidate, proof = pair(index=index)
    assert not pairing.payload_ready
    if metadata_first:
        pairing.metadata(candidate.observation)
        assert not pairing.payload_ready
    header = candidate.header.encode()
    for start in range(0, len(header), fragment):
        pairing.header(header[start : start + fragment])
    if not metadata_first:
        assert not pairing.payload_ready
        pairing.metadata(candidate.observation)
    assert pairing.payload_ready
    for start in range(0, len(candidate.rgb), 5):
        pairing.feed(candidate.rgb[start : start + 5])
    assert not pairing.terminal
    assert pairing.observation == proof.observation
    assert pairing.eof() == candidate.header
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize(
    "field",
    ["session_id", "request_id", "frame_id", "x", "width", "observation_sha256"],
)
def test_header_mismatch_is_terminal(field: str) -> None:
    """A well-formed but miscorrelated header never admits RGB."""
    pairing, candidate, _ = pair()
    changes: dict[str, object] = {
        "session_id": bytes.fromhex("b" * 32),
        "request_id": 3,
        "frame_id": 2,
        "x": 0,
        "width": 5,
        "observation_sha256": bytes(32),
    }
    identity = replace(candidate.header.identity, **{field: changes[field]})
    header = FrameHeader(identity, candidate.header.image_sha256).encode()
    pairing.metadata(candidate.observation)
    with pytest.raises(ValueError, match=r".+"):
        pairing.header(header)
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize("mismatch", ["epoch", "request", "frame", "target"])
def test_observation_mismatch_is_terminal(mismatch: str) -> None:
    """A canonical observation cannot substitute another target or frame."""
    candidate, proof = frame(1)
    pairing = SequencePairing(
        replace(proof.observation.target, hwnd=100)
        if mismatch == "target"
        else proof.observation.target,
        "b" * 32 if mismatch == "epoch" else EPOCH,
        3 if mismatch == "request" else 2,
        2 if mismatch == "frame" else 1,
        lambda: None,
    )
    with pytest.raises(ValueError, match=r".+"):
        pairing.metadata(candidate.observation)
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize(
    "failure",
    [
        "metadata",
        "header",
        "empty-header",
        "oversize-header",
        "body-before-pair",
        "eof-before-pair",
        "partial-header-eof",
        "metadata-only-eof",
        "short-body",
        "bad-digest",
        "trailer",
        "empty-chunk",
        "mutable-chunk",
    ],
)
def test_malformed_or_duplicate_flow_never_recovers(failure: str) -> None:
    """Length, digest, duplicate, type and ordering faults are terminal."""
    pairing, candidate, _ = pair()
    actions: dict[str, Callable[[], object]] = {
        "metadata": lambda: pairing.metadata(candidate.observation),
        "header": lambda: pairing.header(candidate.header.encode()),
        "empty-header": lambda: pairing.header(b""),
        "oversize-header": lambda: pairing.header(candidate.header.encode() + b"x"),
        "body-before-pair": lambda: pairing.feed(candidate.rgb),
        "eof-before-pair": pairing.eof,
        "partial-header-eof": pairing.eof,
        "metadata-only-eof": pairing.eof,
        "short-body": pairing.eof,
        "bad-digest": pairing.eof,
        "trailer": lambda: pairing.feed(b"x"),
        "empty-chunk": lambda: pairing.feed(b""),
        "mutable-chunk": lambda: pairing.feed(cast("Any", bytearray(candidate.rgb))),
    }
    if failure in ("metadata", "metadata-only-eof"):
        pairing.metadata(candidate.observation)
    elif failure == "partial-header-eof":
        pairing.header(candidate.header.encode()[:8])
    elif failure in (
        "header",
        "short-body",
        "bad-digest",
        "trailer",
        "empty-chunk",
        "mutable-chunk",
    ):
        pairing.header(candidate.header.encode())
        pairing.metadata(candidate.observation)
        if failure == "short-body":
            pairing.feed(candidate.rgb[:-1])
        elif failure == "bad-digest":
            pairing.feed(candidate.rgb[::-1])
        elif failure == "trailer":
            pairing.feed(candidate.rgb)
    with pytest.raises(ValueError, match=r".+"):
        actions[failure]()
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize("raw", [b"{}", b"x", b"\xff", b"x" * 8193])
def test_unparseable_observation_fails_closed(raw: bytes) -> None:
    """Invalid UTF8, shape and size cannot become accepted provider evidence."""
    pairing, candidate, _ = pair()
    with pytest.raises(ValueError, match=r".+"):
        pairing.metadata(raw)
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize("operation", ["metadata", "header", "feed", "eof"])
def test_cancel_before_each_operation_is_terminal(operation: str) -> None:
    """The current deadline/cancel guard is sampled before each admission."""
    check = Mock()
    pairing, candidate, _ = pair(check)
    if operation in ("feed", "eof"):
        pairing.header(candidate.header.encode())
        pairing.metadata(candidate.observation)
    if operation == "eof":
        pairing.feed(candidate.rgb)
    check.side_effect = TimeoutError("cancelled at admission")
    actions = {
        "metadata": lambda: pairing.metadata(candidate.observation),
        "header": lambda: pairing.header(candidate.header.encode()),
        "feed": lambda: pairing.feed(candidate.rgb),
        "eof": pairing.eof,
    }
    with pytest.raises(TimeoutError, match="cancelled at admission"):
        actions[operation]()
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize(
    "operation",
    ["metadata-pair", "header-pair", "feed", "eof", "metadata-first", "partial-header"],
)
def test_cancel_during_each_operation_is_terminal(operation: str) -> None:
    """A deadline/cancel change during work is checked before the method returns."""
    check = Mock()
    pairing, candidate, _ = pair(check)
    if operation == "metadata-pair":
        pairing.header(candidate.header.encode())
    elif operation == "header-pair":
        pairing.metadata(candidate.observation)
    elif operation in ("feed", "eof"):
        pairing.header(candidate.header.encode())
        pairing.metadata(candidate.observation)
    if operation == "eof":
        pairing.feed(candidate.rgb)
    check.side_effect = [None, TimeoutError("expired during operation")]
    actions = {
        "metadata-pair": lambda: pairing.metadata(candidate.observation),
        "metadata-first": lambda: pairing.metadata(candidate.observation),
        "header-pair": lambda: pairing.header(candidate.header.encode()),
        "partial-header": lambda: pairing.header(candidate.header.encode()[:8]),
        "feed": lambda: pairing.feed(candidate.rgb),
        "eof": pairing.eof,
    }
    with pytest.raises(TimeoutError, match="expired during operation"):
        actions[operation]()
    assert_terminal(pairing, candidate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("frame_id", True),
        ("frame_id", 0),
        ("frame_id", 9),
        ("frame_id", 1.0),
        ("request_id", True),
        ("request_id", 0),
        ("request_id", 33),
        ("request_id", 2.0),
        ("epoch", "0" * 32),
        ("epoch", "A" * 32),
        ("epoch", "a" * 31),
        ("epoch", b"a" * 32),
        ("target", object()),
    ],
)
def test_constructor_rejects_ambiguous_or_unbounded_identity(
    field: str, value: object
) -> None:
    """No malformed correlation can allocate a usable pairing instance."""
    _, proof = frame(1)
    arguments: dict[str, Any] = {
        "target": proof.observation.target,
        "epoch": EPOCH,
        "request_id": 2,
        "frame_id": 1,
        "check": lambda: None,
    }
    arguments[field] = value
    with pytest.raises(ValueError, match="invalid-frame-pairing-identity"):
        SequencePairing(**arguments)
