"""Owned settings setup is bounded separately from the unchanged capture budget."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QObject
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
    run.trace = subject.SettingsTrace(enabled=False)
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
        def setWindowIcon(self, _icon: object) -> None:  # noqa: N802 - Qt API name
            raise AssertionError("Constructor-only test must not set an icon")

        def exec(self) -> int:
            raise AssertionError("Constructor-only test must not execute a dialog")

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


def test_disabled_settings_trace_never_reads_a_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-settings scenarios keep the original observer without timing work."""
    clock = Mock(side_effect=AssertionError("Disabled tracing must not read clocks"))
    monkeypatch.setattr(
        subject,
        "time",
        SimpleNamespace(perf_counter=clock, process_time=clock, thread_time=clock),
    )
    trace = subject.SettingsTrace(enabled=False)
    trace.mark("disabled")
    assert trace.timeline == []
    assert trace.overflow == 0
    clock.assert_not_called()


def test_settings_trace_keeps_distinct_wall_process_and_thread_clocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall stalls remain distinguishable from CPU work on this GUI thread."""
    wall = Mock(side_effect=[10.0, 16.5])
    process = Mock(side_effect=[2.0, 2.125, 2.25])
    thread = Mock(side_effect=[1.0, 1.025, 1.05])
    monkeypatch.setattr(
        subject,
        "time",
        SimpleNamespace(perf_counter=wall, process_time=process, thread_time=thread),
    )
    trace = subject.SettingsTrace(enabled=True)
    trace.mark("show")
    trace.mark("readiness", wall=16.75)
    assert trace.timeline == [
        {
            "phase": "show",
            "wall_ms": 6500.0,
            "process_cpu_ms": 125.0,
            "thread_cpu_ms": 25.0,
        },
        {
            "phase": "readiness",
            "wall_ms": 6750.0,
            "process_cpu_ms": 250.0,
            "thread_cpu_ms": 50.0,
        },
    ]
    assert wall.call_count == 2


def test_settings_trace_is_bounded_and_reports_dropped_marks() -> None:
    """A noisy native dialog cannot grow the timing receipt without a bound."""
    trace = subject.SettingsTrace(enabled=True)
    for index in range(trace.LIMIT + 3):
        trace.mark(str(index))
    assert len(trace.timeline) == trace.LIMIT
    assert trace.timeline[0]["phase"] == "0"
    assert trace.timeline[-1]["phase"] == str(trace.LIMIT - 1)
    assert trace.overflow == 3


def test_settings_trace_forwards_one_owned_call_and_ignores_other_dialogs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Class-level instrumentation neither repeats calls nor attributes outsiders."""
    trace = subject.SettingsTrace(enabled=False)
    owned, unrelated = Mock(), Mock()
    trace.dialog = owned
    mark = Mock()
    monkeypatch.setattr(trace, "mark", mark)
    sentinel = object()
    operation = Mock(return_value=sentinel)
    assert trace.observe(unrelated, "outsider", operation) is sentinel
    mark.assert_not_called()
    assert trace.observe(owned, "exec", operation) is sentinel
    assert operation.call_count == 2
    assert [call.args for call in mark.call_args_list] == [
        ("exec.entry",),
        ("exec.return",),
    ]


@pytest.mark.parametrize("control", [False, True])
def test_settings_trace_forwards_original_exception_and_records_unwind(
    monkeypatch: pytest.MonkeyPatch, *, control: bool
) -> None:
    """Ordinary and control exceptions preserve identity without a return marker."""
    trace = subject.SettingsTrace(enabled=False)
    owned = Mock()
    trace.dialog = owned
    mark = Mock()
    monkeypatch.setattr(trace, "mark", mark)
    failure = KeyboardInterrupt("owned") if control else RuntimeError("owned")
    operation = Mock(side_effect=failure)
    with pytest.raises(type(failure)) as caught:
        trace.observe(owned, "constructor", operation)
    assert caught.value is failure
    operation.assert_called_once_with()
    assert [call.args for call in mark.call_args_list] == [
        ("constructor.entry",),
        ("constructor.raise",),
    ]


@pytest.mark.parametrize("handled", [False, True])
def test_settings_trace_preserves_input_filter_and_exact_event_scope(
    monkeypatch: pytest.MonkeyPatch, *, handled: bool
) -> None:
    """Timing only selected owned events never changes the existing filter result."""
    trace = subject.SettingsTrace(enabled=False)
    owned, unrelated = QObject(), QObject()
    monkeypatch.setattr(trace, "dialog", owned)
    mark = Mock()
    monkeypatch.setattr(trace, "mark", mark)
    original_filter = Mock(return_value=handled)
    monkeypatch.setattr(subject.InputTrace, "eventFilter", original_filter)
    show = QEvent(QEvent.Type.Show)
    ignored = QEvent(QEvent.Type.Hide)
    assert trace.eventFilter(unrelated, show) is handled
    assert trace.eventFilter(owned, ignored) is handled
    mark.assert_not_called()
    assert trace.eventFilter(owned, show) is handled
    mark.assert_called_once_with("event.Show")
    assert [call.args for call in original_filter.call_args_list] == [
        (unrelated, show),
        (owned, ignored),
        (owned, show),
    ]


def test_settings_failure_serializes_same_timeline_in_log_and_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed setup retains timing evidence in both existing diagnostic outputs."""
    run, _dialog, _posted = make_run(monkeypatch)
    run.errors = ["Owned settings setup never became ready before the deadline"]
    run.outcome = "escape"
    run.posted = False
    run.triggered = False
    run.expired_overlay_count = 0
    run.expired_selecting = False
    run.trace.timeline = [
        {
            "phase": "exec.entry",
            "wall_ms": 17.0,
            "process_cpu_ms": 12.0,
            "thread_cpu_ms": 10.0,
        }
    ]
    run.trace.overflow = 2
    run.record(tmp_path, "settings", [])
    line = capsys.readouterr().out.strip()
    assert line.startswith("NATIVE_CAPTURE_STAGE=")
    logged = json.loads(line.removeprefix("NATIVE_CAPTURE_STAGE="))
    receipt = json.loads((tmp_path / "native-capture-trace.json").read_text())
    assert logged["settings_timeline"] == receipt["settings_timeline"]
    assert receipt["settings_timeline"] == run.trace.timeline
    assert logged["settings_timeline_overflow"] == 2
    assert receipt["settings_timeline_overflow"] == 2
    assert logged["hotkey_posted"] is False
    assert logged["freeze_count"] == 0
    assert receipt["frames"] == []
    assert receipt["errors"] == run.errors
