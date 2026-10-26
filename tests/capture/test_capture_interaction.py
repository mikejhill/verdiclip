"""Owned target/review controls accept replay input without native activation."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSpinBox
from pytestqt.qtbot import QtBot
from tests.capture.test_popup_capture import ExistingScene
from tests.capture.test_scroll_acquisition import Run

from verdiclip.capture.input_policy import InputKind, SelectionInput
from verdiclip.capture.interaction import CapturePanel
from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.reviewed import ReviewedCapture
from verdiclip.capture.scroll.panels import ScrollReviewPanel, ScrollTargetPanel
from verdiclip.capture.scroll.targets import TargetCandidate
from verdiclip.geometry import Point, Rect


def button(panel: CapturePanel, text: str) -> QPushButton:
    """Locate only a button owned by this newly created panel."""
    return next(item for item in panel.findChildren(QPushButton) if text in item.text())


def click(panel: CapturePanel, control: QPushButton) -> None:
    """Convert a logical widget center into the broker's physical point contract."""
    logical = control.mapToGlobal(control.rect().center())
    geometry = ScreenGeometry.from_screen(panel.screen())
    point = geometry.to_physical(Point(logical.x(), logical.y()))
    for kind in (InputKind.PRESS, InputKind.RELEASE):
        panel.receive_input(SelectionInput(kind, point))


@pytest.mark.parametrize("scene_kind", ["popup", "submenu", "modal", "about"])
def test_target_confirmation_keeps_existing_popup_and_modality(
    qtbot: QtBot, qapp: QApplication, scene_kind: str
) -> None:
    """Semantic Start operates owned UI while existing owners retain their state."""
    scene = ExistingScene(scene_kind, qapp)
    panel = ScrollTargetPanel(
        "Example",
        (TargetCandidate("container", Rect(0, 0, 96, 48)),),
        external_input=True,
    )
    qtbot.addWidget(panel)
    results: list[object] = []
    panel.confirmed.connect(results.append)
    cursor = QCursor.pos()
    try:
        panel.show()
        assert panel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert panel.windowFlags() & Qt.WindowType.WindowTransparentForInput
        click(panel, button(panel, "Start"))
        assert len(results) == 1
        assert QCursor.pos() == cursor
        scene.assert_preserved()
        panel.dismiss()
        scene.assert_preserved()
    finally:
        panel.close()
        scene.close()


def test_panel_controls_are_keyboard_reachable_without_qt_focus(qtbot: QtBot) -> None:
    """Tab selects controls; arrow commands edit fields without moving OS cursor."""
    panel = ScrollTargetPanel(
        "Example",
        (TargetCandidate("container", Rect(0, 0, 96, 48)),),
        external_input=True,
    )
    qtbot.addWidget(panel)
    panel.show()
    point = Point(0, 0)
    fields = panel.findChildren(QSpinBox)
    original = fields[0].value()
    panel.receive_input(SelectionInput(InputKind.NEXT, point))
    panel.receive_input(SelectionInput(InputKind.MOVE, point, "arrow:right", 10))
    assert fields[0].value() == original + 10
    panel.receive_input(SelectionInput(InputKind.NEXT, point, "previous"))
    panel.receive_input(SelectionInput(InputKind.ENTER, point))
    assert panel.isVisible()


def test_hidden_retired_and_unmatched_controls_never_activate(qtbot: QtBot) -> None:
    """Late releases and invisible UI cannot turn into a new Start action."""
    panel = ScrollTargetPanel(
        "Example",
        (TargetCandidate("container", Rect(0, 0, 96, 48)),),
        external_input=True,
    )
    qtbot.addWidget(panel)
    results: list[object] = []
    panel.confirmed.connect(results.append)
    control = button(panel, "Start")
    click(panel, control)
    assert not results
    panel.show()
    logical = control.mapToGlobal(control.rect().center())
    point = ScreenGeometry.from_screen(panel.screen()).to_physical(
        Point(logical.x(), logical.y())
    )
    panel.receive_input(SelectionInput(InputKind.RELEASE, point))
    assert not results
    panel.dismiss()
    click(panel, control)
    assert not results


def test_capture_router_rejects_foreign_controls_and_preserves_styles(
    qtbot: QtBot,
) -> None:
    """Explicit registration cannot route commands to another window's controls."""
    panel = CapturePanel(external_input=True)
    qtbot.addWidget(panel)
    own = QPushButton("Own", panel)
    foreign = QPushButton("Foreign")
    qtbot.addWidget(foreign)
    own.setStyleSheet("color: red;")
    with pytest.raises(ValueError, match="owned descendant"):
        panel.set_capture_controls((foreign,))
    panel.set_capture_controls((own,))
    panel.set_capture_controls(())
    assert own.styleSheet() == "color: red;"


def test_review_accepts_owned_control_without_a_native_click(
    qtbot: QtBot, tmp_path: Path
) -> None:
    """The existing canonical image/ack path remains the only delivery contract."""
    run = Run(tmp_path)
    run.start()
    run.settle()
    run.driver.stop()
    panel = ScrollReviewPanel(run.session, external_input=True)
    qtbot.addWidget(panel)
    captures: list[object] = []
    accepted: list[object] = []
    panel.reviewed_capture.connect(captures.append)
    panel.capture_accept_requested.connect(accepted.append)
    panel.show()
    click(panel, button(panel, "whole"))
    assert not captures
    assert len(accepted) == 1
    capture = accepted[0]
    assert isinstance(capture, ReviewedCapture)
    assert not panel.release_capture_delivery("stale-id")
    assert panel.release_capture_delivery(capture.capture_id)
    assert captures == [capture]
    assert not panel.release_capture_delivery(capture.capture_id)
    panel.dismiss()
    run.session.close()


def test_mixed_pointer_and_tab_cannot_activate_an_unpressed_start(qtbot: QtBot) -> None:
    """A release belongs to its pressed control, independently of keyboard selection."""
    panel = ScrollTargetPanel(
        "Example", (TargetCandidate("x", Rect(0, 0, 96, 48)),), external_input=True
    )
    qtbot.addWidget(panel)
    panel.show()
    cancel = button(panel, "Cancel")
    global_point = cancel.mapToGlobal(cancel.rect().center())
    point = ScreenGeometry.from_screen(panel.screen()).to_physical(
        Point(global_point.x(), global_point.y())
    )
    starts: list[object] = []
    cancelled: list[bool] = []
    panel.confirmed.connect(starts.append)
    panel.cancelled.connect(lambda: cancelled.append(True))
    panel.receive_input(SelectionInput(InputKind.PRESS, point))
    panel.receive_input(SelectionInput(InputKind.NEXT, point))
    panel.receive_input(SelectionInput(InputKind.RELEASE, point))
    assert not starts
    assert cancelled == [True]


def test_silent_target_retirement_is_not_user_cancellation(qtbot: QtBot) -> None:
    """Owner-driven UI replacement has no cancellation side effect."""
    panel = ScrollTargetPanel(
        "Example", (TargetCandidate("x", Rect(0, 0, 96, 48)),), external_input=True
    )
    qtbot.addWidget(panel)
    cancelled: list[bool] = []
    panel.cancelled.connect(lambda: cancelled.append(True))
    panel.show()
    panel.dismiss()
    assert not cancelled


def test_virtual_controls_handle_empty_disabled_and_combo_navigation(
    qtbot: QtBot,
) -> None:
    """Keyboard navigation never opens a native combo popup or focuses a widget."""
    panel = CapturePanel(external_input=True)
    qtbot.addWidget(panel)
    panel.show()
    point = Point(0, 0)
    panel.receive_input(SelectionInput(InputKind.ENTER, point))
    panel.receive_input(SelectionInput(InputKind.NEXT, point))
    panel.receive_input(SelectionInput(InputKind.MOVE, point, "arrow:right"))
    combo = QComboBox(panel)
    combo.addItems(("First", "Second", "Third"))
    combo.show()
    panel.set_capture_controls((combo,))
    panel.receive_input(SelectionInput(InputKind.MOVE, point, "arrow:right"))
    assert combo.currentIndex() == 1
    panel.receive_input(SelectionInput(InputKind.MOVE, point, "arrow:left"))
    assert combo.currentIndex() == 0
    panel.receive_input(SelectionInput(InputKind.ENTER, point))
    assert not combo.view().isVisible()
    combo.setEnabled(False)
    panel.receive_input(SelectionInput(InputKind.MOVE, point, "arrow:right"))
    assert combo.currentIndex() == 0
    with pytest.raises(ValueError, match="before presentation"):
        panel.use_capture_input()


def test_unrepresentable_semantic_geometry_refuses_start_without_overflow(
    qtbot: QtBot,
) -> None:
    """A provider's extreme coordinates cannot overflow Qt integer controls."""
    panel = ScrollTargetPanel(
        "Huge provider geometry",
        (TargetCandidate("huge", Rect(1e100, 0, 96, 48)),),
        external_input=True,
    )
    qtbot.addWidget(panel)
    panel.show()
    start = button(panel, "Start")
    assert not start.isEnabled()
    results: list[object] = []
    panel.confirmed.connect(results.append)
    click(panel, start)
    assert not results
