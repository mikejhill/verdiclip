"""A retained-page transfer transaction with no document serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication

from verdiclip.editor.page import DocumentPage

if TYPE_CHECKING:
    from verdiclip.editor import window as window_types


class TabTransfer:
    """Hold strong references until attachment succeeds or rollback completes."""

    def __init__(
        self,
        source: window_types.EditorWindow,
        page: DocumentPage,
        target: window_types.EditorWindow | None,
        *,
        index: int | None,
        position: QPoint | None,
    ) -> None:
        self.owner = source.workspace
        self.source = source
        self.page = page
        self.target = target
        self.index = index
        self.position = position
        self.created = target is None
        self.original_index = source.pages.index(page)
        self.source_active = source.pages[source.tab_bar.currentIndex()]
        self.target_active = (
            target.pages[target.tab_bar.currentIndex()]
            if target and target.pages
            else None
        )

    def run(self) -> window_types.EditorWindow | None:
        """Publish a complete destination or restore the original attachment."""
        try:
            target = self._prepare()
            self.source.take_page(self.page)
            target.attach_page(
                self.page,
                min(
                    self.index if self.index is not None else len(target.pages),
                    len(target.pages),
                ),
            )
            target.activate_page(self.page)
            target.show()
            self._verify(target)
        except (RuntimeError, ValueError, MemoryError) as error:
            try:
                self._rollback()
            except (RuntimeError, ValueError, MemoryError):
                # A partial rollback may already have reattached the page. Never
                # delete that host, or let a second failure discard ownership.
                self.owner.recover_retained()
            message = (
                "Could not move tab; your document is retained. "
                f"Use Window > Recover retained tabs if needed. {error}"
            )
            self.source.statusBar().showMessage(message)
            self.owner.transfer_failed.emit(message)
            return None
        else:
            return target

    def _prepare(self) -> window_types.EditorWindow:
        from verdiclip.editor.window import (  # noqa: PLC0415 - factory breaks the UI ownership cycle
            EditorOptions,
            EditorWindow,
        )

        if self.target is None:
            self.target = EditorWindow(
                None,
                self.page.record.delivery,
                EditorOptions(confirm_close=self.source.confirm_close),
                workspace=self.owner,
            )
            self.target.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            if self.position is not None:
                self._position(self.target, self.position)
        return self.target

    def _position(self, target: window_types.EditorWindow, position: QPoint) -> None:
        """Keep a detached editor inside an actual screen's available area."""
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        target.resize(
            min(self.source.width(), area.width()),
            min(self.source.height(), area.height()),
        )
        target.move(
            max(area.left(), min(position.x(), area.right() - target.width() + 1)),
            max(area.top(), min(position.y(), area.bottom() - target.height() + 1)),
        )

    def _verify(self, target: window_types.EditorWindow) -> None:
        if self.page not in target.pages:
            raise RuntimeError("Destination did not retain the document")

    def _rollback(self) -> None:
        if self.target is not None and self.page in self.target.pages:
            self.target.take_page(self.page)
        if self.page not in self.source.pages:
            self.source.attach_page(self.page, self.original_index)
            self.source.activate_page(self.source_active)
        if self.target is not None and self.target_active is not None:
            self.target.activate_page(self.target_active)
        if self.created and self.target is not None:
            self.owner.forget_window(self.target)
            self.target.deleteLater()
