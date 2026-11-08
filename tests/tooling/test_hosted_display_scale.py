"""Hosted scale setup never substitutes simulated DPI for measured display state."""

from __future__ import annotations

import ctypes as c
import json
import subprocess
from dataclasses import replace
from itertools import chain, count, repeat
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts import hosted_display_scale as scale

_BASE = scale.Reading(1, 100, 0, 96, 2, (0, 0, 1920, 1080))
_READY = replace(_BASE, monitor_scale=200, window_dpi=192)


@pytest.fixture
def hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicitly model an ephemeral Windows runner, without calling Windows."""
    monkeypatch.setattr(scale.sys, "platform", "win32")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("WINDIR", "C:/Windows")
    for name in scale._OVERRIDES:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize(
    "missing", ["GITHUB_ACTIONS", "RUNNER_ENVIRONMENT", "platform"]
)
def test_refuses_non_hosted_before_native_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, missing: str
) -> None:
    """A direct invocation on a laptop cannot open Settings or create evidence."""
    if missing == "platform":
        monkeypatch.setattr(scale.sys, "platform", "linux")
    else:
        monkeypatch.delenv(missing)
    display = Mock()
    monkeypatch.setattr(scale, "Display", display)
    output = tmp_path / "not-created"
    with pytest.raises(RuntimeError, match="disposable hosted Windows"):
        scale.qualify(output)
    display.assert_not_called()
    assert not output.exists()


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("name", scale._OVERRIDES)
def test_refuses_synthetic_scale(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Synthetic Qt scaling is not a substitute for the runner's monitor scale."""
    monkeypatch.setenv(name, "2")
    with pytest.raises(RuntimeError, match="synthetic Qt"):
        scale.require_hosted()


@pytest.mark.parametrize(
    "reading",
    [
        replace(_READY, monitor_count=2),
        replace(_READY, scale_hresult=-1),
        replace(_READY, monitor_scale=100),
        replace(_READY, window_dpi=96),
        replace(_READY, window_dpi=0),
        replace(_READY, window_awareness=1),
    ],
)
def test_qualification_requires_both_independent_measurements(
    reading: scale.Reading,
) -> None:
    """A plausible value accompanying failed HRESULT or wrong awareness fails."""
    assert not reading.qualified
    assert _READY.qualified


def _install_display(monkeypatch: pytest.MonkeyPatch, *readings: object) -> Mock:
    display = Mock()
    display.read.side_effect = chain(readings, repeat(readings[-1]))
    monkeypatch.setattr(scale, "Display", Mock(return_value=display))
    monkeypatch.setattr(scale, "failure_image", Mock())
    monkeypatch.setattr(scale.time, "sleep", Mock())
    monkeypatch.setattr(scale.time, "monotonic", Mock(side_effect=count()))
    return display


@pytest.mark.usefixtures("hosted")
def test_success_measures_again_after_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """UI success alone is insufficient; a second native reading is required."""
    display = _install_display(monkeypatch, _BASE, _READY)
    select = Mock()
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    assert display.read.call_count == 2
    select.assert_called_once_with(tmp_path)
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["status"] == "setup_verified"
    assert report["before"]["monitor_scale"] == 100
    assert report["after"]["monitor_scale"] == 200
    assert "target application not yet qualified" in report["scope"]


@pytest.mark.usefixtures("hosted")
def test_native_readback_can_settle_without_repeating_selection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One delayed native transition is observed without another UI mutation."""
    display = _install_display(monkeypatch, _BASE, _BASE, _READY)
    select = Mock()
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    assert display.read.call_count == 3
    select.assert_called_once_with(tmp_path)


@pytest.mark.usefixtures("hosted")
def test_already_200_does_not_reapply_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Existing measured 200% is checked twice without mutating Settings."""
    _install_display(monkeypatch, _READY, _READY)
    select = Mock()
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    select.assert_not_called()


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize(
    "reading", [replace(_BASE, monitor_count=2), replace(_BASE, scale_hresult=-1)]
)
def test_unusable_monitor_readback_never_opens_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reading: scale.Reading
) -> None:
    """Do not change a display whose identity or independent scale is uncertain."""
    _install_display(monkeypatch, reading, reading)
    select = Mock()
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 1
    select.assert_not_called()


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("after", [_BASE, replace(_READY, window_dpi=96)])
def test_completed_selection_without_actual_dpi_is_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, after: scale.Reading
) -> None:
    """Settings applying nothing or only a partial change is never green."""
    _install_display(monkeypatch, _BASE, after, after)
    monkeypatch.setattr(scale, "select_scale", Mock())
    assert scale.qualify(tmp_path) == 1
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["status"] == "failed"
    assert "Actual monitor scale" in report["error"]


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize(
    "error",
    [RuntimeError("unsupported setup"), subprocess.TimeoutExpired("owned helper", 60)],
)
def test_setup_error_does_not_retry_or_claim_late_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    """Even a successful failure readback does not hide an uncertain UI attempt."""
    _install_display(monkeypatch, _BASE, _READY)
    select = Mock(side_effect=error)
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 1
    select.assert_called_once()
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["status"] == "failed"
    assert report["failure_readback"]["monitor_scale"] == 200
    assert report["selection_outcome_uncertain"] is isinstance(
        error, subprocess.TimeoutExpired
    )


@pytest.mark.usefixtures("hosted")
def test_secondary_readback_failure_keeps_original_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence collection cannot replace the setup error or cause a false pass."""
    _install_display(monkeypatch, _BASE, OSError("readback unavailable"))
    monkeypatch.setattr(
        scale, "select_scale", Mock(side_effect=RuntimeError("missing 200%"))
    )
    assert scale.qualify(tmp_path) == 1
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert "missing 200%" in report["error"]
    assert report["readback_error"] == "OSError"


@pytest.mark.usefixtures("hosted")
def test_settings_child_has_hard_timeout_and_no_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the checked-in companion executes in a bounded MTA child."""

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (tmp_path / "settings-outcome.json").write_text(
            json.dumps({"status": "selection_completed", "closed_owned_window": True}),
            encoding="utf-8-sig",
        )
        return subprocess.CompletedProcess([], 0, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    scale.select_scale(tmp_path)
    arguments = run.call_args.args[0]
    assert arguments[0].endswith("powershell.exe")
    assert "-Mta" in arguments
    assert "-ExecutionPolicy" not in arguments
    assert Path(arguments[arguments.index("-File") + 1]) == Path(
        scale.__file__
    ).with_suffix(".ps1")
    assert run.call_args.kwargs["timeout"] == 60
    assert not run.call_args.kwargs.get("shell")


@pytest.mark.usefixtures("hosted")
def test_child_launch_errors_are_retained_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PowerShell policy/parser errors still leave a useful bounded diagnostic."""
    run = Mock(return_value=subprocess.CompletedProcess([], 1, "", "x" * 10000))
    monkeypatch.setattr(scale.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Settings setup or cleanup failed"):
        scale.select_scale(tmp_path)
    assert len((tmp_path / "settings-process-error.txt").read_text()) == 2048


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("closed", [False, None])
def test_cleanup_failure_cannot_become_late_scale_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncode: int,
    *,
    closed: bool | None,
) -> None:
    """Failed/unverified cleanup stays failed even when native scale reaches 200%."""
    _install_display(monkeypatch, _BASE, _READY)

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (tmp_path / "settings-outcome.json").write_text(
            json.dumps({"status": "selection_completed", "closed_owned_window": closed})
        )
        return subprocess.CompletedProcess([], returncode, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    assert scale.qualify(tmp_path) == 1
    run.assert_called_once()
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["status"] == "failed"
    assert report["failure_readback"]["monitor_scale"] == 200
    assert report["failure_readback"]["window_dpi"] == 192
    assert "after" not in report


@pytest.mark.usefixtures("hosted")
def test_stale_cleanup_receipt_cannot_qualify_a_new_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child must produce fresh proof rather than inherit a previous receipt."""
    (tmp_path / "settings-outcome.json").write_text(
        json.dumps({"status": "selection_completed", "closed_owned_window": True})
    )
    monkeypatch.setattr(
        scale.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, "", "")),
    )
    with pytest.raises(RuntimeError, match="disappearance was not confirmed"):
        scale.select_scale(tmp_path)


def _native_display() -> tuple[scale.Display, Mock]:
    display = object.__new__(scale.Display)
    user, shcore = Mock(), Mock()
    display.user, display.shcore = user, shcore
    user.SetThreadDpiAwarenessContext.return_value = 123
    user.MonitorFromPoint.return_value = 11
    user.CreateWindowExW.return_value = 22
    user.DestroyWindow.return_value = 1
    user.GetSystemMetrics.return_value = 1
    user.GetDpiForWindow.return_value = 192
    user.GetWindowDpiAwarenessContext.return_value = 456
    user.GetAwarenessFromDpiAwarenessContext.return_value = 2

    def monitor_info(_handle: int, pointer: c._CArgObject) -> int:
        info = c.cast(pointer, c.POINTER(scale._MonitorInfo)).contents
        info.monitor.right, info.monitor.bottom = 1920, 1080
        return 1

    def monitor_scale(_handle: int, pointer: c._CArgObject) -> int:
        c.cast(pointer, c.POINTER(c.c_int)).contents.value = 200
        return 0

    user.GetMonitorInfoW.side_effect = monitor_info
    shcore.GetScaleFactorForMonitor.side_effect = monitor_scale
    return display, user


def test_native_readback_uses_owned_hwnd_and_restores_context() -> None:
    """The measurement HWND is destroyed and thread awareness is restored."""
    display, user = _native_display()
    assert display.read() == _READY
    user.GetDpiForWindow.assert_called_once_with(22)
    user.DestroyWindow.assert_called_once_with(22)
    assert user.SetThreadDpiAwarenessContext.call_count == 2
    assert user.SetThreadDpiAwarenessContext.call_args.args == (123,)
    assert user.CreateWindowExW.call_args.args[3] == 0x80000000  # invisible WS_POPUP


def test_native_readback_failure_still_releases_window_and_context() -> None:
    """A native getter failure cannot leak the probe window or awareness context."""
    display, user = _native_display()
    user.GetDpiForWindow.side_effect = OSError("failed read")
    with pytest.raises(OSError, match="failed read"):
        display.read()
    user.DestroyWindow.assert_called_once_with(22)
    assert user.SetThreadDpiAwarenessContext.call_args.args == (123,)
