"""Typed asynchronous UIA boundary for discovery, observation and consented movement."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from verdiclip.capture.scroll.pixels import FrameDestination
from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess, UiaRequest
from verdiclip.capture.scroll.uia_protocol import (
    PERCENT_MAX,
    UiaDiscovery,
    UiaObservation,
    UiaReference,
    UiaSample,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect


class UiaAdapter(QObject):
    """Expose validated snapshots, never live automation elements or shell text.

    The capture owner retains origin/latest scroll references and an input
    interference latch. Restore is only an explicit best-effort request after
    verifying that latch and the original target; it is not automatic on error.
    """

    discovered = Signal(object, object)  # UiaRequest, UiaDiscovery
    observed = Signal(object, object)  # UiaRequest, UiaObservation
    sampled = Signal(object, object)  # UiaRequest, UiaSample
    failed = Signal(object, str)
    busy_changed = Signal(bool)
    scope_closed = Signal()
    cleanup_failed = Signal(str)

    def __init__(
        self, parent: QObject | None = None, *, process: UiaProcess | None = None
    ) -> None:
        """Default to an unqualified native transport; injected fixtures stay local."""
        super().__init__(parent)
        self._process = process if process is not None else UiaProcess(self)
        self._request: UiaRequest | None = None
        self._expected: UiaReference | None = None
        self._destination: FrameDestination | None = None
        self._process.completed.connect(self._completed)
        self._process.failed.connect(self._failed)
        self._process.busy_changed.connect(self.busy_changed)
        self._process.scope_closed.connect(self.scope_closed)
        self._process.cleanup_failed.connect(self.cleanup_failed)

    @property
    def busy(self) -> bool:
        """The transport also accounts for a child currently terminating."""
        return self._process.busy

    def inspect(self, window_handle: int, point: Point) -> UiaRequest:
        """Read bounded ancestors of a selected point without touching controls."""
        if (
            isinstance(window_handle, bool)
            or window_handle < 1
            or not all(math.isfinite(v) for v in (point.x, point.y))
        ):
            raise ValueError("Discovery needs a valid window and finite physical point")
        return self._start(
            UiaAction.INSPECT,
            {"window_handle": window_handle, "x": point.x, "y": point.y},
        )

    def observe(
        self, reference: UiaReference, *, viewport: Rect | None = None
    ) -> UiaRequest:
        """Revalidate generation, geometry and scroll metrics without movement."""
        return self._start(
            UiaAction.OBSERVE, self._payload(reference, viewport), reference
        )

    def step(
        self, reference: UiaReference, *, confirmed: bool, viewport: Rect | None = None
    ) -> UiaRequest:
        """Request only a provider vertical step after explicit capture consent."""
        if not confirmed:
            raise CaptureError("Scrolling requires explicit Start confirmation")
        payload = self._payload(reference, viewport)
        payload["confirmed"] = confirmed
        return self._start(UiaAction.STEP, payload, reference)

    def restore(
        self,
        reference: UiaReference,
        percent: float,
        *,
        confirmed: bool,
        viewport: Rect | None = None,
    ) -> UiaRequest:
        """Best-effort position restoration cannot run from an unconfirmed request."""
        if not confirmed:
            raise CaptureError(
                "Position restoration requires capture-owner confirmation"
            )
        if not math.isfinite(percent) or not 0 <= percent <= PERCENT_MAX:
            raise ValueError("Restore position must be a finite percentage")
        payload = self._payload(reference, viewport)
        payload.update({"confirmed": confirmed, "restore_percent": percent})
        return self._start(UiaAction.RESTORE, payload, reference)

    @staticmethod
    def _payload(
        reference: UiaReference, viewport: Rect | None = None
    ) -> dict[str, object]:
        """Use native process generation and runtime identity for every operation."""
        payload: dict[str, object] = {
            "window_handle": reference.window_handle,
            "reference": reference.encode(),
        }
        if viewport is not None:
            payload["viewport"] = [
                viewport.x,
                viewport.y,
                viewport.width,
                viewport.height,
            ]
        return payload

    def sample(
        self, reference: UiaReference, destination: FrameDestination, *, confirmed: bool
    ) -> UiaRequest:
        """Capture only the explicitly selected ROI into one caller-owned file."""
        if not confirmed:
            raise CaptureError("Pixel capture requires explicit Start confirmation")
        payload = self._payload(reference, destination.viewport)
        payload.update({"confirmed": confirmed, "image_path": str(destination.path)})
        return self._start(UiaAction.SAMPLE, payload, reference, destination)

    def _start(
        self,
        action: UiaAction,
        payload: dict[str, object],
        expected: UiaReference | None = None,
        destination: FrameDestination | None = None,
    ) -> UiaRequest:
        """One request owns response interpretation until completion or cancellation."""
        if self.busy or self._request is not None:
            raise CaptureError("UI Automation already has an operation in flight")
        request = self._process.start(action, payload)
        self._expected, self._request = expected, request
        self._destination = destination
        return request

    def _completed(self, request: UiaRequest, reply: object) -> None:
        """Parse before emission; late snapshots cannot acquire target identity."""
        if request is not self._request:
            return
        expected = self._expected
        destination = self._destination
        self._request = self._expected = self._destination = None
        try:
            result = self._parse(request, reply, expected, destination)
        except (ValueError, TypeError) as error:
            self.failed.emit(request, str(error))
            return
        if isinstance(result, UiaDiscovery):
            self.discovered.emit(request, result)
        elif isinstance(result, UiaSample):
            self.sampled.emit(request, result)
        else:
            self.observed.emit(request, result)

    @staticmethod
    def _parse(
        request: UiaRequest,
        reply: object,
        expected: UiaReference | None,
        destination: FrameDestination | None,
    ) -> UiaDiscovery | UiaObservation | UiaSample:
        """Keep provider decoding failures separate from downstream signal slots."""
        if request.action is UiaAction.INSPECT:
            return UiaDiscovery.decode(reply)
        if expected is None:
            raise ValueError("UI Automation observation has no expected target")
        if request.action is UiaAction.SAMPLE:
            if destination is None:
                raise ValueError("Pixel observation has no selected destination")
            return UiaSample.decode(
                reply,
                expected,
                viewport=destination.viewport,
                image_path=str(destination.path),
            )
        return UiaObservation.decode(reply, expected)

    def _failed(self, request: UiaRequest, message: str) -> None:
        """Leave failure visible and prevent its late completion from delivering."""
        if request is self._request:
            self._request = self._expected = self._destination = None
            self.failed.emit(request, message)

    def cancel(self) -> None:
        """Invalidate callbacks before stopping only the owned provider process."""
        self._request = self._expected = self._destination = None
        self._process.cancel()

    def register_scope_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Clean private pixel files only after the helper has terminated."""
        self._process.register_scope_cleanup(cleanup)
