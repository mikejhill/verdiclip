"""Owner clock tests: no child process, IPC, or native capture is started."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from verdiclip.capture import scroll_helper as module
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import MAX_SECONDS, ScrollResult
from verdiclip.geometry import Point


@pytest.fixture
def owner(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> tuple[ScrollHelper, list[float]]:
    """Keep Qt signals real while replacing every process boundary."""
    del qtbot
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    helper = ScrollHelper()
    monkeypatch.setattr(helper._process, "start", Mock())
    monkeypatch.setattr(helper._process, "kill", Mock())
    monkeypatch.setattr(helper._process, "write", Mock())
    helper.start(Point(0, 0), window=1, input_tick=2)
    return helper, clock


def success() -> ScrollResult:
    """Return an in-memory decoded image."""
    return ScrollResult(QImage(2, 2, QImage.Format.Format_RGB32), "", "restored", 2)


@pytest.mark.parametrize("remaining", [10.0, 0.5, 0.0, -1.0])
def test_cancel_only_shortens(
    owner: tuple[ScrollHelper, list[float]], remaining: float
) -> None:
    """Enforce the original monotonic bound without a running child."""
    helper, clock = owner
    original = 100 + MAX_SECONDS + 2
    assert helper._deadline == original
    clock[0] = original - remaining
    helper.cancel()
    assert helper._cancel_deadline == min(original, clock[0] + 1.5)
    interval = helper._timer.interval()
    clock[0] += 0.2
    helper.cancel()
    assert helper._deadline == original
    assert helper._timer.interval() == interval
    helper.shutdown()


@pytest.mark.parametrize("stage", ["exit", "decode", "cleanup"])
def test_delayed_timeout_cannot_publish_success(
    owner: tuple[ScrollHelper, list[float]], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """Enforce the original monotonic bound without a running child."""
    helper, clock = owner
    deadline = 100 + MAX_SECONDS + 2
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    clock[0] = deadline if stage == "exit" else deadline - 0.01

    def decode() -> ScrollResult:
        if stage == "decode":
            clock[0] = deadline
        return success()

    monkeypatch.setattr(helper, "_read_result", decode)
    if stage == "cleanup":
        assert helper._directory is not None
        cleanup = helper._directory.cleanup

        def expire_cleanup() -> None:
            cleanup()
            clock[0] = deadline

        monkeypatch.setattr(helper._directory, "cleanup", expire_cleanup)
    helper._exited(0, QProcess.ExitStatus.NormalExit)
    helper._timeout()
    helper._exited(0, QProcess.ExitStatus.NormalExit)
    assert len(results) == 1
    assert results[0].image is None
    assert "timed out" in results[0].message
    assert helper._directory is None


def test_on_time_success_is_rechecked_before_later_publication(
    owner: tuple[ScrollHelper, list[float]],
) -> None:
    """Enforce the original monotonic bound without a running child."""
    helper, clock = owner
    result = success()
    clock[0] = 100 + MAX_SECONDS + 2 - 0.001
    assert helper.publication_result(result).image is result.image
    helper._complete(result)
    clock[0] += 0.001
    assert helper.publication_result(result).image is None


def test_timeout_keeps_scratch_until_exit(
    owner: tuple[ScrollHelper, list[float]],
) -> None:
    """Never clean child-owned scratch before confirmed process exit."""
    helper, _clock = owner
    directory = helper._directory
    helper._timeout()
    assert helper._directory is directory
    helper._exited(1, QProcess.ExitStatus.CrashExit)
    assert helper._directory is None


@pytest.mark.parametrize("offset", [-0.001, 0.0, 0.001])
def test_exact_owner_publication_boundary(
    owner: tuple[ScrollHelper, list[float]], offset: float
) -> None:
    """The 37-second owner bound accepts only strictly earlier publication."""
    helper, clock = owner
    assert helper._deadline == 137.0
    clock[0] = 137.0 + offset
    results: list[ScrollResult] = []
    helper.finished.connect(results.append)
    helper._complete(success())
    assert len(results) == 1
    assert (results[0].image is not None) == (offset < 0)


def test_launch_setup_consumes_original_budget(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slow argument/setup work does not rebase the original start deadline."""
    del qtbot
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    helper = ScrollHelper()
    monkeypatch.setattr(helper._process, "start", Mock())
    monkeypatch.setattr(
        helper._process, "setArguments", lambda _args: clock.__setitem__(0, 105.0)
    )
    helper.start(Point(0, 0), window=1, input_tick=2)
    assert helper._deadline == 137.0
    assert helper._timer.interval() == 32000
    helper.shutdown()
