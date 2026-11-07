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
    run.first_setup_callback_ms = None
    run.constructor_return_ms = None
    run.constructor_returned_after_timeout = False
    run.first_readiness = None
    run.last_readiness = None
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


def test_first_and_last_readiness_preserve_actual_observed_predicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later ready observation never overwrites the initial refusal snapshot."""
    run, dialog, posted = make_run(monkeypatch)
    dialog.isVisible.return_value = False
    run._settings_ready()
    first = run.first_readiness
    assert first == {
        "elapsed_ms": 1000,
        "new_dialog_count": 1,
        "visible": False,
        "modal": True,
        "active": True,
        "exposed": True,
    }
    dialog.isVisible.return_value = True
    run._settings_ready()
    assert run.first_readiness == first
    assert run.last_readiness is not first
    assert run.last_readiness is not None
    assert run.last_readiness["visible"] is True
    assert run.first_setup_callback_ms == 1000
    posted.assert_called_once_with()


def test_first_callback_already_late_does_not_invent_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An overdue first callback records its time without probing after deadline."""
    run, dialog, posted = make_run(monkeypatch)
    monkeypatch.setattr(subject, "time", SimpleNamespace(perf_counter=lambda: 5.2))
    run._settings_ready()
    assert run.first_setup_callback_ms == 5200
    assert run.first_readiness is None
    assert run.last_readiness is None
    dialog.windowHandle.assert_not_called()
    posted.assert_not_called()


@pytest.mark.parametrize(
    "outcome", ["returned", "raised", "control", "returned-after-timeout"]
)
def test_actual_constructor_wrapper_preserves_call_exception_and_scope(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Only actual successful constructor return gets a marker, even after timeout."""
    run, _dialog, _posted = make_run(monkeypatch)
    failure = (
        KeyboardInterrupt("owned control")
        if outcome == "control"
        else RuntimeError("owned construction failure")
    )
    calls: list[tuple[object, object, object]] = []
    snapshot: dict[str, bool | int | float | None] = {"visible": False}
    run.first_readiness = snapshot.copy()
    run.last_readiness = snapshot.copy()

    class OwnedDialog:
        def __init__(
            self, settings: object, parent: object = None, *, key_capture: object = None
        ) -> None:
            calls.append((settings, parent, key_capture))
            assert run.constructor_return_ms is None
            if outcome in {"raised", "control"}:
                raise failure
            if outcome == "returned-after-timeout":
                run.done = True

    original = OwnedDialog.__init__
    settings, parent, key_capture = object(), object(), object()
    action = Mock(
        side_effect=lambda: OwnedDialog(settings, parent, key_capture=key_capture)
    )
    monkeypatch.setattr(subject, "SettingsDialog", OwnedDialog)
    monkeypatch.setattr(
        run,
        "app",
        SimpleNamespace(
            controller=SimpleNamespace(
                tray=SimpleNamespace(action=lambda _: SimpleNamespace(trigger=action))
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(run, "await_settings", Mock())
    if outcome in {"raised", "control"}:
        with pytest.raises(type(failure)) as caught:
            run.open_dialog("settings")
        assert caught.value is failure
        assert run.constructor_return_ms is None
    else:
        run.open_dialog("settings")
        assert run.constructor_return_ms == 1000
        assert run.constructor_returned_after_timeout is (
            outcome == "returned-after-timeout"
        )
    assert calls == [(settings, parent, key_capture)]
    assert OwnedDialog.__init__ is original
    assert run.first_readiness == snapshot
    assert run.last_readiness == snapshot
    action.assert_called_once_with()
