"""Production scrolling/pixel adapter behind explicit native-preservation capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PySide6.QtCore import QObject

from verdiclip.capture.scroll.acquisition import AcquisitionWork, WorkKind
from verdiclip.capture.scroll.backend import (
    BackendFinish,
    BackendFrame,
    ScrollBackend,
    TargetVerification,
)
from verdiclip.capture.scroll.models import ScrollTarget
from verdiclip.capture.scroll.pixels import FrameDestination, FrameFiles
from verdiclip.capture.scroll.uia_adapter import UiaAdapter
from verdiclip.capture.scroll.uia_process import UiaRequest
from verdiclip.capture.scroll.uia_protocol import (
    UiaObservation,
    UiaReference,
    UiaSample,
)
from verdiclip.exceptions import CaptureError, PlatformError


@dataclass(frozen=True, slots=True)
class NativeBackendOptions:
    """Qualified input ownership is a required capability."""

    preservation_qualified: bool = False
    input_unchanged: Callable[[], bool] | None = None
    max_frame_pixels: int = 16_000_000
    frame_parent: Path | None = None

    def __post_init__(self) -> None:
        """Reject invalid allocation policies before starting asynchronous work."""
        if self.max_frame_pixels < 1:
            raise ValueError("Frame pixel budget must be positive")


class UiaFrameBackend(ScrollBackend):
    """Bind one selected container to warm UIA operations and private ROI pixels.

    Construct one backend per capture. The caller supplies the qualified native
    lease's latched input/focus safety predicate; no lease means unavailable.
    No method activates a window, dismisses content or forwards a click.
    """

    POSITION_EPSILON = 0.001

    def __init__(
        self,
        reference: UiaReference,
        adapter: UiaAdapter,
        parent: QObject | None = None,
        *,
        options: NativeBackendOptions | None = None,
    ) -> None:
        """Retain immutable target metadata; do not read a desktop or create files."""
        super().__init__(parent)
        self._reference, self._adapter = reference, adapter
        self._options = options if options is not None else NativeBackendOptions()
        self._target: ScrollTarget | None = None
        self._request: UiaRequest | None = None
        self._context: int | AcquisitionWork | None = None
        self._destination: FrameDestination | None = None
        self._files: FrameFiles | None = None
        self._origin: float | None = None
        self._interfered = False
        self._cancelled = False
        self._finish_id: int | None = None
        self._restore_requested = False
        self._restoring = False
        self._restored = False
        self._finish_note = ""
        adapter.observed.connect(self._observed)
        adapter.sampled.connect(self._sampled)
        adapter.failed.connect(self._failed)
        adapter.scope_closed.connect(self._scope_closed)
        adapter.cleanup_failed.connect(self._cleanup_failed)

    @property
    @override
    def unavailable_reason(self) -> str | None:
        """Require a qualified preservation lease before native operations."""
        if (
            not self._options.preservation_qualified
            or self._options.input_unchanged is None
        ):
            return (
                "Scrolling capture is unavailable until native input preservation "
                "is verified."
            )
        return None

    def _safe(self) -> bool:
        """Once interference is detected, never restore or move from a stale plan."""
        guard = self._options.input_unchanged
        try:
            unchanged = guard is not None and guard()
        except (CaptureError, PlatformError, OSError, RuntimeError):
            unchanged = False
        if self._cancelled or self._interfered or not unchanged:
            self._interfered = True
            return False
        return True

    @override
    def verify(self, target: ScrollTarget, request_id: int) -> None:
        """Fresh UIA identity, geometry, metrics and occlusion precede every step."""
        reason = self.unavailable_reason
        if reason is not None:
            raise CaptureError(reason)
        if self._request is not None or self._adapter.busy:
            raise CaptureError("The scroll area already has an operation in flight")
        if (
            target.identity != self._reference.identity
            or target.viewport.intersected(self._reference.bounds) != target.viewport
            or not self._safe()
        ):
            self.verified.emit(
                TargetVerification(request_id, target, safe_to_scroll=False)
            )
            return
        if self._target is not None and target != self._target:
            self.verified.emit(
                TargetVerification(request_id, target, safe_to_scroll=False)
            )
            return
        self._target = target
        self._context = request_id
        self._request = self._adapter.observe(self._reference, viewport=target.viewport)

    @override
    def execute(self, work: AcquisitionWork) -> None:
        """Issue only a current target's single step or exact selected-ROI sample."""
        if self._target != work.target or not self._safe():
            raise CaptureError("Scrolling target safety changed")
        if self._request is not None or self._adapter.busy:
            raise CaptureError("Scrolling backend already has work in flight")
        self._context = work
        if work.kind is WorkKind.SCROLL:
            self._request = self._adapter.step(
                self._reference, confirmed=True, viewport=work.target.viewport
            )
        else:
            if self._files is None:
                self._files = FrameFiles(
                    max_pixels=self._options.max_frame_pixels,
                    parent=self._options.frame_parent,
                )
                self._adapter.register_scope_cleanup(self._files.close)
            self._destination = self._files.destination(work.target.viewport)
            self._request = self._adapter.sample(
                self._reference, self._destination, confirmed=True
            )

    def _take_context(self, request: UiaRequest) -> int | AcquisitionWork | None:
        """Clear correlation before emitting a signal that can reenter the backend."""
        if request is not self._request:
            return None
        context = self._context
        self._request = self._context = None
        return context

    def _observed(self, request: UiaRequest, observation: UiaObservation) -> None:
        """Metrics alone never manufacture a pixel displacement or document end."""
        context = self._take_context(request)
        if context is None or self._target is None:
            return
        changed_position = (
            abs(
                observation.reference.vertical_percent
                - self._reference.vertical_percent
            )
            > self.POSITION_EPSILON
        )
        changed_extent = observation.reference.view_size != self._reference.view_size
        had_origin = self._origin is not None
        if changed_extent or (
            changed_position
            and had_origin
            and isinstance(context, int)
            and not self._restoring
        ):
            self._interfered = True
        safe = self._safe()
        self._reference = observation.reference
        if self._origin is None and safe:
            self._origin = observation.reference.vertical_percent
        if self._restoring:
            self._restored = (
                safe
                and self._origin is not None
                and abs(self._reference.vertical_percent - self._origin)
                <= self.POSITION_EPSILON
            )
            if not self._restored:
                self._finish_note = (
                    "The original scroll position could not be restored."
                )
            self._stop_worker()
        elif self._finish_id is not None:
            self._begin_restore()
        elif isinstance(context, int):
            self.verified.emit(
                TargetVerification(context, self._target, safe_to_scroll=safe)
            )
        elif safe:
            self.moved.emit(context)
        else:
            self._interfered = True
            self.failed.emit(context, "The selected area changed during scrolling.")

    def _sampled(self, request: UiaRequest, sample: UiaSample) -> None:
        """Open only a validated caller-owned PNG after the helper closed its handle."""
        context = self._take_context(request)
        destination, self._destination = self._destination, None
        if (
            not isinstance(context, AcquisitionWork)
            or destination is None
            or self._target is None
        ):
            return
        drifted = (
            abs(
                sample.observation.reference.vertical_percent
                - self._reference.vertical_percent
            )
            > self.POSITION_EPSILON
        )
        safe = (
            self._safe()
            and not drifted
            and sample.observation.reference.view_size == self._reference.view_size
        )
        self._reference = sample.observation.reference
        if not safe:
            self._interfered = True
            if self._finish_id is not None:
                self._finish_note = (
                    "The original position could not be safely restored."
                )
                self._stop_worker()
            else:
                self.failed.emit(context, "Input or content changed during capture.")
            return
        if self._finish_id is not None:
            self._begin_restore()
            return
        if self._files is None:
            self.failed.emit(context, "Private capture pixels are unavailable.")
            return
        try:
            image = self._files.consume(destination)
        except CaptureError as error:
            self._interfered = True
            self.failed.emit(context, str(error))
            return
        actual_target = ScrollTarget(
            self._reference.identity,
            self._target.title,
            sample.viewport,
            self._target.geometry_epoch,
        )
        self.sampled.emit(
            context,
            BackendFrame(actual_target, image, at_end=sample.observation.at_end),
        )

    def _failed(self, request: UiaRequest, message: str) -> None:
        """Unknown outcomes stop acquisition and disallow automatic restoration."""
        context = self._take_context(request)
        if context is None:
            return
        self._interfered = True
        if self._finish_id is not None:
            self._finish_note = "The original position could not be safely restored."
            self._stop_worker()
        else:
            self.failed.emit(context, message)

    @override
    def finish(self, request_id: int, *, restore: bool) -> None:
        """Let one in-flight operation settle before a bounded safe restoration."""
        self._finish_id = request_id
        self._restore_requested = restore
        if self._request is None:
            self._begin_restore()

    def _begin_restore(self) -> None:
        """Restore only known positions with unchanged input ownership."""
        if self._finish_id is None:
            return
        if (
            not self._restore_requested
            or not self._safe()
            or self._origin is None
            or self._target is None
        ):
            if self._restore_requested:
                self._finish_note = (
                    "The original position could not be safely restored."
                )
            self._stop_worker()
            return
        if (
            abs(self._reference.vertical_percent - self._origin)
            <= self.POSITION_EPSILON
        ):
            self._restored = True
            self._stop_worker()
            return
        self._restoring = True
        self._context = self._finish_id
        try:
            self._request = self._adapter.restore(
                self._reference,
                self._origin,
                confirmed=True,
                viewport=self._target.viewport,
            )
        except CaptureError:
            self._finish_note = "The original position could not be restored."
            self._stop_worker()

    def _stop_worker(self) -> None:
        """Retain private files until actual worker termination releases their scope."""
        self._adapter.cancel()
        if not self._adapter.busy:
            self._scope_closed()

    def _scope_closed(self) -> None:
        """Cleanup receipt is independent from whether a window/editor will open."""
        request_id, self._finish_id = self._finish_id, None
        if request_id is not None and not self._cancelled:
            self.finished.emit(
                BackendFinish(request_id, self._restored, self._finish_note)
            )

    def _cleanup_failed(self, message: str) -> None:
        """Keep cleanup errors visible without discarding verified capture pixels."""
        self._finish_note = message

    @override
    def cancel(self) -> None:
        """Invalidate receipts immediately without replaying target input."""
        self._cancelled = True
        self._finish_id = None
        self._request = self._context = self._destination = None
        self._adapter.cancel()
