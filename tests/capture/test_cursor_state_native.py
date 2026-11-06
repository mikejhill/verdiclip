"""Explicit disposable-hosted cursor-state experiment, not presentation evidence."""

import json
import sys

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.cursor_state_experiment import run_experiment, runtime_control

pytestmark = pytest.mark.native_desktop


def test_cursor_state_falsification(
    qtbot: QtBot, request: pytest.FixtureRequest
) -> None:
    """Report bounded observed state without equating it to rendered hiding."""
    del qtbot  # Keep QApplication alive for the independent observer.
    if not request.config.getoption("--native-broker-handoff"):
        pytest.skip("Explicit hosted admission required")
    if sys.platform != "win32":
        pytest.fail("Windows required", pytrace=False)
    control = runtime_control()
    results = run_experiment() if control["verified"] is True else []
    print(  # noqa: T201 - fixed-schema hosted receipt
        "CURSOR_STATE="
        + json.dumps(
            {
                "runtime_control": control,
                "cases": results,
                "presentation_verified": False,
            },
            sort_keys=True,
        )
    )
    # An unsafe/unsupported observation is a completed falsification, not support.
    assert results or control["verified"] is False
