"""Real credit framing and peer ordering with deterministic non-native endpoints."""

import json
import struct
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from verdiclip.capture.frame_credit import CHUNK_BYTES, CreditToken
from verdiclip.capture.frame_envelope import FrameIdentity, FrameStream
from verdiclip.platform import helper_peer as peer
from verdiclip.platform.helper_protocol import Decoder, Message, ProtocolError, encode

_EPOCH = "b" * 32
_DEADLINE = 3_000_000_000


@pytest.mark.parametrize("kind", ["frame_wait", "frame_credit"])
def test_exact_credit_record_boundary_with_every_fragment_split(kind: str) -> None:
    """The prefix counts toward 512 bytes, including whitespace on the wire."""
    token = CreditToken.for_chunk(_EPOCH, 1, CHUNK_BYTES, 0)
    message = Message(kind, 1, token.payload())
    encoded = encode(message)
    payload = encoded[4:] + b" " * (512 - len(encoded))
    for split in range(1, 512):
        decoder = Decoder()
        wire = struct.pack(">I", len(payload)) + payload
        assert len(wire) == 512
        assert decoder.feed(wire[:split]) == []
        assert decoder.feed(wire[split:]) == [message]
        assert decoder.buffer == bytearray()
        decoder = Decoder()
        oversized = struct.pack(">I", len(payload) + 1) + payload + b" "
        assert len(oversized) == 513
        assert decoder.feed(oversized[:split]) == []
        with pytest.raises(ProtocolError, match="oversized-credit-record"):
            decoder.feed(oversized[split:])
        assert decoder.buffer == bytearray()
        with pytest.raises(ProtocolError, match="decoder-failed"):
            decoder.feed(encoded)


@pytest.mark.parametrize("kind", ["frame_wait", "frame_credit"])
def test_encoder_applies_credit_cap_and_old_version_fails(kind: str) -> None:
    """Real encoders cannot emit an old-version or oversized credit record."""
    padding = 512 - len(encode(Message(kind, 1, {"padding": ""})))
    assert len(encode(Message(kind, 1, {"padding": "x" * padding}))) == 512
    message = Message(kind, 1, {"padding": "x" * (padding + 1)})
    with pytest.raises(ProtocolError, match="oversized-credit-record"):
        encode(message)
    token = CreditToken.for_chunk(_EPOCH, 1, CHUNK_BYTES, 0)
    valid = Message(kind, 1, token.payload())
    with pytest.raises(ProtocolError, match="invalid-envelope"):
        encode(replace(valid, version=1))
    old = encode(valid)[4:].replace(b'"version":2', b'"version":1')
    decoder = Decoder()
    with pytest.raises(ProtocolError, match="invalid-envelope"):
        decoder.feed(struct.pack(">I", len(old)) + old)
    assert decoder.failed


@dataclass
class Transfer:
    """One actual peer observation with only native transport/provider substituted."""

    control: Mock
    frame: Mock
    provider: Mock
    protocol: peer.PeerProtocol
    request: Message
    launch: peer.PeerLaunch
    pixels: bytes
    now: list[int]
    events: list[tuple[str, object]]

    def run(self) -> None:
        """Exercise production peer orchestration, codecs and credit state."""
        peer._observe(
            self.control, self.frame, self.launch, self.protocol, self.request
        )


@pytest.fixture
def transfer(monkeypatch: pytest.MonkeyPatch) -> Transfer:
    """Retain the real shared deadline and encode/decode every control record."""
    now = [1_000_000_000]
    monkeypatch.setattr(peer.time, "monotonic_ns", lambda: now[0])
    launch = peer.PeerLaunch("VerdiClip-helper-" + "a" * 32, _EPOCH, 7)
    target = peer.ObserveTarget(7, 1234, 99, (1, 1))
    request = Message(
        "request",
        1,
        {
            "operation": "observe",
            "observe_schema": 1,
            "deadline_ns": _DEADLINE,
            "request": json.loads(target.serialize()),
        },
    )
    identity = FrameIdentity(bytes.fromhex(_EPOCH), 1, 1, 0, 0, 257, 256, bytes(32))
    pixels = bytes(identity.payload_bytes)
    observation = Mock()
    observation.canonical = b'{"provider_fixture":true}'
    observation.frame_identity.return_value = identity
    provider = Mock(return_value=SimpleNamespace(observation=observation, rgb=pixels))
    monkeypatch.setattr(peer, "observe_once", provider)
    control, frame = Mock(), Mock()
    events: list[tuple[str, object]] = []

    def checkpoint() -> None:
        control.observe_clock.check()
        assert control.deadline_ns == frame.deadline_ns == _DEADLINE
        assert control.observe_clock is frame.observe_clock

    def write(message: Message) -> None:
        checkpoint()
        assert Decoder().feed(encode(message)) == [message]
        events.append((message.kind, message))

    def write_bytes(data: bytes) -> None:
        checkpoint()
        events.append(("binary", data))

    control.checkpoint.side_effect = checkpoint
    control.write.side_effect = write
    frame.write_bytes.side_effect = write_bytes
    frame.close.side_effect = lambda: events.append(("close", None))

    def read() -> Message:
        wait = control.write.call_args.args[0]
        assert wait.kind == "frame_wait"
        index = wait.payload["chunk_index"]
        assert frame.write_bytes.call_count == index + 1
        events.append(("credit", index))
        return Decoder().feed(encode(Message("frame_credit", 1, wait.payload)))[0]

    control.read.side_effect = read
    return Transfer(
        control,
        frame,
        provider,
        peer.PeerProtocol(),
        request,
        launch,
        pixels,
        now,
        events,
    )


def test_peer_waits_before_each_body_write_and_keeps_deadline(
    transfer: Transfer,
) -> None:
    """Only a matching grant unlocks the next bounded chunk, including the tail."""
    transfer.run()
    assert [kind for kind, _ in transfer.events] == [
        "response",
        "binary",
        *[kind for _ in range(4) for kind in ("frame_wait", "credit", "binary")],
        "close",
    ]
    chunks = [call.args[0] for call in transfer.frame.write_bytes.call_args_list]
    assert len(chunks[0]) == 128
    assert [len(chunk) for chunk in chunks[1:]] == [65536, 65536, 65536, 768]
    identity = transfer.provider.return_value.observation.frame_identity()
    stream = FrameStream(chunks[0], identity)
    for chunk in chunks[1:]:
        stream.feed(chunk)
    assert stream.finish().identity.payload_bytes == len(transfer.pixels)
    assert transfer.provider.call_args.kwargs["deadline"] == 3.0
    assert transfer.protocol.last_id == 1
    assert transfer.protocol.observe_consumed
    transfer.frame.close.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        "wrong-kind",
        "wrong-request",
        "wrong-epoch",
        "wrong-sequence",
        "wrong-count",
        "wrong-offset",
        "wrong-frame",
        "wrong-schema",
        "missing-field",
        "extra-field",
        "boolean",
        "duplicate-key",
        "old-version",
        "duplicate-credit",
        "expired-first",
        "expired-interior",
    ],
)
def test_peer_rejects_credit_without_next_body_write(
    transfer: Transfer, failure: str
) -> None:
    """Bad, repeated or late grants never authorize the held chunk."""
    original_read = transfer.control.read.side_effect
    first: Message | None = None

    def read() -> Message:
        nonlocal first
        message = original_read()
        assert isinstance(message, Message)
        if failure in ("duplicate-credit", "expired-interior") and first is None:
            first = message
            return message
        if failure == "duplicate-credit":
            assert first is not None
            return first
        if failure.startswith("expired"):
            transfer.now[0] = _DEADLINE
            return message
        payload = dict(message.payload)
        envelope_changes = {
            "wrong-kind": {"kind": "request"},
            "wrong-request": {"request_id": 2},
        }
        if failure in envelope_changes:
            message = replace(message, **envelope_changes[failure])
        payload_changes: dict[str, dict[str, object]] = {
            "wrong-epoch": {"epoch": "c" * 32},
            "wrong-sequence": CreditToken.for_chunk(
                _EPOCH, 1, len(transfer.pixels), 1
            ).payload(),
            "wrong-count": {"count": CHUNK_BYTES - 1},
            "wrong-offset": {"offset": 1},
            "wrong-frame": {"frame_id": 2},
            "wrong-schema": {"credit_schema": 2},
            "extra-field": {"extra": 1},
            "boolean": {"chunk_index": False},
        }
        payload.update(payload_changes.get(failure, {}))
        if failure == "missing-field":
            del payload["count"]
        encoded = encode(replace(message, payload=payload))[4:]
        if failure == "duplicate-key":
            encoded = encoded.replace(b'"count":', b'"count":1,"count":')
        elif failure == "old-version":
            encoded = encoded.replace(b'"version":2', b'"version":1')
        return Decoder().feed(struct.pack(">I", len(encoded)) + encoded)[0]

    transfer.control.read.side_effect = read
    with pytest.raises(ValueError, match=r".+"):
        transfer.run()
    allowed = 1 if failure in ("duplicate-credit", "expired-interior") else 0
    assert transfer.frame.write_bytes.call_count == allowed + 1
    assert transfer.control.read.call_count == allowed + 1
    transfer.frame.close.assert_not_called()
    assert transfer.control.deadline_ns == transfer.frame.deadline_ns == _DEADLINE
    assert transfer.protocol.last_id == 1
