"""Exercise the isolated helper against installed applications on hosted Windows.

Local HTML and text are ordinary document inputs, not foreign-process code or
application-specific runtime profiles. Exit nonzero when native feasibility
fails; diagnostic images and JSON never replace the result of the test.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QImage
from scripts.hosted_display_scale import target_dpi
from scripts.scrolling_header_probe import (
    probe_target,
    scroll_info,
    window_dpi,
)
from scripts.scrolling_item_witness import ListItemWitness
from scripts.scrolling_oracle import verify_sequence

from verdiclip.capture.grabber import MssScreenSource
from verdiclip.capture.scroll_helper import ScrollHelper
from verdiclip.capture.scroll_helper import main as helper_main
from verdiclip.capture.scrolling import (
    END_PERCENT,
    MAX_FRAMES,
    MAX_PIXELS,
    MAX_SECONDS,
    ScrollPosition,
    ScrollResult,
    ScrollTarget,
    ScrollTransaction,
)
from verdiclip.exceptions import CaptureError
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _ListSteps
from verdiclip.platform.windows import WindowInfo, WindowLocator

_ROWS = 120
_LINE_HEIGHT = 24
_CSS_DPI = 96
_EDGE_ROW_DPIS = (96, 120)
_EDGE_ROW_MODES = ("edge", "edge-nested")
_CONTENT_FIXTURE_DPI = 120
_OPAQUE_ALPHA = 0xFF
_CONTENT_FIXTURE_MODES = ("edge", "edge-prose-click", "edge-prose-enter")
_CONTENT_FIXTURE_SOURCES = {
    "edge": "3418cba923f78b445a150713236f915e8a0ed196fddfa6c75736428d7b5b24f5",
    "edge-prose-click": (
        "a2d42ad75d0ed8cd6fd144d15c7cdb6debe8b0510951218d0b984d8691b71fd4"
    ),
    "edge-prose-enter": (
        "a2d42ad75d0ed8cd6fd144d15c7cdb6debe8b0510951218d0b984d8691b71fd4"
    ),
}
_START_PERCENT = 37.0
_DIAGNOSTIC_FRAMES = 2
_WINDOW_WIDTH = 1000
_WINDOW_HEIGHT = 760
_GREEN = 80
_BLUE = 160
# Shared scheduling/acceptance deadline, not a preemptive filesystem timeout.
# The workflow owns the outer bound if a synchronous cleanup call stalls.
_CLEANUP_SECONDS = 20.0
_NATIVE_CONTROL_BAR = 2
_SHARING_VIOLATION = 32
_REPRESENTATIVE_PREFIXES = (
    "edge-prose-",
    "edge-panel-",
    "edge-sidebar-",
    "edge-background-",
    "explorer-",
)


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
        report["owned_fixture_sha256"] = hashlib.sha256(
            path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
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
    report["executable"] = str(executable)
    _record_version(executable, report)
    return subprocess.Popen(command)  # noqa: S603 - fixed installed apps and owned content


def _record_version(executable: Path, report: dict[str, object]) -> None:
    """Record optional file metadata without blocking the actual application test."""
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    literal = str(executable).replace("'", "''")
    try:
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
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        report["version"] = None
        report["version_error"] = f"{type(error).__name__}: {error}"


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


def _target_evidence(
    target: UiaScrollTarget, output: Path, mode: str, selected: dict[str, object]
) -> ListItemWitness | None:
    """Record extra geometry only for the explicitly owned representative cases."""
    if mode not in _EDGE_ROW_MODES and not mode.startswith(_REPRESENTATIVE_PREFIXES):
        return None
    selected["initial_position"] = asdict(target.position())
    selected["window_dpi"] = window_dpi(target)
    if mode in _CONTENT_FIXTURE_MODES:
        bounds = target._provider_bounds
        selected["provider_bounds"] = (
            asdict(bounds) if isinstance(bounds, Rect) else None
        )
    if mode.startswith("explorer-"):
        geometry = probe_target(target)
        (output / "header-geometry.json").write_text(
            json.dumps(geometry, indent=2), encoding="utf-8"
        )
        selected["initial_native"] = _restoration_native(target, geometry)
        if asdict(target.position()) != selected["initial_position"]:
            raise CaptureError("The initial restoration position changed")
        target.check()
        return ListItemWitness(target, geometry)
    return None


def _frame_scroll_info(
    target: UiaScrollTarget, position: ScrollPosition, geometry: dict[str, object]
) -> list[dict[str, object]]:
    """Tie read-only native metadata to the just-acquired stable frame position."""
    target.check()
    if target.position() != position:
        raise CaptureError("The scroll panel moved before native metadata capture")
    result = scroll_info(target, geometry)
    if target.position() != position:
        raise CaptureError("The scroll panel moved during native metadata capture")
    target.check()
    return result


def _frame_observation(
    operation: ScrollTransaction,
    position: ScrollPosition,
    evidence: ListItemWitness | None,
    mode: str,
    *,
    previous_view: float | None = None,
) -> dict[str, object]:
    """Extend only Explorer's existing frame records with native metadata."""
    observation: dict[str, object] = {
        "horizontal": position.horizontal,
        "vertical": position.vertical,
        "view_size": position.view_size,
    }
    if mode.startswith("explorer-"):
        if evidence is None or evidence.target is not operation._target:
            raise CaptureError("Native scroll evidence target was not retained")
        observation["native_scroll_info"] = _frame_scroll_info(
            evidence.target, position, evidence.geometry
        )
        observation["native_item_witness"] = evidence.observe(position)
        if position.vertical == END_PERCENT or (
            previous_view is not None and position.view_size != previous_view
        ):
            observation["native_scroll_info_after"] = _restoration_native(
                evidence.target, evidence.geometry
            )
            if evidence.target.position() != position:
                raise CaptureError("The frame moved during repeated native observation")
            evidence.target.check()
    return observation


def _restoration_native(
    target: UiaScrollTarget, geometry: dict[str, object]
) -> list[dict[str, object]]:
    """Independently read the retained probe HWNDs, without product restore proof."""
    position = target.position()
    rows = _frame_scroll_info(target, position, geometry)
    getter = target._user.GetParent
    getter.argtypes, getter.restype = (w.HWND,), w.HWND
    for row in rows:
        handle = row["native_handle"]
        pid = w.DWORD()
        target._user.GetWindowThreadProcessId(handle, c.byref(pid))
        row.update(
            parent=int(getter(handle) or 0),
            root=int(target._user.GetAncestor(handle, 2) or 0),
            pid=int(pid.value),
        )
    if (
        position != target.position()
        or rows != _native_repeat(target, geometry, rows)
        or position != target.position()
    ):
        raise CaptureError("The native restoration observation changed")
    target.check()
    return rows


def _native_repeat(
    target: UiaScrollTarget,
    geometry: dict[str, object],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Recheck scalar identity and native values around the independent read."""
    repeated = scroll_info(target, geometry)
    for row, original in zip(repeated, rows, strict=True):
        handle = row["native_handle"]
        pid = w.DWORD()
        target._user.GetWindowThreadProcessId(handle, c.byref(pid))
        row.update(
            parent=int(target._user.GetParent(handle) or 0),
            root=int(target._user.GetAncestor(handle, 2) or 0),
            pid=int(pid.value),
        )
        if row != original:
            raise CaptureError("The native restoration observation changed")
    return repeated


def _record_failure_position(target: ScrollTarget, selected: dict[str, object]) -> None:
    """Keep a guarded final sample, never a settling or restoration assertion."""
    try:
        target.check()
        observed = asdict(target.position())
        target.check()
    except Exception as error:  # noqa: BLE001 - diagnostics cannot replace failure
        selected["final_observation_error"] = str(error)
    else:
        selected["final_observed_position"] = observed


def _failure_native_identity(rows: list[dict[str, object]], original: object) -> None:
    """Require the original native owners, not merely two equal recycled HWNDs."""
    keys = ("native_handle", "bar", "parent", "root", "pid")
    required = [row for row in rows if row.get("bar") == _NATIVE_CONTROL_BAR]
    if (
        not isinstance(original, list)
        or len(rows) != len(original)
        or not rows
        or not required
        or any(row.get("success") is not True for row in required)
        or any(
            not isinstance(before, dict)
            or any(row.get(key) != before.get(key) for key in keys)
            or row.get("window_valid") is not True
            or row.get("root_matches") is not True
            for row, before in zip(rows, original, strict=True)
        )
    ):
        raise CaptureError("Failure native identity is unavailable or changed")


def _record_terminal_position(
    target: ScrollTarget,
    result: ScrollResult,
    selected: dict[str, object],
    output: Path,
    evidence: ListItemWitness | None = None,
) -> None:
    """Observe terminal metadata without changing the transaction's outcome."""
    if result.image is None:
        _record_failure_position(target, selected)
        return
    if selected.get("mode") in _EDGE_ROW_MODES:
        selected["final_window_dpi"] = window_dpi(cast("UiaScrollTarget", target))
    if result.restoration == "restored":
        target.check()
        final = asdict(target.position())
        target.check()
        state = {"initial": selected["initial_position"], "final": final}
        if evidence is not None:
            if evidence.target is not target:
                raise CaptureError("Native restoration target was not retained")
            state.update(
                initial_native=selected["initial_native"],
                final_native=_restoration_native(evidence.target, evidence.geometry),
            )
            if asdict(target.position()) != final:
                raise CaptureError("The final restoration position changed")
            target.check()
        (output / "restoration.json").write_text(
            json.dumps(state),
            encoding="utf-8",
        )


def _count_list_call[**P, T](
    function: Callable[P, T], counts: dict[str, int]
) -> Callable[P, T]:
    """Prove List capability selection without timing or extra provider reads."""

    def observed(*args: P.args, **kwargs: P.kwargs) -> T:
        counts["count"] += 1
        try:
            return function(*args, **kwargs)
        except Exception:
            counts["exceptions"] += 1
            raise

    return observed


def _save_frame(
    frame: QImage,
    observation: dict[str, object],
    observations: list[dict[str, object]],
    output: Path,
) -> None:
    """Retain accepted frames and metadata in their original order."""
    if not frame.save(str(output / f"frame-{len(observations)}.png")):
        raise OSError("Could not save the native scrolling diagnostic frame")
    observations.append(observation)

    (output / "frames.json").write_text(
        json.dumps(observations, indent=2), encoding="utf-8"
    )


def _record_setter(
    writes: list[float], percent: float, before_write: Callable[[], None] | None
) -> None:
    """Count admitted/possibly issued setters, not failed preflight calls."""
    if before_write is not None:
        before_write()
    writes.append(percent)


def _trace_helper() -> None:
    """Save bounded native evidence in this owned test child, never production."""
    logging.basicConfig(level=logging.INFO)
    mode = sys.argv.pop()
    output = Path(sys.argv.pop())
    original, run = ScrollTransaction._frame, ScrollTransaction.run
    move = UiaScrollTarget.move
    opening = UiaScrollTarget._open
    selected: dict[str, object] = {}
    observations: list[dict[str, object]] = []
    evidence: ListItemWitness | None = None
    writes: list[float] = []
    complete_sequence = mode in _EDGE_ROW_MODES or mode.startswith(
        _REPRESENTATIVE_PREFIXES
    )
    frame_limit = MAX_FRAMES if complete_sequence else _DIAGNOSTIC_FRAMES
    retained_pixels = 0
    list_calls = {name: {"count": 0, "exceptions": 0} for name in ("begin", "finish")}

    def record_open(
        target: UiaScrollTarget,
        point: Point,
        *,
        window: int | None = None,
        input_tick: int | None = None,
    ) -> None:
        nonlocal evidence
        opening(target, point, window=window, input_tick=input_tick)
        selected.update(
            mode=mode,
            window=target._window,
            input_tick=target._input_tick,
            area=asdict(target.area),
        )
        evidence = _target_evidence(target, output, mode, selected)

    def record_move(
        target: UiaScrollTarget,
        percent: float,
        *,
        before_write: Callable[[], None] | None = None,
    ) -> None:
        move(
            target,
            percent,
            before_write=partial(_record_setter, writes, percent, before_write),
        )

    def record_result(operation: ScrollTransaction) -> ScrollResult:
        result = run(operation)
        if complete_sequence:
            _record_terminal_position(
                operation._target, result, selected, output, evidence
            )
        return result

    def record_frame(operation: ScrollTransaction, position: ScrollPosition) -> QImage:
        nonlocal retained_pixels
        frame = original(operation, position)
        if len(observations) < frame_limit:
            retained_pixels += frame.width() * frame.height()
            if retained_pixels > MAX_PIXELS:
                raise CaptureError("Diagnostic frame evidence exceeds its pixel limit")
            observation = _frame_observation(
                operation,
                position,
                evidence,
                mode,
                previous_view=(
                    cast("float", observations[-1]["view_size"])
                    if observations
                    else None
                ),
            )
            _save_frame(frame, observation, observations, output)
            if len(observations) == 1 and mode in (
                "edge-cancel",
                "edge-input",
                "edge-app-escape",
            ):
                # Give the parent a deterministic confirmed-state boundary.
                # The real stdin watcher/input check remains unchanged.
                time.sleep(0.4)
        return frame

    try:
        with (
            patch.object(
                _ListSteps,
                "begin",
                _count_list_call(_ListSteps.begin, list_calls["begin"]),
            ),
            patch.object(
                _ListSteps,
                "finish",
                _count_list_call(_ListSteps.finish, list_calls["finish"]),
            ),
            patch.object(ScrollTransaction, "_frame", record_frame),
            patch.object(ScrollTransaction, "run", record_result),
            patch.object(UiaScrollTarget, "move", record_move),
            patch.object(UiaScrollTarget, "_open", record_open),
        ):
            helper_main()
    finally:
        # Missing evidence fails qualification, never replaces the capture error.
        with suppress(OSError):
            (output / "list-calls.json").write_text(
                json.dumps(list_calls), encoding="utf-8"
            )
        (output / "target.json").write_text(json.dumps(selected), encoding="utf-8")
        (output / "movement.json").write_text(
            json.dumps(
                {"writes": len(writes), "last_vertical": writes[-1] if writes else None}
            ),
            encoding="utf-8",
        )


def _traced_helper(output: Path, mode: str) -> ScrollHelper:
    """Use the same owned child, adding only existing test-frame diagnostics."""
    helper = ScrollHelper()
    start = helper._process.start

    def trace_start(*_args: object, **_kwargs: object) -> None:
        script = (
            "from scripts.simple_scroll_native import _trace_helper; _trace_helper()"
        )
        arguments = helper._process.arguments()[2:]
        helper._process.setArguments(
            ["-c", script, *arguments, str(output.resolve()), mode]
        )
        start()

    tracing = patch.object(helper._process, "start", trace_start)
    tracing.start()
    helper.finished.connect(lambda _result: tracing.stop())
    return helper


def _capture(  # noqa: PLR0913 - test-only factory and optional lifecycle receipt
    point: Point,
    output: Path,
    mode: str,
    *,
    window: int,
    input_tick: int,
    helper_factory: Callable[[], ScrollHelper] | None = None,
    lifecycle: dict[str, object] | None = None,
) -> ScrollResult:
    loop = QEventLoop()
    helper = (
        _traced_helper(output, mode) if helper_factory is None else helper_factory()
    )
    results: list[ScrollResult] = []

    def completed(result: ScrollResult) -> None:
        if lifecycle is not None:
            lifecycle["start_to_finished_seconds"] = time.monotonic() - started
            lifecycle["clock"] = (
                "helper.start to actual helper.finished; not transaction"
            )
        results.append(result)
        loop.quit()

    helper.finished.connect(completed)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(round((MAX_SECONDS + 5.0) * 1000))
    started = time.monotonic()
    helper.start(point, window=window, input_tick=input_tick)
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
        helper.shutdown()
        raise RuntimeError("The helper did not produce its bounded terminal result")
    (output / "helper.log").write_text(
        helper.diagnostics,
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


def _edge_row_height(output: Path, report: dict[str, object]) -> int:
    """Scale authored CSS, never measured row spans, for the two qualified cases."""
    if report.get("application") not in _EDGE_ROW_MODES:
        return _LINE_HEIGHT
    selected = json.loads((output / "target.json").read_text(encoding="utf-8"))
    scale = os.environ.get("DISPLAY_SCALE")
    expected = _CSS_DPI if scale is None else target_dpi(int(scale))
    dpi = selected.get("window_dpi")
    report.update(expected_target_dpi=expected, observed_target_dpi=dpi)
    if (
        type(dpi) is not int
        or dpi not in _EDGE_ROW_DPIS
        or dpi != expected
        or type(selected.get("final_window_dpi")) is not int
        or selected["final_window_dpi"] != dpi
        or selected.get("mode") != report["application"]
    ):
        raise RuntimeError("Independent target DPI does not match the qualified scale")
    height, remainder = divmod(_LINE_HEIGHT * dpi, _CSS_DPI)
    _require(not remainder, "Authored CSS row height is not an integral physical span")
    report["physical_row_height"] = height
    return height


def _fixture_pixels(image: QImage) -> list[int]:
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    pixels = list(memoryview(image.constBits()).cast("I"))
    _require(
        all(pixel >> 24 == _OPAQUE_ALPHA for pixel in pixels),
        "Fixture pixels are not opaque",
    )
    return pixels


def _fixture_difference(before: QImage, after: QImage) -> dict[str, object]:
    """Keep whole-raster equality distinct from the narrower document check."""
    _require(
        not before.isNull() and before.size() == after.size(), "Fixture size changed"
    )
    differences = [
        {"x": i % before.width(), "y": i // before.width(), "before": a, "after": b}
        for i, (a, b) in enumerate(
            zip(_fixture_pixels(before), _fixture_pixels(after), strict=True)
        )
        if a != b
    ]
    return {
        "whole_viewport_equal": not differences,
        "viewport_differences_argb": differences,
    }


def _fixture_footprint(
    before: QImage, after: QImage, member: Callable[[int, int], bool]
) -> int:
    """Require stable source-derived membership, exact complement and no islands."""
    old, new = _fixture_pixels(before), _fixture_pixels(after)
    footprint = {i for i, pixel in enumerate(old) if member(i, pixel)}
    _require(
        footprint == {i for i, pixel in enumerate(new) if member(i, pixel)}
        and all(
            a == b or i in footprint
            for i, (a, b) in enumerate(zip(old, new, strict=True))
        ),
        "Fixture paint or nonpaint footprint changed",
    )
    width, height = before.width(), before.height()
    reached = {
        i
        for i in footprint
        if i % width in (0, width - 1) or i // width in (0, height - 1)
    }
    pending = list(reached)
    while pending:
        i = pending.pop()
        neighbors = [i - width, i + width]
        if i % width:
            neighbors.append(i - 1)
        if i % width < width - 1:
            neighbors.append(i + 1)
        for neighbor in neighbors:
            if neighbor in footprint and neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    _require(reached == footprint, "Fixture nonpaint has an interior island")
    return len(footprint)


def _fixture_context(
    output: Path,
    report: dict[str, object],
    before: QImage,
    after: QImage,
    *,
    mode: str,
) -> dict[str, object]:
    """Fail closed without actual owned-source and unclipped native geometry proof."""
    report.update(_fixture_difference(before, after))
    _require(
        mode in _CONTENT_FIXTURE_SOURCES
        and report.get("owned_fixture_sha256") == _CONTENT_FIXTURE_SOURCES[mode],
        "Restored content lacks the exact owned fixture source",
    )
    target = json.loads((output / "target.json").read_text(encoding="utf-8"))
    state = json.loads((output / "restoration.json").read_text(encoding="utf-8"))
    area = Rect(**target["area"])
    _require(
        target.get("mode") == mode
        and type(target.get("window_dpi")) is int
        and target.get("window_dpi") == _CONTENT_FIXTURE_DPI
        and target.get("final_window_dpi", _CONTENT_FIXTURE_DPI) == _CONTENT_FIXTURE_DPI
        and target["initial_position"] == state["initial"] == state["final"]
        and state["initial"]["horizontal"] == -1.0
        and (before.width(), before.height()) == (area.width, area.height)
        and target.get("provider_bounds") == target["area"],
        "Controlled fixture source, DPI, state or unclipped geometry is unproved",
    )
    return cast("dict[str, object]", target)


def _fixture_row_offset(image: QImage, height: int) -> int:
    """Derive integer source coordinates from every complete authored ID boundary."""
    labels = _row_labels(image)
    top, bottom = _boundary_rows(labels)
    end = len(labels) - bottom
    boundaries = [
        (y, row)
        for y, row in enumerate(labels)
        if y > top and row is not None and labels[y - 1] != row
    ]
    _require(
        len(boundaries) >= _DIAGNOSTIC_FRAMES,
        "Insufficient complete authored row boundaries",
    )
    offsets = {height * row - y for y, row in boundaries}
    _require(len(offsets) == 1, "Ambiguous authored row offset or phase")
    offset = offsets.pop()
    _require(
        offset >= 0
        and all(
            row == (offset + y) // height
            for y, row in enumerate(labels)
            if top <= y < end
        ),
        "Authored row IDs, order or phase changed",
    )
    return offset


def _fixture_row_paint(pixel: int, row: int) -> bool:
    """Opaque black glyph coverage cannot exceed its authored RGB background."""
    return (
        ((pixel >> 16) & 255) <= row + 30
        and ((pixel >> 8) & 255) <= _GREEN
        and (pixel & 255) <= _BLUE
    )


def _verify_table_content(
    before: QImage, after: QImage, frames: list[QImage], *, height: int
) -> dict[str, object]:
    offset = _fixture_row_offset(before, height)
    _require(
        _fixture_row_offset(after, height) == offset, "Document row position changed"
    )
    count = _fixture_footprint(
        before,
        after,
        lambda i, pixel: (
            not _fixture_row_paint(pixel, (offset + i // before.width()) // height)
        ),
    )
    origins = [_fixture_row_offset(frame, height) for frame in frames]
    boundaries = [_boundary_rows(_row_labels(frame)) for frame in frames]
    _require(origins == sorted(set(origins)), "Raw frame document order changed")
    witnesses = []
    for i, (old, new) in enumerate(
        zip(_fixture_pixels(before), _fixture_pixels(after), strict=True)
    ):
        if old == new:
            continue
        x, document_y = i % before.width(), offset + i // before.width()
        row = document_y // height
        samples = []
        for index, (frame, origin) in enumerate(zip(frames, origins, strict=True)):
            y = document_y - origin
            row_top = row * height - origin
            top, bottom = boundaries[index]
            if top <= row_top and row_top + height <= frame.height() - bottom:
                pixel = frame.pixel(x, y)
                if _fixture_row_paint(pixel, row):
                    samples.append((index, pixel))
        _require(
            len(samples) >= _DIAGNOSTIC_FRAMES
            and len({pixel for _, pixel in samples}) == 1,
            "Missing or disagreeing direct document witnesses",
        )
        witnesses.append({"x": x, "document_y": document_y, "frames_and_argb": samples})
    return {
        "document_offset": offset,
        "raw_frame_offsets": origins,
        "nonpaint_pixels": count,
        "document_witnesses": witnesses,
    }


def _verify_edge_sequence(
    result: ScrollResult, output: Path, report: dict[str, object]
) -> None:
    """Require complete independent pixels and exact original footer retention."""
    if result.image is None:
        raise RuntimeError(result.message)
    first = QImage(str(output / "frame-0.png"))
    _, bottom = _boundary_rows(_row_labels(first))
    _require(
        bottom == 0
        or result.image.copy(
            0, result.image.height() - bottom, result.image.width(), bottom
        )
        == first.copy(0, first.height() - bottom, first.width(), bottom),
        "The original bottom boundary pixels were changed",
    )
    before, after = (QImage(str(output / name)) for name in ("before.png", "after.png"))
    report.update(_fixture_difference(before, after))
    report.update(verify_sequence(result.image, output))
    _require(
        report["oracle_frames"] == result.frames,
        "Whole-image oracle did not retain every accepted frame",
    )
    if before == after:
        report["document_content_restored"] = True
        return
    _require(
        report.get("application") == "edge" and result.restoration == "restored",
        "Restored pixels differ from the original middle-start view",
    )
    target = _fixture_context(
        output,
        report,
        before,
        after,
        mode="edge",
    )
    _require(
        target.get("final_window_dpi") == _CONTENT_FIXTURE_DPI,
        "Final fixture DPI is unproved",
    )
    frames = [QImage(str(output / f"frame-{i}.png")) for i in range(result.frames)]
    _require(
        all(frame.size() == before.size() for frame in frames),
        "Raw fixture frame size changed",
    )
    proof = _verify_table_content(
        before, after, frames, height=_LINE_HEIGHT * _CONTENT_FIXTURE_DPI // _CSS_DPI
    )
    origins = cast("list[int]", proof["raw_frame_offsets"])
    _require(
        origins[0] == 0 and origins[-1] + after.height() == result.image.height(),
        "Authored row coordinates do not reach both document endpoints",
    )
    report.update(proof)
    report["document_content_restored"] = True


def _verify_edge_rows(
    result: ScrollResult, output: Path, report: dict[str, object]
) -> None:
    """Verify all visible numbered rows, retaining original boundary chrome."""
    if result.image is None:
        raise RuntimeError(result.message)
    height = _edge_row_height(output, report)
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
        all(counts[row] == height - (top if row == 0 else 0) for row in seen),
        "Document row spans were lost or duplicated",
    )
    _require(
        result.image.height() == _ROWS * height + bottom,
        "Unexpected document and retained-chrome height",
    )
    if report.get("application") in _EDGE_ROW_MODES:
        _verify_edge_sequence(result, output, report)


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
        result = _capture(
            point, output, kind, window=window.handle, input_tick=target._input_tick
        )
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


def main(
    *,
    gui: bool = False,
    case: Callable[[str, Path, dict[str, object]], None] | None = None,
) -> None:
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
    if gui:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        app = QApplication(sys.argv[:1])
        app.setQuitOnLastWindowClosed(False)
    else:
        app = QCoreApplication(sys.argv[:1])
    app.setApplicationName("VerdiClip scrolling qualification")
    report: dict[str, object] = {
        "application": kind,
        "success": False,
        "oracle": (
            "interruption, withheld image and exact provider write/state outcome"
            if kind
            in (
                "edge-cancel",
                "edge-app-escape",
                "edge-input",
                "edge-covered",
                "edge-select-input",
                "edge-select-focus",
            )
            else "ordered rows, complete interior spans, retained boundary chrome"
            if kind.startswith("edge")
            else "capture and restore smoke; screenshot content review required"
        ),
    }
    try:
        scratch = TemporaryDirectory(prefix="verdiclip-scroll-test-", delete=False)
        process = None
        try:
            process = _launch(kind, Path(scratch.name), report)
            (case or _case)(kind, output, report)
        finally:
            deadline = time.monotonic() + _CLEANUP_SECONDS
            try:
                window = report.get("window")
                handle = window.get("handle") if isinstance(window, dict) else None
                _stop_owned_process(
                    process,
                    window=handle if isinstance(handle, int) else None,
                    deadline=deadline,
                )
                _cleanup_owned_directory(scratch, deadline=deadline)
            except (OSError, subprocess.SubprocessError) as error:
                report["cleanup_error"] = str(error)
                if isinstance(error, subprocess.CalledProcessError):
                    report["cleanup_stdout"] = (
                        error.stdout.decode("utf-8", errors="replace")
                        if error.stdout
                        else ""
                    )
                    report["cleanup_stderr"] = (
                        error.stderr.decode("utf-8", errors="replace")
                        if error.stderr
                        else ""
                    )
                raise
    except Exception as error:
        report["success"] = False
        report["error"] = str(error)
        raise
    finally:
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )


def _cleanup_remaining(deadline: float) -> float:
    """Reject cleanup work or success after the shared shutdown deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Owned directory cleanup exceeded the shutdown deadline")
    return remaining


def _cleanup_owned_directory(
    directory: TemporaryDirectory[str], *, deadline: float
) -> None:
    """Retry only owned-file sharing violations within the shutdown deadline."""
    while True:
        _cleanup_remaining(deadline)
        try:
            directory.cleanup()
        except OSError as error:
            if getattr(error, "winerror", None) != _SHARING_VIOLATION:
                raise
            remaining = _cleanup_remaining(deadline)
            time.sleep(min(0.1, remaining))
            _cleanup_remaining(deadline)
        else:
            _cleanup_remaining(deadline)
            return


def _request_owned_close(process: subprocess.Popen[bytes], window: int) -> bool:
    """Post ordinary close only to a verified window of this live owned process."""
    user = c.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD
    user.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    user.PostMessageW.restype = w.BOOL
    pid = w.DWORD()
    if (
        not user.GetWindowThreadProcessId(window, c.byref(pid))
        or pid.value != process.pid
        or process.poll() is not None
    ):
        return False  # The existing owned-PID fallback remains available.
    if not user.PostMessageW(window, 0x0010, 0, 0):  # WM_CLOSE; never SendMessage
        raise OSError("Could not close the owned test application window")
    return True


def _stop_owned_process(
    process: subprocess.Popen[bytes] | None,
    *,
    window: int | None = None,
    deadline: float | None = None,
) -> None:
    """Close gracefully, then force only the owned PID within one existing budget."""
    if process is None or process.poll() is not None:
        return
    if deadline is None:
        deadline = time.monotonic() + _CLEANUP_SECONDS
    if window is not None and _request_owned_close(process, window):
        try:
            process.wait(timeout=min(3.0, max(0.0, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass  # One close request, then the existing owned-process fallback.
        else:
            return
    if process.poll() is not None:
        return
    subprocess.run(  # noqa: S603 - only the process owned by this test
        [
            str(Path(os.environ["WINDIR"]) / "System32/taskkill.exe"),
            "/PID",
            str(process.pid),
            "/T",
            "/F",
        ],
        check=True,
        timeout=min(10.0, max(0.0, deadline - time.monotonic())),
        capture_output=True,
    )
    process.wait(timeout=max(0.0, deadline - time.monotonic()))


if __name__ == "__main__":
    main()
