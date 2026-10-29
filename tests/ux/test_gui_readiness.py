"""Hosted first-controller cost accounting; never a total cold-launch claim.

Each selector runs alone in a fresh pytest process on a disposable hosted VM.
QApplication, imports and fixture setup precede the measured factory interval.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import FakeHotkeys, make_app

from verdiclip.shell import controller as controller_module
from verdiclip.shell.controller import AppController
from verdiclip.shell.tray import TrayIcon

if TYPE_CHECKING:
    from tests.ux.test_app_journeys import AppFactory

HOSTED_OPT_IN = (
    sys.platform == "win32"
    and os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    and os.environ.get("VERDICLIP_GUI_READINESS_QUALIFICATION") == "1"
)
pytestmark = [
    pytest.mark.ux,
    pytest.mark.skipif(
        not HOSTED_OPT_IN, reason="Explicit disposable hosted GUI qualification only"
    ),
]
NS_PER_MS = 1_000_000
CAPTURE_BUDGET_MS = 300
VISIBLE_TIMEOUT_MS = 2500


def _profile(request: pytest.FixtureRequest, qapp: QApplication) -> dict[str, object]:
    """Require the exact single-node hosted profile and record actual versions."""
    assert request.session.testscollected == 1, (
        "Each qualification selector needs a fresh process"
    )
    assert qVersion() == os.environ["VERDICLIP_EXPECTED_QT"]
    assert qapp.platformName() == os.environ["VERDICLIP_EXPECTED_QPA"]
    return {
        "source_sha": os.environ.get("GITHUB_SHA"),
        "pid": os.getpid(),
        "node": request.node.nodeid,
        "python": sys.version,
        "qt": qVersion(),
        "pyside": pyside_version,
        "qpa": qapp.platformName(),
        "real_os_backends": False,
        "input_injected": False,
        "qapplication_and_import_costs_included": False,
        "fixture_setup_costs_included": False,
        "total_cold_launch_measured": False,
        "root_fixture_may_clear_vm_clipboard_on_teardown": True,
    }


def _emit(receipt: dict[str, object]) -> None:
    """Write metadata to the job log before any latency-budget assertion."""
    sys.stdout.write(
        "GUI_READINESS_RECEIPT=" + json.dumps(receipt, sort_keys=True) + "\n"
    )
    sys.stdout.flush()


def test_first_controller_start_and_capture_costs(
    request: pytest.FixtureRequest, qapp: QApplication, qtbot: QtBot
) -> None:
    """Report the cost shift and retain UX-CAP-11's unchanged 300ms budget."""
    profile = _profile(request, qapp)
    factory: AppFactory = request.getfixturevalue(make_app.__name__)
    started = time.perf_counter_ns()
    app = factory()
    startup_completed = time.perf_counter_ns()
    capture_started = time.perf_counter_ns()
    try:
        app.press_hotkey("fullscreen")  # Fake WM_HOTKEY dispatch, synthetic pixels.
        qtbot.waitUntil(
            lambda: (
                len(app.controller.editors) == 1
                and app.controller.editors[0].isVisible()
            ),
            timeout=VISIBLE_TIMEOUT_MS,
        )
    finally:
        capture_completed = time.perf_counter_ns()
        readiness = app.controller.gui_readiness
        first_capture_ms = (capture_completed - capture_started) / NS_PER_MS
        _emit(
            {
                **profile,
                "case": "first-controller-start-and-capture",
                "outer_make_app_ms": (startup_completed - started) / NS_PER_MS,
                "first_capture_ms": first_capture_ms,
                "outer_factory_minus_start_sequence_ms": (startup_completed - started)
                / NS_PER_MS
                - readiness.controller_start_ms
                if readiness is not None
                else None,
                "combined_factory_to_visible_wall_ms": (capture_completed - started)
                / NS_PER_MS,
                "between_intervals_ms": (capture_started - startup_completed)
                / NS_PER_MS,
                "startup_readiness": asdict(readiness)
                if readiness is not None
                else None,
                "controller_start_plus_capture_ms": readiness.combined_ms(
                    first_capture_ms
                )
                if readiness is not None
                else None,
                "editor_visible": len(app.controller.editors) == 1
                and app.controller.editors[0].isVisible(),
                "capture_budget_ms": CAPTURE_BUDGET_MS,
                "preparation_is_nested_in_startup": True,
            }
        )
    assert readiness is not None
    assert readiness.fonts.family_count > 0
    assert readiness.fonts.digit_advance > 0
    assert readiness.fonts.elapsed_ms <= readiness.controller_start_ms
    assert readiness.controller_start_ms <= (startup_completed - started) / NS_PER_MS
    assert app.controller.tray.isVisible()
    assert "fullscreen" in app.hotkeys.registered
    assert app.controller.editors[0].action("tool_select").isChecked()
    assert first_capture_ms < CAPTURE_BUDGET_MS


def test_preparation_failure_never_admits_tray_or_hotkeys(
    request: pytest.FixtureRequest, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the real Qt controller boundary with a failed preparation call."""
    profile = _profile(request, qapp)
    factory: AppFactory = request.getfixturevalue(make_app.__name__)
    controllers: list[AppController] = []
    admission: list[str] = []
    original_start = AppController.start
    failure = RuntimeError("hosted font preparation failure sentinel")

    def track_start(controller: AppController) -> None:
        controllers.append(controller)  # make_app does not append a failed start.
        original_start(controller)

    def fail_preparation() -> None:
        raise failure

    def tray_show(_tray: TrayIcon) -> None:
        admission.append("tray.show")

    def register(
        _backend: FakeHotkeys, hwnd: int, hotkey_id: int, modifiers: int, vk: int
    ) -> bool:
        del hwnd, hotkey_id, modifiers, vk
        admission.append("hotkey.register")
        return True

    monkeypatch.setattr(AppController, "start", track_start)
    monkeypatch.setattr(controller_module, "prepare_gui_fonts", fail_preparation)
    monkeypatch.setattr(TrayIcon, "show", tray_show)
    monkeypatch.setattr(FakeHotkeys, "register", register)
    try:
        with pytest.raises(
            RuntimeError, match="hosted font preparation failure sentinel"
        ) as raised:
            factory()
        _emit(
            {
                **profile,
                "case": "preparation-failure-admission",
                "controllers_created": len(controllers),
                "admission_calls": admission,
                "receipt_published": any(
                    c.gui_readiness is not None for c in controllers
                ),
                "failure_propagated_unchanged": raised.value is failure,
            }
        )
        assert raised.value is failure
        assert len(controllers) == 1
        assert admission == []
        assert controllers[0].gui_readiness is None
        assert not controllers[0].tray.isVisible()
    finally:
        for controller in controllers:
            # Failed factories are outside make_app's cleanup list.
            controller.shutdown()
