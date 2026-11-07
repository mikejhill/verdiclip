"""Pure bounded RGB24 image envelope, unused by application/provider paths.

Wire layout is big-endian, exactly 128 bytes: magic[8], version:u16,
header_bytes:u16, session[16], request:u32, frame:u32, x:i32, y:i32,
width:u32, height:u32, stride:u32, payload_bytes:u64, image_sha256[32],
observation_sha256[32]. Version 1 means tightly packed top-down RGB24 only.

A reader obtains exactly HEADER_BYTES first, validates against its outstanding
observation, then streams at most MAX_CHUNK_BYTES at a time. No payload is
buffered here. EOF must immediately follow the declared payload; finish only
then. Caller owns canonical observation hashing, trusted session correlation,
file/handle lifetime, deadlines and aggregate memory/disk admission. Digests
prove byte consistency, not native state or authorization.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from verdiclip.capture.scroll_stitch import (
    MAX_FRAMES,
    MAX_OUTPUT_ROWS,
    MAX_VIEWPORT_PIXELS,
)
from verdiclip.platform.helper_protocol import MAX_REQUESTS

MAGIC = b"VCLPRGB\0"
VERSION = 1
HEADER_BYTES = 128
MAX_CHUNK_BYTES = 1_048_576
_CHANNELS = 3
_SESSION_BYTES = 16
_DIGEST_BYTES = 32
_MIN_COORDINATE = -(2**31)
_MAX_COORDINATE = 2**31 - 1
_LAYOUT = struct.Struct(">8sHH16sIIiiIIIQ32s32s")


class FrameEnvelopeError(ValueError):
    """Malformed, uncorrelated or incomplete image evidence."""


def _integer(value: object, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise FrameEnvelopeError("Invalid bounded image integer")


def _bytes(value: object, length: int) -> None:
    if type(value) is not bytes or len(value) != length:
        raise FrameEnvelopeError("Invalid immutable image identifier or digest")


@dataclass(frozen=True, slots=True)
class FrameIdentity:
    """Expected session, operation and physical viewport from a guarded observation."""

    session_id: bytes
    request_id: int
    frame_id: int
    x: int
    y: int
    width: int
    height: int
    observation_sha256: bytes

    def __post_init__(self) -> None:
        """Check all geometry and identifiers before any payload allocation/read."""
        _bytes(self.session_id, _SESSION_BYTES)
        if self.session_id == bytes(_SESSION_BYTES):
            raise FrameEnvelopeError("Session identifier must be nonzero")
        _integer(self.request_id, 1, MAX_REQUESTS)
        _integer(self.frame_id, 1, MAX_FRAMES)
        _integer(self.x, _MIN_COORDINATE, _MAX_COORDINATE)
        _integer(self.y, _MIN_COORDINATE, _MAX_COORDINATE)
        _integer(self.width, 1, MAX_VIEWPORT_PIXELS)
        _integer(self.height, 1, MAX_OUTPUT_ROWS)
        if self.width * self.height > MAX_VIEWPORT_PIXELS:
            raise FrameEnvelopeError("Viewport pixel bound exceeded")
        if (
            self.x + self.width > _MAX_COORDINATE
            or self.y + self.height > _MAX_COORDINATE
        ):
            raise FrameEnvelopeError("Physical viewport edges exceed coordinate range")
        _bytes(self.observation_sha256, _DIGEST_BYTES)

    @property
    def stride(self) -> int:
        """Return tightly packed RGB24 row length, never an external allocation hint."""
        return self.width * _CHANNELS

    @property
    def payload_bytes(self) -> int:
        """Return the bounded exact pixel payload length."""
        return self.stride * self.height


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """Immutable checked metadata; constructing it does not verify image bytes."""

    identity: FrameIdentity
    image_sha256: bytes

    def __post_init__(self) -> None:
        """Reject mutable or unvalidated metadata."""
        if type(self.identity) is not FrameIdentity:
            raise FrameEnvelopeError("Expected validated frame identity")
        _bytes(self.image_sha256, _DIGEST_BYTES)

    def encode(self) -> bytes:
        """Encode a fixed-size header without copying or allocating its payload."""
        identity = self.identity
        return _LAYOUT.pack(
            MAGIC,
            VERSION,
            HEADER_BYTES,
            identity.session_id,
            identity.request_id,
            identity.frame_id,
            identity.x,
            identity.y,
            identity.width,
            identity.height,
            identity.stride,
            identity.payload_bytes,
            self.image_sha256,
            identity.observation_sha256,
        )


def header_for_rgb(identity: FrameIdentity, rgb: bytes) -> FrameHeader:
    """Hash an existing immutable buffer only after its dimensions/size are checked."""
    if type(identity) is not FrameIdentity:
        raise FrameEnvelopeError("Expected validated frame identity")
    if type(rgb) is not bytes or len(rgb) != identity.payload_bytes:
        raise FrameEnvelopeError("Image bytes do not match viewport dimensions")
    return FrameHeader(identity, hashlib.sha256(rgb).digest())


def decode_header(data: bytes, expected: FrameIdentity) -> FrameHeader:
    """Validate exactly one header and correlation before consuming pixel data."""
    if type(expected) is not FrameIdentity:
        raise FrameEnvelopeError("Expected validated frame identity")
    _bytes(data, HEADER_BYTES)
    (
        magic,
        version,
        size,
        session,
        request,
        frame,
        x,
        y,
        width,
        height,
        stride,
        length,
        digest,
        observation,
    ) = _LAYOUT.unpack(data)
    if (magic, version, size) != (MAGIC, VERSION, HEADER_BYTES):
        raise FrameEnvelopeError("Unsupported image header format")
    identity = FrameIdentity(session, request, frame, x, y, width, height, observation)
    if stride != identity.stride or length != identity.payload_bytes:
        raise FrameEnvelopeError("Image stride or length does not match dimensions")
    if identity != expected:
        raise FrameEnvelopeError("Image does not match outstanding observation")
    return FrameHeader(identity, digest)


class FrameStream:
    """Validate bounded chunks without retaining pixels; failure is terminal."""

    def __init__(self, header: bytes, expected: FrameIdentity) -> None:
        """Validate the header before initializing the streaming image digest."""
        self._header = decode_header(header, expected)
        self._digest = hashlib.sha256()
        self._received = 0
        self._terminal = False

    @property
    def header(self) -> FrameHeader:
        """Expose immutable checked metadata without allowing reassignment."""
        return self._header

    def feed(self, chunk: bytes) -> None:
        """Consume one bounded nonempty chunk; reject excess bytes before hashing."""
        if self._terminal:
            raise FrameEnvelopeError("Image stream is terminal")
        if (
            type(chunk) is not bytes
            or not 0 < len(chunk) <= MAX_CHUNK_BYTES
            or self._received + len(chunk) > self._header.identity.payload_bytes
        ):
            self._terminal = True
            raise FrameEnvelopeError("Invalid, oversized or trailing image chunk")
        self._digest.update(chunk)
        self._received += len(chunk)

    def finish(self) -> FrameHeader:
        """At verified EOF, attest exact length/digest without materializing pixels."""
        if self._terminal:
            raise FrameEnvelopeError("Image stream is terminal")
        self._terminal = True
        if self._received != self._header.identity.payload_bytes:
            raise FrameEnvelopeError("Truncated image payload")
        if self._digest.digest() != self._header.image_sha256:
            raise FrameEnvelopeError("Image payload digest mismatch")
        return self._header
