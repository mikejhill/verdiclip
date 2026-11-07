"""Isolated lifecycle-thread experiment, never product transport.

Reuses the inert fragmented Session's identity/EOF/retirement gates. Synthetic
pre/post-launch stalls release the GIL; they do NOT make CreateProcessW
interruptible or prove bounded retirement of a genuinely stuck native call.
Run under an external process watchdog. No QThread termination or GUI waits.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast, override

from scripts.owned_pipe_smoke import GapStats, Message, ProtocolError, Session

if TYPE_CHECKING:
    from types import TracebackType

    from PySide6.QtCore import QCoreApplication

CASES = (
    "normal",
    "cancel-before",
    "cancel-pre-create",
    "cancel-post-create",
    "timeout-post-create",
)
STALL_SECONDS = 0.5
MAX_DIAGNOSTIC_PHASES = 64
MAX_DIAGNOSTIC_ERRORS = 8


class Diagnostics:
    """Persist bounded phases/errors because the owned child has no inherited stderr."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.lock = threading.Lock()
        self.phases: list[str] = []
        self.errors: list[str] = []

    def record(self, phase: str, error: str | None = None) -> None:
        """Retain at most 64 phases and 8 bounded errors, independent of success."""
        with self.lock:
            if len(self.phases) < MAX_DIAGNOSTIC_PHASES:
                self.phases.append(phase[:128])
            if error is not None and len(self.errors) < MAX_DIAGNOSTIC_ERRORS:
                self.errors.append(error[:4096])
            temporary = self.output / "diagnostic.tmp"
            temporary.write_text(
                json.dumps({"phases": self.phases, "errors": self.errors}),
                encoding="utf-8",
            )
            temporary.replace(self.output / "diagnostic.json")


def exit_after_retirement(gate: CompletionGate) -> None:
    """Exit the GUI loop only after both proofs; do not ask windows to close."""
    from PySide6.QtCore import QCoreApplication  # noqa: PLC0415

    gate.complete(lambda: QCoreApplication.exit(0))


class LaunchControl:
    """Only cancellation and immutable timestamp snapshots cross thread ownership."""

    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.marks: dict[str, int] = {}
        self.stall_witnesses: dict[str, int] = {"heartbeat": 0, "paint": 0}
        self.pending = False
        self.returned = False
        self.started = False

    def mark(self, name: str) -> None:
        """Record fixed experiment transitions once."""
        with self.lock:
            self.marks.setdefault(name, time.perf_counter_ns())

    def cancel(self) -> None:
        """Latch without waiting for the lifecycle thread's event loop."""
        self.mark("cancel_requested")
        self.cancelled.set()

    def admit(self) -> None:
        """Reject queued ready/send work after cancellation, even mid-callback."""
        if self.cancelled.is_set():
            self.mark("cancel_observed")
            raise ProtocolError("cancelled")

    def launch(
        self,
        start: Callable[[], None],
        case: str,
        delay: Callable[[float], None] = time.sleep,
    ) -> None:
        """Keep pending status through native return and the synthetic return stall."""
        self.pending = True
        self.mark("launch_entered")
        try:
            self.admit()
            if case == "cancel-pre-create":
                self.mark("pre_create_stall")
                delay(STALL_SECONDS)
            self.admit()
            self.started = True
            self.mark("native_entered")
            start()
            self.mark("native_returned")
            if case in ("cancel-post-create", "timeout-post-create"):
                self.mark("post_create_stall")
                delay(3.2 if case == "timeout-post-create" else STALL_SECONDS)
            self.admit()
        finally:
            self.returned = True
            self.pending = False
            self.mark("launch_returned")

    def witness(self, kind: str) -> None:
        """Count GUI dispatches strictly inside the controlled launch stall."""
        if kind not in ("heartbeat", "paint"):
            raise ValueError("Unknown GUI witness")
        with self.lock:
            if (
                "pre_create_stall" in self.marks or "post_create_stall" in self.marks
            ) and ("launch_returned" not in self.marks):
                self.stall_witnesses[kind] += 1

    def witnesses(self) -> dict[str, int]:
        """Return a fixed-size snapshot after worker retirement."""
        with self.lock:
            return dict(self.stall_witnesses)

    def snapshot(self) -> dict[str, int]:
        """Copy observations without handing mutable shared state to Qt signals."""
        with self.lock:
            return dict(self.marks)


def case_observed(case: str, control: LaunchControl, paints: int) -> bool:
    """Require cancellation inside the selected stall, not a lucky earlier cancel."""
    if control.pending or not control.returned:
        return False
    marks = control.snapshot()
    if case == "normal":
        return control.started and paints > 0
    if case == "cancel-before":
        return not control.started
    witnesses = control.witnesses()
    if not all(witnesses.values()):
        return False
    if case == "timeout-post-create":
        return control.started and paints > 0
    phase = "pre_create_stall" if case == "cancel-pre-create" else "post_create_stall"
    return (
        paints > 0
        and control.started == (case == "cancel-post-create")
        and phase in marks
        and "cancel_requested" in marks
        and marks[phase] <= marks["cancel_requested"] < marks["launch_returned"]
    )


class CompletionGate:
    """GUI accepts one generation's terminal receipt and retirement notification."""

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.receipt: str | None = None
        self.thread_finished = False
        self.delivered = False

    def accept(self, generation: int, receipt: str) -> bool:
        """Reject stale/duplicate receipts before any completion notification."""
        if generation != self.generation or self.receipt is not None or self.delivered:
            return False
        self.receipt = receipt
        return True

    def complete(self, notify: Callable[[], None]) -> None:
        """Latch before notifying so reentrant cancellation/finish cannot repeat it."""
        if self.receipt is not None and self.thread_finished and not self.delivered:
            self.delivered = True
            notify()


class WorkerSession(Session):
    """Fixed fragmented transport, with launch cancellation checked at admissions."""

    def __init__(
        self,
        owner: QCoreApplication,
        directory: Path,
        control: LaunchControl,
        experiment: str,
    ) -> None:
        self.control, self.experiment = control, experiment
        super().__init__(owner, "fragmented", directory)

    @override
    def _start(self) -> None:
        try:
            self.control.launch(super()._start, self.experiment)
            self._check_operation_deadline()
        except ProtocolError as error:
            self._fail(error.code)
            if not self.control.started:
                self.helper.close()
            self.poll_timer.start()
            self.deadline_timer.start()

    @override
    def _check_operation_deadline(self) -> None:
        self.control.admit()
        super()._check_operation_deadline()

    @override
    def _connected(self) -> None:
        if self.control.cancelled.is_set():
            self._fail("cancelled")
            return
        super()._connected()

    @override
    def _message(self, message: Message) -> None:
        self.control.admit()
        super()._message(message)

    @override
    def _poll(self) -> None:
        if self.control.cancelled.is_set() and not self.done:
            self.control.mark("cancel_observed")
            self._fail("cancelled")
        super()._poll()

    @override
    def _fail(self, reason: str) -> None:
        requested = self.control.snapshot().get("cancel_requested")
        if requested is not None:
            self.latency.marks.setdefault("cancel_requested", requested)
        super()._fail(reason)

    @override
    def _finish(self) -> None:
        if self.control.pending or not self.control.returned:
            raise RuntimeError("Cannot finish while launch outcome remains pending")
        super()._finish()


def terminal_receipt(
    session: WorkerSession, control: LaunchControl, case: str
) -> dict[str, object]:
    """Publish terminal evidence without laundering an uncertain native outcome."""
    result = session.result()
    if case != "normal":
        expected = "startup-timeout" if case == "timeout-post-create" else "cancelled"
        result["passed"] = (
            session.failure == expected
            and session.helper.status.cleanup_confirmed
            and not session.helper.status.cleanup_errors
            and session.helper.status.failure is None
            and session.cleanup_failure is None
            and not session.helper.status.producer_verified
            and session.flight.pending is None
        )
    result["passed"] = bool(
        result["passed"] and control.returned and not control.pending
    )
    result.update(
        experiment=case,
        launch_started=control.started,
        launch_returned=control.returned,
        transitions_ns=control.snapshot(),
        gui_stall_witnesses=control.witnesses(),
    )
    return result


def run(output: Path) -> int:  # noqa: C901, PLR0915 - isolated Qt proof wiring
    """Create a visible GUI and retain workers until terminal plus thread.finished."""
    from PySide6.QtCore import (  # noqa: PLC0415
        QObject,
        Qt,
        QThread,
        QTimer,
        Signal,
        Slot,
    )
    from PySide6.QtGui import QCloseEvent, QPaintEvent  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication, QWidget  # noqa: PLC0415

    output.mkdir(parents=True, exist_ok=False)
    diagnostics = Diagnostics(output)
    diagnostics.record("gui-constructing")
    previous_hook = sys.excepthook

    def capture_exception(
        kind: type[BaseException], value: BaseException, tb: object
    ) -> None:
        diagnostics.record(
            "python-exception",
            "".join(
                traceback.format_exception(
                    kind, value, cast("TracebackType | None", tb)
                )
            ),
        )

    sys.excepthook = capture_exception
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)

    class Window(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.control = LaunchControl()
            self.paint = GapStats()
            self.beat = GapStats()
            self.setWindowTitle("VerdiClip inert lifecycle worker proof")
            self.resize(480, 180)

        @override
        def paintEvent(self, event: QPaintEvent) -> None:
            self.control.witness("paint")
            self.paint.tick(time.perf_counter_ns())
            super().paintEvent(event)

        @override
        def closeEvent(self, event: QCloseEvent) -> None:
            self.control.cancel()
            event.ignore()

        @Slot()
        def pulse(self) -> None:
            self.control.witness("heartbeat")
            self.beat.tick(time.perf_counter_ns())
            self.update()

    class Worker(QObject):
        terminal = Signal(int, str)

        def __init__(
            self, generation: int, case: str, directory: Path, control: LaunchControl
        ) -> None:
            super().__init__()
            self.generation, self.case = generation, case
            self.directory, self.control = directory, control
            self.session: WorkerSession | None = None

        @Slot()
        def start(self) -> None:
            diagnostics.record(f"{self.case}:worker-start")
            self.session = WorkerSession(
                cast("QCoreApplication", self), self.directory, self.control, self.case
            )
            diagnostics.record(f"{self.case}:session-created")
            self.session.start()
            diagnostics.record(f"{self.case}:launch-returned")

        def quit(self) -> None:
            if self.session is None:
                raise RuntimeError("Missing worker session")
            result = terminal_receipt(self.session, self.control, self.case)
            result["generation"] = self.generation
            diagnostics.record(f"{self.case}:terminal-emitting")
            self.terminal.emit(self.generation, json.dumps(result))
            self.deleteLater()
            QThread.currentThread().quit()

    class Receiver(QObject):
        def __init__(self, gate: CompletionGate) -> None:
            super().__init__()
            self.gate = gate

        @Slot(int, str)
        def terminal(self, generation: int, receipt: str) -> None:
            diagnostics.record(f"{generation}:terminal-received")
            self.gate.accept(generation, receipt)
            exit_after_retirement(self.gate)

        @Slot()
        def stopped(self) -> None:
            diagnostics.record(f"{self.gate.generation}:thread-finished")
            self.gate.thread_finished = True
            exit_after_retirement(self.gate)

    window = Window()
    window.show()
    heartbeat = QTimer(window)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(window.pulse)
    heartbeat.start()
    results = []
    for index, case in enumerate(CASES):
        with tempfile.TemporaryDirectory(prefix="verdiclip-worker-proof-") as directory:
            control = LaunchControl()
            window.control, window.beat, window.paint = control, GapStats(), GapStats()
            window.beat.tick(time.perf_counter_ns())
            gate = CompletionGate(index)
            receiver = Receiver(gate)
            thread = QThread()
            worker = Worker(index, case, Path(directory), control)
            worker.moveToThread(thread)
            thread.started.connect(worker.start)
            worker.terminal.connect(
                receiver.terminal, Qt.ConnectionType.QueuedConnection
            )
            thread.finished.connect(
                receiver.stopped, Qt.ConnectionType.QueuedConnection
            )
            cancel_timer = QTimer(window)
            cancel_timer.setSingleShot(True)
            cancel_timer.timeout.connect(control.cancel)
            if case == "cancel-before":
                control.cancel()
            elif case.startswith("cancel-"):
                cancel_timer.start(100)
            thread.start()
            diagnostics.record(f"{case}:gui-exec-enter")
            application.exec()
            diagnostics.record(f"{case}:gui-exec-return")
            cancel_timer.stop()
            cancel_timer.deleteLater()
            result = json.loads(gate.receipt or "{}")
            result.update(
                gui_heartbeat=window.beat.report(),
                gui_paint=window.paint.report(),
                thread_finished=gate.thread_finished,
            )
            result["passed"] = bool(
                result.get("passed")
                and gate.delivered
                and case_observed(case, control, window.paint.count)
                and not diagnostics.errors
            )
            (output / f"{case}.json").write_text(json.dumps(result, indent=2))
            results.append(result)
            receiver.deleteLater()
            thread.deleteLater()
            if not result["passed"]:
                break
    heartbeat.stop()
    window.hide()
    passed = len(results) == len(CASES) and all(item["passed"] for item in results)
    (output / "receipt.json").write_text(
        json.dumps(
            {
                "passed": passed,
                "cases": results,
                "limitations": "Synthetic stalls; not interruptible native launch. "
                "An external watchdog is required for genuinely stuck calls.",
            },
            indent=2,
        )
    )
    diagnostics.record("proof-return")
    sys.excepthook = previous_hook
    return int(not passed)


def main() -> int:
    """Run only with an explicitly selected Windows evidence directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This visible native proof requires Windows")
    return run(args.output)


if __name__ == "__main__":
    raise SystemExit(main())
