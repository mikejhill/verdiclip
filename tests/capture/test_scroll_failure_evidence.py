"""Diagnostic metadata cannot change verdicts or grow with capture length."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.capture.scroll_failure_evidence import (
    annotate_failure,
    elapsed,
    observe_settling,
    observed,
    safe_receipt,
)
from tests.capture.test_scroll_service import ServiceWorkflow
from tests.capture.test_scroll_settling import Viewport

from verdiclip.capture.scroll.settling import SettleStatus

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize("error", [RuntimeError(), SystemExit(), KeyboardInterrupt()])
def test_observer_failure_preserves_exact_result(error: BaseException) -> None:
    """Even diagnostic control signals cannot replace the operation result."""
    token = object()
    assert observed(lambda: token, Mock(side_effect=error))() is token


def test_operation_failure_is_never_swallowed() -> None:
    """Only diagnostics are guarded; a product control signal escapes unchanged."""
    error = SystemExit("owned control")
    record = Mock()
    with pytest.raises(SystemExit) as caught:
        observed(Mock(side_effect=error), record)()
    assert caught.value is error
    record.assert_not_called()


def test_note_failure_preserves_original_exception() -> None:
    """Receipt construction and attachment failures do not affect reraising."""
    error = RuntimeError("original")
    annotate_failure(error, Mock(side_effect=KeyboardInterrupt))
    assert not hasattr(error, "__notes__")
    broken = Mock(spec=BaseException)
    broken.add_note.side_effect = ValueError
    annotate_failure(broken, lambda: {"state": "capturing"})
    broken.add_note.assert_called_once()


def test_deadline_receipt_distinguishes_late_third_equal_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three identical frames cannot pass a late deadline, even with diagnostics."""
    evidence = observe_settling(monkeypatch)
    gate, request = Viewport.begin()
    for sequence, timestamp in ((1, 1.75), (2, 1.90)):
        frame = replace(Viewport.frame(sequence), observed_at=timestamp)
        assert gate.submit(request, frame) is SettleStatus.WAITING
    frame = replace(Viewport.frame(3), observed_at=2.014)
    assert gate.submit(request, frame) is SettleStatus.EXHAUSTED
    assert gate.take(request) is None
    receipt = evidence.first_exhausted
    assert receipt is not None
    assert receipt["phase"] == "expire"
    assert receipt["samples"] == receipt["consecutive"] == 2
    assert receipt["elapsed_seconds"] == 2.014
    assert receipt["last_observed_seconds"] == 1.90
    assert not receipt["timing_unavailable"]
    assert evidence.exhausted == 1
    assert evidence.mismatches == 0


def test_mismatch_evidence_stays_bounded_across_many_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only eight first-generation entries and one exhaustion survive."""
    evidence = observe_settling(monkeypatch)
    gate, request = Viewport.begin()
    for generation in range(100):
        if generation:
            request = gate.begin(Viewport.TARGET, token=1, now=0)
        for sequence in range(1, 16):
            assert (
                gate.submit(request, Viewport.frame(sequence, color=sequence))
                is SettleStatus.WAITING
            )
        assert gate.expire(request, now=2.0) is SettleStatus.EXHAUSTED
    assert len(evidence.events) == 8
    assert evidence.generations == 100
    assert evidence.submissions == 1500
    assert evidence.mismatches == 1400
    assert evidence.exhausted == 100
    assert evidence.first_exhausted is not None
    assert evidence.first_exhausted["consecutive"] == 1
    assert set(evidence.snapshot()) == {
        "generations",
        "submissions",
        "mismatches",
        "exhausted",
        "first_generation",
        "first_exhausted",
    }


def test_snapshot_failure_is_metadata_only() -> None:
    """Broken receipt generation leaves a bounded unavailable marker."""
    assert safe_receipt(Mock(side_effect=SystemExit)) == {
        "diagnostic_unavailable": True
    }


@pytest.mark.parametrize("broken_receipt", [False, True])
def test_review_wait_preserves_exact_exception(
    qtbot: QtBot,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    broken_receipt: bool,
) -> None:
    """Actual review helper rethrows the same exception even if its receipt fails."""
    run = ServiceWorkflow(qapp, tmp_path)
    original = RuntimeError("synthetic original wait failure")
    proxy = Mock(spec=QtBot)
    calls = 0

    def wait(callback: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert kwargs == {"timeout": 5000}
            raise original
        qtbot.waitUntil(cast("Callable[[], bool]", callback))

    proxy.waitUntil.side_effect = wait
    if broken_receipt:
        monkeypatch.setattr(run, "failure_receipt", Mock(side_effect=SystemExit))
    try:
        with pytest.raises(RuntimeError) as caught:
            run.review(cast("QtBot", proxy))
        assert caught.value is original
        if broken_receipt:
            assert not hasattr(original, "__notes__")
        else:
            assert len(original.__notes__) == 1
            assert len(original.__notes__[0]) < 512
            assert '"failure_category": "none"' in original.__notes__[0]
    finally:
        run.close()


@pytest.mark.parametrize(
    ("start", "end"), [(float("nan"), 2), (0, float("inf")), (2, 1), (0, None)]
)
def test_invalid_relative_timing_is_unavailable(start: float, end: object) -> None:
    """Do not report a plausible zero or raw timestamp when timing is invalid."""
    assert elapsed(start, end) is None
