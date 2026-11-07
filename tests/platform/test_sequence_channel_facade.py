"""Owner-thread public sequence boundaries, with no native socket execution."""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication
from tests.capture.test_sequence_retention import frame

from verdiclip.capture.retained_frame import DeadlineStamp
from verdiclip.capture.sequence_credit import CreditToken
from verdiclip.platform import sequence_channel as channel
from verdiclip.platform.sequence_protocol import Decoder, Message, encode


@pytest.fixture
def facade(
    qapp: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> channel.SequenceChannel:
    """Use immutable queued data and a metadata-only fake vault without a thread."""
    del qapp
    value = channel.SequenceChannel()
    value._state = "ready"
    value._generation = 1
    value._credit_epoch = "a" * 32
    value._sequence_deadline_ns = 30_000_000_000
    value._vault = Mock()
    value._vault.stamp.return_value = DeadlineStamp("ready", 1, 30.0)
    monkeypatch.setattr(channel.time, "monotonic_ns", lambda: 1_000_000_000)
    return value


def begin(facade: channel.SequenceChannel) -> None:
    """Complete only the public begin correlation callback."""
    _, proof = frame(1)
    facade.begin(1, proof.observation.target)
    facade._reply(
        1,
        1,
        channel._serialized(
            {"operation": "sequence_begin", "deadline_ns": 30_000_000_000}
        ),
    )


def test_typed_public_begin_observe_credit_and_finish(
    facade: channel.SequenceChannel,
) -> None:
    """Only one queued request and one consumed exact credit token can be active."""
    requests: list[tuple[int, bytes]] = []
    grants: list[bytes] = []
    finished: list[bool] = []
    facade._request.connect(lambda caller, raw: requests.append((caller, raw)))
    facade._credit.connect(lambda _g, raw: grants.append(raw))
    facade._finish.connect(lambda: finished.append(True))
    begin(facade)
    assert json.loads(requests[0][1])["operation"] == "sequence_begin"
    facade.observe(2)
    token = CreditToken.for_chunk("a" * 32, 2, 1, 36, 0)
    facade._credit_required(1, encode(Message("frame_wait", 2, token.payload())))
    facade.grant_frame_credit(1, 2, 1, 0)
    assert facade._pending_credit is None
    assert Decoder().feed(grants[0]) == [Message("frame_credit", 2, token.payload())]
    facade._reply(1, 2, b'{"operation":"frame_accepted","frame_id":1}')
    facade.finish()
    assert facade._state == "closing"
    assert finished == [True]


@pytest.mark.parametrize(
    "field", ["generation", "request", "frame", "chunk", "missing", "duplicate"]
)
def test_bad_public_credit_irreversibly_cancels(
    facade: channel.SequenceChannel, field: str
) -> None:
    """An invalid capacity grant cannot merely raise while transfer continues."""
    begin(facade)
    facade.observe(2)
    token = CreditToken.for_chunk("a" * 32, 2, 1, 36, 0)
    if field != "missing":
        facade._credit_required(1, encode(Message("frame_wait", 2, token.payload())))
    args = [1, 2, 1, 0]
    if field == "duplicate":
        facade.grant_frame_credit(*args)
    elif field != "missing":
        args[["generation", "request", "frame", "chunk"].index(field)] += 1
    with pytest.raises(ValueError, match="credit"):
        facade.grant_frame_credit(*args)
    assert facade._cancelled.is_set()
    assert facade._state == "closing"
    cast("Mock", facade._vault).invalidate.assert_called()


@pytest.mark.parametrize(
    "fault", ["epoch", "frame", "request", "chunk", "duplicate", "kind", "trailing"]
)
def test_bad_public_wait_cancels(facade: channel.SequenceChannel, fault: str) -> None:
    """Stale or malformed waits cannot be exposed as another capacity token."""
    begin(facade)
    facade.observe(2)
    token = CreditToken.for_chunk(
        "b" * 32 if fault == "epoch" else "a" * 32,
        3 if fault == "request" else 2,
        2 if fault == "frame" else 1,
        131072,
        1 if fault == "chunk" else 0,
    )
    raw = encode(
        Message(
            "frame_credit" if fault == "kind" else "frame_wait",
            token.request_id,
            token.payload(),
        )
    )
    if fault == "duplicate":
        facade._credit_required(1, raw)
    if fault == "trailing":
        raw += b"x"
    with pytest.raises(ValueError, match=r".+"):
        facade._credit_required(1, raw)
    assert facade._cancelled.is_set()


@pytest.mark.parametrize("mode", ["old-generation", "closing", "cancelled"])
def test_stale_queued_wait_is_filtered(
    facade: channel.SequenceChannel, mode: str
) -> None:
    """A retired generation never reopens a capacity window."""
    begin(facade)
    facade.observe(2)
    events: list[bytes] = []
    facade.credit_required.connect(lambda _g, raw: events.append(raw))
    if mode == "closing":
        facade._state = "closing"
    if mode == "cancelled":
        facade._cancelled.set()
    facade._credit_required(0 if mode == "old-generation" else 1, b"malformed")
    assert events == []
    assert facade._pending_credit is None


@pytest.mark.parametrize(
    "fault", ["not-begun", "frame-limit", "caller", "stamp", "expiry"]
)
def test_invalid_public_observe_never_queues(
    facade: channel.SequenceChannel, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """No request gets a new clock after serialization or invalid state."""
    if fault != "not-begun":
        begin(facade)
    if fault == "frame-limit":
        facade._frame_id = 8
    if fault == "stamp":
        cast("Mock", facade._vault).stamp.return_value = None
    if fault == "expiry":
        monkeypatch.setattr(
            channel.time,
            "monotonic_ns",
            Mock(side_effect=[1_000_000_000, 3_000_000_000]),
        )
    requests: list[bytes] = []
    facade._request.connect(lambda _caller, raw: requests.append(raw))
    with pytest.raises(ValueError, match=r".+"):
        facade.observe(1 if fault == "caller" else 2)
    assert requests == []
    assert facade._cancelled.is_set()


def test_public_phase_metadata_has_no_pixels(facade: channel.SequenceChannel) -> None:
    """Identity/ready/progress are filtered to the current generation and state."""
    events: list[object] = []
    facade.identity.connect(lambda *args: events.append(args))
    facade.ready.connect(lambda *args: events.append(args))
    facade.progress.connect(lambda *args: events.append(args))
    facade._state = "starting"
    facade._identity(0, b"old")
    facade._identity(1, b"identity")
    facade._ready(0)
    facade._ready(1)
    facade._progress(1, b"not-busy")
    facade._state = "busy"
    facade._progress(1, b"metadata")
    assert events == [(1, b"identity"), (1,), (1, b"metadata")]
