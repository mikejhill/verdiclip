"""Deadline-isolated UIA helper transport with no GUI-thread provider calls."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from verdiclip.exceptions import CaptureError


class UiaAction(StrEnum):
    """The helper has no invoke, click, activation or arbitrary script command."""

    INSPECT = "inspect"
    OBSERVE = "observe"
    STEP = "step"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class UiaRequest:
    """Correlate one immutable bounded JSON operation with its completion."""

    identity: str
    action: UiaAction
    encoded: bytes


class UiaProcess(QObject):
    """Own one short-lived MTA helper, killing only that child on deadline.

    A provider may finish an already-dispatched action after a timeout. Failure
    therefore has an unknown native outcome and must stop acquisition; callers
    must not automatically retry movement. Native qualification is opt-in and
    remains disabled by default until preservation tests pass.
    """

    completed = Signal(object, object)  # UiaRequest, bounded decoded JSON object
    failed = Signal(object, str)  # UiaRequest, reason
    busy_changed = Signal(bool)
    MAX_BYTES = 65_536
    DEADLINE_MS = 3000

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        helper_command: Sequence[str] | None = None,
        qualified: bool = False,
    ) -> None:
        """Permit a fixed helper command override for protocol-only fixture tests."""
        super().__init__(parent)
        self._qualified = qualified
        self._command = tuple(helper_command) if helper_command is not None else ()
        self._process: QProcess | None = None
        self._pending: UiaRequest | None = None
        self._output = bytearray()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)

    @property
    def busy(self) -> bool:
        """A terminating child still owns its slot until Qt reports completion."""
        return self._process is not None

    def start(self, action: UiaAction, payload: dict[str, object]) -> UiaRequest:
        """Start one bounded operation; requests never become shell expressions."""
        if self.busy:
            raise CaptureError("The UI Automation helper is still busy")
        if not self._qualified:
            raise CaptureError(
                "Scrolling capture is unavailable until native preservation is verified"
            )
        if not isinstance(action, UiaAction):
            raise TypeError("UI Automation requires a supported action")
        identity = uuid4().hex
        encoded = json.dumps(
            {"request_id": identity, "action": action.value, "payload": payload},
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise CaptureError("UI Automation request exceeds its size limit")
        request = UiaRequest(identity, action, encoded)
        command = self._command or self._default_command()
        process = QProcess(self)
        self._process, self._pending = process, request
        self._output.clear()
        process.started.connect(lambda: self._send(process, request))
        process.readyReadStandardOutput.connect(lambda: self._read(process))
        process.readyReadStandardError.connect(lambda: self._drain_error(process))
        process.errorOccurred.connect(lambda error: self._error(process, error))
        process.finished.connect(
            lambda code, status: self._finished(process, code, status)
        )
        self._timer.start(self.DEADLINE_MS)
        QTimer.singleShot(0, lambda: self._launch(process, request, command))
        return request

    def _launch(
        self, process: QProcess, request: UiaRequest, command: tuple[str, ...]
    ) -> None:
        """Defer startup so callers can reserve request identity before callbacks."""
        if process is self._process and request is self._pending:
            self.busy_changed.emit(self.busy)
            if process is self._process and request is self._pending:
                process.start(command[0], list(command[1:]))

    @staticmethod
    def _default_command() -> tuple[str, ...]:
        """Use Windows' installed host without profile or policy bypass options."""
        if sys.platform != "win32":
            raise CaptureError("Automatic scrolling requires Windows UI Automation")
        system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        host = system / "System32/WindowsPowerShell/v1.0/powershell.exe"
        helper = Path(__file__).with_name("uia_helper.ps1")
        if not host.is_file() or not helper.is_file():
            raise CaptureError("The Windows UI Automation helper is unavailable")
        return (
            str(host),
            "-NoProfile",
            "-NonInteractive",
            "-Mta",
            "-File",
            str(helper),
        )

    def _send(self, process: QProcess, request: UiaRequest) -> None:
        """Send data through stdin only to the original owned child."""
        if process is self._process and request is self._pending:
            process.write(request.encoded)
            process.closeWriteChannel()

    def _read(self, process: QProcess) -> None:
        """Bound accumulated stdout even if an unexpected host floods output."""
        data = process.readAllStandardOutput().data()
        if process is not self._process or self._pending is None:
            return
        if len(self._output) + len(data) > self.MAX_BYTES:
            self._fail("UI Automation returned too much data; capture stopped")
        else:
            self._output.extend(data)

    def _drain_error(self, process: QProcess) -> None:
        """Do not expose provider exception text or accumulate unbounded stderr."""
        if process.readAllStandardError().size() > self.MAX_BYTES:
            self._fail("UI Automation helper exceeded its diagnostic limit")

    def _error(self, process: QProcess, error: QProcess.ProcessError) -> None:
        """Failed startup has no finished signal, so release its ownership here."""
        if process is not self._process:
            return
        if error is QProcess.ProcessError.FailedToStart:
            self._fail("UI Automation helper could not start")
            self._dispose(process)

    def _finished(
        self, process: QProcess, code: int, status: QProcess.ExitStatus
    ) -> None:
        """Reject malformed, foreign, failed or duplicate provider completions."""
        self._read(process)
        request = self._pending
        if process is self._process and request is not None:
            if code != 0 or status is not QProcess.ExitStatus.NormalExit:
                self._fail("UI Automation did not complete; the target may have moved")
            else:
                reply = self._decode(request)
                if reply is not None:
                    self._dispose(process)
                    self.completed.emit(request, reply)
                    return
        self._dispose(process)

    def _decode(self, request: UiaRequest) -> object | None:
        """Admit one correctly correlated JSON object, without trusting its schema."""
        try:
            reply: object = json.loads(self._output.decode("utf-8-sig"))
        except (ValueError, RecursionError):
            self._fail("UI Automation returned an unreadable response")
            return None
        if not isinstance(reply, dict) or reply.get("request_id") != request.identity:
            self._fail("UI Automation returned an unrelated response")
            return None
        if reply.get("ok") is not True:
            self._fail("This scroll area is unavailable or changed; capture stopped")
            return None
        self._pending = None
        self._timer.stop()
        return reply

    def _timeout(self) -> None:
        """A silent/hung provider cannot leave progress permanently active."""
        self._fail(
            "UI Automation timed out; capture stopped and the target may have moved"
        )

    def _fail(self, message: str) -> None:
        """Invalidate before callbacks and kill only this transport's child."""
        request, self._pending = self._pending, None
        self._timer.stop()
        if self._process is not None:
            if self._process.state() is QProcess.ProcessState.NotRunning:
                self._dispose(self._process)
            else:
                self._process.kill()
        if request is not None:
            self.failed.emit(request, message)

    def _dispose(self, process: QProcess) -> None:
        """Keep QProcess alive through its terminal signal before deferred delete."""
        if process is self._process:
            self._process = None
            self._timer.stop()
            self.busy_changed.emit(self.busy)
        process.deleteLater()

    def cancel(self) -> None:
        """Stop future delivery; already-dispatched native movement is uncertain."""
        self._fail("Scrolling capture was cancelled; no further work will be issued")
