"""Asynchronous scrolling backend contracts; the default is visibly unavailable."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from verdiclip.capture.scroll.acquisition import AcquisitionWork
from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.exceptions import CaptureError


@dataclass(frozen=True, slots=True)
class TargetVerification:
    """Fresh identity and safety evidence for one outstanding validation request."""

    request_id: int
    target: ScrollTarget
    safe_to_scroll: bool


@dataclass(frozen=True, slots=True)
class BackendFrame:
    """One actual observed target and viewport, never a guessed request identity."""

    target: ScrollTarget
    image: QImage
    at_end: bool = False

    def __post_init__(self) -> None:
        """Retain independent value ownership without another pixel allocation."""
        object.__setattr__(self, "image", QImage(self.image))


@dataclass(frozen=True, slots=True)
class BackendFinish:
    """Bounded cleanup/restoration outcome, independent of capture completeness."""

    request_id: int
    restored: bool
    message: str = ""


class ScrollBackend(QObject):
    """Safe unavailable implementation and signal contract for concrete adapters.

    verify must revalidate target identity/geometry/occlusion and the active
    input-interference latch. execute repeats safety checks immediately before
    native work. finish may restore only when original target/last owned position
    remain proven; unknown or interrupted actions must not trigger a retry.
    """

    verified = Signal(object)  # TargetVerification
    sampled = Signal(object, object)  # AcquisitionWork, BackendFrame
    moved = Signal(object)  # AcquisitionWork, only after successful provider reply
    failed = Signal(object, str)  # request identity, bounded user-facing explanation
    finished = Signal(object)  # BackendFinish

    @property
    def unavailable_reason(self) -> str | None:
        """Native qualification must be explicit rather than an optimistic fallback."""
        return (
            "Scrolling capture is unavailable until native input preservation "
            "is verified."
        )

    def verify(self, target: ScrollTarget, request_id: int) -> None:
        """Read fresh target safety metadata without clicks, focus or scrolling."""
        del target, request_id
        raise CaptureError(self.unavailable_reason or "Scrolling backend unavailable")

    def execute(self, work: AcquisitionWork) -> None:
        """Concrete backends may execute only this target-bound one-flight request."""
        del work
        raise CaptureError(self.unavailable_reason or "Scrolling backend unavailable")

    def finish(self, request_id: int, *, restore: bool) -> None:
        """Produce terminal cleanup even when restoration is unsupported."""
        del restore
        self.cancel()
        self.finished.emit(BackendFinish(request_id, restored=False))

    def cancel(self) -> None:
        """No-op for the unavailable backend; concrete adapters cancel owned work."""
