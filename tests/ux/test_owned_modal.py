"""Real Qt delivery for the test-owned modal failure bridge (hosted/offscreen)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog
from pytestqt.qtbot import QtBot
from scripts.owned_modal import run_owned_modal


class OwnedCancellation(BaseException):
    """An application-specific control signal must survive the Qt boundary."""


@pytest.mark.parametrize(
    "kind",
    [
        AssertionError,
        KeyboardInterrupt,
        SystemExit,
        GeneratorExit,
        OwnedCancellation,
        pytest.fail.Exception,
        pytest.skip.Exception,
    ],
)
def test_qt_callback_failure_returns_to_python_after_owned_cleanup(
    qtbot: QtBot, kind: type[BaseException]
) -> None:
    """Even interpreter control signals survive the actual C++ callback boundary."""
    dialog = QDialog()
    qtbot.addWidget(dialog)
    original = kind("owned callback failure")
    stopped = []
    called = []

    def callback() -> None:
        called.append(True)
        assert dialog.isVisible()
        raise original

    def arm(delay: int, action: Callable[[], None]) -> Callable[[], None]:
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(action)
        timer.start(delay)

        def stop() -> None:
            timer.stop()
            stopped.append(not timer.isActive())
            timer.deleteLater()

        return stop

    with pytest.raises(kind) as caught:
        run_owned_modal(
            execute=dialog.exec, close=dialog.reject, callback=callback, arm=arm
        )
    assert caught.value is original
    assert called == [True]
    assert stopped == [True, True]
    assert not dialog.isVisible()


def test_qt_watchdog_cleanup_is_failure_without_callback_delivery(qtbot: QtBot) -> None:
    """Real deadline delivery rejects only this fixture and still fails the test."""
    dialog = QDialog()
    qtbot.addWidget(dialog)
    stopped = []
    called = []

    def arm(delay: int, action: Callable[[], None]) -> Callable[[], None]:
        if delay == 0:
            return lambda: stopped.append("withheld action")
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(action)
        timer.start(delay)

        def stop() -> None:
            timer.stop()
            stopped.append("watchdog")
            timer.deleteLater()

        return stop

    with pytest.raises(TimeoutError, match="cleanup is not success"):
        run_owned_modal(
            execute=dialog.exec,
            close=dialog.reject,
            callback=lambda: called.append(True),
            arm=arm,
            timeout_ms=25,
        )
    assert called == []
    assert stopped == ["withheld action", "watchdog"]
    assert not dialog.isVisible()
