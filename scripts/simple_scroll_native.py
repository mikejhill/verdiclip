"""Exercise the isolated helper against installed applications on hosted Windows.

Local HTML and text are ordinary document inputs, not foreign-process code or
application-specific runtime profiles. Exit nonzero when native feasibility
fails; diagnostic images and JSON never replace the result of the test.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QImage

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scroll_helper import main as helper_main
from verdiclip.capture.scrolling import (
    END_PERCENT,
    ScrollPosition,
    ScrollResult,
    ScrollTransaction,
)
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo, WindowLocator

_ROWS = 120
_LINE_HEIGHT = 24
_START_PERCENT = 37.0
_DIAGNOSTIC_FRAMES = 2
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


def _launch(
    kind: str, directory: Path, report: dict[str, object]
) -> subprocess.Popen[bytes]:
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
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    literal = str(executable).replace("'", "''")
    version = subprocess.run(  # noqa: S603 - read version of the fixed installed app
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"(Get-Item -LiteralPath '{literal}').VersionInfo | "
            "Select-Object FileVersion,ProductVersion | ConvertTo-Json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    report["version"] = json.loads(version.stdout)
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
    user.SystemParametersInfoW.argtypes = (w.UINT, w.UINT, c.c_void_p, w.UINT)
    user.SystemParametersInfoW.restype = w.BOOL
    work = w.RECT()
    if not user.SystemParametersInfoW(48, 0, c.byref(work), 0):
        raise RuntimeError("Could not read the hosted desktop work area")
    if not user.MoveWindow(
        window.handle,
        work.left + 10,
        work.top + 10,
        min(_WINDOW_WIDTH, work.right - work.left - 20),
        min(_WINDOW_HEIGHT, work.bottom - work.top - 20),
        1,
    ):
        raise RuntimeError("Could not position the owned test application")
    if not user.SetForegroundWindow(window.handle):
        raise RuntimeError("The owned test application could not become foreground")
    time.sleep(1)
    return _window()


def _trace_helper() -> None:
    """Save two accepted frames in this owned test child, never in production."""
    logging.basicConfig(level=logging.DEBUG)
    output = Path(sys.argv.pop())
    original = ScrollTransaction._frame
    observations: list[dict[str, float]] = []

    def record_frame(operation: ScrollTransaction, position: ScrollPosition) -> QImage:
        frame = original(operation, position)
        if len(observations) < _DIAGNOSTIC_FRAMES:
            if not frame.save(str(output / f"frame-{len(observations)}.png")):
                raise OSError("Could not save the native scrolling diagnostic frame")
            observations.append(asdict(position))
            (output / "frames.json").write_text(
                json.dumps(observations, indent=2), encoding="utf-8"
            )
        return frame

    with patch.object(ScrollTransaction, "_frame", record_frame):
        helper_main()


def _capture(point: Point, output: Path) -> ScrollResult:
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
    start = helper._process.start

    def trace_start() -> None:
        arguments = helper._process.arguments()[2:]
        helper._process.setArguments(
            [
                "-c",
                "from scripts.simple_scroll_native import _trace_helper; "
                "_trace_helper()",
                *arguments,
                str(output.resolve()),
            ]
        )
        start()

    with patch.object(helper._process, "start", trace_start):
        helper.start(point)
    loop.exec()
    timer.stop()
    if not results:
        helper.cancel()
        raise RuntimeError("The helper did not produce its bounded terminal result")
    (output / "helper.log").write_text(
        bytes(helper._process.readAllStandardError().data()).decode(
            "utf-8", errors="replace"
        ),
        encoding="utf-8",
    )
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


def _position_start(
    target: UiaScrollTarget, report: dict[str, object], *, nested: bool
) -> ScrollPosition:
    """Wait for the setup write before defining the helper's starting state."""
    target.check()
    # One test-only public UIA write avoids racing two asynchronous requests.
    target._scroll.call(
        4, (c.c_double, c.c_double), 35.0 if nested else -1.0, _START_PERCENT
    )
    report["immediate_setup_position"] = asdict(target.position())
    previous = None
    for _ in range(8):
        time.sleep(0.08)
        target.check()
        current = target.position()
        report["observed_setup_position"] = asdict(current)
        if (
            current == previous
            and 0.0 < current.vertical < END_PERCENT
            and (not nested or 0.0 < current.horizontal < END_PERCENT)
        ):
            return current
        previous = current
    raise RuntimeError("The application's test starting position did not settle")


def _verify_selected_target(output: Path, area: Rect, start: ScrollPosition) -> None:
    """The helper must capture the prepared panel, not a restored outer scroller."""
    image = QImage(str(output / "frame-0.png"))
    positions = json.loads((output / "frames.json").read_text(encoding="utf-8"))
    _require(
        (image.width(), image.height()) == (area.width, area.height)
        and positions[0]["horizontal"] == start.horizontal
        and positions[0]["view_size"] == start.view_size,
        "The helper selected a different panel from the prepared target",
    )


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    window = _prepare(_window())
    report["window"] = asdict(window)
    screens = MssScreenSource()
    try:
        frozen = screens.freeze()
        report["desktop"] = asdict(frozen.bounds)
        frozen.image.save(str(output / "desktop-before-discovery.png"))
    finally:
        screens.close()
    point = (
        Point(window.bounds.left + 350, window.bounds.top + 300)
        if kind == "edge-nested"
        else window.bounds.center
    )
    report["selected_point"] = asdict(point)
    with UiaScrollTarget(point) as target:
        report["provider_bounds"] = asdict(target._provider_bounds)
        report["viewport"] = asdict(target.area)
        area = target.area
        report["initial"] = asdict(target.position())
        initial = _position_start(target, report, nested=kind == "edge-nested")
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
            report["outer_before"] = asdict(outer_before)
    result = _capture(point, output)
    report.update(
        message=result.message, restoration=result.restoration, frames=result.frames
    )
    if result.image is not None:
        report["result_size"] = [result.image.width(), result.image.height()]
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
            outer_after = outer.position()
            report["outer_after"] = asdict(outer_after)
            _require(
                outer_after == outer_before,
                "Capturing the inner panel moved the outer page",
            )
    if (output / "frame-0.png").is_file():
        _verify_selected_target(output, area, initial)
    _require(
        result.image is not None and result.restoration == "restored",
        result.message or result.restoration,
    )
    if kind.startswith("edge"):
        _verify_edge_rows(result)
    report["success"] = True


def main() -> None:
    """Run one actual-app case; the workflow owns the outer wall-clock limit."""
    logging.basicConfig(level=logging.INFO)
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
        process = _launch(kind, Path(scratch), report)
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
