"""Restored capture failure boundaries retain the released implementation."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot
from tests.ux.test_capture_journeys import FakeDesktop, FakeWindows

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import CaptureMode
from verdiclip.capture.service import CaptureService
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Rect


@pytest.mark.parametrize("operation", ["fullscreen", "repeat", "selection"])
def test_capture_failure_emits_error_without_output(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Freeze/crop failure never emits a successful screenshot or leaves selection."""
    desktop = FakeDesktop()
    service = CaptureService(desktop, FakeWindows())
    errors: list[str] = []
    outputs: list[object] = []
    service.failed.connect(errors.append)
    service.captured.connect(outputs.append)
    area = Rect(10, 10, 20, 20)
    try:
        if operation == "fullscreen":
            desktop.fail = True
            service.capture_fullscreen()
        elif operation == "repeat":
            service._last_area = area
            service._last_mode = CaptureMode.REGION
            desktop.fail = True
            service.repeat_last()
        else:
            service._frozen = desktop.freeze()

            def fail_crop(_self: FrozenScreen, _area: Rect) -> object:
                raise CaptureError("owned crop failure")

            monkeypatch.setattr(FrozenScreen, "crop", fail_crop)
            service._finish_selection(area, CaptureMode.REGION, "Owned")
        qtbot.waitUntil(lambda: bool(errors))
        assert len(errors) == 1
        assert outputs == []
        assert not service.is_selecting
    finally:
        service.cancel()


def test_stale_pending_callback_does_not_start_capture(qtbot: QtBot) -> None:
    """A retired queued callback is harmless without a pending request."""
    desktop = FakeDesktop()
    service = CaptureService(desktop, FakeWindows())
    service._start_pending()
    qtbot.wait(0)
    assert desktop.frames == 0
    assert not service.is_selecting
