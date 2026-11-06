"""Real blocked storage proves GUI responsiveness and transaction lifetime safety."""

from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QPushButton
from pytestqt.qtbot import QtBot
from tests.capture.test_capture_interaction import click
from tests.capture.test_scroll_acquisition import Run
from tests.capture.test_scroll_binding import FixtureBackend
from tests.capture.test_scroll_core import Documents
from tests.capture.test_scroll_workflow import WorkflowFixture

from verdiclip.capture.scroll.acquisition import AcquisitionState, ScrollAcquisition
from verdiclip.capture.scroll.backend import BackendFrame
from verdiclip.capture.scroll.binding import (
    BindingOptions,
    BindingState,
    ScrollCaptureBinding,
)
from verdiclip.capture.scroll.models import ScrollLimitError, ScrollLimits, StopReason
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.capture.scroll.spooling import StorageJob
from verdiclip.capture.scroll.storage import PngStripWriter, StagedAppend, StripStore
from verdiclip.capture.scroll.workflow import WorkflowState
from verdiclip.exceptions import CaptureError


class StorageControl(BaseException):
    """An application-specific control must escape without losing pixel ownership."""


def storage_controls() -> tuple[BaseException, ...]:
    """Exercise controls beyond the built-in interrupt and process-exit types."""
    return (
        SystemExit(3),
        KeyboardInterrupt(),
        GeneratorExit(),
        StorageControl(),
        BaseExceptionGroup("storage controls", [StorageControl(), KeyboardInterrupt()]),
    )


def wait_for_job(job: StorageJob, seconds: float = 5) -> None:
    """Always wait for the exact worker before asserting its terminal evidence."""
    completed = job.wait(seconds)
    assert completed


def wait_for_event(event: threading.Event, seconds: float) -> None:
    """Test synchronization must run even when Python assertions are disabled."""
    completed = event.wait(seconds)
    assert completed


def assert_job_not_retired(job: StorageJob) -> None:
    """A live worker cannot expose even an internally finalized receipt."""
    assert not job.done
    completed = job.wait(0)
    assert not completed
    with pytest.raises(CaptureError, match="not finished"):
        job.take()


class BlockedWriter(PngStripWriter):
    """Hold one actual PNG write until the test explicitly releases it."""

    def __init__(self, *, block_at: int = 1) -> None:
        self.block_at = block_at
        self.writes = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.on_gui: list[bool] = []

    @override
    def write(self, image: QImage, path: Path) -> None:
        self.writes += 1
        app = QApplication.instance()
        assert app is not None
        self.on_gui.append(QThread.currentThread() == app.thread())
        if self.writes == self.block_at:
            self.entered.set()
            if not self.release.wait(10):
                raise CaptureError("Test did not release its blocked writer")
        super().write(image, path)


class BlockedCapture:
    """Own a synthetic provider and a real private spool for interrupted flows."""

    def __init__(
        self,
        path: Path,
        *,
        block_at: int = 1,
        max_seconds: float = 120,
    ) -> None:
        self.writer = BlockedWriter(block_at=block_at)
        self.session = ScrollSession(
            path, writer=self.writer, limits=ScrollLimits(max_seconds=max_seconds)
        )
        self.backend = FixtureBackend()
        self.binding = ScrollCaptureBinding(
            self.session, self.backend, options=BindingOptions(interval_ms=1)
        )
        self.reviews: list[ScrollSession] = []
        self.failures: list[str] = []
        self.binding.review_ready.connect(
            lambda session, _note: self.reviews.append(session)
        )
        self.binding.failed.connect(self.failures.append)

    def start(self, qtbot: QtBot) -> None:
        """Wait until the actual worker is held inside its selected write."""
        started = self.binding.start_confirmed(self.backend.TARGET)
        assert started
        qtbot.waitUntil(self.writer.entered.is_set)

    def close(self, qtbot: QtBot) -> None:
        """Release every test blocker and await acknowledged storage retirement."""
        self.binding.cancel()
        self.writer.release.set()
        qtbot.waitUntil(lambda: not self.binding.storage_pending)
        self.session.close()
        self.binding.deleteLater()


def test_blocked_baseline_keeps_gui_responsive_and_stop_cancels(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """A storage wait cannot starve Qt or manufacture a baseline after Stop."""
    capture = BlockedCapture(tmp_path)
    beats: list[bool] = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))
    try:
        capture.start(qtbot)
        timer.start()
        qtbot.waitUntil(lambda: len(beats) >= 10)
        assert capture.writer.on_gui == [False]
        assert capture.backend.samples == 3
        assert capture.backend.moves == 0
        capture.binding.stop()
        assert capture.binding.state is BindingState.CANCELLED
        assert capture.session.state is ScrollState.CANCELLED
        assert capture.binding.storage_pending
        assert not capture.reviews
        started = capture.binding.start_confirmed(capture.backend.TARGET)
        assert not started
        capture.writer.release.set()
        qtbot.waitUntil(lambda: not capture.binding.storage_pending)
        assert not capture.reviews
        assert not list(tmp_path.iterdir())
    finally:
        timer.stop()
        capture.close(qtbot)


def test_stop_during_novel_rows_reviews_only_the_committed_prefix(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """Even fast native finish cannot expose review while rollback owns files."""
    capture = BlockedCapture(tmp_path, block_at=2)
    try:
        capture.start(qtbot)
        capture.binding.stop()
        qtbot.waitUntil(lambda: bool(capture.backend.finish_restore))
        assert not capture.reviews
        assert capture.session.result is not None
        assert capture.session.result.height == 48
        assert capture.backend.moves == 1
        capture.writer.release.set()
        qtbot.waitUntil(lambda: bool(capture.reviews))
        accepted = capture.session.accept()
        assert accepted.image == capture.backend.source.copy(0, 0, 96, 48)
        assert accepted.stop_reason == StopReason.USER_STOP.value
        assert capture.backend.max_in_flight == 1
    finally:
        capture.close(qtbot)


@pytest.mark.parametrize("block_at", [1, 2])
def test_unchanged_overall_deadline_stops_blocked_storage(
    qtbot: QtBot,
    tmp_path: Path,
    block_at: int,
) -> None:
    """Disk work cannot extend the finite deadline or admit pixels afterward."""
    capture = BlockedCapture(tmp_path, block_at=block_at, max_seconds=0.25)
    try:
        capture.start(qtbot)
        qtbot.waitUntil(lambda: capture.binding.state is not BindingState.CAPTURING)
        assert capture.binding.storage_pending
        assert not capture.reviews
        capture.writer.release.set()
        qtbot.waitUntil(lambda: not capture.binding.storage_pending)
        if block_at == 1:
            assert capture.binding.state is BindingState.FAILED
            assert capture.failures
            assert not list(tmp_path.iterdir())
        else:
            qtbot.waitUntil(lambda: bool(capture.reviews))
            accepted = capture.session.accept()
            assert accepted.image.height() == 48
            assert accepted.stop_reason == StopReason.LIMIT.value
    finally:
        capture.close(qtbot)


def test_finished_receipt_delivered_after_deadline_is_not_admitted(
    tmp_path: Path,
) -> None:
    """The admission clock is GUI delivery time, not the earlier frame timestamp."""
    run = Run(tmp_path)
    run.driver = ScrollAcquisition(run.session, max_seconds=1, defer_storage=True)
    run.start()
    run.settle()
    request = run.driver.storage_request
    assert request is not None
    receipt = StripStore.stage(request)
    try:
        admitted = run.driver.complete_storage(receipt, now=1)
        assert not admitted
        assert run.driver.state is AcquisitionState.FAILED
        assert run.driver.stop_reason is StopReason.LIMIT
        assert run.session.state is ScrollState.IDLE
    finally:
        receipt.close()
        run.session.close()


def test_admission_is_metadata_only_and_rejects_stale_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed staged directory changes no manifest until its exact admission."""
    store = StripStore(tmp_path, ScrollLimits())
    request = store.prepare(Documents.image(height=120))
    receipt = StripStore.stage(request)
    assert store.height == 0
    with monkeypatch.context() as patch:

        def filesystem_forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("Admission performed filesystem I/O")

        for method in ("stat", "read_bytes", "replace", "mkdir", "resolve"):
            patch.setattr(Path, method, filesystem_forbidden)
        store.admit(receipt)
    assert store.height == 120
    next_request = store.prepare(Documents.image(height=120))
    stale = StripStore.stage(next_request)
    store.abandon()
    try:
        with pytest.raises(CaptureError, match="stale"):
            store.admit(stale)
        assert store.height == 120
    finally:
        stale.close()
        store.close()


def test_failed_rollback_retains_owners_for_explicit_retry(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup exception cannot count as quiescence or permit another capture."""
    capture = BlockedCapture(tmp_path)
    actual_close = StagedAppend.close
    attempts: list[StagedAppend] = []

    def fail_close(receipt: StagedAppend) -> None:
        attempts.append(receipt)
        raise CaptureError("Injected storage cleanup failure")

    try:
        capture.start(qtbot)
        with monkeypatch.context() as patch:
            patch.setattr(StagedAppend, "close", fail_close)
            capture.binding.cancel()
            capture.writer.release.set()
            qtbot.waitUntil(lambda: bool(capture.binding.storage_error))
            assert capture.binding.storage_pending
            assert len(attempts) == 1
            started = capture.binding.start_confirmed(capture.backend.TARGET)
            assert not started
        assert StagedAppend.close is actual_close
        retried = capture.binding.retry_storage_cleanup()
        assert retried
        qtbot.waitUntil(lambda: not capture.binding.storage_pending)
        assert not list(tmp_path.iterdir())
    finally:
        capture.close(qtbot)


def test_shutdown_does_not_wait_indefinitely_or_need_gui_for_late_cleanup(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """A timed-out job retires its own files without calling a deleted QObject."""
    capture = BlockedCapture(tmp_path)
    capture.start(qtbot)
    started = time.monotonic()
    with pytest.raises(CaptureError, match="unconfirmed"):
        capture.binding.shutdown_storage(timeout_ms=20)
    assert time.monotonic() - started < 0.5
    job = capture.binding._storage_job
    assert isinstance(job, StorageJob)
    capture.binding.deleteLater()
    qtbot.wait(1)
    capture.writer.release.set()
    wait_for_job(job)
    receipt = job.take()
    assert not receipt.remaining
    assert not receipt.error
    assert not list(tmp_path.iterdir())
    capture.session.close()


def test_synchronous_failed_stage_retains_failed_cleanup_for_session_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synchronous model callers share the same retryable directory ownership."""

    class FailedWriter(PngStripWriter):
        @override
        def write(self, image: QImage, path: Path) -> None:
            super().write(image, path)
            raise CaptureError("Injected write failure")

    session = ScrollSession(tmp_path, writer=FailedWriter())

    def fail_cleanup(_temporary: tempfile.TemporaryDirectory[str]) -> None:
        raise PermissionError("Injected lock")

    with monkeypatch.context() as patch:
        patch.setattr(tempfile.TemporaryDirectory, "cleanup", fail_cleanup)
        with pytest.raises(CaptureError, match="write failure") as failure:
            session.start(
                FixtureBackend.TARGET,
                Documents.image(height=48),
                now=0,
                taken_at=datetime.now(UTC),
            )
        assert isinstance(failure.value.__cause__, CaptureError)
        assert "temporary capture storage" in str(failure.value.__cause__)
    session.close()
    assert not list(tmp_path.iterdir())


def start_blocked_workflow(
    qtbot: QtBot,
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    block_at: int = 1,
) -> tuple[WorkflowFixture, BlockedWriter]:
    """Drive real confirmation widgets while replacing only storage and provider I/O."""
    writer = BlockedWriter(block_at=block_at)
    monkeypatch.setattr(
        "verdiclip.capture.scroll.workflow.ScrollSession",
        lambda parent: ScrollSession(parent, writer=writer),
    )
    fixture = WorkflowFixture(path)
    panel = fixture.offer()
    start = next(
        button for button in panel.findChildren(QPushButton) if "Start" in button.text()
    )
    click(panel, start)
    qtbot.waitUntil(writer.entered.is_set)
    return fixture, writer


def test_stop_rollback_failure_retry_retains_the_original_prefix(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed stale-directory cleanup must not turn Stop into capture cancellation."""
    fixture, writer = start_blocked_workflow(qtbot, tmp_path, monkeypatch, block_at=2)

    def fail_close(_receipt: StagedAppend) -> None:
        raise CaptureError("Injected stale transaction lock")

    def idle() -> bool:
        return fixture.workflow.state is WorkflowState.IDLE

    try:
        fixture.workflow.stop()
        with monkeypatch.context() as patch:
            patch.setattr(StagedAppend, "close", fail_close)
            writer.release.set()
            qtbot.waitUntil(
                lambda: fixture.workflow.state is WorkflowState.CLEANUP_FAILED
            )
            assert fixture.releases == [True]
            handled = fixture.workflow.input_closed(cleaned=True, message="")
            assert handled
            assert fixture.workflow.state is WorkflowState.CLEANUP_FAILED
            assert not fixture.panels
        completed = fixture.workflow.retry_cleanup()
        assert not completed
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.REVIEW)
        assert len(fixture.sessions) == 1
        accepted = fixture.sessions[0].accept()
        assert accepted.image.height() == 48
        assert accepted.stop_reason == StopReason.USER_STOP.value
        assert fixture.backends[0].moves == 1
    finally:
        writer.release.set()
        fixture.workflow.cancel()
        qtbot.waitUntil(idle)
        fixture.close()


def test_retired_binding_cannot_cancel_or_release_a_reentrant_replacement(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every old receipt loses permission before workflow IDLE exposes replacement."""
    fixture, writer = start_blocked_workflow(qtbot, tmp_path, monkeypatch)
    old = fixture.workflow._binding
    session = fixture.workflow._session
    assert old is not None
    assert session is not None
    replacements: list[bool] = []

    def replace_when_idle() -> None:
        if fixture.workflow.state is WorkflowState.IDLE and not replacements:
            replacements.append(True)
            fixture.workflow.begin(
                fixture.WINDOW,
                fixture.SCREEN.physical.top_left,
                (fixture.SCREEN,),
                explicit=True,
            )
            old.status_changed.emit("stale status")
            old.review_ready.emit(session, "stale review")
            old.failed.emit("stale failure")
            old.cancelled.emit()
            old.storage_changed.emit()
            old.storage_failed.emit("stale storage failure")
            old.input_release_requested.emit()

    fixture.workflow.work_changed.connect(replace_when_idle)
    try:
        fixture.workflow.cancel()
        assert fixture.releases == [True]
        assert fixture.workflow.state is WorkflowState.CLOSING
        writer.release.set()
        qtbot.waitUntil(lambda: bool(replacements))
        assert fixture.workflow.state is WorkflowState.DISCOVERING
        assert not fixture.failures
        assert not fixture.panels
        assert fixture.releases == [True]
    finally:
        writer.release.set()
        fixture.workflow.work_changed.disconnect(replace_when_idle)
        fixture.close()


@pytest.mark.parametrize(
    "failure", [OSError(), CaptureError(), RuntimeError("unexpected")]
)
def test_cleanup_worker_returns_nonempty_failure_without_automatic_retry(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    """Blank or unexpected failures retain ownership without spawning retry loops."""
    capture = BlockedCapture(tmp_path)
    attempts: list[bool] = []

    def fail_close(_receipt: StagedAppend) -> None:
        attempts.append(True)
        raise failure

    try:
        capture.start(qtbot)
        with monkeypatch.context() as patch:
            patch.setattr(StagedAppend, "close", fail_close)
            capture.binding.cancel()
            capture.writer.release.set()
            qtbot.waitUntil(lambda: bool(capture.binding.storage_error))
            qtbot.wait(30)
            assert attempts == [True]
            assert capture.binding.storage_pending
        retried = capture.binding.retry_storage_cleanup()
        assert retried
        qtbot.waitUntil(lambda: not capture.binding.storage_pending)
    finally:
        capture.close(qtbot)


def test_cleanup_thread_start_failure_retains_files_for_retry(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed thread creation cannot drop the only committed-directory owner."""
    capture = BlockedCapture(tmp_path)
    capture.writer.release.set()
    try:
        started = capture.binding.start_confirmed(capture.backend.TARGET)
        assert started
        qtbot.waitUntil(lambda: bool(capture.reviews))

        def fail_start(_thread: threading.Thread) -> None:
            raise RuntimeError("Injected exhausted threads")

        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail_start)
            capture.binding.cancel()
            assert capture.binding.storage_pending
            assert "start capture cleanup" in capture.binding.storage_error
            assert list(tmp_path.iterdir())
        retried = capture.binding.retry_storage_cleanup()
        assert retried
        qtbot.waitUntil(lambda: not capture.binding.storage_pending)
        assert not list(tmp_path.iterdir())
    finally:
        capture.close(qtbot)


def test_staging_thread_start_failure_is_terminal_without_a_false_baseline(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No worker means no COMMITTING stall and no fabricated capture pixels."""
    capture = BlockedCapture(tmp_path)

    def fail_start(_thread: threading.Thread) -> None:
        raise RuntimeError("Injected worker start failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail_start)
            started = capture.binding.start_confirmed(capture.backend.TARGET)
            assert started
            qtbot.waitUntil(lambda: bool(capture.failures))
        assert capture.binding.state is BindingState.FAILED
        assert not capture.binding.storage_pending
        assert capture.session.state is ScrollState.IDLE
        assert not list(tmp_path.iterdir())
    finally:
        capture.close(qtbot)


@pytest.mark.parametrize("control", storage_controls())
def test_worker_control_keeps_exact_directory_for_failed_cleanup_retry(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control: BaseException,
) -> None:
    """A worker control after writing cannot leave cleanup only to a finalizer."""

    class InterruptedWriter(PngStripWriter):
        @override
        def write(self, image: QImage, path: Path) -> None:
            super().write(image, path)
            raise control

    escaped: list[BaseException] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: escaped.append(args.exc_value)
    )
    store = StripStore(tmp_path, ScrollLimits(), writer=InterruptedWriter())
    request = store.prepare(Documents.image(height=48))
    job = StorageJob(request)
    job.start()
    wait_for_job(job)
    outcome = job.take()
    assert escaped == [control]
    assert escaped[0] is control
    assert outcome.staged is None
    assert "did not complete" in outcome.error
    assert len(outcome.remaining) == 1
    receipt = outcome.remaining[0]
    assert receipt.directory is request.directory

    def fail_cleanup(_temporary: tempfile.TemporaryDirectory[str]) -> None:
        raise PermissionError("Injected cleanup lock")

    with monkeypatch.context() as patch:
        patch.setattr(tempfile.TemporaryDirectory, "cleanup", fail_cleanup)
        cleanup = StorageJob(cleanup=(receipt,))
        cleanup.start()
        qtbot.waitUntil(lambda: cleanup.done)
        failed = cleanup.take()
        assert failed.remaining == (receipt,)
        assert failed.error
    retry = StorageJob(cleanup=failed.remaining)
    retry.start()
    wait_for_job(retry)
    retired = retry.take()
    assert not retired.remaining
    store.close()
    assert not list(tmp_path.iterdir())


def test_worker_confinement_failure_retains_exact_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving cleanup ownership is also fallible and must produce a receipt."""
    store = StripStore(tmp_path, ScrollLimits())
    staged = StripStore.stage(store.prepare(Documents.image(height=48)))

    def fail_resolve(_path: Path, **_kwargs: object) -> Path:
        raise OSError("Injected resolve failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", fail_resolve)
        job = StorageJob(cleanup=(staged,))
        job.start()
        wait_for_job(job)
        receipt = job.take()
        assert receipt.remaining == (staged,)
        assert receipt.error
    staged.close()
    store.close()
    assert not list(tmp_path.iterdir())


def test_start_then_control_keeps_the_live_worker_owned_for_shutdown(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupted Thread.start wait cannot abandon an already launched writer."""
    capture = BlockedCapture(tmp_path)
    driver = capture.binding._driver
    target = capture.backend.TARGET
    driver.start_confirmed(target, now=0, taken_at=datetime.now(UTC))
    capture.binding._state = BindingState.CAPTURING
    clock = [0.1]
    capture.binding._clock = lambda: clock[0]
    for _ in range(2):
        work = driver.sample(now=clock[0])
        completed = driver.complete_sample(
            work,
            capture.backend.source.copy(0, 0, 96, 48),
            target=target,
            now=clock[0],
        )
        assert completed
        clock[0] += 0.1
    work = driver.sample(now=clock[0])
    capture.binding._pending = work
    actual_start = threading.Thread.start
    control = KeyboardInterrupt()

    def start_then_interrupt(thread: threading.Thread) -> None:
        actual_start(thread)
        raise control

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", start_then_interrupt)
            with pytest.raises(KeyboardInterrupt) as caught:
                capture.binding._sampled(
                    work,
                    BackendFrame(target, capture.backend.source.copy(0, 0, 96, 48)),
                )
            assert caught.value is control
        job = capture.binding._storage_job
        assert job is not None
        wait_for_event(capture.writer.entered, 1)
        with pytest.raises(CaptureError, match="unconfirmed"):
            capture.binding.shutdown_storage(timeout_ms=10)
        assert capture.binding._storage_job is job
        capture.writer.release.set()
        capture.binding.shutdown_storage(timeout_ms=1000)
        assert not capture.binding.storage_pending
        assert not capture.reviews
        assert not list(tmp_path.iterdir())
    finally:
        capture.close(qtbot)


@pytest.mark.parametrize("input_closed_first", [False, True])
def test_cancel_cleanup_failure_keeps_retry_available_after_input_receipt(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    input_closed_first: bool,
) -> None:
    """Either receipt order preserves explicit cleanup failure and retry ownership."""
    fixture, writer = start_blocked_workflow(qtbot, tmp_path, monkeypatch)

    def fail_close(_receipt: StagedAppend) -> None:
        raise CaptureError("Injected rollback failure")

    try:
        fixture.workflow.cancel()
        if input_closed_first:
            handled = fixture.workflow.input_closed(cleaned=True, message="")
            assert handled
        with monkeypatch.context() as patch:
            patch.setattr(StagedAppend, "close", fail_close)
            writer.release.set()
            qtbot.waitUntil(
                lambda: fixture.workflow.state is WorkflowState.CLEANUP_FAILED
            )
            if not input_closed_first:
                handled = fixture.workflow.input_closed(cleaned=True, message="")
                assert handled
            assert fixture.workflow.state is WorkflowState.CLEANUP_FAILED
        completed = fixture.workflow.retry_cleanup()
        assert not completed
        qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
        assert not list(tmp_path.iterdir())
    finally:
        writer.release.set()
        fixture.close()


@pytest.mark.parametrize("limit", [False, True])
@pytest.mark.parametrize("prefix", [False, True])
def test_deferred_storage_failure_preserves_only_verified_prefix(
    tmp_path: Path,
    *,
    limit: bool,
    prefix: bool,
) -> None:
    """Admission failures keep resource and encoder reasons distinct."""
    run = Run(tmp_path)
    run.driver = ScrollAcquisition(run.session, defer_storage=True)
    run.start()
    run.settle()
    if prefix:
        first = run.driver.storage_request
        assert first is not None
        admitted = run.driver.complete_storage(StripStore.stage(first), now=run.now)
        assert admitted
        run.move()
        run.settle(24)
    request = run.driver.storage_request
    assert request is not None
    receipt = replace(
        StripStore.stage(request),
        error=ScrollLimitError("quota") if limit else CaptureError("encoder"),
    )
    try:
        admitted = run.driver.complete_storage(receipt, now=run.now)
        assert not admitted
        assert run.driver.stop_reason is (
            StopReason.LIMIT if limit else StopReason.ERROR
        )
        if prefix:
            accepted = run.session.accept()
            assert accepted.image.height() == 48
        else:
            assert run.driver.state is AcquisitionState.FAILED
    finally:
        receipt.close()
        run.session.close()


def test_storage_reservations_and_actual_byte_admission_remain_bounded(
    tmp_path: Path,
) -> None:
    """A second reservation and a forged oversized receipt cannot enlarge the prefix."""
    store = StripStore(tmp_path, ScrollLimits())
    request = store.prepare(Documents.image(height=48))
    with pytest.raises(CaptureError, match="in flight"):
        store.prepare(Documents.image(height=48))
    staged = StripStore.stage(request)
    oversized = replace(
        staged,
        strips=(
            replace(staged.strips[0], size_bytes=ScrollLimits().max_disk_bytes + 1),
        ),
    )
    try:
        with pytest.raises(ScrollLimitError):
            store.admit(oversized)
        assert store.height == 0
    finally:
        staged.close()
        store.close()


def test_worker_api_requires_one_operation_and_one_finished_receipt(
    tmp_path: Path,
) -> None:
    """Unstarted, mixed, stale and duplicate receipt operations fail explicitly."""
    store = StripStore(tmp_path, ScrollLimits())
    request = store.prepare(Documents.image(height=48))
    staged = StripStore.stage(request)
    try:
        with pytest.raises(ValueError, match="simultaneously"):
            StorageJob(request, cleanup=(staged,))
        job = StorageJob(cleanup=(staged,))
        completed = job.wait(0)
        assert not completed
        with pytest.raises(CaptureError, match="not finished"):
            job.take()
        job.start()
        wait_for_job(job)
        discarded = job.discard()
        assert not discarded
        receipt = job.take()
        assert not receipt.error
        with pytest.raises(CaptureError, match="not finished"):
            job.take()
    finally:
        staged.close()
        store.close()


def test_unexpected_staging_boundary_failure_always_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug before a staging receipt exists still returns a terminal error."""
    store = StripStore(tmp_path, ScrollLimits())
    request = store.prepare(Documents.image(height=48))

    failure = RuntimeError("Injected stage boundary failure")
    escaped: list[BaseException] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: escaped.append(args.exc_value)
    )

    def fail_stage(_request: object) -> StagedAppend:
        raise failure

    monkeypatch.setattr(StripStore, "stage", fail_stage)
    job = StorageJob(request)
    job.start()
    wait_for_job(job)
    receipt = job.take()
    assert escaped == [failure]
    assert escaped[0] is failure
    assert "did not complete" in receipt.error
    assert len(receipt.remaining) == 1
    assert receipt.remaining[0].directory is request.directory
    receipt.remaining[0].close()
    store.close()


def test_cancellation_can_transfer_more_cleanup_while_worker_is_retiring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same worker retires late transferred owners without a second queue."""
    store = StripStore(tmp_path, ScrollLimits())
    first = StripStore.stage(store.prepare(Documents.image(height=48)))
    store.abandon()
    second = StripStore.stage(store.prepare(Documents.image(height=48)))
    entered, release = threading.Event(), threading.Event()
    actual_close = StagedAppend.close

    def block_first(receipt: StagedAppend) -> None:
        if receipt is first:
            entered.set()
            wait_for_event(release, 5)
        actual_close(receipt)

    monkeypatch.setattr(StagedAppend, "close", block_first)
    job = StorageJob(cleanup=(first,))
    job.start()
    try:
        wait_for_event(entered, 1)
        discarded = job.discard((second,))
        assert discarded
        release.set()
        wait_for_job(job)
        receipt = job.take()
        assert not receipt.error
        assert not receipt.remaining
        assert not list(tmp_path.iterdir())
    finally:
        release.set()
        wait_for_job(job)
        first.close()
        second.close()
        store.close()


def test_uncertain_start_wait_uses_remainder_until_actual_thread_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready receipt is insufficient while the same worker still has a live tail."""
    job = StorageJob()
    original_work = job._work
    release = threading.Event()
    tail_finished = threading.Event()

    def delayed_tail() -> None:
        original_work()
        wait_for_event(release, 2)
        tail_finished.set()

    monkeypatch.setattr(job, "_work", delayed_tail)
    start = threading.Timer(0.01, job.start)
    finish = threading.Timer(0.05, release.set)
    start.start()
    finish.start()
    try:
        wait_for_job(job, 1)
        assert tail_finished.is_set()
        receipt = job.take()
        assert not receipt.error
    finally:
        release.set()
        start.join(1)
        finish.join(1)
        wait_for_job(job, 1)


@pytest.mark.parametrize("control_index", range(5))
@pytest.mark.parametrize("api", ["store", "session"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_synchronous_controls_propagate_with_retryable_directory_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_index: int,
    api: str,
    *,
    cleanup_fails: bool,
) -> None:
    """Every control escapes unchanged, including after a failed cleanup attempt."""
    control = storage_controls()[control_index]

    class InterruptedWriter(PngStripWriter):
        @override
        def write(self, image: QImage, path: Path) -> None:
            super().write(image, path)
            raise control

    image = Documents.image(height=48)
    if api == "store":
        store = StripStore(tmp_path, ScrollLimits(), writer=InterruptedWriter())

        def stage() -> None:
            store.append(image)

        close = store.close
    else:
        session = ScrollSession(tmp_path, writer=InterruptedWriter())

        def stage() -> None:
            session.start(
                FixtureBackend.TARGET, image, now=0, taken_at=datetime.now(UTC)
            )

        close = session.close

    def fail_cleanup(_temporary: tempfile.TemporaryDirectory[str]) -> None:
        raise PermissionError("Injected cleanup lock")

    try:
        with monkeypatch.context() as patch:
            if cleanup_fails:
                patch.setattr(tempfile.TemporaryDirectory, "cleanup", fail_cleanup)
            with pytest.raises(type(control)) as caught:
                stage()
            assert caught.value is control
            if cleanup_fails:
                assert isinstance(control.__cause__, CaptureError)
                assert list(tmp_path.iterdir())
            else:
                assert not list(tmp_path.iterdir())
    finally:
        close()
    assert not list(tmp_path.iterdir())


def test_unfinished_cleanup_receipt_cannot_admit_unwritten_pixels(
    tmp_path: Path,
) -> None:
    """Pre-I/O ownership is not evidence that any tile was successfully staged."""
    store = StripStore(tmp_path, ScrollLimits())
    image = Documents.image(height=48)
    request = store.prepare(image)
    try:
        with pytest.raises(CaptureError, match="incomplete"):
            store.admit(StripStore.cleanup_receipt(request))
        assert store.height == 0
        assert not list(tmp_path.iterdir())
        store.admit(StripStore.stage(request))
        assert store.materialize() == image
    finally:
        store.close()


def test_control_during_cancel_transfer_retains_staged_and_committed_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final worker retirement preserves both owners and waits for the control hook."""
    entered, release = threading.Event(), threading.Event()
    hook_entered, hook_release = threading.Event(), threading.Event()
    escaped: list[BaseException] = []
    control = StorageControl()

    class InterruptedWriter(PngStripWriter):
        writes = 0

        @override
        def write(self, image: QImage, path: Path) -> None:
            super().write(image, path)
            self.writes += 1
            if self.writes == 2:
                entered.set()
                wait_for_event(release, 5)
                raise control

    def observe_control(args: threading.ExceptHookArgs) -> None:
        assert isinstance(args.exc_value, BaseException)
        escaped.append(args.exc_value)
        hook_entered.set()
        wait_for_event(hook_release, 5)

    monkeypatch.setattr(threading, "excepthook", observe_control)
    store = StripStore(tmp_path, ScrollLimits(), writer=InterruptedWriter())
    store.append(Documents.image(height=48))
    request = store.prepare(Documents.image(height=48))
    job = StorageJob(request)
    job.start()
    committed: tuple[StagedAppend, ...] = ()
    try:
        wait_for_event(entered, 1)
        committed = store.release()
        assert len(committed) == 1
        accepted = job.discard(committed)
        assert accepted
        release.set()
        wait_for_event(hook_entered, 1)
        assert_job_not_retired(job)
        hook_release.set()
        wait_for_job(job)
        terminal = job.take()
        assert escaped == [control]
        assert escaped[0] is control
        assert len(terminal.remaining) == 2
        assert terminal.remaining[0].directory is request.directory
        assert terminal.remaining[1] is committed[0]
        retry = StorageJob(cleanup=terminal.remaining)
        retry.start()
        wait_for_job(retry)
        retired = retry.take()
        assert not retired.remaining
        assert not retired.error
        assert not list(tmp_path.iterdir())
    finally:
        release.set()
        hook_release.set()
        wait_for_job(job)
        request.directory.close()
        for receipt in committed:
            receipt.close()
        store.close()
