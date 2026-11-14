"""``ctl capture`` in the app: every capture type, its target and its answer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QGuiApplication, QImage
from pytestqt.qtbot import QtBot

from verdiclip.automation.capturing import CaptureOutcome, CaptureSpec
from verdiclip.automation.protocol import AutomationError, Deferred, ErrorCode
from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.models import ScreenGeometry
from verdiclip.document.document import Document
from verdiclip.editor.page import DocumentRecord
from verdiclip.editor.session import EditorSession
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.output.delivery import ImageDelivery
from verdiclip.settings import EditorSettings, OutputSettings
from verdiclip.shell.automation_capture import AutomationCaptures

pytestmark = pytest.mark.usefixtures("qapp")


@dataclass(frozen=True, slots=True)
class Win:
    """A window on the fake desktop."""

    handle: int
    title: str
    bounds: Rect


@dataclass(slots=True)
class Windows:
    """Windows topmost first, and the active one."""

    windows: list[Win] = field(default_factory=list)
    active: Win | None = None

    def visible_windows(
        self, *, exclude: frozenset[int] = frozenset()
    ) -> Sequence[Win]:
        """Return the windows."""
        return [w for w in self.windows if w.handle not in exclude]

    def foreground(self) -> Win | None:
        """Return the active window."""
        return self.active


@dataclass(slots=True)
class Desktop:
    """The virtual desktop, one colour; ``fail`` makes freezing fail."""

    fail: bool = False
    frames: int = 0
    explode: bool = False

    def freeze(self) -> FrozenScreen:
        """Return the desktop image."""
        if self.explode:
            msg = "cannot convert float infinity to integer"
            raise OverflowError(msg)
        if self.fail:
            msg = "screen is locked"
            raise CaptureError(msg)
        self.frames += 1
        geo = QGuiApplication.primaryScreen().virtualGeometry()
        image = QImage(geo.width(), geo.height(), QImage.Format.Format_RGB32)
        image.fill(QColor("teal"))
        return FrozenScreen(image, Rect(geo.x(), geo.y(), geo.width(), geo.height()))


@dataclass(slots=True)
class Choosers:
    """Record which chooser or scrolling capture was asked for."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def choose_region(self) -> None:
        """Record the region overlay."""
        self.calls.append(("region",))

    def choose_scrolling(self) -> None:
        """Record the scrolling chooser."""
        self.calls.append(("scrolling",))

    def scroll_at(self, point: Point, title: str, *, from_top: bool) -> None:
        """Record a scrolling capture."""
        self.calls.append(("scroll_at", point, title, from_top))


@dataclass(slots=True)
class Admitted:
    """Open captures as documents, or fail like a broken editor."""

    opened: list[tuple[str, Path | None, bool, QImage]] = field(default_factory=list)
    fail: bool = False

    def __call__(
        self,
        image: QImage,
        title: str,
        saved: Path | None,
        taken_at: datetime,
        delivered: bool,  # noqa: FBT001 - matches the controller's callback
    ) -> DocumentRecord:
        """Record and return a new document."""
        del taken_at
        if self.fail:
            msg = "no editor"
            raise RuntimeError(msg)
        self.opened.append((title, saved, delivered, image))
        session = EditorSession(Document(image), EditorSettings(), delivered=delivered)
        return DocumentRecord(session, ImageDelivery(OutputSettings()), title=title)


NOTEPAD = Win(1, "Notepad - notes.txt", Rect(10, 20, 300, 200))
EDGE = Win(2, "Docs - Microsoft Edge", Rect(50, 60, 400, 300))


@dataclass
class Rig:
    """The captures under test and their fakes."""

    captures: AutomationCaptures
    desktop: Desktop
    windows: Windows
    choosers: Choosers
    admitted: Admitted
    raised: list[int]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    """Captures over two windows; Notepad is active and raising works."""
    desktop, windows = Desktop(), Windows([NOTEPAD, EDGE], NOTEPAD)
    choosers, admitted = Choosers(), Admitted()
    raised: list[int] = []

    def activate(handle: int) -> bool:
        raised.append(handle)
        return handle != 99

    captures = AutomationCaptures(
        desktop,
        windows,
        choosers,
        admitted,
        lambda: ImageDelivery(OutputSettings(directory=tmp_path)),
        activate=activate,
        settle_ms=1,
    )
    return Rig(captures, desktop, windows, choosers, admitted, raised)


def outcome(answer: Deferred[CaptureOutcome]) -> CaptureOutcome:
    """Return the settled outcome."""
    seen: list[CaptureOutcome | None] = []
    errors: list[AutomationError | None] = []

    def record(value: CaptureOutcome | None, failure: AutomationError | None) -> None:
        seen.append(value)
        errors.append(failure)

    answer.then(record)
    assert errors == [None], errors
    value = seen[0]
    assert value is not None
    return value


def tab(done: CaptureOutcome) -> DocumentRecord:
    """Return the tab a capture opened in."""
    assert done.record is not None
    return done.record


def error(answer: Deferred[CaptureOutcome]) -> AutomationError:
    """Return the settled error."""
    errors: list[AutomationError | None] = []
    answer.then(lambda _v, e: errors.append(e))
    failure = errors[0]
    assert failure is not None
    return failure


def spec(**params: object) -> CaptureSpec:
    """Parse a capture request."""
    return CaptureSpec.from_params(dict(params))  # ty: ignore[invalid-argument-type]


def size(image: QImage) -> tuple[int, int]:
    """Return an image's size."""
    return image.width(), image.height()


def test_region_with_a_rectangle_opens_at_once(rig: Rig, tmp_path: Path) -> None:
    """A given region is cropped from one frozen frame, saved and opened."""
    target = tmp_path / "region.png"

    done = outcome(
        rig.captures.start(
            spec(mode="region", region=[5, 6, 40, 30], output=str(target))
        )
    )

    title, saved, delivered, image = rig.admitted.opened[0]
    assert (title, saved, delivered, size(image)) == (
        "Screenshot",
        target,
        True,
        (40, 30),
    )
    assert done.saved == target
    assert QImage(str(target)).width() == 40
    assert not rig.captures.waiting


def test_fullscreen_takes_the_named_cursor_or_every_monitor(rig: Rig) -> None:
    """Fullscreen crops one monitor, or the whole desktop with all."""
    screen = ScreenGeometry.from_screen(QGuiApplication.screens()[0]).physical

    first = outcome(rig.captures.start(spec(mode="fullscreen", monitor=1)))
    cursor = outcome(rig.captures.start(spec(mode="fullscreen")))
    every = outcome(rig.captures.start(spec(mode="fullscreen", all=True)))

    whole = rig.desktop.freeze().bounds
    assert tab(first).session.document.image_rect.width == screen.width
    assert tab(cursor).title == "Screen"
    assert tab(every).session.document.image_rect.width == whole.width


def test_a_missing_monitor_is_not_found(rig: Rig) -> None:
    """A monitor number past the last one names how many there are."""
    failure = error(rig.captures.start(spec(mode="fullscreen", monitor=9)))

    assert failure.code is ErrorCode.NOT_FOUND
    assert "no monitor 9" in failure.message
    assert not rig.captures.waiting


def test_the_active_window_is_captured_without_raising(rig: Rig) -> None:
    """The foreground window needs no raise and opens under its title."""
    done = outcome(rig.captures.start(spec(mode="window")))

    assert rig.raised == []
    assert tab(done).title == NOTEPAD.title
    assert size(rig.admitted.opened[0][3]) == (300, 200)


def test_a_named_window_is_raised_then_captured(rig: Rig, qtbot: QtBot) -> None:
    """A window found by title is brought to the front, then captured."""
    answer = rig.captures.start(spec(mode="window", title="microsoft edge"))

    assert rig.captures.waiting
    qtbot.waitUntil(lambda: answer.done)
    done = outcome(answer)
    assert rig.raised == [EDGE.handle]
    assert tab(done).title == EDGE.title
    assert done.warning == ""


def test_exact_titles_win_and_points_and_handles_pick_windows(
    rig: Rig, qtbot: QtBot
) -> None:
    """An exact title beats a partial one; at and handle also find windows."""
    exact = Win(3, "Notes", Rect(0, 0, 50, 50))
    rig.windows.windows = [Win(4, "Notes - draft", Rect(0, 0, 60, 60)), exact, EDGE]
    rig.windows.active = exact

    by_title = outcome(rig.captures.start(spec(mode="window", title="notes")))
    by_point = rig.captures.start(spec(mode="window", at=[100, 100]))
    qtbot.waitUntil(lambda: by_point.done)
    by_handle = rig.captures.start(spec(mode="window", handle=EDGE.handle))
    qtbot.waitUntil(lambda: by_handle.done)

    assert tab(by_title).title == "Notes"
    assert tab(outcome(by_point)).title == EDGE.title
    assert tab(outcome(by_handle)).title == EDGE.title


def test_a_window_windows_wont_raise_is_captured_with_a_warning(
    rig: Rig, qtbot: QtBot
) -> None:
    """If Windows refuses to raise it, the capture says it may be covered."""
    rig.windows.windows.append(Win(99, "Stubborn", Rect(0, 0, 80, 80)))

    answer = rig.captures.start(spec(mode="window", handle=99))
    qtbot.waitUntil(lambda: answer.done)

    assert "may cover" in outcome(answer).warning


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "window", "title": "Paint"},
        {"mode": "window", "handle": 50},
        {"mode": "window", "at": [5000, 5000]},
        {"mode": "scrolling", "title": "Paint"},
    ],
)
def test_unknown_windows_are_not_found(rig: Rig, params: dict[str, object]) -> None:
    """A window that isn't visible is not found, and targets is suggested."""
    failure = error(rig.captures.start(spec(**params)))

    assert failure.code is ErrorCode.NOT_FOUND
    assert "verdiclip ctl targets" in failure.message


def test_no_active_window_is_not_found(rig: Rig) -> None:
    """Without an active window, a plain window capture is not found."""
    rig.windows.active = None

    assert error(rig.captures.start(spec(mode="window"))).code is ErrorCode.NOT_FOUND


def test_frozen_screen_failures_are_reported(rig: Rig) -> None:
    """A desktop that can't be read fails the capture with the reason."""
    rig.desktop.fail = True

    failure = error(rig.captures.start(spec(mode="region", region=[0, 0, 5, 5])))

    assert failure.code is ErrorCode.FAILED
    assert "locked" in failure.message


def test_a_failure_after_raising_is_reported(rig: Rig, qtbot: QtBot) -> None:
    """Freezing can fail after the window was raised; that answers too."""
    answer = rig.captures.start(spec(mode="window", title="Edge"))
    rig.desktop.fail = True

    qtbot.waitUntil(lambda: answer.done)

    assert error(answer).code is ErrorCode.FAILED


def test_interactive_region_waits_for_the_overlay(rig: Rig) -> None:
    """Without a region the overlay opens; its result answers the request."""
    answer = rig.captures.start(spec(mode="universal", interactive=True))

    assert rig.choosers.calls == [("region",)]
    assert rig.captures.waiting
    assert not answer.done
    rig.captures.deliver(
        QImage(7, 8, QImage.Format.Format_RGB32), "Picked", datetime.now().astimezone()
    )

    assert tab(outcome(answer)).title == "Picked"
    assert not rig.captures.waiting


def test_scrolling_targets(rig: Rig, qtbot: QtBot) -> None:
    """Scrolling starts at a point, at a window's center, or in the chooser."""
    at = rig.captures.start(spec(mode="scrolling", at=[30, 40], **{"from": "here"}))
    rig.captures.deliver(
        QImage(5, 50, QImage.Format.Format_RGB32),
        "Long",
        datetime.now().astimezone(),
        "Stopped early with Esc",
    )
    window = rig.captures.start(spec(mode="scrolling", title="Edge"))
    qtbot.waitUntil(lambda: len(rig.choosers.calls) == 2)
    rig.captures.cancelled()
    chooser = rig.captures.start(spec(mode="scrolling", interactive=True))

    assert rig.choosers.calls == [
        ("scroll_at", Point(30, 40), "", False),
        ("scroll_at", EDGE.bounds.center, EDGE.title, True),
        ("scrolling",),
    ]
    assert outcome(at).warning == "Stopped early with Esc"
    assert error(window).code is ErrorCode.CANCELLED
    assert not chooser.done


def test_failures_and_shutdown_answer_a_waiting_request(rig: Rig) -> None:
    """A failed capture and closing the app both answer with an error."""
    failed = rig.captures.start(spec(mode="universal", interactive=True))
    rig.captures.failed("No scrolling area there")
    closing = rig.captures.start(spec(mode="universal", interactive=True))
    rig.captures.shutdown()

    assert error(failed).message == "No scrolling area there"
    assert "closing" in error(closing).message
    rig.captures.cancelled()  # Nothing waits any more: no effect
    rig.captures.deliver(
        QImage(1, 1, QImage.Format.Format_RGB32), "x", datetime.now().astimezone()
    )
    assert len(rig.admitted.opened) == 0


def test_delivery_problems_become_warnings(rig: Rig, tmp_path: Path) -> None:
    """A save that fails still opens the capture, with a warning."""
    done = outcome(
        rig.captures.start(
            spec(mode="region", region=[0, 0, 5, 5], output=str(tmp_path / "x.abc"))
        )
    )

    assert done.saved is None
    assert done.warning.startswith("Not delivered:")
    assert rig.admitted.opened[0][2] is False


def test_copy_marks_the_capture_delivered(rig: Rig) -> None:
    """Copying counts as delivered, so closing won't warn."""
    done = outcome(
        rig.captures.start(spec(mode="region", region=[0, 0, 5, 5], copy=True))
    )

    assert done.copied
    assert rig.admitted.opened[0][2] is True


def test_no_editor_saves_and_copies_without_a_tab(rig: Rig, tmp_path: Path) -> None:
    """With editor false the capture only goes to the file and clipboard."""
    target = tmp_path / "only.png"

    done = outcome(
        rig.captures.start(
            spec(
                mode="region",
                region=[0, 0, 5, 5],
                output=str(target),
                copy=True,
                editor=False,
            )
        )
    )

    assert done.record is None
    assert (done.saved, done.copied) == (target, True)
    assert rig.admitted.opened == []
    assert QImage(str(target)).width() == 5


def test_no_editor_fails_when_nothing_was_delivered(rig: Rig, tmp_path: Path) -> None:
    """Without a tab, a failed save is an error, not a warning."""
    failure = error(
        rig.captures.start(
            spec(
                mode="region",
                region=[0, 0, 5, 5],
                output=str(tmp_path / "x.abc"),
                editor=False,
            )
        )
    )

    assert failure.code is ErrorCode.FAILED
    assert "could not be delivered" in failure.message
    assert rig.admitted.opened == []
    assert not rig.captures.waiting


def test_an_editor_that_cant_open_fails_the_capture(rig: Rig) -> None:
    """If the editor can't take the image, the request fails."""
    rig.admitted.fail = True

    failure = error(rig.captures.start(spec(mode="region", region=[0, 0, 5, 5])))

    assert failure.code is ErrorCode.FAILED
    assert "could not be opened" in failure.message


def test_targets_list_monitors_and_windows(rig: Rig) -> None:
    """Monitors are numbered from 1 and the active window is marked."""
    listed = rig.captures.targets()

    monitors = listed["monitors"]
    windows = listed["windows"]
    assert isinstance(monitors, list)
    assert isinstance(windows, list)
    first = monitors[0]
    assert isinstance(first, dict)
    assert (first["monitor"], first["primary"]) == (1, True)
    assert windows == [
        {
            "handle": 1,
            "title": NOTEPAD.title,
            "bounds": [10, 20, 300, 200],
            "foreground": True,
        },
        {
            "handle": 2,
            "title": EDGE.title,
            "bounds": [50, 60, 400, 300],
            "foreground": False,
        },
    ]


def test_only_the_request_that_scrolls_is_cancelled_when_scrolling_ends(
    rig: Rig,
) -> None:
    """A scrolling session ending answers its own request, never a later one."""
    window = rig.captures.start(spec(mode="universal", interactive=True))
    rig.captures.scrolling_finished()  # Someone else's session: no effect
    waiting = not window.done
    rig.captures.scrolling_started()  # The overlay's Ctrl+click handed it over
    rig.captures.scrolling_finished()

    assert waiting
    assert error(window).code is ErrorCode.CANCELLED
    rig.captures.scrolling_started()  # Nothing waits: no effect


def test_an_unexpected_error_still_frees_the_capture(rig: Rig) -> None:
    """A bug while starting answers failed and doesn't block later captures."""
    rig.desktop.explode = True
    with pytest.raises(OverflowError):
        rig.captures.start(spec(mode="region", region=[0, 0, 5, 5]))

    assert not rig.captures.waiting
