"""Pure framing contracts extracted from the native inert transport proof."""

import json
import struct

import pytest

from verdiclip.platform.sequence_protocol import (
    HEADER_BYTES,
    MAX_JSON_DEPTH,
    MAX_MESSAGE_BYTES,
    MAX_REQUESTS,
    Decoder,
    Message,
    ProtocolError,
    decode,
    encode,
)


def test_every_byte_boundary_can_fragment() -> None:
    """Header and UTF-8 payload splits preserve exactly one complete message."""
    message = Message("response", 1, {"text": "écho"})
    wire = encode(message)
    for cut in range(len(wire) + 1):
        decoder = Decoder()
        assert decoder.feed(wire[:cut]) + decoder.feed(wire[cut:]) == [message]
        assert decoder.buffer == b""
    decoder = Decoder()
    received = []
    for value in wire:
        received.extend(decoder.feed(bytes([value])))
    assert received == [message]


def test_coalesced_messages_and_partial_tail() -> None:
    """Multiple bounded frames do not lose the incomplete trailing message."""
    first, second = Message("ready", 0, {}), Message("response", 1, {})
    a, b = encode(first), encode(second)
    decoder = Decoder()
    assert decoder.feed(a + b[:8]) == [first]
    assert decoder.feed(b[8:]) == [second]


@pytest.mark.parametrize("size", [0, MAX_MESSAGE_BYTES + 1, 0xFFFFFFFF])
def test_bad_length_rejects_before_payload_and_latches(size: int) -> None:
    """A hostile header cannot request an allocation or recover after rejection."""
    decoder = Decoder()
    with pytest.raises(ProtocolError, match="oversized"):
        decoder.feed(struct.pack(">I", size))
    assert decoder.buffer == b""
    with pytest.raises(ProtocolError, match="decoder-failed"):
        decoder.feed(encode(Message("response", 1, {})))


def test_buffer_limit_even_without_message_completion() -> None:
    """Raw incoming buffers have the same explicit bounded allocation ceiling."""
    decoder = Decoder()
    with pytest.raises(ProtocolError, match="buffer-limit"):
        decoder.feed(b"x" * (MAX_MESSAGE_BYTES + HEADER_BYTES + 1))
    assert decoder.failed


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b"{}",
        b"[]",
        b"{",
        b'{"version":3,"version":3}',
        b'{"version":true,"kind":"response","request_id":1,"payload":{}}',
        b'{"version":3,"kind":"response","request_id":true,"payload":{}}',
        b'{"version":3,"kind":"response","request_id":1,"payload":{"x":NaN}}',
        b'{"version":3,"kind":"response","request_id":1,"payload":{"x":1e999}}',
        b'{"version":3,"kind":"response","request_id":1,"payload":[],"extra":0}',
    ],
)
def test_malformed_envelopes_reject(payload: bytes) -> None:
    """Identity fields are not coerced, overwritten or nonfinite."""
    with pytest.raises(ProtocolError):
        Decoder().feed(struct.pack(">I", len(payload)) + payload)


def test_nesting_and_request_budget() -> None:
    """Small byte count is not permission for deeply nested JSON or endless IDs."""
    value: object = 1
    for _ in range(MAX_JSON_DEPTH + 1):
        value = [value]
    wire = json.dumps(
        {"version": 3, "kind": "response", "request_id": 1, "payload": {"x": value}}
    ).encode()
    with pytest.raises(ProtocolError, match="json-complexity"):
        decode(wire)
    with pytest.raises(ProtocolError, match="invalid-envelope"):
        encode(Message("response", MAX_REQUESTS + 1, {}))
    with pytest.raises(ProtocolError, match="oversized"):
        encode(Message("response", 1, {"text": "x" * MAX_MESSAGE_BYTES}))
