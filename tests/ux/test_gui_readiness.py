"""Shared explicit GUI benchmark; never a total cold-launch claim.

Each selector runs alone in a fresh pytest process, locally or on a hosted VM.
QApplication, imports and fixture setup precede the measured factory interval.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

import pytest
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from scripts.gui_benchmark_context import benchmark_context
from shiboken6 import __version__ as shiboken_version
from tests.ux.test_app_journeys import FakeHotkeys, make_app

from verdiclip.shell import controller as controller_module
from verdiclip.shell.controller import AppController
from verdiclip.shell.tray import TrayIcon

if TYPE_CHECKING:
    from tests.ux.test_app_journeys import AppFactory

pytestmark = [
    pytest.mark.ux,
    pytest.mark.skipif(
        os.environ.get("VERDICLIP_GUI_READINESS_QUALIFICATION") != "1",
        reason="Explicit hosted or separately authorized local qualification only",
    ),
]
NS_PER_MS = 1_000_000
CAPTURE_BUDGET_MS = 300
VISIBLE_TIMEOUT_MS = 2500


@pytest.fixture(autouse=True)
def clean_clipboard() -> None:
    """Override root teardown: these two selectors never use the clipboard."""


def _profile(request: pytest.FixtureRequest, qapp: QApplication) -> dict[str, object]:
    """Require identical explicit single-node/runtime admission for both roles."""
    context = benchmark_context(os.environ, sys.platform)
    assert request.session.testscollected == 1, (
        "Each qualification selector needs a fresh process"
    )
    assert qVersion() == os.environ["VERDICLIP_EXPECTED_QT"]
    assert qapp.platformName() == os.environ["VERDICLIP_EXPECTED_QPA"]
    return {
        **context,
        "pid": os.getpid(),
        "node": request.node.nodeid,
        "python": sys.version,
        "qt": qVersion(),
        "pyside": pyside_version,
        "shiboken": shiboken_version,
        "qpa": qapp.platformName(),
        "real_os_backends": False,
        "input_injected": False,
        "qapplication_and_import_costs_included": False,
        "fixture_setup_costs_included": False,
        "total_cold_launch_measured": False,
        "root_fixture_may_clear_vm_clipboard_on_teardown": False,
        "benchmark_clipboard_access": False,
    }


def _runtime_context(qapp: QApplication) -> dict[str, object]:
    """Read anonymous environment facts only after the measured interval."""
    screens = []
    for screen in qapp.screens():
        geometry = screen.geometry()
        screens.append(
            {
                "geometry": [
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                ],
                "dpr": screen.devicePixelRatio(),
            }
        )
    return {
        "os_system": platform.system(),
        "os_release": platform.release(),
        "os_build": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "screen_count": len(screens),
        "screens": screens,
        "environment_sampled_after_interval": True,
        "physical_dpi_or_local_hosted_parity_proved": False,
    }


def _emit(receipt: dict[str, object]) -> None:
    """Write metadata to the job log before any latency-budget assertion."""
    sys.stdout.write(
        "GUI_READINESS_RECEIPT=" + json.dumps(receipt, sort_keys=True) + "\n"
    )
    sys.stdout.flush()


def _document_dimensions(controller: AppController) -> list[int] | None:
    """Read actual synthetic pixels after timing; absent/ambiguous editors stay null."""
    editors = controller.editors
    if len(editors) != 1:
        return None
    image = editors[0].session.document.image
    return [image.width(), image.height()]


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
                **_runtime_context(qapp),
                "case": "first-controller-start-and-capture",
                "outer_make_app_ms": (startup_completed - started) / NS_PER_MS,
                "first_capture_ms": first_capture_ms,
                "document_image_dimensions": _document_dimensions(app.controller),
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

    # Retire only the first fixture-owned editor before measuring a warm capture.
    first_editor = app.controller.editors[0]
    first_editor.session.history.mark_delivered()
    closed = first_editor.close()
    assert closed
    qtbot.waitUntil(lambda: app.controller.editors == (), timeout=VISIBLE_TIMEOUT_MS)
    warm_started = time.perf_counter_ns()
    try:
        app.press_hotkey("fullscreen")
        qtbot.waitUntil(
            lambda: (
                len(app.controller.editors) == 1
                and app.controller.editors[0].isVisible()
            ),
            timeout=VISIBLE_TIMEOUT_MS,
        )
    finally:
        warm_ms = (time.perf_counter_ns() - warm_started) / NS_PER_MS
        _emit(
            {
                **profile,
                **_runtime_context(qapp),
                "case": "warm-capture",
                "warm_capture_ms": warm_ms,
                "document_image_dimensions": _document_dimensions(app.controller),
                "capture_budget_ms": CAPTURE_BUDGET_MS,
                "editor_visible": len(app.controller.editors) == 1
                and app.controller.editors[0].isVisible(),
                "same_controller_as_first_capture": True,
                "preceded_by_first_capture_and_environment_sampling": True,
            }
        )
    assert app.controller.editors[0] is not first_editor
    assert app.controller.editors[0].action("tool_select").isChecked()
    assert warm_ms < CAPTURE_BUDGET_MS


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
                **_runtime_context(qapp),
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
