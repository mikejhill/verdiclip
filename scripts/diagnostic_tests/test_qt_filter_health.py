"""Noninterference controls for the opt-in Qt filter diagnostic."""

# Standalone tests outside testpaths preserve the original seeded suite.
# ruff: noqa: S101, PLR2004
from __future__ import annotations

from typing import cast
from unittest.mock import Mock

import pytest
from scripts import qt_filter_health as diagnostic


def test_cleanup_attempts_every_action() -> None:
    """One failed cleanup must not abandon remaining owned resources."""
    first = Mock(side_effect=RuntimeError("synthetic"))
    second = Mock()
    third = Mock(side_effect=ValueError("synthetic"))
    assert diagnostic.cleanup(
        [("first", first), ("second", second), ("third", third)]
    ) == ["first:RuntimeError", "third:ValueError"]
    second.assert_called_once_with()
    third.assert_called_once_with()


def test_reporting_failure_does_not_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken optional reporter records its error without raising."""
    config = pytest.Config.fromdictargs({}, [])
    reporter = Mock()
    monkeypatch.setattr(config.pluginmanager, "get_plugin", Mock(return_value=reporter))
    reporter.write_line.side_effect = RuntimeError
    state = diagnostic._State()
    diagnostic._write(config, {"summary": {}}, state)
    assert state.reporting_errors == 1


def test_probe_and_reporting_errors_preserve_teardown_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostic exceptions cannot replace the original teardown failure."""
    config = pytest.Config.fromdictargs({}, [])
    reporter = Mock()
    monkeypatch.setattr(config.pluginmanager, "get_plugin", Mock(return_value=reporter))
    reporter.write_line.side_effect = RuntimeError
    item = Mock(config=config, nodeid="private-control")
    monkeypatch.setattr(diagnostic, "probe", Mock(side_effect=ValueError))
    hook = diagnostic.pytest_runtest_teardown(cast("pytest.Item", item), None)
    next(hook)
    original = LookupError("original teardown")
    with pytest.raises(LookupError) as caught:
        hook.throw(original)
    assert caught.value is original
    state = config.stash[diagnostic._STATE]
    assert state.first_failure is not None
    assert state.first_failure["state"] == "probe-error"
    assert state.reporting_errors == 1


def test_output_is_bounded_and_preserves_preceding_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many unhealthy boundaries produce only first failure and final summary."""
    config = pytest.Config.fromdictargs({}, [])
    reporter = Mock()
    monkeypatch.setattr(config.pluginmanager, "get_plugin", Mock(return_value=reporter))
    typed = config
    diagnostic._record(
        typed,
        "healthy",
        {"state": "sampled", "app_filter": 1, "receiver_filter": 1, "receiver": 1},
    )
    for index in range(20):
        diagnostic._record(typed, f"bad-{index}", {"state": "wrong-thread"})
    diagnostic.pytest_terminal_summary(object(), typed)
    assert reporter.write_line.call_count == 2
    state = config.stash[diagnostic._STATE]
    assert state.first_failure is not None
    assert state.first_failure["after"] == "bad-0"
    assert state.first_failure["previous_healthy"] == "healthy"
    assert state.counts == {"healthy": 1, "wrong-thread": 20}
