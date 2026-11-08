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
from collections import Counter
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
    logging.basicConfig(level=logging.INFO)
    mode = sys.argv.pop()
    output = Path(sys.argv.pop())
    original = ScrollTransaction._frame
    move = UiaScrollTarget.move
    observations: list[dict[str, float]] = []
    writes: list[float] = []

    def record_move(target: UiaScrollTarget, percent: float) -> None:
        writes.append(percent)
        move(target, percent)

    def record_frame(operation: ScrollTransaction, position: ScrollPosition) -> QImage:
        frame = original(operation, position)
        if len(observations) < _DIAGNOSTIC_FRAMES:
            if not frame.save(str(output / f"frame-{len(observations)}.png")):
                raise OSError("Could not save the native scrolling diagnostic frame")
            observations.append(asdict(position))
            (output / "frames.json").write_text(
                json.dumps(observations, indent=2), encoding="utf-8"
            )
            if len(observations) == 1 and mode in ("edge-cancel", "edge-input"):
                # Give the parent a deterministic confirmed-state boundary.
                # The real stdin watcher/input check remains unchanged.
                time.sleep(0.4)
        return frame

    try:
        with (
            patch.object(ScrollTransaction, "_frame", record_frame),
            patch.object(UiaScrollTarget, "move", record_move),
        ):
            helper_main()
    finally:
        (output / "movement.json").write_text(
            json.dumps(
                {"writes": len(writes), "last_vertical": writes[-1] if writes else None}
            ),
            encoding="utf-8",
        )


def _capture(point: Point, output: Path, mode: str = "") -> ScrollResult:
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
        script = (
            "from scripts.simple_scroll_native import _trace_helper; _trace_helper()"
        )
        arguments = helper._process.arguments()[2:]
        helper._process.setArguments(
            [
                "-c",
                script,
                *arguments,
                str(output.resolve()),
                mode,
            ]
        )
        start()

    with patch.object(helper._process, "start", trace_start):
        helper.start(point)
    interrupt = QTimer()
    interrupt.setInterval(20)
    acted = False

    def interfere() -> None:
        nonlocal acted
        if not (output / "frame-0.png").exists():
            return
        interrupt.stop()
        if mode == "edge-cancel":
            helper.cancel()
        else:
            _real_mouse_input()
        acted = True

    if mode in ("edge-cancel", "edge-input"):
        interrupt.timeout.connect(interfere)
        interrupt.start()
    loop.exec()
    interrupt.stop()
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
    if mode in ("edge-cancel", "edge-input"):
        _require(acted, "The native interruption was not exercised")
    return results[0]


def _real_mouse_input() -> None:
    """Move one pixel only in the disposable hosted desktop; no held buttons."""
    _require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
        "Real input qualification requires a disposable hosted runner",
    )
    user = c.WinDLL("user32")
    user.mouse_event.argtypes = (w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.c_size_t)
    user.mouse_event.restype = None
    user.mouse_event(1, 1, 0, 0, 0)  # MOUSEEVENTF_MOVE, relative, no button state


def _cover_panel(
    area: Rect, window: int, output: Path, report: dict[str, object]
) -> int:
    """Render an owned, non-activating tiny overlay between the nine samples."""
    user = c.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.argtypes = (
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        c.c_int,
        c.c_int,
        c.c_int,
        c.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        c.c_void_p,
    )
    user.CreateWindowExW.restype = w.HWND
    user.UpdateWindow.argtypes, user.UpdateWindow.restype = (w.HWND,), w.BOOL
    user.GetForegroundWindow.restype = w.HWND
    user.WindowFromPoint.argtypes, user.WindowFromPoint.restype = (w.POINT,), w.HWND
    x, y = round(area.left + area.width / 4), round(area.top + area.height / 4)
    handle = user.CreateWindowExW(
        0x08000088,
        "STATIC",
        "",
        0x90000104,
        x,
        y,
        20,
        20,
        None,
        None,
        None,
        None,
    )  # NOACTIVATE | TOOLWINDOW | TOPMOST; SS_NOTIFY makes STATIC hit-test opaque
    _require(handle, "Could not create the owned overlay")
    if not isinstance(handle, int):
        raise TypeError("Overlay returned an invalid window handle")
    try:
        # This setup runs before the Qt event loop. Dispatch the initial paint.
        _require(user.UpdateWindow(handle), "Could not paint the owned overlay")
        time.sleep(0.1)
        _require(user.GetForegroundWindow() == window, "Overlay changed foreground")
        _require(
            user.WindowFromPoint(w.POINT(x + 10, y + 10)) == handle,
            "Overlay is not above the panel",
        )
        for sample_x in (area.left, area.center.x, area.right - 1):
            for sample_y in (area.top, area.center.y, area.bottom - 1):
                _require(
                    not (x <= sample_x < x + 20 and y <= sample_y < y + 20),
                    "Overlay did not miss the old sample points",
                )
        screens = MssScreenSource()
        try:
            frozen = screens.freeze()
            covered = frozen.crop(area)
            _require(
                covered.save(str(output / "covered.png")),
                "Could not save overlay evidence",
            )
            report["overlay_color"] = _verify_cover_pixels(
                QImage(str(output / "before.png")),
                covered,
                x - round(area.left),
                y - round(area.top),
            )
        finally:
            screens.close()
    except BaseException:
        _close_overlay(handle)
        raise
    else:
        return handle


def _verify_cover_pixels(before: QImage, covered: QImage, left: int, top: int) -> str:
    """Prove the entire owned cover changed pixels and nothing outside it did."""
    _require(
        not before.isNull() and before.size() == covered.size(),
        "Invalid overlay baseline",
    )
    _require(
        0 <= left <= before.width() - 20 and 0 <= top <= before.height() - 20,
        "The owned overlay rectangle is clipped",
    )
    before = before.convertToFormat(QImage.Format.Format_RGB32)
    covered = covered.convertToFormat(QImage.Format.Format_RGB32)
    old, new = (
        memoryview(before.constBits()).cast("I"),
        memoryview(covered.constBits()).cast("I"),
    )
    width = before.width()
    color = new[top * width + left]
    for y in range(before.height()):
        for x in range(width):
            index = y * width + x
            if left <= x < left + 20 and top <= y < top + 20:
                _require(
                    new[index] == color and old[index] != new[index],
                    "Overlay was not fully rendered",
                )
            else:
                _require(
                    old[index] == new[index], "Pixels outside the owned overlay changed"
                )
    return covered.pixelColor(left, top).name()


def _close_overlay(handle: int) -> None:
    user = c.WinDLL("user32", use_last_error=True)
    user.DestroyWindow.argtypes, user.DestroyWindow.restype = (w.HWND,), w.BOOL
    _require(user.DestroyWindow(handle), "Could not remove the owned overlay")


def _row_labels(image: QImage) -> list[int | None]:
    """Read authored background landmarks without cropping the screenshot."""
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    pixels = memoryview(image.constBits()).cast("I")
    stride = image.bytesPerLine() // 4
    labels: list[int | None] = []
    for y in range(image.height()):
        color = Counter(pixels[y * stride : y * stride + image.width()]).most_common(1)[
            0
        ][0]
        row = ((color >> 16) & 0xFF) - 30
        labels.append(
            row
            if (color >> 8) & 0xFF == _GREEN
            and color & 0xFF == _BLUE
            and 0 <= row < _ROWS
            else None
        )
    return labels


def _boundary_rows(labels: list[int | None]) -> tuple[int, int]:
    content = [index for index, row in enumerate(labels) if row is not None]
    if not content:
        raise RuntimeError("No document landmarks were visible")
    return content[0], len(labels) - content[-1] - 1


def _verify_edge_rows(
    result: ScrollResult, output: Path, report: dict[str, object]
) -> None:
    """Verify all visible numbered rows, retaining original boundary chrome."""
    if result.image is None:
        raise RuntimeError(result.message)
    first = QImage(str(output / "frame-0.png"))
    top, bottom = _boundary_rows(_row_labels(first))
    labels = _row_labels(result.image)
    report["boundary_rows"] = {"top": top, "bottom": bottom}
    _require(
        _boundary_rows(labels) == (top, bottom),
        "The original panel boundary extent was not retained",
    )
    _require(
        top == 0
        or result.image.copy(0, 0, result.image.width(), top)
        == first.copy(0, 0, first.width(), top),
        "The original top boundary pixels were changed",
    )
    content = labels[top : len(labels) - bottom]
    _require(None not in content, "Unexpected chrome interrupts document rows")
    seen: list[int | None] = []
    for row in content:
        if not seen or seen[-1] != row:
            seen.append(row)
    _require(seen == list(range(_ROWS)), "Document rows are missing or out of order")
    counts = Counter(content)
    report["content_row_heights"] = [counts[row] for row in range(_ROWS)]
    report["first_content_row_height"] = counts[0]
    report["last_content_row_height"] = counts[_ROWS - 1]
    # The original top border may cover part of the first authored row. It is
    # retained visibly, not claimed as recovered hidden document pixels.
    _require(
        all(counts[row] == _LINE_HEIGHT - (top if row == 0 else 0) for row in seen),
        "Document row spans were lost or duplicated",
    )
    _require(
        result.image.height() == _ROWS * _LINE_HEIGHT + bottom,
        "Unexpected document and retained-chrome height",
    )


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


def _desktop_evidence(output: Path, report: dict[str, object]) -> None:
    """Retain the physical desktop bounds before touching the test document."""
    screens = MssScreenSource()
    try:
        frozen = screens.freeze()
        report["desktop"] = asdict(frozen.bounds)
        frozen.image.save(str(output / "desktop-before-discovery.png"))
    finally:
        screens.close()


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    window = _prepare(_window())
    report["window"] = asdict(window)
    _desktop_evidence(output, report)
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
    overlay = (
        _cover_panel(area, window.handle, output, report)
        if kind == "edge-covered"
        else None
    )
    try:
        result = _capture(point, output, kind)
    finally:
        if overlay is not None:
            _close_overlay(overlay)
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
    if kind in ("edge-cancel", "edge-input", "edge-covered"):
        report["movement"] = _verify_interruption(kind, result, initial, after, output)
        report["success"] = True
        return
    _require(
        after == initial,
        "The starting horizontal/vertical position was not restored",
    )
    _require(
        result.image is not None and result.restoration == "restored",
        result.message or result.restoration,
    )
    if kind.startswith("edge"):
        _verify_edge_rows(result, output, report)
    report["success"] = True


def _verify_interruption(
    kind: str,
    result: ScrollResult,
    initial: ScrollPosition,
    after: ScrollPosition,
    output: Path,
) -> dict[str, object]:
    """Validate terminal withholding and actual state without restoring in test."""
    movement = json.loads((output / "movement.json").read_text(encoding="utf-8"))
    if not isinstance(movement, dict):
        raise TypeError("Invalid native movement evidence")
    _require(result.image is None, "An interrupted capture published an image")
    _require(not (output / "result.png").exists(), "An interrupted image was saved")
    if kind == "edge-input":
        _require(
            "mouse or keyboard" in result.message, "Real input did not stop capture"
        )
        _require(
            result.restoration.startswith("not restored"),
            "Input was fought by restoration",
        )
        _require(
            movement == {"writes": 1, "last_vertical": 0.0},
            "Input allowed another provider write",
        )
        _require(
            after == ScrollPosition(initial.horizontal, 0.0, initial.view_size),
            "Input case did not retain the interrupted top state",
        )
    else:
        _require(after == initial, "Interrupted case did not preserve starting state")
        if kind == "edge-cancel":
            _require(result.restoration == "restored", "Cancellation did not restore")
            _require("cancelled" in result.message, "Cancellation was not observed")
            _require(
                movement == {"writes": 2, "last_vertical": initial.vertical},
                "Cancellation did not use exactly one restore write",
            )
        else:
            _require("covers" in result.message, "Small overlay was not rejected")
            _require(
                movement == {"writes": 0, "last_vertical": None},
                "Covered target received a provider write",
            )

    writes, last = movement.get("writes"), movement.get("last_vertical")
    if not isinstance(writes, int) or not isinstance(last, (float, type(None))):
        raise TypeError("Invalid native movement values")
    return {"writes": writes, "last_vertical": last}


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
        "oracle": (
            "interruption, withheld image and exact provider write/state outcome"
            if kind in ("edge-cancel", "edge-input", "edge-covered")
            else "ordered rows, complete interior spans, retained boundary chrome"
            if kind.startswith("edge")
            else "capture and restore smoke; screenshot content review required"
        ),
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
