"""Native diagnostic samples with ordinary prose, not colored pixel landmarks.

Run only on the existing disposable hosted Windows desktop. This reuses the
actual UIA helper and exact PNG acquisition. A successful capture is only a
capture/restoration smoke result, not an independent completeness proof.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import html
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import override
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from scripts import simple_scroll_app as app
from scripts import simple_scroll_native as native
from scripts.scrolling_oracle import verify_sequence
from scripts.simple_scroll_handoff import Desktop

from verdiclip.capture.scroll_helper import _watch_parent
from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition, ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget
from verdiclip.platform.windows import WindowInfo, WindowLocator

_WAIT_TIMEOUT = 0x00000102
_MIDDLE_SETUP_RANGE = 5.0
_EXPLORER_STARTS = {
    "explorer-middle-click": 50.0,
    "explorer-middle-enter": 50.0,
    "explorer-bottom-click": END_PERCENT,
    "explorer-bottom-enter": END_PERCENT,
}


def document(layout: str) -> str:
    """Lay out project documentation as normal prose with realistic boundaries."""
    source = Path("docs/design/philosophy.md").read_text(encoding="utf-8")
    paragraphs = []
    for block in source.split("\n\n"):
        if not block.strip() or block.startswith(("```", "|", "---")):
            continue
        if block.startswith("#"):
            paragraphs.append(f"<h2>{html.escape(block.lstrip('# ').strip())}</h2>")
        else:
            paragraphs.append(f"<p>{html.escape(block)}</p>")
    article = "<article>" + "".join(paragraphs) + "</article>"
    if layout == "edge-panel":
        article = (
            "<h1>Project design notes</h1><p>Scroll the document panel below.</p>"
            '<section tabindex="0" aria-label="Design document">'
            + article
            + '</section><p class="outside">Related project resources</p>'
        )
    elif layout == "edge-sidebar":
        article += (
            "<aside><h2>On this page</h2><p>Purpose and principles</p>"
            "<p>Architecture</p><p>Testing strategy</p></aside>"
        )
    return (
        '<!doctype html><meta charset="utf-8">'
        "<title>VerdiClip scrolling check</title><style>"
        "body{margin:0;background:#fff;color:#24292f;font:15px/1.45 Arial}"
        "article{max-width:650px;margin:0 auto;padding:24px}"
        "h1,h2{font-weight:600;line-height:1.2;margin:1.6em 0 .7em}"
        "p{margin:0 0 1.1em;white-space:pre-line}"
        "section{height:385.5px;width:740.5px;margin:24px;"
        "max-width:calc(100% - 78px);max-height:calc(100vh - 206px);"
        "border:3px solid #b1b5ba;padding:12px;overflow:scroll}"
        "section article{width:820px;max-width:none;margin:0}"
        "body>h1,body>p{margin-left:24px}.outside{height:800px}"
        "@media(max-height:500px){"
        "body>h1{font-size:18px;margin-top:8px;margin-bottom:4px}"
        "body>p{font-size:12px;line-height:1.2;margin-bottom:8px}"
        "section{margin-top:8px;max-height:calc(100vh - 110px)}}"
        "aside{position:fixed;right:15px;top:24px;width:170px;"
        "background:#f6f8fa;padding:12px;font-size:13px}"
        + ("article{margin-left:0;max-width:610px}" if layout == "edge-sidebar" else "")
        + "</style>"
        + article
    )


def _inspect_result(
    result: ScrollResult, output: Path, report: dict[str, object]
) -> None:
    if result.image is None:
        raise RuntimeError(result.message)
    report.update(verify_sequence(result.image, output))
    native._require(
        report["oracle_frames"] == result.frames,
        "Whole-image oracle did not retain every accepted frame",
    )


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    report["oracle"] = "capture/restoration smoke only; inspect exact PNG content"
    report["gesture_hold_ms"] = 120
    if kind in _EXPLORER_STARTS:
        ExplorerStartCapture(kind, output, report).run()
    else:
        app._case(kind, output, report)


def _verify_restored(capture: app.AppCapture, after: QImage) -> None:
    """Separate exact scroll-state restoration from the measured panel outline."""
    if capture.result is not None and capture.result.image is not None:
        native._require(
            capture.result.image.save(str(capture.output / "result.png")),
            "Could not retain the complete image before restoration checks",
        )
    state = json.loads(
        (capture.output / "restoration.json").read_text(encoding="utf-8")
    )
    native._require(state["initial"] == state["final"], "Scroll state was not restored")
    capture.report["restored_scroll_state"] = state
    first = QImage(str(capture.output / "frame-0.png"))
    native._require(first.size() == after.size(), "Restored panel geometry changed")
    if first == after:
        capture.report["restored_pixels"] = "exact"
        return
    # This owned CSS fixture's measured focus outline changes within its outer
    # four pixels (including rounded corners). All interior pixels stay exact.
    perimeter = 4
    native._require(
        capture.kind.startswith("edge-panel-")
        and first.copy(
            perimeter,
            perimeter,
            first.width() - 2 * perimeter,
            first.height() - 2 * perimeter,
        )
        == after.copy(
            perimeter,
            perimeter,
            after.width() - 2 * perimeter,
            after.height() - 2 * perimeter,
        ),
        "Restored content pixels differ beyond the measured fixture perimeter",
    )
    capture.report["restored_pixels"] = "exact interior; focus perimeter changed"
    capture.report["restored_perimeter_changed_pixels"] = sum(
        first.pixel(x, y) != after.pixel(x, y)
        for y in range(first.height())
        for x in range(first.width())
    )


class HumanDesktop(Desktop):
    """Separate press and release with event processing, as physical input does."""

    @override
    def click(
        self, widget: QWidget, point: Point, *, enter: bool = False, hold_ms: int = 120
    ) -> None:
        """Complete a held gesture on the already-verified owned test widget."""
        super().click(widget, point, enter=enter, hold_ms=hold_ms)


class ExplorerStartCapture(app.AppCapture):
    """Prepare only owned Explorer variants before the actual chooser gesture."""

    @override
    def _settle_target_foreground(self) -> None:
        super()._settle_target_foreground()
        native._require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Explorer start setup requires a disposable hosted runner",
        )
        # ExplorerCase patches this lookup to recheck the exact folder/HWND/PID.
        native._require(
            app._window() == self.window, "The owned Explorer setup window changed"
        )
        request = {
            "kind": self.kind,
            "point": asdict(self.point),
            "window": asdict(self.window),
            "pid": self.report["explorer_window_pid"],
            "fixture": self.report["explorer_fixture"],
        }
        (self.output / "setup-request.json").write_text(
            json.dumps(request), encoding="utf-8"
        )
        evidence = self.output / "setup.json"
        evidence.write_text("{}", encoding="utf-8")
        try:
            _run_explorer_setup(self.output)
        finally:
            self.report["explorer_start_setup"] = json.loads(
                evidence.read_text(encoding="utf-8")
            )
        setup = json.loads(evidence.read_text(encoding="utf-8"))
        native._require(
            app._window() == self.window, "The owned Explorer setup window changed"
        )
        self.start_position = ScrollPosition(**setup["settled_position"])
        self.start_area = Rect(**setup["area"])
        native._require(
            not QImage(str(self.output / "before.png")).isNull(),
            "The Explorer setup child did not retain its initial PNG",
        )

    @override
    def _verify_restored(self, after: QImage) -> None:
        state = json.loads(
            (self.output / "restoration.json").read_text(encoding="utf-8")
        )
        target = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        native._require(
            state["initial"] == state["final"] == asdict(self.start_position)
            and target["initial_position"] == state["initial"]
            and target["area"] == asdict(self.start_area),
            "Explorer did not restore the actual starting scroll state",
        )
        before = QImage(str(self.output / "before.png"))
        native._require(
            not before.isNull() and before == after,
            "Explorer did not restore the actual starting pixels",
        )
        self.report.update(restored_scroll_state=state, restored_pixels="exact")

    @override
    def _terminal(self, result: ScrollResult) -> None:
        if result.image is not None:
            native._require(
                result.image.save(str(self.output / "result.png")),
                "Could not retain the complete image before restoration checks",
            )
        try:
            super()._terminal(result)
        except RuntimeError:
            if self.kind.startswith("explorer-bottom-") and result.image is None:
                self._verify_rejected_start()
            raise  # A withheld image remains a failed product diagnostic.

    def _verify_rejected_start(self) -> None:
        target = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        movement = json.loads(
            (self.output / "movement.json").read_text(encoding="utf-8")
        )
        before = QImage(str(self.output / "before.png"))
        after = QImage(str(self.output / "after.png"))
        evidence = {
            "zero_production_writes": movement == {"writes": 0, "last_vertical": None},
            "unchanged_state": target.get("initial_position")
            == target.get("final_observed_position")
            == asdict(self.start_position)
            and target.get("area") == asdict(self.start_area),
            "unchanged_pixels": not before.isNull() and before == after,
        }
        self.report["rejected_start_evidence"] = evidence
        native._require(
            all(evidence.values()),
            "Rejected Explorer start did not preserve zero writes/state/pixels",
        )


def _run_explorer_setup(output: Path) -> None:
    """Keep one child owned through a 20-second wait and five-second kill/reap."""
    script = (
        "from scripts.scrolling_diagnostics import _setup_explorer_child; "
        "_setup_explorer_child()"
    )
    with (output / "setup.log").open("wb") as log:
        process = subprocess.Popen(  # noqa: S603 - fixed owned diagnostic child
            [sys.executable, "-c", script, str(output.resolve())],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            native._require(
                process.wait(timeout=20) == 0, "Explorer setup child failed"
            )
        finally:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
            finally:
                if process.stdin is not None:
                    process.stdin.close()


def _setup_explorer_child() -> None:
    """Initialize UIA only in a fresh process without a QApplication/OLE thread."""
    threading.Thread(
        target=_watch_parent, args=(threading.Event(),), daemon=True
    ).start()
    output = Path(sys.argv[1])
    setup: dict[str, object] = {}
    try:
        user = c.WinDLL("user32", use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes = (c.c_void_p,)
        user.SetProcessDpiAwarenessContext.restype = w.BOOL
        native._require(
            user.SetProcessDpiAwarenessContext(c.c_void_p(-4)),
            "Could not establish setup physical-pixel coordinates",
        )
        request = json.loads(
            (output / "setup-request.json").read_text(encoding="utf-8")
        )
        owner = ExplorerCase()
        owner.folder = Path(request["fixture"]["folder"])
        owner.before = set(request["fixture"]["previous_handles"])
        owner.pid = request["pid"]
        window = request["window"]
        owner.window = WindowInfo(
            window["handle"], window["title"], Rect(**window["bounds"])
        )
        _position_explorer_start(
            request["kind"], Point(**request["point"]), output, owner, setup
        )
    finally:
        (output / "setup.json").write_text(json.dumps(setup), encoding="utf-8")


def _position_explorer_start(
    kind: str, point: Point, output: Path, owner: ExplorerCase, setup: dict[str, object]
) -> None:
    """Own all setup UIA interfaces in the child, releasing before it exits."""
    native._require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
        "Explorer start setup requires a disposable hosted runner",
    )
    window = owner._verified_window()
    native._require(window == owner.window, "The owned Explorer setup window changed")
    requested = _EXPLORER_STARTS[kind]
    observations: list[dict[str, float]] = []
    setup.update(requested_vertical=requested, observed_positions=observations)
    with UiaScrollTarget(point, window=window.handle) as target:
        target.check()
        before = target.position()
        setup["pre_setup_position"] = asdict(before)
        native._require(
            owner._verified_window() == window,
            "The owned Explorer setup window changed",
        )
        target.move(requested)  # One guarded public UIA setup write, no wheel.
        setup["setup_writes"] = 1
        setup["immediate_position"] = asdict(target.position())
        previous, previous_image = None, QImage()
        for _ in range(8):
            time.sleep(0.08)
            target.check()
            current = target.position()
            image = target.frame()
            observations.append(asdict(current))
            # Test-only 45-55% qualification permits row quantization around 50%.
            # Preserve the exact tuple; this does not relax product validation.
            positioned = (
                current.vertical == END_PERCENT
                if requested == END_PERCENT
                else abs(current.vertical - requested) <= _MIDDLE_SETUP_RANGE
            )
            if (
                positioned
                and current.horizontal == before.horizontal
                and 0.0 < current.view_size < END_PERCENT
                and current == previous == target.position()
                and not image.isNull()
                and image == previous_image
            ):
                native._require(
                    owner._verified_window() == window,
                    "The owned Explorer setup window changed",
                )
                target.check()
                native._require(
                    target.position() == current and target.frame() == image,
                    "The Explorer start changed during ownership verification",
                )
                setup["settled_position"] = asdict(current)
                setup["area"] = asdict(target.area)
                setup["initial_png"] = "before.png"
                native._require(
                    image.save(str(output / "before.png")),
                    "Could not save the actual Explorer starting view",
                )
                return
            previous, previous_image = current, image
    raise RuntimeError("The Explorer test starting position did not settle")


def _explorer(
    _kind: str, directory: Path, report: dict[str, object]
) -> subprocess.Popen[bytes]:
    """Open a disposable folder in Explorer; never touch user files or settings."""
    folder = directory / "VerdiClip scrolling check"
    folder.mkdir()
    for number in range(160):
        name = f"Project note {number:03d} - design and testing.txt"
        (folder / name).write_text(
            "Owned scrolling diagnostic input.\n", encoding="utf-8"
        )
    executable = Path(os.environ["WINDIR"]) / "explorer.exe"
    report["executable"] = str(executable)
    native._record_version(executable, report)
    # A separate Explorer process is requested; existing cleanup verifies PID
    # ownership before closing a window and never kills the desktop shell.
    # Explorer parses comma-delimited fields, not conventional argv switches.
    return subprocess.Popen([str(executable), "/separate,", str(folder)])  # noqa: S603


def _shell_windows(folder: Path) -> dict[int, bool]:
    """Read HWNDs and exact fixture identity, without retaining other paths."""
    powershell = (
        Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    literal = str(folder).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; $shell=New-Object -ComObject Shell.Application;"
        "$items=@($shell.Windows() | ForEach-Object {"
        "if ([IO.Path]::GetFileName($_.FullName) -ieq 'explorer.exe') {"
        "$handle=[long]$_.HWND; $exact=$false; try {"
        "$exact=([IO.Path]::GetFullPath($_.Document.Folder.Self.Path) "
        f"-eq [IO.Path]::GetFullPath('{literal}')) }} catch {{}};"
        "[pscustomobject]@{ hwnd=$handle; target=$exact }"
        "} }); ConvertTo-Json -InputObject $items -Compress"
    )
    result = subprocess.run(  # noqa: S603 - read public shell metadata for owned fixture
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    items = json.loads(result.stdout)
    if not isinstance(items, list) or any(
        not isinstance(item, dict)
        or type(item.get("hwnd")) is not int
        or item.get("hwnd", 0) <= 0
        or type(item.get("target")) is not bool
        for item in items
    ):
        raise TypeError("Invalid Explorer window identity response")
    result_by_handle = {int(item["hwnd"]): bool(item["target"]) for item in items}
    if len(result_by_handle) != len(items):
        raise RuntimeError("Explorer exposed duplicate HWND identities")
    return result_by_handle


def _window_pid(window: int) -> int:
    user = c.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (w.HWND, c.POINTER(w.DWORD))
    user.GetWindowThreadProcessId.restype = w.DWORD
    pid = w.DWORD()
    native._require(
        user.GetWindowThreadProcessId(window, c.byref(pid)),
        "The fixture window no longer exists",
    )
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    kernel.WaitForSingleObject.restype = w.DWORD
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.CloseHandle.restype = w.BOOL
    process = kernel.OpenProcess(0x00100000, 0, pid.value)  # SYNCHRONIZE only.
    native._require(process, "Could not observe the fixture window's process")
    try:
        native._require(
            kernel.WaitForSingleObject(process, 0) == _WAIT_TIMEOUT,
            "The fixture window's process exited",
        )
    finally:
        kernel.CloseHandle(process)
    return pid.value


class ExplorerCase:
    """Own one new fixture window, never the shell or Explorer broker process."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.folder: Path | None = None
        self.before: set[int] = set()
        self.window: WindowInfo | None = None
        self.pid: int | None = None
        self.report: dict[str, object] = {}
        self.prepare = native._prepare

    def launch(
        self, kind: str, directory: Path, report: dict[str, object]
    ) -> subprocess.Popen[bytes]:
        """Record existing shell windows before opening a unique owned folder."""
        self.folder = directory / "VerdiClip scrolling check"
        self.report = report
        self.before = set(_shell_windows(self.folder))
        self.process = _explorer(kind, directory, report)
        return self.process

    def _identities(self) -> dict[int, bool]:
        if self.folder is None:
            raise RuntimeError("Explorer fixture was not prepared")
        return _shell_windows(self.folder)

    def _verify(self, window: WindowInfo) -> None:
        identities = self._identities()
        native._require(
            window.handle not in self.before
            and identities.get(window.handle) is True
            and sum(identities.values()) == 1,
            "Explorer did not expose one new exact fixture folder window",
        )
        pid = _window_pid(window.handle)
        native._require(
            self.pid is None or pid == self.pid,
            "The fixture window's process changed",
        )
        self.pid = pid

    def _verified_window(self) -> WindowInfo:
        if self.window is None:
            raise RuntimeError("Explorer fixture window was not identified")
        visible = WindowLocator().visible_windows()
        window = next((w for w in visible if w.handle == self.window.handle), None)
        if window is None:
            raise RuntimeError("The fixture window is no longer visible")
        self._verify(window)
        return window

    def _prepare(self, window: WindowInfo) -> WindowInfo:
        current = self._verified_window()
        native._require(
            current.handle == window.handle,
            "Capture setup tried to change the fixture window",
        )
        return self.prepare(current)

    def run(self, kind: str, output: Path, report: dict[str, object]) -> None:
        """Verify exact newly created folder identity before moving or focusing."""
        try:
            window = native._window()
        except RuntimeError:
            report["explorer_launcher_pid"] = (
                None if self.process is None else self.process.pid
            )
            report["explorer_launcher_exit"] = (
                None if self.process is None else self.process.poll()
            )
            native._desktop_evidence(output, report)
            raise
        self._verify(window)
        self.window = window
        report["explorer_window_pid"] = self.pid
        report["explorer_launcher_pid"] = (
            None if self.process is None else self.process.pid
        )
        report["explorer_new_exact_folder_window"] = True
        if kind in _EXPLORER_STARTS:
            report["explorer_fixture"] = {
                "folder": str(self.folder),
                "previous_handles": sorted(self.before),
            }
        with (
            patch.object(native, "_window", self._verified_window),
            patch.object(app, "_window", self._verified_window),
            patch.object(app, "_prepare", self._prepare),
        ):
            _case(kind, output, report)

    def cleanup(
        self,
        _process: subprocess.Popen[bytes] | None,
        *,
        window: int | None = None,
        deadline: float | None = None,
    ) -> None:
        """Close only the still-verified new fixture HWND; never kill Explorer."""
        del window
        user = c.WinDLL("user32", use_last_error=True)
        user.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
        user.PostMessageW.restype = w.BOOL
        user.IsWindow.argtypes = (w.HWND,)
        user.IsWindow.restype = w.BOOL
        if self.window is not None and not user.IsWindow(self.window.handle):
            self.report["explorer_cleanup"] = "retained fixture HWND confirmed absent"
            return
        identities = self._identities()
        candidates = [
            h for h, exact in identities.items() if exact and h not in self.before
        ]
        if not candidates:
            native._require(
                self.window is None,
                "Fixture HWND is alive but its exact path is missing; none closed",
            )
            self.report["explorer_cleanup"] = "no verified fixture window; none closed"
            return
        native._require(
            len(candidates) == 1 and sum(identities.values()) == 1,
            "Ambiguous fixture windows; none closed",
        )
        handle = candidates[0]
        native._require(
            self.window is None or handle == self.window.handle,
            "Fixture window identity changed; no window closed",
        )
        pid = _window_pid(handle)
        native._require(
            self.pid is None or pid == self.pid,
            "Fixture process identity changed; no window closed",
        )
        native._require(user.PostMessageW(handle, 0x0010, 0, 0), "Fixture close failed")
        close_deadline = time.monotonic() + 3.0
        if deadline is not None:
            close_deadline = min(close_deadline, deadline)
        for _ in range(30):
            remaining = close_deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
            if not user.IsWindow(handle):
                self.report["explorer_cleanup"] = "verified fixture window closed"
                return
        raise RuntimeError("Fixture window did not close; Explorer was not killed")


def main() -> None:
    """Keep diagnostics separate from the colored-row qualification oracle."""
    kind = sys.argv[1]
    layout = kind.removesuffix("-click").removesuffix("-enter")
    native._require(
        layout
        in (
            "edge-prose",
            "edge-panel",
            "edge-sidebar",
            "edge-background",
            "explorer",
            "explorer-middle",
            "explorer-bottom",
        )
        and kind.endswith(("-click", "-enter")),
        "Unknown representative document layout",
    )
    explorer = ExplorerCase()
    is_explorer = layout.startswith("explorer")
    with (
        patch.object(native, "_document", lambda **_kwargs: document(layout)),
        patch.object(app, "_verify_edge_rows", _inspect_result),
        patch.object(app.AppCapture, "_verify_restored", _verify_restored),
        patch.object(app, "Desktop", HumanDesktop),
        patch.object(
            native,
            "_launch",
            explorer.launch if is_explorer else native._launch,
        ),
        patch.object(
            native,
            "_stop_owned_process",
            explorer.cleanup if is_explorer else native._stop_owned_process,
        ),
    ):
        native.main(gui=True, case=explorer.run if is_explorer else _case)


if __name__ == "__main__":
    main()
