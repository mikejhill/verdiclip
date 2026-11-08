"""Fixed moving EDIT pixel ownership, separate from the old observe profile.

Reservations count controlled payload slots, not RSS, native hidden allocations or
arbitrary escaped consumer aliases. Storage owners retain leases through publication.
All copying, freezing, hashing and pixel disposal occur outside publication locks.
The caller, not these byte primitives, authenticates fresh-pipe EOF and native state.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import traceback
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Never

from verdiclip.capture.frame_envelope import FrameHeader, FrameIdentity

MAX_FRAMES = 7
MAX_WIDTH = 493
MAX_HEIGHT = 413
MAX_FRAME_BYTES = MAX_WIDTH * MAX_HEIGHT * 3
MAX_OUTPUT_BYTES = 493 * 621 * 3
MAX_OBSERVATION_BYTES = 32_768
MAX_FRAME_PROOF_BYTES = 49_152
MAX_MESSAGE_BYTES = 65_536
MAX_RECEIPT_BYTES = 16_384
MAX_READ_BYTES = 65_536
MAX_SINK_BYTES = MAX_FRAME_BYTES * 2
PARENT_PIXEL_RESERVATION = 12 * 1024 * 1024
PEER_PIXEL_RESERVATION = 32 * 1024 * 1024
_ROOT_BYTES = 533 * 661 * 3
_BGRA_BYTES = 533 * 661 * 4
_MAX_PHASE_LENGTH = 32
_DIGEST_BYTES = 32

PARENT_PIXEL_SLOTS = MappingProxyType(
    {
        **{f"source_{i}": MAX_FRAME_BYTES for i in range(1, 8)},
        "current_mutable": MAX_FRAME_BYTES,
        "current_frozen": MAX_FRAME_BYTES,
        **{f"transport_{i}": MAX_READ_BYTES for i in range(1, 4)},
        "assembly_chunks": MAX_OUTPUT_BYTES,
        "assembly_final": MAX_OUTPUT_BYTES,
    }
)
PEER_PIXEL_SLOTS = MappingProxyType(
    {
        **{f"source_root_{i}": _ROOT_BYTES for i in range(1, 8)},
        **{f"source_client_{i}": MAX_FRAME_BYTES for i in range(1, 8)},
        **{f"sample_root_{i}": _ROOT_BYTES for i in range(1, 4)},
        **{f"sample_bgra_{i}": _BGRA_BYTES for i in range(1, 3)},
        **{f"crop_temp_{i}": MAX_FRAME_BYTES for i in range(1, 5)},
        **{f"transport_{i}": MAX_READ_BYTES for i in range(1, 4)},
        "assembly_chunks": MAX_OUTPUT_BYTES,
        "assembly_final": MAX_OUTPUT_BYTES,
    }
)
PARENT_KNOWN_PIXEL_BYTES = sum(PARENT_PIXEL_SLOTS.values())
PEER_KNOWN_PIXEL_BYTES = sum(PEER_PIXEL_SLOTS.values())


class RetainedFrameError(ValueError):
    """A permanently unavailable frame or invalid ownership transition."""


def _reject(reason: str) -> Never:
    raise RetainedFrameError(reason)


def discard_traceback(error: BaseException) -> None:
    """Drop unwound pixel-operation locals before releasing owned reservations.

    Failure reporting retains bounded reason strings, not image-owning exception
    stacks. Clear only this error and its explicit chain, not unrelated frames.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        current.__traceback__ = None
        following = current.__cause__ or current.__context__
        current.__cause__ = current.__context__ = None
        current = following


class PixelReservation:
    """One reserved slot; keep this owner alive for every controlled payload alias."""

    def __init__(self, ledger: PixelSlots, name: str, size: int) -> None:
        self._ledger = ledger
        self._name = name
        self._size = size
        self._payload: bytes | bytearray | tuple[bytes, ...] | None = None
        self._token = object()
        self._released = False

    @property
    def payload(self) -> bytes | bytearray | tuple[bytes, ...] | None:
        """Return the owned storage, without copying it."""
        return self._payload

    @property
    def ledger(self) -> PixelSlots:
        """Expose the exact allocation domain for cross-pool rejection."""
        return self._ledger

    @property
    def name(self) -> str:
        """Expose bounded ownership metadata only."""
        return self._name

    def bind(self, payload: bytes | bytearray | tuple[bytes, ...]) -> None:
        """Attach one newly allocated payload after its slot was reserved."""
        if type(payload) is tuple and all(type(x) is bytes for x in payload):
            length = sum(len(x) for x in payload)
        elif type(payload) in (bytes, bytearray):
            length = len(payload)
        else:
            _reject("Invalid pixel slot payload")
        with self._ledger._lock:
            if (
                self._released
                or self._payload is not None
                or length > self._size
                or self._ledger._tokens.get(self._name) is not self._token
            ):
                _reject("Pixel slot is unavailable or payload exceeds reservation")
            self._payload = payload

    def move(self, name: str) -> None:
        """Transfer an existing owner to a final slot without copying pixels."""
        self._ledger._move(self, name)

    def release(self) -> None:
        """Drop owned pixels first; callers must first drop other controlled aliases."""
        if not self._released:
            self._released = True
            self._payload = None
            self._ledger._release(self._token)

    def __enter__(self) -> PixelReservation:
        """Retain a scoped temporary allocation reservation."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Dispose temporary storage even when allocation or comparison failed."""
        self.release()

    def __del__(self) -> None:
        """Release after the last owning candidate/capsule alias disappears."""
        if not getattr(self, "_released", True):
            self.release()


class PixelSlots:
    """Fixed named payload inventory with metadata-only synchronization."""

    def __init__(self, slots: Mapping[str, int], capacity: int) -> None:
        self._limits = dict(slots)
        self._capacity = capacity
        self._active: dict[str, int] = {}
        self._tokens: dict[str, object] = {}
        self._lock = threading.Lock()
        self._peak = 0
        self._invalid = False

    @property
    def reserved_bytes(self) -> int:
        """Count live reserved pixel capacity, not interpreter allocation overhead."""
        with self._lock:
            return sum(self._active.values())

    @property
    def peak_bytes(self) -> int:
        """Report the largest simultaneously reserved controlled payload capacity."""
        with self._lock:
            return self._peak

    @property
    def active_slots(self) -> tuple[str, ...]:
        """Return immutable names without exposing any retained pixel data."""
        with self._lock:
            return tuple(sorted(self._active))

    def invalidate(self) -> None:
        """Forbid new allocations without releasing any still-owned aliases."""
        with self._lock:
            self._invalid = True

    def reserve(self, name: str, *, size: int | None = None) -> PixelReservation:
        """Reserve before allocation; forbid duplicate slots and unlisted headroom."""
        with self._lock:
            limit = self._limits.get(name, 0)
            count = limit if size is None else size
            if (
                self._invalid
                or name in self._active
                or type(count) is not int
                or not 0 < count <= limit
                or sum(self._active.values()) + count > self._capacity
            ):
                _reject("Pixel slot unavailable or allocation bound exceeded")
            owner = PixelReservation(self, name, count)
            self._active[name] = count
            self._tokens[name] = owner._token
            self._peak = max(self._peak, sum(self._active.values()))
        return owner

    def _release(self, token: object) -> None:
        with self._lock:
            name = next(
                (name for name, current in self._tokens.items() if current is token),
                None,
            )
            if name is not None:
                self._tokens.pop(name)
                self._active.pop(name)

    def _move(self, owner: PixelReservation, name: str) -> None:
        with self._lock:
            if (
                self._invalid
                or owner._released
                or owner._ledger is not self
                or name in self._active
                or owner._name not in self._active
                or self._tokens.get(owner._name) is not owner._token
                or owner._size > self._limits.get(name, 0)
            ):
                _reject("Pixel slot cannot transfer to requested owner")
            self._active[name] = self._active.pop(owner._name)
            self._tokens[name] = self._tokens.pop(owner._name)
            owner._name = name


class ParentPixelSlots(PixelSlots):
    """The independent 12 MiB parent reservation with 7,530,969 listed bytes."""

    def __init__(self) -> None:
        super().__init__(PARENT_PIXEL_SLOTS, PARENT_PIXEL_RESERVATION)


class PeerPixelSlots(PixelSlots):
    """The independent 32 MiB peer reservation with 22,140,517 listed bytes."""

    def __init__(self) -> None:
        super().__init__(PEER_PIXEL_SLOTS, PEER_PIXEL_RESERVATION)


def _identity(identity: FrameIdentity) -> None:
    if (
        type(identity) is not FrameIdentity
        or (identity.width, identity.height) != (MAX_WIDTH, MAX_HEIGHT)
        or not 1 <= identity.frame_id <= MAX_FRAMES
        or identity.request_id != 2 * identity.frame_id + 1
    ):
        _reject("Frame exceeds the fixed moving EDIT identity bound")


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
    """Worker-private bytes whose storage owner keeps its reservation alive."""

    storage: PixelReservation = field(repr=False)
    header: FrameHeader
    observation: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Check shape and digests off the GUI, outside any publication lock."""
        if type(self.header) is not FrameHeader:
            _reject("Expected immutable frame header")
        _identity(self.header.identity)
        _immutable(self.observation, MAX_OBSERVATION_BYTES)
        if (
            type(self.storage) is not PixelReservation
            or type(self.rgb) is not bytes
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

    @property
    def rgb(self) -> bytes:
        """Return the immutable raster without creating another storage owner."""
        data = self.storage.payload
        if type(data) is not bytes:
            _reject("Candidate pixel storage is unavailable")
        return data


@dataclass(frozen=True, slots=True)
class VerifiedFrame:
    """Consumer capsule sharing its candidate's storage reservation, never Qt pixels."""

    candidate: VerifiedCandidate = field(repr=False)
    receipt_sha256: bytes

    def __post_init__(self) -> None:
        """Validate metadata without hashing or copying already verified pixels."""
        if (
            type(self.candidate) is not VerifiedCandidate
            or type(self.receipt_sha256) is not bytes
            or len(self.receipt_sha256) != _DIGEST_BYTES
        ):
            _reject("Invalid immutable frame capsule")

    @property
    def rgb(self) -> bytes:
        """Expose immutable consumer-owned pixels."""
        return self.candidate.rgb

    @property
    def header(self) -> FrameHeader:
        """Expose the immutable accepted envelope."""
        return self.candidate.header

    @property
    def observation(self) -> bytes:
        """Expose canonical bounded observation metadata."""
        return self.candidate.observation

    @property
    def identity(self) -> FrameIdentity:
        """Expose the protocol identity, not the public caller identifier."""
        return self.header.identity


class FrameSink:
    """One worker owns N mutable bytes and at most one N-byte immutable freeze."""

    def __init__(
        self,
        identity: FrameIdentity,
        check: Callable[[], None],
        *,
        slots: ParentPixelSlots | None = None,
    ) -> None:
        _identity(identity)
        self._identity = identity
        self._check = check
        if slots is not None and type(slots) is not ParentPixelSlots:
            _reject("Expected the fixed parent pixel slot domain")
        self.slots = ParentPixelSlots() if slots is None else slots
        self._owner = threading.get_ident()
        self._storage: PixelReservation | None = None
        self._received = 0
        self._failure: str | None = None
        try:
            self._admit()
            self._storage = self.slots.reserve("current_mutable")
            self._admit()
            self._storage.bind(_allocate(identity.payload_bytes))
            self._admit()
        except BaseException as error:
            discard_traceback(error)
            self.discard()
            self.slots.invalidate()
            raise

    @property
    def allocated_bytes(self) -> int:
        """Report this sink's owned mutable pixel bytes only."""
        return 0 if self._storage is None else self._identity.payload_bytes

    def _admit(self) -> None:
        if threading.get_ident() != self._owner:
            _reject("Frame sink wrong owner")
        self._check()

    def _require_building(self) -> bytearray:
        if self._storage is None or type(self._storage.payload) is not bytearray:
            _reject(self._failure or "Frame sink is terminal")
        return self._storage.payload

    def _failed(self, error: BaseException) -> None:
        if self._failure is None:
            self._failure = (str(error) or type(error).__name__)[:4096]
        self.discard()
        self.slots.invalidate()

    def feed(self, chunk: bytes) -> None:
        """Copy one already-admitted credit chunk; never accumulate a fragment list."""
        try:
            buffer = self._require_building()
            self._admit()
            if (
                type(chunk) is not bytes
                or not 0 < len(chunk) <= MAX_READ_BYTES
                or self._received + len(chunk) > self._identity.payload_bytes
            ):
                _reject("Invalid, oversized, or trailing frame chunk")
            _copy(buffer, self._received, chunk)
            self._received += len(chunk)
            self._admit()
        except BaseException as error:
            buffer = None
            discard_traceback(error)
            self._failed(error)
            raise

    def freeze(self, header: FrameHeader, canonical: bytes) -> VerifiedCandidate:
        """Freeze only after caller-verified EOF, not merely a matching byte count."""
        frozen = None
        try:
            buffer = self._require_building()
            self._admit()
            if type(header) is not FrameHeader or header.identity != self._identity:
                _reject("Verified header does not match the sink")
            _immutable(canonical, MAX_OBSERVATION_BYTES)
            if self._received != self._identity.payload_bytes:
                _reject("Cannot freeze a truncated frame")
            frozen = self.slots.reserve("current_frozen")
            self._admit()
            frozen.bind(_freeze(buffer))
            buffer = None
            self.discard()
            self._admit()
            candidate = VerifiedCandidate(frozen, header, canonical)
            self._admit()
        except BaseException as error:
            buffer = candidate = None
            discard_traceback(error)
            if frozen is not None:
                frozen.release()
            self._failed(error)
            raise
        else:
            return candidate

    def discard(self) -> None:
        """Drop only owned mutable bytes; never claim secure erasure."""
        if self._storage is not None:
            self._storage.release()
            self._storage = None


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
            _immutable(receipt, MAX_RECEIPT_BYTES)
            frame = VerifiedFrame(
                candidate,
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
            _immutable(receipt, MAX_RECEIPT_BYTES)
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
            _immutable(receipt, MAX_RECEIPT_BYTES)
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
            _immutable(receipt, MAX_RECEIPT_BYTES)
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
