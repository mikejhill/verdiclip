"""Application-owned warm MTA lease, independent of transient UI owners."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from math import ceil
from time import monotonic

from PySide6.QtCore import QCoreApplication, QObject, QProcess, Qt, QTimer, Signal

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics, WorkerTrace
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
    CLEANUP_MS = 2000

    def __init__(self) -> None:
        """Bind resource lifetime to the application rather than a dialog/widget."""
        super().__init__(QCoreApplication.instance())
        self._process: QProcess | None = None
        self._pipe: UiaPipe | None = None
        self._pending: UiaRequest | None = None
        self._closing = False
        self._released = False
        self._ready = False
        self._request_sent = False
        self._trace = WorkerTrace(monotonic)
        self._cleanup_state = "closed"
        self._operation_ms = 0
        self._cleanup: list[Callable[[], None]] = []
        self._scope_open = False
        self._output = bytearray()
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self._deadline.timeout.connect(self._timeout)
        self._deadline_at = 0.0
        self._deadline_serial = 0
        self._deadline_phase = "idle"
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.timeout.connect(self.cancel)
        self._lifetime = QTimer(self)
        self._lifetime.setSingleShot(True)
        self._lifetime.timeout.connect(self._expired)
        self._cleanup_deadline = QTimer(self)
        self._cleanup_deadline.setSingleShot(True)
        self._cleanup_deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self._cleanup_deadline.timeout.connect(self._cleanup_timeout)
        self._cleanup_at = 0.0

    @property
    def diagnostics(self) -> WorkerDiagnostics:
        """Copy bounded timing evidence without exposing the helper's data."""
        return WorkerDiagnostics(
            phase=self._deadline_phase,
            process_state=self._process.state().name if self._process else "NotRunning",
            cleanup=self._cleanup_state,
            request_sent=self._request_sent,
            events=tuple(self._trace.events),
            dropped_events=self._trace.dropped_events,
            stdout_bytes=self._trace.stdout_bytes,
            stderr_bytes=self._trace.stderr_bytes,
            pipe_bytes=self._trace.pipe_bytes,
            max_deadline_lateness_ms=self._trace.max_deadline_lateness_ms,
        )

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
            pipe.connected.connect(lambda: self._pipe_connected(pipe))
            pipe.received.connect(lambda data: self._pipe_read(pipe, data))
            pipe.failed.connect(lambda message: self._pipe_failed(pipe, message))
        process = self._new_process() if first else self._process
        if process is None:
            raise CaptureError("The UI Automation worker is unavailable")
        self._pending = request
        self._request_sent = False
        self._trace.mark("queued")
        self._operation_ms = deadline_ms
        self._output.clear()
        self._idle.stop()
        self._arm_deadline(
            startup_ms if first else deadline_ms, "startup" if first else "operation"
        )
        QTimer.singleShot(0, lambda: self._dispatch(process, request, command))

    def _arm_deadline(self, milliseconds: int, phase: str) -> None:
        self._deadline_serial += 1
        self._deadline_phase = phase
        self._deadline_at = monotonic() + milliseconds / 1000
        self._deadline.start(milliseconds)

    def _stop_deadline(self) -> None:
        self._deadline_serial += 1
        self._deadline_at = 0.0
        self._deadline_phase = "idle"
        self._deadline.stop()

    def _new_process(self) -> QProcess:
        """Own callbacks on this stable lease until terminal retirement."""
        process = QProcess(self)
        self._process = process
        self._ready = False
        self._trace = WorkerTrace(monotonic)
        self._cleanup_state = "open"
        self._scope_open = True
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.started.connect(lambda: self._started(process))
        process.readyReadStandardError.connect(lambda: self._drain_error(process))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        process.finished.connect(lambda _code, _status: self._finished(process))
        self._lifetime.start(self.LIFETIME_MS)
        return process

    def _started(self, process: QProcess) -> None:
        if process is self._process:
            self._trace.mark("process_started")

    def _pipe_connected(self, pipe: UiaPipe) -> None:
        if pipe is self._pipe:
            self._trace.mark("pipe_accepted")

    def _dispatch(
        self, process: QProcess, request: UiaRequest, command: tuple[str, ...]
    ) -> None:
        """Notify only after the caller has recorded the new request identity."""
        if process is not self._process or request is not self._pending:
            return
        self._trace.mark("dispatch")
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
            self._trace.mark("launch_requested")
            process.start(command[0], arguments)
            if self._pipe is not None:
                process.closeWriteChannel()

    def _send(self, process: QProcess) -> None:
        """Use newline-delimited JSON; retain stdin for subsequent MTA operations."""
        if (
            process is self._process
            and self._pending is not None
            and not self._request_sent
        ):
            data = self._pending.encoded + b"\n"
            self._request_sent = True
            self._trace.mark("request_sent")
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
        self._trace.stdout_bytes += len(data)
        if self._pipe is not None:
            if self._trace.stdout_bytes > self.MAX_BYTES:
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
            self._trace.pipe_bytes += len(data)
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
        self._trace.mark("protocol_ready")
        self._arm_deadline(self._operation_ms, "operation")
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
        self._trace.mark("response_received")
        self._stop_deadline()
        self._idle.start(self.IDLE_MS)
        self.completed.emit(request, reply)
        self.busy_changed.emit(self.busy)

    def _drain_error(self, process: QProcess) -> None:
        """Drain diagnostics without exposing provider text or retaining it."""
        if process is self._process and not self._trace.error_bytes(
            bytes(process.readAllStandardError().data())
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
        self._trace.mark("process_finished")
        if self._pending is not None:
            self._read(process)
        if self._pending is not None:
            self._fail("UI Automation did not complete; the target may have moved")
        self._dispose(process)

    def _timeout(self) -> None:
        """Observe buffered receipts once before deciding a due phase has failed."""
        process, request = self._process, self._pending
        if process is None or request is None or self._closing:
            return
        serial, phase = self._deadline_serial, self._deadline_phase
        remaining = self._deadline_at - monotonic()
        if remaining > 0:
            self._deadline.start(max(1, ceil(remaining * 1000)))
            return
        self._trace.max_deadline_lateness_ms = max(
            self._trace.max_deadline_lateness_ms, ceil(-remaining * 1000)
        )
        # readyRead and timeout are independent queued event sources. Inspect
        # only already buffered bytes; never wait, pump events, or retry work.
        pipe = self._pipe
        self._drain_error(process)
        if process is not self._process or request is not self._pending:
            return
        if pipe is not None:
            pipe.drain()
        if process is self._process and request is self._pending:
            self._read(process)
        if (
            process is not self._process
            or request is not self._pending
            or serial != self._deadline_serial
            or phase != self._deadline_phase
        ):
            return
        self._trace.mark(f"{phase}_timeout")
        logger.warning("UI Automation deadline expired: %s", self.diagnostics)
        self._fail(
            f"UI Automation {phase} timed out; capture stopped "
            + (
                "and the target may have moved"
                if self._request_sent
                else "before a command was sent"
            )
        )

    def _expired(self) -> None:
        """No helper persists beyond the explicit finite active-session lease."""
        self._fail("UI Automation session reached its lifetime limit")

    def _fail(self, message: str) -> None:
        """Invalidate before callbacks and terminate only this lease's child."""
        request, self._pending = self._pending, None
        self._stop_deadline()
        self._idle.stop()
        if self._process is not None:
            was_closing = self._closing
            self._closing = True
            if self._process.state() is QProcess.ProcessState.NotRunning:
                self._dispose(self._process)
            else:
                if not was_closing:
                    self._trace.mark("termination_requested")
                    self._cleanup_state = "waiting"
                    self._cleanup_at = monotonic() + self.CLEANUP_MS / 1000
                    self._cleanup_deadline.start(self.CLEANUP_MS)
                self._process.kill()
        else:
            self._close_scope()
        if request is not None:
            self.failed.emit(request, message)

    def _cleanup_timeout(self) -> None:
        """Report unconfirmed cleanup without freeing a still-live helper slot."""
        process = self._process
        if process is None or not self._closing:
            return
        remaining = self._cleanup_at - monotonic()
        if remaining > 0:
            self._cleanup_deadline.start(max(1, ceil(remaining * 1000)))
            return
        if process.state() is QProcess.ProcessState.NotRunning:
            self._dispose(process)
            return
        self._cleanup_state = "unconfirmed"
        self._trace.mark("cleanup_unconfirmed")
        logger.warning("UI Automation cleanup unconfirmed: %s", self.diagnostics)
        self.cleanup_failed.emit(
            "UI Automation cleanup could not be confirmed; the worker remains reserved"
        )

    def _dispose(self, process: QProcess) -> None:
        """Break retirement cycles before releasing child or lease ownership."""
        if process is not self._process:
            return
        process.setParent(QCoreApplication.instance())
        self._process = None
        self._ready = False
        self._closing = False
        self._stop_deadline()
        self._idle.stop()
        self._lifetime.stop()
        self._cleanup_deadline.stop()
        self._cleanup_at = 0.0
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
        self._cleanup_state = "confirmed"
        callbacks, self._cleanup = self._cleanup, []
        for cleanup in callbacks:
            try:
                self._run_cleanup(cleanup)
            except (CaptureError, OSError) as error:
                logger.warning("Capture scope cleanup failed: %s", error)
                self._cleanup_state = "callback_failed"
                self.cleanup_failed.emit(str(error))
        self._trace.mark("scope_closed")
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
