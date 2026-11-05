"""Document-owned job tickets; workers return data, never window callbacks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import uuid4

from PySide6.QtGui import QImage

from verdiclip.document.commands import Command
from verdiclip.document.document import DocumentState
from verdiclip.document.history import History


class JobKind(StrEnum):
    """Lifetime policy, rather than a particular OCR/export implementation."""

    ANALYSIS = "analysis"
    OUTPUT = "output"
    EDIT = "edit"


class JobOutcome(StrEnum):
    """An explicit completion receipt for callers on the model's owning thread."""

    CURRENT = "current"
    STALE = "stale"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNKNOWN = "unknown"
    DEFERRED = "deferred"
    COMMITTED_AFTER_CANCEL = "committed_after_cancel"


@dataclass(frozen=True, slots=True)
class JobTicket:
    """Retain an immutable content snapshot and a document/lifetime identity."""

    identifier: str
    document_id: str
    generation: int
    state_id: int
    kind: JobKind
    snapshot: DocumentState


@dataclass(slots=True)
class _Pending:
    ticket: JobTicket
    cancel_requested: bool = False


class DocumentJobs:
    """Validate results on the document thread, independent of host changes.

    This class starts no threads, performs no input or file I/O, and adds no
    OCR/export feature. It is the lifecycle contract for asynchronous consumers.
    """

    def __init__(self, document_id: str, history: History) -> None:
        self._document_id = document_id
        self._history = history
        self._generation = 0
        self._pending: dict[str, _Pending] = {}
        self._closing = False
        self._disposed = False

    @property
    def blocks_close(self) -> bool:
        """An output stays live until completion or acknowledged cancellation."""
        return any(
            pending.ticket.kind is JobKind.OUTPUT for pending in self._pending.values()
        )

    @property
    def pending(self) -> tuple[JobTicket, ...]:
        """Expose receipts without exposing a mutable registry."""
        return tuple(pending.ticket for pending in self._pending.values())

    def freeze(self) -> None:
        """Prevent new work while close prompts collect decisions."""
        self._closing = True

    def unfreeze(self) -> None:
        """Allow work again after a cancelled or vetoed close plan."""
        self._closing = False

    def begin(self, kind: JobKind) -> JobTicket:
        """Snapshot content without sharing a mutable QImage wrapper with a worker."""
        if self._disposed or self._closing:
            raise RuntimeError("Document is closing or has been disposed")
        state = self._history.document.state
        snapshot = replace(state, image=QImage(state.image))
        ticket = JobTicket(
            uuid4().hex,
            self._document_id,
            self._generation,
            self._history.state_id,
            kind,
            snapshot,
        )
        self._pending[ticket.identifier] = _Pending(ticket)
        return ticket

    def _entry(self, ticket: JobTicket) -> _Pending | None:
        pending = self._pending.get(ticket.identifier)
        if (
            pending is not None
            and pending.ticket is ticket
            and ticket.document_id == self._document_id
            and ticket.generation == self._generation
        ):
            return pending
        return None

    def request_cancel(self, ticket: JobTicket) -> bool:
        """Request cancellation without pretending a worker has already stopped."""
        entry = self._entry(ticket)
        if entry is None:
            return False
        entry.cancel_requested = True
        return True

    def acknowledge_cancel(self, ticket: JobTicket) -> bool:
        """Release a lease only after the worker acknowledges cancellation."""
        entry = self._entry(ticket)
        if entry is None or not entry.cancel_requested:
            return False
        self._pending.pop(ticket.identifier)
        return True

    def finish(self, ticket: JobTicket, *, success: bool = True) -> JobOutcome:
        """Settle once; old-state output cannot clear newer document changes."""
        entry = self._entry(ticket)
        if entry is None:
            return JobOutcome.UNKNOWN
        self._pending.pop(ticket.identifier)
        if entry.cancel_requested:
            if success and ticket.kind is JobKind.OUTPUT:
                self._history.mark_delivered(expected_state_id=ticket.state_id)
                return JobOutcome.COMMITTED_AFTER_CANCEL
            return JobOutcome.CANCELLED
        if not success:
            return JobOutcome.FAILED
        if ticket.state_id != self._history.state_id:
            return JobOutcome.STALE
        if ticket.kind is JobKind.OUTPUT:
            self._history.mark_delivered(expected_state_id=ticket.state_id)
        return JobOutcome.CURRENT

    def apply(self, ticket: JobTicket, command: Command) -> JobOutcome:
        """Apply one undoable edit only to the originating, still-current state."""
        if ticket.kind is not JobKind.EDIT:
            raise ValueError("Only an edit job can apply a document command")
        if self._entry(ticket) is None:
            return JobOutcome.UNKNOWN
        if self._closing:
            return JobOutcome.DEFERRED
        outcome = self.finish(ticket)
        if outcome is JobOutcome.CURRENT:
            self._history.execute(command)
        return outcome

    def close(self) -> None:
        """Invalidate analysis/edit results after approved close; never lose output."""
        if self._disposed:
            return
        if self.blocks_close:
            raise RuntimeError("An output job is still running")
        self._disposed = True
        self._generation += 1
        self._pending.clear()
