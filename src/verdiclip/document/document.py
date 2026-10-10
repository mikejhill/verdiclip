"""The editable document: base image, non-destructive crop, and annotations."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from PySide6.QtGui import QImage

from verdiclip.document.annotations import Annotation, CounterMarker
from verdiclip.geometry import Point, Rect

logger = logging.getLogger(__name__)

type DocumentListener = Callable[[], None]


@dataclass(frozen=True, slots=True)
class DocumentState:
    """Everything a whole-image change replaces: pixels, crop, and annotations."""

    image: QImage
    crop: Rect
    annotations: tuple[Annotation, ...]


class Document:
    """An image plus annotations; mutate only through ``History.execute``.

    Annotations are stored in original-image coordinates. Cropping changes
    only the visible rectangle, so undoing a crop is exact and obfuscation
    always pixelates the true image beneath it.
    """

    def __init__(self, image: QImage) -> None:
        if image.isNull():
            msg = "Document image must not be null"
            raise ValueError(msg)
        self._image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self._image_rect = Rect(0, 0, image.width(), image.height())
        self._crop = self._image_rect
        self._annotations: list[Annotation] = []
        self._last_counter_label = "0"
        self._listeners: list[DocumentListener] = []

    # Read access

    @property
    def image(self) -> QImage:
        """Return the full, uncropped base image."""
        return self._image

    @property
    def image_rect(self) -> Rect:
        """Return the full image rectangle."""
        return self._image_rect

    @property
    def crop(self) -> Rect:
        """Return the visible (cropped) rectangle in image coordinates."""
        return self._crop

    @property
    def annotations(self) -> tuple[Annotation, ...]:
        """Return all annotations, bottom to top."""
        return tuple(self._annotations)

    @property
    def visible_annotations(self) -> tuple[Annotation, ...]:
        """Return annotations that overlap the crop, bottom to top."""
        return tuple(a for a in self._annotations if a.bounds.intersects(self._crop))

    @property
    def next_counter_label(self) -> str:
        """Return the label for the next counter: last label + 1, or "1"."""
        try:
            return str(int(self._last_counter_label) + 1)
        except ValueError:
            return "1"

    @property
    def state(self) -> DocumentState:
        """Return the image, crop, and annotations together."""
        return DocumentState(self._image, self._crop, tuple(self._annotations))

    def find(self, annotation_id: str) -> Annotation | None:
        """Return the annotation with ``annotation_id`` if present."""
        return next((a for a in self._annotations if a.id == annotation_id), None)

    def index_of(self, annotation_id: str) -> int:
        """Return the stacking index of ``annotation_id``.

        Raises:
            KeyError: If no annotation has that id.
        """
        for index, annotation in enumerate(self._annotations):
            if annotation.id == annotation_id:
                return index
        raise KeyError(annotation_id)

    def annotation_at(self, point: Point, tolerance: float) -> Annotation | None:
        """Return the topmost visible annotation under ``point``."""
        for annotation in reversed(self.visible_annotations):
            if annotation.hit_test(point, tolerance):
                return annotation
        return None

    def annotations_in(self, area: Rect) -> list[Annotation]:
        """Return visible annotations whose bounds intersect ``area``."""
        return [a for a in self.visible_annotations if a.bounds.intersects(area)]

    # Observation

    def subscribe(self, listener: DocumentListener) -> None:
        """Call ``listener`` after every change."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: DocumentListener) -> None:
        """Stop calling ``listener``."""
        self._listeners.remove(listener)

    # Mutation (commands only)

    def insert(self, items: Sequence[tuple[int, Annotation]]) -> None:
        """Insert each ``(index, annotation)`` in ascending index order."""
        for index, annotation in sorted(items, key=lambda pair: pair[0]):
            self._annotations.insert(index, annotation)
            self._track_counter(annotation)
        self._notify()

    def remove(self, ids: Iterable[str]) -> list[tuple[int, Annotation]]:
        """Remove annotations by id; return ``(index, annotation)`` pairs removed."""
        wanted = set(ids)
        removed = [(i, a) for i, a in enumerate(self._annotations) if a.id in wanted]
        self._annotations = [a for a in self._annotations if a.id not in wanted]
        self._notify()
        return removed

    def replace(self, annotations: Iterable[Annotation]) -> None:
        """Replace annotations in place, matching by id."""
        by_id = {a.id: a for a in annotations}
        missing = by_id.keys() - {a.id for a in self._annotations}
        if missing:
            msg = f"Cannot replace unknown annotations: {sorted(missing)}"
            raise KeyError(msg)
        self._annotations = [by_id.get(a.id, a) for a in self._annotations]
        for annotation in by_id.values():
            self._track_counter(annotation)
        self._notify()

    def reorder(self, ids_bottom_to_top: Sequence[str]) -> None:
        """Set the stacking order to ``ids_bottom_to_top`` (a permutation)."""
        by_id = {a.id: a for a in self._annotations}
        if sorted(ids_bottom_to_top) != sorted(by_id):
            msg = "Reorder must list every annotation exactly once"
            raise ValueError(msg)
        self._annotations = [by_id[i] for i in ids_bottom_to_top]
        self._notify()

    def set_crop(self, crop: Rect) -> None:
        """Set the visible rectangle, clamped to the image."""
        clamped = crop.intersected(self._image_rect)
        if clamped.is_empty:
            msg = f"Crop {crop} does not overlap the image {self._image_rect}"
            raise ValueError(msg)
        self._crop = clamped
        self._notify()

    def restore(self, state: DocumentState) -> None:
        """Replace the image, crop, and annotations (rotate, flip, resize)."""
        if state.image.isNull():
            msg = "Document image must not be null"
            raise ValueError(msg)
        self._image = state.image.convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        self._image_rect = Rect(0, 0, self._image.width(), self._image.height())
        self._crop = state.crop.intersected(self._image_rect)
        if self._crop.is_empty:
            self._crop = self._image_rect
        self._annotations = list(state.annotations)
        self._notify()

    # Internals

    def _track_counter(self, annotation: Annotation) -> None:
        """Remember the latest counter label for numbering."""
        if isinstance(annotation, CounterMarker):
            self._last_counter_label = annotation.label

    def _notify(self) -> None:
        """Inform listeners of a change."""
        for listener in list(self._listeners):
            listener()
