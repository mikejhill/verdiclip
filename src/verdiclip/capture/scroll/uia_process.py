"""Deadline-bounded facade with isolated managed and native helper lifetimes."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QObject, Signal
from shiboken6 import isValid

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.capture.scroll.uia_worker import UiaWorkerLease
from verdiclip.exceptions import CaptureError


class _IsolatedScope(QObject):
    """Application-owned barrier surviving the destruction of its GUI facade."""

    completed = Signal(object, object)
    failed = Signal(object, str)
    busy_changed = Signal(bool)
    scope_closed = Signal()
    cleanup_failed = Signal(str)

    def __init__(self) -> None:
        super().__init__(QCoreApplication.instance())
        self._leases: dict[bool, UiaWorkerLease] = {}
        self._retired: set[UiaWorkerLease] = set()
        self._last: UiaWorkerLease | None = None
        self._failed_lease: UiaWorkerLease | None = None
        self._pending: UiaRequest | None = None
        self._cleanup: list[Callable[[], None]] = []
        self._failed_cleanup: list[Callable[[], None]] = []
        self._open = False
        self._closing = False
        self._broken = False
        self._released = False
        self._cancelling = False
        self._finishing = False
        self._failure_cleanup: str | None = None

    @property
    def busy(self) -> bool:
        """Reserve both lanes for one request, closing scope or failed cleanup."""
        return (
            self._pending is not None
            or self._closing
            or self._broken
            or any(lease.busy for lease in self._leases.values())
        )

    @property
    def worker_pids(self) -> tuple[int, ...]:
        """Report every currently owned child, never an inferred descendant."""
        return tuple(
            pid
            for lease in self._leases.values()
            if (pid := lease.worker_pid) is not None and pid > 0
        )

    @property
    def worker_pid(self) -> int | None:
        """Keep last-operation provenance, falling back to another live lease."""
        if self._last is not None and self._last.worker_pid is not None:
            return self._last.worker_pid
        return next(
            (
                lease.worker_pid
                for lease in self._leases.values()
                if lease.worker_pid is not None
            ),
            None,
        )

    @property
    def diagnostics(self) -> WorkerDiagnostics | None:
        """Prefer failed cleanup evidence over the most recent request's trace."""
        lease = self._failed_lease or self._last
        if lease is None:
            return None
        diagnostics = lease.diagnostics
        return (
            replace(diagnostics, cleanup=self._failure_cleanup)
            if self._failure_cleanup is not None
            else diagnostics
        )

    @property
    def diagnostics_for_workers(self) -> dict[str, WorkerDiagnostics]:
        """Retain separate immutable lifecycle snapshots for each created lane."""
        return {
            "native" if native else "managed": lease.diagnostics
            for native, lease in self._leases.items()
        }

    def lease(self, *, native: bool) -> UiaWorkerLease:
        """Create only a direct application-owned lease for the selected lane."""
        if native not in self._leases:
            lease = UiaWorkerLease()
            self._leases[native] = lease
            self._retired.add(lease)  # No child/scope has been admitted yet.
            lease.completed.connect(self._completed)
            lease.failed.connect(self._failed)
            lease.busy_changed.connect(self._busy_changed)
            lease.scope_closed.connect(self._lease_closed)
            lease.cleanup_failed.connect(self._cleanup_failed)
        return self._leases[native]

    def submit(
        self,
        lease: UiaWorkerLease,
        request: UiaRequest,
        command: tuple[str, ...],
        *,
        budgets: tuple[int, int],
        use_local_pipe: bool,
    ) -> None:
        """Reserve globally before a worker can issue synchronous callbacks."""
        if self.busy or self._released:
            raise CaptureError("The UI Automation helpers are busy or closing")
        self._open = True
        if lease in self._retired:
            # Open the worker scope before private-pipe admission. Its existing
            # close path can then prove even pre-child refusal was cleaned.
            lease.register_cleanup(self._cleanup_marker)
        self._retired.discard(lease)
        self._last = lease
        self._pending = request
        try:
            lease.submit(
                request,
                command,
                deadline_ms=budgets[0],
                startup_ms=budgets[1],
                use_local_pipe=use_local_pipe,
            )
        except Exception:
            if self._pending is request:
                self._pending = None
            self._finish_scope()
            raise

    def _completed(self, request: UiaRequest, reply: object) -> None:
        if request is self._pending:
            self._pending = None
            self.completed.emit(request, reply)

    def _failed(self, request: UiaRequest, message: str) -> None:
        if request is self._pending:
            self._pending = None
            self.failed.emit(request, message)
            self._finish_scope()

    @staticmethod
    def _cleanup_marker() -> None:
        """A worker-owned no-op makes pre-launch scope retirement observable."""

    def _busy_changed(self, _busy: object) -> None:
        self.busy_changed.emit(self.busy)

    def _cleanup_failed(self, message: str) -> None:
        self._broken = True
        sender = self.sender()
        if isinstance(sender, UiaWorkerLease):
            self._failed_lease = sender
        self._failure_cleanup = "unconfirmed"
        self.cleanup_failed.emit(message)
        self.busy_changed.emit(self.busy)

    def _lease_closed(self) -> None:
        lease = self.sender()
        if not isinstance(lease, UiaWorkerLease) or lease not in self._leases.values():
            return
        if (
            lease.busy
            or lease.worker_pid is not None
            or lease.diagnostics.cleanup != "confirmed"
        ):
            self._broken = True
            self._failed_lease = lease
            self._failure_cleanup = "unconfirmed"
            self.cleanup_failed.emit("UI Automation worker cleanup is unconfirmed")
            return
        self._retired.add(lease)
        self._finish_scope()

    def register_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Retain shared frames until both owned helper scopes have retired."""
        if self._closing or self._broken or self._released:
            raise CaptureError("UI Automation scope cleanup is already pending")
        self._cleanup.append(cleanup)
        self._open = True

    def _finish_scope(self) -> None:
        if (
            self._finishing
            or self._cancelling
            or self._pending is not None
            or not self._open
            or any(lease not in self._retired for lease in self._leases.values())
        ):
            return
        self._finishing = True
        self._closing = True
        self._open = False
        callbacks, self._cleanup = self._cleanup, []
        controls: list[BaseException] = []
        for callback in callbacks:
            try:
                UiaWorkerLease._run_cleanup(callback)
            except BaseException as error:  # noqa: BLE001 - defer controls until every private cleanup is attempted
                self._broken = True
                self._failure_cleanup = "callback_failed"
                self._failed_cleanup.append(callback)
                if not isinstance(error, Exception):
                    controls.append(error)
                self.cleanup_failed.emit(str(error))
        self._closing = False
        self._finishing = False
        if not self._broken:
            self.scope_closed.emit()
        self.busy_changed.emit(self.busy)
        if len(controls) == 1:
            raise controls[0]
        if controls:
            raise BaseExceptionGroup("Capture cleanup controls", controls)
        if self._released and not self._broken:
            self.deleteLater()

    def cancel(self) -> None:
        """Stop both existing children; never create a helper to cancel another."""
        if self._cancelling or self._finishing:
            return
        self._closing = True
        self._cancelling = True
        try:
            for lease in tuple(self._leases.values()):
                self._retire_lease(lease, release=False)
        finally:
            self._cancelling = False
        self._finish_scope()
        if not self._open and not self._broken:
            self._closing = False
        self.busy_changed.emit(self.busy)

    def _retire_lease(self, lease: UiaWorkerLease, *, release: bool) -> None:
        if not isValid(lease) and lease in self._retired:
            return
        try:
            if release:
                lease.release()
            else:
                lease.cancel()
        except (CaptureError, RuntimeError, OSError):
            self._broken = True
            self._failure_cleanup = "unconfirmed"
            self._failed_lease = lease
            self.cleanup_failed.emit("UI Automation worker cancellation is unconfirmed")

    def release(self) -> None:
        """Keep the application-owned barrier alive until both leases retire."""
        self._released = True
        self.cancel()
        for lease in tuple(self._leases.values()):
            self._retire_lease(lease, release=True)
        if not self._open and not self._broken:
            self.deleteLater()

    def shutdown(self, *, timeout_ms: int) -> None:
        """Share one finite wait budget across both already-owned children."""
        deadline = monotonic() + timeout_ms / 1000
        self._released = True
        errors: list[BaseException] = []
        try:
            self.cancel()
        except BaseException as error:  # noqa: BLE001 - defer controls until both owned lanes are attempted
            errors.append(error)
        for lease in tuple(self._leases.values()):
            if not isValid(lease):
                if lease not in self._retired:
                    errors.append(CaptureError("Worker ownership was lost"))
                continue
            try:
                lease.shutdown(timeout_ms=max(0, int((deadline - monotonic()) * 1000)))
            except BaseException as error:  # noqa: BLE001 - independently attempt the other owned lane
                errors.append(error)
        if errors or self.busy or self.worker_pids:
            self._broken = True
            self._failure_cleanup = "unconfirmed"
            if any(not isinstance(error, Exception) for error in errors):
                if len(errors) == 1:
                    raise errors[0]
                raise BaseExceptionGroup("Owned worker shutdown failures", errors)
            raise CaptureError("UI Automation shutdown cleanup is unconfirmed") from (
                BaseExceptionGroup("Owned worker shutdown failures", errors)
                if errors
                else None
            )


class UiaProcess(QObject):
    """Keep managed/native helpers isolated without coupling either to a widget.

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
        native_helper_command: Sequence[str] | None = None,
        qualified: bool = False,
        use_local_pipe: bool | None = None,
    ) -> None:
        """Allow a fixed protocol fixture command without native desktop execution."""
        super().__init__(parent)
        self._qualified = qualified
        self._command = tuple(helper_command) if helper_command is not None else ()
        self._native_command = (
            tuple(native_helper_command) if native_helper_command is not None else ()
        )
        if native_helper_command is not None and not self._native_command:
            raise ValueError("An explicit native helper command cannot be empty")
        self._isolated = helper_command is None or native_helper_command is not None
        self._use_pipe = (
            helper_command is None if use_local_pipe is None else use_local_pipe
        )
        self._lease: UiaWorkerLease | None = None
        self._scope: _IsolatedScope | None = None
        self._shutdown = False

    @property
    def busy(self) -> bool:
        """A warm idle helper is reusable; a terminating slot remains reserved."""
        if self._isolated:
            return self._scope is not None and self._scope.busy
        return self._lease is not None and self._lease.busy

    @property
    def worker_pid(self) -> int | None:
        """Expose only the owned helper PID for lifetime/provenance checks."""
        if self._isolated:
            return self._scope.worker_pid if self._scope is not None else None
        return self._lease.worker_pid if self._lease is not None else None

    @property
    def worker_pids(self) -> tuple[int, ...]:
        """Expose both owned lanes so cleanup checks cannot miss a warm helper."""
        if self._isolated:
            return self._scope.worker_pids if self._scope is not None else ()
        pid = self.worker_pid
        return (pid,) if pid is not None and pid > 0 else ()

    @property
    def diagnostics(self) -> WorkerDiagnostics | None:
        """Expose bounded phase evidence, including after confirmed retirement."""
        if self._isolated:
            return self._scope.diagnostics if self._scope is not None else None
        return self._lease.diagnostics if self._lease is not None else None

    @property
    def diagnostics_for_workers(self) -> dict[str, WorkerDiagnostics]:
        """Distinguish isolated lanes from an explicitly shared protocol fixture."""
        if self._isolated:
            return (
                self._scope.diagnostics_for_workers if self._scope is not None else {}
            )
        return {"shared": self._lease.diagnostics} if self._lease is not None else {}

    def start(self, action: UiaAction, payload: dict[str, object]) -> UiaRequest:
        """Reserve one request before asynchronous startup/provider callbacks."""
        if self.busy:
            raise CaptureError("The UI Automation helper is still busy")
        if self._shutdown:
            raise CaptureError("UI Automation is shut down")
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
        native = self._isolated and action in {UiaAction.STEP, UiaAction.RESTORE}
        command = (
            self._native_command or (*self._default_command(), "-NativeOnly")
            if native
            else self._command or self._default_command()
        )
        lease = self._get_lease(native=native)
        if self._isolated:
            self._get_scope().submit(
                lease,
                request,
                command,
                budgets=(self.DEADLINE_MS, self.STARTUP_MS),
                use_local_pipe=self._use_pipe,
            )
        else:
            lease.submit(
                request,
                command,
                deadline_ms=self.DEADLINE_MS,
                startup_ms=self.STARTUP_MS,
                use_local_pipe=self._use_pipe,
            )
        return request

    def _get_lease(self, *, native: bool = False) -> UiaWorkerLease:
        """Only an application event loop may own asynchronous process resources."""
        if QCoreApplication.instance() is None:
            raise CaptureError("UI Automation requires an application event loop")
        if self._isolated:
            return self._get_scope().lease(native=native)
        if self._lease is None:
            self._lease = UiaWorkerLease()
            self._lease.completed.connect(self.completed)
            self._lease.failed.connect(self.failed)
            self._lease.busy_changed.connect(self.busy_changed)
            self._lease.scope_closed.connect(self.scope_closed)
            self._lease.cleanup_failed.connect(self.cleanup_failed)
            self.destroyed.connect(self._lease.release)
        return self._lease

    def _get_scope(self) -> _IsolatedScope:
        if QCoreApplication.instance() is None:
            raise CaptureError("UI Automation requires an application event loop")
        if self._scope is None:
            self._scope = _IsolatedScope()
            self._scope.completed.connect(self.completed)
            self._scope.failed.connect(self.failed)
            self._scope.busy_changed.connect(self.busy_changed)
            self._scope.scope_closed.connect(self.scope_closed)
            self._scope.cleanup_failed.connect(self.cleanup_failed)
            self.destroyed.connect(self._scope.release)
        return self._scope

    def register_scope_cleanup(self, cleanup: Callable[[], None]) -> None:
        """Bind private file cleanup to actual worker termination, not GUI lifetime."""
        if self._isolated:
            self._get_scope().register_cleanup(cleanup)
        else:
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
        if self._isolated and self._scope is not None:
            self._scope.cancel()
        elif self._lease is not None:
            self._lease.cancel()

    def shutdown(self, *, timeout_ms: int = UiaWorkerLease.CLEANUP_MS) -> None:
        """Permanently stop admission and retire existing helpers only."""
        if timeout_ms < 0:
            raise ValueError("Shutdown timeout cannot be negative")
        self._shutdown = True
        if self._isolated and self._scope is not None:
            self._scope.shutdown(timeout_ms=timeout_ms)
        elif self._lease is not None:
            self._lease.shutdown(timeout_ms=timeout_ms)
