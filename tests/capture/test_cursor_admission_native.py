"""Explicit hosted admission only; unsupported is not a cursor fix."""

import json
import os
import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.cursor_admission import run_admission

pytestmark = pytest.mark.native_desktop


def test_owned_cursor_admission(
    qtbot: QtBot, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """No desktop APIs run without the dedicated explicit hosted handoff."""
    if not request.config.getoption("--native-broker-handoff"):
        pytest.skip("Explicit hosted admission required")
    if sys.platform != "win32":
        pytest.fail("Windows required", pytrace=False)
    receipt: dict[str, object] = {"completed": False, "presentation_verified": False}
    try:
        receipt["sessions"] = run_admission(
            qtbot, tmp_path, os.environ["CURSOR_ADMISSION_FIXTURE"]
        )
        receipt["completed"] = True
    finally:
        sys.stdout.write(
            "CURSOR_ADMISSION=" + json.dumps(receipt, sort_keys=True) + "\n"
        )
