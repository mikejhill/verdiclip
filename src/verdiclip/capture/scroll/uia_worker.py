"""Application-owned warm MTA lease, independent of transient UI owners."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from PySide6.QtCore import QCoreApplication, QObject, QProcess, QTimer, Signal

from verdiclip.capture.scroll.uia_messages import UiaRequest
from verdiclip.capture.scroll.uia_pipe import UiaPipe
from verdiclip.exceptions import CaptureError

logger = logging.getLogger(__name__)


class UiaWorkerLease(QObject):
    """Retain a warm child only during active use and finite idle/session budgets.

    The application owns this resource lease. Destroying a GUI facade releases
    it, but cannot recursively delete a running/retiring process. No live COM
    object crosses the JSON boundary and no provider call runs on Qt's thread.
    """

    completed = Signal(object, object)
    failed = Signal(object, str)
    busy_changed = Signal(bool)
    scope_closed = Signal()
    cleanup_failed = Signal(str)
    MAX_BYTES = 65_536
    IDLE_MS = 30_000
    LIFETIME_MS = 180_000
    PROTOCOL_VERSION = 1

    def __init__(self) -> None:
        """Bind resource lifetime to the application rather than a dialog/widget."""
        super().__init__(QCoreApplication.instance())
        self._process: QProcess | None = None
        self._pipe: UiaPipe | None = None
        self._pending: UiaRequest | None = None
        self._closing = False
        self._released = False
        self._ready = False
        self._operation_ms = 0
        self._cleanup: list[Callable[[], None]] = []
        self._scope_open = False
        self._output = bytearray()
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(self._timeout)
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.cancel)
        self._lifetime = QTimer(self)
        self._lifetime.setSingleShot(True)
        self._lifetime.timeout.connect(self._expired)

    @property
    def busy(self) -> bool:
        """An idle warm process is reusable; a terminating process is reserved."""
        return self._pending is not None or self._closing

    @property
    def worker_pid(self) -> int | None:
        """Expose only the currently owned child for cleanup/provenance checks."""
        return int(self._process.processId()) if self._process is not None else None

    def submit(
        self,
        request: UiaRequest,
        command: tuple[str, ...],
        *,
        deadline_ms: int,
        startup_ms: int,
        use_local_pipe: bool = False,
    ) -> None:
        """Reserve one request, reusing a healthy warm MTA child when available."""
        if self.busy or self._released:
            raise CaptureError("The UI Automation helper is still busy or closing")
        first = self._process is None
        if first and use_local_pipe:
            pipe = UiaPipe(self)
            pipe.listen()
            self._pipe = pipe
            pipe.received.connect(lambda data: self._pipe_read(pipe, data))
            pipe.failed.connect(lambda message: self._pipe_failed(pipe, message))
        process = self._new_process() if first else self._process
        if process is None:
            raise CaptureError("The UI Automation worker is unavailable")
        self._pending = request
        self._operation_ms = deadline_ms
        self._output.clear()
        self._idle.stop()
        self._deadline.start(startup_ms if first else deadline_ms)
        QTimer.singleShot(0, lambda: self._dispatch(process, request, command))

    def _new_process(self) -> QProcess:
        """Own callbacks on this stable lease until terminal retirement."""
        process = QProcess(self)
        self._process = process
        self._ready = False
        self._scope_open = True
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.readyReadStandardError.connect(lambda: self._drain_error(process))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        process.finished.connect(lambda _code, _status: self._finished(process))
        self._lifetime.start(self.LIFETIME_MS)
        return process

    def _dispatch(
        self, process: QProcess, request: UiaRequest, command: tuple[str, ...]
    ) -> None:
        """Notify only after the caller has recorded the new request identity."""
        if process is not self._process or request is not self._pending:
            return
        self.busy_changed.emit(self.busy)
        if process is not self._process or request is not self._pending:
            return
        if process.state() is QProcess.ProcessState.Running:
            if self._ready:
                self._send(process)
        else:
            arguments = list(command[1:])
            if self._pipe is not None:
                arguments.extend(("-PipeName", self._pipe.name))
            process.start(command[0], arguments)
            if self._pipe is not None:
                process.closeWriteChannel()

    def _send(self, process: QProcess) -> None:
        """Use newline-delimited JSON; retain stdin for subsequent MTA operations."""
        if process is self._process and self._pending is not None:
            data = self._pending.encoded + b"\n"
            if self._pipe is None:
                process.write(data)
            else:
                try:
                    self._pipe.write(data)
                except CaptureError as error:
                    self._fail(str(error))

    def _read(self, process: QProcess) -> None:
        """Only the current request can consume one bounded, complete JSON line."""
        if process is not self._process:
            return
        data = process.readAllStandardOutput().data()
        if self._pipe is not None:
            if len(data) > self.MAX_BYTES:
                self._fail("UI Automation exceeded its diagnostic limit")
            return
        self._read_data(bytes(data))

    def _read_data(self, data: bytes) -> None:
        """Use the same bounded protocol decoder for private IPC and test peers."""
        if self._pending is None or len(self._output) + len(data) > self.MAX_BYTES:
            self._fail("UI Automation returned unexpected or oversized data")
            return
        self._output.extend(data)
        if b"\n" not in self._output:
            return
        line, _, remainder = self._output.partition(b"\n")
        if remainder.strip():
            self._fail("UI Automation returned more than one response")
            return
        self._output.clear()
        if self._ready:
            self._accept_line(bytes(line))
        else:
            self._accept_ready(bytes(line))

    def _pipe_read(self, pipe: UiaPipe, data: bytes) -> None:
        if pipe is self._pipe:
            self._read_data(data)

    def _pipe_failed(self, pipe: UiaPipe, message: str) -> None:
        if pipe is self._pipe:
            self._fail(message)

    def _accept_ready(self, line: bytes) -> None:
        """Separate host initialization from a provider operation's strict deadline."""
        try:
            ready: object = json.loads(line.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            self._fail("UI Automation returned an unreadable startup response")
            return
        if (
            not isinstance(ready, dict)
            or ready.get("ready") is not True
            or ready.get("protocol") != self.PROTOCOL_VERSION
            or isinstance(ready.get("protocol"), bool)
        ):
            self._fail("UI Automation worker protocol is unsupported")
            return
        self._ready = True
        self._deadline.start(self._operation_ms)
        if self._process is not None:
            self._send(self._process)

    def _accept_line(self, line: bytes) -> None:
        """Retire correlation state before delivery so direct callback retry is safe."""
        request = self._pending
        if request is None:
            return
        try:
            reply: object = json.loads(line.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            self._fail("UI Automation returned an unreadable response")
            return
        if not isinstance(reply, dict) or reply.get("request_id") != request.identity:
            self._fail("UI Automation returned an unrelated response")
            return
        if reply.get("ok") is not True:
            self._fail("This scroll area is unavailable or changed; capture stopped")
            return
        self._pending = None
        self._deadline.stop()
        self._idle.start(self.IDLE_MS)
        self.completed.emit(request, reply)
        self.busy_changed.emit(self.busy)

    def _drain_error(self, process: QProcess) -> None:
        """Drain diagnostics without exposing provider text or retaining it."""
        if (
            process is self._process
            and process.readAllStandardError().size() > self.MAX_BYTES
        ):
            self._fail("UI Automation exceeded its diagnostic limit")

    def _error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        """Failed startup has no finished signal; retire that owned child directly."""
        if process is self._process and error is QProcess.ProcessError.FailedToStart:
            self._fail("UI Automation helper could not start")
            self._dispose(process)

    def _finished(self, process: QProcess) -> None:
        """An unexpected EOF cannot silently complete an outstanding operation."""
        if process is not self._process:
            return
        if self._pending is not None:
            self._read(process)
        if self._pending is not None:
            self._fail("UI Automation did not complete; the target may have moved")
        self._dispose(process)

    def _timeout(self) -> None:
        """No automatic movement retry follows an unknown provider outcome."""
        phase = "operation" if self._ready else "startup"
        self._fail(
            f"UI Automation {phase} timed out; capture stopped "
            "and the target may have moved"
        )

    def _expired(self) -> None:
        """No helper persists beyond the explicit finite active-session lease."""
        self._fail("UI Automation session reached its lifetime limit")

    def _fail(self, message: str) -> None:
        """Invalidate before callbacks and terminate only this lease's child."""
        request, self._pending = self._pending, None
        self._deadline.stop()
        self._idle.stop()
        if self._process is not None:
            self._closing = True
            if self._process.state() is QProcess.ProcessState.NotRunning:
                self._dispose(self._process)
            else:
                self._process.kill()
        else:
            self._close_scope()
        if request is not None:
            self.failed.emit(request, message)

    def _dispose(self, process: QProcess) -> None:
        """Break retirement cycles before releasing child or lease ownership."""
        if process is not self._process:
            return
        process.setParent(QCoreApplication.instance())
        self._process = None
        self._ready = False
        self._closing = False
        self._deadline.stop()
        self._idle.stop()
        self._lifetime.stop()
        process.deleteLater()
        pipe, self._pipe = self._pipe, None
        if pipe is not None:
            pipe.setParent(QCoreApplication.instance())
            pipe.close()
        self._close_scope()
        self.busy_changed.emit(self.busy)
        if self._released:
            self.deleteLater()

    def register_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Retain private frame resources until this worker is known to be gone."""
        self._cleanup.append(cleanup)
        self._scope_open = True

    def _close_scope(self) -> None:
        """Cleanup is after child exit; failures never escape a Qt signal callback."""
        if not self._scope_open:
            return
        self._scope_open = False
        callbacks, self._cleanup = self._cleanup, []
        for cleanup in callbacks:
            try:
                self._run_cleanup(cleanup)
            except (CaptureError, OSError) as error:
                logger.warning("Capture scope cleanup failed: %s", error)
                self.cleanup_failed.emit(str(error))
        self.scope_closed.emit()

    @staticmethod
    def _run_cleanup(cleanup: Callable[[], None]) -> None:
        """Normalize the callback boundary by immediately re-raising with cause."""
        try:
            cleanup()
        except (CaptureError, OSError):
            raise
        except Exception as error:
            raise CaptureError(
                f"Temporary capture cleanup callback failed ({type(error).__name__})"
            ) from error

    def cancel(self) -> None:
        """End the current warm-worker scope without issuing further native work."""
        self._fail("Scrolling capture was cancelled; no further work will be issued")

    def release(self) -> None:
        """A disappearing facade leaves a retained lease until its child is gone."""
        self._released = True
        self.cancel()
        if self._process is None:
            self.deleteLater()
