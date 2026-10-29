"""Offscreen user actions for explicit targeting and bounded-result review."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QSpinBox
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_acquisition import Run

from verdiclip.capture.reviewed import CaptureCompleteness, ReviewedCapture
from verdiclip.capture.scroll.models import ScrollLimits, ScrollTarget
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.session import ScrollState
from verdiclip.capture.scroll.targets import TargetCandidate
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


class Controls:
    """Find controls by their visible or accessible user-facing names."""

    @staticmethod
    def button(panel: ScrollTargetPanel | ScrollReviewPanel, text: str) -> QPushButton:
        """Locate a labelled action without reaching into widget implementation."""
        return next(
            button
            for button in panel.findChildren(QPushButton)
            if text in button.text()
        )

    @staticmethod
    def field(panel: ScrollTargetPanel | ScrollReviewPanel, name: str) -> QSpinBox:
        """Locate a screen-reader-labelled row or ROI field."""
        return next(
            field
            for field in panel.findChildren(QSpinBox)
            if name in field.accessibleName()
        )


def test_target_proposal_needs_explicit_start_and_emits_once(qtbot: QtBot) -> None:
    """Showing, editing or repeated clicking never implies extra consent."""
    panel = ScrollTargetPanel(
        "Example", (TargetCandidate("container", Rect(-200, 0, 96, 48)),)
    )
    qtbot.addWidget(panel)
    confirmed: list[object] = []
    panel.confirmed.connect(confirmed.append)
    panel.show()
    assert not confirmed
    start = Controls.button(panel, "Start")
    qtbot.mouseClick(start, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(start, Qt.MouseButton.LeftButton)
    assert len(confirmed) == 1
    assert not start.isEnabled()
    assert isinstance(confirmed[0], ScrollTarget)
    assert confirmed[0].viewport == Rect(-200, 0, 96, 48)


def test_outside_roi_cannot_authorize_scroll(qtbot: QtBot) -> None:
    """A coordinate edit outside the semantic container is refused visibly."""
    panel = ScrollTargetPanel(
        "Example", (TargetCandidate("container", Rect(0, 0, 96, 48)),)
    )
    qtbot.addWidget(panel)
    confirmed: list[object] = []
    panel.confirmed.connect(confirmed.append)
    Controls.field(panel, "Content left").setValue(-1)
    qtbot.mouseClick(Controls.button(panel, "Start"), Qt.MouseButton.LeftButton)
    assert not confirmed
    assert any("inside" in label.text() for label in panel.findChildren(QLabel))


def test_unsupported_target_has_no_start_action(qtbot: QtBot) -> None:
    """Unsupported discovery is explicit rather than apparently successful."""
    panel = ScrollTargetPanel("Example", ())
    qtbot.addWidget(panel)
    assert not Controls.button(panel, "Start").isEnabled()
    assert any("No supported" in label.text() for label in panel.findChildren(QLabel))


def test_review_emits_canonical_capture_once_after_accept(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Review does not save or copy and duplicate clicks cannot redeliver."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    payloads: list[object] = []
    panel.reviewed_capture.connect(payloads.append)
    previews = [
        label
        for label in panel.findChildren(QLabel)
        if "preview" in label.accessibleName()
    ]
    assert len(previews) == 1
    assert not previews[0].pixmap().isNull()
    panel.show()
    assert not payloads
    button = Controls.button(panel, "whole")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert len(payloads) == 1
    assert isinstance(payloads[0], ReviewedCapture)
    assert payloads[0].image == run.image.copy(0, 0, 96, 48)
    assert run.session.state is ScrollState.DELIVERED
    run.session.close()


def test_large_result_can_deliver_explicit_small_range(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Whole-result refusal leaves selected-row fallback usable and truthful."""
    run = Run(tmp_path, limits=ScrollLimits(max_flatten_pixels=96 * 24))
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    preview = next(
        label
        for label in panel.findChildren(QLabel)
        if "preview" in label.accessibleName()
    )
    assert not preview.pixmap().isNull()
    payloads: list[object] = []
    panel.reviewed_capture.connect(payloads.append)
    qtbot.mouseClick(Controls.button(panel, "whole"), Qt.MouseButton.LeftButton)
    assert not payloads
    assert run.session.state is ScrollState.REVIEW
    Controls.field(panel, "Number of selected").setValue(20)
    Controls.field(panel, "First selected").setValue(4)
    qtbot.mouseClick(Controls.button(panel, "selected"), Qt.MouseButton.LeftButton)
    assert len(payloads) == 1
    assert isinstance(payloads[0], ReviewedCapture)
    capture = payloads[0]
    assert capture.image == run.image.copy(0, 4, 96, 20)
    assert capture.completeness is CaptureCompleteness.PARTIAL
    assert capture.stop_reason == "selected_range"
    assert run.session.accept(top=4, height=20) is capture
    with pytest.raises(CaptureError, match="another range"):
        run.session.accept()
    run.session.close()


def test_discard_notifies_owner_and_cleans_only_session(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """The capture owner, rather than the widget, owns spool lifetime."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    panel.cancelled.connect(run.driver.cancel)
    panel.show()
    qtbot.mouseClick(Controls.button(panel, "Discard"), Qt.MouseButton.LeftButton)
    assert run.session.state is ScrollState.CANCELLED
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["target", "review"])
def test_escape_backs_out_without_delivering(
    qtbot: QtBot, tmp_path: Path, kind: str
) -> None:
    """Keyboard cancellation follows the same owner-notification path as Discard."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    panel = (
        ScrollReviewPanel(run.session)
        if kind == "review"
        else ScrollTargetPanel(
            "Example", (TargetCandidate("container", Rect(0, 0, 96, 48)),)
        )
    )
    qtbot.addWidget(panel)
    cancelled: list[bool] = []
    panel.cancelled.connect(lambda: cancelled.append(True))
    panel.show()
    panel.activateWindow()
    panel.setFocus()
    qtbot.waitUntil(panel.isActiveWindow)
    qtbot.keyClick(panel, Qt.Key.Key_Escape)
    assert cancelled == [True]
    assert not panel.isVisible()
    assert run.session.state is ScrollState.REVIEW
    run.session.close()


@pytest.mark.parametrize("top", [0, 4])
def test_equivalent_row_range_retries_keep_identity(tmp_path: Path, top: int) -> None:
    """Implicit remaining-height and explicit equivalent rows are one acceptance."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    capture = run.session.accept(top=top)
    assert run.session.accept(top=top, height=48 - top) is capture
    run.session.close()


def test_corrupt_review_pixels_cannot_be_delivered(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """A failed spool read gives visible feedback and never emits a capture."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    tile = next(tmp_path.glob("verdiclip-scroll-*/*.png"))
    tile.write_bytes(b"interrupted-storage")
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    payloads: list[object] = []
    panel.reviewed_capture.connect(payloads.append)
    assert any("integrity" in label.text() for label in panel.findChildren(QLabel))
    qtbot.mouseClick(Controls.button(panel, "whole"), Qt.MouseButton.LeftButton)
    assert not payloads
    assert run.session.state is ScrollState.REVIEW
    run.session.close()


def test_impossible_preview_budget_has_explicit_feedback(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """An unavailable preview is described, rather than shown as an empty success."""
    run = Run(tmp_path, limits=ScrollLimits(max_materialize_bytes=1000))
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    assert any(
        "available review memory" in label.text()
        for label in panel.findChildren(QLabel)
    )
    run.session.close()


def test_preview_uses_actual_tile_capacity_for_a_longer_composed_result(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Small safe pages remain visible when result height exceeds every tile."""
    run = Run(tmp_path, limits=ScrollLimits(max_materialize_bytes=50_000))
    run.start()
    run.settle()
    run.move()
    run.settle(24)
    run.driver.stop()
    assert not run.session.page(0, 1).isNull()
    assert run.session.page_rows(768) == 5
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    preview = next(
        label
        for label in panel.findChildren(QLabel)
        if "preview" in label.accessibleName()
    )
    assert not preview.pixmap().isNull()
    assert Controls.field(panel, "Number of selected").value() == 5
    run.session.close()


def test_delivery_needs_matching_ack_and_retry_keeps_capture_identity(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """Controller success can acknowledge tray-only output without an editor window."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session)
    qtbot.addWidget(panel)
    captures: list[object] = []
    panel.reviewed_capture.connect(captures.append)
    qtbot.mouseClick(Controls.button(panel, "whole"), Qt.MouseButton.LeftButton)
    capture = captures[0]
    assert isinstance(capture, ReviewedCapture)
    assert not panel.acknowledge_delivery("other-capture")
    assert not panel.delivery_failed("other-capture", "not relevant")
    assert panel.delivery_failed(capture.capture_id, "Destination unavailable")
    qtbot.mouseClick(Controls.button(panel, "Retry"), Qt.MouseButton.LeftButton)
    assert len(captures) == 2
    assert captures[1] is capture
    assert panel.acknowledge_delivery(capture.capture_id)
    assert panel.acknowledge_delivery(capture.capture_id)
    assert not panel.delivery_failed(capture.capture_id, "stale failure")
    assert any(
        label.text() == "Capture delivered." for label in panel.findChildren(QLabel)
    )
    assert run.session.state is ScrollState.DELIVERED
    run.session.close()
