"""Application ownership, atomic tab transfers and document-safe close plans."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from verdiclip.editor.closing import CloseTransaction
from verdiclip.editor.page import DocumentPage, DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.editor.transfer import TabTransfer
from verdiclip.output.delivery import ImageDelivery

if TYPE_CHECKING:
    from verdiclip.editor.window import CloseApproval, EditorOptions, EditorWindow


class Workspace(QObject):
    """Own sessions independently of host widgets and keep transfers reversible."""

    window_added = Signal(object)
    window_removed = Signal(object)
    transfer_failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.parking = QWidget()
        self.parking.setObjectName("document-transfer-parking")
        self._documents: dict[str, DocumentRecord] = {}
        self._pages: dict[str, DocumentPage] = {}
        self._windows: list[EditorWindow] = []
        self._transferring = False
        self._drag: tuple[EditorWindow, DocumentPage] | None = None
        self._drag_generation = 0
        self._pending_presentations: set[EditorWindow] = set()
        self._presentation_timer = QTimer(self)
        self._presentation_timer.setInterval(50)
        self._presentation_timer.timeout.connect(self.present_retained)

    @property
    def windows(self) -> tuple[EditorWindow, ...]:
        """Return live hosts in most-recently-activated order."""
        return tuple(self._windows)

    @property
    def documents(self) -> tuple[DocumentRecord, ...]:
        """Return registered documents, including those in a transfer lease."""
        return tuple(self._documents.values())

    @property
    def transferring(self) -> bool:
        """Prevent reentrant structural changes during a move commit."""
        return self._transferring

    @property
    def has_work(self) -> bool:
        """Document/transfer ownership, rather than window count, controls idle."""
        return bool(self._documents) or self._transferring or self._drag is not None

    def host_of(self, identifier: str) -> EditorWindow | None:
        """Resolve current presentation from document identity, never a tab index."""
        return next(
            (
                window
                for window in self.windows
                if any(page.record.identifier == identifier for page in window.pages)
            ),
            None,
        )

    def refresh_document(self, identifier: str) -> None:
        """Refresh presentation metadata without creating an undoable image edit."""
        page = self._pages.get(identifier)
        if page is not None:
            page.changed.emit()

    def recover_retained(self) -> bool:
        """Retry presentation after an allocation/attachment rollback failure."""
        from verdiclip.editor.window import EditorOptions, EditorWindow  # noqa: PLC0415

        self.present_retained()
        for identifier, page in self._pages.items():
            if self.host_of(identifier) is not None:
                continue
            host = None
            try:
                host = EditorWindow(
                    None, page.record.delivery, EditorOptions(), workspace=self
                )
                host.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                host.attach_page(page, 0)
                host.show()
            except (RuntimeError, ValueError, MemoryError):
                # Keep the registry and parking widget alive for another retry.
                if host is not None and not host.pages:
                    self.forget_window(host)
                    host.deleteLater()
                return False
        return True

    def _existing_capture(self, options: EditorOptions) -> DocumentPage | None:
        if options.provenance is None:
            return None
        capture_id = options.provenance.capture_id
        return next(
            (
                page
                for page in self._pages.values()
                if page.record.provenance is not None
                and page.record.provenance.capture_id == capture_id
            ),
            None,
        )

    def register_document(self, record: DocumentRecord) -> None:
        """Retain a session until an explicitly approved document close."""
        existing = self._documents.get(record.identifier)
        if existing is not None and existing is not record:
            raise ValueError("Document identity already registered")
        self._documents[record.identifier] = record

    def register_page(self, page: DocumentPage) -> None:
        """Retain the view independently of both source and destination hosts."""
        self.register_document(page.record)
        self._pages[page.record.identifier] = page

    def register_window(self, window: EditorWindow) -> None:
        """Wire one host without making a provisional window the active target."""
        self._windows.append(window)
        bar = window.tab_bar
        bar.drag_started.connect(
            lambda identifier: self._start_drag(window, identifier)
        )
        bar.drag_moved.connect(self._preview_drag)
        bar.drag_released.connect(self._finish_drag)
        bar.drag_cancelled.connect(self._clear_drag)
        self.window_added.emit(window)

    def activated(self, window: EditorWindow) -> None:
        """Remember only an actual editor host, never an overlay or popup."""
        if window in self._windows and window.eligible and not self._transferring:
            self._windows.remove(window)
            self._windows.insert(0, window)

    def forget_window(self, window: EditorWindow) -> None:
        """Forget an approved empty host without disposing moved documents."""
        if window in self._windows:
            self._pending_presentations.discard(window)
            if not self._pending_presentations:
                self._presentation_timer.stop()
            self._windows.remove(window)
            self._clear_drag()
            self.window_removed.emit(window)

    def dispose_document(self, page: DocumentPage) -> None:
        """Dispose only work whose close was approved, disconnecting listeners."""
        page.record.jobs.close()
        self._documents.pop(page.record.identifier)
        self._pages.pop(page.record.identifier)
        page.dispose()
        page.deleteLater()

    def open_document(  # noqa: PLR0913 - explicit admission policy
        self,
        session: EditorSession,
        delivery: ImageDelivery,
        options: EditorOptions,
        target: EditorWindow | None = None,
        *,
        new_window: bool = False,
        activate: bool = True,
    ) -> EditorWindow:
        """Admit producer-independent work without replacing any existing tab."""
        from verdiclip.editor.window import EditorWindow  # noqa: PLC0415 - avoid cycle

        should_activate = activate and not self._protected_ui()
        existing = self._existing_capture(options)
        if existing is not None:
            host = self.host_of(existing.record.identifier)
            if host is not None:
                with host.presentation_policy(activate=should_activate):
                    self._present_host(host, activate=should_activate)
                return host
        if target is None or target not in self._windows or not target.eligible:
            target = next((window for window in self._windows if window.eligible), None)
        if new_window or target is None:
            target = EditorWindow(None, delivery, options, workspace=self)
            target.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        record = DocumentRecord(
            session,
            delivery,
            options.title or "Screenshot",
            options.source_path,
            provenance=options.provenance,
        )
        with target.presentation_policy(activate=should_activate):
            if existing is None:
                target.add_document(record, activate=should_activate)
            else:
                target.attach_page(existing, len(target.pages))
                if should_activate:
                    target.activate_page(existing)
            self._present_host(target, activate=should_activate)
        return target

    def _present_host(self, target: EditorWindow, *, activate: bool) -> None:
        # Visible/minimized hosts need no presentation change for background work.
        if not target.isVisible():
            # Reserve before show: failure must remain retryable/recoverable.
            self._pending_presentations.add(target)
        if not activate and not target.isVisible() and self._protected_ui():
            target.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self._pending_presentations.add(target)
            self._presentation_timer.start()
            return
        if activate or not target.isVisible():
            target.setAttribute(
                Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate
            )
            if activate and target.isMinimized():
                target.setWindowState(
                    target.windowState() & ~Qt.WindowState.WindowMinimized
                )
            target.show()
        self._pending_presentations.discard(target)
        if not self._pending_presentations:
            self._presentation_timer.stop()
        if activate:
            target.raise_()
            target.activateWindow()

    @staticmethod
    def _protected_ui() -> bool:
        return (
            QApplication.activePopupWidget() is not None
            or QApplication.activeModalWidget() is not None
        )

    def present_retained(self) -> None:
        """Present deferred captures only after protected Qt UI ends naturally.

        Qt itself dismisses an active QMenu when another top-level is shown,
        even with WA_ShowWithoutActivating. Never trigger that path while the
        popup/modal exists. Ownership remains in the workspace throughout.
        """
        if self._transferring or self._protected_ui():
            return
        for host in tuple(self._pending_presentations):
            if host not in self._windows:
                self._pending_presentations.discard(host)
                continue
            if not host.eligible:
                continue
            if not host.pages:
                self._pending_presentations.discard(host)
                continue
            if host.isVisible():
                self._pending_presentations.discard(host)
                continue
            try:
                with host.presentation_policy(activate=False):
                    host.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                    host.show()
            except (RuntimeError, MemoryError) as error:
                self._presentation_timer.stop()
                self.transfer_failed.emit(
                    f"Editor retained but could not be shown. Retry Window > "
                    f"Recover retained tabs. {error}"
                )
                return
            self._pending_presentations.discard(host)
        if not self._pending_presentations:
            self._presentation_timer.stop()

    def move(
        self,
        source: EditorWindow,
        page: DocumentPage,
        target: EditorWindow | None = None,
        index: int | None = None,
        position: QPoint | None = None,
    ) -> bool:
        """Prepare, attach and publish once, restoring the source on failure."""
        if (
            source not in self._windows
            or self._transferring
            or not source.eligible
            or page not in source.pages
        ):
            return False
        if target is not None and (target not in self._windows or not target.eligible):
            return False
        if target is source:
            old = source.pages.index(page)
            slot = min(
                max(index if index is not None else old, 0), len(source.pages) - 1
            )
            source.tab_bar.moveTab(old, slot)
            return True
        self._transferring = True
        try:
            destination = TabTransfer(
                source, page, target, index=index, position=position
            ).run()
        finally:
            self._transferring = False
        if destination is None:
            return False
        destination.raise_()
        destination.activateWindow()
        destination.canvas.setFocus()
        if not source.pages:
            source.close()
        return True

    def close_documents(
        self,
        entries: tuple[tuple[EditorWindow, tuple[CloseApproval, ...]], ...],
        can_commit: Callable[[], bool] | None = None,
    ) -> bool:
        """Stage every selected page before destroying any accepted document."""
        if self._transferring:
            return False
        self._transferring = True
        try:
            return CloseTransaction(self, entries, can_commit or (lambda: True)).run()
        finally:
            self._transferring = False

    def close_all(self, can_commit: Callable[[], bool] | None = None) -> bool:
        """Approve exact states before disposing any work, retaining reservations."""
        guard = can_commit or (lambda: True)
        self.cancel_drag()
        if self._transferring or not guard() or not self.recover_retained():
            return False
        windows = self.windows
        documents = self.documents
        if any(not window.eligible for window in windows):
            return False
        for window in windows:
            window.begin_close_plan()
        try:
            plans = self._collect_approvals(windows)
            if plans is None:
                return False
            entries = tuple(zip(windows, plans, strict=True))
            if not self._plans_current(entries, documents, guard):
                return False
            if not self.close_documents(entries, can_commit=guard):
                return False
            return (
                all(window.commit_close_plan(()) for window in windows)
                and not self.has_work
            )
        finally:
            for window in windows:
                window.end_close_plan()

    def _plans_current(
        self,
        entries: tuple[tuple[EditorWindow, tuple[CloseApproval, ...]], ...],
        documents: tuple[DocumentRecord, ...],
        guard: Callable[[], bool],
    ) -> bool:
        for window, _plan in entries:
            window.flush_pending_edits()
        return (
            self.documents == documents
            and self.windows == tuple(window for window, _plan in entries)
            and guard()
            and all(window.approvals_current(plan) for window, plan in entries)
        )

    @staticmethod
    def _collect_approvals(
        windows: tuple[EditorWindow, ...],
    ) -> list[tuple[CloseApproval, ...]] | None:
        plans: list[tuple[CloseApproval, ...]] = []
        for window in windows:
            plan = window.close_approvals()
            if plan is None:
                return None
            plans.append(plan)
        return plans

    def cancel_drag(self) -> None:
        """Release tab input without closing any widget or discarding edits."""
        for window in self.windows:
            window.tab_bar.cancel_drag()
        self._clear_drag()

    def _start_drag(self, source: EditorWindow, identifier: str) -> None:
        page = next(
            (page for page in source.pages if page.record.identifier == identifier),
            None,
        )
        if page is None or not source.eligible or self._transferring:
            source.tab_bar.cancel_drag()
            return
        page.suspend()
        self._drag = source, page

    def _target_at(self, position: QPoint) -> EditorWindow | None:
        widget = QApplication.widgetAt(position)
        for window in self.windows:
            bar = window.tab_bar
            if (
                window.eligible
                and window.isVisible()
                and not window.isMinimized()
                and (widget is bar or (widget is not None and bar.isAncestorOf(widget)))
            ):
                return window
        return None

    def _preview_drag(self, position: QPoint) -> None:
        for window in self.windows:
            window.tab_bar.show_insertion()
        target = self._target_at(position)
        if target is not None:
            target.tab_bar.show_insertion(target.tab_bar.insertion_at(position))

    def _finish_drag(self, position: QPoint) -> None:
        drag = self._drag
        self._clear_drag()
        generation = self._drag_generation
        if drag is not None:
            # Qt must finish release dispatch before hit testing or restructuring.
            QTimer.singleShot(
                0, lambda: self._complete_drop(drag, position, generation)
            )

    def _complete_drop(
        self, drag: tuple[EditorWindow, DocumentPage], position: QPoint, generation: int
    ) -> None:
        if generation != self._drag_generation:
            return
        target = self._target_at(position)
        index = target.tab_bar.insertion_at(position) if target is not None else None
        if target is drag[0] and index is not None:
            old = drag[0].pages.index(drag[1])
            index -= int(index > old)
        self.move(*drag, target, index, position)

    def _clear_drag(self) -> None:
        self._drag_generation += 1
        self._drag = None
        for window in self.windows:
            window.tab_bar.show_insertion()

    def show_tab_menu(
        self, source: EditorWindow, page: DocumentPage, position: object
    ) -> None:
        """Keyboard-accessible counterparts for every structural drag action."""
        menu = QMenu(source)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.addAction(
            "Close tab",
            lambda: (
                source.close_tab(source.pages.index(page))
                if page in source.pages
                else None
            ),
        )
        menu.addAction("Move to new window", lambda: self.move(source, page))
        for offset, label in ((-1, "Move tab left"), (1, "Move tab right")):
            menu.addAction(
                label,
                lambda delta=offset: (
                    self.move(source, page, source, source.pages.index(page) + delta)
                    if page in source.pages
                    else None
                ),
            )
        self._add_targets(menu.addMenu("Move to window"), source, page)
        if isinstance(position, QPoint):
            menu.popup(position)

    def _add_targets(
        self, menu: QMenu, source: EditorWindow, page: DocumentPage
    ) -> None:
        for number, window in enumerate(self.windows, 1):
            if window is not source and window.eligible:
                menu.addAction(
                    f"{number}. {window.windowTitle()}",
                    lambda target=window: self.move(source, page, target),
                )

    def populate_window_menu(self, menu: QMenu, source: EditorWindow) -> None:
        """Refresh stable-ID tab and destination actions when opened."""
        for action in menu.actions()[5:]:
            menu.removeAction(action)
            action.deleteLater()
        menu.addAction("Recover retained tabs", self.recover_retained)
        if not source.pages:
            return
        self._add_targets(
            menu.addMenu("Move tab to window"),
            source,
            source.pages[source.tab_bar.currentIndex()],
        )
        menu.addSeparator()
        for number, page in enumerate(source.pages, 1):
            action = menu.addAction(
                f"{number}. {page.record.label}",
                lambda current=page: (
                    source.activate_page(current) if current in source.pages else None
                ),
            )
            action.setCheckable(True)
            action.setChecked(source.pages.index(page) == source.tab_bar.currentIndex())
