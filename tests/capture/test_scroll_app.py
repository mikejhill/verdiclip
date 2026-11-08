"""Small boundary checks for the actual-app native integration harness."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot
from scripts.simple_scroll_app import AppCapture
from scripts.simple_scroll_native import _traced_helper

from verdiclip.capture.models import ScreenGeometry
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import ScrollSelectionState
from verdiclip.platform.windows import WindowInfo


def test_same_traced_child_forwards_original_arguments_and_one_result(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The reused diagnostic changes no child count, transport or retained inputs."""
    start = QProcess.start
    calls: list[list[str]] = []

    def launch(process: QProcess) -> None:
        arguments = process.arguments()
        calls.append(arguments)
        script = (
            "import json,sys; from pathlib import Path;"
            "Path(sys.argv[-2], 'argv.json').write_text(json.dumps(sys.argv[1:]));"
            "print(json.dumps({'success':False,'message':'native substitute',"
            "'restoration':'unchanged','frames':0}))"
        )
        process.setArguments(["-c", script, *arguments[2:]])
        start(process)

    monkeypatch.setattr(QProcess, "start", launch)
    helper = _traced_helper(tmp_path, "edge-app-tray")
    results: list[object] = []
    helper.finished.connect(results.append)
    with qtbot.waitSignal(helper.finished, timeout=5000):
        helper.start(Point(12, 34), window=56, input_tick=78)
    assert len(calls) == len(results) == 1
    assert calls[0][:2] == [
        "-c",
        "from scripts.simple_scroll_native import _trace_helper; _trace_helper()",
    ]
    forwarded = json.loads((tmp_path / "argv.json").read_text())
    assert forwarded[:4] == ["12", "34", "56", "78"]
    assert forwarded[-2:] == [str(tmp_path.resolve()), "edge-app-tray"]
    assert not Path(forwarded[4]).exists()
    helper.shutdown()


@pytest.mark.parametrize("boundary", ["valid", "foreign", "not_hosted"])
def test_native_shortcut_input_is_restricted_and_releases_modifiers(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Real key injection belongs only to the disposable, verified target desktop."""
    case = object.__new__(AppCapture)
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 200, 100))
    case.desktop = Mock()
    case.desktop.foreground.return_value = 999 if boundary == "foreign" else 123
    monkeypatch.setenv(
        "GITHUB_ACTIONS", "false" if boundary == "not_hosted" else "true"
    )
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    if boundary == "valid":
        case._keys((0x11, 0x12, 0x78))
        assert [call.args for call in case.desktop.user.keybd_event.call_args_list] == [
            (0x11, 0, 0, 0),
            (0x12, 0, 0, 0),
            (0x78, 0, 0, 0),
            (0x78, 0, 2, 0),
            (0x12, 0, 2, 0),
            (0x11, 0, 2, 0),
        ]
    else:
        with pytest.raises(RuntimeError):
            case._keys((0x11, 0x12, 0x78))
        case.desktop.user.keybd_event.assert_not_called()


def test_native_repeat_waits_for_admission_but_rejects_tall_dimensions() -> None:
    """A queued ordinary capture is awaited; a tall Repeat is never accepted."""
    case = object.__new__(AppCapture)
    case.kind = "edge-app-tray"
    case.controller = Mock()
    case.controller.workspace.documents = [Mock(), Mock()]
    case.original = Mock()
    case.original.session.document.image = QImage(200, 100, QImage.Format.Format_RGB32)
    case.editor = Mock()
    case.editor.session.document.image = QImage(200, 1200, QImage.Format.Format_RGB32)
    case.report = {}
    case.loop = Mock()
    case.repeat_area = Rect(0, 0, 200, 100)
    case._verify_repeat()
    assert not case.report
    case.loop.quit.assert_not_called()
    case.controller.workspace.documents.append(Mock())
    with pytest.raises(RuntimeError, match="tall composite"):
        case._verify_repeat()
    assert not case.report


@pytest.mark.parametrize("boundary", ["valid", "no_selector", "dynamic_repeat"])
def test_native_seed_requires_real_chooser_and_fixed_area(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """The fixture seeds a selected window, not dynamic active-window Repeat."""
    case = object.__new__(AppCapture)
    case.controller = Mock()
    case.desktop = Mock()
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 200, 100))
    case.point = Point(50, 50)
    case.report = {}
    overlay = Mock()
    overlay.screen_geometry.physical = case.window.bounds
    case.controller.capture.overlays = [] if boundary == "no_selector" else [overlay]
    case.controller.capture._last_area = (
        None if boundary == "dynamic_repeat" else case.window.bounds
    )
    case.controller.workspace.documents = [Mock()]
    case.controller.editors = [Mock()]
    monkeypatch.setattr("scripts.simple_scroll_app.QTest.qWait", Mock())
    if boundary == "valid":
        case._seed_editor()
        case.desktop.click.assert_called_once_with(overlay, case.point)
        assert case.report["initial_repeat_area"] == {
            "x": 0,
            "y": 0,
            "width": 200,
            "height": 100,
        }
    else:
        with pytest.raises(RuntimeError, match=r"chooser|retain"):
            case._seed_editor()
        assert not case.report
    case.controller.tray.action.assert_called_once_with("region")


@pytest.mark.parametrize("boundary", ["settled", "native_foreign", "qt_active"])
def test_native_setup_requires_observed_qt_and_native_foreground(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """A menu must not open ahead of the prior application's queued deactivate."""
    case = object.__new__(AppCapture)
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 200, 100))
    case.desktop = Mock()
    case.desktop.foreground.return_value = 456 if boundary == "native_foreign" else 123
    case.report = {}
    events = Mock()
    monkeypatch.setattr("scripts.simple_scroll_app.QApplication.processEvents", events)
    monkeypatch.setattr("scripts.simple_scroll_app.QTest.qWait", Mock())
    monkeypatch.setattr(
        "scripts.simple_scroll_app.QApplication.applicationState",
        lambda: (
            Qt.ApplicationState.ApplicationActive
            if boundary == "qt_active"
            else Qt.ApplicationState.ApplicationInactive
        ),
    )
    if boundary == "settled":
        case._settle_target_foreground()
        assert case.report == {
            "setup_qt_inactive": True,
            "setup_foreground": 123,
            "setup_qt_state": "ApplicationInactive",
        }
        events.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="did not settle"):
            case._settle_target_foreground()
        assert "setup_qt_inactive" not in case.report
        assert case.report["setup_foreground"] == case.desktop.foreground.return_value
        assert events.call_count == 100
    case.desktop.user.SetForegroundWindow.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        "edge-app-tray",
        "edge-app-hotkey",
        "edge-prose-click",
        "edge-prose-enter",
        "edge-background-click",
        "edge-background-enter",
    ],
)
def test_native_chooser_uses_one_realistic_held_gesture(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Hosted click and Enter paths process events between press and release."""
    case = object.__new__(AppCapture)
    case.kind = kind
    case.controller = Mock()
    case.desktop = Mock()
    case.point = Point(50, 50)
    case.repeating = case.selected = case.interrupted = False
    overlay = Mock()
    overlay.screen_geometry.physical = Rect(0, 0, 200, 100)
    case.controller._scrolling.overlays = [overlay]
    order: list[str] = []
    trace = Mock(side_effect=lambda: order.append("trace"))
    case.desktop.click.side_effect = lambda *_args, **_kwargs: order.append("gesture")
    monkeypatch.setattr(case, "_trace_background_activation", trace)
    case._observe()
    case._observe()
    case.desktop.click.assert_called_once_with(
        overlay,
        case.point,
        enter=kind == "edge-app-hotkey" or kind.endswith("-enter"),
        hold_ms=120,
    )
    assert trace.call_count == int(kind.startswith("edge-background-"))
    assert order == (
        ["trace", "gesture"] if kind.startswith("edge-background-") else ["gesture"]
    )


@pytest.mark.parametrize("scale", [1, 2])
@pytest.mark.parametrize("boundary", ["valid", "foreground", "point", "chrome"])
def test_background_setup_requires_owned_focus_and_original_exposed_edge_point(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scale: int,
    boundary: str,
) -> None:
    """Real owned Qt UI is positioned without UIA prewarming or target writes."""
    del qtbot
    case = object.__new__(AppCapture)
    case.kind = "edge-background-click"
    case.window = WindowInfo(123, "Owned Edge", Rect(20, 30, 600, 500))
    case.point = case.window.bounds.center
    case.output, case.report = tmp_path, {}
    case._background = None
    case._background_bounds = None
    desktop = Mock()
    case.desktop = desktop
    source = Mock()
    case.source = source
    desktop.foreground.side_effect = lambda: (
        999
        if boundary == "foreground" or case._background is None
        else int(case._background.winId())
    )
    desktop.user.GetAncestor.return_value = 999 if boundary == "point" else 123
    native = Mock()
    native.frame_bounds.return_value = Rect(
        20 + 16 * scale,
        250 if boundary == "chrome" else 30 + 8 * scale,
        140 * scale,
        16 * scale,
    )
    geometry = ScreenGeometry(
        "test", Rect(0, 0, 800, 600), Rect(0, 0, 800 * scale, 600 * scale), scale
    )
    monkeypatch.setattr(ScreenGeometry, "from_screen", lambda _screen: geometry)
    monkeypatch.setattr("scripts.simple_scroll_app.Win32WindowApi", lambda: native)
    monkeypatch.setattr("scripts.simple_scroll_app.QTest.qWait", Mock())
    try:
        if boundary == "valid":
            case._prepare_background_selection()
            widget = case._background
            assert widget is not None
            assert (widget.x(), widget.y()) == (
                round(20 / scale + 16),
                round(30 / scale + 8),
            )
            assert (widget.width(), widget.height()) == (140, 16)
            assert case.report["background_hwnd"] == int(widget.winId())
            assert case.report["background_foreground"] == int(widget.winId())
            assert case.report["background_selected_window"] == 123
            assert not case._background_bounds.contains(case.point)
            source.freeze.return_value.image.save.assert_called_once_with(
                str(tmp_path / "background-entry.png")
            )
        else:
            with pytest.raises(RuntimeError):
                case._prepare_background_selection()
            source.freeze.assert_not_called()
        desktop.user.SetForegroundWindow.assert_not_called()
    finally:
        if case._background is not None:
            case._background.close()


def test_ordinary_native_cases_do_not_create_background_setup() -> None:
    """Existing cases preserve their foreground setup and create no extra UI."""
    case = object.__new__(AppCapture)
    case.kind = "edge-app-tray"
    case._prepare_background_selection()
    assert not hasattr(case, "_background")


@pytest.mark.parametrize("requested", [False, True])
@pytest.mark.parametrize("writes", [[], [123], [123, 123], [999]])
def test_background_helper_requires_production_activation_and_exact_target(
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    writes: list[int],
    *,
    requested: bool,
) -> None:
    """Natural foreground return alone cannot pass the native background gate."""
    del qtbot
    case = object.__new__(AppCapture)
    case.kind = "edge-background-enter"
    case.output, case.report = tmp_path, {}
    case.point = Point(50, 50)
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 200, 100))
    case.controller = Mock()
    case.controller._scrolling._choice = (case.point, case.window, 456)
    case.controller._scrolling.overlays = []
    case.controller._scrolling._activation_requested = requested
    case.controller._scrolling._foreground = 789
    case._activation_writes = writes
    case.desktop = Mock()
    case.desktop.foreground.return_value = 123
    launch = Mock()
    monkeypatch.setattr("scripts.simple_scroll_app._traced_helper", launch)
    if requested and writes == [123]:
        assert case._helper() is launch.return_value
        launch.assert_called_once_with(tmp_path, case.kind)
        assert case.report["selected_window"] == 123
        assert case.report["release_input_tick"] == 456
        assert case.report["saved_prechooser_foreground"] == 789
        assert case.report["handoff_activation_requested"] is True
    else:
        with pytest.raises(
            RuntimeError, match=r"production activation|native activation"
        ):
            case._helper()
        launch.assert_not_called()
    case.desktop.user.SetForegroundWindow.assert_not_called()


def test_background_widget_cannot_overlap_the_captured_panel(tmp_path: Path) -> None:
    """The native completion gate uses discovered UIA bounds, not assumed chrome."""
    case = object.__new__(AppCapture)
    case.output = tmp_path
    case.report = {"release_input_tick": 456}
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 600, 500))
    case.helper = Mock(diagnostics="owned helper")
    case.desktop = Mock()
    case._background_bounds = Rect(20, 15, 140, 16)
    (tmp_path / "target.json").write_text(
        json.dumps(
            {
                "window": 123,
                "input_tick": 456,
                "area": {"x": 0, "y": 20, "width": 500, "height": 400},
            }
        )
    )
    (tmp_path / "movement.json").write_text("{}")
    with pytest.raises(RuntimeError, match="overlapped the captured panel"):
        case._terminal(ScrollResult(None, "no image", "unchanged", 0))


def test_background_setup_failure_closes_owned_extra_widget(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even an early background qualification failure retires its owned window."""
    del qtbot
    case = object.__new__(AppCapture)
    case.kind = "edge-background-click"
    case.window = WindowInfo(123, "Owned Edge", Rect(0, 0, 600, 500))
    case.report = {}
    case.controller = Mock()
    case.controller.workspace.documents = []
    case.controller.editors = []
    case.hotkeys = Mock(registered={"scrolling": Mock()})
    case.loop = Mock()
    case.timer = Mock()
    case.source = Mock()
    widget = Mock()
    case._background = widget
    restore = Mock()
    case._restore_activation = restore
    monkeypatch.setattr("scripts.simple_scroll_app._prepare", lambda window: window)
    monkeypatch.setattr(case, "_seed_editor", Mock())
    monkeypatch.setattr(case, "_settle_target_foreground", Mock())
    monkeypatch.setattr(
        case,
        "_prepare_background_selection",
        Mock(side_effect=RuntimeError("Owned setup failed")),
    )
    with pytest.raises(RuntimeError, match="Owned setup failed"):
        case.run()
    widget.close.assert_called_once()
    restore.assert_called_once()
    assert case._restore_activation is None
    assert case._background is None
    case.controller.shutdown.assert_called_once()
    case.source.close.assert_called_once()


@pytest.mark.parametrize("outcome", ["accepted", "refused", "error"])
def test_background_activation_trace_forwards_the_real_function_and_restores_it(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Tracing records exact calls while preserving native returns and errors."""
    case = object.__new__(AppCapture)
    case.kind = "edge-background-click"
    case._activation_writes = []
    case._restore_activation = None
    case.report = {}
    case.controller = Mock()
    native = object.__new__(ScrollSelectionState)
    user = Mock()
    native._user = user
    original = Mock(return_value=0 if outcome == "refused" else 1)
    if outcome == "error":
        original.side_effect = OSError("Native failure")
    user.SetForegroundWindow = original
    case.controller._scrolling._state = native
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    case._trace_background_activation()
    assert user.SetForegroundWindow is not original
    try:
        if outcome == "error":
            with pytest.raises(OSError, match="Native failure"):
                user.SetForegroundWindow(123)
        else:
            assert user.SetForegroundWindow(123) == original.return_value
        original.assert_called_once_with(123)
        assert case.report == {
            "background_activation_writes": [123],
            "background_activation_write_count": 1,
        }
        assert case._activation_writes == [123]
    finally:
        assert case._restore_activation is not None
        case._restore_activation()
    assert user.SetForegroundWindow is original


@pytest.mark.parametrize("boundary", ["ordinary", "not_hosted", "not_native"])
def test_background_activation_trace_is_limited_to_hosted_native_adapter(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """The pass-through tracer never applies to other cases or another adapter."""
    case = object.__new__(AppCapture)
    case.kind = "edge-app-tray" if boundary == "ordinary" else "edge-background-enter"
    case._activation_writes = []
    case._restore_activation = None
    case.report = {}
    case.controller = Mock()
    state = Mock()
    case.controller._scrolling._state = state
    original = state._user.SetForegroundWindow
    monkeypatch.setenv(
        "GITHUB_ACTIONS", "false" if boundary == "not_hosted" else "true"
    )
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    if boundary == "ordinary":
        case._trace_background_activation()
    elif boundary == "not_hosted":
        with pytest.raises(RuntimeError, match="disposable hosted runner"):
            case._trace_background_activation()
    else:
        with pytest.raises(TypeError, match="native selection adapter"):
            case._trace_background_activation()
    assert state._user.SetForegroundWindow is original
    original.assert_not_called()
    assert case._restore_activation is None
    assert not case.report
