"""Complete offscreen capture-to-review flow with asynchronous synthetic content."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QPushButton
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_core import Documents

from verdiclip.capture.reviewed import CaptureCompleteness, ReviewedCapture
from verdiclip.capture.scroll.acquisition import AcquisitionWork, WorkKind
from verdiclip.capture.scroll.backend import (
    BackendFinish,
    BackendFrame,
    ScrollBackend,
    TargetVerification,
)
from verdiclip.capture.scroll.binding import (
    BindingOptions,
    BindingState,
    ScrollCaptureBinding,
)
from verdiclip.capture.scroll.models import ScrollTarget, StopReason
from verdiclip.capture.scroll.panels import ScrollReviewPanel
from verdiclip.capture.scroll.session import ScrollSession, ScrollState
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class FixtureBackend(ScrollBackend):
    """Exercise callback ordering without UIA, desktop pixels or input."""

    TARGET = ScrollTarget("fixture", "Example", Rect(0, 0, 96, 48))

    def __init__(self) -> None:
        super().__init__()
        self.source = Documents.image(width=96, height=240)
        self.top = 0
        self.verifications = 0
        self.moves = 0
        self.samples = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.cancel_count = 0
        self.unsafe_after_baseline = False
        self.fail_after_move = False
        self.silent_prepare = False
        self.silent_finish = False
        self.finish_restore: list[bool] = []
        self.raise_stage = ""
        self.interrupt_kind: WorkKind | None = None
        self.interrupt: Callable[[], None] | None = None
        self.interrupt_finish = False

    @property
    @override
    def unavailable_reason(self) -> str | None:
        return None

    @override
    def verify(self, target: ScrollTarget, request_id: int) -> None:
        """Return independent fresh target evidence before every provider step."""
        self.verifications += 1
        if self.raise_stage == "verify":
            raise CaptureError("Fixture verification unavailable")
        safe = not self.unsafe_after_baseline or self.verifications == 1
        if not self.silent_prepare:
            QTimer.singleShot(
                0,
                lambda: self.verified.emit(
                    TargetVerification(request_id, target, safe)
                ),
            )

    @override
    def execute(self, work: AcquisitionWork) -> None:
        """Expose only known contiguous source rows from a finite document."""
        self.in_flight += 1
        if self.raise_stage == "execute":
            self.in_flight -= 1
            raise CaptureError("Fixture capture unavailable")
        self.max_in_flight = max(self.max_in_flight, self.in_flight)

        def complete() -> None:
            self.in_flight -= 1
            if work.kind is WorkKind.SCROLL:
                self.top += 24
                self.moves += 1
                self.moved.emit(work)
            elif self.fail_after_move and self.moves:
                self.failed.emit(work, "Fixture provider unavailable")
            else:
                self.samples += 1
                frame = BackendFrame(
                    work.target,
                    self.source.copy(
                        0,
                        self.top,
                        int(work.target.viewport.width),
                        int(work.target.viewport.height),
                    ),
                    at_end=self.top >= 48,
                )
                self.sampled.emit(work, frame)

        QTimer.singleShot(0, complete)
        if work.kind is self.interrupt_kind and self.interrupt is not None:
            interrupt, self.interrupt = self.interrupt, None
            interrupt()

    @override
    def finish(self, request_id: int, *, restore: bool) -> None:
        """Keep position restoration distinct from image completeness."""
        self.finish_restore.append(restore)
        if self.raise_stage == "finish":
            raise CaptureError("Fixture restoration unavailable")
        if not self.silent_finish:
            if restore:
                self.top = 0
            QTimer.singleShot(
                0,
                lambda: self.finished.emit(BackendFinish(request_id, restored=restore)),
            )
            if self.interrupt_finish and self.interrupt is not None:
                self.interrupt()

    @override
    def cancel(self) -> None:
        """Intentionally leave queued receipts to test the binding's stale guards."""
        self.cancel_count += 1
        self.failed.emit(None, "Late cancellation callback")


def test_full_async_flow_reviews_and_delivers_canonical_image(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """One-flight target validation, pixels, end evidence and explicit UI acceptance."""
    backend = FixtureBackend()
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    statuses: list[str] = []
    binding.status_changed.connect(statuses.append)
    with qtbot.waitSignal(binding.review_ready, timeout=5000):
        assert binding.start_confirmed(backend.TARGET)
        assert not binding.start_confirmed(backend.TARGET)
    assert binding.state is BindingState.REVIEW
    assert backend.max_in_flight == 1
    assert backend.moves == 2
    assert backend.verifications == backend.moves + 1
    assert backend.top == 0
    assert backend.finish_restore == [True]
    assert len(statuses) >= 4
    panel = ScrollReviewPanel(session)
    qtbot.addWidget(panel)
    captures: list[object] = []
    panel.reviewed_capture.connect(captures.append)
    whole = next(
        button for button in panel.findChildren(QPushButton) if "whole" in button.text()
    )
    qtbot.mouseClick(whole, Qt.MouseButton.LeftButton)
    capture = captures[0]
    assert isinstance(capture, ReviewedCapture)
    assert capture.image == backend.source.copy(0, 0, 96, 96)
    assert capture.completeness is CaptureCompleteness.COMPLETE
    assert panel.acknowledge_delivery(capture.capture_id)
    session.close()


def test_unsupported_backend_is_visible_and_creates_no_pixels(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """An unqualified native build cannot silently appear to complete capture."""
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(session, ScrollBackend())
    with qtbot.waitSignal(binding.failed) as failed:
        assert not binding.start_confirmed(FixtureBackend.TARGET)
    assert "preservation" in failed.args[0]
    assert binding.state is BindingState.FAILED
    assert session.state is ScrollState.IDLE
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("stage", ["verify", "execute", "finish"])
def test_synchronous_backend_errors_cannot_escape_or_strand_progress(
    qtbot: QtBot, tmp_path: Path, stage: str
) -> None:
    """Native adapter startup/IO errors yield failure or a useful verified prefix."""
    backend = FixtureBackend()
    backend.raise_stage = stage
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    signal = binding.review_ready if stage == "finish" else binding.failed
    with qtbot.waitSignal(signal):
        binding.start_confirmed(backend.TARGET)
    if stage == "finish":
        assert binding.state is BindingState.REVIEW
        assert session.accept().image.height() == 96
        session.close()
    else:
        assert binding.state is BindingState.FAILED
        assert session.state is ScrollState.IDLE
    assert backend.cancel_count > 0


def test_stop_before_preparation_and_unavailable_backend_contract(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Stopping preparation discards no pixels; unavailable adapters cannot act."""
    backend = FixtureBackend()
    backend.silent_prepare = True
    binding = ScrollCaptureBinding(ScrollSession(tmp_path), backend)
    binding.stop()
    binding.start_confirmed(backend.TARGET)
    with qtbot.waitSignal(binding.cancelled):
        binding.stop()
    assert backend.samples == backend.moves == 0
    binding.cancel()
    unavailable = ScrollBackend()
    with pytest.raises(CaptureError, match="preservation"):
        unavailable.verify(backend.TARGET, 1)
    work = AcquisitionWork(1, WorkKind.SAMPLE, backend.TARGET, 0)
    with pytest.raises(CaptureError, match="preservation"):
        unavailable.execute(work)
    with qtbot.waitSignal(unavailable.finished) as finished:
        unavailable.finish(1, restore=True)
    assert not finished.args[0].restored


@pytest.mark.parametrize("stage", ["sample", "move", "finish"])
def test_cancel_between_dispatch_and_receipt_cannot_resurrect_capture(
    qtbot: QtBot, tmp_path: Path, stage: str
) -> None:
    """Queued sample/movement/finish receipts lose ownership immediately on Cancel."""
    backend = FixtureBackend()
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    backend.interrupt = binding.cancel
    if stage == "finish":
        backend.interrupt_finish = True
    else:
        backend.interrupt_kind = (
            WorkKind.SAMPLE if stage == "sample" else WorkKind.SCROLL
        )
    reviews: list[object] = []
    binding.review_ready.connect(lambda *values: reviews.append(values))
    with qtbot.waitSignal(binding.cancelled):
        binding.start_confirmed(backend.TARGET)
    qtbot.waitUntil(lambda: backend.in_flight == 0)
    # A sentinel queued after dispatch lets its earlier terminal receipt run too.
    with qtbot.waitSignal(backend.failed):
        QTimer.singleShot(0, lambda: backend.failed.emit(None, "stale sentinel"))
    assert binding.state is BindingState.CANCELLED
    assert session.state is ScrollState.CANCELLED
    assert not reviews
    assert not list(tmp_path.iterdir())


def test_changed_safety_before_movement_keeps_only_baseline(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A stale/unsafe target cannot receive even the first scroll operation."""
    backend = FixtureBackend()
    backend.unsafe_after_baseline = True
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    with qtbot.waitSignal(binding.review_ready):
        binding.start_confirmed(backend.TARGET)
    assert backend.moves == 0
    capture = session.accept()
    assert capture.image.height() == 48
    assert capture.stop_reason == StopReason.TARGET_CHANGED.value
    session.close()


def test_backend_failure_preserves_prefix_with_error_reason(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """An asynchronous provider failure cannot be mislabeled as a user Stop."""
    backend = FixtureBackend()
    backend.fail_after_move = True
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    with qtbot.waitSignal(binding.review_ready):
        binding.start_confirmed(backend.TARGET)
    capture = session.accept()
    assert capture.stop_reason == StopReason.ERROR.value
    assert capture.image.height() == 48
    session.close()


def test_cancel_before_prepared_ignores_late_receipts(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Closing target setup cannot resurrect capture through queued callbacks."""
    backend = FixtureBackend()
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    binding.start_confirmed(backend.TARGET)
    with qtbot.waitSignal(binding.cancelled):
        binding.cancel()
    qtbot.waitUntil(lambda: backend.cancel_count > 0)
    assert binding.state is BindingState.CANCELLED
    assert backend.samples == 0
    assert session.state is ScrollState.CANCELLED
    assert not list(tmp_path.iterdir())


def test_silent_prepare_fails_on_deadline(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The UI cannot remain in preparation indefinitely without a provider reply."""
    monkeypatch.setattr(ScrollCaptureBinding, "PREPARE_MS", 30)
    backend = FixtureBackend()
    backend.silent_prepare = True
    binding = ScrollCaptureBinding(ScrollSession(tmp_path), backend)
    with qtbot.waitSignal(binding.failed):
        binding.start_confirmed(backend.TARGET)
    assert binding.state is BindingState.FAILED
    assert not list(tmp_path.iterdir())


def test_silent_restoration_still_offers_verified_result(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Position restoration failure cannot trap or discard valid captured pixels."""
    monkeypatch.setattr(ScrollCaptureBinding, "FINISH_MS", 30)
    backend = FixtureBackend()
    backend.silent_finish = True
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    with qtbot.waitSignal(binding.review_ready) as ready:
        binding.start_confirmed(backend.TARGET)
    assert "could not be restored" in ready.args[1]
    assert binding.state is BindingState.REVIEW
    assert session.accept().image.height() == 96
    session.close()


def test_cancel_from_progress_callback_cannot_start_native_verification(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Reentrant UI cancellation takes effect before external work dispatch."""
    backend = FixtureBackend()
    binding = ScrollCaptureBinding(ScrollSession(tmp_path), backend)
    binding.status_changed.connect(
        lambda message: binding.cancel() if message.startswith("Preparing") else None
    )
    with qtbot.waitSignal(binding.cancelled):
        assert not binding.start_confirmed(backend.TARGET)
    assert binding.state is BindingState.CANCELLED
    assert backend.verifications == 0
    assert not list(tmp_path.iterdir())


def test_cancel_from_finishing_status_cannot_restore_or_open_review(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A queued restoration/review cannot override cancellation from a UI callback."""
    backend = FixtureBackend()
    session = ScrollSession(tmp_path)
    binding = ScrollCaptureBinding(
        session, backend, options=BindingOptions(interval_ms=1)
    )
    reviews: list[object] = []
    binding.review_ready.connect(lambda capture, _message: reviews.append(capture))
    binding.status_changed.connect(
        lambda message: binding.cancel() if message.startswith("Finishing") else None
    )
    with qtbot.waitSignal(binding.cancelled):
        binding.start_confirmed(backend.TARGET)
    assert not reviews
    assert not backend.finish_restore
    assert session.state is ScrollState.CANCELLED
    assert not list(tmp_path.iterdir())
