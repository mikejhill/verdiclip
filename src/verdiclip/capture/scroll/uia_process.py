"""GUI facade for a deadline-bounded, reusable MTA process lease."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, Signal

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_worker import UiaWorkerLease
from verdiclip.exceptions import CaptureError


class UiaProcess(QObject):
    """Reuse one helper during explicit capture without coupling it to a widget.

    Destroying this facade releases its application-owned lease. Native actions
    already dispatched may finish after cancellation, so no failed movement is
    automatically retried. Native launch stays unqualified by default.
    """

    completed = Signal(object, object)
    failed = Signal(object, str)
    busy_changed = Signal(bool)
    scope_closed = Signal()
    cleanup_failed = Signal(str)
    MAX_BYTES = 65_536
    DEADLINE_MS = 3000
    STARTUP_MS = 8000

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        helper_command: Sequence[str] | None = None,
        qualified: bool = False,
        use_local_pipe: bool | None = None,
    ) -> None:
        """Allow a fixed protocol fixture command without native desktop execution."""
        super().__init__(parent)
        self._qualified = qualified
        self._command = tuple(helper_command) if helper_command is not None else ()
        self._use_pipe = (
            helper_command is None if use_local_pipe is None else use_local_pipe
        )
        self._lease: UiaWorkerLease | None = None

    @property
    def busy(self) -> bool:
        """A warm idle helper is reusable; a terminating slot remains reserved."""
        return self._lease is not None and self._lease.busy

    @property
    def worker_pid(self) -> int | None:
        """Expose only the owned helper PID for lifetime/provenance checks."""
        return self._lease.worker_pid if self._lease is not None else None

    @property
    def diagnostics(self) -> WorkerDiagnostics | None:
        """Expose bounded phase evidence, including after confirmed retirement."""
        return self._lease.diagnostics if self._lease is not None else None

    def start(self, action: UiaAction, payload: dict[str, object]) -> UiaRequest:
        """Reserve one request before asynchronous startup/provider callbacks."""
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
        lease = self._get_lease()
        lease.submit(
            request,
            command,
            deadline_ms=self.DEADLINE_MS,
            startup_ms=self.STARTUP_MS,
            use_local_pipe=self._use_pipe,
        )
        return request

    def _get_lease(self) -> UiaWorkerLease:
        """Only an application event loop may own asynchronous process resources."""
        if QCoreApplication.instance() is None:
            raise CaptureError("UI Automation requires an application event loop")
        if self._lease is None:
            self._lease = UiaWorkerLease()
            self._lease.completed.connect(self.completed)
            self._lease.failed.connect(self.failed)
            self._lease.busy_changed.connect(self.busy_changed)
            self._lease.scope_closed.connect(self.scope_closed)
            self._lease.cleanup_failed.connect(self.cleanup_failed)
            self.destroyed.connect(self._lease.release)
        return self._lease

    def register_scope_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Bind private file cleanup to actual worker termination, not GUI lifetime."""
        self._get_lease().register_cleanup(cleanup)

    @staticmethod
    def _default_command() -> tuple[str, ...]:
        """Use the installed Windows host without profile or policy bypass flags."""
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

    def cancel(self) -> None:
        """End only this helper scope, preserving the facade for safe retry."""
        if self._lease is not None:
            self._lease.cancel()
