"""Pure RGB envelope tests; no filesystem, provider or capture qualification."""

import hashlib
import struct
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from verdiclip.capture.frame_envelope import (
    HEADER_BYTES,
    MAGIC,
    MAX_CHUNK_BYTES,
    FrameEnvelopeError,
    FrameHeader,
    FrameIdentity,
    FrameStream,
    decode_header,
    header_for_rgb,
)

RGB = bytes(range(18))
IDENTITY = FrameIdentity(b"s" * 16, 3, 2, -100, -200, 3, 2, b"o" * 32)


def test_exact_wire_layout_and_roundtrip() -> None:
    """Independent layout proves exact 128 bytes and fixed RGB stride semantics."""
    header = header_for_rgb(IDENTITY, RGB)
    expected = struct.pack(
        ">8sHH16sIIiiIIIQ32s32s",
        b"VCLPRGB\0",
        1,
        128,
        b"s" * 16,
        3,
        2,
        -100,
        -200,
        3,
        2,
        9,
        18,
        hashlib.sha256(RGB).digest(),
        b"o" * 32,
    )
    assert len(expected) == HEADER_BYTES == 128
    assert header.encode() == expected
    assert decode_header(expected, IDENTITY) == header
    assert expected[:8] == MAGIC
    for target, name, value in (
        (header, "image_sha256", b"x" * 32),
        (IDENTITY, "width", 1),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, name, value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", b""),
        ("session_id", bytes(16)),
        ("session_id", bytearray(16)),
        ("request_id", 0),
        ("request_id", 129),
        ("request_id", True),
        ("request_id", float("nan")),
        ("frame_id", float("inf")),
        ("frame_id", 1.5),
        ("frame_id", 0),
        ("frame_id", 129),
        ("x", -(2**31) - 1),
        ("x", 2**31 - 1),
        ("y", 2**31),
        ("y", True),
        ("width", 0),
        ("width", -1),
        ("width", 16_000_001),
        ("height", 0),
        ("height", 32769),
        ("width", 8_000_001),
        ("observation_sha256", bytes(31)),
        ("observation_sha256", bytearray(32)),
    ],
)
def test_invalid_identity(field: str, value: object) -> None:
    """Bounds reject booleans, nonfinite/fractional IDs and arithmetic overflow."""
    with pytest.raises(FrameEnvelopeError, match=r".+"):
        replace(IDENTITY, **{field: value})


@pytest.mark.parametrize(
    ("offset", "format_code", "value"),
    [
        (0, ">8s", b"BADMAGIC"),
        (8, ">H", 2),
        (10, ">H", 127),
        (28, ">I", 0),
        (32, ">I", 129),
        (44, ">I", 0),
        (44, ">I", 2**32 - 1),
        (48, ">I", 32769),
        (52, ">I", 10),
        (56, ">Q", 2**64 - 1),
        (12, ">16s", b"z" * 16),
        (28, ">I", 4),
        (32, ">I", 3),
        (36, ">i", -99),
        (96, ">32s", b"z" * 32),
    ],
)
def test_wire_mutations(offset: int, format_code: str, value: object) -> None:
    """Wire bounds and observation correlation are checked before payload use."""
    data = bytearray(header_for_rgb(IDENTITY, RGB).encode())
    struct.pack_into(format_code, data, offset, cast("int | bytes", value))
    with pytest.raises(FrameEnvelopeError, match=r".+"):
        decode_header(bytes(data), IDENTITY)


@pytest.mark.parametrize("size", [0, 1, 127, 129, 1024])
def test_exact_header_length(size: int) -> None:
    """No truncated header or header plus payload can pass the header parser."""
    with pytest.raises(FrameEnvelopeError, match="immutable"):
        decode_header(bytes(size), IDENTITY)


def test_maximum_geometry_does_not_allocate_pixels() -> None:
    """Maximum declarations fit fixed headers; no payload-sized object is created."""
    identity = replace(IDENTITY, width=16000, height=1000)
    header = FrameHeader(identity, bytes(32))
    assert identity.payload_bytes == 48_000_000
    assert decode_header(header.encode(), identity) == header
    identity = replace(IDENTITY, width=1, height=32768)
    assert (
        decode_header(FrameHeader(identity, bytes(32)).encode(), identity).identity
        == identity
    )


@pytest.mark.parametrize("split", range(len(RGB) + 1))
def test_stream_all_split_boundaries(split: int) -> None:
    """Streaming fragmentation changes neither the digest nor source pixels."""
    header = header_for_rgb(IDENTITY, RGB)
    stream = FrameStream(header.encode(), IDENTITY)
    for chunk in (RGB[:split], RGB[split:]):
        if chunk:
            stream.feed(chunk)
    assert stream.header == header
    assert stream.finish() == header
    with pytest.raises(FrameEnvelopeError, match="terminal"):
        stream.feed(b"x")
    with pytest.raises(FrameEnvelopeError, match="terminal"):
        stream.finish()


@pytest.mark.parametrize(
    "chunk", [b"", RGB + b"x", bytearray(RGB), bytes(MAX_CHUNK_BYTES + 1)]
)
def test_invalid_chunks_latch_failure(chunk: object) -> None:
    """Excess chunks are rejected without hashing and cannot resume afterward."""
    stream = FrameStream(header_for_rgb(IDENTITY, RGB).encode(), IDENTITY)
    with pytest.raises(FrameEnvelopeError, match="chunk"):
        stream.feed(cast("bytes", chunk))
    with pytest.raises(FrameEnvelopeError, match="terminal"):
        stream.feed(RGB)
    with pytest.raises(FrameEnvelopeError, match="terminal"):
        stream.finish()


@pytest.mark.parametrize("payload", [RGB[:-1], bytes(len(RGB))])
def test_payload_truncation_or_corruption(payload: bytes) -> None:
    """A complete header cannot attest incomplete or changed image data."""
    stream = FrameStream(header_for_rgb(IDENTITY, RGB).encode(), IDENTITY)
    stream.feed(payload)
    with pytest.raises(FrameEnvelopeError, match=r"Truncated|digest mismatch"):
        stream.finish()
    with pytest.raises(FrameEnvelopeError, match="terminal"):
        stream.finish()


def test_trailer_before_eof_fails() -> None:
    """Reader must forward unexpected data instead of stopping at declared length."""
    stream = FrameStream(header_for_rgb(IDENTITY, RGB).encode(), IDENTITY)
    stream.feed(RGB)
    with pytest.raises(FrameEnvelopeError, match="trailing"):
        stream.feed(b"trailer")


def test_invalid_python_contracts() -> None:
    """Metadata and buffers are strict immutable values, not coercible objects."""
    invalid = cast("FrameIdentity", object())
    for build in (
        lambda: FrameHeader(invalid, bytes(32)),
        lambda: FrameHeader(IDENTITY, bytes(31)),
        lambda: header_for_rgb(invalid, RGB),
        lambda: header_for_rgb(IDENTITY, cast("bytes", cast("object", bytearray(RGB)))),
        lambda: header_for_rgb(IDENTITY, RGB[:-1]),
        lambda: decode_header(bytes(128), invalid),
        lambda: decode_header(cast("bytes", cast("object", bytearray(128))), IDENTITY),
    ):
        with pytest.raises(FrameEnvelopeError, match=r".+"):
            build()
