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
import math
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast, override
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QWidget
from scripts import simple_scroll_app as app
from scripts import simple_scroll_native as native
from scripts.scrolling_header_probe import _scroll_info, probe_target
from scripts.scrolling_item_witness import header_layout
from scripts.scrolling_oracle import verify_sequence
from scripts.simple_scroll_handoff import Desktop

from verdiclip.capture.scroll_helper import _watch_parent
from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition, ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Interface
from verdiclip.platform.windows import WindowInfo, WindowLocator

_WAIT_TIMEOUT = 0x00000102
_MIDDLE_SETUP_RANGE = 5.0
_INVERSE_EXPERIMENT = "explorer-inverse-restore"
_SCROLLBAR_ROLE = 50014
_SB_CTL = 2
_MAX_SCROLLBAR_CHILDREN = 64
_MAX_SCROLLBAR_ANCESTORS = 8
_SCALED_FIXTURE_DPI = 192
_SCALED_FIXTURE_BORDER = 6
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


def _scaled_fixture_ring_restored(first: QImage, after: QImage) -> bool:
    """Prove this 192-DPI CSS fixture's original ring and exact complement.

    This is a fixture-only color oracle, independent of production registration.
    The original 3 CSS pixel border is rectangular; focused corner antialiasing
    may change only its original members. No rectangular inset is ignored.
    """
    gray = 0xFFB1B5BA
    width, height = first.width(), first.height()
    if min(width, height) <= 2 * (_SCALED_FIXTURE_BORDER + 1):
        return False

    def side(length: int, *, vertical: bool, reverse: bool) -> tuple[int, int]:
        def pixel(offset: int) -> int:
            coordinate = length - 1 - offset if reverse else offset
            return (
                first.pixel(width // 2, coordinate)
                if vertical
                else first.pixel(coordinate, height // 2)
            )

        onset = int(pixel(0) != gray)
        outer = length - 1 if reverse else 0
        exterior = (
            (first.pixel(x, outer) for x in range(width))
            if vertical
            else (first.pixel(outer, y) for y in range(height))
        )
        if onset and any(color != pixel(0) for color in exterior):
            return 0, 0
        end = onset
        while end <= onset + _SCALED_FIXTURE_BORDER and pixel(end) == gray:
            end += 1
        return onset, end

    left, right = (side(width, vertical=False, reverse=r) for r in (False, True))
    top, bottom = (side(height, vertical=True, reverse=r) for r in (False, True))
    if any(
        not 1 <= end - start <= _SCALED_FIXTURE_BORDER
        for start, end in (left, right, top, bottom)
    ):
        return False
    for y in range(height):
        for x in range(width):
            inside = (
                left[0] <= x < width - right[0] and top[0] <= y < height - bottom[0]
            )
            member = inside and (
                x < left[1]
                or x >= width - right[1]
                or y < top[1]
                or y >= height - bottom[1]
            )
            # The inward boundary must be non-gray: reject connected protrusions,
            # without treating isolated gray content pixels as border members.
            inner_boundary = (
                left[1] <= x < width - right[1]
                and top[1] <= y < height - bottom[1]
                and (
                    x in (left[1], width - right[1] - 1)
                    or y in (top[1], height - bottom[1] - 1)
                )
            )
            valid = (
                first.pixel(x, y) == gray
                if member
                else first.pixel(x, y) == after.pixel(x, y)
                and not (inner_boundary and first.pixel(x, y) == gray)
            )
            if not valid:
                return False
    return True


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
    first = QImage(str(capture.output / "frame-0.png"))
    if capture.kind.startswith("explorer-") and state["initial"] != state["final"]:
        _verify_normalized_restore(capture, state, first, after)
        return
    native._require(state["initial"] == state["final"], "Scroll state was not restored")
    capture.report["restored_scroll_state"] = state
    native._require(first.size() == after.size(), "Restored panel geometry changed")
    if first == after:
        capture.report["restored_pixels"] = "exact"
        return
    target_path = capture.output / "target.json"
    target = (
        json.loads(target_path.read_text(encoding="utf-8"))
        if target_path.is_file()
        else {}
    )
    if (
        capture.kind.startswith("edge-panel-")
        and target.get("window_dpi") == _SCALED_FIXTURE_DPI
    ):
        native._require(
            _scaled_fixture_ring_restored(first, after),
            "Restored content pixels differ beyond the original fixture border ring",
        )
        capture.report["restored_pixels"] = (
            "exact complement; original border ring changed"
        )
        capture.report["restored_perimeter_changed_pixels"] = sum(
            first.pixel(x, y) != after.pixel(x, y)
            for y in range(first.height())
            for x in range(first.width())
        )
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


def _native_rail(rows: list[dict[str, object]], handle: int) -> dict[str, int]:
    candidates = [row for row in rows if row["bar"] == _SB_CTL]
    native._require(
        len(candidates) == 1 and candidates[0]["native_handle"] == handle,
        "Restoration lacks one exact native ScrollBar",
    )
    row = candidates[0]
    native._require(
        row["success"] is True
        and row["window_valid"] is True
        and row["root_matches"] is True
        and all(type(row[key]) is int for key in ("min", "max", "page", "pos")),
        "Restoration lacks valid native ScrollBar values",
    )
    return {key: value for key, value in row.items() if type(value) is int}


def _verify_native_mapping(position: dict[str, float], state: dict[str, int]) -> None:
    native._require(
        _same_native_arithmetic(
            position["vertical"], _native_inverse_percent(state, state["pos"])
        )
        and _same_native_arithmetic(
            position["view_size"],
            END_PERCENT * state["page"] / (state["max"] - state["min"]),
        ),
        "Restoration native/UIA mapping differs",
    )


def _verify_normalized_restore(
    capture: app.AppCapture,
    state: dict[str, object],
    before: QImage,
    after: QImage,
) -> None:
    """Independently require original native units and every non-rail pixel."""
    native._require(
        capture.kind.startswith("explorer-") and "bottom" not in capture.kind,
        "Only a top/middle Explorer normalization has this independent oracle",
    )
    geometry = json.loads((capture.output / "header-geometry.json").read_text())
    area, header = header_layout(geometry)
    bars = [
        node
        for node in geometry["uia"]["nodes"]
        if node["parent"] == 0
        and node["control_type"] == _SCROLLBAR_ROLE
        and not node["offscreen"]
    ]
    native._require(
        len(bars) == 1 and 0 < header.width < area.width,
        "Restoration requires a separately identified native rail",
    )
    handle = bars[0]["native_handle"]
    original = _native_rail(
        cast("list[dict[str, object]]", state["initial_native"]), handle
    )
    final = _native_rail(cast("list[dict[str, object]]", state["final_native"]), handle)
    frames = json.loads((capture.output / "frames.json").read_text())
    terminal = frames[-1]
    endpoint = _native_rail(terminal["native_scroll_info"], handle)
    initial = cast("dict[str, float]", state["initial"])
    restored = cast("dict[str, float]", state["final"])
    native._require(
        all(
            original[key] == final[key]
            for key in ("parent", "root", "pid", "min", "max", "pos")
        )
        and all(original[key] > 0 for key in ("parent", "root", "pid"))
        and all(original[key] == endpoint[key] for key in ("min", "max"))
        and final["page"] == endpoint["page"] != original["page"]
        and initial["vertical"] < END_PERCENT
        and terminal["vertical"] == END_PERCENT
        and restored["view_size"] == terminal["view_size"] != initial["view_size"]
        and initial["horizontal"] == restored["horizontal"] == terminal["horizontal"],
        "Restoration changed native position, identity, range or normalized page",
    )
    for position, sample in (
        (initial, original),
        (restored, final),
        (terminal, endpoint),
    ):
        _verify_native_mapping(position, sample)
    target = json.loads((capture.output / "target.json").read_text())
    native._require(
        target["initial_position"] == initial
        and target["area"] == asdict(area)
        and target["window"] == original["root"]
        and not before.isNull()
        and before.size() == after.size()
        and (before.width(), before.height()) == (area.width, area.height)
        and before.copy(0, 0, round(header.width), before.height())
        == after.copy(0, 0, round(header.width), after.height()),
        "Restoration changed target geometry or original non-rail pixels",
    )
    capture.report.update(
        restored_scroll_state=state,
        restored_pixels="exact non-rail; native position exact; normalized page",
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
            state["initial"] == asdict(self.start_position)
            and target["initial_position"] == state["initial"]
            and target["area"] == asdict(self.start_area),
            "Explorer did not restore the actual starting scroll state",
        )
        before = QImage(str(self.output / "before.png"))
        if state["initial"] != state["final"]:
            _verify_normalized_restore(self, state, before, after)
            return
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
        super()._terminal(result)


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
        if request["kind"] == _INVERSE_EXPERIMENT:
            _inverse_explorer_restore(Point(**request["point"]), output, owner, setup)
        else:
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


@dataclass(frozen=True)
class _InverseSample:
    position: ScrollPosition
    native: dict[str, int]
    image: QImage

    def record(self) -> dict[str, object]:
        return {"position": asdict(self.position), "native": self.native}


def _native_inverse_percent(native: dict[str, int], position: int) -> float:
    """Map native scroll units to percentage, never to image pixel displacement."""
    minimum, maximum, page = native["min"], native["max"], native["page"]
    last = maximum - max(page - 1, 0)
    if not (minimum < last <= maximum and page > 0 and minimum <= position <= last):
        raise RuntimeError("Inverse experiment has an invalid native scroll range")
    return END_PERCENT * (position - minimum) / (last - minimum)


def _same_native_arithmetic(observed: float, computed: float) -> bool:
    """Allow only finite equality or one adjacent float for operation ordering."""
    return (
        math.isfinite(observed)
        and math.isfinite(computed)
        and observed
        in (
            computed,
            math.nextafter(computed, -math.inf),
            math.nextafter(computed, math.inf),
        )
    )


class _InverseScrollBar:
    """Retain one verified direct native ScrollBar for this single experiment."""

    def __init__(self, target: UiaScrollTarget, pid: int) -> None:
        self.target, self.pid = target, pid
        self.geometry = probe_target(target)
        self.area, self.header = header_layout(self.geometry)
        self.rail = Rect(
            self.header.right,
            self.area.top,
            self.area.right - self.header.right,
            self.area.height,
        )
        native._require(
            0 < self.rail.width < self.area.width
            and self.rail.width == round(self.rail.width)
            and target.header == self.header,
            "Inverse experiment requires the separately verified ScrollBar rail",
        )
        candidates: list[_Interface] = []
        child = target._walker.relative(target._element, target._owners, 4)
        for _ in range(_MAX_SCROLLBAR_CHILDREN):
            if child is None:
                break
            if child.control_type() == _SCROLLBAR_ROLE and not child.offscreen():
                candidates.append(child)
            child = target._walker.relative(child, target._owners, 6)
        native._require(
            child is None and len(candidates) == 1,
            "Inverse experiment requires one bounded direct ScrollBar",
        )
        self.element = candidates[0]
        handle = self.element.native_handle()
        if handle is None or handle <= 0:
            raise RuntimeError("Inverse experiment lacks a native ScrollBar HWND")
        self.handle = handle
        tree = cast("dict[str, object]", self.geometry["uia"])
        nodes = cast("list[dict[str, object]]", tree["nodes"])
        native._require(
            [
                node["native_handle"]
                for node in nodes
                if node["parent"] == 0
                and node["control_type"] == _SCROLLBAR_ROLE
                and not node["offscreen"]
            ]
            == [handle],
            "Inverse experiment ScrollBar differs from the measured geometry",
        )
        getter = target._user.GetParent
        getter.argtypes, getter.restype = (w.HWND,), w.HWND
        self.parent = int(getter(handle) or 0)
        native._require(self.parent > 0, "Inverse experiment lacks a native parent")
        self.horizontal = target.position().horizontal
        self.range: tuple[int, int] | None = None
        self.check()

    def check(self) -> None:
        target = self.target
        target.check()
        native._require(
            self.element.control_type() == _SCROLLBAR_ROLE
            and not self.element.offscreen()
            and self.element.native_handle() == self.handle
            and self.element.bounds() == self.rail
            and target.area == self.area
            and target.header == self.header
            and target._user.GetParent(self.handle) == self.parent
            and target._user.GetAncestor(self.handle, 2) == target._window
            and _window_pid(self.handle) == self.pid,
            "Inverse experiment ScrollBar geometry/role/HWND/parent/root/PID changed",
        )
        with ExitStack() as owners:
            parent = target._walker.parent(self.element, owners)
            native._require(
                parent is not None
                and target._automation.same_element(parent, target._element),
                "Inverse experiment ScrollBar changed its direct UIA parent",
            )
            point = self.rail.center
            current = target._automation.pointer_result(
                7, owners, (w.POINT, w.POINT(round(point.x), round(point.y)))
            )
            for _ in range(_MAX_SCROLLBAR_ANCESTORS):
                if current.control_type() == _SCROLLBAR_ROLE:
                    native._require(
                        target._automation.same_element(current, self.element),
                        "Inverse experiment ScrollBar identity changed",
                    )
                    target.check()
                    return
                parent = target._walker.parent(current, owners)
                if parent is None:
                    break
                current = parent
        raise RuntimeError(
            "Inverse experiment cannot reidentify the retained ScrollBar"
        )

    def read(self) -> _InverseSample:
        self.check()
        position = self.target.position()
        raw = _scroll_info(self.target, self.handle, 2)
        native._require(
            raw["success"] is True
            and raw["window_valid"] is True
            and raw["root_matches"] is True
            and all(type(raw[key]) is int for key in ("min", "max", "page", "pos")),
            "Inverse experiment cannot read exact native ScrollBar state",
        )
        state = {key: cast("int", raw[key]) for key in ("min", "max", "page", "pos")}
        bounds = (state["min"], state["max"])
        # Only operation-order rounding may differ, never native state or H.
        native._require(
            position.horizontal == self.horizontal
            and _same_native_arithmetic(
                position.vertical, _native_inverse_percent(state, state["pos"])
            )
            and _same_native_arithmetic(
                position.view_size,
                END_PERCENT * state["page"] / (state["max"] - state["min"]),
            )
            and (self.range is None or bounds == self.range),
            "Inverse experiment native/UIA mapping or min/max changed",
        )
        self.range = bounds
        image = self.target.frame()
        native._require(
            position == self.target.position()
            and raw == _scroll_info(self.target, self.handle, 2)
            and not image.isNull()
            and (image.width(), image.height()) == (self.area.width, self.area.height),
            "Inverse experiment moved during native/pixel observation",
        )
        self.check()
        return _InverseSample(position, state, image)

    def settle(
        self, phase: str, output: Path, evidence: dict[str, object]
    ) -> _InverseSample:
        observations: list[dict[str, object]] = []
        evidence[f"{phase}_observations"] = observations
        previous = None
        for _ in range(8):
            time.sleep(0.08)
            current = self.read()
            observations.append(current.record())
            native._require(
                current.image.save(str(output / f"inverse-{phase}.png")),
                "Could not save inverse experiment pixels",
            )
            if current == previous:
                self.check()
                evidence[phase] = current.record()
                return current
            previous = current
        raise RuntimeError(f"Inverse experiment {phase} did not settle")


def _inverse_explorer_restore(
    point: Point, output: Path, owner: ExplorerCase, setup: dict[str, object]
) -> None:
    """Try one fresh inverse mapping, never an old-percent write or correction loop."""
    _position_explorer_start("explorer-middle-click", point, output, owner, setup)
    writes: list[float] = []
    evidence: dict[str, object] = {
        "product_capture_qualification": False,
        "native_units_are_pixels": False,
        "endpoint_and_restore_write_requests": writes,
    }
    setup["inverse_restore"] = evidence
    window = owner._verified_window()
    if owner.pid is None:
        raise RuntimeError("Inverse experiment lacks the retained fixture PID")
    with UiaScrollTarget(point, window=window.handle) as target:
        bar = _InverseScrollBar(target, owner.pid)
        (output / "inverse-geometry.json").write_text(
            json.dumps(bar.geometry, indent=2), encoding="utf-8"
        )
        evidence["retained_scrollbar"] = {
            "hwnd": bar.handle,
            "native_parent": bar.parent,
            "role": _SCROLLBAR_ROLE,
            "root": window.handle,
            "pid": owner.pid,
            "rail": asdict(bar.rail),
            "direct_uia_parent_and_identity_checked": True,
        }
        original = bar.settle("original", output, evidence)
        native._require(
            asdict(original.position) == setup["settled_position"]
            and original.image == QImage(str(output / "before.png")),
            "Inverse experiment original differs from its one-write middle setup",
        )
        native._require(
            owner._verified_window() == window, "Fixture changed before endpoint"
        )
        native._require(bar.read() == original, "Original changed before endpoint")
        writes.append(END_PERCENT)
        target.move(END_PERCENT)
        endpoint = bar.settle("endpoint", output, evidence)
        width, header = round(bar.header.width), round(bar.header.height)
        native._require(
            endpoint.position.vertical == END_PERCENT
            and endpoint.native["page"] > original.native["page"]
            and endpoint.image.copy(0, 0, width, header)
            == original.image.copy(0, 0, width, header),
            "Inverse experiment lacks the stable endpoint page transition/Header",
        )
        native._require(
            owner._verified_window() == window, "Fixture changed before inverse"
        )
        native._require(bar.read() == endpoint, "Endpoint changed before inverse")
        requested = _native_inverse_percent(endpoint.native, original.native["pos"])
        native._require(0 < requested < END_PERCENT, "Inverse request is not interior")
        evidence["computed_restore_percent"] = requested
        writes.append(requested)
        target.move(requested)
        final = bar.settle("final", output, evidence)
        checks = {
            "original_native_position": final.native["pos"] == original.native["pos"],
            "endpoint_page_preserved": final.native["page"] == endpoint.native["page"],
            "header_pixels_exact": final.image.copy(0, 0, width, header)
            == original.image.copy(0, 0, width, header),
            "body_pixels_exact": final.image.copy(
                0, header, width, final.image.height() - header
            )
            == original.image.copy(0, header, width, original.image.height() - header),
        }
        evidence["final_checks"] = checks
        native._require(
            all(checks.values()),
            "One inverse write did not restore exact native position/content pixels",
        )
        bar.check()
        target.check()
        setup["experiment_passed"] = True


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
            if kind == _INVERSE_EXPERIMENT:
                self._inverse_case(output, report)
            else:
                _case(kind, output, report)

    def _inverse_case(self, output: Path, report: dict[str, object]) -> None:
        """Run a fresh fixture experiment, never the product capture qualification."""
        native._require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Explorer inverse experiment requires a disposable hosted runner",
        )
        window = self._prepare(self._verified_window())
        report.update(
            window=asdict(window),
            oracle="test-only one-write inverse-native-position experiment",
            product_capture_qualification=False,
        )
        request = {
            "kind": _INVERSE_EXPERIMENT,
            "point": asdict(window.bounds.center),
            "window": asdict(window),
            "pid": self.pid,
            "fixture": {
                "folder": str(self.folder),
                "previous_handles": sorted(self.before),
            },
        }
        (output / "setup-request.json").write_text(
            json.dumps(request), encoding="utf-8"
        )
        evidence = output / "setup.json"
        evidence.write_text("{}", encoding="utf-8")
        try:
            _run_explorer_setup(output)
        finally:
            report["inverse_restore_experiment"] = json.loads(
                evidence.read_text(encoding="utf-8")
            )
        setup = json.loads(evidence.read_text(encoding="utf-8"))
        native._require(setup.get("experiment_passed") is True, "Experiment incomplete")
        report["experiment_passed"] = True
        # native.main's product success stays false, even if this experiment passes.

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
        kind == _INVERSE_EXPERIMENT
        or (
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
            and kind.endswith(("-click", "-enter"))
        ),
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
        native.main(
            gui=kind != _INVERSE_EXPERIMENT, case=explorer.run if is_explorer else _case
        )


if __name__ == "__main__":
    main()
