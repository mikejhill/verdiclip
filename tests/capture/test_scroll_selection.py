"""Point-selection ownership and final publication without native UIA."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scroll_selection import ScrollingCapture, ScrollSelection
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.hotkeys import Hotkey, HotkeyService
from verdiclip.platform.windows import WindowInfo

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


@pytest.fixture
def operation(qtbot: QtBot) -> Iterator[tuple[ScrollSelection, ScrollHelper, Mock]]:
    """Real Qt ownership with only native state and child startup substituted."""
    del qtbot
    image = QImage(2000, 2000, QImage.Format.Format_RGB32)
    image.fill(0xFF345678)
    screens = Mock()
    screens.freeze.return_value = FrozenScreen(image, Rect(0, 0, 2000, 2000))
    state = Mock()
    state.windows.return_value = [WindowInfo(123, "Document", Rect(0, 0, 1000, 1000))]
    state.tick.return_value = 456
    state.foreground.return_value = 123
    state.observe.return_value = 123
    hotkeys = HotkeyService(Mock())
    helper = ScrollHelper()
    helper.start = Mock()
    helper.cancel = Mock()
    service = ScrollSelection(
        screens, hotkeys, state=state, helper_factory=lambda: helper
    )
    try:
        yield service, helper, state
    finally:
        service.shutdown()
        hotkeys.close()


def start(qtbot: QtBot, service: ScrollSelection, helper: ScrollHelper) -> None:
    """Finish one point selection through the actual queued handoff."""
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    assert not service.overlays
    starting = helper.start
    assert isinstance(starting, Mock)
    qtbot.waitUntil(lambda: starting.called)
    starting.assert_called_once_with(Point(30, 40), window=123, input_tick=456)
    assert service.busy


@pytest.mark.parametrize("cancel", [False, True])
def test_one_complete_image_or_cancel_before_queued_publication(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock], *, cancel: bool
) -> None:
    """A terminal child result is not permission to deliver after a queued cancel."""
    service, helper, state = operation
    captures: list[ScrollingCapture] = []
    failures = Mock()
    service.failed.connect(failures)
    service.captured.connect(captures.append)
    start(qtbot, service, helper)
    image = QImage(20, 500, QImage.Format.Format_RGB32)
    image.fill(0xFF123456)
    helper.finished.emit(ScrollResult(image, "", "restored", 3))
    assert not captures
    assert service.busy
    if cancel:
        service.cancel()
    qtbot.waitUntil(lambda: not service.busy)
    assert len(captures) == (0 if cancel else 1)
    if captures:
        assert captures[0].image == image
        assert captures[0].title == "Document"
        assert not hasattr(captures[0], "area")
        assert state.check.call_count == 2
    assert "scroll_cancel" not in service._hotkeys.registered
    failures.assert_not_called()


def test_changed_input_after_child_exit_withholds_image(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The parent preserves the release baseline through its last queued turn."""
    service, helper, state = operation
    captures = Mock()
    service.captured.connect(captures)
    start(qtbot, service, helper)
    state.check.side_effect = CaptureError("Input changed")
    with qtbot.waitSignal(service.failed) as failed:
        helper.finished.emit(
            ScrollResult(QImage(20, 50, QImage.Format.Format_RGB32), "", "restored", 3)
        )
    assert "Input changed" in failed.args[0]
    assert "stage=publication" in caplog.text
    captures.assert_not_called()
    assert not service.busy


def test_cancel_waits_for_helper_retirement_and_preserves_other_hotkeys(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Cancellation cannot start another child or discard unrelated bindings."""
    service, helper, _state = operation
    service._hotkeys.register("ordinary", Hotkey.parse("print_screen"))
    start(qtbot, service, helper)
    service._hotkeys.triggered.emit("scroll_cancel")
    assert service.busy
    assert isinstance(helper.cancel, Mock)
    helper.cancel.assert_called_once()
    service.capture()
    assert isinstance(helper.start, Mock)
    assert helper.start.call_count == 1
    helper.finished.emit(ScrollResult(None, "Cancelled", "not restored", 1))
    qtbot.waitUntil(lambda: not service.busy)
    assert list(service._hotkeys.registered) == ["ordinary"]


@pytest.mark.parametrize("boundary", ["queued_show", "selector", "queued_launch"])
def test_cancel_before_launch_never_starts_helper(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock], boundary: str
) -> None:
    """Every pre-child boundary remains cancellable without provider work."""
    service, helper, _state = operation
    service.capture()
    if boundary != "queued_show":
        qtbot.waitUntil(lambda: bool(service.overlays))
    if boundary == "queued_launch":
        service._selected(Point(30, 40))
    service.cancel()
    qtbot.wait(10)
    assert not service.busy
    assert not service.overlays
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    assert "scroll_cancel" not in service._hotkeys.registered


@pytest.mark.parametrize("failure", ["state", "escape", "start"])
def test_launch_failure_has_no_image_and_releases_temporary_escape(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each start failure retires the session without touching ordinary capture."""
    service, helper, state = operation
    if failure == "state":
        state.check.side_effect = CaptureError("Target changed")
    elif failure == "escape":
        assert isinstance(service._hotkeys._api, Mock)
        service._hotkeys._api.register.return_value = False
    else:
        assert isinstance(helper.start, Mock)
        helper.start.side_effect = OSError("Cannot start")
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed):
        service._selected(Point(30, 40))
    assert not service.busy
    assert "stage=launch" in caplog.text
    assert not service.overlays
    assert "scroll_cancel" not in service._hotkeys.registered


def test_selection_of_no_window_fails_before_hotkey_or_helper(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Desktop/gap selection is not silently retargeted to a different window."""
    service, helper, _state = operation
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed):
        service._selected(Point(1900, 1900))
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    assert not service.busy


def test_shutdown_rejects_even_a_success_already_queued(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Shutdown closes all owned surfaces and never delivers a late image."""
    service, helper, _state = operation
    captures = Mock()
    service.captured.connect(captures)
    start(qtbot, service, helper)
    helper.finished.emit(
        ScrollResult(QImage(10, 20, QImage.Format.Format_RGB32), "", "restored", 2)
    )
    service.shutdown()
    qtbot.wait(10)
    captures.assert_not_called()
    assert not service.busy
    assert "scroll_cancel" not in service._hotkeys.registered


def test_failed_exit_retains_owner_until_later_terminal_result(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
) -> None:
    """An exit refusal must not strand busy state, Escape or later selection."""
    service, helper, _state = operation
    captures = Mock()
    service.captured.connect(captures)
    start(qtbot, service, helper)
    shutdown = helper.shutdown
    monkeypatch.setattr(
        helper, "shutdown", Mock(side_effect=CaptureError("Still running"))
    )
    with pytest.raises(CaptureError, match="Still running"):
        service.shutdown()
    assert service.busy
    assert "scroll_cancel" in service._hotkeys.registered
    assert not service._closing
    monkeypatch.setattr(helper, "shutdown", shutdown)
    helper.finished.emit(
        ScrollResult(QImage(10, 20, QImage.Format.Format_RGB32), "", "restored", 2)
    )
    qtbot.waitUntil(lambda: not service.busy)
    captures.assert_not_called()
    assert "scroll_cancel" not in service._hotkeys.registered
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service.cancel()
    assert not service.busy


def test_lazy_native_state_and_unavailable_screen_fail_visibly(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
) -> None:
    """Startup does not require native UIA, and missing displays do not hang."""
    service, _helper, state = operation
    service._state = None
    monkeypatch.setattr(
        "verdiclip.capture.scroll_selection.ScrollSelectionState", lambda: state
    )
    monkeypatch.setattr(
        "verdiclip.capture.scroll_selection.QGuiApplication.screens", list
    )
    with qtbot.waitSignal(service.failed) as failure:
        service.capture()
    assert "No screen" in failure.args[0]
    assert not service.busy


@pytest.mark.parametrize("closes", [False, True])
def test_initiating_popup_is_dismissed_or_fails_without_stealing_input(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    *,
    closes: bool,
) -> None:
    """The chooser cannot launch beneath an uncooperative popup."""
    service, helper, _state = operation
    popup = Mock()
    popup.close.return_value = closes
    active = Mock(side_effect=[popup, None] if closes else [popup])
    monkeypatch.setattr(
        "verdiclip.capture.scroll_selection.QApplication.activePopupWidget", active
    )
    service.capture()
    popup.close.assert_called_once()
    assert service.busy == closes
    service.cancel()
    qtbot.wait(0)
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


@pytest.mark.parametrize("owner", ["popup", "mouse", "keyboard"])
def test_foreign_input_owner_prevents_selector_start(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    owner: str,
) -> None:
    """A later popup or grab is not dismissed to force selection through it."""
    service, helper, _state = operation
    service.capture()
    target = {
        "popup": "QApplication.activePopupWidget",
        "mouse": "QWidget.mouseGrabber",
        "keyboard": "QWidget.keyboardGrabber",
    }[owner]
    monkeypatch.setattr(f"verdiclip.capture.scroll_selection.{target}", Mock)
    with qtbot.waitSignal(service.failed) as failure:
        pass
    assert "holding input" in failure.args[0]
    assert not service.busy
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


@pytest.mark.parametrize(
    "lost", ["application", "window", "mouse", "keyboard", "popup"]
)
def test_selector_loses_input_without_ever_starting_child(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    lost: str,
) -> None:
    """Selecting never fights another application's focus or input grab."""
    service, helper, _state = operation
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    active = service.overlays[0]
    monkeypatch.setattr(QApplication, "activeWindow", lambda: active)
    if lost == "application":
        service.eventFilter(service, QEvent(QEvent.Type.ApplicationDeactivate))
    elif lost == "window":
        monkeypatch.setattr(QApplication, "activeWindow", lambda: None)
    elif lost == "popup":
        monkeypatch.setattr(QApplication, "activePopupWidget", Mock)
    else:
        monkeypatch.setattr(QWidget, f"{lost}Grabber", QWidget)
    service._check_selector()
    assert not service.busy
    assert not service.overlays
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    service._check_selector()


def test_owned_selector_focus_and_grab_are_kept(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
) -> None:
    """Ordinary selector activation is not itself an input-cancellation reason."""
    service, _helper, _state = operation
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    active = service.overlays[0]
    monkeypatch.setattr(QApplication, "activeWindow", lambda: active)
    monkeypatch.setattr(QWidget, "mouseGrabber", lambda: active)
    service._check_selector()
    assert service.busy
    service.cancel()


def test_failed_start_with_live_child_keeps_owner_until_actual_exit(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
) -> None:
    """An unconfirmed startup cleanup cannot enable a concurrent capture."""
    service, helper, _state = operation
    shutdown = helper.shutdown
    states: list[tuple[bool, bool]] = []
    service.state_changed.connect(
        lambda active, cancelling: states.append((active, cancelling))
    )

    def refuse_shutdown() -> None:
        assert states == [(True, False), (True, True)]
        raise CaptureError("Still running")

    monkeypatch.setattr(helper, "start", Mock(side_effect=CaptureError("Start failed")))
    monkeypatch.setattr(helper, "shutdown", refuse_shutdown)
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed) as failure:
        service._selected(Point(30, 40))
    assert "Still running" in failure.args[0]
    assert service.busy
    assert states == [(True, False), (True, True)]
    assert "scroll_cancel" in service._hotkeys.registered
    monkeypatch.setattr(helper, "shutdown", shutdown)
    helper.finished.emit(ScrollResult(None, "Stopped", "unknown", 0))
    qtbot.waitUntil(lambda: not service.busy)
    assert states == [(True, False), (True, True), (False, True)]
    assert "scroll_cancel" not in service._hotkeys.registered


def test_repeated_idle_shutdown_does_not_emit_another_terminal_signal(
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
) -> None:
    """Retirement is idempotent even when controller teardown follows completion."""
    service, _helper, _state = operation
    finished = Mock()
    service.finished.connect(finished)
    service.shutdown()
    service.shutdown()
    finished.assert_not_called()


def test_null_foreground_settles_without_activation_or_rebasing(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """A chooser's transient NULL foreground is not a lost user selection."""
    service, helper, state = operation
    state.observe.side_effect = [0, 123]
    start(qtbot, service, helper)
    state.activate.assert_not_called()
    state.tick.assert_called_once()
    window = state.windows.return_value[0]
    assert state.observe.call_args_list[0].args == (Point(30, 40), window, 456)
    assert state.observe.call_args_list[1].args == (Point(30, 40), window, 456)


def test_chosen_background_window_gets_one_activation_then_read_only_settle(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Delayed Windows activation is observed without repeated focus requests."""
    service, helper, state = operation
    state.foreground.return_value = 900
    state.observe.side_effect = [900, 900, 0, 123]
    start(qtbot, service, helper)
    state.activate.assert_called_once_with(
        Point(30, 40), state.windows.return_value[0], 456, 900
    )
    state.tick.assert_called_once()
    assert state.observe.call_count == 4
    assert isinstance(service._hotkeys._api, Mock)
    assert service._hotkeys._api.register.call_count == 1


@pytest.mark.parametrize("foregrounds", [[901], [900, 901]])
def test_foreign_focus_aborts_without_activation_retry(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    foregrounds: list[int],
) -> None:
    """An unrelated foreground before or after activation is never stolen back."""
    service, helper, state = operation
    state.foreground.return_value = 900
    state.observe.side_effect = foregrounds
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed) as failure:
        service._selected(Point(30, 40))
    assert "foreground window changed" in failure.args[0]
    assert state.activate.call_count == len(foregrounds) - 1
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    assert not service.busy


@pytest.mark.parametrize("change", ["input", "hit", "geometry"])
def test_changed_selection_while_activation_is_pending_never_starts_helper(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    change: str,
) -> None:
    """Every settling read retains the exact original selection guards."""
    service, helper, state = operation
    state.foreground.return_value = 900
    state.observe.side_effect = [900, CaptureError(f"{change} changed")]
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed) as failure:
        service._selected(Point(30, 40))
    assert change in failure.args[0]
    state.activate.assert_called_once()
    state.tick.assert_called_once()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


@pytest.mark.parametrize("pending", [0, 900])
def test_settle_timeout_is_bounded_without_activation_retry(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    pending: int,
) -> None:
    """NULL or unchanged focus after one request ends visibly with no child."""
    service, helper, state = operation
    state.foreground.return_value = 900
    state.observe.return_value = pending
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    service._null_until = 0
    service._launch()
    state.activate.assert_called_once()
    service._handoff_until = 0
    with qtbot.waitSignal(service.failed) as failure:
        service._launch()
    assert "did not activate" in failure.args[0]
    state.activate.assert_called_once()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    assert not service.busy


def test_activation_refusal_fails_without_retry_or_helper(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Windows can refuse the one ordinary request, and that refusal is final."""
    service, helper, state = operation
    state.foreground.return_value = state.observe.return_value = 900
    state.activate.side_effect = CaptureError("Windows refused activation")
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    with qtbot.waitSignal(service.failed) as failure:
        service._selected(Point(30, 40))
    assert "refused" in failure.args[0]
    qtbot.wait(40)
    state.activate.assert_called_once()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


@pytest.mark.parametrize("pending", [0, 900])
@pytest.mark.parametrize("shutdown", [False, True])
def test_pending_handoff_can_be_cancelled_without_later_helper_or_error(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    pending: int,
    *,
    shutdown: bool,
) -> None:
    """Escape and teardown retire both NULL dismissal and activation waits."""
    service, helper, state = operation
    state.foreground.return_value = 900
    state.observe.return_value = pending
    failures = Mock()
    service.failed.connect(failures)
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    service._launch()
    assert "scroll_cancel" in service._hotkeys.registered
    assert state.activate.call_count == bool(pending)
    if shutdown:
        service.shutdown()
    else:
        service._hotkeys.triggered.emit("scroll_cancel")
    state.observe.return_value = 123
    qtbot.wait(40)
    service._launch()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    failures.assert_not_called()
    assert "scroll_cancel" not in service._hotkeys.registered
    assert not service.busy


def test_late_activation_completion_does_not_extend_handoff_deadline(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """An eventual foreground observation cannot resurrect an expired handoff."""
    service, helper, state = operation
    state.foreground.return_value = state.observe.return_value = 900
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    service._launch()
    service._handoff_until = 0
    state.observe.return_value = 123
    with qtbot.waitSignal(service.failed):
        service._launch()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
    state.activate.assert_called_once()


def test_repeated_choice_during_settle_cannot_rebase_or_retarget(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Only the first completed gesture owns the chosen point and release tick."""
    service, helper, state = operation
    state.foreground.return_value = state.observe.return_value = 900
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    service._launch()
    state.tick.return_value = 789
    service._selected(Point(35, 45))
    state.observe.return_value = 123
    service._launch()
    state.tick.assert_called_once()
    state.activate.assert_called_once()
    assert isinstance(helper.start, Mock)
    helper.start.assert_called_once_with(Point(30, 40), window=123, input_tick=456)


def test_unknown_prechooser_foreground_is_not_rebased_to_later_foreign_window(
    qtbot: QtBot,
    operation: tuple[ScrollSelection, ScrollHelper, Mock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A NULL initial baseline cannot identify a later foreign window as safe."""
    service, helper, state = operation
    state.foreground.return_value = 0
    state.observe.side_effect = [0, 900]
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    service._selected(Point(30, 40))
    service._launch()
    state.foreground.return_value = 900
    with qtbot.waitSignal(service.failed) as failure:
        service._launch()
    assert "foreground window changed" in failure.args[0]
    assert "saved_foreground=0 foreground=900" in caplog.text
    assert "activation_requested=False" in caplog.text
    state.activate.assert_not_called()
    state.tick.assert_called_once()
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()


def test_failed_diagnostic_read_does_not_replace_handoff_error(
    qtbot: QtBot, operation: tuple[ScrollSelection, ScrollHelper, Mock]
) -> None:
    """Failure logging is read-only and cannot prevent session retirement."""
    service, helper, state = operation
    service.capture()
    qtbot.waitUntil(lambda: bool(service.overlays))
    state.foreground.side_effect = OSError("Foreground unavailable")
    state.observe.side_effect = CaptureError("Original selection changed")
    with qtbot.waitSignal(service.failed) as failure:
        service._selected(Point(30, 40))
    assert failure.args[0] == "Original selection changed"
    assert not service.busy
    assert isinstance(helper.start, Mock)
    helper.start.assert_not_called()
