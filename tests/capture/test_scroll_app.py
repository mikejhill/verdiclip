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

from verdiclip.geometry import Point, Rect
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
