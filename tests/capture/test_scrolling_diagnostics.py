"""Ordinary-layout diagnostic inputs must not reuse the colored-row oracle."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import math
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget
from scripts import scrolling_diagnostics as diagnostics

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scrolling import ScrollPosition, ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.windows import WindowInfo


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


@pytest.mark.parametrize(
    "kind", ["explorer-click", "edge-panel-enter", *diagnostics._EXPLORER_STARTS]
)
def test_start_variants_leave_ordinary_app_cases_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Only explicitly named Explorer starts install the diagnostic subclass."""
    ordinary, start = Mock(), Mock()
    monkeypatch.setattr(diagnostics.app, "_case", ordinary)
    monkeypatch.setattr(diagnostics, "ExplorerStartCapture", start)
    report: dict[str, object] = {}
    diagnostics._case(kind, tmp_path, report)
    if kind in diagnostics._EXPLORER_STARTS:
        start.assert_called_once_with(kind, tmp_path, report)
        start.return_value.run.assert_called_once_with()
        ordinary.assert_not_called()
    else:
        ordinary.assert_called_once_with(kind, tmp_path, report)
        start.assert_not_called()


@pytest.mark.parametrize("kind", list(diagnostics._EXPLORER_STARTS))
def test_start_variants_keep_verified_explorer_launch_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Middle/bottom must not accidentally launch the native Notepad fallback."""
    runner = Mock(spec=diagnostics.ExplorerCase)
    monkeypatch.setattr(diagnostics, "ExplorerCase", Mock(return_value=runner))
    monkeypatch.setattr(diagnostics.sys, "argv", ["diagnostics", kind])

    def main(*, gui: bool, case: object) -> None:
        assert gui is True
        assert case == runner.run
        assert diagnostics.native._launch == runner.launch
        assert diagnostics.native._stop_owned_process == runner.cleanup

    monkeypatch.setattr(diagnostics.native, "main", main)
    diagnostics.main()


def _start_capture(
    output: Path, kind: str = "explorer-middle-click"
) -> diagnostics.ExplorerStartCapture:
    capture = object.__new__(diagnostics.ExplorerStartCapture)
    capture.kind, capture.output, capture.report = kind, output, {}
    capture.window = WindowInfo(9, "Owned fixture", Rect(1, 2, 100, 100))
    capture.point = Point(30, 40)
    capture.report.update(
        explorer_window_pid=456,
        explorer_fixture={"folder": str(output / "fixture"), "previous_handles": [8]},
    )
    return capture


def _image(color: int = 0xFFAA6677) -> QImage:
    image = QImage(10, 20, QImage.Format.Format_RGB32)
    image.fill(color)
    return image


@pytest.mark.parametrize(
    ("kind", "vertical"),
    [
        (kind, vertical)
        for kind in diagnostics._EXPLORER_STARTS
        for vertical in ([45.0, 49.8, 55.0] if "middle" in kind else [100.0])
    ],
)
def test_explorer_start_child_settles_with_scoped_uia_lifetime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, vertical: float
) -> None:
    """One setup move precedes bounded stable readback and an exact initial PNG."""
    capture = _start_capture(tmp_path, kind)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    steps: list[str] = []
    owner = Mock(spec=diagnostics.ExplorerCase)
    owner.window = capture.window
    owner._verified_window.return_value = capture.window
    sleep = Mock()
    monkeypatch.setattr(diagnostics.time, "sleep", sleep)
    requested = diagnostics._EXPLORER_STARTS[kind]
    before = ScrollPosition(-1.0, 0.0, 12.0)
    # Native row quantization may give an interior percentage near the request.
    start = ScrollPosition(-1.0, vertical, 12.0)
    target = Mock(area=Rect(1, 2, 10, 20))
    target.position.return_value = before

    def move(percent: float) -> None:
        assert percent == requested
        steps.append("public UIA setup")
        target.position.return_value = start

    target.move.side_effect = move
    target.frame.return_value = _image()
    context = Mock()
    context.__enter__ = Mock(return_value=target)
    context.__exit__ = Mock(side_effect=lambda *_: steps.append("UIA released"))
    constructor = Mock(return_value=context)
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", constructor)
    setup: dict[str, object] = {}
    diagnostics._position_explorer_start(kind, capture.point, tmp_path, owner, setup)
    assert steps == ["public UIA setup", "UIA released"]
    assert owner._verified_window.call_count == 3
    constructor.assert_called_once_with(capture.point, window=9)
    target.move.assert_called_once_with(requested)
    assert sleep.call_count == 2
    assert QImage(str(tmp_path / "before.png")) == _image()
    assert setup == {
        "requested_vertical": requested,
        "pre_setup_position": asdict(before),
        "immediate_position": asdict(start),
        "observed_positions": [asdict(start), asdict(start)],
        "setup_writes": 1,
        "settled_position": asdict(start),
        "area": asdict(target.area),
        "initial_png": "before.png",
    }


@pytest.mark.parametrize(
    ("invalid", "observed"),
    [
        ("host", 50.0),
        ("identity", 50.0),
        ("ignored", 0.0),
        ("near_top", 1.0),
        ("below_middle", 44.9),
        ("above_middle", 55.1),
        ("near_bottom", 99.0),
        ("pixels", 50.0),
    ],
)
def test_explorer_start_rejects_unsafe_or_unsettled_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid: str, observed: float
) -> None:
    """An ignored write, near-end result or moving pixels cannot qualify as middle."""
    capture = _start_capture(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv(
        "RUNNER_ENVIRONMENT", "self-hosted" if invalid == "host" else "github-hosted"
    )
    owner = Mock(spec=diagnostics.ExplorerCase)
    owner.window = capture.window
    owner._verified_window.return_value = (
        Mock(handle=10) if invalid == "identity" else capture.window
    )
    sleep = Mock()
    monkeypatch.setattr(diagnostics.time, "sleep", sleep)
    target = Mock()
    target.position.return_value = ScrollPosition(-1.0, 0.0, 12.0)
    target.move.side_effect = lambda _percent: setattr(
        target.position, "return_value", ScrollPosition(-1.0, observed, 12.0)
    )
    if invalid == "pixels":
        target.frame.side_effect = [_image(i) for i in range(8)]
    else:
        target.frame.return_value = _image()
    context = Mock()
    context.__enter__ = Mock(return_value=target)
    context.__exit__ = Mock(return_value=False)
    constructor = Mock(return_value=context)
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", constructor)
    setup: dict[str, object] = {}
    with pytest.raises(RuntimeError):
        diagnostics._position_explorer_start(
            capture.kind, capture.point, tmp_path, owner, setup
        )
    assert not (tmp_path / "before.png").exists()
    if invalid in ("host", "identity"):
        constructor.assert_not_called()
        target.move.assert_not_called()
    else:
        target.move.assert_called_once_with(50.0)
        assert sleep.call_count == 8
        assert setup["pre_setup_position"] == asdict(ScrollPosition(-1.0, 0.0, 12.0))
        assert (
            setup["observed_positions"]
            == [asdict(ScrollPosition(-1.0, observed, 12.0))] * 8
        )
        assert "settled_position" not in setup
        context.__exit__.assert_called_once()


@pytest.mark.parametrize("kind", list(diagnostics._EXPLORER_STARTS))
def test_gui_setup_passes_only_json_to_child_after_final_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """No UIA instance or COM interface may be created in the QApplication hook."""
    capture = _start_capture(tmp_path, kind)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    steps: list[str] = []
    monkeypatch.setattr(
        diagnostics.app.AppCapture,
        "_settle_target_foreground",
        lambda _: steps.append("final preparation"),
    )
    verify = Mock(return_value=capture.window)
    monkeypatch.setattr(diagnostics.app, "_window", verify)
    uia = Mock()
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", uia)
    start = ScrollPosition(-1.0, diagnostics._EXPLORER_STARTS[kind], 12.0)
    area = Rect(1, 2, 10, 20)
    setup = {"settled_position": asdict(start), "area": asdict(area)}

    def run(output: Path) -> None:
        assert output == tmp_path
        steps.append("owned subprocess")
        assert json.loads((output / "setup-request.json").read_text()) == {
            "kind": kind,
            "point": asdict(capture.point),
            "window": asdict(capture.window),
            "pid": 456,
            "fixture": capture.report["explorer_fixture"],
        }
        (output / "setup.json").write_text(json.dumps(setup), encoding="utf-8")
        assert _image().save(str(output / "before.png"))

    monkeypatch.setattr(diagnostics, "_run_explorer_setup", run)
    capture._settle_target_foreground()
    assert steps == ["final preparation", "owned subprocess"]
    assert capture.start_position == start
    assert capture.start_area == area
    assert capture.report["explorer_start_setup"] == setup
    assert verify.call_count == 2
    uia.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout", "kill_timeout"])
def test_setup_child_has_bounded_wait_kill_reap_and_owned_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    """A blocked native call cannot survive setup timeout or leave stdin owned."""
    process = Mock()
    process.wait.return_value = 0 if outcome == "success" else 1
    process.poll.return_value = process.wait.return_value
    if outcome in ("timeout", "kill_timeout"):
        process.poll.return_value = None
        process.wait.side_effect = [
            diagnostics.subprocess.TimeoutExpired("setup", 20),
            diagnostics.subprocess.TimeoutExpired("setup", 5)
            if outcome == "kill_timeout"
            else 1,
        ]
    popen = Mock(return_value=process)
    monkeypatch.setattr(diagnostics.subprocess, "Popen", popen)
    if outcome == "success":
        diagnostics._run_explorer_setup(tmp_path)
    elif outcome == "failed":
        with pytest.raises(RuntimeError, match="setup child failed"):
            diagnostics._run_explorer_setup(tmp_path)
    else:
        with pytest.raises(diagnostics.subprocess.TimeoutExpired):
            diagnostics._run_explorer_setup(tmp_path)
    command = popen.call_args.args[0]
    assert command[:2] == [diagnostics.sys.executable, "-c"]
    assert "_setup_explorer_child()" in command[2]
    assert command[3] == str(tmp_path.resolve())
    assert popen.call_args.kwargs["stdin"] == diagnostics.subprocess.PIPE
    assert popen.call_args.kwargs["stderr"] == diagnostics.subprocess.STDOUT
    assert process.wait.call_args_list[0].kwargs == {"timeout": 20}
    if outcome in ("timeout", "kill_timeout"):
        process.kill.assert_called_once_with()
        assert process.wait.call_args_list[1].kwargs == {"timeout": 5}
    else:
        process.kill.assert_not_called()
    process.stdin.close.assert_called_once_with()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("kind", ["explorer-middle-click"])
def test_setup_child_rebuilds_ownership_without_constructing_gui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, *, fails: bool
) -> None:
    """The process entry installs EOF retirement and retains even partial evidence."""
    request = {
        "kind": kind,
        "point": {"x": 30, "y": 40},
        "window": asdict(WindowInfo(9, "Owned fixture", Rect(1, 2, 100, 100))),
        "pid": 456,
        "fixture": {"folder": str(tmp_path / "fixture"), "previous_handles": [8]},
    }
    (tmp_path / "setup-request.json").write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(diagnostics.sys, "argv", ["-c", str(tmp_path)])
    thread = Mock()
    monkeypatch.setattr(diagnostics.threading, "Thread", thread)
    gui = Mock()
    monkeypatch.setattr(diagnostics.app, "AppCapture", gui)
    user = Mock()
    monkeypatch.setattr(diagnostics.c, "WinDLL", Mock(return_value=user), raising=False)

    def position(
        kind: str,
        point: Point,
        output: Path,
        owner: diagnostics.ExplorerCase,
        setup: dict[str, object],
    ) -> None:
        assert kind == request["kind"]
        assert point == Point(30, 40)
        assert output == tmp_path
        assert owner.folder == tmp_path / "fixture"
        assert owner.before == {8}
        assert owner.pid == 456
        assert owner.window is not None
        assert asdict(owner.window) == request["window"]
        setup["pre_setup_position"] = asdict(ScrollPosition(-1.0, 0.0, 12.0))
        if fails:
            raise RuntimeError("UIA setup failed")

    monkeypatch.setattr(diagnostics, "_position_explorer_start", position)
    if fails:
        with pytest.raises(RuntimeError, match="UIA setup failed"):
            diagnostics._setup_explorer_child()
    else:
        diagnostics._setup_explorer_child()
    assert "pre_setup_position" in json.loads((tmp_path / "setup.json").read_text())
    assert thread.call_args.kwargs["target"] is diagnostics._watch_parent
    assert thread.call_args.kwargs["daemon"] is True
    thread.return_value.start.assert_called_once_with()
    assert (
        user.SetProcessDpiAwarenessContext.call_args.args[0].value
        == c.c_void_p(-4).value
    )
    gui.assert_not_called()


def _start_evidence(capture: diagnostics.ExplorerStartCapture) -> None:
    capture.start_position = ScrollPosition(
        -1.0, diagnostics._EXPLORER_STARTS[capture.kind], 12.0
    )
    capture.start_area = Rect(1, 2, 10, 20)
    start = asdict(capture.start_position)
    (capture.output / "target.json").write_text(
        json.dumps(
            {
                "initial_position": start,
                "final_observed_position": start,
                "area": asdict(capture.start_area),
            }
        ),
        encoding="utf-8",
    )
    (capture.output / "restoration.json").write_text(
        json.dumps({"initial": start, "final": start}), encoding="utf-8"
    )
    (capture.output / "movement.json").write_text(
        '{"writes": 0, "last_vertical": null}', encoding="utf-8"
    )
    assert _image().save(str(capture.output / "before.png"))
    assert _image().save(str(capture.output / "after.png"))
    assert _image(0xFFFFFFFF).save(str(capture.output / "frame-0.png"))


@pytest.mark.parametrize("invalid", ["none", "pixels", "state", "target", "area"])
def test_middle_restoration_uses_actual_start_not_top_frame(
    tmp_path: Path, invalid: str
) -> None:
    """Complete image coverage and original-view restoration are separate checks."""
    capture = _start_capture(tmp_path)
    _start_evidence(capture)
    after = _image(0xFFFFFFFF) if invalid == "pixels" else _image()
    if invalid == "state":
        capture.start_position = ScrollPosition(-1.0, 0.0, 12.0)
    elif invalid == "target":
        target = json.loads((tmp_path / "target.json").read_text(encoding="utf-8"))
        target["initial_position"]["vertical"] = 0.0
        (tmp_path / "target.json").write_text(json.dumps(target), encoding="utf-8")
    elif invalid == "area":
        capture.start_area = Rect(2, 2, 10, 20)
    if invalid == "none":
        capture._verify_restored(after)
        assert capture.report["restored_pixels"] == "exact"
    else:
        with pytest.raises(RuntimeError, match="actual starting"):
            capture._verify_restored(after)


@pytest.mark.parametrize("kind", list(diagnostics._EXPLORER_STARTS))
@pytest.mark.parametrize("outcome", ["complete", "failed"])
def test_start_terminal_keeps_actual_helper_validation_and_full_row_oracle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, outcome: str
) -> None:
    """The subclass cannot substitute restoration checks for complete row proof."""
    capture = _start_capture(tmp_path, kind)
    _start_evidence(capture)
    capture.helper = Mock(diagnostics="Retained helper diagnostics")
    capture.desktop = Mock(foreground=lambda: 9)
    capture.source = Mock()
    capture.source.freeze.return_value.crop.return_value = _image()
    capture._background_bounds = None
    capture.report["release_input_tick"] = 123
    target = json.loads((tmp_path / "target.json").read_text(encoding="utf-8"))
    target.update(window=9, input_tick=123)
    (tmp_path / "target.json").write_text(json.dumps(target), encoding="utf-8")
    oracle = Mock(return_value={"oracle_frames": 16, "oracle_verified_rows": 240})
    monkeypatch.setattr(diagnostics, "verify_sequence", oracle)
    monkeypatch.setattr(
        diagnostics.app, "_verify_edge_rows", diagnostics._inspect_result
    )
    if outcome == "complete":
        result = ScrollResult(_image(), "", "restored", 16)
        capture._terminal(result)
        oracle.assert_called_once_with(result.image, tmp_path)
        assert capture.report["oracle_frames"] == 16
        assert capture.report["restored_pixels"] == "exact"
        assert QImage(str(tmp_path / "result.png")) == result.image
    else:
        (tmp_path / "movement.json").write_text(
            '{"writes": 3, "last_vertical": 0.0}', encoding="utf-8"
        )
        result = ScrollResult(None, "Ordinary capture failure", "not restored", 1)
        with pytest.raises(RuntimeError, match="Ordinary capture failure"):
            capture._terminal(result)
        assert capture.report["message"] == "Ordinary capture failure"
        assert capture.report["movement"] == {"writes": 3, "last_vertical": 0.0}
        assert "rejected_start_evidence" not in capture.report
        assert not (tmp_path / "result.png").exists()
        oracle.assert_not_called()
    assert capture.result is result
    assert "success" not in capture.report


@pytest.mark.parametrize("failure", ["state", "perimeter"])
def test_representative_result_pixels_survive_strict_restoration_failure(
    tmp_path: Path, failure: str
) -> None:
    """A failed panel assertion preserves candidate pixels without qualifying them."""
    capture = _start_capture(tmp_path)
    _start_evidence(capture)
    capture.kind = "edge-panel-click"
    capture.result = ScrollResult(_image(), "", "restored", 16)
    if failure == "state":
        (tmp_path / "restoration.json").write_text(
            '{"initial": {"vertical": 0}, "final": {"vertical": 1}}',
            encoding="utf-8",
        )
    with pytest.raises(RuntimeError):
        diagnostics._verify_restored(capture, _image())
    assert QImage(str(tmp_path / "result.png")) == capture.result.image
    assert "oracle_frames" not in capture.report
    assert "success" not in capture.report


def test_native_inverse_uses_fresh_page_and_native_units_only() -> None:
    """Measured ranges explain changed percentages without assuming pixel units."""
    original = {"min": 0, "max": 15999, "page": 1787, "pos": 7106}
    endpoint = {"min": 0, "max": 15999, "page": 1817, "pos": 14183}
    assert diagnostics._native_inverse_percent(original, 7106) == 49.996482093857736
    assert diagnostics._native_inverse_percent(endpoint, 7106) == 50.10223507015441
    assert diagnostics._native_inverse_percent(endpoint, 14183) == 100.0
    offset = {"min": 100, "max": 16099, "page": 1817, "pos": 14283}
    assert diagnostics._native_inverse_percent(offset, 7206) == 50.10223507015441


@pytest.mark.parametrize(
    ("state", "position"),
    [
        ({"min": 0, "max": 0, "page": 1}, 0),
        ({"min": 0, "max": 100, "page": 0}, 10),
        ({"min": 0, "max": 100, "page": 102}, 10),
        ({"min": 1, "max": 100, "page": 20}, 0),
        ({"min": 0, "max": 100, "page": 20}, 82),
    ],
)
def test_native_inverse_rejects_unmappable_original_position(
    state: dict[str, int], position: int
) -> None:
    """An invalid new range stops before the computed restoration write."""
    with pytest.raises(RuntimeError, match="native scroll range"):
        diagnostics._native_inverse_percent(state, position)


def _inverse_sample(phase: str, *, scale: int = 1) -> diagnostics._InverseSample:
    page = 1787 if phase == "original" else 1817
    position = 14183 if phase == "endpoint" else 7106
    state = {"min": 0, "max": 15999, "page": page, "pos": position}
    image = QImage(117 * scale, 80 * scale, QImage.Format.Format_RGB32)
    image.fill(0xFF334455)
    if phase == "endpoint":
        image.setPixel(20, 20 * scale, 0xFF556677)
    return diagnostics._InverseSample(
        ScrollPosition(
            -1.0,
            diagnostics._native_inverse_percent(state, position),
            100 * page / 15999,
        ),
        state,
        image,
    )


def _inverse_read_bar(monkeypatch: pytest.MonkeyPatch) -> diagnostics._InverseScrollBar:
    sample = _inverse_sample("original")
    bar = object.__new__(diagnostics._InverseScrollBar)
    bar.target = Mock()
    bar.target.position.return_value = sample.position
    bar.target.frame.return_value = sample.image
    bar.handle, bar.horizontal, bar.range = 73, -1.0, (0, 15999)
    bar.area = Rect(0, 0, 117, 80)
    monkeypatch.setattr(bar, "check", Mock())
    return bar


@pytest.mark.parametrize(
    "invalid",
    [
        "none",
        "vertical",
        "view_size",
        "horizontal",
        "range",
        "native",
        "during",
        "pos_down",
        "pos_up",
    ],
)
def test_inverse_native_reads_require_exact_mapping_and_atomic_pixels(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    """Arithmetic equivalence cannot hide native, horizontal or read-time changes."""
    sample = _inverse_sample("original")
    bar = _inverse_read_bar(monkeypatch)
    target = cast("Mock", bar.target)
    position = sample.position
    if invalid == "vertical":
        position = replace(position, vertical=position.vertical + 0.000001)
    elif invalid == "view_size":
        position = replace(position, view_size=position.view_size + 0.000001)
    elif invalid == "horizontal":
        position = replace(position, horizontal=0.0)
    elif invalid == "range":
        bar.range = (0, 16000)
    target.position.return_value = position
    raw: dict[str, object] = {
        **sample.native,
        "success": invalid != "native",
        "window_valid": True,
        "root_matches": True,
    }
    if invalid in ("pos_down", "pos_up"):
        raw["pos"] = sample.native["pos"] + (-1 if invalid == "pos_down" else 1)
    reader = Mock(return_value=raw)
    if invalid == "during":
        reader.side_effect = [raw, {**raw, "pos": 7107}]
    monkeypatch.setattr(diagnostics, "_scroll_info", reader)
    if invalid == "none":
        assert bar.read() == sample
    else:
        with pytest.raises(RuntimeError):
            bar.read()
    target.move.assert_not_called()


@pytest.mark.parametrize("field", ["vertical", "view_size"])
@pytest.mark.parametrize("steps", [-2, -1, 0, 1, 2])
def test_inverse_mapping_allows_only_each_immediately_adjacent_float(
    monkeypatch: pytest.MonkeyPatch, field: str, steps: int
) -> None:
    """One-ULP ordering applies only to the two computed comparisons."""
    sample = _inverse_sample("original")
    observed = math.nextafter(
        getattr(sample.position, field),
        math.inf if steps > 0 else -math.inf,
        steps=abs(steps),
    )
    position = replace(sample.position, **{field: observed})
    bar = _inverse_read_bar(monkeypatch)
    target = cast("Mock", bar.target)
    target.position.return_value = position
    monkeypatch.setattr(
        diagnostics,
        "_scroll_info",
        Mock(
            return_value={
                **sample.native,
                "success": True,
                "window_valid": True,
                "root_matches": True,
            }
        ),
    )
    if abs(steps) <= 1:
        assert bar.read().position == position
    else:
        with pytest.raises(RuntimeError, match="native/UIA mapping"):
            bar.read()
    target.move.assert_not_called()


@pytest.mark.parametrize(
    ("observed", "computed"),
    [(math.inf, math.inf), (-math.inf, -math.inf), (math.nan, 1.0), (1.0, math.nan)],
)
def test_inverse_arithmetic_requires_finite_values(
    observed: float, computed: float
) -> None:
    """Non-finite values cannot become equal through arithmetic equivalence."""
    assert not diagnostics._same_native_arithmetic(observed, computed)


@pytest.mark.parametrize("scale", [1, 2])
@pytest.mark.parametrize(
    "changed",
    [
        "none",
        "role",
        "bounds",
        "hwnd",
        "parent",
        "root",
        "pid",
        "uia_parent",
        "identity",
    ],
)
def test_inverse_retains_actual_scrollbar_identity_and_scaled_rail(
    monkeypatch: pytest.MonkeyPatch, scale: int, changed: str
) -> None:
    """The rail comes from measured roles, including 34px at 200%."""
    area, header = (
        Rect(0, 0, 117 * scale, 80 * scale),
        Rect(0, 0, 100 * scale, 10 * scale),
    )
    rail = Rect(header.right, 0, 17 * scale, area.height)
    element = Mock()
    element.control_type.return_value = diagnostics._SCROLLBAR_ROLE
    element.offscreen.return_value = False
    element.native_handle.return_value = 73
    element.bounds.return_value = rail
    target = Mock(area=area, header=header, _window=9)
    target._walker.relative.side_effect = [element, None]
    target._walker.parent.return_value = target._element
    target._automation.pointer_result.return_value = element
    target._automation.same_element.return_value = True
    target._user.GetParent.return_value = 70
    target._user.GetAncestor.return_value = 9
    target.position.return_value = ScrollPosition(-1.0, 50.0, 12.0)
    monkeypatch.setattr(
        diagnostics,
        "probe_target",
        Mock(
            return_value={
                "uia": {
                    "nodes": [
                        {
                            "parent": 0,
                            "control_type": diagnostics._SCROLLBAR_ROLE,
                            "native_handle": 73,
                            "offscreen": False,
                        }
                    ]
                }
            }
        ),
    )
    monkeypatch.setattr(diagnostics, "header_layout", Mock(return_value=(area, header)))
    pid = Mock(return_value=456)
    monkeypatch.setattr(diagnostics, "_window_pid", pid)
    bar = diagnostics._InverseScrollBar(target, 456)
    assert bar.rail == rail
    assert bar.element is element
    if changed == "role":
        element.control_type.return_value = 50033
    elif changed == "bounds":
        element.bounds.return_value = Rect(rail.x + 1, rail.y, rail.width, rail.height)
    elif changed == "hwnd":
        element.native_handle.return_value = 74
    elif changed == "parent":
        target._user.GetParent.return_value = 71
    elif changed == "root":
        target._user.GetAncestor.return_value = 10
    elif changed == "pid":
        pid.return_value = 457
    elif changed == "uia_parent":
        target._walker.parent.return_value = None
    elif changed == "identity":
        target._automation.same_element.side_effect = [True, False]
    if changed == "none":
        bar.check()
    else:
        with pytest.raises(RuntimeError):
            bar.check()
    target.move.assert_not_called()


def _normalized_evidence(capture: diagnostics.ExplorerStartCapture) -> QImage:
    """Keep native units deliberately unrelated to the tiny synthetic pixels."""
    capture.result = None
    area, header = Rect(1, 2, 10, 20), Rect(1, 2, 8, 3)
    original = {
        "native_handle": 77,
        "bar": 2,
        "success": True,
        "window_valid": True,
        "root_matches": True,
        "parent": 8,
        "root": 9,
        "pid": 123,
        "min": 0,
        "max": 15999,
        "page": 1787,
        "pos": 7106,
    }
    if capture.kind in ("explorer-click", "explorer-enter"):
        original["pos"] = 0
    final = {**original, "page": 1817}
    endpoint = {**final, "pos": 14183}

    def position(sample: dict[str, int]) -> dict[str, float]:
        return {
            "horizontal": -1.0,
            "vertical": diagnostics._native_inverse_percent(sample, sample["pos"]),
            "view_size": 100 * sample["page"] / sample["max"],
        }

    initial, restored = position(original), position(final)
    capture.start_position = ScrollPosition(**initial)
    capture.start_area = area
    geometry = {
        "captured_bounds": asdict(area),
        "uia": {
            "truncated": False,
            "nodes": [
                {"parent": None, "control_type": 50008, "offscreen": False},
                {
                    "parent": 0,
                    "control_type": 50034,
                    "bounds": asdict(header),
                    "offscreen": False,
                },
                {
                    "parent": 0,
                    "control_type": 50014,
                    "bounds": asdict(Rect(9, 2, 2, 20)),
                    "offscreen": False,
                    "native_handle": 77,
                },
            ],
        },
    }
    records = {
        "header-geometry.json": geometry,
        "restoration.json": {
            "initial": initial,
            "final": restored,
            "initial_native": [original],
            "final_native": [final],
        },
        "target.json": {"initial_position": initial, "area": asdict(area), "window": 9},
        "frames.json": [{**position(endpoint), "native_scroll_info": [endpoint]}],
    }
    for name, record in records.items():
        (capture.output / name).write_text(json.dumps(record), encoding="utf-8")
    before = _image()
    assert before.save(str(capture.output / "before.png"))
    top = before if original["pos"] == 0 else _image(0xFFFFFFFF)
    assert top.save(str(capture.output / "frame-0.png"))
    return before


@pytest.mark.parametrize(
    "kind",
    [
        "explorer-click",
        "explorer-enter",
        "explorer-middle-click",
        "explorer-middle-enter",
    ],
)
def test_normalized_oracle_requires_native_units_and_every_nonrail_pixel(
    tmp_path: Path, kind: str
) -> None:
    """Four rail-only pixel changes cannot mask even one Header/body mismatch."""
    capture = _start_capture(tmp_path)
    capture.kind = kind
    after = _normalized_evidence(capture)
    for x, y in ((8, 0), (9, 0), (8, 19), (9, 19)):
        after.setPixel(x, y, 0xFFFFFFFF)
    if "middle" in kind:
        capture._verify_restored(after)
    else:
        diagnostics._verify_restored(capture, after)
    assert capture.report["restored_pixels"] == (
        "exact non-rail; native position exact; normalized page"
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "position",
        "parent",
        "root",
        "pid",
        "min",
        "max",
        "page",
        "horizontal",
        "view",
        "two_ulp",
        "terminal_page",
        "terminal_range",
        "terminal_position",
        "header_pixel",
        "body_pixel",
        "geometry",
        "ambiguous",
        "unsupported",
        "failed_read",
        "bottom",
        "edge",
        "missing",
    ],
)
def test_normalized_oracle_rejects_invalid_independent_evidence(
    tmp_path: Path, invalid: str
) -> None:
    """A reported product success is never proof of native/pixel restoration."""
    capture = _start_capture(tmp_path)
    after = _normalized_evidence(capture)
    state_path = tmp_path / "restoration.json"
    state = json.loads(state_path.read_text())
    if invalid in ("parent", "root", "pid", "min", "max", "page"):
        state["final_native"][0][invalid] += 1
    elif invalid == "position":
        state["final_native"][0]["pos"] += 1
    elif invalid in ("horizontal", "view", "two_ulp"):
        _damage_normalized_position(state["final"], invalid)
    elif invalid.startswith("terminal_"):
        path = tmp_path / "frames.json"
        frames = json.loads(path.read_text())
        key = {
            "terminal_page": "page",
            "terminal_range": "max",
            "terminal_position": "pos",
        }[invalid]
        frames[0]["native_scroll_info"][0][key] += 1
        path.write_text(json.dumps(frames))
    elif invalid in ("header_pixel", "body_pixel"):
        after.setPixel(0, 0 if invalid == "header_pixel" else 10, 0xFFFFFFFF)
    elif invalid in ("geometry", "ambiguous", "unsupported"):
        _damage_normalized_geometry(tmp_path, invalid)
    elif invalid == "failed_read":
        state["final_native"][0]["success"] = False
    elif invalid in ("bottom", "edge"):
        capture.kind = (
            "explorer-bottom-click" if invalid == "bottom" else "edge-panel-click"
        )
    elif invalid == "missing":
        del state["final_native"]
    state_path.write_text(json.dumps(state))
    with pytest.raises((RuntimeError, CaptureError, KeyError)):
        capture._verify_restored(after)
    assert "restored_pixels" not in capture.report


def _damage_normalized_geometry(output: Path, invalid: str) -> None:
    path = output / "header-geometry.json"
    geometry = json.loads(path.read_text())
    if invalid == "geometry":
        geometry["captured_bounds"]["width"] += 1
    elif invalid == "ambiguous":
        geometry["uia"]["nodes"].append(geometry["uia"]["nodes"][-1])
    else:
        geometry["uia"]["nodes"][-1]["offscreen"] = True
    path.write_text(json.dumps(geometry))


def _damage_normalized_position(position: dict[str, float], invalid: str) -> None:
    if invalid == "two_ulp":
        position["vertical"] = math.nextafter(
            math.nextafter(position["vertical"], math.inf), math.inf
        )
    else:
        position["view_size" if invalid == "view" else "horizontal"] += 1


def _scaled_restoration_pair() -> tuple[QImage, QImage]:
    first = QImage(90, 60, QImage.Format.Format_RGB32)
    first.fill(0xFFFFFFFF)
    for y in range(1, 60):
        for x in range(89):
            if x < 5 or x >= 83 or y < 7 or y >= 54:
                first.setPixel(x, y, 0xFFB1B5BA)
    after = first.copy()
    for y in range(1, 60):
        for x in range(89):
            if x < 3 or x >= 85 or y < 5 or y >= 56:
                # Include differing corner coverage, only on original members.
                after.setPixel(x, y, 0xFF101010 if x % 2 else 0xFF505050)
    return first, after


def _fractional_restoration_pair(
    *, border: int = 3, exterior: int = 0
) -> tuple[QImage, QImage]:
    """Keep the observed 120-DPI three-pixel ring distinct from its upper bound."""
    first = QImage(90, 60, QImage.Format.Format_RGB32)
    first.fill(0xFFFFFFFF)
    after = first.copy()
    for y in range(exterior, 60 - exterior):
        for x in range(exterior, 90 - exterior):
            if (
                x < exterior + border
                or x >= 90 - exterior - border
                or y < exterior + border
                or y >= 60 - exterior - border
            ):
                first.setPixel(x, y, 0xFFB1B5BA)
                after.setPixel(x, y, 0xFF101010 if x % 2 else 0xFF505050)
    return first, after


@pytest.mark.parametrize("exterior", [0, 1])
@pytest.mark.parametrize(
    "damage", ["none", "interior_gray", "protrusion", "nonmember", "missing_ring"]
)
def test_120_ring_derives_members_and_preserves_exact_complement(
    exterior: int, damage: str
) -> None:
    """Neither the four-pixel cap nor a content color selects ignored pixels."""
    first, after = _fractional_restoration_pair(exterior=exterior)
    inner = exterior + 3
    if damage == "interior_gray":
        first.setPixel(40, 30, 0xFFB1B5BA)
    elif damage == "protrusion":
        first.setPixel(inner, 20, 0xFFB1B5BA)
        after.setPixel(inner, 20, 0xFFB1B5BA)
    elif damage == "nonmember":
        after.setPixel(inner, 20, 0xFF000000)
    elif damage == "missing_ring":
        first.setPixel(exterior, 20, 0xFFFFFFFF)
    assert diagnostics._scaled_fixture_ring_restored(first, after, dpi=120) == (
        damage == "none"
    )


@pytest.mark.parametrize("textured_before", [False, True])
def test_120_ring_preserves_every_exterior_pixel(*, textured_before: bool) -> None:
    """Changed exterior and unchanged textured exterior are both rejected."""
    first, after = _fractional_restoration_pair(exterior=1)
    after.setPixel(89, 20, 0xFF000000)
    if textured_before:
        first.setPixel(89, 20, 0xFF000000)
    assert not diagnostics._scaled_fixture_ring_restored(first, after, dpi=120)


@pytest.mark.parametrize("border", [3, 4, 5, 6])
def test_120_ring_cap_is_derived_from_css_not_192_dpi(border: int) -> None:
    """Ceil(3 CSS pixels at 1.25 scale) bounds, but does not assume, ring width."""
    first, after = _fractional_restoration_pair(border=border)
    accepted = border <= math.ceil(diagnostics._FIXTURE_CSS_BORDER * 120 / 96)
    assert diagnostics._scaled_fixture_ring_restored(first, after, dpi=120) == accepted
    if accepted:
        diagnostics._focused_panel_evidence(first, after, after, dpi=120)
    else:
        with pytest.raises(RuntimeError, match="original fixture border ring"):
            diagnostics._focused_panel_evidence(first, after, after, dpi=120)


@pytest.mark.parametrize("dpi", [96, 120, 144, 192])
@pytest.mark.parametrize("geometry", ["valid", "null", "small", "changed"])
def test_ring_helper_rejects_unsupported_dpi_and_invalid_geometry(
    dpi: int, geometry: str
) -> None:
    """Direct helper calls cannot bypass supported DPI or nonempty equal shapes."""
    first, after = _fractional_restoration_pair()
    if geometry == "null":
        first, after = QImage(), QImage()
    elif geometry == "small":
        first, after = first.copy(0, 0, 8, 8), after.copy(0, 0, 8, 8)
    elif geometry == "changed":
        after = after.copy(0, 0, 89, 60)
    assert diagnostics._scaled_fixture_ring_restored(first, after, dpi=dpi) == (
        dpi in (120, 192) and geometry == "valid"
    )


def test_120_unchanged_isolated_gray_content_stays_in_exact_complement() -> None:
    """An isolated gray content pixel remains content even when unchanged."""
    first, after = _fractional_restoration_pair()
    first.setPixel(40, 30, 0xFFB1B5BA)
    after.setPixel(40, 30, 0xFFB1B5BA)
    assert diagnostics._scaled_fixture_ring_restored(first, after, dpi=120)


@pytest.mark.parametrize("kind", ["edge-panel-click", "edge-panel-enter"])
@pytest.mark.parametrize("damage", ["none", "state", "geometry", "prose", "scrollbar"])
def test_120_panel_restoration_keeps_state_shape_and_content_gates(
    tmp_path: Path, kind: str, damage: str
) -> None:
    """Only the owned CSS panel can use its measured ring with exact scroll state."""
    capture = _start_capture(tmp_path)
    _start_evidence(capture)
    capture.kind = "edge-prose-click" if damage == "prose" else kind
    capture.result = None
    first, after = _fractional_restoration_pair()
    assert first.save(str(tmp_path / "frame-0.png"))
    (tmp_path / "target.json").write_text(json.dumps({"window_dpi": 120}))
    if damage == "state":
        path = tmp_path / "restoration.json"
        state = json.loads(path.read_text())
        state["final"]["horizontal"] += 1
        path.write_text(json.dumps(state))
    elif damage == "geometry":
        after = after.copy(0, 0, 89, 60)
    elif damage == "scrollbar":
        after.setPixel(86, 30, 0xFF000000)
    if damage == "none":
        diagnostics._verify_restored(capture, after)
        assert capture.report["restored_pixels"] == (
            "exact complement; original border ring changed"
        )
    else:
        with pytest.raises(RuntimeError, match=r"restored|Restored"):
            diagnostics._verify_restored(capture, after)
        assert "restored_pixels" not in capture.report


@pytest.mark.parametrize(
    "invalid",
    [
        "none",
        "interior_gray",
        "protrusion",
        "exterior",
        "nonmember",
        "missing_ring",
        "wide_ring",
        "two_exterior",
    ],
)
def test_scaled_fixture_restore_requires_original_ring_and_exact_complement(
    invalid: str,
) -> None:
    """Only original connected border pixels may change, never an inset rectangle."""
    first, after = _scaled_restoration_pair()
    if invalid == "interior_gray":
        first.setPixel(40, 30, 0xFFB1B5BA)
    elif invalid == "protrusion":
        first.setPixel(5, 20, 0xFFB1B5BA)
        after.setPixel(5, 20, 0xFFB1B5BA)
    elif invalid == "exterior":
        after.setPixel(89, 20, 0xFF000000)
    elif invalid == "nonmember":
        after.setPixel(5, 20, 0xFF000000)
    elif invalid == "missing_ring":
        first.setPixel(0, 20, 0xFFFFFFFF)
    elif invalid == "wide_ring":
        for y in range(1, 60):
            first.setPixel(5, y, 0xFFB1B5BA)
            first.setPixel(6, y, 0xFFB1B5BA)
    elif invalid == "two_exterior":
        for y in range(60):
            first.setPixel(88, y, 0xFFFFFFFF)
            after.setPixel(88, y, 0xFFFFFFFF)
    assert diagnostics._scaled_fixture_ring_restored(first, after) == (
        invalid == "none"
    )


def test_scaled_fixture_unchanged_isolated_gray_content_is_not_border() -> None:
    """An isolated unchanged content color does not join the measured ring."""
    first, after = _scaled_restoration_pair()
    first.setPixel(40, 30, 0xFFB1B5BA)
    after.setPixel(40, 30, 0xFFB1B5BA)
    assert diagnostics._scaled_fixture_ring_restored(first, after)


@pytest.mark.parametrize("dpi", [96, 192])
def test_scaled_ring_oracle_requires_verified_192_dpi(tmp_path: Path, dpi: int) -> None:
    """The legacy 96-DPI oracle keeps its independently established bound."""
    capture = _start_capture(tmp_path)
    _start_evidence(capture)
    capture.kind = "edge-panel-click"
    first, after = _scaled_restoration_pair()
    capture.result = None
    assert first.save(str(tmp_path / "frame-0.png"))
    (tmp_path / "target.json").write_text(
        json.dumps({"window_dpi": dpi}), encoding="utf-8"
    )
    if dpi == 96:
        with pytest.raises(RuntimeError, match="measured fixture perimeter"):
            diagnostics._verify_restored(capture, after)
    else:
        diagnostics._verify_restored(capture, after)
        assert (
            capture.report["restored_pixels"]
            == "exact complement; original border ring changed"
        )


def test_scaled_fixture_exterior_must_be_uniform() -> None:
    """An unchanged but textured column is not proof of fixture exterior."""
    first, after = _scaled_restoration_pair()
    first.setPixel(89, 20, 0xFF000000)
    after.setPixel(89, 20, 0xFF000000)
    assert not diagnostics._scaled_fixture_ring_restored(first, after)


def _normalized_footer_evidence(capture: diagnostics.ExplorerStartCapture) -> QImage:
    """Raw geometry identifies both rails independently of production layout."""
    before = _normalized_evidence(capture)
    path = capture.output / "header-geometry.json"
    geometry = json.loads(path.read_text())
    geometry["uia"]["nodes"][1]["bounds"] = asdict(Rect(1, 2, 25, 3))
    geometry["uia"]["nodes"][2]["bounds"] = asdict(Rect(9, 2, 2, 17))
    geometry["uia"]["nodes"].append(
        {
            "parent": 0,
            "control_type": 50014,
            "bounds": asdict(Rect(1, 19, 8, 3)),
            "offscreen": False,
            "native_handle": 78,
        }
    )
    path.write_text(json.dumps(geometry))
    path = capture.output / "restoration.json"
    state = json.loads(path.read_text())
    horizontal = {**state["initial_native"][0], "native_handle": 78}
    state["initial_native"].insert(0, horizontal)
    state["final_native"].insert(0, horizontal)
    path.write_text(json.dumps(state))
    path = capture.output / "frames.json"
    frames = json.loads(path.read_text())
    frames[-1]["native_scroll_info"].insert(0, horizontal)
    path.write_text(json.dumps(frames))
    return before


@pytest.mark.parametrize("kind", ["explorer-click", "explorer-middle-click"])
def test_normalized_footer_oracle_excludes_only_geometry_selected_vertical_rail(
    tmp_path: Path,
    kind: str,
) -> None:
    """A leading horizontal SB_CTL row is never confused with the vertical one."""
    capture = _start_capture(tmp_path)
    capture.kind = kind
    after = _normalized_footer_evidence(capture)
    for x, y in ((8, 0), (9, 0), (8, 16), (9, 16)):
        after.setPixel(x, y, 0xFFFFFFFF)
    if "middle" in kind:
        capture._verify_restored(after)
    else:
        diagnostics._verify_restored(capture, after)
    assert capture.report["restored_pixels"] == (
        "exact non-rail; native position exact; normalized page"
    )


@pytest.mark.parametrize("column", [0, 7, 8, 9])
def test_normalized_footer_oracle_rejects_one_footer_or_corner_pixel(
    tmp_path: Path,
    column: int,
) -> None:
    """Restoration cannot hide horizontal rail/corner changes in the V exclusion."""
    capture = _start_capture(tmp_path)
    after = _normalized_footer_evidence(capture)
    after.setPixel(column, 19, 0xFFFFFFFF)
    with pytest.raises(RuntimeError, match="non-rail pixels"):
        capture._verify_restored(after)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "extra", "swapped", "rail-height"]
)
def test_normalized_footer_oracle_rejects_ambiguous_native_rail(
    tmp_path: Path,
    damage: str,
) -> None:
    """Both declared controls must remain present, distinct and exactly partitioned."""
    capture = _start_capture(tmp_path)
    after = _normalized_footer_evidence(capture)
    if damage == "rail-height":
        path = tmp_path / "header-geometry.json"
        geometry = json.loads(path.read_text())
        geometry["uia"]["nodes"][2]["bounds"]["height"] = 20
        path.write_text(json.dumps(geometry))
    else:
        path = tmp_path / "restoration.json"
        state = json.loads(path.read_text())
        rows = state["final_native"]
        if damage == "missing":
            rows.pop(0)
        elif damage == "duplicate":
            rows[0]["native_handle"] = 77
        elif damage == "extra":
            rows.append({**rows[0], "native_handle": 79})
        else:
            rows[0]["native_handle"], rows[1]["native_handle"] = 77, 78
        path.write_text(json.dumps(state))
    with pytest.raises((RuntimeError, CaptureError)):
        capture._verify_restored(after)


def _focused_capture(output: Path) -> diagnostics.EdgeFocusedCapture:
    capture = object.__new__(diagnostics.EdgeFocusedCapture)
    capture.kind, capture.output, capture.report = (
        "edge-panel-focused-click",
        output,
        {},
    )
    capture.window = WindowInfo(9, "Owned fixture", Rect(0, 0, 90, 60))
    capture.point = Point(40, 30)
    capture.report["focus_setup_point"] = asdict(capture.point)
    capture.result = None
    capture.desktop = Mock()
    capture.desktop.foreground.return_value = 9
    capture.desktop.user.GetAncestor.return_value = 9
    capture.desktop.user.SetCursorPos.return_value = True
    capture.desktop.input_tick.return_value = 123
    before, after = _scaled_restoration_pair()
    capture.source = Mock()
    capture.source.freeze.side_effect = [
        FrozenScreen(before, capture.window.bounds),
        FrozenScreen(after, capture.window.bounds),
    ]
    return capture


@pytest.mark.parametrize("kind", diagnostics._FOCUSED_PANELS)
def test_focused_case_uses_unchanged_panel_document_and_production_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: str,
) -> None:
    """Focus cases differ only in the explicitly named pre-chooser subclass."""
    runner = Mock()
    monkeypatch.setattr(diagnostics, "EdgeFocusedCapture", Mock(return_value=runner))
    monkeypatch.setattr(diagnostics.sys, "argv", ["diagnostics", kind])
    document = diagnostics.document("edge-panel")
    original_launch = diagnostics.native._launch

    def main(*, gui: bool, case: object) -> None:
        assert gui
        assert case == diagnostics._case
        assert diagnostics.native._launch == original_launch
        assert diagnostics.native._document(nested=False) == document

    monkeypatch.setattr(diagnostics.native, "main", main)
    diagnostics.main()
    diagnostics._case(kind, tmp_path, {})
    runner.run.assert_called_once_with()


@pytest.mark.parametrize("failure", ["none", "click", "shift_tab", "tab"])
def test_focus_setup_has_one_attempt_releases_inputs_and_baselines_last(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    """No failed gesture is retried and no incomplete sequence gets a baseline."""
    capture = _focused_capture(tmp_path)
    desktop = cast("Mock", capture.desktop)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(diagnostics.app, "_window", lambda: capture.window)
    monkeypatch.setattr(diagnostics.app.AppCapture, "_settle_target_foreground", Mock())
    waits: list[int] = []

    def wait(milliseconds: int) -> None:
        waits.append(milliseconds)
        if (
            failure != "none"
            and len(waits) == {"click": 1, "shift_tab": 2, "tab": 3}[failure]
        ):
            raise RuntimeError("setup interrupted")
        if len(waits) <= 3:
            desktop.input_tick.assert_not_called()

    monkeypatch.setattr(diagnostics.QTest, "qWait", wait)
    if failure == "none":
        capture._settle_target_foreground()
        assert capture.report["focus_setup_completed_input_tick"] == 123
        assert desktop.user.GetAncestor.call_count == 5
        assert (tmp_path / "focus-before.png").exists()
        assert (tmp_path / "focus-after.png").exists()
    else:
        with pytest.raises(RuntimeError, match="setup interrupted"):
            capture._settle_target_foreground()
        desktop.input_tick.assert_not_called()
        assert "focus_setup_completed_input_tick" not in capture.report
    assert desktop.user.mouse_event.call_args_list[-1].args == (4, 0, 0, 0, 0)
    keys = [call.args for call in desktop.user.keybd_event.call_args_list]
    if failure != "click":
        assert keys[:4] == [(16, 0, 0, 0), (9, 0, 0, 0), (9, 0, 2, 0), (16, 0, 2, 0)]
    if failure in ("none", "tab"):
        assert keys[4:] == [(9, 0, 0, 0), (9, 0, 2, 0)]


@pytest.mark.parametrize(
    "invalid", ["host", "kind", "window", "foreground", "point", "hit", "cursor"]
)
def test_focus_setup_requires_owned_window_and_point_before_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: str,
) -> None:
    """Any ownership failure blocks input rather than redirecting the gesture."""
    capture = _focused_capture(tmp_path)
    desktop = cast("Mock", capture.desktop)
    monkeypatch.setenv("GITHUB_ACTIONS", "false" if invalid == "host" else "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(
        diagnostics.app,
        "_window",
        lambda: None if invalid == "window" else capture.window,
    )
    if invalid == "kind":
        capture.kind = "edge-panel-click"
    elif invalid == "foreground":
        desktop.foreground.return_value = 8
    elif invalid == "point":
        capture.point = Point(-1, -1)
    elif invalid == "hit":
        desktop.user.GetAncestor.return_value = 8
    elif invalid == "cursor":
        desktop.user.SetCursorPos.return_value = False
    with pytest.raises(RuntimeError):
        capture._focus_gesture(())
    desktop.user.mouse_event.assert_not_called()
    desktop.user.keybd_event.assert_not_called()


@pytest.mark.parametrize(
    "invalid",
    [
        "none",
        "unchanged",
        "content",
        "gray_content",
        "exterior",
        "side",
        "textured_side",
        "top_only",
        "missing_second",
    ],
)
def test_focused_start_is_proved_from_pixels_not_input_gestures(invalid: str) -> None:
    """Initial focus needs a real original-ring change and stable focused sides."""
    before, first = _scaled_restoration_pair()
    second = first.copy()
    if invalid == "unchanged":
        first = before.copy()
    elif invalid == "content":
        first.setPixel(40, 20, 0xFF000000)
    elif invalid == "gray_content":
        before.setPixel(40, 20, 0xFFB1B5BA)
    elif invalid == "exterior":
        first.setPixel(89, 20, 0xFF000000)
    elif invalid == "side":
        second.setPixel(0, 20, 0xFFFFFFFF)
    elif invalid == "textured_side":
        first.setPixel(0, 20, 0xFFFFFFFF)
    elif invalid == "top_only":
        first = before.copy()
        first.setPixel(40, 2, 0xFF000000)
    elif invalid == "missing_second":
        second = QImage()
    if invalid == "none":
        second.setPixel(40, 20, 0xFF000000)  # Scrolling content is expected to move.
        report = diagnostics._focused_panel_evidence(before, first, second)
        assert report["changed_side_columns"] == [[0, 1, 2], [85, 86, 87, 88]]
        assert report["nonborder_pixels"] == "exact"
    else:
        with pytest.raises(RuntimeError):
            diagnostics._focused_panel_evidence(before, first, second)


@pytest.mark.parametrize(
    "invalid", ["none", "dpi", "window", "position", "frame0", "restoration"]
)
@pytest.mark.parametrize("dpi", [96, 192])
def test_focused_start_matches_actual_post_setup_crop_and_exact_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: str,
    dpi: int,
) -> None:
    """The diagnostic cannot claim focus from a different crop, state or DPI."""
    capture = _focused_capture(tmp_path)
    capture.report["expected_target_dpi"] = dpi
    before, first = _scaled_restoration_pair()
    capture._focus_before = FrozenScreen(before, capture.window.bounds)
    capture._focus_after = FrozenScreen(first, capture.window.bounds)
    assert first.save(str(tmp_path / "frame-0.png"))
    assert first.save(str(tmp_path / "frame-1.png"))
    state = {"horizontal": 0.0, "vertical": 0.0, "view_size": 10.0}
    if invalid == "position":
        state["vertical"] = 1.0
    target = {
        "window": 8 if invalid == "window" else 9,
        "window_dpi": 144 if invalid == "dpi" else dpi,
        "area": asdict(capture.window.bounds),
        "initial_position": state,
    }
    (tmp_path / "target.json").write_text(json.dumps(target), encoding="utf-8")
    (tmp_path / "restoration.json").write_text(
        json.dumps({"initial": state, "final": state}), encoding="utf-8"
    )
    monkeypatch.setattr(
        diagnostics.app.AppCapture, "_verify_restored", diagnostics._verify_restored
    )
    after = first.copy()
    if invalid == "frame0":
        altered = first.copy()
        altered.setPixel(40, 20, 0xFF000000)
        assert altered.save(str(tmp_path / "frame-0.png"))
    elif invalid == "restoration":
        after.setPixel(40, 20, 0xFF000000)
    if invalid == "none":
        capture._verify_restored(after)
        assert capture.report["restored_pixels"] == "exact"
        assert "focused_start_evidence" in capture.report
    else:
        with pytest.raises(RuntimeError):
            capture._verify_restored(after)


def test_focused_native_cases_keep_mandatory_200_percent_controls() -> None:
    """Focus variants share the existing same-session display and deadline gates."""
    workflow = Path(".github/workflows/simple-scroll-native.yml").read_text(
        encoding="utf-8"
    )
    cases = workflow[
        workflow.index("$cases = @(") : workflow.index("foreach ($case in $cases)")
    ]
    assert cases.index("'edge-panel-enter'") < cases.index("'edge-panel-focused-click'")
    assert cases.index("'edge-panel-focused-click'") < cases.index(
        "'edge-panel-focused-enter'"
    )
    assert "explorer-" not in cases
    assert "Optional fully traced tiny Explorer stress" in workflow
    assert "if ($target.window_dpi -ne 192)" in workflow


def test_focus_setup_rechecks_ownership_before_keyboard_after_click(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A different root after the completed click stops the sole setup attempt."""
    capture = _focused_capture(tmp_path)
    desktop = cast("Mock", capture.desktop)
    desktop.user.GetAncestor.side_effect = [9, 9, 8]
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(diagnostics.app, "_window", lambda: capture.window)
    monkeypatch.setattr(diagnostics.app.AppCapture, "_settle_target_foreground", Mock())
    monkeypatch.setattr(diagnostics.QTest, "qWait", Mock())
    with pytest.raises(RuntimeError, match="not covered"):
        capture._settle_target_foreground()
    desktop.user.keybd_event.assert_not_called()
    desktop.input_tick.assert_not_called()
    assert desktop.user.mouse_event.call_count == 2


def test_focus_setup_releases_shift_when_tab_press_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A partial key chord cannot leave the setup modifier held."""
    capture = _focused_capture(tmp_path)
    desktop = cast("Mock", capture.desktop)
    monkeypatch.setattr(capture, "_focus_owner", Mock())

    def key_event(key: int, _scan: int, flags: int, _extra: int) -> None:
        if key == 9 and flags == 0:
            raise RuntimeError("Tab press failed")

    desktop.user.keybd_event.side_effect = key_event
    with pytest.raises(RuntimeError, match="Tab press failed"):
        capture._focus_gesture((16, 9))
    assert [call.args for call in desktop.user.keybd_event.call_args_list] == [
        (16, 0, 0, 0),
        (9, 0, 0, 0),
        (9, 0, 2, 0),
        (16, 0, 2, 0),
    ]


def test_fractional_focus_point_records_native_pixel_used_by_target_proof(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Setup records the OS point without changing the later chooser's center."""
    capture = _focused_capture(tmp_path)
    capture.point = Point(40.6, 30.4)
    capture.window = WindowInfo(9, "Owned fixture", Rect(0, 0, 140, 60))
    area = Rect(41, 0, 90, 60)
    before, focused = _scaled_restoration_pair()

    def desktop_image(panel: QImage) -> FrozenScreen:
        image = QImage(140, 60, QImage.Format.Format_RGB32)
        image.fill(0xFFFFFFFF)
        painter = QPainter(image)
        painter.drawImage(41, 0, panel)
        painter.end()
        return FrozenScreen(image, capture.window.bounds)

    source = cast("Mock", capture.source)
    source.freeze.side_effect = [desktop_image(before), desktop_image(focused)]
    desktop = cast("Mock", capture.desktop)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setattr(diagnostics.app, "_window", lambda: capture.window)
    monkeypatch.setattr(diagnostics.app.AppCapture, "_settle_target_foreground", Mock())
    monkeypatch.setattr(diagnostics.QTest, "qWait", Mock())
    capture._settle_target_foreground()
    assert capture.point == Point(40.6, 30.4)
    assert capture.report["focus_setup_point"] == {"x": 41, "y": 30}
    desktop.user.SetCursorPos.assert_called_once_with(41, 30)
    assert [
        (call.args[0].x, call.args[0].y)
        for call in desktop.user.WindowFromPoint.call_args_list
    ] == [(41, 30)] * 5

    assert focused.save(str(tmp_path / "frame-0.png"))
    assert focused.save(str(tmp_path / "frame-1.png"))
    state = {"horizontal": 0.0, "vertical": 0.0, "view_size": 10.0}
    target = {
        "window": 9,
        "window_dpi": 192,
        "area": asdict(area),
        "initial_position": state,
    }
    (tmp_path / "target.json").write_text(json.dumps(target), encoding="utf-8")
    (tmp_path / "restoration.json").write_text(
        json.dumps({"initial": state, "final": state}), encoding="utf-8"
    )
    monkeypatch.setattr(
        diagnostics.app.AppCapture, "_verify_restored", diagnostics._verify_restored
    )
    assert not area.contains(capture.point)
    assert area.contains(Point(41, 30))
    capture._verify_restored(focused)
    assert "focused_start_evidence" in capture.report
    # The former fractional record would reject this exact valid native point.
    capture.report["focus_setup_point"] = asdict(capture.point)
    with pytest.raises(RuntimeError, match="focused 192-DPI starting state"):
        capture._verify_restored(focused)


def _step_geometry(position: ScrollPosition) -> dict[str, object]:
    return {
        "captured_bounds": asdict(Rect(0, 0, 654, 425)),
        "window_dpi": 192,
        "position": asdict(position),
        "uia": {
            "truncated": False,
            "nodes": [
                {"control_type": 50008, "parent": None, "offscreen": False},
                {
                    "control_type": 50034,
                    "parent": 0,
                    "offscreen": False,
                    "bounds": asdict(Rect(0, 0, 1405, 52)),
                },
                {
                    "control_type": 50014,
                    "parent": 0,
                    "offscreen": False,
                    "bounds": asdict(Rect(620, 0, 34, 391)),
                    "native_handle": 73,
                },
                {
                    "control_type": 50014,
                    "parent": 0,
                    "offscreen": False,
                    "bounds": asdict(Rect(0, 391, 620, 34)),
                    "native_handle": 74,
                },
                {
                    "control_type": 50007,
                    "parent": 0,
                    "offscreen": False,
                    "bounds": asdict(Rect(28, 308, 1350, 48)),
                },
            ],
        },
    }


@pytest.mark.parametrize("no_hbar", [False, True])
@pytest.mark.parametrize(("work_height", "expected_height"), [(1080, 1000), (980, 960)])
def test_taller_explorer_changes_only_the_existing_height_cap(
    monkeypatch: pytest.MonkeyPatch,
    work_height: int,
    expected_height: int,
    *,
    no_hbar: bool,
) -> None:
    """Widen only no-hbar, fit real work bounds, and retain the exact owner."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    width = 1900 if no_hbar else 1000
    window = WindowInfo(9, "fixture", Rect(10, 10, width, expected_height))
    user = Mock()

    def work(_action: int, _parameter: int, pointer: c.c_void_p, _flags: int) -> int:
        value = c.cast(pointer, c.POINTER(w.RECT)).contents
        value.left, value.top, value.right, value.bottom = 0, 0, 1920, work_height
        return 1

    def rectangle(handle: int, pointer: c.c_void_p) -> int:
        assert handle == 9
        value = c.cast(pointer, c.POINTER(w.RECT)).contents
        value.left, value.top = 10, 10
        value.right, value.bottom = 10 + width, 10 + expected_height
        return 1

    user.SystemParametersInfoW.side_effect = work
    user.GetWindowRect.side_effect = rectangle
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)

    def prepare(current: WindowInfo) -> WindowInfo:
        assert current is window
        assert width == diagnostics.native._WINDOW_WIDTH
        assert diagnostics.native._WINDOW_HEIGHT == 1000
        return window

    runner = diagnostics.ExplorerCase(taller=True, no_hbar=no_hbar)
    runner.prepare = Mock(side_effect=prepare)
    monkeypatch.setattr(runner, "_verified_window", lambda: window)
    assert runner._prepare_taller(window) == window
    assert diagnostics.native._WINDOW_HEIGHT == 760
    layout = cast("dict[str, object]", runner.report["explorer_layout"])
    assert layout["requested_outer"] == layout["actual_outer"] == asdict(window.bounds)
    assert layout["work_area"] == asdict(Rect(0, 0, 1920, work_height))


@pytest.mark.parametrize(
    "failure", ["host", "work", "small", "prepare", "bounds", "identity"]
)
def test_taller_explorer_never_accepts_unverified_or_offscreen_geometry(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """No retry or fallback follows an unavailable area or changed native HWND."""
    monkeypatch.setenv("GITHUB_ACTIONS", "false" if failure == "host" else "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    user = Mock()

    def work(_action: int, _parameter: int, pointer: c.c_void_p, _flags: int) -> int:
        value = c.cast(pointer, c.POINTER(w.RECT)).contents
        value.left, value.top = 0, 0
        value.right, value.bottom = 1920, 760 if failure == "small" else 1080
        return int(failure != "work")

    user.SystemParametersInfoW.side_effect = work
    # A true return with absent/mismatched bounds must not be accepted.
    user.GetWindowRect.return_value = 1
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    runner = diagnostics.ExplorerCase(taller=True)
    window = WindowInfo(9, "fixture", Rect(10, 10, 1000, 1000))
    prepare = Mock(
        return_value=window,
        side_effect=RuntimeError("Move failed") if failure == "prepare" else None,
    )
    runner.prepare = prepare
    monkeypatch.setattr(
        runner,
        "_verified_window",
        Mock(
            return_value=window,
            side_effect=RuntimeError("Identity changed")
            if failure == "identity"
            else None,
        ),
    )
    with pytest.raises(RuntimeError):
        runner._prepare_taller(window)
    assert diagnostics.native._WINDOW_HEIGHT == 760
    assert prepare.call_count == int(failure in ("prepare", "bounds", "identity"))


@pytest.mark.parametrize(
    "kind", ["explorer-click", "explorer-enter", *diagnostics._EXPLORER_STARTS]
)
def test_taller_option_retains_the_existing_gesture_and_start_case(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """An explicit fixture flag does not rename product cases or change routing."""
    runner = Mock(spec=diagnostics.ExplorerCase)
    constructor = Mock(return_value=runner)
    monkeypatch.setattr(diagnostics, "ExplorerCase", constructor)
    monkeypatch.setattr(
        diagnostics.sys, "argv", ["diagnostics", kind, "--taller-explorer"]
    )
    main = Mock()
    monkeypatch.setattr(diagnostics.native, "main", main)
    diagnostics.main()
    constructor.assert_called_once_with(taller=True)
    main.assert_called_once_with(gui=True, case=runner.run)


@pytest.mark.parametrize(
    "arguments",
    [
        ["edge-prose-click", "--taller-explorer"],
        ["explorer-helper-output", "--taller-explorer"],
        ["explorer-click", "--anything"],
    ],
)
def test_taller_option_rejects_nonfixture_or_unrecognized_requests(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    """Only the explicitly owned Explorer capture fixture receives taller setup."""
    monkeypatch.setattr(diagnostics.sys, "argv", ["diagnostics", *arguments])
    main = Mock()
    monkeypatch.setattr(diagnostics.native, "main", main)
    with pytest.raises(RuntimeError, match="Only Explorer capture"):
        diagnostics.main()
    main.assert_not_called()


@pytest.mark.parametrize("horizontal", [0.0, 50.0, -1.0])
def test_taller_layout_records_actual_header_rails_and_body(
    tmp_path: Path, horizontal: float
) -> None:
    """The representative label is earned from raw roles, not outer-window size."""
    position = ScrollPosition(horizontal, 0.0, 10.0)
    geometry = _step_geometry(position)
    target = {
        "initial_position": asdict(position),
        "area": geometry["captured_bounds"],
        "window_dpi": 192,
    }
    (tmp_path / "header-geometry.json").write_text(json.dumps(geometry))
    (tmp_path / "target.json").write_text(json.dumps(target))
    report: dict[str, object] = {}
    if horizontal == 0.0:
        diagnostics._taller_target_layout(tmp_path, report)
        assert report["explorer_body_height"] == 339
    else:
        with pytest.raises(RuntimeError, match="measured H0/two-rail"):
            diagnostics._taller_target_layout(tmp_path, report)
    layout = cast("dict[str, object]", report["explorer_target_layout"])
    assert layout["horizontal"] == horizontal
    assert layout["header"] == asdict(Rect(0, 0, 620, 52))
    assert layout["footer"] == asdict(Rect(0, 391, 620, 34))
    assert layout["rail"] == asdict(Rect(620, 0, 34, 391))


@pytest.mark.parametrize(("percent", "dpi"), [(125, 120), (200, 192)])
def test_focused_scale_changes_only_expected_target_dpi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, percent: int, dpi: int
) -> None:
    """125% runs the same strict outline witness, without inventing a new mask."""
    monkeypatch.setenv("DISPLAY_SCALE", str(percent))
    capture = Mock()
    monkeypatch.setattr(diagnostics, "EdgeFocusedCapture", Mock(return_value=capture))
    report: dict[str, object] = {}
    diagnostics._case("edge-panel-focused-click", tmp_path, report)
    assert report["expected_target_dpi"] == dpi
    assert diagnostics._SCALED_FIXTURE_DPI == 192
    assert diagnostics._FIXTURE_CSS_BORDER == 3
    capture.run.assert_called_once_with()


def test_representative_cases_are_independent_from_optional_stress() -> None:
    """Failure isolation needs no unsafe post-cleanup continuation or false green."""
    workflow = Path(".github/workflows/simple-scroll-native.yml").read_text()
    original, representative = workflow.split("  representative-layout:", 1)
    assert "Native 200% capture failed: $case" in original
    assert "--taller-explorer" not in original
    assert "fail-fast: false" in representative
    assert "continue-on-error" not in representative
    assert "needs:" not in representative
    assert "scale: [125, 200]" in representative
    for case in ("explorer-click", "explorer-enter", *diagnostics._EXPLORER_STARTS):
        assert case in representative
    for case in (
        "edge-prose-click",
        "edge-prose-enter",
        "edge-panel-click",
        "edge-panel-enter",
        *diagnostics._FOCUSED_PANELS,
    ):
        assert f"{{scale: 125, application: {case}}}" in representative
        assert f"{{scale: 200, application: {case}}}" not in representative
    assert "'125' = 120; '200' = 192" in representative
    assert "steps.setup.outcome == 'success'" in representative
    assert "QT_SCALE_FACTOR" not in representative


@pytest.mark.parametrize("failure", ["area", "raw_horizontal", "one_rail"])
def test_taller_layout_cannot_assume_h0_or_two_rails(
    tmp_path: Path, failure: str
) -> None:
    """A well-formed but different actual layout cannot earn the fixture label."""
    position = ScrollPosition(0.0, 0.0, 10.0)
    geometry = _step_geometry(position)
    target = {
        "initial_position": asdict(position),
        "area": geometry["captured_bounds"],
        "window_dpi": 192,
    }
    if failure == "area":
        target["area"] = asdict(Rect(0, 0, 654, 424))
    elif failure == "raw_horizontal":
        geometry["position"] = asdict(ScrollPosition(20.0, 0.0, 10.0))
    else:
        tree = cast("dict[str, object]", geometry["uia"])
        nodes = cast("list[dict[str, object]]", tree["nodes"])
        nodes[1]["bounds"] = asdict(Rect(0, 0, 620, 52))
        nodes[2]["bounds"] = asdict(Rect(620, 0, 34, 425))
        del nodes[3]
    (tmp_path / "header-geometry.json").write_text(json.dumps(geometry))
    (tmp_path / "target.json").write_text(json.dumps(target))
    with pytest.raises(RuntimeError, match="measured H0/two-rail"):
        diagnostics._taller_target_layout(tmp_path, {})


@pytest.mark.parametrize(
    "kind",
    ["edge-panel-click", "edge-panel-enter", *diagnostics._FOCUSED_PANELS],
)
@pytest.mark.parametrize("damage", ["none", "outer_nonring", "ring"])
def test_120_dpi_already_focused_restoration_requires_every_final_pixel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, damage: str
) -> None:
    """A valid focused start cannot excuse final damage inside the legacy inset."""
    capture = _focused_capture(tmp_path)
    capture.kind = kind
    capture.report["expected_target_dpi"] = 120
    before, first = _fractional_restoration_pair(exterior=1)
    capture._focus_before = FrozenScreen(before, capture.window.bounds)
    capture._focus_after = FrozenScreen(first, capture.window.bounds)
    assert first.save(str(tmp_path / "frame-0.png"))
    assert first.save(str(tmp_path / "frame-1.png"))
    state = {"horizontal": 0.0, "vertical": 0.0, "view_size": 10.0}
    target = {
        "window": capture.window.handle,
        "window_dpi": 120,
        "area": asdict(capture.window.bounds),
        "initial_position": state,
    }
    (tmp_path / "target.json").write_text(json.dumps(target), encoding="utf-8")
    (tmp_path / "restoration.json").write_text(
        json.dumps({"initial": state, "final": state}), encoding="utf-8"
    )
    # This is the actual main() helper binding, not a mocked restoration verdict.
    monkeypatch.setattr(
        diagnostics.app.AppCapture, "_verify_restored", diagnostics._verify_restored
    )
    after = first.copy()
    if damage != "none":
        x, y = (89, 30) if damage == "outer_nonring" else (1, 30)
        assert (before.pixel(x, y) == 0xFFB1B5BA) == (damage == "ring")
        after.setPixel(x, y, 0xFF000000)
        # Demonstrate the exact old false-positive: its entire inset is unchanged.
        assert first.copy(4, 4, 82, 52) == after.copy(4, 4, 82, 52)
    verify = (
        capture._verify_restored
        if kind in diagnostics._FOCUSED_PANELS
        else lambda image: diagnostics._verify_restored(capture, image)
    )
    if damage == "none":
        verify(after)
        assert capture.report["restored_pixels"] == "exact"
    else:
        with pytest.raises(RuntimeError, match="original fixture border ring"):
            verify(after)
        assert "restored_pixels" not in capture.report
    if kind in diagnostics._FOCUSED_PANELS:
        assert "focused_start_evidence" in capture.report


def _helper_fixture(
    height: int,
) -> tuple[diagnostics._InverseSample, dict[str, object]]:
    geometry = _step_geometry(ScrollPosition(0.0, 0.0, 10.0))
    geometry["captured_bounds"] = asdict(Rect(0, 0, 654, height))
    nodes = cast("dict[str, list[dict[str, object]]]", geometry["uia"])["nodes"]
    nodes[2]["bounds"] = asdict(Rect(620, 0, 34, height - 34))
    nodes[3]["bounds"] = asdict(Rect(0, height - 34, 620, 34))
    nodes[4:] = [
        {
            "parent": 0,
            "control_type": 50007,
            "offscreen": False,
            "bounds": asdict(Rect(28, 52 + i * 48, 1350, 48)),
        }
        for i in range(3)
    ]
    image = QImage(654, height, QImage.Format.Format_RGB32)
    image.fill(0xFFEEEEEE)
    image.setPixel(30, 60, 0xFF000000)  # A focus/icon pixel must never be masked.
    painter = QPainter(image)
    painter.fillRect(620, 0, 34, height - 34, 0xFFCCCCCC)
    painter.fillRect(0, height - 34, 654, 34, 0xFFAAAAAA)
    painter.end()
    rows = [
        {
            "native_handle": handle,
            "bar": 2,
            "parent": 70,
            "root": 9,
            "pid": 456,
            "success": True,
            "window_valid": True,
            "root_matches": True,
            "min": 0,
            "max": 1000,
            "page": 100,
            "pos": 0,
        }
        for handle in (73, 74)
    ]
    sample = diagnostics._InverseSample(
        ScrollPosition(0.0, 0.0, 10.0),
        {key: rows[0][key] for key in ("min", "max", "page", "pos")},
        image,
    )
    return sample, {"geometry": geometry, "native_rows": rows}


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "focus",
        "header",
        "body",
        "footer",
        "corner",
        "position",
        "range",
        "pid",
        "handle",
        "invalid",
        "dpi",
        "order",
        "shape",
        "padding",
    ],
)
def test_helper_equivalence_is_exact_and_fail_closed(damage: str | None) -> None:
    """A changed focus, item order, native owner or footer is never repaired."""
    before, after = _helper_fixture(425), _helper_fixture(290)
    image, evidence = after[0].image, after[1]
    geometry = cast("dict[str, object]", evidence["geometry"])
    rows = cast("list[dict[str, object]]", evidence["native_rows"])
    nodes = cast("dict[str, list[dict[str, object]]]", geometry["uia"])["nodes"]
    pixels = {
        "focus": (30, 60),
        "header": (0, 0),
        "body": (200, 210),
        "footer": (0, 289),
        "corner": (653, 289),
    }
    if damage in pixels:
        image.setPixel(*pixels[damage], 0xFF112233)
    elif damage in ("position", "range", "pid", "handle"):
        key = {
            "position": "pos",
            "range": "max",
            "pid": "pid",
            "handle": "native_handle",
        }[damage]
        rows[1][key] = 999
    elif damage == "invalid":
        rows[1]["success"] = False
    elif damage == "dpi":
        geometry["window_dpi"] = 120
    elif damage == "order":
        nodes[4], nodes[5] = nodes[5], nodes[4]
    elif damage in ("shape", "padding"):
        bounds = cast("dict[str, int]", nodes[4]["bounds"])
        bounds["width" if damage == "shape" else "y"] += 1
    if damage is None:
        image.setPixel(653, 100, 0xFF112233)  # Only measured V rail is unverified.
        assert diagnostics._helper_match(before, after) == (620, 34)
        assert image.pixel(653, 100) == 0xFF112233  # Never alter the full output.
    else:
        with pytest.raises((RuntimeError, CaptureError)):
            diagnostics._helper_match(before, after)


@pytest.mark.parametrize("changing", [False, True])
def test_helper_observation_uses_two_fixed_reads_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, changing: bool
) -> None:
    """Native/UIA/PNG evidence is repeated, but mismatch cannot trigger a trial."""
    first, evidence = _helper_fixture(290)
    second, _ = _helper_fixture(290)
    if changing:
        second.image.setPixel(30, 60, 0xFF112233)
    geometry = cast("dict[str, object]", evidence["geometry"])
    area, header, footer, rail = diagnostics.list_layout(geometry)
    bar = Mock(
        read=Mock(side_effect=[first, second]),
        area=area,
        header=header,
        footer=footer,
        rail=rail,
        target=Mock(registration_dpi=192),
    )
    probe = Mock(return_value=geometry)
    monkeypatch.setattr(diagnostics, "probe_target", probe)
    monkeypatch.setattr(
        diagnostics.native,
        "_restoration_native",
        Mock(return_value=evidence["native_rows"]),
    )
    if changing:
        with pytest.raises(RuntimeError, match="repeated observation"):
            diagnostics._helper_observe(bar, tmp_path, "before")
    else:
        sample, receipt = diagnostics._helper_observe(bar, tmp_path, "before")
        assert sample == first
        assert receipt["native_rows"] == evidence["native_rows"]
    assert bar.read.call_count == probe.call_count == 2
    assert (tmp_path / "before.json").is_file()
    assert not QImage(str(tmp_path / "before-repeat.png")).isNull()


def _inject_helper_late_change(
    monkeypatch: pytest.MonkeyPatch,
    target: Mock,
    bar: Mock,
    seen: list[tuple[str, str]],
    late: list[str] | None,
) -> tuple[dict[str, bool], list[str]]:
    """Change eligibility only at the requested post-observation work boundary."""
    state = {"changed": False}
    guarded: list[str] = []

    def change_during(site: str) -> None:
        if (
            late is not None
            and seen
            and seen[-1] == (late[1], "restored")
            and site == late[2]
        ):
            state["changed"] = True

    def guard() -> None:
        guarded.append("guard")
        if state["changed"] and late is not None and late[3] in ("input", "focus"):
            raise CaptureError(f"Late {late[3]} change")

    def position() -> ScrollPosition:
        guarded.append("position")
        change_during("position")
        return ScrollPosition(
            0.0,
            5.0
            if state["changed"] and late is not None and late[3] == "position"
            else 0.0,
            10.0,
        )

    bar.check.side_effect = guard
    target.position.side_effect = position
    original_save = QImage.save
    original_inventory = diagnostics._helper_inventory

    def save(image: QImage, path: str) -> bool:
        result = original_save(image, path)
        if Path(path).name == "result.png":
            change_during("png")
        return result

    def inventory(path: Path) -> list[tuple[str, int, int, str]]:
        result = original_inventory(path)
        change_during("inventory")
        return result

    monkeypatch.setattr(QImage, "save", save)
    monkeypatch.setattr(diagnostics, "_helper_inventory", inventory)

    return state, guarded


@pytest.mark.parametrize(
    "failure",
    [None, "initial", "reference", "result", "height", "restore", "helper"]
    + [
        f"late:{phase}:{site}:{change}"
        for phase in ("reference", "tiny")
        for site in ("png", "inventory")
        for change in ("input", "focus", "position")
    ]
    + [
        f"late:{phase}:position:{change}"
        for phase in ("reference", "tiny")
        for change in ("input", "focus")
    ],
)
def test_helper_output_runs_one_reference_then_one_plain_helper(  # noqa: PLR0915 - explicit two-phase rejection matrix
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None
) -> None:
    """The oracle runs only on real reference receipts; tiny failures stay failed."""
    from unittest.mock import MagicMock  # noqa: PLC0415

    tall = WindowInfo(9, "owned", Rect(10, 10, 1000, 1000))
    tiny = replace(tall, bounds=Rect(10, 10, 1000, 760))
    current = tall
    folder = tmp_path / "fixture"
    folder.mkdir()
    (folder / "Project note 000.txt").write_text("same owned input")
    owner = Mock(folder=folder, pid=456)
    owner._prepare_taller.return_value = tall
    owner._verified_window.side_effect = lambda: current

    def shrink(_owner: diagnostics.ExplorerCase, window: WindowInfo) -> WindowInfo:
        nonlocal current
        assert window == tall
        current = tiny
        return tiny

    resize = Mock(side_effect=shrink)
    monkeypatch.setattr(diagnostics, "_helper_shrink", resize)
    target = MagicMock(_input_tick=67)
    target.__enter__.return_value = target
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", Mock(return_value=target))
    bar = Mock()
    monkeypatch.setattr(diagnostics, "_InverseScrollBar", Mock(return_value=bar))
    seen: list[tuple[str, str]] = []
    late = (
        failure.split(":")
        if failure is not None and failure.startswith("late:")
        else None
    )
    state, guarded = _inject_helper_late_change(monkeypatch, target, bar, seen, late)

    def observe(
        _bar: object, output: Path, phase: str
    ) -> tuple[diagnostics._InverseSample, dict[str, object]]:
        seen.append((output.name, phase))
        value = _helper_fixture(425 if output.name == "reference" else 290)
        if output.name == "tiny" and failure == (
            "initial" if phase == "before" else "restore"
        ):
            value[0].image.setPixel(30, 60, 0xFF112233)
        return value

    monkeypatch.setattr(diagnostics, "_helper_observe", observe)
    outputs: list[str] = []

    def capture(
        _point: Point, output: Path, _mode: str, **kwargs: object
    ) -> ScrollResult:
        outputs.append(output.name)
        assert kwargs["window"] == 9
        assert kwargs["input_tick"] == 67
        assert kwargs["helper_factory"] is (
            None if output.name == "reference" else diagnostics.native.ScrollHelper
        )
        assert seen[-1] == (output.name, "before")
        cast("dict[str, object]", kwargs["lifecycle"])["start_to_finished_seconds"] = (
            30.0
        )
        _helper_fixture(425)[0].image.save(
            str(output / "frame-0.png")
        ) if output.name == "reference" else None
        image = _helper_fixture(
            1000 if failure != "height" or output.name == "reference" else 999
        )[0].image
        if failure == "result" and output.name == "tiny":
            image.setPixel(50, 500, 0xFF112233)
        return ScrollResult(
            None if failure == "helper" and output.name == "tiny" else image,
            "bounded failure",
            "restored",
            6,
        )

    monkeypatch.setattr(diagnostics.native, "_capture", capture)
    oracle = Mock(
        return_value={"oracle_frames": 6},
        side_effect=RuntimeError("Reference incomplete")
        if failure == "reference"
        else None,
    )
    monkeypatch.setattr(diagnostics, "verify_sequence", oracle)
    report: dict[str, object] = {}
    if failure is None:
        diagnostics._helper_output_case(owner, tmp_path, report)
        assert report["success"] is report["helper_output_passed"] is True
        assert report["result_height"] == 1000
        assert guarded[-3:] == ["guard", "position", "guard"]
    else:
        with pytest.raises((RuntimeError, CaptureError)):
            diagnostics._helper_output_case(owner, tmp_path, report)
        assert report["success"] is report["helper_output_passed"] is False
    assert report["product_capture_qualification"] is False
    assert "unverified" in cast("str", report["vertical_rail_raster_provenance"])
    stopped_at_reference = bool(late and late[1] == "reference")
    assert outputs == (
        ["reference"]
        if failure in ("initial", "reference") or stopped_at_reference
        else ["reference", "tiny"]
    )
    assert oracle.call_count == 1
    assert oracle.call_args.args[1] == tmp_path / "reference"
    assert resize.call_count == int(failure != "reference" and not stopped_at_reference)
    if late is not None:
        assert state["changed"]
        assert not QImage(str(tmp_path / late[1] / "result.png")).isNull()
        if late[2] == "position":
            assert guarded[-3:] == ["guard", "position", "guard"]
    if failure in ("result", "height", "restore"):
        assert not QImage(str(tmp_path / "tiny/result.png")).isNull()
    target.move.assert_not_called()


@pytest.mark.parametrize("plain", [False, True])
def test_capture_factory_preserves_default_and_finished_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, plain: bool
) -> None:
    """No process substitution is added to the ordinary explicit helper route."""
    helper = Mock(diagnostics="owned helper log")
    callbacks = []
    helper.finished.connect.side_effect = callbacks.append
    result = ScrollResult(None, "bounded failure", "restored", 6)
    loop = Mock()
    loop.exec.side_effect = lambda: callbacks[0](result)
    monkeypatch.setattr(diagnostics.native, "QEventLoop", Mock(return_value=loop))
    monkeypatch.setattr(diagnostics.native, "QTimer", Mock())
    monkeypatch.setattr(
        diagnostics.native.time, "monotonic", Mock(side_effect=[10.0, 12.5])
    )
    traced = Mock(return_value=helper)
    monkeypatch.setattr(diagnostics.native, "_traced_helper", traced)
    factory = Mock(return_value=helper)
    receipt: dict[str, object] = {}
    assert (
        diagnostics.native._capture(
            Point(10, 20),
            tmp_path,
            "explorer-helper-output",
            window=9,
            input_tick=67,
            helper_factory=factory if plain else None,
            lifecycle=receipt,
        )
        is result
    )
    assert receipt["start_to_finished_seconds"] == 2.5
    assert "not transaction" in cast("str", receipt["clock"])
    assert factory.call_count == int(plain)
    assert traced.call_count == int(not plain)
    helper.start.assert_called_once_with(Point(10, 20), window=9, input_tick=67)
    helper._process.start.assert_not_called()


def test_helper_output_routes_to_core_application_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owned observer stays on the QCoreApplication MTA thread, not a GUI."""
    runner = Mock(spec=diagnostics.ExplorerCase)
    monkeypatch.setattr(diagnostics, "ExplorerCase", Mock(return_value=runner))
    monkeypatch.setattr(
        diagnostics.sys, "argv", ["diagnostics", "explorer-helper-output"]
    )
    main = Mock()
    monkeypatch.setattr(diagnostics.native, "main", main)
    diagnostics.main()
    main.assert_called_once_with(gui=False, case=runner.run)


@pytest.mark.parametrize("failure", [None, "move", "bounds", "identity"])
def test_helper_resize_changes_only_height(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    """No refocus, shell retargeting, width change or second resize is allowed."""
    window = WindowInfo(9, "owned", Rect(10, 10, 1000, 1000))
    small = replace(window, bounds=Rect(10, 10, 1000, 760))
    owner = Mock(report={"explorer_layout": {"actual_outer": asdict(window.bounds)}})
    owner._verified_window.side_effect = [
        window,
        replace(small, handle=10) if failure == "identity" else small,
    ]
    user = Mock()
    user.MoveWindow.return_value = failure != "move"

    def rectangle(_handle: int, pointer: c.c_void_p) -> int:
        value = c.cast(pointer, c.POINTER(w.RECT)).contents
        value.left, value.top, value.right, value.bottom = 10, 10, 1010, 770
        if failure == "bounds":
            value.right += 1
        return 1

    user.GetWindowRect.side_effect = rectangle
    monkeypatch.setattr(c, "WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr(diagnostics.time, "sleep", Mock())
    if failure is None:
        assert diagnostics._helper_shrink(owner, window) == small
    else:
        with pytest.raises(RuntimeError):
            diagnostics._helper_shrink(owner, window)
    user.MoveWindow.assert_called_once_with(9, 10, 10, 1000, 760, 1)
    user.SetForegroundWindow.assert_not_called()


def test_plain_capture_keeps_real_module_command_and_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, qapp: object
) -> None:
    """Use the real owner without spawning any child or entering native UIA."""
    del qapp
    helper = diagnostics.native.ScrollHelper()
    start = Mock()
    monkeypatch.setattr(helper._process, "start", start)
    result = ScrollResult(None, "bounded failure", "restored", 0)
    loop = Mock()
    loop.exec.side_effect = lambda: helper._complete(result)
    monkeypatch.setattr(diagnostics.native, "QEventLoop", Mock(return_value=loop))
    traced = Mock(side_effect=AssertionError("Traced substitution entered"))
    monkeypatch.setattr(diagnostics.native, "_traced_helper", traced)
    receipt: dict[str, object] = {}
    assert (
        diagnostics.native._capture(
            Point(10, 20),
            tmp_path,
            "explorer-helper-output",
            window=9,
            input_tick=67,
            helper_factory=lambda: helper,
            lifecycle=receipt,
        )
        is result
    )
    assert helper._process.program() == diagnostics.sys.executable
    assert helper._process.arguments()[:6] == [
        "-m",
        "verdiclip.capture.scroll_helper",
        "10",
        "20",
        "9",
        "67",
    ]
    start.assert_called_once_with()
    traced.assert_not_called()
    assert helper._directory is None
    assert helper._terminal
    assert helper._deadline is not None
    assert 0 <= cast("float", receipt["start_to_finished_seconds"]) < 2


def test_helper_workflow_is_one_separate_200_percent_case() -> None:
    """The complete tiny traced stress still fails visibly in its original job."""
    source = Path(".github/workflows/simple-scroll-native.yml").read_text()
    original, representative = source.split("  representative-layout:", 1)
    assert "explorer-helper-output" not in original
    assert "Native 200% capture failed: $case" in original
    assert (
        representative.count(
            "python -m scripts.scrolling_diagnostics explorer-helper-output"
        )
        == 1
    )
    assert (
        "if: matrix.scale == 200 && matrix.application == 'explorer-click'"
        in representative
    )
    assert "product_capture_qualification -ne $false" in representative
    assert "continue-on-error" not in representative


@pytest.mark.parametrize("shutdown_fails", [False, True])
def test_capture_missing_terminal_retires_owner_without_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, shutdown_fails: bool
) -> None:
    """A synchronous shutdown completion cannot turn an absent result into success."""
    helper = Mock()
    callbacks = []
    helper.finished.connect.side_effect = callbacks.append

    def shutdown() -> None:
        if shutdown_fails:
            raise CaptureError("Owned child did not stop")
        callbacks[0](ScrollResult(None, "stopped", "unknown", 0))

    helper.shutdown.side_effect = shutdown
    monkeypatch.setattr(diagnostics.native, "QEventLoop", Mock())
    monkeypatch.setattr(diagnostics.native, "QTimer", Mock())
    expected = CaptureError if shutdown_fails else RuntimeError
    with pytest.raises(
        expected, match="did not stop" if shutdown_fails else "bounded terminal result"
    ):
        diagnostics.native._capture(
            Point(10, 20),
            tmp_path,
            "explorer-helper-output",
            window=9,
            input_tick=67,
            helper_factory=lambda: helper,
        )
    helper.shutdown.assert_called_once_with()
    helper.cancel.assert_not_called()
    assert not (tmp_path / "helper.log").exists()


@pytest.mark.parametrize(
    "kind", ["explorer-click", "explorer-enter", *diagnostics._EXPLORER_STARTS]
)
def test_no_hbar_option_preserves_case(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The additional fixture preserves all six existing gesture/start paths."""
    runner = Mock(spec=diagnostics.ExplorerCase)
    constructor = Mock(return_value=runner)
    monkeypatch.setattr(diagnostics, "ExplorerCase", constructor)
    monkeypatch.setattr(
        diagnostics.sys, "argv", ["diagnostics", kind, "--no-hbar-explorer"]
    )
    main = Mock()
    monkeypatch.setattr(diagnostics.native, "main", main)
    diagnostics.main()
    constructor.assert_called_once_with(no_hbar=True)
    main.assert_called_once_with(gui=True, case=runner.run)


def _no_hbar_sample() -> dict[str, object]:
    area = Rect(0, 0, 654, 425)
    nodes = [
        {
            "parent": -1,
            "control_type": 50008,
            "offscreen": False,
            "bounds": asdict(area),
        },
        {
            "parent": 0,
            "control_type": 50034,
            "offscreen": False,
            "bounds": asdict(Rect(0, 0, 620, 52)),
        },
        {
            "parent": 0,
            "control_type": 50014,
            "offscreen": False,
            "bounds": asdict(Rect(620, 0, 34, 425)),
        },
        *[
            {
                "parent": 0,
                "control_type": 50007,
                "offscreen": False,
                "bounds": asdict(Rect(0, top, 620, 30)),
            }
            for top in (60, 90)
        ],
    ]
    return {
        "captured_bounds": asdict(area),
        "position": asdict(ScrollPosition(-1, 0, 10)),
        "uia": {"nodes": nodes, "truncated": False},
    }


@pytest.mark.parametrize(
    "change", ["valid", "horizontal", "item", "header", "rail", "truncated"]
)
def test_no_hbar_requires_raw_geometry(change: str) -> None:
    """No clipped raw item, incomplete tree or guessed no-scroll sentinel passes."""
    geometry = _no_hbar_sample()
    tree = cast("dict[str, object]", geometry["uia"])
    nodes = cast("list[dict[str, object]]", tree["nodes"])
    if change == "horizontal":
        cast("dict[str, object]", geometry["position"])["horizontal"] = 0
    elif change == "item":
        cast("dict[str, object]", nodes[-1]["bounds"])["width"] = 621
    elif change == "header":
        cast("dict[str, object]", nodes[1]["bounds"])["width"] = 655
    elif change == "rail":
        cast("dict[str, object]", nodes[2]["bounds"])["height"] = 400
    elif change == "truncated":
        tree["truncated"] = True
    if change == "valid":
        assert (
            len(cast("list[object]", diagnostics._no_hbar_geometry(geometry)["items"]))
            == 2
        )
    else:
        with pytest.raises((CaptureError, RuntimeError)):
            diagnostics._no_hbar_geometry(geometry)


@pytest.mark.parametrize(
    ("native_calls", "exceptions"), [(0, 0), (1, 0), (2, 0), (3, 0), (2, 1)]
)
def test_no_hbar_capture_requires_actual_list_calls(
    tmp_path: Path, native_calls: int, exceptions: int
) -> None:
    """A successful generic capture cannot qualify the native List path."""
    geometry = _no_hbar_sample()
    (tmp_path / "header-geometry.json").write_text(json.dumps(geometry))
    (tmp_path / "target.json").write_text(
        json.dumps(
            {
                "initial_position": geometry["position"],
                "area": geometry["captured_bounds"],
            }
        )
    )
    (tmp_path / "list-calls.json").write_text(
        json.dumps(
            {
                name: {"count": native_calls, "exceptions": exceptions}
                for name in ("begin", "finish")
            }
        )
    )
    report: dict[str, object] = {}
    (tmp_path / "frames.json").write_text(json.dumps([{}, {}, {}]))
    if native_calls == 2 and exceptions == 0:
        diagnostics._no_hbar_target_layout(tmp_path, report)
        assert report["native_list_capability_used"] is True
    else:
        with pytest.raises(RuntimeError, match="native List"):
            diagnostics._no_hbar_target_layout(tmp_path, report)


@pytest.mark.parametrize(
    "failure", ["host", "identity", "missing_pid", "missing_folder", "command", "none"]
)
def test_no_hbar_navigation_is_owned_hosted_and_single_attempt(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Unsupported UI fails without repeated toggling or guessed shortcuts."""
    monkeypatch.setenv("GITHUB_ACTIONS", "false" if failure == "host" else "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("WINDIR", "C:/Windows")
    runner = diagnostics.ExplorerCase(no_hbar=True)
    runner.pid = None if failure == "missing_pid" else 123
    runner.folder = None if failure == "missing_folder" else Path("C:/fixture's folder")
    window = WindowInfo(9, "fixture", Rect(10, 10, 1900, 1000))
    runner.report["explorer_layout"] = {
        "actual_outer": asdict(window.bounds),
        "work_area": asdict(Rect(0, 0, 1920, 1080)),
    }
    monkeypatch.setattr(
        runner,
        "_verified_window",
        Mock(return_value=None if failure == "identity" else window),
    )
    command = Mock(
        return_value=Mock(stdout='{"navigation_pane_was_enabled": true}'),
        side_effect=RuntimeError("unsupported UI") if failure == "command" else None,
    )
    monkeypatch.setattr(diagnostics.subprocess, "run", command)
    if failure == "none":
        runner._hide_navigation_pane(window)
        assert runner.report["navigation_pane_setup"] == {
            "navigation_pane_was_enabled": True
        }
    else:
        with pytest.raises(RuntimeError):
            runner._hide_navigation_pane(window)
    assert command.call_count == int(failure in ("command", "none"))
    if command.called:
        assert command.call_args.kwargs["timeout"] == 10
        script = command.call_args.args[0][-1]
        assert "FromHandle([IntPtr]9)" in script
        assert "'View'" in script
        assert "'Show'" in script
        assert "'Navigation pane'" in script
        assert script.count("$toggle.Toggle()") == 1
        assert "finally {" in script
        assert "$show.GetCurrentPattern($pattern).Collapse()" not in script
        assert "InvokePattern" not in script
        assert "$currentView=Exact $root" in script
        assert "Owned View menu did not close" in script
        assert "$pidValue=$root.Current.ProcessId" not in script
        assert "$pidValue=[int]$expected.pid" in script
        assert '"pid": 123' in script
        assert "fixture''s folder" in script
        assert "SameRect $outer $expected.outer" in script
        assert "SameRect $work $expected.work" in script
        assert "if (-not (OwnedControl $visible[0]))" in script
        assert "AreDpiAwarenessContextsEqual($context, [IntPtr](-4))" in script
        assert script.index("CheckDpi\n") < script.index("$expected=")
        assert "function Guard($control=$null) {\n  CheckDpi" in script
        assert "$control.Current.ProcessId -ne $pidValue" in script
        assert "-not (OwnedControl $control)" in script
        for mutation, control in (
            ("$viewPattern.Expand()", "$view"),
            ("$showPattern.Expand()", "$show"),
            ("$toggle.Toggle()", "$pane"),
            ("$currentPattern.Collapse()", "$currentView"),
        ):
            assert (
                script[: script.index(mutation)].rstrip().endswith(f"Guard {control}")
            )
        assert "if ($before -eq [Windows.Automation.ToggleState]::On)" in script


@pytest.fixture
def owned_navigation_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock]:
    """Prepare only fake owned identities, never UI or a real subprocess."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("WINDIR", "C:/Windows")
    runner = diagnostics.ExplorerCase(no_hbar=True)
    runner.pid = 123
    runner.folder = Path("C:/owned fixture")
    window = WindowInfo(9, "fixture", Rect(10, 10, 1900, 1000))
    runner.report["explorer_layout"] = {
        "actual_outer": asdict(window.bounds),
        "work_area": asdict(Rect(0, 0, 1920, 1080)),
    }
    verify = Mock(return_value=window)
    monkeypatch.setattr(runner, "_verified_window", verify)
    command = Mock(return_value=Mock(stdout='{"navigation_pane_was_enabled": true}'))
    monkeypatch.setattr(diagnostics.subprocess, "run", command)
    return runner, window, command, verify


def test_no_hbar_setup_thread_dpi_guards_remain_fail_closed(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Generated script rejects null setup and initial/per-Guard DPI or thread drift."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    assert "SetProcessDpiAwarenessContext" not in script
    assert script.count("::SetThreadDpiAwarenessContext([IntPtr](-4))") == 1
    assert (
        "public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);"
        in script
    )
    assert (
        "if ($previousContext -eq [IntPtr]::Zero) {\n"
        "  throw 'Could not establish physical per-monitor thread DPI context'\n}"
    ) in script
    assert (
        "function CheckDpi {\n"
        "  if ([FixtureNative]::GetCurrentThreadId() -ne $setupThread) {\n"
        "    throw 'Physical per-monitor DPI thread changed'\n  }\n"
        "  $context=[FixtureNative]::GetThreadDpiAwarenessContext()\n"
        "  if (-not [FixtureNative]::AreDpiAwarenessContextsEqual($context, "
        "[IntPtr](-4))) {\n"
        "    throw 'Physical per-monitor DPI context changed'\n  }\n}"
    ) in script
    assert script.index("CheckDpi\n") < script.index(
        "Add-Type -AssemblyName UIAutomationClient"
    )
    assert script.index("$setupThread=") < script.index(
        "$previousContext=[FixtureNative]::SetThreadDpiAwarenessContext"
    )
    assert "function Guard($control=$null) {\n  CheckDpi" in script
    assert script.count("CheckDpi\n") == 2


@pytest.mark.parametrize("kind", ["exit", "timeout"])
@pytest.mark.parametrize(
    ("stream", "tail", "truncated"),
    [
        (None, "", False),
        ("retained output", "retained output", False),
        (b"retained output", "retained output", False),
        ("x" * 16384, "x" * 16384, False),
        (b"x" * 16384, "x" * 16384, False),
        ("x" * 16385, "x" * 16384, True),
        (b"x" * 16385, "x" * 16384, True),
        (b"invalid \xff bytes", "invalid \ufffd bytes", False),
        ("x" * 20000 + "end", "x" * 16381 + "end", True),
        (b"x" * 20000 + b"end", "x" * 16381 + "end", True),
        ("\u20ac" * 6000 + "end", "\u20ac" * 5460 + "end", True),
        (b"\xff" * 6000 + b"end", "\ufffd" * 5460 + "end", True),
    ],
    ids=(
        "missing",
        "text-short",
        "bytes-short",
        "text-at-limit",
        "bytes-at-limit",
        "text-over-limit",
        "bytes-over-limit",
        "invalid-utf8",
        "text-long-tail",
        "bytes-long-tail",
        "multibyte-tail",
        "invalid-bytes-tail",
    ),
)
def test_no_hbar_setup_preserves_bounded_failure_streams(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
    kind: str,
    stream: bytes | str | None,
    tail: str,
    *,
    truncated: bool,
) -> None:
    """Exit one and timeouts retain bytes/text tails without retry or substitution."""
    runner, window, command, verify = owned_navigation_setup
    error = (
        subprocess.CalledProcessError(1, ["not retained"], output=stream, stderr=stream)
        if kind == "exit"
        else subprocess.TimeoutExpired(
            ["not retained"], 10, output=stream, stderr=stream
        )
    )
    command.side_effect = error
    with pytest.raises(type(error)) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    command.assert_called_once()
    assert command.call_args.kwargs == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 10,
    }
    assert runner.report["navigation_pane_setup"] == {
        "status": "failed" if kind == "exit" else "timed_out",
        "returncode": 1 if kind == "exit" else None,
        "stdout_tail": tail,
        "stderr_tail": tail,
        "stdout_truncated": truncated,
        "stderr_truncated": truncated,
    }
    assert len(tail.encode("utf-8")) <= 16 * 1024
    verify.assert_called_once()


def test_setup_stream_failure_ids_stay_short_for_windows() -> None:
    """Windows limits the PYTEST_CURRENT_TEST environment value to 32,767 chars."""
    marks = getattr(
        test_no_hbar_setup_preserves_bounded_failure_streams, "pytestmark", ()
    )
    mark = next(item for item in marks if "ids" in item.kwargs)
    ids = mark.kwargs["ids"]
    assert len(ids) == len(mark.args[1]) == len(set(ids))
    assert all(isinstance(name, str) and 0 < len(name) < 64 for name in ids)


@pytest.mark.parametrize("kind", ["exit", "timeout"])
@pytest.mark.parametrize("failure", ["receipt", "metadata"])
def test_no_hbar_setup_receipt_failure_preserves_original_exception(
    monkeypatch: pytest.MonkeyPatch,
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
    failure: str,
    kind: str,
) -> None:
    """Optional receipt construction/storage failures cannot obscure the exit."""
    runner, window, command, _verify = owned_navigation_setup
    error = (
        subprocess.CalledProcessError(1, ["not retained"], stderr="original")
        if kind == "exit"
        else subprocess.TimeoutExpired(["not retained"], 10, stderr=b"original")
    )
    command.side_effect = error
    if failure == "receipt":
        report = Mock(wraps=runner.report)
        report.__getitem__ = Mock(side_effect=runner.report.__getitem__)
        report.__setitem__ = Mock(side_effect=OSError("receipt unavailable"))
        runner.report = report
    else:
        monkeypatch.setattr(
            diagnostics, "_setup_stream_tail", Mock(side_effect=ValueError("metadata"))
        )
    with pytest.raises(type(error)) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    command.assert_called_once()


def test_no_hbar_setup_keeps_stderr_stage_separate_from_stdout(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """The failing phase survives a timeout independently of ordinary output."""
    runner, window, command, _verify = owned_navigation_setup
    stderr = b"navigation_pane_setup_stage=show_lookup\n"
    error = subprocess.TimeoutExpired("not retained", 10, b"ordinary output", stderr)
    command.side_effect = error
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    receipt = cast("dict[str, object]", runner.report["navigation_pane_setup"])
    assert receipt["stderr_tail"] == stderr.decode()
    assert receipt["stdout_tail"] == "ordinary output"
    command.assert_called_once()


def test_no_hbar_setup_failure_stage_survives_menu_cleanup(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Cleanup phase progress cannot replace the original failed-stage marker."""
    runner, window, command, _verify = owned_navigation_setup
    stderr = (
        "navigation_pane_setup_stage=show_lookup\n"
        "navigation_pane_setup_failed_stage=show_lookup\n"
        "navigation_pane_setup_stage=menu_cleanup\n"
        "Expected one enabled visible Show control\n"
    )
    error = subprocess.CalledProcessError(1, "not retained", stderr=stderr)
    command.side_effect = error
    with pytest.raises(subprocess.CalledProcessError) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    receipt = cast("dict[str, object]", runner.report["navigation_pane_setup"])
    assert receipt["stderr_tail"] == stderr
    assert receipt["stderr_truncated"] is False
    script = command.call_args.args[0][-1]
    assert "function Stage($name) {\n  $script:setupStage=$name" in script
    assert (
        "} catch {\n  [Console]::Error.WriteLine("
        '"navigation_pane_setup_failed_stage=$script:setupStage")\n'
        "  throw\n} finally {\n  Stage 'menu_cleanup'"
    ) in script
    command.assert_called_once()


def test_no_hbar_setup_stages_use_only_fixed_stderr_labels(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Bounded phase labels accompany failed or timed-out existing setup calls."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    assert '[Console]::Error.WriteLine("navigation_pane_setup_stage=$name")' in script
    for stage, action in (
        ("native_bindings", "Add-Type -TypeDefinition"),
        ("dpi_setup", "$setupThread="),
        ("uia_shell_setup", "Add-Type -AssemblyName UIAutomationClient"),
        ("owner_guard", "Guard\nStage 'list_lookup'"),
        ("list_lookup", "$root=[Windows.Automation.AutomationElement]::FromHandle"),
        ("horizontal_readback", "GuardList\n$horizontal="),
        ("view_lookup", "$desktop=[Windows.Automation.AutomationElement]::RootElement"),
        ("view_expand", "Guard $view\n  $viewPattern.Expand()"),
        ("show_lookup", "$show=Exact"),
        ("show_expand", "Guard $show"),
        ("pane_lookup", "$pane=Exact"),
        ("pane_toggle", "Guard $pane"),
        ("menu_cleanup", "# Toggle may dismiss"),
        ("result", "@{ navigation_pane_was_enabled="),
    ):
        assert script.count(f"Stage '{stage}'") == 1
        assert script.index(f"Stage '{stage}'") < script.index(action)
    command.assert_called_once()


def test_no_hbar_success_retains_json_and_post_owner_verification(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """A zero exit is still rejected if the original owner changes afterward."""
    runner, window, command, verify = owned_navigation_setup
    verify.side_effect = [window, None]
    with pytest.raises(RuntimeError, match="Fixture changed during View/Show setup"):
        runner._hide_navigation_pane(window)
    assert runner.report["navigation_pane_setup"] == {
        "navigation_pane_was_enabled": True
    }
    command.assert_called_once()


def test_no_hbar_width_probe_uses_one_owned_list_at_the_planned_point(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """A source contract excludes desktop searches, alternate points and roles."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    expected = script.split("$expected='", 1)[1].split("' | ConvertFrom-Json", 1)[0]
    assert json.loads(expected)["point"] == asdict(window.bounds.center)
    probe = script.split("Stage 'list_lookup'", 1)[1].split("Stage 'view_lookup'", 1)[0]
    assert probe.count("$root.FindAll(") == 1
    assert "[Windows.Automation.TreeScope]::Descendants, $listCondition" in probe
    assert (
        "[Windows.Automation.AutomationElement]::ControlTypeProperty,\n"
        "    [Windows.Automation.ControlType]::List"
    ) in probe
    assert "IsScrollPatternAvailableProperty, $true" in probe
    assert "ProcessIdProperty, $pidValue" in probe
    assert "-not $_.Current.IsOffscreen -and $_.Current.IsEnabled" in probe
    assert (
        "(OwnedControl $_) -and $_.Current.BoundingRectangle.Contains(\n"
        "      [double]$expected.point.x, [double]$expected.point.y)"
    ) in probe
    assert (
        "if ($lists.Count -ne 1) { "
        "throw 'Expected one owned List at the planned point' }"
    ) in probe
    assert "$list=$lists[0]" in probe
    assert "$listBounds=$list.Current.BoundingRectangle" in probe
    assert probe.count(".GetCurrentPattern(") == 1
    assert "[Windows.Automation.ScrollPattern]::Pattern" in probe
    for forbidden in (
        "RootElement",
        "FromPoint",
        "FindFirst",
        "Start-Sleep",
        "while (",
    ):
        assert forbidden not in probe
    command.assert_called_once()


def test_no_hbar_width_probe_requires_stable_guarded_exact_horizontal_states(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Missing patterns, role/bounds/owner drift and unsupported H cannot pass."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    assert (
        "function GuardList {\n  Guard $list\n"
        "  if ($list.Current.ControlType -ne "
        "[Windows.Automation.ControlType]::List -or\n"
        "      -not $list.GetCurrentPropertyValue(\n"
        "        [Windows.Automation.AutomationElement]::"
        "IsScrollPatternAvailableProperty) -or\n"
        "      -not $list.Current.BoundingRectangle.Equals($listBounds) -or\n"
        "      -not $listBounds.Contains([double]$expected.point.x, "
        "[double]$expected.point.y)) {\n"
        "    throw 'Retained List role, pattern or raw geometry changed'\n  }\n}"
    ) in script
    assert (
        "GuardList\n$horizontal=$listPattern.Current.HorizontalScrollPercent\n"
        "$scrollable=$listPattern.Current.HorizontallyScrollable\nGuardList\n"
        "$confirmedHorizontal=$listPattern.Current.HorizontalScrollPercent\n"
        "$confirmedScrollable=$listPattern.Current.HorizontallyScrollable\nGuardList\n"
        "if ($horizontal -ne $confirmedHorizontal -or "
        "$scrollable -ne $confirmedScrollable -or\n"
        "    -not (($horizontal -eq -1 -and -not $scrollable) -or\n"
        "          ($horizontal -eq 0 -and $scrollable))) {\n"
        "  throw 'Unsupported or unstable owned List horizontal state'\n}"
    ) in script
    assert script.count(".Current.HorizontalScrollPercent") == 2
    assert script.count(".Current.HorizontallyScrollable") == 2
    command.assert_called_once()


def test_no_hbar_width_only_returns_before_every_menu_lookup_or_action(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Exact no-scroll state emits its measured receipt and bypasses all menus."""
    runner, window, command, verify = owned_navigation_setup
    receipt = {"width_already_no_hbar": True, "horizontal_before": -1}
    command.return_value.stdout = json.dumps(receipt)
    runner._hide_navigation_pane(window)
    assert runner.report["navigation_pane_setup"] == receipt
    script = command.call_args.args[0][-1]
    prefix, menus = script.split("Stage 'view_lookup'", 1)
    assert prefix.endswith(
        "if ($horizontal -eq -1) {\n  Stage 'width_already_no_hbar'\n"
        "  @{ width_already_no_hbar=$true; horizontal_before=$horizontal } |\n"
        "    ConvertTo-Json -Compress\n  return\n}\n"
    )
    for forbidden in (
        "$view=Exact",
        "$show=Exact",
        "$pane=Exact",
        "$currentView=Exact",
        "RootElement",
        ".Expand()",
        ".Toggle()",
        ".Collapse()",
    ):
        assert forbidden not in prefix
        assert forbidden in menus
    assert menus.count("$toggle.Toggle()") == 1
    assert "width_already_no_hbar=$false" in menus
    assert "horizontal_before=$horizontal" in menus
    command.assert_called_once()
    assert verify.call_count == 2


@pytest.mark.parametrize(
    ("stage", "failure"),
    [
        ("list_lookup", "Expected one owned List at the planned point"),
        ("horizontal_readback", "Retained List role, pattern or raw geometry changed"),
        (
            "horizontal_readback",
            "Retained menu control changed ownership or availability",
        ),
        ("horizontal_readback", "Unsupported or unstable owned List horizontal state"),
    ],
)
def test_no_hbar_width_probe_failures_keep_failure_receipt_without_retry(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
    stage: str,
    failure: str,
) -> None:
    """Rejected native observations remain setup errors, never width-only success."""
    runner, window, command, verify = owned_navigation_setup
    stderr = f"navigation_pane_setup_stage={stage}\n{failure}"
    error = subprocess.CalledProcessError(1, "not retained", stderr=stderr)
    command.side_effect = error
    with pytest.raises(subprocess.CalledProcessError) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    receipt = cast("dict[str, object]", runner.report["navigation_pane_setup"])
    assert receipt["status"] == "failed"
    assert receipt["stderr_tail"] == stderr
    assert "width_already_no_hbar" not in receipt
    command.assert_called_once()
    verify.assert_called_once()


def test_no_hbar_workflow_retains_original_cases_and_budgets() -> None:
    """No-hbar capture adds distinct jobs without replacing existing qualification."""
    source = Path(".github/workflows/simple-scroll-native.yml").read_text()
    _, representative = source.split("  representative-layout:", 1)
    assert "layout: [taller]" in representative
    assert (
        "name: Representative layout / ${{ matrix.scale }}% / ${{ matrix.application }}"
        "${{ matrix.layout == 'no-hbar' && ' / no-hbar' || '' }}"
    ) in representative
    assert (
        "name: scrolling-representative-${{ matrix.scale }}-${{ matrix.application }}"
        "${{ matrix.layout == 'no-hbar' && '-no-hbar' || '' }}"
    ) in representative
    assert "matrix.layout || 'taller'" not in representative
    for scale in (125, 200):
        for case in ("explorer-click", "explorer-enter", *diagnostics._EXPLORER_STARTS):
            assert (
                f"{{scale: {scale}, application: {case}, layout: no-hbar}}"
                in representative
            )
    assert "--no-hbar-explorer" in representative
    assert "--taller-explorer" in representative
    assert "timeout-minutes: 8" in representative
    assert "timeout-minutes: 2" in representative
    assert "continue-on-error" not in representative


def test_no_hbar_setup_never_opens_uia_on_gui_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing isolated helper owns geometry/UIA, not QApplication/OLE."""
    runner = diagnostics.ExplorerCase(no_hbar=True)
    window = WindowInfo(9, "fixture", Rect(10, 10, 1900, 1000))
    monkeypatch.setattr(runner, "_verified_window", Mock(return_value=window))
    prepare = Mock(return_value=window)
    hide = Mock()
    monkeypatch.setattr(runner, "_prepare_taller", prepare)
    monkeypatch.setattr(runner, "_hide_navigation_pane", hide)
    factory = Mock(side_effect=AssertionError("GUI-thread UIA construction"))
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", factory)
    assert runner._prepare(window) == window
    prepare.assert_called_once_with(window)
    hide.assert_called_once_with(window)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "glyph",
        "white_padding",
        "footprint",
        "island",
        "color",
        "colored_initial",
        "missing_output",
        "not_restored",
    ],
)
def test_prose_content_restoration_keeps_every_pixel_outside_original_footprint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    """Source-derived padding is not a strip mask or a coordinate whitelist."""
    first = QImage(80, 90, QImage.Format.Format_RGB32)
    first.fill(0xFFFFFFFF)
    for y in range(first.height()):
        first.setPixel(0, y, 0xFFEEEEEE)
    after = first.copy()
    after.setPixel(0, 20, 0xFFDDDDDD)
    paints = {
        "glyph": [(after, 40, 0xFF24292F)],
        "white_padding": [(after, 10, 0xFFDDDDDD)],
        "footprint": [(after, 0, 0xFFFFFFFF)],
        "island": [(first, 10, 0xFFDDDDDD), (after, 10, 0xFFDDDDDD)],
        "color": [(after, 0, 0xFFAABBCC)],
        "colored_initial": [(first, 10, 0xFFAABBCC), (after, 10, 0xFFAABBCC)],
    }
    for image, x, color in paints.get(mutation, []):
        image.setPixel(x, 20, color)
    capture = Mock(kind="edge-prose-enter", output=tmp_path, report={})
    capture.result = ScrollResult(first, "", "restored", 2)
    if mutation == "missing_output":
        capture.result = None
    elif mutation == "not_restored":
        capture.result = ScrollResult(first, "", "not restored", 2)
    context = {"window_dpi": 120, "initial_position": {"vertical": 0.0}}
    monkeypatch.setattr(
        diagnostics.native, "_fixture_context", lambda *_a, **_kw: context
    )
    oracle = Mock(return_value={"oracle_frames": 2})
    monkeypatch.setattr(diagnostics, "verify_sequence", oracle)
    if mutation == "none":
        diagnostics._verify_prose_content(capture, first, after)
        assert capture.report["document_content_restored"] is True
        assert capture.report["nonpaint_pixels"] == 90
        oracle.assert_not_called()  # Terminal delivery separately requires full output.
        assert capture.result is not None
        diagnostics._inspect_result(capture.result, tmp_path, capture.report)
        oracle.assert_called_once()
    else:
        with pytest.raises(RuntimeError):
            diagnostics._verify_prose_content(capture, first, after)
        assert "document_content_restored" not in capture.report


def test_prose_source_hash_pins_the_white_padding_contract() -> None:
    """A source/layout change must not silently broaden the semantic criterion."""
    digest = hashlib.sha256(
        diagnostics.document("edge-prose").encode("utf-8")
    ).hexdigest()
    for mode in ("edge-prose-click", "edge-prose-enter"):
        assert digest == diagnostics.native._CONTENT_FIXTURE_SOURCES[mode]


def test_view_receipt_is_optional_and_before_unchanged_show_lookup(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
    tmp_path: Path,
) -> None:
    """One read-only receipt does not change the one-toggle navigation sequence."""
    runner, window, command, _verify = owned_navigation_setup
    runner.setup_output = tmp_path / "case's output"
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    expected = script.split("$expected='", 1)[1].split("' | ConvertFrom-Json", 1)[0]
    assert json.loads(expected.replace("''", "'"))["evidence"] == str(
        runner.setup_output
    )
    assert (
        "  Guard $view\n  $viewPattern.Expand()\n"
        "  Start-Sleep -Milliseconds 300\n  ViewReceipt\n"
        "  Stage 'show_lookup'\n  $show=Exact $desktop 'Show' $pidValue"
    ) in script
    assert script.count("\n  ViewReceipt\n") == 1
    assert script.index("Stage 'width_already_no_hbar'") < script.index(
        "\n  ViewReceipt\n"
    )
    receipt = script.split("function ViewReceipt {", 1)[1].split(
        "Stage 'owner_guard'", 1
    )[0]
    for forbidden in (
        "Start-Sleep",
        "FindAll",
        "FindFirst",
        "FromPoint",
        "Exact ",
        ".Expand()",
        ".Collapse()",
        ".Toggle()",
        "Stage ",
    ):
        assert forbidden not in receipt
    assert "} catch {\n    # Fixed label only:" in receipt
    assert 'WriteLine("navigation_pane_view_receipt=$status")' in receipt
    assert script.count("$toggle.Toggle()") == 1
    assert script.count("Start-Sleep -Milliseconds 300") == 2
    assert command.call_args.kwargs["timeout"] == 10
    command.assert_called_once()


def test_view_receipt_bounds_names_traversal_and_owned_screenshot(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Static contracts bound diagnostic reads and keep all image ownership guards."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    receipt = script.split("function ViewReceipt {", 1)[1].split(
        "Stage 'owner_guard'", 1
    )[0]
    for contract in (
        "$roots -lt 64",
        "$visited -lt 64",
        "($visited + $queue.Count) -lt 64",
        "$depth -ge 4",
        "[Math]::Min(80, $name.Length)",
        "$info.ProcessId -eq $pidValue",
        "if (OwnedControl $top)",
        "$info.ProcessId -ne $pidValue -or -not (OwnedControl $element)",
        "$info.ControlType -eq [Windows.Automation.ControlType]::Menu -or",
        "$info.ControlType -eq [Windows.Automation.ControlType]::MenuItem",
        "same_pid_roots=$samePidRoots; unowned_roots=$unownedRoots",
        "root_owner=$owner",
        "bounds=$values; bounds_status=$boundsStatus",
        "truncated=($limited -or $queue.Count -gt 0)",
        "screenshot_scope='owned_window_only'; screenshot_bounds=$expected.outer",
        "$width -gt 4096",
        "$height -gt 2160",
        "($width * $height) -gt 8847360",
        "if ($null -ne $graphics) { $graphics.Dispose() }",
        "if ($null -ne $bitmap) { $bitmap.Dispose() }",
    ):
        assert contract in receipt
    assert receipt.count("$info.Name") == 1
    assert receipt.count("Guard $view") == 4
    assert receipt.count("::GetForegroundWindow()") == 2
    assert receipt.index("Guard $view") < receipt.index("GetFirstChild($desktop)")
    copy = receipt.index("$graphics.CopyFromScreen")
    save = receipt.index("$bitmap.Save")
    assert receipt.rindex("Guard $view", 0, copy) < copy
    assert copy < receipt.index("Guard $view", copy) < save
    assert "[int]$expected.outer.x, [int]$expected.outer.y" in receipt
    assert receipt.index("'view-popup.json'") < copy < save


def test_view_receipt_status_does_not_replace_original_setup_error(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Evidence statuses accompany the exact subprocess failure."""
    runner, window, command, _verify = owned_navigation_setup
    error = diagnostics.subprocess.CalledProcessError(
        1,
        "powershell",
        stderr=(
            "navigation_pane_view_receipt=uia_saved\n"
            "navigation_pane_setup_failed_stage=show_lookup\n"
            "Expected one enabled visible Show control"
        ),
    )
    command.side_effect = error
    with pytest.raises(diagnostics.subprocess.CalledProcessError) as raised:
        runner._hide_navigation_pane(window)
    assert raised.value is error
    result = cast("dict[str, object]", runner.report["navigation_pane_setup"])
    assert result["stderr_tail"] == error.stderr
    command.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "missing_footer",
        "duplicate_vertical",
        "footer_handle",
        "rail_handle",
        "footer_bounds",
        "header",
        "footer",
        "identity",
    ],
)
def test_helper_reader_retains_short_vertical_rail_among_two_native_bars(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Native percent mapping binds to V, never the full-width footer or corner."""
    position = ScrollPosition(0.0, 0.0, 10.0)
    geometry = _step_geometry(position)
    area, header, footer, rail = diagnostics.list_layout(geometry)
    vertical, horizontal = Mock(), Mock()
    for element, bounds, handle in ((vertical, rail, 73), (horizontal, footer, 74)):
        element.control_type.return_value = 50014
        element.offscreen.return_value = False
        element.bounds.return_value = bounds
        element.native_handle.return_value = handle
    target = Mock(
        area=area, header=header, footer=footer, vertical_scrollbar=rail, _window=9
    )
    children = [horizontal, vertical, None]
    if change == "missing_footer":
        children = [vertical, None]
    elif change == "duplicate_vertical":
        children = [vertical, vertical, None]
    elif change in ("footer_handle", "rail_handle"):
        (
            horizontal if change == "footer_handle" else vertical
        ).native_handle.return_value = 75
    elif change == "footer_bounds":
        horizontal.bounds.return_value = Rect(1, 391, 620, 34)
    target._walker.relative.side_effect = children
    target._walker.parent.return_value = target._element
    target._automation.pointer_result.return_value = vertical
    target._automation.same_element.return_value = True
    target._user.GetParent.return_value = 70
    target._user.GetAncestor.return_value = 9
    target.position.return_value = position
    monkeypatch.setattr(diagnostics, "probe_target", Mock(return_value=geometry))
    monkeypatch.setattr(diagnostics, "_window_pid", Mock(return_value=456))
    if change in (
        "missing_footer",
        "duplicate_vertical",
        "footer_handle",
        "rail_handle",
        "footer_bounds",
    ):
        with pytest.raises(RuntimeError, match="two-rail identity"):
            diagnostics._InverseScrollBar(target, 456, two_rails=True)
    else:
        bar = diagnostics._InverseScrollBar(target, 456, two_rails=True)
        assert bar.rail == Rect(620, 0, 34, 391)
        assert bar.footer == Rect(0, 391, 620, 34)
        assert bar.element is vertical
        assert bar.handle == 73
        if change in ("header", "footer"):
            setattr(
                target,
                change,
                {
                    "header": Rect(0, 0, 620, 53),
                    "footer": Rect(0, 392, 620, 33),
                }[change],
            )
        elif change == "identity":
            target._automation.same_element.return_value = False
        if change == "none":
            bar.check()
        else:
            with pytest.raises(RuntimeError):
                bar.check()
    target.move.assert_not_called()


def test_durable_workflow_reaches_main_release_and_keeps_stress_explicit() -> None:
    """Product qualification never depends on opting into the traced tiny stress."""
    workflow = Path(".github/workflows/simple-scroll-native.yml").read_text()
    for job in ("native", "diagnostic", "display-setup", "representative-layout"):
        section = workflow.split(f"  {job}:\n", 1)[1].split("    name:", 1)[0]
        assert "refs/heads/main" in section
        assert "release-please--branches--main--components--verdiclip" in section
    assert "${{ github.event.pull_request.head.sha }}" not in workflow
    assert "continue-on-error" not in workflow
    assert "_no_hbar_target_layout(p, {})" in workflow
    assert "['window_dpi'] == 96" in workflow
    stress = workflow.split(
        "      - name: Optional fully traced tiny Explorer stress", 1
    )[1].split("      - uses:", 1)[0]
    assert (
        "if: github.event_name == 'workflow_dispatch' "
        "&& github.ref == 'refs/heads/main'" in stress
    )
    assert 'throw "Optional traced tiny capture failed: $case"' in stress
    representative = workflow.split("  representative-layout:", 1)[1]
    assert "workflow_dispatch" not in representative
    assert "traced_tiny" not in workflow
    assert "Fresh-reference ordinary helper comparison failed" in representative


def test_view_receipt_nonfinite_offscreen_bounds_are_explicitly_unavailable(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Source contract forbids NaN/Infinity geometry in strict JSON receipts."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    receipt = script.split("function ViewReceipt {", 1)[1].split(
        "Stage 'owner_guard'", 1
    )[0]
    assert (
        "$values=@($bounds.X, $bounds.Y, $bounds.Width, $bounds.Height)\n"
        "      $boundsStatus='available'\n"
        "      foreach ($value in $values) {\n"
        "        if ([double]::IsNaN($value) -or [double]::IsInfinity($value)) {\n"
        "          $values=$null\n"
        "          $boundsStatus='unavailable_nonfinite'\n"
        "          break\n        }\n      }"
    ) in receipt
    node = receipt.split("[void]$nodes.Add(@{", 1)[1].split("})", 1)[0]
    assert "bounds=$values; bounds_status=$boundsStatus" in node
    assert "$bounds." not in node
    assert "offscreen=$info.IsOffscreen; enabled=$info.IsEnabled" in node
    assert "role=$info.ControlType.Id; name=$name" in node
    # Serialization happens only after every node receives the finite/null check.
    assert receipt.index("$values=$null") < receipt.index("[void]$nodes.Add")
    assert receipt.index("[void]$nodes.Add") < receipt.index("ConvertTo-Json")


def test_view_receipt_menu_focus_is_terminal_incomplete_and_same_budget(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """First-menu observation spends the existing budget, not a new lookup budget."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    receipt = script.split("function ViewReceipt {", 1)[1].split(
        "Stage 'owner_guard'", 1
    )[0]
    assert receipt.count("$visited=0") == 1
    assert receipt.count("$visited++") == 1
    assert "$roots -lt 64" in receipt
    assert "$visited -lt 64" in receipt
    assert "($visited + $queue.Count) -lt 64" in receipt
    terminal = "if ($entry.Count -eq 3 -and $entry[2]) { continue }"
    assert receipt.index("[void]$nodes.Add") < receipt.index(terminal)
    assert receipt.index(terminal) < receipt.index("$menuChildren=$false")
    assert (
        "if (-not $focusedMenu -and -not $info.IsOffscreen -and\n"
        "          $info.ControlType -eq [Windows.Automation.ControlType]::Menu) {\n"
        "        $focusedMenu=$true\n        $menuChildren=$true\n"
        "        $discardedQueued=$queue.Count\n        $queue.Clear()\n"
        "        $limited=$true\n      }"
    ) in receipt
    assert "$depth -ge 4 -and -not $menuChildren" in receipt
    assert "$queue.Enqueue(@($child, ($depth + 1), $menuChildren))" in receipt
    assert "scope='bounded_observation_not_unique'" in receipt
    assert "focused_menu_children=$focusedMenu" in receipt
    assert "discarded_queued=$discardedQueued" in receipt
    assert "truncated=($limited -or $queue.Count -gt 0)" in receipt


def test_show_failure_receipt_reuses_counts_without_lookup_or_property_reads(
    owned_navigation_setup: tuple[diagnostics.ExplorerCase, WindowInfo, Mock, Mock],
) -> None:
    """Both original rejection branches keep their exact matching and error text."""
    runner, window, command, _verify = owned_navigation_setup
    runner._hide_navigation_pane(window)
    script = command.call_args.args[0][-1]
    receipt = script.split("function ExactFailureReceipt", 1)[1].split(
        "function Exact(", 1
    )[0]
    exact = script.split("function Exact(", 1)[1].split("function ViewReceipt", 1)[0]
    for forbidden in (".Current", "FindAll", "FindFirst", "OwnedControl", "Guard "):
        assert forbidden not in receipt
    assert "if ($name -ne 'Show' -or -not $expected.evidence) { return }" in receipt
    assert (
        "name_filter='Show'; pid_filter=$pidValue; lookup_scope='Descendants'"
        in receipt
    )
    assert (
        "candidate_count=$candidateCount; enabled_visible_count=$visibleCount"
        in receipt
    )
    assert "filtered_out=($candidateCount - $visibleCount)" in receipt
    assert "filter='not IsOffscreen and IsEnabled'; failure=$reason" in receipt
    assert "} catch {\n    # Optional serialization cannot replace" in receipt
    assert (
        "$items=$root.FindAll([Windows.Automation.TreeScope]::Descendants, "
        "$condition)\n"
        "  $visible=@($items | Where-Object {\n"
        "    -not $_.Current.IsOffscreen -and $_.Current.IsEnabled })"
    ) in exact
    assert exact.count("FindAll") == 1
    assert exact.count("OwnedControl") == 1
    assert (
        "if ($visible.Count -ne 1) {\n"
        "    ExactFailureReceipt $name $items.Count $visible.Count 'count'\n"
        '    throw "Expected one enabled visible $name control"\n  }'
    ) in exact
    assert (
        "if (-not (OwnedControl $visible[0])) {\n"
        "    ExactFailureReceipt $name $items.Count $visible.Count 'ownership'\n"
        '    throw "The $name control does not belong to the retained fixture"\n  }'
    ) in exact
    assert "return $visible[0]" in exact


def test_manual_dispatch_rejects_nonmain_before_any_source_checkout() -> None:
    """Unsupported manual refs fail visibly before fetching or executing their code."""
    workflow = Path(".github/workflows/simple-scroll-native.yml").read_text()
    display = workflow.split("  display-setup:\n", 1)[1].split(
        "  representative-layout:", 1
    )[0]
    condition = display.split("    name:", 1)[0]
    assert "((github.event_name == 'workflow_dispatch') ||" in condition
    steps = display.split("    steps:\n", 1)[1]
    guard, remainder = steps.split("      - uses: actions/checkout@", 1)
    assert guard.startswith(
        "      - name: Reject manual dispatch outside main before checkout\n"
    )
    assert "if: github.event_name == 'workflow_dispatch'" in guard
    assert "$env:GITHUB_REF -ne 'refs/heads/main'" in guard
    assert "throw 'Manual scrolling diagnostics require main'" in guard
    assert "${{" not in guard
    assert "ref: ${{ github.event.pull_request.head.sha || github.sha }}" in remainder
    assert remainder.index("Require exact setup source") < remainder.index("setup-uv@")
    assert "$actual -ne $env:QUALIFICATION_HEAD" in remainder
