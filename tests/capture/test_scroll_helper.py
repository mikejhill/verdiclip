"""Real owned-process lifecycle tests using pipes, without native UIA or sockets."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtGui import QImage

from verdiclip.capture.scroll_helper import ScrollHelper, _watch_parent, main
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


_SUCCESS = """
import json, sys
from pathlib import Path
from PySide6.QtGui import QImage
image = QImage(10, 20, QImage.Format.Format_RGB32)
image.fill(0xff123456)
image.save(str(Path(sys.argv[1]) / 'capture.png'))
print(json.dumps({'success': True, 'message': '',
                  'restoration': 'restored', 'frames': 3}))
"""


def program(monkeypatch: pytest.MonkeyPatch, source: str) -> list[Path]:
    """Use a tiny owned subprocess in place of the Windows-only helper body."""
    start = QProcess.start
    directories: list[Path] = []

    def launch(process: QProcess) -> None:
        directory = process.arguments()[-1]
        directories.append(Path(directory))
        process.setArguments(["-c", source, directory])
        start(process)

    monkeypatch.setattr(QProcess, "start", launch)
    return directories


def test_one_result_and_temporary_image_cleanup(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real child exits before its image is delivered once and scratch removed."""
    directories = program(monkeypatch, _SUCCESS)
    helper = ScrollHelper()
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    with qtbot.waitSignal(helper.finished, timeout=5000):
        helper.start(Point(10, 20), window=45, input_tick=67)
    assert len(results) == 1
    assert results[0].image is not None
    assert results[0].image.pixelColor(0, 0).name() == "#123456"
    assert results[0].restoration == "restored"
    assert all(not path.exists() for path in directories)
    helper.cancel()
    assert len(results) == 1
    with pytest.raises(RuntimeError):
        helper.start(Point(10, 20), window=45, input_tick=67)


@pytest.mark.parametrize(
    "source", ["print('not json')", "raise SystemExit(3)", "print('{}')"]
)
def test_bad_or_crashed_helper_never_publishes(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    """Invalid child output is a terminal failure, never a partial image."""
    program(monkeypatch, source)
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000) as signal:
        helper.start(Point(10, 20), window=45, input_tick=67)
    result = signal.args[0]
    assert result.image is None
    assert result.message
    assert result.restoration.startswith("unknown")


def test_cancel_discards_success_already_in_flight(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation wins even if the process later returns a successful image."""
    program(monkeypatch, _SUCCESS)
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000) as signal:
        helper.start(Point(10, 20), window=45, input_tick=67)
        helper.cancel()
    assert signal.args[0].image is None
    assert "cancelled" in signal.args[0].message


def test_unresponsive_process_is_killed_before_cleanup(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked provider substitute cannot leave the parent waiting forever."""
    directories = program(monkeypatch, "import time; time.sleep(60)")
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000) as signal:
        helper.start(Point(10, 20), window=45, input_tick=67)
        assert helper._process.waitForStarted(1000)
        helper._timeout()
    assert signal.args[0].image is None
    assert "could not be restored" in signal.args[0].message
    assert helper._process.state() == QProcess.ProcessState.NotRunning
    assert all(not path.exists() for path in directories)


def test_parent_eof_exits_without_restoration(monkeypatch: pytest.MonkeyPatch) -> None:
    """EOF means ownership ended; it is not permission for another UIA write."""
    cancelled = threading.Event()
    monkeypatch.setattr("sys.stdin", io.StringIO("cancel\n"))

    def exit_process(code: int) -> None:
        assert code == 1
        raise SystemExit(code)

    monkeypatch.setattr("os._exit", exit_process)
    with pytest.raises(SystemExit):
        _watch_parent(cancelled)
    assert cancelled.is_set()


def test_repeated_cancel_does_not_extend_deadline(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated Escape cannot keep a blocked provider alive indefinitely."""
    program(monkeypatch, "import time; time.sleep(60)")
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000):
        helper.start(Point(10, 20), window=45, input_tick=67)
        assert helper._process.waitForStarted(1000)
        helper.cancel()
        helper._timer.setInterval(100)
        helper.cancel()
        assert helper._timer.interval() == 100


def test_cleanup_failure_still_delivers_terminal_result(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows file lock must not suppress the only terminal signal."""
    program(monkeypatch, _SUCCESS)
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000) as signal:
        helper.start(Point(10, 20), window=45, input_tick=67)
        assert helper._directory is not None
        directory = helper._directory
        cleanup = directory.cleanup

        def fail_cleanup() -> None:
            raise OSError("Locked temporary image")

        monkeypatch.setattr(directory, "cleanup", fail_cleanup)
    try:
        assert signal.args[0].image is not None
        assert "could not be removed" in signal.args[0].message
    finally:
        cleanup()


@pytest.mark.parametrize("failure", ["none", "discovery", "cleanup", "dpi", "save"])
def test_helper_entry_produces_one_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """Exercise real serialization/file output with native boundaries mocked."""
    user = Mock()
    user.SetProcessDpiAwarenessContext.return_value = failure != "dpi"
    monkeypatch.setattr("ctypes.WinDLL", Mock(return_value=user), raising=False)
    monkeypatch.setattr("sys.argv", ["helper", "10", "20", "45", "67", str(tmp_path)])
    thread = Mock()
    monkeypatch.setattr("verdiclip.capture.scroll_helper.threading.Thread", thread)
    target = MagicMock()
    if failure == "cleanup":
        target.__exit__.side_effect = CaptureError("Cleanup failed")
    constructor = Mock(return_value=target)
    if failure == "discovery":
        constructor.side_effect = CaptureError("No scroll pattern")
    monkeypatch.setattr("verdiclip.platform.scrolling.UiaScrollTarget", constructor)
    image = QImage(10, 20, QImage.Format.Format_RGB32)
    image.fill(0xFF123456)
    operation = Mock()
    operation.run.return_value = ScrollResult(image, "", "restored", 3)
    monkeypatch.setattr(
        "verdiclip.capture.scrolling.ScrollTransaction", Mock(return_value=operation)
    )
    if failure == "save":
        monkeypatch.setattr(QImage, "save", Mock(return_value=False))
    if failure in ("dpi", "save"):
        with pytest.raises(OSError, match="Could not"):
            main()
        assert capsys.readouterr().out == ""
        if failure == "dpi":
            constructor.assert_not_called()
            thread.assert_not_called()
        return
    main()
    constructor.assert_called_once_with(Point(10, 20), window=45, input_tick=67)
    value = json.loads(capsys.readouterr().out)
    assert value["success"] is (failure == "none")
    assert value["frames"] == (3 if failure == "none" else 0)
    if failure == "none":
        assert value["restoration"] == "restored"
        assert QImage(str(tmp_path / "capture.png")) == image
    elif failure == "discovery":
        assert value["message"] == "No scroll pattern"
        assert value["restoration"] == "unchanged: target discovery failed"
        operation.run.assert_not_called()
    else:
        assert value["message"] == "Cleanup failed"
        assert value["restoration"] == "unknown: provider cleanup failed"
        assert not (tmp_path / "capture.png").exists()
    thread.return_value.start.assert_called_once()


def test_failed_start_and_duplicate_terminal_callbacks_deliver_once() -> None:
    """A launch failure has one honest unchanged result, even with late signals."""
    helper = ScrollHelper()
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    helper.cancel()  # No process to cancel.
    helper._error(QProcess.ProcessError.FailedToStart)
    helper._exited(1, QProcess.ExitStatus.CrashExit)
    helper._complete(ScrollResult(None, "late", "unknown", 0))
    assert len(results) == 1
    assert results[0].restoration == "unchanged"
    assert results[0].message == "Could not start scrolling capture"
    with pytest.raises(ValueError, match="Missing capture directory"):
        helper._read_result()


@pytest.mark.parametrize("failure", ["oversized", "size", "decode"])
def test_invalid_image_result_never_publishes(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Check output and image bounds before decoding any child-owned pixels."""
    source = "print('x' * 8193)" if failure == "oversized" else _SUCCESS
    program(monkeypatch, source)
    if failure != "oversized":
        reader = Mock()
        reader.size.return_value.width.return_value = 0 if failure == "size" else 10
        reader.size.return_value.height.return_value = 20
        reader.read.return_value = QImage()
        monkeypatch.setattr(
            "verdiclip.capture.scroll_helper.QImageReader", lambda _path: reader
        )
    helper = ScrollHelper()
    with qtbot.waitSignal(helper.finished, timeout=5000) as signal:
        helper.start(Point(10, 20), window=45, input_tick=67)
    assert signal.args[0].image is None
    assert signal.args[0].restoration.startswith("unknown")
    assert (
        "Oversized" in signal.args[0].message
        if failure == "oversized"
        else "Invalid" in signal.args[0].message
    )


def test_launch_retains_the_selected_window_and_release_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary QProcess argv carries both expected values, not a new baseline."""
    helper = ScrollHelper()
    start = Mock()
    monkeypatch.setattr(helper._process, "start", start)
    helper.start(Point(10, 20), window=45, input_tick=67)
    assert helper._process.arguments()[:6] == [
        "-m",
        "verdiclip.capture.scroll_helper",
        "10",
        "20",
        "45",
        "67",
    ]
    start.assert_called_once()
    helper._complete(ScrollResult(None, "Not launched in this test", "unchanged", 0))
