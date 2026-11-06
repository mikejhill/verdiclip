"""Hosted-only root architecture comparison; never native product qualification."""

import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot
from tests.capture.root_comparison import (
    RootComparison,
    RootProbeTrace,
    require_probe_environment,
)

from verdiclip.capture.scroll import uia_worker

pytestmark = pytest.mark.native_desktop


def test_one_cold_root_lookup(
    qtbot: QtBot,
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cold arm on its own VM, with failures retained and no fallback."""
    if not request.config.getoption("--native-broker-handoff"):
        pytest.skip("Root comparison requires explicit hosted qualification")
    if sys.platform != "win32":
        pytest.fail("Root comparison requires Windows", pytrace=False)
    client, provider = require_probe_environment()
    monkeypatch.setattr(uia_worker, "WorkerTrace", RootProbeTrace)
    case = RootComparison(qtbot, tmp_path, client, provider)
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
