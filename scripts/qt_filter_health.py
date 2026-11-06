"""Opt-in pytest diagnostic: private event delivery after test teardown.

Load explicitly with ``-p scripts.qt_filter_health``. No production imports,
fixture changes, event-loop pumping, input events, windows, or persistent filters.
Output is evidence only and does not replace or modify any test verdict.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import override

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

_EVENT_TYPE = QEvent.Type(QEvent.registerEventType())


class _Receiver(QObject):
    """Receive only this probe's private synchronous event."""

    def __init__(self, event: QEvent) -> None:
        super().__init__()
        self.expected = event
        self.seen = 0

    @override
    def event(self, event: QEvent) -> bool:
        """Count our private event without intercepting unrelated events."""
        if event is self.expected:
            self.seen += 1
            return True
        return super().event(event)


class _Filter(QObject):
    """Observe one private receiver/event pair without consuming it."""

    def __init__(self, receiver: _Receiver) -> None:
        super().__init__()
        self.receiver = receiver
        self.seen = 0

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Count exactly the probe event and always permit dispatch."""
        if watched is self.receiver and event is self.receiver.expected:
            self.seen += 1
        return False


def probe() -> dict[str, object]:
    """Sample delivery without creating an application or draining its queue."""
    app = QCoreApplication.instance()
    if app is None:
        return {"state": "no-application"}
    if not isValid(app):
        return {"state": "invalid-application"}
    if app.thread() != QThread.currentThread():
        return {"state": "wrong-thread"}
    event = QEvent(_EVENT_TYPE)
    receiver = _Receiver(event)
    app_filter = _Filter(receiver)
    receiver_filter = _Filter(receiver)
    result: dict[str, object] = {}
    try:
        app.installEventFilter(app_filter)
        receiver.installEventFilter(receiver_filter)
        sent = QCoreApplication.sendEvent(receiver, event)
        result.update(
            {
                "state": "sampled",
                "app_filter": app_filter.seen,
                "receiver_filter": receiver_filter.seen,
                "receiver": receiver.seen,
                "send_result": sent,
                "filter_valid": isValid(app_filter) and isValid(receiver_filter),
                "same_thread": all(
                    obj.thread() == app.thread()
                    for obj in (receiver, app_filter, receiver_filter)
                ),
                "modal_present": (
                    QApplication.activeModalWidget() is not None
                    if isinstance(app, QApplication)
                    else None
                ),
            }
        )
    except Exception as error:  # noqa: BLE001 - diagnostic evidence only
        result.update(state="probe-error", error_type=type(error).__name__)
    finally:
        result["cleanup_errors"] = cleanup(
            [
                (
                    "remove-receiver-filter",
                    lambda: (
                        receiver.removeEventFilter(receiver_filter)
                        if isValid(receiver) and isValid(receiver_filter)
                        else None
                    ),
                ),
                (
                    "remove-app-filter",
                    lambda: (
                        app.removeEventFilter(app_filter)
                        if isValid(app) and isValid(app_filter)
                        else None
                    ),
                ),
                (
                    "delete-receiver-filter",
                    lambda: (
                        delete(receiver_filter) if isValid(receiver_filter) else None
                    ),
                ),
                (
                    "delete-app-filter",
                    lambda: delete(app_filter) if isValid(app_filter) else None,
                ),
                (
                    "delete-receiver",
                    lambda: delete(receiver) if isValid(receiver) else None,
                ),
            ]
        )
    return result


def cleanup(actions: list[tuple[str, Callable[[], None]]]) -> list[str]:
    """Attempt every cleanup independently; retain fixed labels and error types."""
    errors = []
    for label, action in actions:
        try:
            action()
        except Exception as error:  # noqa: BLE001 - do not skip later cleanup
            errors.append(f"{label}:{type(error).__name__}")
    return errors


@dataclass
class _State:
    counts: dict[str, int] = field(default_factory=dict)
    previous_healthy: str | None = None
    first_failure: dict[str, object] | None = None
    reporting_errors: int = 0


_STATE = pytest.StashKey[_State]()


def _write(config: pytest.Config, receipt: dict[str, object], state: _State) -> None:
    try:
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_line("QT_FILTER_HEALTH " + json.dumps(receipt))
    except Exception:  # noqa: BLE001 - optional reporting must not change verdict
        state.reporting_errors += 1


def _record(config: pytest.Config, nodeid: str, result: dict[str, object]) -> None:
    state = config.stash.setdefault(_STATE, _State())
    healthy = (
        result.get("state") == "sampled"
        and all(
            result.get(key) == 1
            for key in ("app_filter", "receiver_filter", "receiver")
        )
        and not result.get("cleanup_errors")
    )
    status = "healthy" if healthy else str(result.get("state"))
    if status == "sampled":
        status = "unhealthy"
    state.counts[status] = state.counts.get(status, 0) + 1
    if healthy:
        state.previous_healthy = nodeid
    elif status != "no-application" and state.first_failure is None:
        state.first_failure = {
            "after": nodeid,
            "previous_healthy": state.previous_healthy,
            **result,
        }
        _write(config, {"first_failure": state.first_failure}, state)


def pytest_terminal_summary(terminalreporter: object, config: pytest.Config) -> None:
    """Emit at most one compact final counter summary."""
    del terminalreporter
    state = config.stash.get(_STATE, _State())
    _write(
        config,
        {
            "summary": state.counts,
            "first_failure": state.first_failure,
            "reporting_errors": state.reporting_errors,
        },
        state,
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, object, object]:
    """Sample after all existing teardown hooks, including pytest-qt cleanup."""
    del nextitem
    try:
        return (yield)
    finally:
        result: dict[str, object]
        try:
            result = probe()
        except Exception as error:  # noqa: BLE001 - diagnostic never replaces verdict
            result = {"state": "probe-error", "error_type": type(error).__name__}
        try:
            _record(item.config, item.nodeid, result)
        except Exception:  # noqa: BLE001 - bookkeeping cannot replace a test verdict
            state = item.config.stash.setdefault(_STATE, _State())
            state.reporting_errors += 1
