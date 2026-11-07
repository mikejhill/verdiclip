"""Owned settings setup is bounded separately from the unchanged capture budget."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from tests.capture import test_capture_native as subject


def as_mock(value: object) -> Mock:
    """Narrow the explicitly injected mock, never a real Qt method."""
    assert isinstance(value, Mock)
    return value


def make_run(monkeypatch: pytest.MonkeyPatch) -> tuple[subject.NativeRun, Mock, Mock]:
    """Create only the boundary state, without native input or a QApplication."""
    run = subject.NativeRun.__new__(subject.NativeRun)
    run.done = False
    run.phase = "settings-setup"
    run.setup_started = 0.0
    run.setup_elapsed_ms = None
    run.settings_dialog = None
    run.existing_settings = ()
    run.errors = []
    dialog = Mock()
    dialog.isVisible.return_value = True
    dialog.isModal.return_value = True
    dialog.windowHandle.return_value.isExposed.return_value = True
    monkeypatch.setattr(subject, "SettingsDialog", Mock)
    monkeypatch.setattr(
        subject,
        "QApplication",
        SimpleNamespace(
            topLevelWidgets=lambda: [dialog],
            activeModalWidget=lambda: dialog,
        ),
    )
    monkeypatch.setattr(subject, "time", SimpleNamespace(perf_counter=lambda: 1.0))
    monkeypatch.setattr(run, "readiness", Mock(), raising=False)
    monkeypatch.setattr(run, "deadline", Mock(), raising=False)
    posted = Mock()
    monkeypatch.setattr(run, "post_hotkey", posted)
    monkeypatch.setattr(run, "finish", Mock())
    monkeypatch.setattr(run, "_expired", Mock())
    return run, dialog, posted


def test_ready_dispatches_once_with_unchanged_action_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate readiness cannot dispatch again or extend an action deadline."""
    run, dialog, posted = make_run(monkeypatch)
    run._settings_ready()
    run._settings_ready()
    assert run.settings_dialog is dialog
    assert run.phase == "capture-action"
    assert run.setup_elapsed_ms == 1000
    posted.assert_called_once_with()
    as_mock(run.deadline.start).assert_called_once_with(5000)
    as_mock(run.readiness.stop).assert_called_once_with()


@pytest.mark.parametrize(
    "boundary", ["visible", "modal", "exposed", "missing-handle", "active", "existing"]
)
def test_unready_owned_dialog_cannot_dispatch(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Construction alone, or some other active dialog, is not readiness."""
    run, dialog, posted = make_run(monkeypatch)
    if boundary == "visible":
        dialog.isVisible.return_value = False
    elif boundary == "modal":
        dialog.isModal.return_value = False
    elif boundary == "exposed":
        dialog.windowHandle.return_value.isExposed.return_value = False
    elif boundary == "missing-handle":
        dialog.windowHandle.return_value = None
    elif boundary == "active":
        monkeypatch.setattr(subject.QApplication, "activeModalWidget", lambda: None)
    else:
        run.existing_settings = (dialog,)
    run._settings_ready()
    posted.assert_not_called()
    as_mock(run.deadline.start).assert_not_called()
    assert run.phase == "settings-setup"


@pytest.mark.parametrize("ticks", [[5.0], [4.9, 5.1]])
def test_expired_or_late_ready_never_starts_capture(
    monkeypatch: pytest.MonkeyPatch, ticks: list[float]
) -> None:
    """Readiness returned after deadline cannot turn setup timeout into success."""
    run, _dialog, posted = make_run(monkeypatch)
    clock = iter(ticks)
    monkeypatch.setattr(
        subject, "time", SimpleNamespace(perf_counter=lambda: next(clock))
    )
    run._settings_ready()
    as_mock(run._expired).assert_called_once_with()
    posted.assert_not_called()
    as_mock(run.deadline.start).assert_not_called()


def test_readiness_error_is_latched_and_closes_owned_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A destroyed dialog cannot raise an untracked ordinary Qt callback error."""
    run, dialog, posted = make_run(monkeypatch)
    dialog.windowHandle.side_effect = RuntimeError("private detail")
    run._settings_ready()
    assert run.errors == ["Owned settings readiness observation failed"]
    as_mock(run.finish).assert_called_once_with()
    posted.assert_not_called()


def test_close_disconnects_all_observers_despite_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed capture cleanup cannot leak a hotkey observer into the next test."""
    run, _dialog, _posted = make_run(monkeypatch)
    failure = RuntimeError("owned cancellation failure")
    monkeypatch.setattr(run, "finish", Mock(side_effect=failure))
    monkeypatch.setattr(run, "qt_app", Mock(), raising=False)
    monkeypatch.setattr(run, "app", Mock(), raising=False)
    monkeypatch.setattr(run, "trace", Mock(), raising=False)
    with pytest.raises(RuntimeError) as caught:
        run.close()
    assert caught.value is failure
    as_mock(run.qt_app.focusWindowChanged.disconnect).assert_called_once()
    as_mock(run.qt_app.removeEventFilter).assert_called_once_with(run.trace)
    as_mock(run.app.hotkeys.triggered.disconnect).assert_called_once()


def test_timeout_latches_setup_and_precleanup_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timer-first expiration retains setup time and state before cleanup clears it."""
    run, _dialog, _posted = make_run(monkeypatch)
    capture = SimpleNamespace(overlays=(object(),), is_selecting=True)
    monkeypatch.setattr(
        run,
        "app",
        SimpleNamespace(controller=SimpleNamespace(capture=capture)),
        raising=False,
    )
    monkeypatch.setattr(subject, "time", SimpleNamespace(perf_counter=lambda: 5.2))
    subject.NativeRun._expired(run)
    assert run.setup_elapsed_ms == 5200
    assert run.expired_overlay_count == 1
    assert run.expired_selecting is True
    assert run.errors == ["Owned settings setup never became ready before the deadline"]
    as_mock(run.finish).assert_called_once_with()
