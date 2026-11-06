"""Hosted-only root architecture comparison; never native product qualification."""

import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.runtime_comparison import (
    RuntimeComparison,
    require_probe_environment,
)

pytestmark = pytest.mark.native_desktop


def test_runtime_identity_comparison(
    qtbot: QtBot,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """One cold arm on its own VM, with failures retained and no fallback."""
    if not request.config.getoption("--native-broker-handoff"):
        pytest.skip("Root comparison requires explicit hosted qualification")
    if sys.platform != "win32":
        pytest.fail("Root comparison requires Windows", pytrace=False)
    provider = require_probe_environment()
    case = RuntimeComparison(qtbot, tmp_path, provider)
    try:
        case.run()
    except Exception:  # noqa: BLE001 - export only constant labels, never fixture data
        case.receipt["outcome"] = "failed"
    finally:
        case.close()
    if case.receipt["outcome"] != "passed":
        pytest.fail(
            "Read-only root comparison failed; sanitized receipt retained",
            pytrace=False,
        )
