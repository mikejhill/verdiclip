"""Ordinary capture requests top-level activation, not merely new-tab selection."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot
from tests.shell.test_passive_capture_delivery import factory, presentation_calls
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.models import Capture, CaptureMode
from verdiclip.capture.overlay import SelectionOverlay
from verdiclip.capture.scroll.workflow import ScrollWorkflow
from verdiclip.geometry import Rect
from verdiclip.shell import controller

__all__ = ["factory", "make_app", "presentation_calls"]


def ordinary(image: QImage) -> Capture:
    return Capture(
        image,
        CaptureMode.REGION,
        Rect(0, 0, image.width(), image.height()),
        "New capture",
        datetime.now(UTC),
        preserve_foreground=True,
    )


@pytest.mark.parametrize("minimized", [False, True])
def test_existing_window_activates_for_repeated_ordinary_captures(
    factory: AppFactory,
    sample_image: QImage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    minimized: bool,
) -> None:
    monkeypatch.setattr(controller, "permits_capture_activation", lambda: True)
    app = factory()
    app.controller._on_captured(ordinary(sample_image))
    host = app.controller.editors[0]
    original = host.pages[0]
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    for _ in range(2):
        other.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is other)
        if minimized:
            host.showMinimized()
        app.controller._on_captured(ordinary(sample_image))
        qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
        assert not host.isMinimized()
        assert host.tab_bar.currentIndex() == len(host.pages) - 1
        assert host.pages[0] is original
        assert app.controller.editors == (host,)
    assert len(host.pages) == 3


@pytest.mark.parametrize("refusal", ["native", "busy", "exception"])
def test_refusal_keeps_existing_minimized_host_passive(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
    refusal: str,
) -> None:
    monkeypatch.setattr(controller, "permits_capture_activation", lambda: True)
    app = factory()
    app.controller._on_captured(ordinary(sample_image))
    host = app.controller.editors[0]
    host.showMinimized()
    presentation_calls.clear()

    def guard() -> bool:
        if refusal == "exception":
            raise OSError("unknown")
        return refusal != "native"

    monkeypatch.setattr(controller, "permits_capture_activation", guard)
    if refusal == "busy":
        app.controller._capture._ending = True
    try:
        app.controller._on_captured(ordinary(sample_image))
        assert host.isMinimized()
        assert presentation_calls == []
        assert len(host.pages) == 2
    finally:
        app.controller._capture._ending = False


@pytest.mark.parametrize("boundary", ["show", "restore", "raise"])
def test_native_protection_appearing_during_presentation_stops_activation(
    factory: AppFactory,
    sample_image: QImage,
    monkeypatch: pytest.MonkeyPatch,
    presentation_calls: list[str],
    boundary: str,
) -> None:
    """Reentrant UI changes cannot be overwritten by an earlier safe snapshot."""
    allowed = True
    monkeypatch.setattr(controller, "permits_capture_activation", lambda: allowed)
    app = factory()
    app.controller._on_captured(ordinary(sample_image))
    host = app.controller.editors[0]
    if boundary == "restore":
        host.showMinimized()
    presentation_calls.clear()
    name = {"show": "show", "restore": "setWindowState", "raise": "raise_"}[boundary]
    original = getattr(host, name)

    def protect(*args: object) -> None:
        nonlocal allowed
        original(*args)
        allowed = False

    monkeypatch.setattr(host, name, protect)
    app.controller._on_captured(ordinary(sample_image))
    assert "activate" not in presentation_calls
    assert "focus" not in presentation_calls
    assert presentation_calls == (["raise"] if boundary == "raise" else [])
    assert len(host.pages) == 2


@pytest.mark.parametrize(
    "state",
    [
        "idle",
        "starting",
        "pending",
        "preparing",
        "ending",
        "broker",
        "overlays",
        "workflow",
    ],
)
def test_delivery_guard_distinguishes_direct_frame_from_unretired_work(
    factory: AppFactory, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """Direct callbacks may activate; queued or unconfirmed cleanup never may."""
    app = factory()
    monkeypatch.setattr(controller, "permits_capture_activation", lambda: True)
    service = app.controller._capture
    with monkeypatch.context() as patch:
        if state == "pending":
            patch.setattr(service, "_pending", lambda: None)
        elif state == "overlays":
            patch.setattr(service, "_overlays", [cast(SelectionOverlay, object())])
        elif state == "workflow":
            patch.setattr(
                service,
                "_workflow",
                cast(ScrollWorkflow, SimpleNamespace(has_work=True)),
            )
        elif state == "broker":
            patch.setattr(type(service._broker), "busy", property(lambda _self: True))
        elif state != "idle":
            patch.setattr(service, "_" + state, True)
        assert app.controller._capture_activation_allowed() is (
            state in {"idle", "starting"}
        )


@pytest.mark.parametrize("entry", ["capture_fullscreen", "capture_active_window"])
def test_direct_service_completion_activates_existing_editor(
    factory: AppFactory,
    sample_image: QImage,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    """Real service callbacks complete inside _starting without a live input lease."""
    monkeypatch.setattr(controller, "permits_capture_activation", lambda: True)
    app = factory()
    app.controller._on_captured(ordinary(sample_image))
    host = app.controller.editors[0]
    other = QWidget()
    qtbot.addWidget(other)
    other.show()
    other.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is other)
    service = app.controller._capture
    states: list[tuple[bool, bool, bool]] = []
    service.captured.connect(
        lambda _capture: states.append(
            (service._starting, service.delivery_ready, service.input_active)
        )
    )
    getattr(service, entry)()
    qtbot.waitUntil(lambda: len(host.pages) == 2)
    qtbot.waitUntil(lambda: QApplication.activeWindow() is host)
    assert states == [(True, True, False)]
    assert app.controller.editors == (host,)
    assert host.tab_bar.currentIndex() == 1
