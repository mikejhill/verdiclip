"""Ordinary-layout diagnostic inputs must not reuse the colored-row oracle."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QWidget
from scripts import scrolling_diagnostics as diagnostics

from verdiclip.geometry import Point


@pytest.mark.parametrize("layout", ["edge-prose", "edge-panel", "edge-sidebar"])
def test_document_has_real_prose_and_declared_layout(layout: str) -> None:
    """Vary normal document layout without authoring row-level pixel IDs."""
    source = diagnostics.document(layout)
    assert "The user's flow is the product" not in source  # Escaped HTML prose.
    assert "Get a precise piece of the screen" in source
    assert "background:rgb(" not in source
    assert ('<section tabindex="0"' in source) == (layout == "edge-panel")
    assert ("<aside>" in source) == (layout == "edge-sidebar")


def test_panel_keeps_prose_and_declares_responsive_limits() -> None:
    """Only the outer panel/header adapt; prose remains horizontally scrollable."""
    source = diagnostics.document("edge-panel")
    assert "section{height:385.5px;width:740.5px;margin:24px;" in source
    assert "max-width:calc(100% - 78px);max-height:calc(100vh - 206px);" in source
    assert "border:3px solid #b1b5ba;padding:12px;overflow:scroll}" in source
    assert "section article{width:820px;max-width:none;margin:0}" in source
    assert (
        "@media(max-height:500px){"
        "body>h1{font-size:18px;margin-top:8px;margin-bottom:4px}"
        "body>p{font-size:12px;line-height:1.2;margin-bottom:8px}"
        "section{margin-top:8px;max-height:calc(100vh - 110px)}}"
    ) in source
    assert "Project design notes" in source
    assert "Scroll the document panel below." in source
    assert "Related project resources" in source


def test_diagnostic_declares_limited_oracle_even_when_capture_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed attempt cannot inherit the special colored-row completeness claim."""
    monkeypatch.setattr(
        diagnostics.app, "_case", Mock(side_effect=RuntimeError("Cannot join"))
    )
    report: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="Cannot join"):
        diagnostics._case("edge-prose", tmp_path, report)
    assert report["oracle"] == (
        "capture/restoration smoke only; inspect exact PNG content"
    )


def test_explorer_uses_only_disposable_owned_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explorer diagnostics do not open user folders or alter app settings."""
    monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
    popen = Mock()
    monkeypatch.setattr(diagnostics.subprocess, "Popen", popen)
    monkeypatch.setattr(diagnostics.native, "_record_version", Mock())
    report: dict[str, object] = {}
    diagnostics._explorer("explorer-click", tmp_path, report)
    folder = tmp_path / "VerdiClip scrolling check"
    assert len(list(folder.glob("*.txt"))) == 160
    assert popen.call_args.args[0] == [
        str(tmp_path / "Windows" / "explorer.exe"),
        "/separate,",
        str(folder),
    ]


def test_diagnostic_gesture_uses_held_native_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both click and Enter allow Qt turns between native press and release."""
    click = Mock()
    monkeypatch.setattr(diagnostics.Desktop, "click", click)
    desktop = object.__new__(diagnostics.HumanDesktop)
    widget = Mock(spec=QWidget)
    desktop.click(widget, Point(30, 40), enter=True)
    click.assert_called_once_with(widget, Point(30, 40), enter=True, hold_ms=120)


def test_explorer_discovery_failure_preserves_setup_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing folder target is a setup failure, not a capture verdict."""
    runner = diagnostics.ExplorerCase()
    runner.process = Mock(pid=123)
    runner.process.poll.return_value = 0
    monkeypatch.setattr(
        diagnostics.native, "_window", Mock(side_effect=RuntimeError("No folder"))
    )
    evidence = Mock()
    monkeypatch.setattr(diagnostics.native, "_desktop_evidence", evidence)
    case = Mock()
    monkeypatch.setattr(diagnostics, "_case", case)
    report: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="No folder"):
        runner.run("explorer-click", tmp_path, report)
    assert report == {"explorer_launcher_pid": 123, "explorer_launcher_exit": 0}
    evidence.assert_called_once_with(tmp_path, report)
    case.assert_not_called()


@pytest.mark.parametrize("state", ["new", "existing", "wrong_path", "ambiguous"])
def test_explorer_capture_requires_new_exact_folder_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    """The folder HWND can be broker-owned, but must be new and exact."""
    runner = diagnostics.ExplorerCase()
    process = Mock(pid=123)
    monkeypatch.setattr(diagnostics, "_explorer", Mock(return_value=process))
    windows = Mock(return_value={9: False} if state == "existing" else {8: False})
    monkeypatch.setattr(diagnostics, "_shell_windows", windows)
    assert runner.launch("explorer-click", tmp_path, {}) is process
    windows.return_value = {9: state != "wrong_path"}
    if state == "ambiguous":
        windows.return_value[10] = True
    monkeypatch.setattr(
        diagnostics.native, "_window", Mock(return_value=Mock(handle=9))
    )
    monkeypatch.setattr(diagnostics, "_window_pid", Mock(return_value=456))
    case = Mock()
    monkeypatch.setattr(diagnostics, "_case", case)
    report: dict[str, object] = {}
    if state == "new":
        runner.run("explorer-click", tmp_path, report)
        case.assert_called_once_with("explorer-click", tmp_path, report)
        assert report["explorer_window_pid"] == 456
        assert report["explorer_launcher_pid"] == 123
        assert report["explorer_new_exact_folder_window"] is True
    else:
        with pytest.raises(RuntimeError, match="one new exact fixture"):
            runner.run("explorer-click", tmp_path, report)
        case.assert_not_called()


@pytest.mark.parametrize("changed", ["none", "pid", "path", "missing"])
def test_explorer_rechecks_retained_identity_before_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: str
) -> None:
    """Subsequent AppCapture discovery cannot retarget a same-title window."""
    runner = diagnostics.ExplorerCase()
    runner.folder = tmp_path
    runner.window = Mock(handle=9)
    runner.pid = 456
    visible = [] if changed == "missing" else [Mock(handle=9), Mock(handle=10)]
    monkeypatch.setattr(
        diagnostics,
        "WindowLocator",
        Mock(return_value=Mock(visible_windows=lambda: visible)),
    )
    monkeypatch.setattr(
        diagnostics, "_shell_windows", Mock(return_value={9: changed != "path"})
    )
    monkeypatch.setattr(
        diagnostics, "_window_pid", Mock(return_value=789 if changed == "pid" else 456)
    )
    if changed == "none":
        assert runner._verified_window() is visible[0]
    else:
        with pytest.raises(RuntimeError):
            runner._verified_window()


@pytest.mark.parametrize("state", ["same", "changed", "invalid"])
def test_explorer_preparation_rechecks_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """The second AppCapture preparation must not trust a cached window record."""
    prepare = Mock()
    monkeypatch.setattr(diagnostics.native, "_prepare", prepare)
    runner = diagnostics.ExplorerCase()
    current = Mock(handle=9)
    verify = Mock(
        return_value=current,
        side_effect=RuntimeError("Identity changed") if state == "invalid" else None,
    )
    monkeypatch.setattr(runner, "_verified_window", verify)
    given = Mock(handle=10 if state == "changed" else 9)
    if state == "same":
        runner._prepare(given)
        prepare.assert_called_once_with(current)
    else:
        with pytest.raises(RuntimeError):
            runner._prepare(given)
        prepare.assert_not_called()
    verify.assert_called_once_with()


@pytest.mark.parametrize(
    "invalid",
    [None, {}, [{"hwnd": 9, "target": "false"}], [{"hwnd": 0, "target": False}]],
)
def test_shell_identity_response_is_strict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid: object
) -> None:
    """String truthiness must not authorize another Explorer window."""
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        Mock(return_value=Mock(stdout=json.dumps(invalid))),
    )
    with pytest.raises(TypeError, match="identity response"):
        diagnostics._shell_windows(tmp_path)


def test_shell_identity_uses_canonical_exact_fixture_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a canonical equality boolean, never unrelated paths, is retained."""
    monkeypatch.setenv("WINDIR", str(tmp_path))
    run = Mock(return_value=Mock(stdout='[{"hwnd":9,"target":true}]'))
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    assert diagnostics._shell_windows(tmp_path / "test's folder") == {9: True}
    script = run.call_args.args[0][-1]
    assert "[IO.Path]::GetFullPath($_.Document.Folder.Self.Path) -eq " in script
    assert "test''s folder" in script
    assert "$handle=[long]$_.HWND; $exact=$false; try" in script


def test_shell_identity_rejects_duplicate_tab_handles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two tab records cannot silently overwrite an existing window identity."""
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setattr(
        diagnostics.subprocess,
        "run",
        Mock(
            return_value=Mock(
                stdout='[{"hwnd":9,"target":false},{"hwnd":9,"target":true}]'
            )
        ),
    )
    with pytest.raises(RuntimeError, match="duplicate HWND"):
        diagnostics._shell_windows(tmp_path)


@pytest.mark.parametrize("alive", [True, False])
def test_window_pid_requires_observable_live_process(
    monkeypatch: pytest.MonkeyPatch, *, alive: bool
) -> None:
    """A PID match alone is not a process-liveness assertion."""
    user, kernel = Mock(), Mock()

    def process_id(_window: int, out: c.c_void_p) -> int:
        c.cast(out, c.POINTER(w.DWORD)).contents.value = 456
        return 7

    user.GetWindowThreadProcessId.side_effect = process_id
    kernel.OpenProcess.return_value = 123
    kernel.WaitForSingleObject.return_value = diagnostics._WAIT_TIMEOUT if alive else 0
    monkeypatch.setattr(
        c,
        "WinDLL",
        lambda name, **_: user if name == "user32" else kernel,
        raising=False,
    )
    if alive:
        assert diagnostics._window_pid(9) == 456
    else:
        with pytest.raises(RuntimeError, match="exited"):
            diagnostics._window_pid(9)
    kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize(
    "changed", ["none", "pid", "path", "hwnd", "ambiguous", "refused", "gone"]
)
def test_explorer_cleanup_never_kills_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: str
) -> None:
    """One close targets only the retained, new, still-exact folder HWND."""
    runner = diagnostics.ExplorerCase()
    runner.folder, runner.window, runner.pid = tmp_path, Mock(handle=9), 456
    windows = {9: True}
    if changed == "path":
        windows = {9: False}
    elif changed == "hwnd":
        windows = {10: True}
    elif changed == "ambiguous":
        windows[10] = True
    monkeypatch.setattr(diagnostics, "_shell_windows", Mock(return_value=windows))
    monkeypatch.setattr(
        diagnostics, "_window_pid", Mock(return_value=789 if changed == "pid" else 456)
    )
    user = Mock()
    user.IsWindow.return_value = changed != "gone"
    if changed == "none":
        user.IsWindow.side_effect = [True, False]
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr(diagnostics.time, "sleep", Mock())
    kill = Mock()
    monkeypatch.setattr(diagnostics.native, "_stop_owned_process", kill)
    if changed in ("none", "gone"):
        runner.cleanup(Mock(pid=123), window=9)
        assert (
            "closed" in str(runner.report["explorer_cleanup"])
            if changed == "none"
            else "absent" in str(runner.report["explorer_cleanup"])
        )
    else:
        with pytest.raises(RuntimeError):
            runner.cleanup(Mock(pid=123), window=9)
    if changed in ("none", "refused"):
        user.PostMessageW.assert_called_once_with(9, 0x0010, 0, 0)
    else:
        user.PostMessageW.assert_not_called()
    kill.assert_not_called()


@pytest.mark.parametrize("deadline", [None, 0.0, 0.25, 20.0])
def test_explorer_cleanup_keeps_short_close_bound_and_shared_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, deadline: float | None
) -> None:
    """Even a remaining 20-second budget grants no longer Explorer close wait."""
    runner = diagnostics.ExplorerCase()
    runner.folder, runner.window, runner.pid = tmp_path, Mock(handle=9), 456
    monkeypatch.setattr(diagnostics, "_shell_windows", Mock(return_value={9: True}))
    monkeypatch.setattr(diagnostics, "_window_pid", Mock(return_value=456))
    user = Mock()
    user.IsWindow.return_value = True
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    now = 0.0

    def pause(seconds: float) -> None:
        nonlocal now
        now += seconds

    sleep = Mock(side_effect=pause)
    monkeypatch.setattr(diagnostics.time, "sleep", sleep)
    monkeypatch.setattr(diagnostics.time, "monotonic", lambda: now)
    kill = Mock()
    monkeypatch.setattr(diagnostics.native, "_stop_owned_process", kill)
    with pytest.raises(RuntimeError, match="Explorer was not killed"):
        runner.cleanup(Mock(pid=123), window=9, deadline=deadline)
    expected = 3.0 if deadline is None else min(3.0, deadline)
    assert now == pytest.approx(expected)
    assert now <= expected
    assert sleep.call_count <= 30
    if deadline == 0.0:
        sleep.assert_not_called()
    elif deadline == 0.25:
        assert [call.args[0] for call in sleep.call_args_list] == pytest.approx(
            [0.1, 0.1, 0.05]
        )
    user.PostMessageW.assert_called_once_with(9, 0x0010, 0, 0)
    kill.assert_not_called()
