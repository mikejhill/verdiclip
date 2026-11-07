"""Portable tests of native qualification oracles, limits and owned cleanup."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QByteArray, QProcess
from PySide6.QtGui import QColor, QImage, QPainter
from pytestqt.qtbot import QtBot
from scripts import frozen_menu_input as input_module
from scripts import frozen_menu_native as native
from scripts.frozen_menu_fixture import MenuFixture, MenuPatch
from scripts.frozen_menu_input import (
    HotkeyObservation,
    OwnedInput,
    has_contrast,
    is_marker,
    pixel_digest,
)


def marker() -> QImage:
    """Match the fixture's unscaled interior, without capturing any desktop."""
    image = QImage(224, 84, QImage.Format.Format_RGB32)
    image.fill(QColor("#d72b91"))
    painter = QPainter(image)
    painter.fillRect(112, 0, 112, 84, QColor("#19bd83"))
    painter.end()
    return image


def test_pixel_oracle_rejects_single_corrupted_pixel_and_background() -> None:
    """Neither a selected center sample nor a callback claim can substitute pixels."""
    expected = marker()
    assert is_marker(expected)
    altered = expected.copy()
    altered.setPixelColor(3, 7, QColor("#18314d"))
    assert not is_marker(altered)
    assert pixel_digest(altered) != pixel_digest(expected)
    altered.fill(QColor("#18314d"))
    assert not is_marker(altered)
    assert not has_contrast(altered)
    assert has_contrast(expected)
    assert not is_marker(QImage())
    assert not has_contrast(QImage())
    with pytest.raises(ValueError, match="Empty image"):
        pixel_digest(QImage())


def test_marker_widget_paints_the_oracle_not_a_synthetic_receipt(qtbot: QtBot) -> None:
    """Actual widget paint yields both expected colors independent of theme."""
    patch = MenuPatch()
    qtbot.addWidget(patch)
    actual = patch.grab().toImage().copy(8, 8, 224, 84)
    assert is_marker(actual)
    assert pixel_digest(actual) == pixel_digest(marker())


@pytest.mark.parametrize(
    "category", [b"windows_generic_MSG", b"windows_dispatcher_MSG"]
)
def test_observer_records_but_does_not_consume_real_category(category: bytes) -> None:
    """Both Qt paths remain observable without replacing production dispatch."""
    observation = HotkeyObservation()
    message = w.MSG()
    message.message = 0x0312
    message.wParam = 42
    assert observation.nativeEventFilter(
        QByteArray(category), ctypes.addressof(message)
    ) == (False, 0)
    assert observation.events == [{"category": category.decode(), "id": 42, "hwnd": 0}]
    assert observation.nativeEventFilter(b"unrelated", ctypes.addressof(message)) == (
        False,
        0,
    )
    assert observation.nativeEventFilter(category, 0) == (False, 0)
    message.message = 0x0100
    assert observation.nativeEventFilter(category, ctypes.addressof(message)) == (
        False,
        0,
    )
    assert len(observation.events) == 1


def test_partial_chord_failure_releases_every_submitted_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure is preserved, modifiers are not stranded, and nothing is retried."""
    api = Mock()
    monkeypatch.setattr(
        input_module.ctypes, "WinDLL", Mock(return_value=api), raising=False
    )
    owner = OwnedInput()
    monkeypatch.setattr(owner, "foreground_owned", lambda: True)
    api.SendInput.side_effect = [1, 0, 1, 1]
    monkeypatch.setattr(input_module.ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(RuntimeError, match="SendInput failed"):
        owner.chord(0x81)
    assert not owner.down
    assert api.SendInput.call_count == 4


def test_input_target_loss_blocks_new_actions_but_allows_owned_key_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No keydown goes to another application after a focus change."""
    api = Mock()
    api.SendInput.return_value = 1
    monkeypatch.setattr(
        input_module.ctypes, "WinDLL", Mock(return_value=api), raising=False
    )
    owner = OwnedInput()
    monkeypatch.setattr(owner, "foreground_owned", lambda: False)
    with pytest.raises(RuntimeError, match="not an owned"):
        owner.key(0x11)
    api.SendInput.assert_not_called()
    owner.release()
    api.SendInput.assert_not_called()
    assert owner.down == set()


def test_late_success_is_failure_not_a_faster_second_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness/action deadlines cannot be passed by a blocked callback."""
    ticks = iter((0, 0.1, 6, 6))
    monkeypatch.setattr(
        input_module,
        "time",
        SimpleNamespace(perf_counter=lambda: next(ticks), sleep=lambda _: None),
    )
    monkeypatch.setattr(
        input_module, "QApplication", SimpleNamespace(processEvents=lambda: None)
    )
    with pytest.raises(TimeoutError, match=r"within 5\.0s"):
        input_module.wait_for(lambda: True, "late observation")


def test_child_start_failure_runs_bounded_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed setup still owns a potentially started process and must reap it."""
    process = Mock()
    process.waitForStarted.return_value = False
    process.waitForFinished.side_effect = [False, True]
    process.state.return_value = QProcess.ProcessState.Running
    factory = Mock(return_value=process)
    factory.ProcessState = QProcess.ProcessState
    monkeypatch.setattr(native, "QProcess", factory)
    with pytest.raises(RuntimeError, match="failed to start"):
        native.FixtureOwner(tmp_path / "receipt.json", foreign=True)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.waitForFinished.call_count == 2


def test_owned_fixture_retirement_stops_its_timer_and_popup(qtbot: QtBot) -> None:
    """The local variant cannot leak a periodic callback into the next case."""
    fixture = MenuFixture()
    qtbot.addWidget(fixture.background)
    try:
        fixture.show()
        qtbot.waitUntil(fixture.menu.isVisible)
        assert fixture.timer.isActive()
    finally:
        fixture.close()
    assert not fixture.timer.isActive()
    assert not fixture.menu.isVisible()
    assert not fixture.background.isVisible()


@pytest.mark.parametrize("patch", [None, [], [1, 2, 3], [1, 2, "x", 4]])
def test_malformed_fixture_geometry_cannot_feed_native_input(patch: object) -> None:
    """Missing/invalid child data is a failed setup, never guessed coordinates."""
    with pytest.raises(RuntimeError, match="Invalid fixture patch"):
        native.state_patch({"patch": patch})


def test_comparison_keeps_exact_baselines_and_existing_gates() -> None:
    """Both historical controls run identical scripts with no weakened CI gate."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/frozen-menu-native.yml").read_text(
        encoding="utf-8"
    )
    assert "a1c6bbe488284cf5f5b4ed7a8b1e30181e0f62ed" in workflow
    assert "1f73bde2b6ab5ab2324ce8079159004ee2713d3a" in workflow
    assert 'qt: ["6.11.2", "6.12.0"]' in workflow
    assert "source: [candidate, released-0121, released-0131]" in workflow
    assert "continue-on-error" not in workflow
    assert "timeout-minutes: 6" in workflow
    assert "tests/capture/test_capture_native.py" in workflow
    assert "tests/capture/test_tabs_baseline_native.py" in workflow


@pytest.mark.parametrize(
    "scenario",
    ["owned-drag", "foreign-drag", "owned-window", "owned-fullscreen", "tray-command"],
)
def test_missing_capture_or_emitted_error_cannot_qualify(scenario: str) -> None:
    """The original native error is not mistaken for a successful empty result."""
    with pytest.raises(RuntimeError, match="missing frame/capture"):
        native.validate_outcome(scenario, 1, [], ["Real desktop capture failed"])


def test_expected_fault_is_exact_and_never_hides_a_different_error() -> None:
    """Only the deliberate fixture fault is accepted in the failure lane."""
    native.validate_outcome("owned-error", 1, [], ["Owned qualification freeze fault"])
    with pytest.raises(RuntimeError, match="Unexpected capture errors"):
        native.validate_outcome("owned-error", 1, [], ["Real desktop capture failed"])
    with pytest.raises(RuntimeError, match="missing frame/capture"):
        native.validate_outcome("owned-repeat", 2, [], [])


@pytest.mark.parametrize("started", [True, False])
def test_already_stopped_child_is_reaped_without_termination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, started: bool
) -> None:
    """A confirmed stopped process is not a false cleanup failure."""
    process = Mock()
    process.waitForStarted.return_value = started
    process.state.return_value = QProcess.ProcessState.NotRunning
    process.errorString.return_value = "owned setup failure"
    factory = Mock(return_value=process)
    factory.ProcessState = QProcess.ProcessState
    monkeypatch.setattr(native, "QProcess", factory)
    if started:
        fixture = native.FixtureOwner(tmp_path / "receipt.json", foreign=True)
        fixture.close()
    else:
        with pytest.raises(RuntimeError, match="failed to start: owned setup failure"):
            native.FixtureOwner(tmp_path / "receipt.json", foreign=True)
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    process.waitForFinished.assert_not_called()


def test_input_ledger_precedes_submission_and_clears_after_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A killed native call leaves only its owned pending release in durable state."""
    path = tmp_path / "input.json"
    monkeypatch.setenv("QUALIFICATION_INPUT_LEDGER", str(path))
    api = Mock()
    observations: list[dict[str, object]] = []

    def submitted(*_args: object) -> int:
        observations.append(json.loads(path.read_text(encoding="utf-8")))
        return 1

    api.SendInput.side_effect = submitted
    monkeypatch.setattr(
        input_module.ctypes, "WinDLL", Mock(return_value=api), raising=False
    )
    owner = OwnedInput()
    monkeypatch.setattr(owner, "foreground_owned", lambda: True)
    owner.key(0x11)
    owner.button()
    owner.release()
    assert observations[0]["keys"] == [0x11]
    assert observations[1]["mouse_down"] is True
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["keys"] == []
    assert after["mouse_down"] is False


def test_rejected_input_does_not_create_timeout_release_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unrelated foreground receives neither a new input nor a cleanup keyup."""
    path = tmp_path / "input.json"
    monkeypatch.setenv("QUALIFICATION_INPUT_LEDGER", str(path))
    api = Mock()
    monkeypatch.setattr(
        input_module.ctypes, "WinDLL", Mock(return_value=api), raising=False
    )
    owner = OwnedInput()
    monkeypatch.setattr(owner, "foreground_owned", lambda: False)
    with pytest.raises(RuntimeError, match="not an owned"):
        owner.button()
    owner.release()
    api.SendInput.assert_not_called()
    assert not path.exists()


@pytest.mark.parametrize("valid", [True, False])
def test_timeout_cleanup_only_accepts_recorded_owned_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, valid: bool
) -> None:
    """The external supervisor cannot expand a ledger into arbitrary key input."""
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {"pid": 1234, "keys": [0x11] if valid else [0x41], "mouse_down": True}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        input_module, "sys", SimpleNamespace(platform="win32", stdout=Mock())
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    owner = Mock()
    factory = Mock(return_value=owner)
    monkeypatch.setattr(input_module, "OwnedInput", factory)
    if valid:
        input_module.release_timeout_ledger(path, 1234)
        assert owner.down == {0x11}
        assert owner.mouse_down is True
        owner.release.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="Invalid owned"):
            input_module.release_timeout_ledger(path, 1234)
        factory.assert_not_called()


def test_supervisor_cleanup_failure_cannot_start_later_native_tests() -> None:
    """Even the legacy native step is gated on confirmed input retirement."""
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/frozen-menu-native.yml").read_text(
        encoding="utf-8"
    )
    assert "WaitForExit(10000)" in workflow
    assert "NATIVE_INPUT_CLEANUP=FAILED" in workflow
    assert "steps.observations.outputs.input_cleanup_confirmed == 'true'" in workflow
    assert "'input_cleanup_confirmed=true' >> $env:GITHUB_OUTPUT" in workflow


def test_pretrigger_readiness_retains_first_and_last_pixel_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting for fixture paint is setup only and cannot erase its first state."""
    probe = Mock()
    context = Mock()
    context.__enter__ = Mock(return_value=probe)
    context.__exit__ = Mock(return_value=None)
    background = marker()
    background.fill(QColor("#18314d"))
    probe.freeze.return_value.crop.side_effect = [background, marker()]
    monkeypatch.setattr(native, "MssScreenSourceContext", Mock(return_value=context))

    def wait_twice(predicate: object, _label: str, _seconds: float) -> None:
        assert callable(predicate)
        assert predicate() is False
        assert predicate() is True

    monkeypatch.setattr(native, "wait_for", wait_twice)
    receipt: dict[str, object] = {}
    result = native.ready_reference(
        native.Rect(0, 0, 224, 84), "owned-drag", 5, receipt
    )
    assert is_marker(result)
    assert receipt == {
        "first_pixel_ready": False,
        "last_pixel_ready": True,
        "pixel_readiness_attempts": 2,
    }
    context.__exit__.assert_called_once()


def test_native_probe_failure_is_never_readiness_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Actual desktop-read errors propagate immediately instead of becoming skips."""
    probe = Mock()
    probe.freeze.side_effect = native.CaptureError("native read failed")
    context = Mock()
    context.__enter__ = Mock(return_value=probe)
    context.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(native, "MssScreenSourceContext", Mock(return_value=context))
    with pytest.raises(native.CaptureError, match="native read failed"):
        native.ready_reference(native.Rect(0, 0, 224, 84), "owned-drag", 5, {})
    probe.freeze.assert_called_once()
    context.__exit__.assert_called_once()
