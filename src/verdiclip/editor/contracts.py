"""Shared host values and owner interface, independent of concrete windows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QPoint, SignalInstance
from PySide6.QtWidgets import QMenu, QWidget

from verdiclip.document.admission import CaptureProvenance
from verdiclip.editor.page import DocumentPage, DocumentRecord


@dataclass(frozen=True, slots=True)
class EditorOptions:
    """How an editor window presents its document."""

    title: str = ""
    source_path: Path | None = None
    confirm_close: bool = True
    provenance: CaptureProvenance | None = None


@dataclass(frozen=True, slots=True)
class CloseApproval:
    """Approve one exact content state, never a mutable active-tab position."""

    page: DocumentPage
    state_id: int

    @property
    def current(self) -> bool:
        """Refuse new edits or output leases created after the decision."""
        record = self.page.record
        return (
            record.session.history.state_id == self.state_id
            and not record.operations
            and not record.jobs.blocks_close
        )


class WorkspaceOwner[HostT](Protocol):
    """Services used by a host/transaction, without importing its concrete owner."""

    @property
    def parking(self) -> QWidget:
        """Retain views through host detachment and rollback."""
        ...

    @property
    def transferring(self) -> bool:
        """Prevent reentrant structural changes while ownership is staged."""
        ...

    @property
    def windows(self) -> tuple[HostT, ...]:
        """Expose registered presentation hosts."""
        ...

    @property
    def documents(self) -> tuple[DocumentRecord, ...]:
        """Retain admitted documents independently of visible hosts."""
        ...

    @property
    def transfer_failed(self) -> SignalInstance:
        """Report recoverable ownership/presentation failures."""
        ...

    def register_window(self, window: HostT) -> None:
        """Register a constructed host without presenting it."""
        ...

    def register_page(self, page: DocumentPage) -> None:
        """Own one retained view and its document."""
        ...

    def forget_window(self, window: HostT) -> None:
        """Remove an approved empty host."""
        ...

    def activated(self, window: HostT) -> None:
        """Record an actual host activation."""
        ...

    def host_of(self, identifier: str) -> HostT | None:
        """Find presentation by stable document identity."""
        ...

    def recover_retained(self) -> bool:
        """Retry retained presentation without recreating documents."""
        ...

    def dispose_document(self, page: DocumentPage) -> None:
        """Dispose only a document with a current close approval."""
        ...

    def close_documents(
        self,
        entries: tuple[tuple[HostT, tuple[CloseApproval, ...]], ...],
        can_commit: Callable[[], bool] | None = None,
    ) -> bool:
        """Stage and validate all selected pages before disposal."""
        ...

    def close_all(self, can_commit: Callable[[], bool] | None = None) -> bool:
        """Apply the same save/cancel policy to all retained work."""
        ...

    def move(
        self,
        source: HostT,
        page: DocumentPage,
        target: HostT | None = None,
        index: int | None = None,
        position: QPoint | None = None,
    ) -> bool:
        """Reorder or transfer a retained page atomically."""
        ...

    def show_tab_menu(
        self, source: HostT, page: DocumentPage, position: object
    ) -> None:
        """Expose structural actions through the keyboard-accessible menu."""
        ...

    def populate_window_menu(self, menu: QMenu, source: HostT) -> None:
        """Populate destinations from current ownership."""
        ...
