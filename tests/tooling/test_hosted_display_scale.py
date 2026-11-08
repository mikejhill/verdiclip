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
_READY = replace(
    _BASE, monitor_scale=200, window_dpi=192, physical_bounds=(0, 0, 2560, 1440)
)
_SUCCESS = {
    "status": "selection_completed",
    "scale_target": 200,
    "closed_owned_window": True,
    "resolution_verified": True,
    "resolution_target": {"width": 2560, "height": 1440},
    "resolution_after": {"width": 2560, "height": 1440},
    "resolution_selection_attempted": True,
    "keep_changes_attempted": True,
    "keep_changes_confirmed": True,
}


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
    select = Mock(return_value=(2560, 1440))
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    assert display.read.call_count == 2
    select.assert_called_once_with(tmp_path, scale=200)
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
    select = Mock(return_value=(2560, 1440))
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    assert display.read.call_count == 3
    select.assert_called_once_with(tmp_path, scale=200)


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
    monkeypatch.setattr(scale, "select_scale", Mock(return_value=(2560, 1440)))
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
            json.dumps(_SUCCESS),
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
def test_windows_utf8_bom_cleanup_receipt_is_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows PowerShell's literal EF BB BF prefix is not malformed JSON."""
    payload = (
        b'\xef\xbb\xbf{\r\n "status": "selection_completed",\r\n'
        b' "scale_target": 200, "closed_owned_window": true,\r\n'
        b' "resolution_verified": true,\r\n'
        b' "resolution_target": {"width": 2560, "height": 1440},\r\n'
        b' "resolution_after": {"width": 2560, "height": 1440},\r\n'
        b' "resolution_selection_attempted": false,\r\n'
        b' "keep_changes_attempted": false,\r\n'
        b' "keep_changes_confirmed": false\r\n}\r\n'
    )

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (tmp_path / "settings-outcome.json").write_bytes(payload)
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(scale.subprocess, "run", Mock(side_effect=complete))
    scale.select_scale(tmp_path)
    assert (tmp_path / "settings-outcome.json").read_bytes() == payload


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("size", [(2560, 1440), (1920, 1200), (1600, 1200)])
def test_allowed_resolution_receipt_matches_independent_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, size: tuple[int, int]
) -> None:
    """Each finite allowed mode must match both receipt and final native pixels."""
    _install_display(monkeypatch, _BASE, replace(_READY, physical_bounds=(0, 0, *size)))

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        mode = {"width": size[0], "height": size[1]}
        outcome = {**_SUCCESS, "resolution_target": mode, "resolution_after": mode}
        (tmp_path / "settings-outcome.json").write_text(json.dumps(outcome))
        return subprocess.CompletedProcess([], 0, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    assert scale.qualify(tmp_path) == 0
    run.assert_called_once()
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["resolution_target"] == list(size)


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("size", [(1920, 1080), (3840, 2160), (1440, 2560)])
def test_unlisted_resolution_receipt_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, size: tuple[int, int]
) -> None:
    """Matching readback and confirmation cannot authorize an unlisted mode."""

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        mode = {"width": size[0], "height": size[1]}
        outcome = {**_SUCCESS, "resolution_target": mode, "resolution_after": mode}
        (tmp_path / "settings-outcome.json").write_text(json.dumps(outcome))
        return subprocess.CompletedProcess([], 0, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    with pytest.raises(
        RuntimeError, match="resolution or Keep changes was not verified"
    ):
        scale.select_scale(tmp_path)
    run.assert_called_once()


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resolution_verified", False),
        ("resolution_verified", None),
        ("resolution_after", {"width": 1024, "height": 768}),
        ("resolution_after", {"width": 1080, "height": 1920}),
        ("resolution_after", {"width": 1920, "height": 1200}),
        ("resolution_after", None),
        ("resolution_target", {"width": 1920, "height": 1080}),
        ("resolution_target", {"width": 1920, "height": 1200}),
        ("resolution_target", None),
        ("resolution_selection_attempted", None),
        ("resolution_selection_attempted", False),
        ("keep_changes_attempted", False),
        ("keep_changes_attempted", None),
        ("keep_changes_confirmed", False),
        ("keep_changes_confirmed", None),
    ],
)
def test_resolution_failure_cannot_become_late_scale_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object
) -> None:
    """Late 200%/192 DPI cannot excuse missing resolution/confirmation proof."""
    _install_display(monkeypatch, _BASE, _READY)

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        outcome = {**_SUCCESS, field: value}
        (tmp_path / "settings-outcome.json").write_text(json.dumps(outcome))
        return subprocess.CompletedProcess([], 0, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    assert scale.qualify(tmp_path) == 1
    run.assert_called_once()
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["status"] == "failed"
    assert "Keep changes was not verified" in report["error"]
    assert report["failure_readback"]["window_dpi"] == 192


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("size", [(1024, 768), (1920, 1080), (1920, 1200)])
def test_resolution_reversion_after_settings_is_not_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, size: tuple[int, int]
) -> None:
    """Independent physical bounds must agree after the Settings child exits."""
    _install_display(monkeypatch, _BASE, replace(_READY, physical_bounds=(0, 0, *size)))
    select = Mock(return_value=(2560, 1440))
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 1
    select.assert_called_once()
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert "resolution did not remain" in report["error"]


@pytest.mark.usefixtures("hosted")
def test_already_scaled_display_does_not_require_resolution_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An already measured native 200% display needs no resolution mutation."""
    ready = replace(_READY, physical_bounds=(0, 0, 1920, 1080))
    _install_display(monkeypatch, ready, ready)
    select = Mock()
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path) == 0
    select.assert_not_called()


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
        info.monitor.right, info.monitor.bottom = 2560, 1440
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


@pytest.mark.parametrize(("percent", "dpi"), [(125, 120), (200, 192)])
def test_actual_scale_requires_its_exact_native_dpi(percent: int, dpi: int) -> None:
    """125% is 120 native DPI; neither 150 DPI nor a 200% reading qualifies it."""
    reading = replace(_READY, monitor_scale=percent, window_dpi=dpi)
    assert reading.qualifies(percent)
    for wrong in (96, 150, 192 if dpi == 120 else 120):
        assert not replace(reading, window_dpi=wrong).qualifies(percent)
    assert not reading.qualifies(200 if percent == 125 else 125)


@pytest.mark.parametrize("percent", [0, 100, 150, 201])
def test_unsupported_scale_never_opens_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, percent: int
) -> None:
    """The requested scale is a finite choice, not a generic UI mutation input."""
    display, select = Mock(), Mock()
    monkeypatch.setattr(scale, "Display", display)
    monkeypatch.setattr(scale, "select_scale", select)
    with pytest.raises(ValueError, match="Only actual 125% or 200%"):
        scale.qualify(tmp_path / "absent", scale=percent)
    display.assert_not_called()
    select.assert_not_called()
    assert not (tmp_path / "absent").exists()


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("actual_dpi", [120, 150, 192])
def test_125_settings_selection_needs_independent_native_readback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, actual_dpi: int
) -> None:
    """Choosing 125% cannot bypass the independent target-scale readback."""
    reading = replace(_READY, monitor_scale=125, window_dpi=actual_dpi)
    _install_display(monkeypatch, _BASE, reading)
    select = Mock(return_value=(2560, 1440))
    monkeypatch.setattr(scale, "select_scale", select)
    assert scale.qualify(tmp_path, scale=125) == (0 if actual_dpi == 120 else 1)
    select.assert_called_once_with(tmp_path, scale=125)
    report = json.loads((tmp_path / "display-scale.json").read_text())
    assert report["scale_target"] == 125
    assert report["dpi_target"] == 120
    assert report["after"]["window_dpi"] == actual_dpi


@pytest.mark.usefixtures("hosted")
@pytest.mark.parametrize("receipt_scale", [125, 200, None])
def test_settings_child_receipt_is_bound_to_the_requested_scale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, receipt_scale: int | None
) -> None:
    """The existing one-shot Settings child must attest the exact offered choice."""

    def complete(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        (tmp_path / "settings-outcome.json").write_text(
            json.dumps({**_SUCCESS, "scale_target": receipt_scale}), encoding="utf-8"
        )
        return subprocess.CompletedProcess([], 0, "", "")

    run = Mock(side_effect=complete)
    monkeypatch.setattr(scale.subprocess, "run", run)
    if receipt_scale == 125:
        assert scale.select_scale(tmp_path, scale=125) == (2560, 1440)
    else:
        with pytest.raises(RuntimeError, match="requested scale"):
            scale.select_scale(tmp_path, scale=125)
    run.assert_called_once()
    arguments = run.call_args.args[0]
    assert arguments[arguments.index("-Scale") + 1] == "125"
    assert run.call_args.kwargs["timeout"] == 60


def test_settings_scale_choice_remains_exact_and_single_attempt() -> None:
    """Both finite offered choices use the original bounded owned Settings path."""
    script = Path(scale.__file__).with_suffix(".ps1").read_text()
    assert "[ValidateSet(125, 200)][int]$Scale = 200" in script
    assert "('^' + $Scale + '%([ ]+\\(Recommended\\))?$')" in script
    assert script.count("$pattern.Select()") == 1
    assert "scale_target = $Scale" in script
