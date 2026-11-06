"""Scrolling Stop/Cancel remain usable while real private storage is blocked."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QPushButton
from pytestqt.qtbot import QtBot
from tests.capture.test_capture_interaction import click
from tests.capture.test_scroll_spooling import start_blocked_workflow

from verdiclip.capture.scroll.panels import ScrollReviewPanel
from verdiclip.capture.scroll.workflow import WorkflowState

pytestmark = pytest.mark.ux


@pytest.mark.parametrize(("action", "block_at"), [("Stop", 2), ("Cancel", 1)])
def test_storage_wait_keeps_capture_controls_responsive(
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    block_at: int,
) -> None:
    """UX-CAP-16: responsive controls release input and reject uncommitted rows."""
    fixture, writer = start_blocked_workflow(
        qtbot, tmp_path, monkeypatch, block_at=block_at
    )
    progress = fixture.workflow.panel
    assert progress is not None
    beats: list[bool] = []
    timer = QTimer()
    timer.setInterval(1)
    timer.timeout.connect(lambda: beats.append(True))

    def idle() -> bool:
        return fixture.workflow.state is WorkflowState.IDLE

    try:
        timer.start()
        qtbot.waitUntil(lambda: len(beats) >= 10)
        control = next(
            button
            for button in progress.findChildren(QPushButton)
            if action in button.text()
        )
        click(progress, control)
        qtbot.waitUntil(lambda: fixture.releases == [True])
        assert not writer.release.is_set()
        assert not fixture.captures
        assert not fixture.panels
        if action == "Cancel":
            assert not progress.isVisible()
            assert fixture.workflow.state is WorkflowState.CLOSING
        writer.release.set()
        if action == "Stop":
            qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.REVIEW)
            review = fixture.workflow.panel
            assert isinstance(review, ScrollReviewPanel)
            assert review.isVisible()
            status = next(
                label
                for label in review.findChildren(QLabel)
                if label.accessibleName() == "Capture result and stop reason"
            )
            assert "x 48 pixels" in status.text()
            assert "Stopped by you" in status.text()
        else:
            qtbot.waitUntil(lambda: fixture.workflow.state is WorkflowState.IDLE)
            assert not list(tmp_path.iterdir())
    finally:
        timer.stop()
        writer.release.set()
        fixture.workflow.cancel()
        qtbot.waitUntil(idle)
        fixture.close()
