"""Real QtCore ownership/signals with fake leases; no GUI/native helper/process.

Run directly with Python, not pytest's QApplication/root clipboard fixtures.
No QProcess, widget, native API, WTS, network or desktop operation is performed.
"""
# ruff: noqa: PT009, PT027 - standalone Core-only unittest avoids GUI conftest

from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar, override
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from shiboken6 import isValid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from verdiclip.capture.scroll.uia_diagnostics import WorkerDiagnostics
from verdiclip.capture.scroll.uia_messages import UiaAction, UiaRequest
from verdiclip.exceptions import CaptureError

if TYPE_CHECKING:
    from verdiclip.capture.scroll.uia_process import UiaProcess

if __name__ != "__main__":
    import pytest

    pytestmark = pytest.mark.usefixtures("qapp")


def application(*, collected: bool) -> QCoreApplication:
    """Never create Core before QApplication when collected by the GUI suite."""
    app = QCoreApplication.instance()
    if collected:
        if app is None or not app.inherits("QApplication"):
            raise RuntimeError("Collected routing tests require the qapp fixture")
        return app
    return app or QCoreApplication([])


class FakeLease(QObject):
    """Qt signals/parent lifetime are real; helper requests are in-memory only."""

    completed = Signal(object, object)
    failed = Signal(object, str)
    busy_changed = Signal(bool)
    scope_closed = Signal()
    cleanup_failed = Signal(str)
    instances: ClassVar[list[FakeLease]] = []

    def __init__(self) -> None:
        super().__init__(QCoreApplication.instance())
        self.pending: UiaRequest | None = None
        self.closing = False
        self.worker_pid: int | None = None
        self.calls: list[tuple[UiaRequest, tuple[str, ...], int, int, bool]] = []
        self.cancelled = 0
        self.released = 0
        self.auto_retire = False
        self.reject_submit = False
        self.reject_cancel = False
        self.callbacks = []
        self._diagnostics = WorkerDiagnostics(
            phase="idle",
            process_state="NotRunning",
            cleanup="confirmed",
            request_sent=False,
            events=(),
            dropped_events=0,
            stdout_bytes=0,
            stderr_bytes=0,
            pipe_bytes=0,
            max_deadline_lateness_ms=0,
        )
        self.instances.append(self)

    @property
    def diagnostics(self) -> WorkerDiagnostics:
        """Expose the same immutable content-free public diagnostics contract."""
        return self._diagnostics

    @property
    def busy(self) -> bool:
        """Fake operation/retirement occupancy without an OS process."""
        return self.pending is not None or self.closing

    def submit(
        self,
        request: UiaRequest,
        command: tuple[str, ...],
        *,
        deadline_ms: int,
        startup_ms: int,
        use_local_pipe: bool,
    ) -> None:
        """Record routing, flags and original budgets without launching anything."""
        if self.busy:
            raise CaptureError("Fake occupied lease")
        if self.reject_submit:
            self.retire()
            raise CaptureError("Fake admission failure")
        self.pending = request
        self.worker_pid = 1000 + self.instances.index(self)
        self._diagnostics = replace(
            self._diagnostics, cleanup="open", process_state="Running"
        )
        self.calls.append((request, command, deadline_ms, startup_ms, use_local_pipe))
        active = True
        self.busy_changed.emit(active)

    def finish(self) -> None:
        """Simulate a valid reply, including the worker's later busy emission."""
        request, self.pending = self.pending, None
        if request is None:
            raise AssertionError("No fake request")
        self.completed.emit(request, {"request_id": request.identity, "ok": True})
        self.busy_changed.emit(self.busy)

    def register_cleanup(self, callback: object) -> None:
        """Legacy shared fixtures retain their original callback registration."""
        self.callbacks.append(callback)

    @staticmethod
    def _run_cleanup(callback: object) -> None:
        """Normalize fake callback failures using the worker's existing policy."""
        if not callable(callback):
            raise TypeError("Cleanup callback required")
        try:
            callback()
        except (CaptureError, OSError):
            raise
        except Exception as error:
            raise CaptureError("Fake callback failed") from error

    def cancel(self) -> None:
        """Reserve fake retirement; never create or kill a real process."""
        self.cancelled += 1
        if self.reject_cancel:
            raise RuntimeError("Fake cancellation failure")
        request, self.pending = self.pending, None
        self.closing = self.worker_pid is not None
        if request is not None:
            self.failed.emit(request, "Fake cancelled")
        if self.worker_pid is None or self.auto_retire:
            self.retire()
        self.busy_changed.emit(self.busy)

    def retire(self, *, cleanup: str = "confirmed") -> None:
        """Supply an explicit fake termination receipt through a real Qt signal."""
        self.worker_pid = None
        self.closing = False
        self._diagnostics = replace(
            self._diagnostics, cleanup=cleanup, process_state="NotRunning"
        )
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            self._run_cleanup(callback)
        self.scope_closed.emit()
        self.busy_changed.emit(self.busy)

    def release(self) -> None:
        """Model application-owned lease release after facade destruction."""
        self.released += 1
        self.cancel()


class FacadeModule(ModuleType):
    """Type the dynamically isolated facade class without an unused cast."""

    UiaProcess: type[UiaProcess]


def subject(worker_type: type[QObject] = FakeLease) -> FacadeModule:
    """Load the real facade against fake leases without importing uia_worker."""
    fake = ModuleType("verdiclip.capture.scroll.uia_worker")
    attribute = "UiaWorkerLease"
    setattr(fake, attribute, worker_type)
    path = (
        Path(__file__).resolve().parents[2]
        / "src/verdiclip/capture/scroll/uia_process.py"
    )
    spec = importlib.util.spec_from_file_location("_uia_process_routing_subject", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Missing facade source")
    module = FacadeModule(spec.name)
    module.__dict__.update(importlib.util.module_from_spec(spec).__dict__)
    with patch.dict(sys.modules, {fake.__name__: fake}):
        spec.loader.exec_module(module)
    return module


class RoutingTests(unittest.TestCase):
    """Real QtCore resource lifecycle; fake provider, process and deadline events."""

    @override
    def setUp(self) -> None:
        """Create only a Core application; load no GUI application or QPA plugin."""
        self.app = application(collected=__name__ != "__main__")
        FakeLease.instances = []
        self.module = subject()
        self.default_patch = patch.object(
            self.module.UiaProcess,
            "_default_command",
            return_value=("fixed-host", "-Mta", "-File", "fixed-helper"),
        )
        self.default_patch.start()
        self.addCleanup(self.default_patch.stop)

    @override
    def tearDown(self) -> None:
        """Retire only fake QObject instances; no real process/API cleanup exists."""
        for lease in FakeLease.instances:
            if isValid(lease):
                lease.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def client(self) -> UiaProcess:
        """Return a qualified facade while keeping default command routing."""
        return self.module.UiaProcess(qualified=True)

    def warm_both(self, client: UiaProcess) -> tuple[FakeLease, FakeLease]:
        """Complete managed and native fake requests without retiring workers."""
        client.start(UiaAction.INSPECT, {})
        managed = FakeLease.instances[0]
        managed.finish()
        client.start(UiaAction.STEP, {})
        native = FakeLease.instances[1]
        native.finish()
        return managed, native

    def test_default_actions_use_distinct_direct_leases_and_flags(self) -> None:
        """Only STEP/RESTORE use NativeOnly; neither operation budget changes."""
        client = self.client()
        managed, native = self.warm_both(client)
        for action in (UiaAction.OBSERVE, UiaAction.SAMPLE, UiaAction.RESTORE):
            client.start(action, {})
            (native if action is UiaAction.RESTORE else managed).finish()
        self.assertEqual(len(FakeLease.instances), 2)
        self.assertTrue(all("-NativeOnly" not in call[1] for call in managed.calls))
        self.assertTrue(all(call[1][-1] == "-NativeOnly" for call in native.calls))
        self.assertTrue(
            all(
                call[2:4] == (3000, 8000)
                for lease in (managed, native)
                for call in lease.calls
            )
        )
        self.assertIs(managed.parent(), self.app)
        self.assertIs(native.parent(), self.app)
        self.assertEqual(set(client.diagnostics_for_workers), {"managed", "native"})
        self.assertEqual(
            set(client.worker_pids), {managed.worker_pid, native.worker_pid}
        )

    def test_one_global_operation_and_reentrant_busy_aggregation(self) -> None:
        """A completion callback may switch lanes; the old False cannot clear it."""
        client = self.client()
        observed = []
        client.busy_changed.connect(observed.append)
        client.start(UiaAction.INSPECT, {})
        with self.assertRaises(CaptureError):
            client.start(UiaAction.STEP, {})
        client.completed.connect(lambda *_args: client.start(UiaAction.STEP, {}))
        FakeLease.instances[0].finish()
        self.assertTrue(client.busy)
        self.assertTrue(observed[-1])
        self.assertEqual(len(FakeLease.instances), 2)

    def test_cancel_defers_shared_cleanup_until_both_retired(self) -> None:
        """A per-worker scope receipt cannot release shared frame ownership."""
        client = self.client()
        managed, native = self.warm_both(client)
        cleaned = []
        closed = []
        client.register_scope_cleanup(lambda: cleaned.append(True))
        client.scope_closed.connect(lambda: closed.append(True))
        client.cancel()
        managed.retire()
        self.assertEqual(cleaned, [])
        self.assertEqual(closed, [])
        self.assertTrue(client.busy)
        self.assertEqual(client.worker_pid, native.worker_pid)
        native.retire()
        self.assertEqual((cleaned, closed), ([True], [True]))
        self.assertFalse(client.busy)
        self.assertIsNone(client.worker_pid)
        self.assertEqual(client.worker_pids, ())
        client.cancel()
        self.assertEqual(cleaned, [True])

    def test_facade_deletion_keeps_application_owned_cleanup_barrier(self) -> None:
        """Real deferred QObject destruction releases both fake leases safely."""
        client = self.client()
        managed, native = self.warm_both(client)
        cleaned = []
        client.register_scope_cleanup(lambda: cleaned.append(True))
        scope = client._scope
        client.deleteLater()
        QCoreApplication.sendPostedEvents(client, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(client))
        self.assertTrue(isValid(scope))
        self.assertEqual((managed.released, native.released), (1, 1))
        managed.retire()
        self.assertEqual(cleaned, [])
        native.retire()
        self.assertEqual(cleaned, [True])
        QCoreApplication.sendPostedEvents(scope, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(scope))

    def test_cleanup_failure_never_emits_false_global_success(self) -> None:
        """Even a worker's later closed signal cannot erase known cleanup loss."""
        client = self.client()
        managed, native = self.warm_both(client)
        cleaned = []
        closed = []
        failures = []
        client.register_scope_cleanup(lambda: cleaned.append(True))
        client.scope_closed.connect(lambda: closed.append(True))
        client.cleanup_failed.connect(failures.append)
        client.cancel()
        native.cleanup_failed.emit("fake ownership loss")
        managed.retire()
        native.retire()
        self.assertEqual(cleaned, [True])
        self.assertEqual(closed, [])
        self.assertTrue(client.busy)
        diagnostics = client.diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics.cleanup, "unconfirmed")
        self.assertTrue(failures)

    def test_custom_command_preserves_single_lease_unless_explicit_native_command(
        self,
    ) -> None:
        """Protocol fixtures remain shared unless the caller opts into isolation."""
        client = self.module.UiaProcess(helper_command=("fixture",), qualified=True)
        client.start(UiaAction.INSPECT, {})
        lease = FakeLease.instances[0]
        lease.finish()
        client.start(UiaAction.STEP, {})
        lease.finish()
        self.assertEqual(len(FakeLease.instances), 1)
        self.assertTrue(all(call[1] == ("fixture",) for call in lease.calls))
        explicit = self.module.UiaProcess(
            helper_command=("managed-fixture",),
            native_helper_command=("native-fixture",),
            qualified=True,
        )
        explicit.start(UiaAction.STEP, {})
        self.assertEqual(FakeLease.instances[-1].calls[-1][1], ("native-fixture",))

    def test_cleanup_blocks_reentrancy_but_closed_allows_new_scope(
        self,
    ) -> None:
        """Shared cleanup stays reserved; success exposes a new request slot."""
        client = self.client()
        managed, native = self.warm_both(client)
        blocked = []

        def cleanup() -> None:
            client.cancel()  # Cannot clear the enclosing finishing reservation.
            with self.assertRaises(CaptureError):
                client.start(UiaAction.OBSERVE, {})
            with self.assertRaises(CaptureError):
                client.register_scope_cleanup(lambda: None)
            blocked.append("cleanup")

        def closed() -> None:
            client.start(UiaAction.STEP, {})
            blocked.append("closed")

        client.register_scope_cleanup(cleanup)
        client.scope_closed.connect(closed)
        managed.retire()
        native.retire()
        self.assertEqual(blocked, ["cleanup", "closed"])
        self.assertTrue(client.busy)
        self.assertIsNotNone(native.pending)

    def test_cleanup_callback_failure_is_sticky_and_callbacks_are_not_retried(
        self,
    ) -> None:
        """One failed shared callback cannot skip independent cleanup or pass."""
        client = self.client()
        managed, native = self.warm_both(client)
        calls = []
        closed = []

        def fail() -> None:
            calls.append("failed")
            raise OSError("synthetic callback failure")

        client.register_scope_cleanup(fail)
        client.register_scope_cleanup(lambda: calls.append("other"))
        client.scope_closed.connect(lambda: closed.append(True))
        client.cancel()
        managed.retire()
        native.retire()
        client.cancel()
        self.assertEqual(calls, ["failed", "other"])
        self.assertEqual(closed, [])
        self.assertTrue(client.busy)
        diagnostics = client.diagnostics
        assert diagnostics is not None
        self.assertEqual(diagnostics.cleanup, "callback_failed")

    def test_submit_failure_clears_global_pending_after_real_closed_signal(
        self,
    ) -> None:
        """Synchronous per-lease admission retirement cannot strand global busy."""
        client = self.client()
        cleaned = []
        client.register_scope_cleanup(lambda: cleaned.append(True))
        client._get_lease(native=True)
        FakeLease.instances[0].reject_submit = True
        with self.assertRaises(CaptureError):
            client.start(UiaAction.STEP, {})
        self.assertFalse(client.busy)
        self.assertEqual(cleaned, [True])

    def test_cancel_exception_attempts_other_lease_and_retains_failed_scope(
        self,
    ) -> None:
        """A failed cancellation cannot prevent retiring the independent helper."""
        client = self.client()
        managed, native = self.warm_both(client)
        managed.reject_cancel = True
        client.cancel()
        self.assertEqual((managed.cancelled, native.cancelled), (1, 1))
        self.assertTrue(client.busy)
        self.assertEqual(
            set(client.worker_pids), {managed.worker_pid, native.worker_pid}
        )

    def test_collected_guard_cannot_create_core_before_qapp(self) -> None:
        """Missing suite fixture fails before creating any replacement application."""
        with (
            patch.object(QCoreApplication, "instance", return_value=None),
            self.assertRaises(RuntimeError),
        ):
            application(collected=True)

    def test_real_worker_prechild_pipe_refusal_retains_confirmed_vs_unknown(
        self,
    ) -> None:
        """Real admission code uses a fake pipe and cannot create a QProcess."""
        from verdiclip.capture.scroll import (  # noqa: PLC0415 - real worker only in admission regression
            uia_worker,
        )

        for broken_close in (False, True):

            class RejectedPipe(QObject):
                fail_close: ClassVar[bool] = broken_close

                def listen(self) -> None:
                    raise CaptureError("Fake pipe listen refusal")

                def close(self) -> None:
                    if self.fail_close:
                        raise RuntimeError("Fake pipe close failure")

            module = subject(uia_worker.UiaWorkerLease)
            cleaned = []
            closed = []
            with (
                patch.object(uia_worker, "UiaPipe", RejectedPipe),
                patch.object(
                    uia_worker.UiaWorkerLease,
                    "_new_process",
                    side_effect=AssertionError(
                        "Pre-child refusal cannot launch a process"
                    ),
                ),
                patch.object(
                    module.UiaProcess, "_default_command", return_value=("fixed-host",)
                ),
            ):
                client = module.UiaProcess(qualified=True)
                client.register_scope_cleanup(
                    lambda cleaned=cleaned: cleaned.append(True)
                )
                client.scope_closed.connect(lambda closed=closed: closed.append(True))
                with self.assertRaises(CaptureError):
                    client.start(UiaAction.INSPECT, {})
                if broken_close:
                    self.assertTrue(client.busy)
                    self.assertEqual((cleaned, closed), ([], []))
                else:
                    self.assertFalse(client.busy)
                    self.assertEqual((cleaned, closed), ([True], [True]))
                client.deleteLater()
                QCoreApplication.sendPostedEvents(client, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
