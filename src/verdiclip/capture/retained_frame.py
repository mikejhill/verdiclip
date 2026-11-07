"""Worker-only bounded pixel sink and metadata-only cross-thread publication vault.

The transport must pair metadata/header before constructing a sink, validate each
chunk before feeding it, and supply the header returned by actual-EOF validation
to ``freeze``. Neither a byte count nor these pure primitives authenticates EOF.
The vault never allocates, copies, hashes, or clears pixel buffers under its lock.
Its bound is channel-local: one N-byte mutable buffer plus its N-byte freeze copy,
not process RSS, transport buffers, or consumer-owned aliases after take.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Never

from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity
from verdiclip.capture.observe_protocol import (
    MAX_HEIGHT,
    MAX_OBSERVATION_BYTES,
    MAX_READ_BYTES,
    MAX_WIDTH,
)
from verdiclip.platform.helper_protocol import MAX_MESSAGE_BYTES

MAX_FRAME_BYTES = MAX_WIDTH * MAX_HEIGHT * 3
MAX_SINK_BYTES = MAX_FRAME_BYTES * 2
_MAX_PHASE_LENGTH = 32
_DIGEST_BYTES = 32


class RetainedFrameError(ValueError):
    """A permanently unavailable frame or invalid publication transition."""


def _reject(reason: str) -> Never:
    raise RetainedFrameError(reason)


def _identity(identity: FrameIdentity) -> None:
    if (
        type(identity) is not FrameIdentity
        or identity.width > MAX_WIDTH
        or identity.height > MAX_HEIGHT
        or identity.payload_bytes > MAX_FRAME_BYTES
    ):
        _reject("Frame exceeds the one-observe allocation bound")


def _immutable(data: bytes, limit: int) -> None:
    if type(data) is not bytes or not 0 < len(data) <= limit:
        _reject("Expected bounded immutable metadata bytes")


def _allocate(length: int) -> bytearray:
    return bytearray(length)


def _copy(buffer: bytearray, offset: int, chunk: bytes) -> None:
    buffer[offset : offset + len(chunk)] = chunk


def _freeze(buffer: bytearray) -> bytes:
    return bytes(buffer)


@dataclass(frozen=True, slots=True)
class VerifiedCandidate:
    """Worker-private immutable payload qualified by the original observe gates."""

    rgb: bytes = field(repr=False)
    header: FrameHeader
    observation: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Check shape and digests outside any vault lock."""
        if type(self.header) is not FrameHeader:
            _reject("Expected immutable frame header")
        _identity(self.header.identity)
        _immutable(self.observation, MAX_OBSERVATION_BYTES)
        if (
            type(self.rgb) is not bytes
            or len(self.rgb) != self.header.identity.payload_bytes
        ):
            _reject("Retained pixels do not match the exact frame size")
        if hashlib.sha256(self.rgb).digest() != self.header.image_sha256:
            _reject("Retained pixels do not match the verified digest")
        if (
            hashlib.sha256(self.observation).digest()
            != self.header.identity.observation_sha256
        ):
            _reject("Observation does not match the verified frame")


@dataclass(frozen=True, slots=True)
class VerifiedFrame:
    """Single-use immutable consumer capsule, never serialized into Qt signals."""

    rgb: bytes = field(repr=False)
    header: FrameHeader
    observation: bytes = field(repr=False)
    receipt_sha256: bytes

    def __post_init__(self) -> None:
        """Keep every capsule field immutable without repeating pixel hashing."""
        if type(self.header) is not FrameHeader:
            _reject("Expected immutable frame header")
        _identity(self.header.identity)
        _immutable(self.observation, MAX_OBSERVATION_BYTES)
        if (
            type(self.rgb) is not bytes
            or len(self.rgb) != self.header.identity.payload_bytes
            or type(self.receipt_sha256) is not bytes
            or len(self.receipt_sha256) != _DIGEST_BYTES
        ):
            _reject("Invalid immutable frame capsule")

    @property
    def identity(self) -> FrameIdentity:
        """Expose the protocol request identity, not the facade caller identifier."""
        return self.header.identity


class FrameSink:
    """Preallocate exactly N worker-owned bytes, with at most 2N during freezing."""

    def __init__(self, identity: FrameIdentity, check: Callable[[], None]) -> None:
        _identity(identity)
        self._identity = identity
        self._check = check
        self._buffer: bytearray | None = None
        self._received = 0
        self._failure: str | None = None
        try:
            self._check()
            self._buffer = _allocate(identity.payload_bytes)
            self._check()
        except BaseException:
            self.discard()
            raise

    @property
    def allocated_bytes(self) -> int:
        """Report only this sink's currently owned mutable pixel payload."""
        return 0 if self._buffer is None else len(self._buffer)

    def _require_building(self) -> bytearray:
        if self._buffer is None:
            _reject(self._failure or "Frame sink is terminal")
        return self._buffer

    def _failed(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = str(error) or type(error).__name__
        self.discard()

    def feed(self, chunk: bytes) -> None:
        """Copy one already-pairing-validated bounded chunk into its exact offset."""
        try:
            buffer = self._require_building()
            self._check()
            if (
                type(chunk) is not bytes
                or not 0 < len(chunk) <= MAX_READ_BYTES
                or self._received + len(chunk) > self._identity.payload_bytes
            ):
                _reject("Invalid, oversized, or trailing frame chunk")
            _copy(buffer, self._received, chunk)
            self._received += len(chunk)
            self._check()
        except BaseException as error:
            buffer = None
            self._failed(error)
            raise

    def freeze(self, header: FrameHeader, canonical: bytes) -> VerifiedCandidate:
        """Freeze only after the caller verifies actual EOF, length, and digest."""
        try:
            buffer = self._require_building()
            self._check()
            if type(header) is not FrameHeader or header.identity != self._identity:
                _reject("Verified header does not match the sink")
            _immutable(canonical, MAX_OBSERVATION_BYTES)
            if self._received != self._identity.payload_bytes:
                _reject("Cannot freeze a truncated frame")
            rgb = _freeze(buffer)
            self.discard()
            del buffer
            self._check()
            candidate = VerifiedCandidate(rgb, header, canonical)
            self._check()
        except BaseException as error:
            buffer = rgb = candidate = None
            del buffer
            self._failed(error)
            raise
        else:
            return candidate

    def discard(self) -> None:
        """Drop worker-owned mutable storage without a secure-erasure claim."""
        self._buffer = None


class FrameState(StrEnum):
    """One-way payload ownership, independent of native worker retention."""

    EMPTY = "EMPTY"
    BUILDING = "BUILDING"
    SEALED = "SEALED"
    TAKEN = "TAKEN"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class DeadlineStamp:
    """Worker-authoritative absolute phase deadline, without renewed allowances."""

    phase: str
    revision: int
    deadline: float


class FrameVault:
    """Short locked metadata transitions; all pixel work belongs to the worker."""

    def __init__(self, generation: int, cancelled: threading.Event) -> None:
        if type(generation) is not int or generation <= 0:
            _reject("Invalid frame generation")
        if type(cancelled) is not threading.Event:
            _reject("Expected shared cancellation event")
        self._generation = generation
        self._cancelled = cancelled
        self._lock = threading.Lock()
        self._state = FrameState.EMPTY
        self._stamp: DeadlineStamp | None = None
        self._identity: FrameIdentity | None = None
        self._frame: VerifiedFrame | None = None
        self._receipt: bytes | None = None
        self._authorized = False
        self._last_time: float | None = None

    @property
    def state(self) -> FrameState:
        """Read only ownership metadata; this does not transfer or expose pixels."""
        with self._lock:
            return self._state

    def stamp(self) -> DeadlineStamp | None:
        """Read the latest immutable worker stamp, or missing startup evidence."""
        with self._lock:
            return self._stamp

    def publish_stamp(self, phase: str, deadline: float) -> DeadlineStamp:
        """Publish an existing deadline unchanged; revision advances only on change."""
        if (
            type(phase) is not str
            or not 0 < len(phase) <= _MAX_PHASE_LENGTH
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline <= 0
        ):
            _reject("Invalid authoritative deadline stamp")
        with self._lock:
            previous = self._stamp
            if previous is not None and (previous.phase, previous.deadline) == (
                phase,
                deadline,
            ):
                return previous
            revision = 1 if previous is None else previous.revision + 1
            self._stamp = DeadlineStamp(phase, revision, float(deadline))
            return self._stamp

    def _admit_locked(self) -> None:
        now = time.monotonic()
        if (
            self._cancelled.is_set()
            or self._stamp is None
            or type(now) not in (int, float)
            or not math.isfinite(now)
            or now >= self._stamp.deadline
            or (self._last_time is not None and now < self._last_time)
            or self._state in (FrameState.INVALID, FrameState.TAKEN)
        ):
            _reject("Frame cancelled, expired, or unavailable")
        self._last_time = now

    def _drop_locked(self) -> VerifiedFrame | None:
        discarded, self._frame = self._frame, None
        self._receipt = None
        self._authorized = False
        if self._state != FrameState.TAKEN:
            self._state = FrameState.INVALID
        return discarded

    @contextmanager
    def _transition(self) -> Generator[None]:
        """Latch a failed admission before another thread can take the frame."""
        discarded = None
        try:
            with self._lock:
                try:
                    yield
                except BaseException:
                    discarded = self._drop_locked()
                    raise
        finally:
            del discarded

    def invalidate(self) -> None:
        """Invalidate any unclaimed frame; release the moved reference after unlock."""
        with self._lock:
            discarded = self._drop_locked()
        del discarded

    def expire(self, revision: int, now: float) -> bool:
        """Ignore obsolete timers; expire only the current absolute deadline."""
        if (
            type(revision) is not int
            or type(now) not in (int, float)
            or not math.isfinite(now)
        ):
            _reject("Invalid expiry evidence")
        with self._lock:
            stamp = self._stamp
            if stamp is None or stamp.revision != revision or now < stamp.deadline:
                return False
            discarded = self._drop_locked()
        del discarded
        return True

    def begin(self, identity: FrameIdentity) -> None:
        """Reserve one correlated identity without allocating pixel storage."""
        try:
            _identity(identity)
            with self._transition():
                self._admit_locked()
                if self._state != FrameState.EMPTY:
                    _reject("Invalid or duplicate frame reservation")
                self._identity = identity
                self._state = FrameState.BUILDING
        except BaseException:
            self.invalidate()
            raise

    def seal(self, candidate: VerifiedCandidate, receipt: bytes) -> None:
        """Construct the capsule on the worker, then seal under retirement gates."""
        try:
            if type(candidate) is not VerifiedCandidate:
                _reject("Expected immutable verified candidate")
            _immutable(receipt, MAX_MESSAGE_BYTES)
            frame = VerifiedFrame(
                candidate.rgb,
                candidate.header,
                candidate.observation,
                hashlib.sha256(receipt).digest(),
            )
            with self._transition():
                self._admit_locked()
                if (
                    self._state != FrameState.BUILDING
                    or self._stamp is None
                    or self._stamp.phase != "retiring"
                    or frame.identity != self._identity
                ):
                    _reject("Frame does not match retiring reservation")
                self._frame = frame
                self._receipt = receipt
                self._state = FrameState.SEALED
            # Sealing itself must not erase cancellation or deadline expiry.
            with self._transition():
                self._admit_locked()
        except BaseException:
            self.invalidate()
            raise

    def authorize(self, receipt: bytes) -> None:
        """Accept exact metadata only after the lifetime's receipt/finished gates."""
        try:
            _immutable(receipt, MAX_MESSAGE_BYTES)
            with self._transition():
                self._admit_locked()
                if (
                    self._state != FrameState.SEALED
                    or self._authorized
                    or receipt != self._receipt
                ):
                    _reject("Missing or mismatched frame authorization")
                self._authorized = True
        except BaseException:
            self.invalidate()
            raise

    def check_authorized(self, receipt: bytes) -> None:
        """Recheck queued facade delivery without consuming or renewing authority."""
        try:
            _immutable(receipt, MAX_MESSAGE_BYTES)
            with self._transition():
                self._admit_locked()
                if (
                    self._state != FrameState.SEALED
                    or not self._authorized
                    or receipt != self._receipt
                    or self._frame is None
                ):
                    _reject("Missing or mismatched frame delivery authorization")
        except BaseException:
            self.invalidate()
            raise

    def take(self, generation: int, request_id: int, receipt: bytes) -> VerifiedFrame:
        """Move the capsule once; subsequent cancellation cannot revoke the result."""
        try:
            _immutable(receipt, MAX_MESSAGE_BYTES)
            with self._transition():
                self._admit_locked()
                if (
                    self._state != FrameState.SEALED
                    or not self._authorized
                    or type(generation) is not int
                    or generation != self._generation
                    or type(request_id) is not int
                    or self._identity is None
                    or request_id != self._identity.request_id
                    or receipt != self._receipt
                    or self._frame is None
                ):
                    _reject("Missing or mismatched frame publication")
                frame, self._frame = self._frame, None
                self._receipt = None
                self._authorized = False
                self._state = FrameState.TAKEN
                return frame
        except BaseException:
            self.invalidate()
            raise
