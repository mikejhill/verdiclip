"""Private bounded frame collection and one final immutable sequence handoff.

No partial result escapes this collector. Pixel storage and hashing belong to the
worker; vault locks protect metadata only, separately from live Qt/native owners.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Never

from verdiclip.capture.frame_envelope import FrameIdentity
from verdiclip.capture.observe_protocol import MAX_HEIGHT, MAX_WIDTH
from verdiclip.capture.retained_frame import (
    MAX_FRAME_BYTES,
    DeadlineStamp,
    FrameState,
    VerifiedCandidate,
    VerifiedFrame,
)
from verdiclip.platform.sequence_protocol import (
    MAX_FRAME_PROOF_BYTES,
    SequenceFrameProof,
)

MAX_FRAMES = 8
MAX_SOURCE_BYTES = MAX_FRAME_BYTES * MAX_FRAMES
MAX_MESSAGE_BYTES = 16_384
_MAX_PHASE_LENGTH = 32
_DIGEST_BYTES = 32


def _reject(reason: str) -> Never:
    raise ValueError(reason)


def _epoch(value: str) -> None:
    if (
        type(value) is not str
        or re.fullmatch(r"[0-9a-f]{32}", value) is None
        or value == "0" * 32
    ):
        _reject("Invalid sequence epoch")


def _immutable(value: bytes, limit: int) -> None:
    if type(value) is not bytes or not 0 < len(value) <= limit:
        _reject("Expected bounded immutable metadata")


@dataclass(frozen=True, slots=True)
class CollectedFrame:
    """One original immutable source plus its canonical accepted proof."""

    candidate: VerifiedCandidate
    proof: SequenceFrameProof

    def __post_init__(self) -> None:
        """Bind immutable source bytes to the exact canonical transport evidence."""
        if (
            type(self.candidate) is not VerifiedCandidate
            or type(self.proof) is not SequenceFrameProof
        ):
            _reject("Invalid collected source or proof")
        value = self.proof.value
        if (
            self.proof.observation.frame_identity() != self.candidate.header.identity
            or self.proof.observation.canonical != self.candidate.observation
            or value["image_sha256"] != self.candidate.header.image_sha256.hex()
            or value["header_sha256"]
            != hashlib.sha256(self.candidate.header.encode()).hexdigest()
        ):
            _reject("Collected source and proof disagree")


def _same_source(one: CollectedFrame, two: CollectedFrame) -> bool:
    """Keep the first read-only source's full validated provider state fixed.

    Compare the full target (including runtime ID/framework), full guard and
    entire scroll state. Only invocation epoch/request/frame correlation,
    settled quiet duration and outer proof acquisition/deadline stamps are
    excluded here; their independent validators still apply. Settling sample
    count and observation schema remain fixed. This is not a scrolling profile.
    """
    first, second = one.candidate.header.identity, two.candidate.header.identity
    left = one.proof.value["observation"]["observation"]
    right = two.proof.value["observation"]["observation"]
    return (
        (first.x, first.y, first.width, first.height)
        == (second.x, second.y, second.width, second.height)
        and all(
            left[key] == right[key]
            for key in ("observation_schema", "target", "guard", "scroll")
        )
        and bool(left["settled"]["samples"] == right["settled"]["samples"])
    )


@dataclass(frozen=True, slots=True)
class SequenceCandidate:
    """Worker-private evidence; not permission to publish provisional pixels."""

    epoch: str
    frames: tuple[CollectedFrame, ...]
    chain: bytes

    def __post_init__(self) -> None:
        """Bound and correlate source identities without copying pixel data."""
        _epoch(self.epoch)
        if type(self.frames) is not tuple or not 1 <= len(self.frames) <= MAX_FRAMES:
            _reject("Invalid sequence frame count")
        if type(self.chain) is not bytes or len(self.chain) != _DIGEST_BYTES:
            _reject("Invalid immutable sequence chain")
        previous = 0
        chain = bytes(32)
        total = 0
        for index, frame in enumerate(self.frames, 1):
            if type(frame) is not CollectedFrame:
                _reject("Invalid collected source")
            identity = frame.candidate.header.identity
            if (
                identity.frame_id != index
                or identity.session_id != bytes.fromhex(self.epoch)
                or identity.request_id <= previous
                or frame.proof.observation.frame_identity() != identity
            ):
                _reject("Invalid sequence source ordering")
            if index > 1 and not _same_source(self.frames[0], frame):
                _reject("Read-only sequence source state changed")
            previous = identity.request_id
            total += identity.payload_bytes
            chain = hashlib.sha256(chain + frame.proof.digest).digest()
        if total > MAX_SOURCE_BYTES or self.chain != chain:
            _reject("Invalid sequence byte bound or proof chain")


@dataclass(frozen=True, slots=True)
class VerifiedSequence:
    """Consumer-owned immutable result after the single publication gate."""

    epoch: str
    frames: tuple[VerifiedFrame, ...]
    proofs: tuple[bytes, ...]
    chain: bytes
    receipt_sha256: bytes

    def __post_init__(self) -> None:
        """Require immutable bounded fields without repeating pixel hashing."""
        _epoch(self.epoch)
        if (
            type(self.frames) is not tuple
            or not 1 <= len(self.frames) <= MAX_FRAMES
            or any(type(frame) is not VerifiedFrame for frame in self.frames)
            or type(self.proofs) is not tuple
            or len(self.proofs) != len(self.frames)
            or any(
                type(proof) is not bytes or not 0 < len(proof) <= MAX_FRAME_PROOF_BYTES
                for proof in self.proofs
            )
            or type(self.chain) is not bytes
            or len(self.chain) != _DIGEST_BYTES
            or type(self.receipt_sha256) is not bytes
            or len(self.receipt_sha256) != _DIGEST_BYTES
        ):
            _reject("Invalid immutable sequence capsule")


class SequenceCollector:
    """One worker-thread owner, at most eight complete immutable sources."""

    def __init__(self, epoch: str, check: Callable[[], None]) -> None:
        _epoch(epoch)
        self.epoch = epoch
        self._check = check
        self._owner = threading.get_ident()
        self._frames: list[CollectedFrame] = []
        self._reserved: FrameIdentity | None = None
        self._committed = 0
        self._terminal = False
        self._failed = threading.Event()
        self._chain = bytes(32)
        self._bytes = 0

    @property
    def retained_bytes(self) -> int:
        """Count owned immutable RGB only, not aliases or interpreter overhead."""
        return self._bytes

    @property
    def count(self) -> int:
        """Count timely committed frames, not partial or merely received data."""
        return self._committed

    @property
    def chain(self) -> bytes:
        """Return bounded metadata only, never source pixels."""
        return self._chain

    def _admit(self) -> None:
        if threading.get_ident() != self._owner:
            self._failed.set()
        if self._failed.is_set() or self._terminal:
            _reject("Sequence collector unavailable or wrong owner")
        self._check()

    def discard(self) -> None:
        """Drop private references on the worker, without an erasure claim."""
        if threading.get_ident() != self._owner:
            self._failed.set()
            _reject("Sequence collector wrong owner")
        self._frames.clear()
        self._reserved = None
        self._bytes = 0
        self._terminal = True

    def reserve(self, identity: FrameIdentity) -> None:
        """Check aggregate capacity and order before constructing a frame sink."""
        try:
            self._admit()
            if (
                type(identity) is not FrameIdentity
                or self._reserved is not None
                or self._committed != len(self._frames)
                or len(self._frames) >= MAX_FRAMES
                or identity.frame_id != len(self._frames) + 1
                or identity.session_id != bytes.fromhex(self.epoch)
                or identity.payload_bytes > MAX_FRAME_BYTES
                or self._bytes + identity.payload_bytes > MAX_SOURCE_BYTES
                or identity.width > MAX_WIDTH
                or identity.height > MAX_HEIGHT
            ):
                _reject("Invalid sequence reservation or uncommitted frame")
            if self._frames:
                previous = self._frames[-1].candidate.header.identity
                if identity.request_id <= previous.request_id or (
                    identity.width,
                    identity.height,
                    identity.x,
                    identity.y,
                ) != (
                    previous.width,
                    previous.height,
                    previous.x,
                    previous.y,
                ):
                    _reject("Sequence geometry or request order changed")
            self._reserved = identity
            self._admit()
        except BaseException:
            self.discard()
            raise

    def accept(self, candidate: VerifiedCandidate, proof: SequenceFrameProof) -> None:
        """Transfer one fully verified frame before its transport acceptance."""
        try:
            self._admit()
            if (
                type(candidate) is not VerifiedCandidate
                or type(proof) is not SequenceFrameProof
                or candidate.header.identity != self._reserved
                or proof.observation.frame_identity() != self._reserved
                or candidate.observation != proof.observation.canonical
                or proof.value["image_sha256"] != candidate.header.image_sha256.hex()
                or proof.value["header_sha256"]
                != hashlib.sha256(candidate.header.encode()).hexdigest()
                or len(self._frames) != self._committed
            ):
                _reject("Frame and proof do not match reservation")
            collected = CollectedFrame(candidate, proof)
            if self._frames and not _same_source(self._frames[0], collected):
                _reject("Read-only sequence source state changed")
            self._frames.append(collected)
            self._bytes += len(candidate.rgb)
            self._admit()
        except BaseException:
            self.discard()
            raise

    def commit(self, proof_digest: bytes) -> None:
        """Admit the exact proof only after its timely acceptance write returns."""
        try:
            self._admit()
            if (
                self._reserved is None
                or len(self._frames) != self._committed + 1
                or proof_digest != self._frames[-1].proof.digest
            ):
                _reject("Missing or mismatched committed frame")
            self._chain = hashlib.sha256(self._chain + proof_digest).digest()
            self._committed += 1
            self._reserved = None
            self._admit()
        except BaseException:
            self.discard()
            raise

    def finish(self) -> SequenceCandidate:
        """Move sources once, after every acceptance and before final sealing."""
        try:
            self._admit()
            if (
                self._reserved is not None
                or not self._frames
                or self._committed != len(self._frames)
            ):
                _reject("Sequence has no completely committed frames")
            result = SequenceCandidate(self.epoch, tuple(self._frames), self._chain)
            self._admit()
            self.discard()
        except BaseException:
            self.discard()
            raise
        else:
            return result


class SequenceVault:
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
        self._identity: str | None = None
        self._frame: VerifiedSequence | None = None
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

    def _drop_locked(self) -> VerifiedSequence | None:
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

    def begin(self, identity: str) -> None:
        """Reserve one correlated identity without allocating pixel storage."""
        try:
            _epoch(identity)
            with self._transition():
                self._admit_locked()
                if self._state != FrameState.EMPTY:
                    _reject("Invalid or duplicate frame reservation")
                self._identity = identity
                self._state = FrameState.BUILDING
        except BaseException:
            self.invalidate()
            raise

    def seal(self, candidate: SequenceCandidate, receipt: bytes) -> None:
        """Construct the capsule on the worker, then seal under retirement gates."""
        try:
            if type(candidate) is not SequenceCandidate:
                _reject("Expected immutable verified candidate")
            _immutable(receipt, MAX_MESSAGE_BYTES)
            digest = hashlib.sha256(receipt).digest()
            frame = VerifiedSequence(
                candidate.epoch,
                tuple(
                    VerifiedFrame(
                        x.candidate.rgb,
                        x.candidate.header,
                        x.candidate.observation,
                        x.proof.digest,
                    )
                    for x in candidate.frames
                ),
                tuple(x.proof.canonical for x in candidate.frames),
                candidate.chain,
                digest,
            )
            with self._transition():
                self._admit_locked()
                if (
                    self._state != FrameState.BUILDING
                    or self._stamp is None
                    or self._stamp.phase != "retiring"
                    or frame.epoch != self._identity
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

    def take(self, generation: int, receipt: bytes) -> VerifiedSequence:
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
                    or self._identity is None
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
