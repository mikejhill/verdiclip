"""Ordinary-layout diagnostic inputs must not reuse the colored-row oracle."""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from scripts import scrolling_diagnostics as diagnostics

from verdiclip.capture.scrolling import ScrollPosition, ScrollResult
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
        assert gui
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
def test_setup_child_rebuilds_ownership_without_constructing_gui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fails: bool
) -> None:
    """The process entry installs EOF retirement and retains even partial evidence."""
    request = {
        "kind": "explorer-middle-click",
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


@pytest.mark.parametrize("invalid", ["none", "writes", "state", "pixels", "missing"])
def test_bottom_rejection_records_unchanged_evidence_but_remains_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid: str
) -> None:
    """Even proven zero-write/no-pixel-change rejection must leave the job red."""
    capture = _start_capture(tmp_path, "explorer-bottom-enter")
    _start_evidence(capture)
    if invalid == "writes":
        (tmp_path / "movement.json").write_text(
            '{"writes": 1, "last_vertical": 0.0}', encoding="utf-8"
        )
    elif invalid == "state":
        capture.start_position = ScrollPosition(-1.0, 50.0, 12.0)
    elif invalid == "pixels":
        assert _image(0xFFFFFFFF).save(str(tmp_path / "after.png"))
    elif invalid == "missing":
        (tmp_path / "before.png").unlink()
    base = Mock(side_effect=RuntimeError("Ordinary product failure"))
    monkeypatch.setattr(diagnostics.app.AppCapture, "_terminal", base)
    result = ScrollResult(None, "Ordinary product failure", "not needed", 0)
    expected = "Ordinary product failure" if invalid == "none" else "Rejected Explorer"
    with pytest.raises(RuntimeError, match=expected):
        capture._terminal(result)
    assert "success" not in capture.report
    evidence = capture.report["rejected_start_evidence"]
    assert isinstance(evidence, dict)
    assert evidence == {
        "zero_production_writes": invalid != "writes",
        "unchanged_state": invalid != "state",
        "unchanged_pixels": invalid not in ("pixels", "missing"),
    }
    base.assert_called_once_with(result)


@pytest.mark.parametrize("kind", list(diagnostics._EXPLORER_STARTS))
def test_start_terminal_keeps_actual_helper_validation_and_full_row_oracle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
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
    if kind.startswith("explorer-middle-"):
        result = ScrollResult(_image(), "", "restored", 16)
        capture._terminal(result)
        oracle.assert_called_once_with(result.image, tmp_path)
        assert capture.report["oracle_frames"] == 16
        assert capture.report["restored_pixels"] == "exact"
        assert QImage(str(tmp_path / "result.png")) == result.image
    else:
        result = ScrollResult(None, "Ordinary bottom rejection", "not needed", 0)
        with pytest.raises(RuntimeError, match="Ordinary bottom rejection"):
            capture._terminal(result)
        assert capture.report["message"] == "Ordinary bottom rejection"
        assert capture.report["rejected_start_evidence"] == {
            "zero_production_writes": True,
            "unchanged_state": True,
            "unchanged_pixels": True,
        }
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
