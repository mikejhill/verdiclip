"""Prepared native delivery observations; no screenshot, device input or clipboard."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import sys
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot
from tests.capture.desktop import DesktopSession
from tests.shell.delivery_fixture import CASES, run_scenario
from tests.ux.test_app_journeys import AppFactory, make_app

pytestmark = pytest.mark.native_desktop


@pytest.fixture(autouse=True)
def require_delivery_handoff(request: pytest.FixtureRequest) -> None:
    """Require an explicit future grant before creating controller/fixture hosts."""
    if not request.config.getoption("--native-delivery-handoff"):
        pytest.skip("Native delivery needs an explicit separate UI handoff")
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        pytest.fail("Native delivery requires Windows QPA")
    session = DesktopSession.current()
    if session is None or session.state != 0 or session.flags != 1:
        pytest.fail("Native delivery requires a positively active/unlocked session")
    head = os.environ.get("VERDICLIP_QUALIFICATION_HEAD", "")
    if len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        pytest.fail("Use the provenance-recording qualification runner")


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Retain the real controller/output with private fake OS integrations."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


@pytest.mark.parametrize("case", CASES)
def test_native_synthetic_delivery(
    case: str,
    factory: AppFactory,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sample actual native foreground around real synthetic-pixel delivery."""
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = w.HWND

    samples: list[int] = []

    def foreground() -> int:
        handle = int(api.GetForegroundWindow() or 0)
        samples.append(handle)
        return handle

    def admit(anchor: QWidget, surface: QWidget) -> tuple[int, ...]:
        allowed = (int(anchor.winId()), int(surface.winId()))
        qtbot.waitUntil(lambda: foreground() in allowed, timeout=3000)
        return allowed

    receipt: dict[str, object] = {"case": case, "passed": False}
    try:
        receipt.update(
            run_scenario(
                case,
                factory,
                qtbot,
                monkeypatch,
                tmp_path,
                foreground=foreground,
                admit=admit,
            )
        )
        receipt["passed"] = True
    finally:
        receipt.update(
            {
                "native_gui": True,
                "screen_capture": False,
                "global_registration": False,
                "qt": qVersion(),
                "python": sys.version,
                "qpa": QGuiApplication.platformName(),
                "foreground_samples": samples,
                "source_head": os.environ["VERDICLIP_QUALIFICATION_HEAD"],
                "source_dirty": os.environ.get("VERDICLIP_QUALIFICATION_DIRTY"),
                "scope": "Sampled Qt/foreground; visible balloon unqualified",
            }
        )
        (tmp_path / "native-delivery-receipt.json").write_text(
            json.dumps(receipt, indent=2), encoding="utf-8"
        )
