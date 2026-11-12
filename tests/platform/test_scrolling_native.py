"""The UI Automation scroll target against an owned Win32 EDIT window."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from tests.capture.desktop import DesktopSession

from verdiclip.capture import scroll_helper
from verdiclip.capture.scrolling import ScrollCapture
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="UI Automation requires Windows"
)


@pytest.fixture(autouse=True)
def qt_on_main_thread(qapp: QApplication) -> QApplication:
    """Create the application first: Qt set up from a worker thread breaks focus.

    Without it, a PNG saved by ``in_thread`` work initializes Qt off the main
    thread and later Qt tests in the same process lose window activation.
    """
    return qapp


class Fixture:
    """The running EDIT window process and where to click."""

    def __init__(self, process: subprocess.Popen[str], line: str) -> None:
        """Parse the rectangle and blank point the child printed."""
        left, top, right, bottom, x, y = (int(v) for v in line.split())
        self.process = process
        self.edit = Rect(left, top, right - left, bottom - top)
        self.blank = Point(x, y)

    @property
    def center(self) -> Point:
        """A point inside the scrolling text."""
        return self.edit.center


@pytest.fixture
def window(request: pytest.FixtureRequest) -> Iterator[Fixture]:
    """Start the owned window, skipping only when the desktop is locked."""
    session = DesktopSession.current()
    reason = session.unavailable_reason() if session is not None else None
    if reason is not None:
        if request.config.getoption("--require-interactive-capture"):
            pytest.fail(reason)
        pytest.skip(reason)
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.platform.edit_window"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        fixture = Fixture(process, process.stdout.readline())
        time.sleep(0.5)  # Let it paint
        yield fixture
    finally:
        process.kill()
        process.wait(10)


def in_thread[T](work: Callable[[], T]) -> T:
    """Run UI Automation on a fresh thread, as the helper process does."""
    results: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            results.append(work())
        except BaseException as error:  # noqa: BLE001 - re-raised below
            errors.append(error)

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(60)
    if errors:
        raise errors[0]
    return results[0]


def test_capture_runs_top_to_bottom_and_restores(window: Fixture) -> None:
    """The whole text is captured and the panel returns to where it was."""
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    def work() -> tuple[int, int, float, float]:
        with UiaScrollTarget(window.center) as target:
            assert window.edit.intersected(target.area) == target.area
            target.scroll_to(50.0)
            time.sleep(0.3)
            before = target.position()
            result = ScrollCapture(target, lambda: False).run()
            time.sleep(0.3)
            after = target.position()
            assert result.image is not None, result.message
            target.check()
            return result.image.height(), target.height, before.percent, after.percent

    height, viewport, before, after = in_thread(work)
    assert height > 3 * viewport
    assert after == pytest.approx(before, abs=1.0)


def test_point_without_a_scroll_panel_is_refused(window: Fixture) -> None:
    """A click outside anything scrollable gives a plain reason."""
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    def work() -> None:
        with UiaScrollTarget(window.blank):
            pass

    with pytest.raises(CaptureError, match="scrolls vertically"):
        in_thread(work)


def test_closed_window_stops_the_check(window: Fixture) -> None:
    """Closing the target window is reported by ``check``."""
    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    def work() -> None:
        with UiaScrollTarget(window.center) as target:
            window.process.kill()
            window.process.wait(10)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    target.check()
                except CaptureError:
                    return
                time.sleep(0.1)
            pytest.fail("check() kept passing after the window closed")

    in_thread(work)


def test_helper_entry_point_writes_image_and_result(
    window: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The child's main() captures and prints one JSON result."""
    point = window.center
    in_thread(
        lambda: scroll_helper.main(
            [str(round(point.x)), str(round(point.y)), str(tmp_path)],
            iter(()),
            lambda: None,
        )
    )
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["success"], result
    assert (tmp_path / "capture.png").is_file()


def test_helper_entry_point_reports_failure(
    window: Fixture, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A point with nothing to scroll produces a failure result, not a crash."""
    point = window.blank
    in_thread(
        lambda: scroll_helper.main(
            [str(point.x), str(point.y), str(tmp_path)], iter(()), lambda: None
        )
    )
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert not result["success"]
    assert "scrolls vertically" in result["message"]


def test_moved_window_stops_the_check(window: Fixture) -> None:
    """Moving the window under the capture is reported by ``check``."""
    import ctypes  # noqa: PLC0415 - Windows-only
    import ctypes.wintypes as w  # noqa: PLC0415

    from verdiclip.platform.scrolling import UiaScrollTarget  # noqa: PLC0415

    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetWindowPos.argtypes = (
        w.HWND,
        w.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        w.UINT,
    )
    user.SetWindowPos.restype = w.BOOL

    def work() -> None:
        with UiaScrollTarget(window.center) as target:
            target.check()
            swp_nosize_nozorder = 0x0001 | 0x0004
            user.SetWindowPos(target._window, None, 160, 140, 0, 0, swp_nosize_nozorder)
            time.sleep(0.3)
            with pytest.raises(CaptureError, match="moved"):
                target.check()

    in_thread(work)
