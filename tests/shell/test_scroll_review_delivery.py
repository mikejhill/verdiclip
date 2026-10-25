"""Real review panels/controller outputs over simulated acquisition only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot
from tests.capture.test_scroll_acquisition import Run
from tests.capture.test_scroll_panels import Controls
from tests.ux.test_app_journeys import AppFactory, make_app

from verdiclip.capture.scroll.session import ScrollState
from verdiclip.editor.close_prompt import CloseChoice, ClosePrompt
from verdiclip.exceptions import CaptureError, DeliveryError
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import CaptureSettings, Settings


@pytest.fixture
def factory(request: pytest.FixtureRequest) -> AppFactory:
    """Compose fake shell/screen/hotkey services without native capture."""
    return cast(AppFactory, request.getfixturevalue(make_app.__name__))


def stopped(path: Path) -> Run:
    """Prepare verified synthetic pixels without calling a native backend."""
    run = Run(path)
    run.start()
    run.settle()
    run.driver.stop()
    return run


@pytest.mark.parametrize(
    "mode",
    [
        (False, False, False),
        (False, False, True),
        (True, False, False),
        (False, True, False),
        (True, True, False),
        (True, True, True),
    ],
)
def test_acknowledgement_obeys_outputs_without_requiring_editor(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: tuple[bool, bool, bool],
) -> None:
    """Tray-only success acknowledges identity and closes only spool ownership."""
    save, copy, edit = mode
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(
                save_to_file=save, copy_to_clipboard=copy, open_editor=edit
            ),
        )
    )
    copied: list[QImage] = []
    monkeypatch.setattr(
        ImageDelivery, "copy", lambda _self, image: copied.append(image)
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)
    panel.show()
    Controls.button(panel, "whole").click()
    assert run.session.state is ScrollState.CLOSED
    assert len(app.controller.workspace.documents) == int(edit or not (save or copy))
    assert len(copied) == int(copy)
    assert len(list((tmp_path / "out").glob("*.png"))) == int(save)
    assert app.controller.pending_deliveries == ()
    assert panel.isVisible(), "Acknowledgement does not dismiss existing review UI"
    assert any(
        label.text() == "Capture delivered." for label in panel.findChildren(QLabel)
    )
    if app.controller.workspace.documents:
        record = app.controller.workspace.documents[0]
        assert record.session.document.image == run.image.copy(
            0, 0, 96, 48
        ).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    assert app.controller._close_editors()


def test_failed_review_keeps_same_payload_then_tray_retry_releases_session(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful save is not repeated when clipboard failure is retried in review."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(
                save_to_file=True, copy_to_clipboard=True, open_editor=False
            ),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)
    payloads: list[object] = []
    panel.reviewed_capture.connect(payloads.append)

    def fail(*_args: object) -> None:
        raise DeliveryError("Clipboard busy")

    monkeypatch.setattr(ImageDelivery, "copy", fail)
    Controls.button(panel, "whole").click()
    assert run.session.state is ScrollState.DELIVERED
    assert app.controller.pending_deliveries == tuple(payloads)
    assert Controls.button(panel, "Retry").isEnabled()
    assert not app.controller.editors
    monkeypatch.setattr(ImageDelivery, "copy", lambda *_args: None)
    Controls.button(panel, "Retry").click()
    assert payloads[0] is payloads[1]
    assert run.session.state is ScrollState.CLOSED
    assert len(list((tmp_path / "out").glob("*.png"))) == 1
    assert not app.controller.pending_deliveries
    assert app.controller._close_editors()


def test_closing_pending_review_preserves_original_for_tray_retry(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a pending window cannot cancel or lose accepted image ownership."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(copy_to_clipboard=True, open_editor=False),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)

    def fail(*_args: object) -> None:
        raise DeliveryError("Clipboard busy")

    monkeypatch.setattr(ImageDelivery, "copy", fail)
    Controls.button(panel, "whole").click()
    original = app.controller.pending_deliveries[0]
    panel.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert run.session.state is ScrollState.DELIVERED
    assert original.image == run.image.copy(0, 0, 96, 48)
    monkeypatch.setattr(ImageDelivery, "copy", lambda *_args: None)
    app.controller.retry_delivery(original.capture_id)
    assert run.session.state is ScrollState.CLOSED
    assert not app.controller.pending_deliveries
    assert app.controller._close_editors()


def test_unaccepted_review_blocks_exit_and_idle_until_explicit_discard(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    """Exit cannot skip a producer-owned review with no admitted document yet."""
    app = factory(Settings())
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)
    assert not app.controller._close_editors()
    assert run.session.state is ScrollState.REVIEW
    app.controller._idle_timer.start(10000)
    app.controller._resume_idle_if_ready()
    assert app.controller._idle_timer.remainingTime() > 0
    Controls.button(panel, "Discard").click()
    assert run.session.state is ScrollState.CLOSED
    assert app.controller._close_editors()


def test_pending_review_cancel_exit_retains_then_explicit_discard_releases(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global close resolves pending originals with normal Save/Discard/Cancel."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(copy_to_clipboard=True, open_editor=False),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)

    def fail(*_args: object) -> None:
        raise DeliveryError("Clipboard busy")

    monkeypatch.setattr(ImageDelivery, "copy", fail)
    Controls.button(panel, "whole").click()
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.CANCEL)
    assert not app.controller._close_editors()
    assert run.session.state is ScrollState.DELIVERED
    monkeypatch.setattr(ClosePrompt, "ask", lambda *_args: CloseChoice.DISCARD)
    assert app.controller._close_editors()
    assert run.session.state is ScrollState.CLOSED
    assert not app.controller.pending_deliveries


def test_cleanup_failure_keeps_retryable_owner_after_successful_delivery(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cleanup error cannot turn actual output success into repeated output."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(copy_to_clipboard=True, open_editor=False),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)
    original = run.session.close
    copied: list[bool] = []
    monkeypatch.setattr(ImageDelivery, "copy", lambda *_args: copied.append(True))

    def fail() -> None:
        raise CaptureError("Temporary directory busy")

    monkeypatch.setattr(run.session, "close", fail)
    Controls.button(panel, "whole").click()
    assert not app.controller.pending_deliveries
    assert not app.controller._close_editors()
    assert copied == [True]
    monkeypatch.setattr(run.session, "close", original)
    assert app.controller._close_editors()
    assert run.session.state is ScrollState.CLOSED


@pytest.mark.parametrize("route", ["hotkey", "tray"])
@pytest.mark.parametrize("action", ["region", "window", "fullscreen", "repeat"])
def test_controller_capture_commands_cancel_tab_drag_before_acquisition(
    factory: AppFactory,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    action: str,
) -> None:
    """All user-facing controller capture routes quiesce drag without UI dismissal."""
    app = factory(Settings())
    calls: list[str] = []
    monkeypatch.setattr(
        app.controller.workspace, "cancel_drag", lambda: calls.append("drag")
    )
    method, command = {
        "region": ("capture_region", "capture_region"),
        "window": ("capture_active_window", "capture_window"),
        "fullscreen": ("capture_fullscreen", "capture_fullscreen"),
        "repeat": ("repeat_last", "repeat_last"),
    }[action]
    monkeypatch.setattr(app.controller.capture, method, lambda: calls.append("capture"))
    if route == "tray":
        getattr(app.controller._tray_commands(), command)()
    else:
        app.controller._on_hotkey(action)
    assert calls == ["drag", "capture"]


def test_reentrant_review_signal_cannot_repeat_outputs_or_prematurely_acknowledge(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nested canonical delivery coalesces while spool remains owned until success."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(copy_to_clipboard=True, open_editor=False),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)
    result = run.session.accept()
    copied: list[bool] = []

    def copy(*_args: object) -> None:
        copied.append(True)
        assert len(copied) == 1
        panel.reviewed_capture.emit(result)
        assert run.session.state is ScrollState.DELIVERED

    monkeypatch.setattr(ImageDelivery, "copy", copy)
    Controls.button(panel, "whole").click()
    assert copied == [True]
    assert run.session.state is ScrollState.CLOSED
    assert app.controller._close_editors()


def test_unexpected_output_error_is_reported_and_keeps_review_retryable(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected synchronous adapter errors do not escape a Qt review signal."""
    app = factory(
        replace(
            Settings(),
            capture=CaptureSettings(copy_to_clipboard=True, open_editor=False),
        )
    )
    run = stopped(tmp_path / "spool")
    panel = app.controller.review_scrolling_capture(run.session)
    qtbot.addWidget(panel)

    def fail(*_args: object) -> None:
        raise RuntimeError("Adapter failed unexpectedly")

    monkeypatch.setattr(ImageDelivery, "copy", fail)
    Controls.button(panel, "whole").click()
    assert app.controller.pending_deliveries
    assert run.session.state is ScrollState.DELIVERED
    assert Controls.button(panel, "Retry").isEnabled()
    monkeypatch.setattr(ImageDelivery, "copy", lambda *_args: None)
    Controls.button(panel, "Retry").click()
    assert run.session.state is ScrollState.CLOSED
    assert app.controller._close_editors()


def test_new_unaccepted_review_during_close_restores_previously_approved_tabs(
    factory: AppFactory,
    qtbot: QtBot,
    tmp_path: Path,
    sample_image: QImage,
) -> None:
    """A review arriving in removeTab callbacks cannot slip past exit guards."""
    app = factory(Settings())
    image_path = tmp_path / "existing.png"
    assert sample_image.save(str(image_path))
    host = app.controller.open_image(image_path)
    assert host is not None
    records = app.controller.workspace.documents
    run = stopped(tmp_path / "spool")
    panels = []

    def arriving(_index: int) -> None:
        if not host.pages and not panels:
            panel = app.controller.review_scrolling_capture(run.session)
            qtbot.addWidget(panel)
            panels.append(panel)

    host.tab_bar.currentChanged.connect(arriving)
    try:
        assert not app.controller._close_editors()
        assert app.controller.workspace.documents == records
        assert host.pages
        assert run.session.state is ScrollState.REVIEW
    finally:
        host.tab_bar.currentChanged.disconnect(arriving)
        for panel in panels:
            panel.close()
    assert app.controller._close_editors()
