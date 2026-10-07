"""Undoable document changes; the only way the document is modified."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import override

from verdiclip.document.annotations import Annotation
from verdiclip.document.document import Document
from verdiclip.geometry import Rect


class Command(ABC):
    """A reversible change to a document."""

    def __init__(self, description: str) -> None:
        self._description = description

    @property
    def description(self) -> str:
        """Return the user-facing name shown as "Undo <description>"."""
        return self._description

    @abstractmethod
    def apply(self, document: Document) -> None:
        """Make the change."""

    @abstractmethod
    def revert(self, document: Document) -> None:
        """Undo the change."""

    def merge(self, other: Command) -> bool:
        """Absorb ``other`` (executed right after this) into this command.

        Returns:
            True if merged, so ``other`` is not pushed separately.
        """
        del other
        return False


class AddAnnotations(Command):
    """Add annotations on top of the stack."""

    def __init__(self, annotations: Sequence[Annotation], description: str) -> None:
        super().__init__(description)
        if not annotations:
            msg = "AddAnnotations needs at least one annotation"
            raise ValueError(msg)
        self._annotations = tuple(annotations)

    @override
    def apply(self, document: Document) -> None:
        """Append the annotations."""
        start = len(document.annotations)
        document.insert([(start + i, a) for i, a in enumerate(self._annotations)])

    @override
    def revert(self, document: Document) -> None:
        """Remove the annotations."""
        document.remove(a.id for a in self._annotations)


class RemoveAnnotations(Command):
    """Remove annotations, remembering their stacking positions."""

    def __init__(self, ids: Sequence[str], description: str = "Delete") -> None:
        super().__init__(description)
        self._ids = tuple(ids)
        self._removed: list[tuple[int, Annotation]] = []

    @override
    def apply(self, document: Document) -> None:
        """Remove the annotations."""
        self._removed = document.remove(self._ids)

    @override
    def revert(self, document: Document) -> None:
        """Restore the annotations at their original positions."""
        document.insert(self._removed)


class ReplaceAnnotations(Command):
    """Swap annotations for modified versions (move, resize, restyle, relabel).

    Commands with the same non-empty ``merge_key`` that touch the same
    annotations coalesce into one undo step (e.g. repeated arrow-key nudges).
    """

    def __init__(
        self,
        before: Sequence[Annotation],
        after: Sequence[Annotation],
        description: str,
        *,
        merge_key: str = "",
    ) -> None:
        super().__init__(description)
        if [a.id for a in before] != [a.id for a in after]:
            msg = "ReplaceAnnotations needs matching ids before and after"
            raise ValueError(msg)
        self._before = tuple(before)
        self._after = tuple(after)
        self._merge_key = merge_key

    @override
    def apply(self, document: Document) -> None:
        """Install the new versions."""
        document.replace(self._after)

    @override
    def revert(self, document: Document) -> None:
        """Restore the old versions."""
        document.replace(self._before)

    @override
    def merge(self, other: Command) -> bool:
        """Coalesce consecutive changes with the same key and ids."""
        if not self._merge_key or not isinstance(other, ReplaceAnnotations):
            return False
        same_ids = [a.id for a in self._after] == [a.id for a in other._after]
        if other._merge_key != self._merge_key or not same_ids:
            return False
        self._after = other._after
        return True


class ReorderAnnotations(Command):
    """Change the stacking order."""

    def __init__(
        self, before: Sequence[str], after: Sequence[str], description: str
    ) -> None:
        super().__init__(description)
        self._before = tuple(before)
        self._after = tuple(after)

    @override
    def apply(self, document: Document) -> None:
        """Apply the new order."""
        document.reorder(self._after)

    @override
    def revert(self, document: Document) -> None:
        """Restore the old order."""
        document.reorder(self._before)


class SetCrop(Command):
    """Change the visible rectangle (non-destructive crop)."""

    def __init__(self, before: Rect, after: Rect) -> None:
        super().__init__("Crop")
        self._before = before
        self._after = after

    @override
    def apply(self, document: Document) -> None:
        """Apply the new crop."""
        document.set_crop(self._after)

    @override
    def revert(self, document: Document) -> None:
        """Restore the previous crop."""
        document.set_crop(self._before)
