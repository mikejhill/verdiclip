"""Undo/redo stack that executes commands against a document."""

from __future__ import annotations

import logging
from collections.abc import Callable

from verdiclip.document.commands import Command
from verdiclip.document.document import Document

logger = logging.getLogger(__name__)

type HistoryListener = Callable[[], None]


class History:
    """Execute, undo, and redo commands; track whether work has been delivered."""

    def __init__(self, document: Document, *, delivered: bool = True) -> None:
        self._document = document
        self._done: list[Command] = []
        self._undone: list[Command] = []
        # A fresh capture starts undelivered; an opened file starts delivered
        self._delivered_depth: int | None = 0 if delivered else None
        self._listeners: list[HistoryListener] = []

    @property
    def document(self) -> Document:
        """Return the document this history edits."""
        return self._document

    @property
    def can_undo(self) -> bool:
        """True if there is something to undo."""
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        """True if there is something to redo."""
        return bool(self._undone)

    @property
    def undo_text(self) -> str:
        """Return the description of the command ``undo`` would revert."""
        return self._done[-1].description if self._done else ""

    @property
    def redo_text(self) -> str:
        """Return the description of the command ``redo`` would apply."""
        return self._undone[-1].description if self._undone else ""

    @property
    def is_delivered(self) -> bool:
        """True if the current state was copied or saved (or opened from a file)."""
        return self._delivered_depth == len(self._done)

    def subscribe(self, listener: HistoryListener) -> None:
        """Call ``listener`` after every execute, undo, redo, or delivery mark."""
        self._listeners.append(listener)

    def execute(self, command: Command) -> None:
        """Apply ``command`` and make it undoable, merging where allowed."""
        command.apply(self._document)
        depth = len(self._done)
        branched = bool(self._undone)
        # A delivered state on the discarded redo branch can't be reached again
        if (
            branched
            and self._delivered_depth is not None
            and self._delivered_depth > depth
        ):
            self._delivered_depth = None
        self._undone.clear()
        mergeable = depth > 0 and not branched and self._delivered_depth != depth
        if mergeable and self._done[-1].merge(command):
            logger.debug("Merged %s into previous command", command.description)
        else:
            self._done.append(command)
        self._notify()

    def undo(self) -> None:
        """Revert the most recent command, if any."""
        if not self._done:
            return
        command = self._done.pop()
        command.revert(self._document)
        self._undone.append(command)
        self._notify()

    def redo(self) -> None:
        """Re-apply the most recently undone command, if any."""
        if not self._undone:
            return
        command = self._undone.pop()
        command.apply(self._document)
        self._done.append(command)
        self._notify()

    def mark_delivered(self) -> None:
        """Record that the current state has been delivered somewhere."""
        self._delivered_depth = len(self._done)
        self._notify()

    def _notify(self) -> None:
        """Inform listeners of a change."""
        for listener in list(self._listeners):
            listener()
