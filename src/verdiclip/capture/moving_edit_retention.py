"""Fixed moving EDIT seven-frame ownership and one final immutable handoff.

No partial result escapes this collector. Pixel storage and hashing belong to the
worker; vault locks protect metadata only, separately from live Qt/native owners.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Never, cast

from verdiclip.capture.edit_observation import validate_continuity
from verdiclip.capture.frame_envelope import FrameIdentity
from verdiclip.capture.moving_edit_frame import (
    MAX_FRAME_BYTES,
    MAX_FRAME_PROOF_BYTES,
    MAX_HEIGHT,
    MAX_OUTPUT_BYTES,
    MAX_WIDTH,
    DeadlineStamp,
    FrameState,
    ParentPixelSlots,
    PixelReservation,
    VerifiedCandidate,
    VerifiedFrame,
    discard_traceback,
)
from verdiclip.platform.moving_edit_protocol import MovingFrameProof

MAX_FRAMES = 7
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
    proof: MovingFrameProof

    def __post_init__(self) -> None:
        """Bind immutable source bytes to the exact canonical transport evidence."""
        if (
            type(self.candidate) is not VerifiedCandidate
            or type(self.proof) is not MovingFrameProof
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


def _same_source(one: CollectedFrame, two: CollectedFrame) -> None:
    validate_continuity(one.proof.observation, two.proof.observation)


_PROVENANCE = struct.Struct(">HH")
_OUTPUT_ROWS = 621
_STRIDE = MAX_WIDTH * 3
_SOURCE_RANGES = ((2, 0, 270), (3, 142, 246), (4, 166, 413))


def _expected_provenance() -> tuple[tuple[int, int], ...]:
    return tuple(
        (frame, row)
        for frame, begin, end in _SOURCE_RANGES
        for row in range(begin, end)
    )


@dataclass(frozen=True, slots=True)
class AssembledPixels:
    """Final immutable raster plus exactly621 packed original source-row pairs."""

    storage: PixelReservation
    provenance: bytes

    def __post_init__(self) -> None:
        """Verify bounded storage and every source mapping outside vault locks."""
        if (
            type(self.storage) is not PixelReservation
            or type(self.provenance) is not bytes
            or len(self.provenance) != _OUTPUT_ROWS * _PROVENANCE.size
            or self.row_provenance != _expected_provenance()
            or len(self.rgb) != MAX_OUTPUT_BYTES
        ):
            _reject("Invalid assembled pixel ownership or row provenance")

    @property
    def rgb(self) -> bytes:
        """Read the immutable final raster without allocating a copy."""
        result = self.storage.payload
        if type(result) is not bytes:
            _reject("Assembled pixels unavailable")
        return result

    @property
    def row_provenance(self) -> tuple[tuple[int, int], ...]:
        """Decode the fixed bounded packed provenance into original frame IDs."""
        return cast(
            "tuple[tuple[int, int], ...]",
            tuple(_PROVENANCE.iter_unpack(self.provenance)),
        )

    @property
    def provenance_sha256(self) -> str:
        """Hash canonical pairs, agreeing with the independent peer summary."""
        encoded = json.dumps(self.row_provenance, separators=(",", ":")).encode()
        _immutable(encoded, MAX_MESSAGE_BYTES)
        return hashlib.sha256(encoded).hexdigest()

    @property
    def summary(self) -> dict[str, object]:
        """Return bounded metadata; never serialize a raster or put it in Qt signals."""
        return {
            "width": MAX_WIDTH,
            "height": _OUTPUT_ROWS,
            "byte_count": MAX_OUTPUT_BYTES,
            "sha256": hashlib.sha256(self.rgb).hexdigest(),
            "source_frame_ids": [2, 3, 4],
            "row_provenance_sha256": self.provenance_sha256,
        }


def _slice_rows(frame: VerifiedCandidate, begin: int, end: int) -> bytes:
    return frame.rgb[begin * _STRIDE : end * _STRIDE]


def _join_chunks(chunks: tuple[bytes, ...]) -> bytes:
    return b"".join(chunks)


def _validate_sources(
    frames: tuple[VerifiedCandidate, ...], slots: ParentPixelSlots
) -> None:
    if type(frames) is not tuple or len(frames) != MAX_FRAMES:
        _reject("Exactly seven immutable source frames required")
    for index, frame in enumerate(frames, 1):
        if (
            type(frame) is not VerifiedCandidate
            or frame.header.identity.frame_id != index
            or frame.storage.ledger is not slots
            or frame.storage.name != f"source_{index}"
        ):
            _reject("Invalid source pixel ownership")
    for left, right in ((0, 6), (1, 5), (3, 4)):
        if frames[left].rgb != frames[right].rgb:
            _reject("Repeated, returned or restored full pixels disagree")


def _validate_overlap(
    previous: bytes, current: bytes, expected: int, check: Callable[[], None]
) -> None:
    # Memoryviews allocate no overlapping full-raster copies. The permitted
    # current temporary slots are therefore not needed by this independent pass.
    before, after = memoryview(previous), memoryview(current)
    candidates = []
    for shift in range(1, 207):
        check()
        if before[shift * _STRIDE :] == after[: (MAX_HEIGHT - shift) * _STRIDE]:
            candidates.append(shift)
    if previous == current or candidates != [expected]:
        _reject("Missing, ambiguous or inconsistent exact full-width overlap")


def assemble_sources(
    frames: tuple[VerifiedCandidate, ...],
    slots: ParentPixelSlots,
    check: Callable[[], None],
) -> AssembledPixels:
    """Independently reproduce fixed seams, all columns and every source row."""
    chunks: PixelReservation | None = None
    final: PixelReservation | None = None
    parts: tuple[bytes, ...] = ()
    pieces: list[bytes] = []
    try:
        check()
        _validate_sources(frames, slots)
        for before, after, expected in ((1, 2, 128), (2, 3, 80)):
            _validate_overlap(frames[before].rgb, frames[after].rgb, expected, check)
        check()
        chunks = slots.reserve("assembly_chunks")
        check()
        for frame, begin, end in _SOURCE_RANGES:
            check()
            pieces.append(_slice_rows(frames[frame - 1], begin, end))
            check()
        parts = tuple(pieces)
        pieces.clear()
        chunks.bind(parts)
        check()
        final = slots.reserve("assembly_final")
        check()
        final.bind(_join_chunks(parts))
        parts = ()
        chunks.release()
        check()
        provenance = b"".join(
            _PROVENANCE.pack(*pair) for pair in _expected_provenance()
        )
        result = AssembledPixels(final, provenance)
        check()
    except BaseException as error:
        parts = ()
        pieces.clear()
        discard_traceback(error)
        if final is not None:
            final.release()
        if chunks is not None:
            chunks.release()
        raise
    return result


def _validate_assembly_sources(
    frames: tuple[CollectedFrame, ...], assembly: AssembledPixels
) -> None:
    sources = tuple(frame.candidate for frame in frames)
    slots = sources[0].storage.ledger
    if type(slots) is not ParentPixelSlots or assembly.storage.ledger is not slots:
        _reject("Invalid assembly allocation domain")
    _validate_sources(sources, slots)
    output = memoryview(assembly.rgb)
    for row, (frame_id, source_row) in enumerate(assembly.row_provenance):
        original = memoryview(sources[frame_id - 1].rgb)
        if (
            output[row * _STRIDE : (row + 1) * _STRIDE]
            != original[source_row * _STRIDE : (source_row + 1) * _STRIDE]
        ):
            _reject("Assembly pixels do not match original row provenance")


@dataclass(frozen=True, slots=True)
class SequenceCandidate:
    """Worker-private evidence; not permission to publish provisional pixels."""

    epoch: str
    frames: tuple[CollectedFrame, ...]
    chain: bytes
    assembly: AssembledPixels

    def __post_init__(self) -> None:
        """Bound and correlate source identities without copying pixel data."""
        _epoch(self.epoch)
        if type(self.frames) is not tuple or len(self.frames) != MAX_FRAMES:
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
                or identity.request_id != 2 * index + 1
                or identity.request_id <= previous
                or frame.proof.observation.frame_identity() != identity
            ):
                _reject("Invalid sequence source ordering")
            for prior in self.frames[: index - 1]:
                _same_source(prior, frame)
            previous = identity.request_id
            total += identity.payload_bytes
            chain = hashlib.sha256(chain + frame.proof.digest).digest()
        if (
            total != MAX_SOURCE_BYTES
            or self.chain != chain
            or type(self.assembly) is not AssembledPixels
        ):
            _reject("Invalid sequence byte bound or proof chain")
        _validate_assembly_sources(self.frames, self.assembly)


@dataclass(frozen=True, slots=True)
class VerifiedSequence:
    """Consumer-owned immutable result after the single publication gate."""

    epoch: str
    frames: tuple[VerifiedFrame, ...]
    proofs: tuple[bytes, ...]
    chain: bytes
    receipt_sha256: bytes
    assembly: AssembledPixels

    def __post_init__(self) -> None:
        """Require immutable bounded fields without repeating pixel hashing."""
        _epoch(self.epoch)
        if (
            type(self.frames) is not tuple
            or len(self.frames) != MAX_FRAMES
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
            or type(self.assembly) is not AssembledPixels
        ):
            _reject("Invalid immutable sequence capsule")


class SequenceCollector:
    """One worker-thread owner; exactly seven sources before final assembly."""

    def __init__(self, epoch: str, check: Callable[[], None]) -> None:
        _epoch(epoch)
        self.epoch = epoch
        self.slots = ParentPixelSlots()
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
        self.slots.invalidate()
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
                or identity.request_id != 2 * identity.frame_id + 1
                or identity.session_id != bytes.fromhex(self.epoch)
                or identity.payload_bytes > MAX_FRAME_BYTES
                or self._bytes + identity.payload_bytes > MAX_SOURCE_BYTES
                or identity.width != MAX_WIDTH
                or identity.height != MAX_HEIGHT
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

    def accept(self, candidate: VerifiedCandidate, proof: MovingFrameProof) -> None:
        """Transfer one fully verified frame before its transport acceptance."""
        try:
            self._admit()
            if (
                type(candidate) is not VerifiedCandidate
                or type(proof) is not MovingFrameProof
                or candidate.header.identity != self._reserved
                or proof.observation.frame_identity() != self._reserved
                or candidate.observation != proof.observation.canonical
                or proof.value["image_sha256"] != candidate.header.image_sha256.hex()
                or proof.value["header_sha256"]
                != hashlib.sha256(candidate.header.encode()).hexdigest()
                or len(self._frames) != self._committed
                or candidate.storage.ledger is not self.slots
                or candidate.storage.name != "current_frozen"
            ):
                _reject("Frame and proof do not match reservation")
            collected = CollectedFrame(candidate, proof)
            for previous in self._frames:
                _same_source(previous, collected)
                old_line = previous.proof.observation.value["native_state"][
                    "text_layout"
                ]["first_visible_line"]
                new_line = proof.observation.value["native_state"]["text_layout"][
                    "first_visible_line"
                ]
                if old_line == new_line and previous.candidate.rgb != candidate.rgb:
                    _reject("Repeated, returned or restored frame bytes changed")
            candidate.storage.move(f"source_{len(self._frames) + 1}")
            self._frames.append(collected)
            self._bytes += len(candidate.rgb)
            self._admit()
        except BaseException as error:
            del candidate
            collected = previous = None
            discard_traceback(error)
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
                or len(self._frames) != MAX_FRAMES
                or self._committed != len(self._frames)
            ):
                _reject("Sequence has no completely committed frames")
            frames = tuple(self._frames)
            assembly = assemble_sources(
                tuple(frame.candidate for frame in frames), self.slots, self._admit
            )
            result = SequenceCandidate(self.epoch, frames, self._chain, assembly)
            self._admit()
            self.discard()
        except BaseException as error:
            frames = ()
            assembly = result = None
            discard_traceback(error)
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
                        x.candidate,
                        x.proof.digest,
                    )
                    for x in candidate.frames
                ),
                tuple(x.proof.canonical for x in candidate.frames),
                candidate.chain,
                digest,
                candidate.assembly,
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
