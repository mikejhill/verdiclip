"""Pure one-frame credit accounting and cancellation ordering, without transport I/O.

Callers establish header/identity pairing before constructing these machines and
retain responsibility for the original deadline, actual EOF and cleanup. A grant
reservation is irrevocably issued before returning, even if a later write fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Event, RLock
from time import monotonic_ns
from typing import Never, cast

CREDIT_SCHEMA = 1
CHUNK_BYTES = 65_536
MAX_FRAME_BYTES = 1_572_864
MAX_CHUNKS = 24
MAX_CREDIT_RECORD_BYTES = 512
MAX_TRANSFER_MESSAGES = 64
_MAX_REQUEST_ID = 128


class CreditError(ValueError):
    """Invalid credit metadata or permanently failed transfer."""


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CreditError("Invalid bounded credit integer")
    return value


@dataclass(frozen=True, slots=True)
class CreditToken:
    """Exact immutable correlation and fixed chunk boundaries."""

    epoch: str
    request_id: int
    frame_id: int
    chunk_index: int
    offset: int
    count: int

    def __post_init__(self) -> None:
        """Reject alternate types, sessions or arbitrary slices."""
        if (
            type(self.epoch) is not str
            or re.fullmatch("[0-9a-f]{32}", self.epoch) is None
            or self.epoch == "0" * 32
        ):
            raise CreditError("Invalid credit epoch")
        _integer(self.request_id, 1, _MAX_REQUEST_ID)
        _integer(self.frame_id, 1, 1)
        _integer(self.chunk_index, 0, MAX_CHUNKS - 1)
        _integer(self.offset, 0, MAX_FRAME_BYTES - 1)
        _integer(self.count, 1, CHUNK_BYTES)
        if self.offset != self.chunk_index * CHUNK_BYTES:
            raise CreditError("Invalid fixed chunk offset")

    def payload(self) -> dict[str, object]:
        """Return the exact payload; request correlation belongs to the envelope."""
        return {
            "credit_schema": CREDIT_SCHEMA,
            "epoch": self.epoch,
            "frame_id": self.frame_id,
            "chunk_index": self.chunk_index,
            "offset": self.offset,
            "count": self.count,
        }

    @classmethod
    def from_payload(cls, request_id: int, payload: object) -> CreditToken:
        """Parse an already duplicate-key-checked control payload."""
        names = {"credit_schema", "epoch", "frame_id", "chunk_index", "offset", "count"}
        if type(payload) is not dict or set(payload) != names:
            raise CreditError("Invalid credit payload fields")
        fields = cast("dict[str, object]", payload)
        _integer(fields["credit_schema"], CREDIT_SCHEMA, CREDIT_SCHEMA)
        epoch = fields["epoch"]
        if type(epoch) is not str:
            raise CreditError("Invalid credit epoch type")
        return cls(
            epoch,
            request_id,
            _integer(fields["frame_id"], 1, 1),
            _integer(fields["chunk_index"], 0, MAX_CHUNKS - 1),
            _integer(fields["offset"], 0, MAX_FRAME_BYTES - 1),
            _integer(fields["count"], 1, CHUNK_BYTES),
        )

    @classmethod
    def for_chunk(
        cls, epoch: str, request_id: int, total_bytes: int, chunk_index: int
    ) -> CreditToken:
        """Derive the only allowed token for this bounded frame and index."""
        _integer(total_bytes, 1, MAX_FRAME_BYTES)
        _integer(chunk_index, 0, (total_bytes - 1) // CHUNK_BYTES)
        offset = chunk_index * CHUNK_BYTES
        return cls(
            epoch,
            request_id,
            1,
            chunk_index,
            offset,
            min(CHUNK_BYTES, total_bytes - offset),
        )


@dataclass(frozen=True, slots=True)
class CreditEvent:
    """Bounded ordered metadata, never a pixel buffer."""

    sequence: int
    time_ns: int
    kind: str
    token: CreditToken | None


@dataclass(frozen=True, slots=True)
class CreditSnapshot:
    """Immutable worker-side state at a cancellation or cleanup boundary."""

    pending: CreditToken | None
    granted_bytes: int
    accepted_bytes: int
    last_grant_sequence: int
    cancel_sequence: int
    cancel_time_ns: int
    observed_sequence: int
    observed_time_ns: int
    failed: bool
    events: tuple[CreditEvent, ...]


@dataclass(frozen=True, slots=True)
class CreditCancellationWitness:
    """Worker snapshots at latch and after cancellation observation/cleanup."""

    latched: CreditSnapshot
    final: CreditSnapshot

    def validate_held(self, token: CreditToken) -> None:
        """Reject incomplete or inconsistent no-grant cancellation evidence."""
        before, after = self.latched, self.final
        if (
            before.pending != token
            or after.pending != token
            or before.granted_bytes != token.offset
            or before.accepted_bytes != token.offset
            or after.granted_bytes != token.offset
            or after.accepted_bytes != token.offset
            or not before.failed
            or not after.failed
            or before.cancel_sequence <= 0
            or before.observed_sequence != 0
            or after.cancel_sequence != before.cancel_sequence
            or after.cancel_time_ns != before.cancel_time_ns
            or after.observed_sequence <= before.cancel_sequence
            or after.observed_time_ns < before.cancel_time_ns
            or after.last_grant_sequence != before.last_grant_sequence
            or after.events[: len(before.events)] != before.events
        ):
            raise CreditError("Incomplete no-grant cancellation witness")
        expected_wait = expected_grant = 0
        last_time = 0
        last_grant = 0
        kinds: list[str] = []
        for sequence, event in enumerate(after.events, 1):
            if event.sequence != sequence or event.time_ns < last_time:
                raise CreditError("Invalid witness event order")
            last_time = event.time_ns
            kinds.append(event.kind)
            if event.kind in {"wait", "grant"}:
                index = expected_wait if event.kind == "wait" else expected_grant
                expected = CreditToken.for_chunk(
                    token.epoch, token.request_id, token.offset + token.count, index
                )
                if event.token != expected or event.sequence >= before.cancel_sequence:
                    raise CreditError("Invalid witness chunk history")
                if event.kind == "wait":
                    if expected_wait != expected_grant:
                        raise CreditError("Invalid witness wait order")
                    expected_wait += 1
                else:
                    if expected_wait != expected_grant + 1 or event.token == token:
                        raise CreditError("Held token was granted")
                    expected_grant += 1
                    last_grant = sequence
            else:
                self._terminal_event(event)
        if (
            expected_wait != token.chunk_index + 1
            or expected_grant != token.chunk_index
            or last_grant != after.last_grant_sequence
            or kinds[-2:] != ["cancel", "observed"]
            or len(kinds) != 2 * token.chunk_index + 3
            or len(before.events) != before.cancel_sequence
            or after.events[-2].sequence != before.cancel_sequence
            or after.events[-2].time_ns != before.cancel_time_ns
            or after.events[-1].sequence != after.observed_sequence
            or after.events[-1].time_ns != after.observed_time_ns
        ):
            raise CreditError("Invalid witness terminal history")

    @staticmethod
    def _terminal_event(event: CreditEvent) -> None:
        if event.token is not None or event.kind not in {"cancel", "observed"}:
            raise CreditError("Unexpected witness event")


class CreditGate:
    """Shared cancellation boundary available before frame initialization."""

    def __init__(self, cancelled: Event) -> None:
        """Own one small lock shared with the frame credit state."""
        self.lock = RLock()
        self.cancelled = cancelled
        self._parent: ParentCredit | None = None

    def cancel(self) -> None:
        """Latch first, then capture registered frame state under the same lock."""
        with self.lock:
            self.cancelled.set()
            if self._parent is not None:
                self._parent._cancel_locked()


class ParentCredit:
    """One active body window plus at most one pending next wait.

    All mutations and snapshots share one short lock. It is never held over I/O,
    copying, hashing, user callbacks or external synchronization.
    """

    def __init__(
        self,
        epoch: str,
        request_id: int,
        total_bytes: int,
        gate: CreditGate | None = None,
    ) -> None:
        """Initialize only after the caller verifies the paired frame header."""
        CreditToken.for_chunk(epoch, request_id, total_bytes, 0)
        self._epoch = epoch
        self._request_id = request_id
        self._total = total_bytes
        self._gate = gate if gate is not None else CreditGate(Event())
        self._lock = self._gate.lock
        self._pending: CreditToken | None = None
        self._next = 0
        self._granted = 0
        self._accepted = 0
        self._failed = False
        self._finished = False
        self._events: list[CreditEvent] = []
        self._last_grant = 0
        self._cancel_sequence = 0
        self._cancel_time = 0
        self._observed_sequence = 0
        self._observed_time = 0
        self._latched: CreditSnapshot | None = None
        with self._lock:
            if self._gate._parent is not None:
                raise CreditError("Credit gate cannot be reused")
            self._gate._parent = self
            if self._gate.cancelled.is_set():
                self._cancel_locked()

    def _fail(self, reason: str) -> Never:
        self._failed = True
        raise CreditError(reason)

    def _live(self) -> None:
        if self._failed or self._finished or self._gate.cancelled.is_set():
            self._fail("Credit transfer is terminal")

    def _event(self, kind: str, token: CreditToken | None = None) -> CreditEvent:
        event = CreditEvent(len(self._events) + 1, monotonic_ns(), kind, token)
        self._events.append(event)
        return event

    def receive_wait(self, token: CreditToken) -> None:
        """Accept exactly the next wait, including while prior bytes drain."""
        with self._lock:
            self._live()
            try:
                expected = CreditToken.for_chunk(
                    self._epoch, self._request_id, self._total, self._next
                )
            except CreditError:
                self._fail("Surplus frame wait")
            if (
                type(token) is not CreditToken
                or token != expected
                or self._pending is not None
            ):
                self._fail("Unexpected frame wait")
            self._pending = token
            self._event("wait", token)

    @property
    def grantable(self) -> CreditToken | None:
        """Expose a token only when no previously granted bytes remain."""
        with self._lock:
            if (
                self._failed
                or self._gate.cancelled.is_set()
                or self._accepted != self._granted
            ):
                return None
            return self._pending

    def reserve_grant(self, token: CreditToken) -> None:
        """Commit an issued grant atomically against cancellation, before I/O."""
        with self._lock:
            self._live()
            if (
                type(token) is not CreditToken
                or self._pending != token
                or self._accepted != self._granted
            ):
                self._fail("Invalid or premature credit reservation")
            self._pending = None
            self._next += 1
            self._granted += token.count
            self._last_grant = self._event("grant", token).sequence

    def accept_body(self, count: int) -> None:
        """Account bytes only after the caller accepted them into its bounded sink."""
        with self._lock:
            self._live()
            if (
                type(count) is not int
                or not 0 < count <= self._granted - self._accepted
            ):
                self._fail("Body exceeds issued credit")
            self._accepted += count

    def _snapshot(self) -> CreditSnapshot:
        return CreditSnapshot(
            self._pending,
            self._granted,
            self._accepted,
            self._last_grant,
            self._cancel_sequence,
            self._cancel_time,
            self._observed_sequence,
            self._observed_time,
            self._failed,
            tuple(self._events),
        )

    def snapshot(self) -> CreditSnapshot:
        """Capture immutable counters and bounded event history."""
        with self._lock:
            return self._snapshot()

    def _cancel_locked(self) -> CreditSnapshot:
        if self._latched is None:
            self._failed = True
            event = self._event("cancel")
            self._cancel_sequence, self._cancel_time = event.sequence, event.time_ns
            self._latched = self._snapshot()
        return self._latched

    def cancel(self) -> CreditSnapshot:
        """Latch cancellation atomically before any later grant reservation."""
        with self._lock:
            self._gate.cancel()
            return self._cancel_locked()

    def finish(self) -> None:
        """Check exact body completion; actual EOF is still the caller's duty."""
        with self._lock:
            self._live()
            if self._accepted != self._total or self._pending is not None:
                self._fail("Incomplete credited frame")
            self._finished = True

    def observe_cancel(self) -> CreditSnapshot:
        """Record worker observation once, without weakening the original latch."""
        with self._lock:
            if self._latched is None:
                self._fail("Cancellation not latched")
            if not self._observed_sequence:
                event = self._event("observed")
                self._observed_sequence, self._observed_time = (
                    event.sequence,
                    event.time_ns,
                )
            return self._snapshot()

    def witness(self) -> CreditCancellationWitness:
        """Return terminal evidence; caller must invoke only after transport cleanup."""
        with self._lock:
            if self._latched is None or not self._observed_sequence:
                raise CreditError("Incomplete cancellation evidence")
            return CreditCancellationWitness(self._latched, self._snapshot())


class PeerCredit:
    """Synchronous peer state: announce, consume one exact credit, then write."""

    def __init__(self, epoch: str, request_id: int, total_bytes: int) -> None:
        """Initialize one fixed frame without allocating its bytes."""
        self._state = ParentCredit(epoch, request_id, total_bytes)
        self._epoch = epoch
        self._request_id = request_id
        self._total = total_bytes
        self._index = 0

    def announce_wait(self) -> CreditToken:
        """Enter WAITING_CREDIT before the caller sends its wait announcement."""
        state = self._state.snapshot()
        if state.accepted_bytes != state.granted_bytes:
            self._state.cancel()
            raise CreditError("Cannot announce before completing body chunk")
        try:
            token = CreditToken.for_chunk(
                self._epoch, self._request_id, self._total, self._index
            )
        except CreditError:
            self._state.cancel()
            raise
        self._state.receive_wait(token)
        return token

    def consume_credit(self, token: CreditToken) -> None:
        """Consume exactly the announced token once before any body write."""
        self._state.reserve_grant(token)
        self._index += 1

    def wrote_body(self, count: int) -> None:
        """Account actual completed native writes, allowing fragmented writes."""
        self._state.accept_body(count)

    @property
    def complete(self) -> bool:
        """Report byte completion only, never EOF or successful capture."""
        state = self._state.snapshot()
        return not state.failed and state.accepted_bytes == self._total
