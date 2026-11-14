"""`verdiclip ctl` captures and closes tabs through the real controller."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot
from tests.ux.test_app_journeys import App, AppFactory
from tests.ux.test_app_journeys import (
    make_app as make_app,  # noqa: PLC0414 - pytest fixture
)
from tests.ux.test_capture_journeys import FakeWindow, FakeWindows

from verdiclip.automation.handlers import AutomationHandlers
from verdiclip.automation.preview import PreviewFolder
from verdiclip.automation.protocol import ErrorCode, JsonObject, Request, Response
from verdiclip.automation.server import ControlServer
from verdiclip.capture.gestures import ClickOutput
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import CANCELLED, ScrollResult
from verdiclip.document.commands import SetCrop
from verdiclip.geometry import Point, Rect

pytestmark = pytest.mark.ux


class Ctl:
    """Send requests to the app as `verdiclip ctl` would; collect late answers."""

    def __init__(self, app: App, tmp_path: Path) -> None:
        self.app = app
        self.server = ControlServer(
            AutomationHandlers(
                app.controller.workspace,
                previews=PreviewFolder(tmp_path / "pv"),
                opener=app.controller.open_file,
                app=app.controller.automation_actions,
            ),
            name="unused",
        )
        self.replies: list[Response] = []

    def send(self, method: str, **params: object) -> bool:
        """Send one request; return whether its answer comes later."""
        line = Request(method, dict(params), ident=len(self.replies) + 1).encode()  # ty: ignore[invalid-argument-type]
        return self.server.answer(
            line, lambda data: self.replies.append(Response.decode(data.rstrip()))
        )

    def ask(self, method: str, **params: object) -> Response:
        """Send one request answered at once and return the answer."""
        assert not self.send(method, **params)
        return self.replies[-1]

    def result(self, method: str, **params: object) -> JsonObject:
        """Return the successful result of a request answered at once."""
        response = self.ask(method, **params)
        assert response.error is None, response.error
        assert response.result is not None
        return response.result


def last_error(ctl: Ctl) -> ErrorCode:
    """Return the code of the last answer, which must be an error."""
    error = ctl.replies[-1].error
    assert error is not None
    return error.code


def test_capture_opens_a_tab_and_close_respects_unsaved_work(
    make_app: AppFactory, tmp_path: Path
) -> None:
    """UX-TRY-17: a region capture opens a tab; close needs discard if unsaved."""
    app = make_app()
    ctl = Ctl(app, tmp_path)

    captured = ctl.result("capture", mode="region", region=[10, 20, 60, 40])
    listed = ctl.result("list")
    refused = ctl.ask("close", doc=captured["id"])
    closed = ctl.result("close", doc=captured["id"], discard=True)

    assert (captured["image"], captured["unsaved"]) == ([60, 40], True)
    assert listed["active"] == captured["id"]
    assert refused.error is not None
    assert refused.error.code is ErrorCode.CONFLICT
    assert closed["discarded"] is True
    assert app.controller.workspace.documents == ()
    assert app.controller.editors == ()


def test_saved_captures_close_and_other_tabs_stay(
    make_app: AppFactory, tmp_path: Path
) -> None:
    """UX-TRY-18: a capture saved with -o closes freely; its window keeps others."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    first = ctl.result("capture", mode="fullscreen")
    second = ctl.result(
        "capture", mode="window", output=str(tmp_path / "w.png"), copy=True
    )

    closed = ctl.result("close", doc=second["id"])

    assert second["saved"] == str(tmp_path / "w.png")
    assert second["copied"] is True
    assert second["title"] == "w.png"
    assert closed["discarded"] is False
    remaining = app.controller.workspace.documents
    assert [r.identifier for r in remaining] == [first["id"]]
    assert len(app.controller.editors) == 1


def test_a_busy_tab_stays_open(make_app: AppFactory, tmp_path: Path) -> None:
    """UX-TRY-18: a saving tab can't be closed by ctl; it answers busy."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    shot = ctl.result("capture", mode="fullscreen")
    record = app.controller.workspace.documents[0]
    record.operations += 1
    try:
        ctl.ask("close", doc=shot["id"], discard=True)
    finally:
        record.operations -= 1

    assert last_error(ctl) is ErrorCode.BUSY
    assert len(app.controller.workspace.documents) == 1


def test_interactive_region_answers_with_the_users_choice(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """UX-TRY-17: region without a rectangle waits for the overlay; busy meanwhile."""
    app = make_app()
    ctl = Ctl(app, tmp_path)

    assert ctl.send("capture", mode="universal", interactive=True)
    qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    ctl.ask("capture", mode="fullscreen")
    busy = last_error(ctl)
    # A modifier's output (here clipboard only) doesn't apply: ctl gets the tab
    app.controller.capture.overlays[0].region_chosen.emit(
        Rect(5, 5, 30, 20), ClickOutput.CLIPBOARD
    )

    assert busy is ErrorCode.BUSY
    answer = ctl.replies[-1]
    assert answer.result is not None
    assert answer.result["image"] == [30, 20]
    assert answer.result["copied"] is False
    assert len(app.controller.workspace.documents) == 1


def test_cancelled_and_failed_choices_answer_with_errors(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """Esc in the overlay answers cancelled (exit 7); a failure says why."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    notes: list[str] = []
    app.controller._notify = lambda title, *_a, **_k: notes.append(title)  # ty: ignore[invalid-assignment]

    ctl.send("capture", mode="universal", interactive=True)
    qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    app.controller.capture.cancel()
    cancelled = last_error(ctl)
    ctl.send("capture", mode="universal", interactive=True)
    qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    app.controller.capture.failed.emit("screen is locked")
    failed = ctl.replies[-1].error
    app.controller.capture.cancel()
    app.controller.capture.failed.emit("later failure")

    assert cancelled is ErrorCode.CANCELLED
    assert failed is not None
    assert failed.message == "screen is locked"
    assert notes == ["Capture failed"]  # Only the failure nobody asked about


def prepare(app: App) -> ScrollHelper:
    """Replace only the scrolling child process."""
    helper = ScrollHelper()
    helper.start = Mock()
    helper.cancel = Mock()
    helper.stop = Mock()
    helper.shutdown = Mock()
    app.controller._scrolling._helper_factory = lambda: helper
    return helper


def test_scrolling_at_a_point_answers_with_the_tall_image(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """UX-TRY-17: scrolling --at starts there from here; the result answers."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    helper = prepare(app)
    service = app.controller._scrolling

    assert ctl.send("capture", mode="scrolling", at=[30, 40], **{"from": "here"})
    qtbot.waitUntil(lambda: helper is service._helper)
    assert isinstance(helper.start, Mock)
    helper.start.assert_called_once_with(Point(30, 40), from_top=False)
    tall = QImage(40, 900, QImage.Format.Format_RGB32)
    helper.finished.emit(ScrollResult(tall, "", 6, "Stopped early with Esc"))

    answer = ctl.replies[-1].result
    assert answer is not None
    assert (answer["image"], answer["warning"]) == (
        [40, 900],
        "Stopped early with Esc",
    )


def test_scrolling_chooser_cancel_and_failure_answer(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """The chooser waits for a click; Esc answers cancelled, failures say why."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    helper = prepare(app)
    service = app.controller._scrolling

    ctl.send("capture", mode="scrolling", interactive=True)
    qtbot.waitUntil(lambda: bool(service.overlays))
    service.cancel()
    cancelled = last_error(ctl)
    ctl.send("capture", mode="scrolling", at=[30, 40])
    qtbot.waitUntil(lambda: helper is service._helper)
    helper.finished.emit(ScrollResult(None, "No scrolling area there", 0))
    failed = ctl.replies[-1].error
    second = prepare(app)
    ctl.send("capture", mode="scrolling", at=[30, 40])
    qtbot.waitUntil(lambda: second is service._helper)
    service.stop()
    second.finished.emit(ScrollResult(None, CANCELLED, 0))
    stopped = last_error(ctl)

    assert cancelled is ErrorCode.CANCELLED
    assert failed is not None
    assert failed.message == "No scrolling area there"
    assert stopped is ErrorCode.CANCELLED
    assert app.controller._scroll_status is None  # No dialog for ctl failures


def test_targets_and_shutdown(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """Targets lists the fake desktop; closing the app answers a waiting capture."""
    app = make_app()
    ctl = Ctl(app, tmp_path)

    targets = ctl.result("targets")
    ctl.send("capture", mode="universal", interactive=True)
    qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    app.controller.shutdown()
    closing = ctl.replies[-1].error
    refused = ctl.ask("capture", mode="fullscreen")

    windows = targets["windows"]
    assert isinstance(windows, list)
    assert windows[0] == {
        "handle": 1,
        "title": "Notepad",
        "bounds": [10, 10, 300, 200],
        "foreground": True,
    }
    assert closing is not None
    assert "closing" in closing.message
    assert refused.error is not None
    assert refused.error.code is ErrorCode.FAILED


def test_open_then_close_a_file(make_app: AppFactory, tmp_path: Path) -> None:
    """Opened files are saved already, so close needs no discard."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    picture = tmp_path / "p.png"
    QImage(12, 8, QImage.Format.Format_RGB32).save(str(picture))
    opened = ctl.result("open", path=str(picture))
    editor = app.controller.editors[0]
    editor.session.history.execute(SetCrop(Rect(0, 0, 12, 8), Rect(1, 1, 5, 5)))

    edited = ctl.ask("close", doc=opened["id"])
    ctl.result("export", doc=opened["id"], path=str(tmp_path / "e.png"))
    closed = ctl.result("close", doc=opened["id"])

    assert edited.error is not None
    assert edited.error.code is ErrorCode.CONFLICT
    assert closed["discarded"] is False
    assert QImage(str(tmp_path / "e.png")).width() == 5


def test_hotkeys_cant_feed_a_waiting_ctl_capture(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """While ctl raises a window, a hotkey capture is refused, not delivered to it."""
    notepad = FakeWindow("Notepad", Rect(10, 10, 300, 200), 1)
    edge = FakeWindow("Edge", Rect(40, 40, 120, 90), 2)
    windows = FakeWindows([notepad, edge], notepad)
    app = make_app(windows=windows)
    ctl = Ctl(app, tmp_path)
    notes: list[str] = []
    app.controller._notify = lambda _title, text, **_k: notes.append(text)  # ty: ignore[invalid-assignment]

    assert ctl.send(
        "capture", mode="window", title="Edge", output=str(tmp_path / "e.png")
    )
    app.press_hotkey("fullscreen")
    app.controller.tray.action("region").trigger()
    app.controller.capture_scrolling()
    qtbot.waitUntil(lambda: bool(ctl.replies))

    answer = ctl.replies[-1].result
    assert answer is not None
    assert answer["image"] == [120, 90]
    assert QImage(str(tmp_path / "e.png")).width() == 120
    assert len(app.controller.workspace.documents) == 1
    assert len(notes) == 3
    assert all("verdiclip ctl" in note for note in notes)


def test_hotkeys_during_a_ctl_overlay_or_scroll(
    make_app: AppFactory, tmp_path: Path, qtbot: QtBot
) -> None:
    """A hotkey brings ctl's overlay back; the scrolling hotkey stops ctl's scroll."""
    app = make_app()
    ctl = Ctl(app, tmp_path)
    helper = prepare(app)
    service = app.controller._scrolling

    ctl.send("capture", mode="universal", interactive=True)
    qtbot.waitUntil(lambda: bool(app.controller.capture.overlays))
    overlays = app.controller.capture.overlays
    app.press_hotkey("region")
    same = app.controller.capture.overlays == overlays
    app.controller.capture.overlays[0].scroll_chosen.emit(
        Point(30, 40),
        "Panel",
        True,  # noqa: FBT003 - Qt signal
        ClickOutput.USUAL,
    )
    qtbot.waitUntil(lambda: helper is service._helper)
    app.controller.capture_scrolling()  # Second request: cancel
    helper.finished.emit(ScrollResult(None, CANCELLED, 0))

    assert same
    assert last_error(ctl) is ErrorCode.CANCELLED
    assert app.controller.workspace.documents == ()
