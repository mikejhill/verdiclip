"""Ordinary-layout diagnostic inputs must not reuse the colored-row oracle."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import math
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


@pytest.mark.parametrize(
    "kind", [*diagnostics._EXPLORER_STARTS, *diagnostics._EXPERIMENTS]
)
def test_start_variants_keep_verified_explorer_launch_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Middle/bottom must not accidentally launch the native Notepad fallback."""
    runner = Mock(spec=diagnostics.ExplorerCase)
    monkeypatch.setattr(diagnostics, "ExplorerCase", Mock(return_value=runner))
    monkeypatch.setattr(diagnostics.sys, "argv", ["diagnostics", kind])

    def main(*, gui: bool, case: object) -> None:
        assert gui == (kind not in diagnostics._EXPERIMENTS)
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
@pytest.mark.parametrize("kind", ["explorer-middle-click", *diagnostics._EXPERIMENTS])
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
    monkeypatch.setattr(
        diagnostics,
        "_inverse_explorer_restore",
        lambda point, output, owner, setup: position(kind, point, output, owner, setup),
    )
    monkeypatch.setattr(
        diagnostics,
        "_explorer_step_geometry",
        lambda point, output, owner, setup: position(kind, point, output, owner, setup),
    )
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


@pytest.mark.parametrize("stable", [False, True])
def test_inverse_stage_needs_two_identical_state_and_pixel_samples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stable: bool
) -> None:
    """Stable percentages cannot qualify a changing native pixel snapshot."""
    bar = object.__new__(diagnostics._InverseScrollBar)
    samples = [_inverse_sample("final") for _ in range(8)]
    if not stable:
        for i, sample in enumerate(samples):
            sample.image.setPixel(1, 1, i)
    reader = Mock(side_effect=samples)
    monkeypatch.setattr(bar, "read", reader)
    monkeypatch.setattr(bar, "check", Mock())
    sleep = Mock()
    monkeypatch.setattr(diagnostics.time, "sleep", sleep)
    evidence: dict[str, object] = {}
    if stable:
        assert bar.settle("final", tmp_path, evidence) == samples[0]
        assert reader.call_count == 2
    else:
        with pytest.raises(RuntimeError, match="final did not settle"):
            bar.settle("final", tmp_path, evidence)
        assert reader.call_count == 8
    assert sleep.call_count == reader.call_count
    assert not QImage(str(tmp_path / "inverse-final.png")).isNull()


def _inverse_owner(
    monkeypatch: pytest.MonkeyPatch, original: diagnostics._InverseSample
) -> Mock:
    owner = Mock(spec=diagnostics.ExplorerCase)
    owner.pid = 456
    owner._verified_window.return_value = WindowInfo(9, "Owned", Rect(0, 0, 117, 80))

    def prepare(
        _kind: str,
        _point: Point,
        output: Path,
        _owner: object,
        setup: dict[str, object],
    ) -> None:
        setup.update(settled_position=asdict(original.position), setup_writes=1)
        assert original.image.save(str(output / "before.png"))

    monkeypatch.setattr(diagnostics, "_position_explorer_start", prepare)
    return owner


@pytest.mark.parametrize(
    "outcome",
    [
        "exact",
        "rail_only",
        "original_changed",
        "endpoint_changed",
        "no_page_change",
        "wrong_native",
        "wrong_native_up",
        "body_changed",
        "header_changed",
    ],
)
def test_inverse_experiment_makes_one_computed_restore_and_never_corrects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str
) -> None:
    """The fresh inverse is the only restore write, even if proof fails."""
    original, endpoint, final = (
        _inverse_sample(phase) for phase in ("original", "endpoint", "final")
    )
    if outcome in ("wrong_native", "wrong_native_up"):
        final.native["pos"] = 7105 if outcome == "wrong_native" else 7107
    elif outcome == "body_changed":
        final.image.setPixel(20, 20, 0xFF123456)
    elif outcome == "header_changed":
        final.image.setPixel(5, 5, 0xFF123456)
    elif outcome == "rail_only":
        final.image.setPixel(110, 20, 0xFF123456)
    elif outcome == "no_page_change":
        endpoint.native["page"] = original.native["page"]
    owner = _inverse_owner(monkeypatch, original)
    target, context = Mock(), Mock()
    context.__enter__, context.__exit__ = (
        Mock(return_value=target),
        Mock(return_value=False),
    )
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", Mock(return_value=context))
    bar = Mock(
        geometry={}, handle=73, header=Rect(0, 0, 100, 10), rail=Rect(100, 0, 17, 80)
    )
    bar.parent = 70
    bar.settle.side_effect = [original, endpoint, final]
    bar.read.side_effect = [
        final if outcome == "original_changed" else original,
        original if outcome == "endpoint_changed" else endpoint,
    ]
    monkeypatch.setattr(diagnostics, "_InverseScrollBar", Mock(return_value=bar))
    setup: dict[str, object] = {}
    if outcome in ("exact", "rail_only"):
        diagnostics._inverse_explorer_restore(Point(50, 50), tmp_path, owner, setup)
        assert setup["experiment_passed"] is True
    else:
        with pytest.raises(RuntimeError):
            diagnostics._inverse_explorer_restore(Point(50, 50), tmp_path, owner, setup)
        assert "experiment_passed" not in setup
    requested = [call.args[0] for call in target.move.call_args_list]
    expected = [100.0, 50.10223507015441]
    if outcome == "original_changed":
        expected = []
    elif outcome in ("endpoint_changed", "no_page_change"):
        expected = [100.0]
    assert requested == expected
    assert original.position.vertical not in requested
    assert setup["setup_writes"] == 1
    evidence = setup["inverse_restore"]
    assert isinstance(evidence, dict)
    assert evidence["endpoint_and_restore_write_requests"] == expected
    assert evidence["product_capture_qualification"] is False
    context.__exit__.assert_called_once()


def test_inverse_fixture_success_is_not_product_capture_qualification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The separate experiment cannot mark native product capture as passed."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    owner = diagnostics.ExplorerCase()
    owner.folder, owner.pid, owner.before = tmp_path / "fixture", 456, {8}
    window = WindowInfo(9, "Owned", Rect(0, 0, 117, 80))
    monkeypatch.setattr(owner, "_verified_window", Mock(return_value=window))
    monkeypatch.setattr(owner, "_prepare", Mock(return_value=window))
    gui = Mock()
    monkeypatch.setattr(diagnostics.app, "AppCapture", gui)

    def run(output: Path) -> None:
        request = json.loads((output / "setup-request.json").read_text())
        assert request["kind"] == "explorer-inverse-restore"
        assert request["fixture"]["previous_handles"] == [8]
        assert request["fixture"]["folder"] == str(owner.folder)
        assert request["pid"] == 456
        assert request["window"] == asdict(window)
        (output / "setup.json").write_text(
            '{"experiment_passed": true}', encoding="utf-8"
        )

    monkeypatch.setattr(diagnostics, "_run_explorer_setup", run)
    report: dict[str, object] = {"success": False}
    owner._inverse_case(tmp_path, report)
    assert report["success"] is False
    assert report["experiment_passed"] is True
    assert report["product_capture_qualification"] is False
    gui.assert_not_called()


@pytest.mark.parametrize("boundary", ["read", "save"])
@pytest.mark.parametrize("change", ["input", "foreground"])
def test_inverse_final_read_or_save_change_cannot_pass_or_trigger_another_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str, change: str
) -> None:
    """Input/focus changes at the second final read/save invalidate exact snapshots."""
    original, endpoint, final = (
        _inverse_sample(phase) for phase in ("original", "endpoint", "final")
    )
    owner = _inverse_owner(monkeypatch, original)
    bar = _inverse_read_bar(monkeypatch)
    target = cast("Mock", bar.target)
    bar.header, bar.rail = Rect(0, 0, 100, 10), Rect(100, 0, 17, 80)
    bar.geometry, bar.parent = {}, 70
    target.position.return_value = final.position
    target.frame.return_value = final.image
    context = Mock()
    context.__enter__, context.__exit__ = (
        Mock(return_value=target),
        Mock(return_value=False),
    )
    monkeypatch.setattr(diagnostics, "UiaScrollTarget", Mock(return_value=context))
    actual_read = bar.read
    state = {"native_reads": 0, "final_saves": 0, "changed": False}

    def check() -> None:
        if state["changed"]:
            raise CaptureError(f"Stopped because {change} changed")

    monkeypatch.setattr(bar, "check", Mock(side_effect=check))
    target.check.side_effect = check

    def read() -> diagnostics._InverseSample:
        if target.move.call_count < 2:
            return original if target.move.call_count == 0 else endpoint
        return actual_read()

    monkeypatch.setattr(bar, "read", read)

    def native_state(*_args: object) -> dict[str, object]:
        state["native_reads"] += 1
        if boundary == "read" and state["native_reads"] == 4:
            state["changed"] = True
        return {
            **final.native,
            "success": True,
            "window_valid": True,
            "root_matches": True,
        }

    monkeypatch.setattr(diagnostics, "_scroll_info", native_state)
    original_save = QImage.save

    def save(image: QImage, filename: str) -> bool:
        result = original_save(image, filename)
        if filename.endswith("inverse-final.png"):
            state["final_saves"] += 1
            if boundary == "save" and state["final_saves"] == 2:
                state["changed"] = True
        return result

    monkeypatch.setattr(QImage, "save", save)
    monkeypatch.setattr(diagnostics.time, "sleep", Mock())
    monkeypatch.setattr(diagnostics, "_InverseScrollBar", Mock(return_value=bar))
    setup: dict[str, object] = {}
    with pytest.raises(CaptureError, match=f"{change} changed"):
        diagnostics._inverse_explorer_restore(Point(50, 50), tmp_path, owner, setup)
    assert state["changed"] is True
    assert "experiment_passed" not in setup
    assert [call.args[0] for call in target.move.call_args_list] == [
        100.0,
        50.10223507015441,
    ]
    context.__exit__.assert_called_once()


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
def test_focused_start_matches_actual_post_setup_crop_and_exact_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid: str,
) -> None:
    """The diagnostic cannot claim focus from a different crop, state or DPI."""
    capture = _focused_capture(tmp_path)
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
        "window_dpi": 96 if invalid == "dpi" else 192,
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


def test_focused_native_cases_run_at_200_percent_before_explorer() -> None:
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
    assert cases.index("'edge-panel-focused-enter'") < cases.index("'explorer-click'")
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


def _step_image(offset: int) -> QImage:
    """Each body row is unique, with fixed Header/footer and a mutable short rail."""
    image = QImage(654, 425, QImage.Format.Format_RGB32)
    image.fill(0xFF334455)
    painter = QPainter(image)
    for y in range(339):
        painter.fillRect(0, 52 + y, 620, 1, 0xFF000000 | ((offset + y) * 127))
    painter.fillRect(620, 0, 34, 391, 0xFF112200 | (offset % 256))
    painter.end()
    return image


def _step_samples() -> list[diagnostics._InverseSample]:
    samples = []
    for index, position in enumerate((0, 298, 596, 15400, 0)):
        page = 597 if index < 3 else 600
        state = {"min": 0, "max": 15999, "page": page, "pos": position}
        samples.append(
            diagnostics._InverseSample(
                ScrollPosition(
                    0.0,
                    diagnostics._native_inverse_percent(state, position),
                    100 * page / 15999,
                ),
                state,
                _step_image((0, 175, 350, 9000, 0)[index]),
            )
        )
    return samples


def _step_items(index: int) -> dict[str, object]:
    body = Rect(0, 52, 620, 339)
    items = []
    for y in (252, 308):
        first, last = Rect(28, y, 1350, 48), Rect(28, y - 175, 1350, 48)
        items.append(
            {
                "before": asdict(first),
                "after": asdict(last),
                "visible_before": asdict(first.intersected(body)),
                "visible_after": asdict(last.intersected(body)),
                "same_identity": True,
                "same_parent": True,
            }
        )
    return {"from_frame": index, "items": items}


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


class _GeometryRun:
    """Simulate the OS boundary while executing real bounded experiment control."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, output: Path) -> None:
        self.output = output
        self.samples = _step_samples()
        self.target = Mock(registration_dpi=192)
        self.bar = object.__new__(diagnostics._InverseScrollBar)
        self.bar.target = self.target
        self.bar.area, self.bar.header = Rect(0, 0, 654, 425), Rect(0, 0, 620, 52)
        self.bar.footer, self.bar.rail = Rect(0, 391, 620, 34), Rect(620, 0, 34, 391)
        self.bar.geometry = _step_geometry(self.samples[0].position)
        self.owner = Mock(spec=diagnostics.ExplorerCase)
        self.owner.pid = 456
        self.owner.window = WindowInfo(9, "Owned", Rect(0, 0, 654, 425))
        self.owner._verified_window.return_value = self.owner.window
        self.reader = Mock(
            side_effect=lambda: self.samples[self.target.move.call_count]
        )
        monkeypatch.setattr(self.bar, "read", self.reader)
        monkeypatch.setattr(self.bar, "check", Mock(side_effect=self.target.check))
        monkeypatch.setattr(diagnostics.time, "sleep", Mock())
        monkeypatch.setattr(
            diagnostics,
            "probe_target",
            lambda _target: _step_geometry(
                self.samples[self.target.move.call_count].position
            ),
        )
        context = Mock()
        context.__enter__, context.__exit__ = Mock(return_value=self.target), Mock()
        context.__exit__.return_value = False
        monkeypatch.setattr(diagnostics, "UiaScrollTarget", Mock(return_value=context))
        self.constructor = Mock(return_value=self.bar)
        monkeypatch.setattr(diagnostics, "_InverseScrollBar", self.constructor)
        self.witness, self.endpoint = Mock(), Mock()
        self.witness.observe.side_effect = [
            {"items": []},
            _step_items(0),
            _step_items(1),
        ]
        monkeypatch.setattr(
            diagnostics,
            "ListItemWitness",
            Mock(side_effect=[self.witness, self.endpoint]),
        )
        monkeypatch.setattr(diagnostics, "_geometry_pair", Mock(return_value=[{}, {}]))
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
        self.setup: dict[str, object] = {}

    def run(self) -> None:
        diagnostics._explorer_step_geometry(
            Point(50, 100), self.output, self.owner, self.setup
        )

    def evidence(self) -> dict[str, object]:
        return cast("dict[str, object]", self.setup["step_geometry"])


def test_step_geometry_four_writes_leave_prediction_mismatch_as_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Independent 175px raw/PNG movement survives a 169px UIA prediction."""
    run = _GeometryRun(monkeypatch, tmp_path)
    run.run()
    assert run.setup["experiment_passed"] is True
    assert run.setup["success"] is False
    assert run.setup["product_capture_qualification"] is False
    requests = [call.args[0] for call in run.target.move.call_args_list]
    step = (
        50
        * run.samples[0].position.view_size
        / (100 - run.samples[0].position.view_size)
    )
    assert requests == [step, run.samples[1].position.vertical + step, 100.0, 0.0]
    assert run.target.move.call_count == 4
    evidence = run.evidence()
    for index in (1, 2):
        proof = cast("dict[str, object]", evidence[f"step-{index}_overlap"])
        assert proof["raw_displacements"] == [175, 175]
        assert proof["png_overlap_exact"] is True
        assert proof["png_overlap_rows"] == 164
        assert cast("float", proof["raw_minus_uia_pixels"]) > 5
    assert evidence["endpoint_item_continuity"] is False
    assert evidence["endpoint_raw_list_items"]
    run.endpoint._seed.assert_called_once()
    run.endpoint.observe.assert_not_called()
    run.witness.close.assert_called_once()
    run.endpoint.close.assert_called_once()
    run.constructor.assert_called_once_with(run.target, 456, two_rails=True)
    for phase in ("original", "step-1", "step-2", "endpoint", "restore"):
        observations = cast("list[object]", evidence[f"geometry-{phase}_observations"])
        assert len(observations) == 2
        image = QImage(str(tmp_path / f"inverse-geometry-{phase}.png"))
        assert (image.width(), image.height()) == (654, 425)


@pytest.mark.parametrize("boundary", [0, 1, 2, 3, 4])
@pytest.mark.parametrize(
    "failure", ["read", "write", "input", "focus", "identity", "unstable"]
)
def test_step_geometry_uncertainty_stops_all_later_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    boundary: int,
    failure: str,
) -> None:
    """Any failed write/sample or guard ends the run, including any restoration."""
    run = _GeometryRun(monkeypatch, tmp_path)
    if failure == "write" and boundary == 0:
        return  # There is no original-view write.
    ticks = 0

    def read() -> diagnostics._InverseSample:
        nonlocal ticks
        index = int(run.target.move.call_count)
        if index == boundary and failure in ("read", "input", "focus"):
            raise CaptureError(f"{failure} changed")
        sample = run.samples[index]
        if index == boundary and failure == "unstable":
            ticks += 1
            image = sample.image.copy()
            image.setPixel(1, 1, ticks)
            return replace(sample, image=image)
        return sample

    run.reader.side_effect = read

    def write(_percent: float) -> None:
        if failure == "write" and run.target.move.call_count == boundary:
            raise CaptureError("write failed")

    run.target.move.side_effect = write

    def owned() -> WindowInfo:
        if failure == "identity" and run.target.move.call_count == boundary:
            raise CaptureError("identity changed")
        return cast("WindowInfo", run.owner.window)

    run.owner._verified_window.side_effect = owned
    with pytest.raises((CaptureError, RuntimeError)):
        run.run()
    assert run.target.move.call_count == boundary
    assert run.setup["experiment_passed"] is False
    assert run.setup["success"] is False
    assert run.setup["product_capture_qualification"] is False
    if boundary:
        writes = cast("list[dict[str, object]]", run.evidence()["write_requests"])
        assert len(writes) == boundary
        assert writes[-1]["outcome"] == "unresolved"


def _change_step_geometry(
    monkeypatch: pytest.MonkeyPatch, run: _GeometryRun, failure: str, writes: int
) -> None:
    def geometry(_target: object) -> dict[str, object]:
        result = _step_geometry(run.samples[run.target.move.call_count].position)
        if run.target.move.call_count == writes:
            if failure == "dpi":
                result["window_dpi"] = 96
            else:
                tree = cast("dict[str, object]", result["uia"])
                nodes = cast("list[dict[str, object]]", tree["nodes"])
                node = nodes[1 if failure == "header" else 3]
                bounds = cast("dict[str, float]", node["bounds"])
                bounds["height"] += 1
        return result

    monkeypatch.setattr(diagnostics, "probe_target", geometry)


@pytest.mark.parametrize(
    ("failure", "writes"),
    [
        ("dpi", 0),
        ("registration_dpi", 0),
        ("horizontal", 0),
        ("start", 0),
        ("header", 1),
        ("footer", 1),
        ("page", 1),
        ("movement", 1),
        ("witness", 1),
        ("overlap", 1),
        ("endpoint", 3),
        ("restore_pos", 4),
        ("restore_page", 4),
        ("restore_header", 4),
        ("restore_body", 4),
        ("restore_footer", 4),
        ("restore_corner", 4),
    ],
)
def test_step_geometry_rejects_invalid_evidence_without_a_corrective_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    writes: int,
) -> None:
    """Full footer/corner, raw witnesses and native position are never waived."""
    run = _GeometryRun(monkeypatch, tmp_path)
    if failure in ("dpi", "header", "footer"):
        _change_step_geometry(monkeypatch, run, failure, writes)
    elif failure == "registration_dpi":
        run.target.registration_dpi = 96
    elif failure == "horizontal":
        run.samples[0] = replace(
            run.samples[0], position=replace(run.samples[0].position, horizontal=1.0)
        )
    elif failure == "start":
        run.samples[0].native["pos"] = 1
    elif failure in ("page", "movement", "endpoint"):
        index = 3 if failure == "endpoint" else 1
        position = run.samples[index].position
        position = replace(
            position,
            **(
                {"view_size": position.view_size + 0.01}
                if failure == "page"
                else {"vertical": 0.0}
            ),
        )
        run.samples[index] = replace(run.samples[index], position=position)
    elif failure == "witness":
        run.witness.observe.side_effect = [
            {"items": []},
            {"items": [], "from_frame": 0},
        ]
    elif failure == "overlap":
        run.samples[1].image.setPixel(10, 70, 0xFF123456)
    elif failure in ("restore_pos", "restore_page"):
        run.samples[4].native["pos" if failure == "restore_pos" else "page"] += 1
    else:
        x, y = {
            "restore_header": (3, 4),
            "restore_body": (10, 70),
            "restore_footer": (10, 400),
            "restore_corner": (640, 400),
        }[failure]
        run.samples[4].image.setPixel(x, y, 0xFF123456)
    with pytest.raises((RuntimeError, CaptureError)):
        run.run()
    assert run.target.move.call_count == writes
    assert run.setup["experiment_passed"] is False
    assert run.setup["success"] is False
    if failure == "overlap":
        assert (
            cast("dict[str, object]", run.evidence()["step-1_overlap"])[
                "png_overlap_exact"
            ]
            is False
        )


@pytest.mark.parametrize(
    "phase", ["original", "step-1", "step-2", "endpoint", "restore"]
)
def test_step_geometry_late_input_after_second_png_save_stops_further_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    phase: str,
) -> None:
    """A valid second snapshot cannot hide input arriving while it is saved."""
    run = _GeometryRun(monkeypatch, tmp_path)
    save = QImage.save
    count = 0

    def changed_save(image: QImage, path: str) -> bool:
        nonlocal count
        result = save(image, path)
        if path.endswith(f"inverse-geometry-{phase}.png"):
            count += 1
            if count == 2:
                run.target.check.side_effect = CaptureError("late input")
        return result

    monkeypatch.setattr(QImage, "save", changed_save)
    with pytest.raises(CaptureError, match="late input"):
        run.run()
    assert count == 2
    assert run.target.move.call_count == (
        "original",
        "step-1",
        "step-2",
        "endpoint",
        "restore",
    ).index(phase)
    assert run.setup["experiment_passed"] is False


@pytest.mark.parametrize(
    "failure",
    [
        "identity",
        "parent",
        "delta",
        "size",
        "visible",
        "duplicate",
        "fractional",
        "offscreen",
    ],
)
def test_step_geometry_forged_visible_or_raw_witnesses_cannot_validate_overlap(
    failure: str,
) -> None:
    """Clipped hit bounds never replace raw displacements or pair identity."""
    first, second = _step_samples()[:2]
    items = _step_items(0)
    pair = cast("list[dict[str, object]]", items["items"])
    item = pair[0]
    if failure in ("identity", "parent"):
        item[f"same_{failure}"] = False
    elif failure == "duplicate":
        pair[1] = pair[0]
    else:
        key, field, value = {
            "delta": ("after", "y", 78),
            "size": ("after", "width", 1300),
            "visible": ("visible_after", "width", 591),
            "fractional": ("after", "y", 77.5),
            "offscreen": ("after", "y", 0),
        }[failure]
        cast("dict[str, object]", item[key])[field] = value
    with pytest.raises(RuntimeError):
        diagnostics._geometry_step(
            Rect(0, 52, 620, 339),
            replace(first, image=first.image.copy(0, 52, 620, 339)),
            replace(second, image=second.image.copy(0, 52, 620, 339)),
            items,
        )


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
def test_step_geometry_retains_short_vertical_rail_among_two_native_bars(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """Native percent mapping binds to V, never the full-width footer or corner."""
    sample = _step_samples()[0]
    geometry = _step_geometry(sample.position)
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
    target.position.return_value = sample.position
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


@pytest.mark.parametrize("passed", [False, True])
def test_step_geometry_report_never_claims_product_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    passed: bool,
) -> None:
    """Even a successful experiment is separate from complete production capture."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    owner = diagnostics.ExplorerCase()
    owner.folder, owner.pid, owner.before = tmp_path / "fixture", 456, {8}
    window = WindowInfo(9, "Owned", Rect(0, 0, 654, 425))
    monkeypatch.setattr(owner, "_verified_window", Mock(return_value=window))
    monkeypatch.setattr(owner, "_prepare", Mock(return_value=window))

    def run(output: Path) -> None:
        request = json.loads((output / "setup-request.json").read_text())
        assert request["kind"] == diagnostics._GEOMETRY_EXPERIMENT
        (output / "setup.json").write_text(
            json.dumps(
                {
                    "experiment_passed": passed,
                    "success": False,
                    "step_geometry": {"write_requests": [{"outcome": "unresolved"}]},
                }
            ),
            encoding="utf-8",
        )
        if not passed:
            raise RuntimeError("partial experiment")

    monkeypatch.setattr(diagnostics, "_run_explorer_setup", run)
    report: dict[str, object] = {"success": True}
    if passed:
        owner._inverse_case(tmp_path, report, kind=diagnostics._GEOMETRY_EXPERIMENT)
    else:
        with pytest.raises(RuntimeError, match="partial experiment"):
            owner._inverse_case(tmp_path, report, kind=diagnostics._GEOMETRY_EXPERIMENT)
    assert report["success"] is False
    assert report["product_capture_qualification"] is False
    assert report["experiment_passed"] is passed
    assert cast("dict[str, object]", report["step_geometry_experiment"])[
        "step_geometry"
    ]


@pytest.mark.parametrize("requested", [math.nan, math.inf, -1.0, 101.0, 1.0])
def test_step_geometry_write_bound_cannot_be_exceeded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    requested: float,
) -> None:
    """There is no fifth-write or non-finite corrective path."""
    run = _GeometryRun(monkeypatch, tmp_path)
    experiment = diagnostics._StepGeometry(run.owner, run.bar, tmp_path, {})
    if requested == 1.0:
        experiment.writes.extend([{}] * 4)
    with pytest.raises(RuntimeError, match="out of bounds"):
        experiment.move("forbidden", requested, run.samples[0])
    run.target.move.assert_not_called()


@pytest.mark.parametrize("changed", ["none", "bounds", "visible", "pair"])
def test_step_geometry_pair_records_raw_overflow_and_exact_hit_intersection(
    changed: str,
) -> None:
    """Endpoint and original pairs expose raw geometry, without displacement claims."""
    witness = Mock()
    bounds = [Rect(28, y, 1350, 48) for y in (252, 308)]
    witness._anchors = [Mock(bounds=rect) for rect in bounds]
    witness._visible.side_effect = bounds
    witness._visible_bounds.side_effect = [
        rect.intersected(Rect(0, 52, 620, 339)) for rect in bounds
    ]
    if changed == "bounds":
        witness._visible.side_effect = [Rect(28, 251, 1350, 48), bounds[1]]
    elif changed == "visible":
        witness._visible_bounds.side_effect = [None]
    elif changed == "pair":
        witness._anchors = witness._anchors[:1]
    if changed == "none":
        result = diagnostics._geometry_pair(witness)
        assert len(result) == 2
        assert result[0] == {
            "raw": asdict(bounds[0]),
            "visible": asdict(Rect(28, 252, 592, 48)),
        }
        assert witness._confirm.call_count == 2
    else:
        with pytest.raises(RuntimeError):
            diagnostics._geometry_pair(witness)
