"""Retain every selected page until close detachment callbacks are validated."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdiclip.editor import window as window_types
    from verdiclip.editor import workspace as workspace_types
    from verdiclip.editor.page import DocumentPage


class CloseTransaction:
    """Stage all hosts before any disposal; rollback keeps actual late edits."""

    def __init__(
        self,
        owner: workspace_types.Workspace,
        entries: tuple[
            tuple[window_types.EditorWindow, tuple[window_types.CloseApproval, ...]],
            ...,
        ],
        can_commit: Callable[[], bool],
    ) -> None:
        self.owner = owner
        self.entries = entries
        self.can_commit = can_commit
        self.documents = owner.documents
        self.windows = owner.windows
        self.original = tuple(
            (
                host,
                host.pages,
                host.pages[host.tab_bar.currentIndex()] if host.pages else None,
            )
            for host, _approvals in entries
        )

    def run(self) -> bool:
        """Detach, flush and validate all work before signal-free disposal."""
        try:
            self._stage_and_validate()
        except (RuntimeError, ValueError, MemoryError) as error:
            self._restore()
            for host, _pages, _active in self.original:
                host.statusBar().showMessage(
                    f"Closing cancelled; your documents are retained. {error}"
                )
            return False
        for _host, approvals in self.entries:
            for approval in approvals:
                self.owner.dispose_document(approval.page)
        return True

    def _stage_and_validate(self) -> None:
        self._stage()
        if not self._current():
            raise RuntimeError("Work changed while closing")

    def _stage(self) -> None:
        for host, approvals in self.entries:
            for approval in approvals:
                if not approval.current or approval.page not in host.pages:
                    raise RuntimeError("A close approval is no longer current")
                host.take_page(approval.page)
        # Detachment/focus signals may have opened or changed an inline editor.
        for _host, approvals in self.entries:
            for approval in approvals:
                approval.page.suspend()

    def _current(self) -> bool:
        return (
            self.owner.documents == self.documents
            and self.owner.windows == self.windows
            and self.can_commit()
            and all(
                approval.current
                and self.owner.host_of(approval.page.record.identifier) is None
                for _host, approvals in self.entries
                for approval in approvals
            )
        )

    def _restore(self) -> None:
        failed = False
        for host, pages, active in self.original:
            for index, page in enumerate(pages):
                try:
                    self._restore_page(host, page, index)
                except (RuntimeError, ValueError, MemoryError):
                    failed = True
            if active is not None and active in host.pages:
                host.activate_page(active)
        if failed:
            # Registry/parking remain strong owners even if allocation still fails.
            self.owner.recover_retained()

    def _restore_page(
        self, host: window_types.EditorWindow, page: DocumentPage, index: int
    ) -> None:
        current = self.owner.host_of(page.record.identifier)
        if current is not None and current is not host:
            current.take_page(page)
        if page not in host.pages:
            host.attach_page(page, index)
        elif host.pages.index(page) != index:
            host.tab_bar.moveTab(host.pages.index(page), index)
