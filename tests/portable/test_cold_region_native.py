"""Cold paint evidence latches first values without touching a native desktop."""

import json
import math
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock

import pytest
from scripts import cold_region_native as subject
from scripts.cold_region_native import FirstPaints, NoWindows, wait_for


def test_first_paint_is_immutable_for_each_window() -> None:
    """A faster second paint must not hide a failed first-paint budget."""
    paints = FirstPaints()
    paints.record(1, 2000)
    paints.record(1, 20)
    assert not paints.complete([1, 2], 2)
    paints.record(2, 2100)
    assert paints.complete([1, 2], 2)
    assert paints.values == {1: 2000, 2: 2100}
    assert not paints.complete([], 0)


@pytest.mark.parametrize("elapsed", [-1, math.inf, math.nan])
def test_invalid_paint_time_cannot_qualify(elapsed: float) -> None:
    """Malformed clocks remain missing evidence, never a favorable timing."""
    paints = FirstPaints()
    with pytest.raises(ValueError, match="Invalid owned paint"):
        paints.record(1, elapsed)
    assert not paints.complete([1], 1)


def test_invalid_window_cannot_qualify() -> None:
    """No desktop/global cursor handle can stand in for an owned HWND."""
    paints = FirstPaints()
    with pytest.raises(ValueError, match="Invalid owned paint"):
        paints.record(0, 10)
    assert paints.values == {}


def test_region_fixture_never_enumerates_foreign_windows() -> None:
    """Window picking is deliberately absent from this first-region observation."""
    source = NoWindows()
    assert source.visible_windows() == ()
    assert source.foreground() is None


def test_partial_or_duplicate_overlay_list_never_counts_as_all_monitors() -> None:
    """A successfully painted first overlay cannot hide a failed second creation."""
    paints = FirstPaints()
    paints.record(1, 20)
    assert not paints.complete([1], 2)
    assert not paints.complete([1, 1], 2)


def test_event_loop_callback_cannot_accept_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paint arriving after a long callback remains an observation timeout."""
    ticks = iter([0.0, 0.1, 5.1])
    monkeypatch.setattr(subject.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(subject.QCoreApplication, "sendPostedEvents", lambda *_: None)
    monkeypatch.setattr(subject.QApplication, "processEvents", lambda: None)
    observed = wait_for(lambda: True, 5.0)
    assert observed is False


@pytest.mark.parametrize("boundary", ["constructor", "signal", "handles"])
def test_setup_and_handle_failures_retire_every_acquired_resource(
    monkeypatch: pytest.MonkeyPatch, boundary: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Actual observe finalizers retire capture and screen owners despite faults."""
    app = Mock()
    source = Mock()
    capture = Mock()
    capture.is_selecting = False
    failure = RuntimeError("Owned fixture failure")
    constructor = Mock(return_value=capture)
    if boundary == "constructor":
        constructor.side_effect = failure
    elif boundary == "signal":
        capture.failed.connect.side_effect = failure
    else:
        type(capture).overlays = PropertyMock(side_effect=failure)
    monkeypatch.setattr(subject, "attest", lambda: None)
    monkeypatch.setattr(subject, "QApplication", Mock(return_value=app))
    monkeypatch.setattr(
        subject,
        "QGuiApplication",
        SimpleNamespace(platformName=lambda: "windows", screens=lambda: [object()]),
    )
    monkeypatch.setattr(
        subject,
        "DesktopSession",
        SimpleNamespace(current=lambda: SimpleNamespace(state=0, flags=1)),
    )
    monkeypatch.setattr(
        subject.ctypes, "WinDLL", Mock(return_value=Mock()), raising=False
    )
    monkeypatch.setattr(subject, "MssScreenSource", Mock(return_value=source))
    monkeypatch.setattr(subject, "CaptureService", constructor)
    monkeypatch.setattr(subject, "wait_for", lambda predicate, _seconds: predicate())
    monkeypatch.setenv("CAPTURE_SOURCE", "candidate")
    original = subject.service_module.SelectionOverlay
    with pytest.raises(RuntimeError, match="Owned fixture failure"):
        subject.observe()
    source.close.assert_called_once_with()
    if boundary != "constructor":
        capture.cancel.assert_called_once_with()
    assert subject.service_module.SelectionOverlay is original
    if boundary == "handles":
        output = capsys.readouterr().out
        receipt = json.loads(output.split("COLD_NATIVE_REGION=", 1)[1])
        assert receipt["cleanup_confirmed"] is False
