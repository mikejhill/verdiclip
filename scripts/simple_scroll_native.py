"""Exercise the isolated helper against installed applications on hosted Windows.

Local HTML and text are ordinary document inputs, not foreign-process code or
application-specific runtime profiles. Exit nonzero when native feasibility
fails; diagnostic images and JSON never replace the result of the test.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scrolling import ScrollResult
from verdiclip.geometry import Point
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo, WindowLocator

_ROWS = 120
_LINE_HEIGHT = 24
_START_PERCENT = 37.0
_WINDOW_WIDTH = 1000
_WINDOW_HEIGHT = 760
_GREEN = 80
_BLUE = 160


def _document(*, nested: bool) -> str:
    rows = "".join(
        f'<div style="height:24px;background:rgb({i + 30},80,160)">'
        f"Line {i:03d}: VerdiClip real Edge scrolling capture. "
        "This is ordinary document content.</div>"
        for i in range(_ROWS)
    )
    inner = (
        (
            '<section aria-label="Scrolling example" tabindex="0" '
            'style="height:420px;width:700px;overflow:scroll">'
            f'<div style="width:900px">{rows}</div></section>'
            '<div style="height:1600px">Outer document continues.</div>'
        )
        if nested
        else rows
    )
    return (
        '<!doctype html><meta charset="utf-8"><title>VerdiClip scrolling check</title>'
        "<style>body{margin:0;font:16px Arial}div{box-sizing:border-box}</style>"
        + inner
    )


def _launch(kind: str, directory: Path) -> subprocess.Popen[bytes]:
    if kind.startswith("edge"):
        path = directory / "scrolling.html"
        path.write_text(_document(nested=kind == "edge-nested"), encoding="utf-8")
        candidates = [
            Path(os.environ.get(key, "")) / "Microsoft/Edge/Application/msedge.exe"
            for key in ("ProgramFiles(x86)", "ProgramFiles")
        ]
        executable = next((p for p in candidates if p.is_file()), None)
        if executable is None:
            raise RuntimeError("Installed Microsoft Edge was not found")
        command = [
            str(executable),
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            f"--user-data-dir={directory / 'profile'}",
            f"--window-size={_WINDOW_WIDTH},{_WINDOW_HEIGHT}",
            path.as_uri(),
        ]
    else:
        path = directory / "VerdiClip scrolling check.txt"
        path.write_text(
            "\n".join(
                f"Line {i:03d}: VerdiClip real Notepad scrolling capture. "
                + "Document content. " * 4
                for i in range(_ROWS)
            ),
            encoding="utf-8",
        )
        executable = Path(os.environ["WINDIR"]) / "System32/notepad.exe"
        command = [str(executable), str(path)]
    return subprocess.Popen(command)  # noqa: S603 - fixed installed apps and owned content


def _window() -> WindowInfo:
    locator = WindowLocator()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for window in locator.visible_windows():
            if "VerdiClip scrolling check" in window.title:
                return window
        time.sleep(0.1)
    raise RuntimeError("The installed application did not show the test document")


def _prepare(window: WindowInfo) -> WindowInfo:
    user = c.WinDLL("user32", use_last_error=True)
    user.MoveWindow.argtypes = (w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.BOOL)
    user.MoveWindow.restype = w.BOOL
    user.SetForegroundWindow.argtypes = (w.HWND,)
    user.SetForegroundWindow.restype = w.BOOL
    if not user.MoveWindow(window.handle, 10, 10, _WINDOW_WIDTH, _WINDOW_HEIGHT, 1):
        raise RuntimeError("Could not position the owned test application")
    if not user.SetForegroundWindow(window.handle):
        raise RuntimeError("The owned test application could not become foreground")
    time.sleep(1)
    return _window()


def _capture(point: Point) -> ScrollResult:
    loop = QEventLoop()
    helper = ScrollHelper()
    results: list[ScrollResult] = []

    def completed(result: ScrollResult) -> None:
        results.append(result)
        loop.quit()

    helper.finished.connect(completed)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(30_000)
    helper.start(point)
    loop.exec()
    timer.stop()
    if not results:
        helper.cancel()
        raise RuntimeError("The helper did not produce its bounded terminal result")
    return results[0]


def _verify_edge_rows(result: ScrollResult) -> None:
    if result.image is None:
        raise RuntimeError(result.message)
    seen: list[int] = []
    for y in range(result.image.height()):
        color = result.image.pixelColor(4, y)
        if color.green() == _GREEN and color.blue() == _BLUE:
            row = color.red() - 30
            if not seen or seen[-1] != row:
                seen.append(row)
    if seen != list(range(_ROWS)):
        raise RuntimeError(
            f"Document rows were missing, duplicated, or out of order: {seen}"
        )
    if result.image.height() != _ROWS * _LINE_HEIGHT:
        raise RuntimeError(f"Unexpected document height: {result.image.height()}")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    window = _prepare(_window())
    point = (
        Point(window.bounds.left + 350, window.bounds.top + 300)
        if kind == "edge-nested"
        else window.bounds.center
    )
    with UiaScrollTarget(point) as target:
        report["viewport"] = asdict(target.area)
        report["initial"] = asdict(target.position())
        if kind == "edge-nested":
            # Test setup only: start the actual document horizontally scrolled.
            # Runtime captures never write the horizontal percentage.
            target._scroll.call(4, (c.c_double, c.c_double), 35.0, -1.0)
        target.move(_START_PERCENT)
        initial = target.position()
        report["start"] = asdict(initial)
        target.frame().save(str(output / "before.png"))
    outer_before = None
    outer_point = Point(window.bounds.right - 80, window.bounds.top + 300)
    if kind == "edge-nested":
        _require(
            initial.horizontal > 0,
            "The nested test did not start horizontally scrolled",
        )
        with UiaScrollTarget(outer_point) as outer:
            outer_before = outer.position()
    result = _capture(point)
    report.update(
        message=result.message, restoration=result.restoration, frames=result.frames
    )
    if result.image is not None:
        result.image.save(str(output / "result.png"))
    with UiaScrollTarget(point) as target:
        after = target.position()
        target.frame().save(str(output / "after.png"))
        report["after"] = asdict(after)
        _require(
            after == initial,
            "The starting horizontal/vertical position was not restored",
        )
    if kind == "edge-nested":
        with UiaScrollTarget(outer_point) as outer:
            _require(
                outer.position() == outer_before,
                "Capturing the inner panel moved the outer page",
            )
    _require(
        result.image is not None and result.restoration == "restored",
        result.message or result.restoration,
    )
    if kind.startswith("edge"):
        _verify_edge_rows(result)
    report["success"] = True


def main() -> None:
    """Run one actual-app case; the workflow owns the outer wall-clock limit."""
    kind = sys.argv[1]
    output = Path("artifacts/simple-scroll") / kind
    output.mkdir(parents=True, exist_ok=True)
    user = c.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = (c.c_void_p,)
    user.SetProcessDpiAwarenessContext.restype = w.BOOL
    _require(
        user.SetProcessDpiAwarenessContext(c.c_void_p(-4)),
        "Could not set physical-pixel coordinates",
    )
    app = QCoreApplication(sys.argv[:1])
    app.setApplicationName("VerdiClip scrolling qualification")
    report: dict[str, object] = {
        "application": kind,
        "success": False,
        "oracle": "ordered rows and exact height"
        if kind.startswith("edge")
        else "capture and restore smoke; screenshot content review required",
    }
    with TemporaryDirectory(prefix="verdiclip-scroll-test-") as scratch:
        process = _launch(kind, Path(scratch))
        try:
            _case(kind, output, report)
        except Exception as error:
            report["error"] = str(error)
            raise
        finally:
            (output / "result.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            if process.poll() is None:
                subprocess.run(  # noqa: S603 - only the process owned above
                    [
                        str(Path(os.environ["WINDIR"]) / "System32/taskkill.exe"),
                        "/PID",
                        str(process.pid),
                        "/T",
                        "/F",
                    ],
                    check=True,
                    timeout=10,
                    capture_output=True,
                )
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
