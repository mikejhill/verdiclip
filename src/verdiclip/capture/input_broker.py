"""Qt lifecycle boundary for finite, nonactivating selection input ownership."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from verdiclip.capture.input_worker import CaptureInputWorker
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

type InputWorkerFactory = Callable[[Point, Rect], CaptureInputWorker]


class InputBroker(QObject):
    """Injectable semantic input and cleanup contract used by capture service."""

    ready = Signal()
    input_event = Signal(object)
    closed = Signal(bool, str)

    @property
    def active(self) -> bool:
        """Unknown input ownership is never an active lease."""
        return False

    @property
    def busy(self) -> bool:
        """The unavailable base owns no resource."""
        return False

    def start(self, initial: Point, bounds: Rect) -> None:
        """A platform implementation must explicitly acquire input ownership."""
        del initial, bounds
        raise CaptureError("Nonactivating capture input is unavailable")

    def release(self) -> None:
        """The unavailable base has no native input to release."""
        self.closed.emit(not self.busy, "")


class NativeInputBroker(InputBroker):
    """Retain its thread lease until acknowledged cleanup, even if UI is deleted."""

    STARTUP_SECONDS = 2

    def __init__(
        self,
        owner: QObject,
        *,
        factory: InputWorkerFactory = CaptureInputWorker,
        clock: Callable[[], float] = time.monotonic,
        require_windows: bool = True,
    ) -> None:
        """Construct no native API and attach lifetime to the application."""
        super().__init__(QCoreApplication.instance())
        self._factory, self._clock = factory, clock
        self._require_windows = require_windows
        self._worker: CaptureInputWorker | None = None
        self._announced = self._closing = self._released = self._broken = False
        self._started = 0.0
        self._failure = ""
        self._poll = QTimer(self)
        self._poll.setInterval(10)
        self._poll.timeout.connect(self._advance)
        owner.destroyed.connect(self._owner_gone)
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    @property
    @override
    def active(self) -> bool:
        """Read the independent terminal event even when Qt is temporarily busy."""
        worker = self._worker
        return (
            worker is not None
            and worker.ready.is_set()
            and not (worker.stopped.is_set() or self._closing)
        )

    @property
    @override
    def busy(self) -> bool:
        """Unconfirmed cleanup blocks reuse rather than starting another hook pair."""
        return self._worker is not None or self._broken

    @override
    def start(self, initial: Point, bounds: Rect) -> None:
        """Reserve once; start no hook on unsupported/non-native Qt surfaces."""
        if self.busy or self._released:
            raise CaptureError("Capture input is still being released")
        if self._require_windows and QGuiApplication.platformName() != "windows":
            raise CaptureError("Capture input requires native Windows Qt")
        self._announced = self._closing = False
        self._failure = ""
        worker = self._factory(initial, bounds)
        self._worker = worker
        self._started = self._clock()
        worker.start()
        self._poll.start()

    @override
    def release(self) -> None:
        """Request cleanup without blocking Qt or forwarding synthetic releases."""
        worker = self._worker
        if worker is None:
            return
        self._closing = True
        worker.release()

    def _owner_gone(self) -> None:
        self._released = True
        self.release()
        if self._worker is None and not self._broken:
            self.deleteLater()

    def _shutdown(self) -> None:
        """Give only this owned worker a finite cleanup window before process exit."""
        self._released = True
        self._poll.stop()
        worker = self._worker
        if worker is not None:
            worker.release()
            if worker.stopped.wait(min(1.1, worker.options.drain_seconds + 0.1)):
                self._broken = not worker.cleaned
                if worker.cleaned:
                    self._worker = None

    def _advance(self) -> None:
        worker = self._worker
        if worker is None:
            return
        if not self._closing:
            worker.heartbeat()
        if (
            worker.ready.is_set()
            and not worker.stopped.is_set()
            and not (self._closing or self._announced)
        ):
            self._announced = True
            self.ready.emit()
        if worker is not self._worker:
            return
        for event in worker.drain():
            if self._announced and not self._closing:
                self.input_event.emit(event)
            if worker is not self._worker:
                return
        if worker.stopped.is_set():
            self._retire(worker)
        elif (
            not self._announced
            and not self._closing
            and (self._clock() - self._started >= self.STARTUP_SECONDS)
        ):
            self._failure = "Capture input did not become ready in time"
            self.release()

    def _retire(self, worker: CaptureInputWorker) -> None:
        self._poll.stop()
        self._failure = worker.failure or self._failure
        self._broken = not worker.cleaned
        if worker.cleaned:
            self._worker = None
        self.closed.emit(worker.cleaned, self._failure)
        if self._released and not self._broken and self._worker is None:
            self.deleteLater()


def make_input_broker(owner: QObject) -> InputBroker:
    """Production factory; tests explicitly inject a replay broker at this seam."""
    return NativeInputBroker(owner)
