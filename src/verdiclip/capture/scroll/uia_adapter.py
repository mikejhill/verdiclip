"""Typed asynchronous UIA boundary for discovery, observation and consented movement."""

from __future__ import annotations

import math

from PySide6.QtCore import QObject, Signal

from verdiclip.capture.scroll.uia_process import UiaAction, UiaProcess, UiaRequest
from verdiclip.capture.scroll.uia_protocol import (
    PERCENT_MAX,
    UiaDiscovery,
    UiaObservation,
    UiaReference,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point


class UiaAdapter(QObject):
    """Expose validated snapshots, never live automation elements or shell text.

    The capture owner retains origin/latest scroll references and an input
    interference latch. Restore is only an explicit best-effort request after
    verifying that latch and the original target; it is not automatic on error.
    """

    discovered = Signal(object, object)  # UiaRequest, UiaDiscovery
    observed = Signal(object, object)  # UiaRequest, UiaObservation
    failed = Signal(object, str)
    busy_changed = Signal(bool)

    def __init__(
        self, parent: QObject | None = None, *, process: UiaProcess | None = None
    ) -> None:
        """Default to an unqualified native transport; injected fixtures stay local."""
        super().__init__(parent)
        self._process = process if process is not None else UiaProcess(self)
        self._request: UiaRequest | None = None
        self._expected: UiaReference | None = None
        self._process.completed.connect(self._completed)
        self._process.failed.connect(self._failed)
        self._process.busy_changed.connect(self.busy_changed)

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

    def observe(self, reference: UiaReference) -> UiaRequest:
        """Revalidate generation, geometry and scroll metrics without movement."""
        return self._start(UiaAction.OBSERVE, self._payload(reference), reference)

    def step(self, reference: UiaReference, *, confirmed: bool) -> UiaRequest:
        """Request only a provider vertical step after explicit capture consent."""
        if not confirmed:
            raise CaptureError("Scrolling requires explicit Start confirmation")
        payload = self._payload(reference)
        payload["confirmed"] = confirmed
        return self._start(UiaAction.STEP, payload, reference)

    def restore(
        self, reference: UiaReference, percent: float, *, confirmed: bool
    ) -> UiaRequest:
        """Best-effort position restoration cannot run from an unconfirmed request."""
        if not confirmed:
            raise CaptureError(
                "Position restoration requires capture-owner confirmation"
            )
        if not math.isfinite(percent) or not 0 <= percent <= PERCENT_MAX:
            raise ValueError("Restore position must be a finite percentage")
        payload = self._payload(reference)
        payload.update({"confirmed": confirmed, "restore_percent": percent})
        return self._start(UiaAction.RESTORE, payload, reference)

    @staticmethod
    def _payload(reference: UiaReference) -> dict[str, object]:
        """Use native process generation and runtime identity for every operation."""
        return {
            "window_handle": reference.window_handle,
            "reference": reference.encode(),
        }

    def _start(
        self,
        action: UiaAction,
        payload: dict[str, object],
        expected: UiaReference | None = None,
    ) -> UiaRequest:
        """One request owns response interpretation until completion or cancellation."""
        if self.busy or self._request is not None:
            raise CaptureError("UI Automation already has an operation in flight")
        request = self._process.start(action, payload)
        self._expected, self._request = expected, request
        return request

    def _completed(self, request: UiaRequest, reply: object) -> None:
        """Parse before emission; late snapshots cannot acquire target identity."""
        if request is not self._request:
            return
        expected = self._expected
        self._request = self._expected = None
        try:
            result = self._parse(request, reply, expected)
        except (ValueError, TypeError) as error:
            self.failed.emit(request, str(error))
            return
        if isinstance(result, UiaDiscovery):
            self.discovered.emit(request, result)
        else:
            self.observed.emit(request, result)

    @staticmethod
    def _parse(
        request: UiaRequest, reply: object, expected: UiaReference | None
    ) -> UiaDiscovery | UiaObservation:
        """Keep provider decoding failures separate from downstream signal slots."""
        if request.action is UiaAction.INSPECT:
            return UiaDiscovery.decode(reply)
        if expected is None:
            raise ValueError("UI Automation observation has no expected target")
        return UiaObservation.decode(reply, expected)

    def _failed(self, request: UiaRequest, message: str) -> None:
        """Leave failure visible and prevent its late completion from delivering."""
        if request is self._request:
            self._request = self._expected = None
            self.failed.emit(request, message)

    def cancel(self) -> None:
        """Invalidate callbacks before stopping only the owned provider process."""
        self._request = self._expected = None
        self._process.cancel()
