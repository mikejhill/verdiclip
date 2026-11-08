"""Portable chooser-proof checks; real focus/input still require hosted Windows."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from scripts.simple_scroll_handoff import Desktop, Handoff, PointOverlay

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def overlay(qtbot: QtBot) -> PointOverlay:
    """Create the same test-only subclass over an ordinary frozen screen."""
    screen = QApplication.primaryScreen()
    assert screen is not None
    geometry = ScreenGeometry.from_screen(screen)
    result = PointOverlay(
        geometry,
        screen,
        FrozenScreen(screen.grabWindow(0).toImage(), geometry.physical),
        [],
        show_magnifier=False,
    )
    qtbot.addWidget(result)
    result.show()
    return result


def test_point_selection_waits_for_completed_mouse_release(qtbot: QtBot) -> None:
    """Press alone cannot start a helper; release emits a physical point once."""
    widget = overlay(qtbot)
    points: list[Point] = []
    widget.chosen.connect(points.append)
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(30, 40))
    assert points == []
    qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(30, 40))
    assert points == [widget.screen_geometry.to_physical(Point(30, 40))]
    assert not widget.shared.pressed
    widget.dismiss()


def test_point_selection_ignores_enter_press_and_auto_repeat(qtbot: QtBot) -> None:
    """The normal key release belongs to selection, not capture interference."""
    widget = overlay(qtbot)
    point = widget.screen_geometry.to_physical(Point(30, 40))
    widget.shared.move_to(point)
    points: list[Point] = []
    widget.chosen.connect(points.append)
    qtbot.keyPress(widget, Qt.Key.Key_Return)
    assert points == []
    event = QKeyEvent(
        QEvent.Type.KeyRelease,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.NoModifier,
        "",
        autorep=True,
    )
    widget.keyReleaseEvent(event)
    assert points == []
    qtbot.keyRelease(widget, Qt.Key.Key_Return)
    assert points == [point]
    widget.dismiss()


def test_point_escape_cancels_without_selection(qtbot: QtBot) -> None:
    """Prototype preserves the ordinary overlay's Escape cancellation."""
    widget = overlay(qtbot)
    chosen = Mock()
    widget.chosen.connect(chosen)
    with qtbot.waitSignal(widget.cancelled):
        qtbot.keyClick(widget, Qt.Key.Key_Escape)
    chosen.assert_not_called()
    widget.dismiss()


def handoff(tmp_path: Path) -> Handoff:
    """Avoid real native launch while preserving the actual handoff methods."""
    result = object.__new__(Handoff)
    result.kind, result.output, result.report = "edge-select", tmp_path, {}
    result.window = WindowInfo(123, "Selected document", Rect(0, 0, 500, 500))
    result.windows = [result.window]
    result.desktop = Mock()
    result.desktop.input_tick.return_value = 456
    result.desktop.foreground.return_value = 123
    result.overlays = []
    return result


def test_release_tick_is_retained_across_queued_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Later input cannot silently become a fresh handoff eligibility baseline."""
    proof = handoff(tmp_path)
    queued: list[Callable[[], None]] = []
    monkeypatch.setattr(
        "scripts.simple_scroll_handoff.QTimer.singleShot",
        lambda _delay, callback: queued.append(callback),
    )
    selector = Mock()
    proof.overlays = [selector]
    callback = Mock()
    monkeypatch.setattr(proof, "_guarded", lambda call: call())
    monkeypatch.setattr(proof, "_handoff", callback)
    proof._selected(Point(30, 40))
    selector.dismiss.assert_called_once()
    assert proof.overlays == []
    callback.assert_not_called()
    assert isinstance(proof.desktop, Mock)
    proof.desktop.input_tick.return_value = 789
    queued[0]()
    callback.assert_called_once_with(Point(30, 40), proof.window, 456)
    assert proof.report["release_input_tick"] == 456


@pytest.mark.parametrize("changed", ["input", "focus"])
def test_queued_handoff_rejects_changed_state_without_starting_helper(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: str
) -> None:
    """Changes while selectors close are rejected before any child is started."""
    del qtbot
    proof = handoff(tmp_path)
    assert isinstance(proof.desktop, Mock)
    if changed == "input":
        proof.desktop.input_tick.return_value = 789
    else:
        proof.desktop.foreground.return_value = 999
    capture = Mock()
    monkeypatch.setattr("scripts.simple_scroll_handoff._capture", capture)
    with pytest.raises(RuntimeError, match=r"Input changed|focus did not return"):
        proof._handoff(Point(30, 40), proof.window, 456)
    capture.assert_not_called()


@pytest.mark.parametrize("changed", ["none", "foreground", "hit"])
def test_native_input_is_restricted_to_owned_hit_and_focus(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """The hosted test refuses to click a foreign foreground or covered widget."""
    widget = overlay(qtbot)
    user = Mock()
    user.GetForegroundWindow.return_value = (
        9 if changed == "foreground" else int(widget.winId())
    )
    user.GetAncestor.return_value = 9 if changed == "hit" else int(widget.winId())
    user.SetCursorPos.return_value = True
    monkeypatch.setattr("ctypes.WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    desktop = Desktop()
    if changed == "none":
        desktop.click(widget, Point(30, 40))
        assert user.mouse_event.call_args_list[0].args == (2, 0, 0, 0, 0)
        assert user.mouse_event.call_args_list[1].args == (4, 0, 0, 0, 0)
        desktop.click(widget, Point(30, 40), enter=True)
        assert user.keybd_event.call_args_list[1].args == (0x0D, 0, 2, 0)
        assert user.WindowFromPoint.argtypes == (w.POINT,)
        assert user.mouse_event.argtypes[-1] == c.c_size_t
    else:
        with pytest.raises(RuntimeError):
            desktop.click(widget, Point(30, 40))
        user.mouse_event.assert_not_called()
        user.keybd_event.assert_not_called()
    widget.dismiss()


@pytest.mark.parametrize("observed", [False, True])
def test_native_input_observation_is_bounded_and_never_rebases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, observed: bool
) -> None:
    """One delayed input event must be observed; a stale tick cannot be accepted."""
    proof = handoff(tmp_path)
    assert isinstance(proof.desktop, Mock)
    proof.desktop.input_tick.side_effect = [456, 456, 789] if observed else [456] * 20
    waits = Mock()
    monkeypatch.setattr("scripts.simple_scroll_handoff.QTest.qWait", waits)
    inject = Mock()
    monkeypatch.setattr("scripts.simple_scroll_handoff._real_mouse_input", inject)
    if observed:
        proof._interfere_input(456)
        assert waits.call_count == 4
    else:
        with pytest.raises(RuntimeError, match="Input interference"):
            proof._interfere_input(456)
        assert waits.call_count == 21
    inject.assert_called_once()
    assert waits.call_args_list[0].args == (20,)
