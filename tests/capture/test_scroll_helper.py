"""Real owned-process lifecycle tests using pipes, without native UIA or sockets."""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QProcess

from verdiclip.capture.scroll_helper import ScrollHelper, _watch_parent
from verdiclip.capture.scrolling import ScrollResult
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
        helper.start(Point(10, 20))
    assert len(results) == 1
    assert results[0].image is not None
    assert results[0].image.pixelColor(0, 0).name() == "#123456"
    assert results[0].restoration == "restored"
    assert all(not path.exists() for path in directories)
    helper.cancel()
    assert len(results) == 1
    with pytest.raises(RuntimeError):
        helper.start(Point(10, 20))


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
        helper.start(Point(10, 20))
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
        helper.start(Point(10, 20))
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
        helper.start(Point(10, 20))
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
        helper.start(Point(10, 20))
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
        helper.start(Point(10, 20))
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
