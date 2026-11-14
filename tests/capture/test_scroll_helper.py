"""The helper process owner, driven by a scripted child process."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from verdiclip.capture import scroll_helper
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point


def run(  # noqa: PLR0913 - scripted child options
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    *,
    cancel: bool = False,
    stop: bool = False,
    from_top: bool = True,
) -> ScrollResult:
    """Run."""
    monkeypatch.setattr(scroll_helper, "CHILD_MODULE", "tests.capture.scroll_child")
    monkeypatch.setenv("SCROLL_CHILD", mode)
    helper = ScrollHelper()
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    helper.start(Point(5.4, 6.6), from_top=from_top)
    if stop:
        helper.stop()
        helper.stop()  # A second request changes nothing
    if cancel:
        helper.cancel()
        helper.cancel()  # A second request changes nothing
    qtbot.waitUntil(lambda: bool(results), timeout=20_000)
    assert len(results) == 1
    return results[0]


def test_success_reads_the_image_and_warning(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Success reads the image and warning."""
    result = run(qtbot, monkeypatch, "success")
    assert result.image is not None
    assert result.image.height() == 30
    assert result.warning == "note"
    assert result.frames == 3


def test_failure_message_is_passed_on(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure message is passed on."""
    result = run(qtbot, monkeypatch, "failure")
    assert result.image is None
    assert result.message == "no"


def test_crash_and_garbage_are_failures(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash and garbage are failures."""
    assert run(qtbot, monkeypatch, "crash").message == (
        "Scrolling capture stopped unexpectedly"
    )
    assert run(qtbot, monkeypatch, "garbage").image is None


def test_cancel_drops_the_image(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancel drops the image."""
    result = run(qtbot, monkeypatch, "wait-cancel", cancel=True)
    assert result.image is None
    assert result.message == "Scrolling capture cancelled"


def test_unresponsive_child_is_killed_after_cancel(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unresponsive child is killed after cancel."""
    monkeypatch.setattr(scroll_helper, "_CANCEL_GRACE_MS", 200)
    result = run(qtbot, monkeypatch, "hang", cancel=True)
    assert result.message == "Scrolling capture took too long"


def test_shutdown_kills_and_reports_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shutdown kills and reports once."""
    monkeypatch.setattr(scroll_helper, "CHILD_MODULE", "tests.capture.scroll_child")
    monkeypatch.setenv("SCROLL_CHILD", "hang")
    helper = ScrollHelper()
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    helper.start(Point(1, 1))
    helper.shutdown()
    helper.shutdown()
    assert [r.message for r in results] == ["Scrolling capture cancelled"]
    with pytest.raises(RuntimeError):
        helper.start(Point(1, 1))


def test_invalid_result_is_a_failure(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A result missing fields is refused; the child's stderr is logged."""
    with caplog.at_level(logging.INFO, logger=scroll_helper.__name__):
        result = run(qtbot, monkeypatch, "invalid")
    assert result.message == "Invalid scrolling result"
    assert "diagnostic line" in caplog.text


def test_oversized_image_is_refused(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An image above the pixel limit is not loaded."""
    monkeypatch.setattr(scroll_helper, "MAX_PIXELS", 100)
    result = run(qtbot, monkeypatch, "success")
    assert result.image is None
    assert result.message == "Invalid scrolling image size"


def test_missing_interpreter_reports_failure_to_start(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A child that cannot start is reported, once."""
    monkeypatch.setattr(sys, "executable", str(tmp_path / "missing"))
    result = run(qtbot, monkeypatch, "success")
    assert result.message == "Could not start scrolling capture"


def test_parent_watch_flags_commands_and_reports_eof() -> None:
    """``cancel`` and ``stop`` set their flags; the end of input calls ``on_eof``."""
    events = {"cancel": threading.Event(), "stop": threading.Event()}
    ends: list[bool] = []
    scroll_helper._watch_parent(
        events, iter(["noise\n", "stop\n"]), lambda: ends.append(True)
    )
    assert events["stop"].is_set()
    assert not events["cancel"].is_set()
    assert ends == [True]


def test_stop_keeps_the_image(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop asks the child to end early and still reads its image."""
    result = run(qtbot, monkeypatch, "wait-stop", stop=True)
    assert result.image is not None
    assert result.warning == "stopped"


def test_capture_from_here_is_passed_to_the_child(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Starting from the current view reaches the child as ``here``."""
    result = run(qtbot, monkeypatch, "success", from_top=False)
    assert result.warning == "note from here"


def test_cancel_after_stop_drops_the_image(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel after a stop still discards what was captured."""
    result = run(qtbot, monkeypatch, "wait-cancel", cancel=True, stop=True)
    assert result.image is None
