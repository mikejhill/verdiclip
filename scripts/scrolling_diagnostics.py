"""Native diagnostic samples with ordinary prose, not colored pixel landmarks.

Run only on the existing disposable hosted Windows desktop. This reuses the
actual UIA helper and exact PNG acquisition. A successful capture is only a
capture/restoration smoke result, not an independent completeness proof.
"""

from __future__ import annotations

import ctypes as c
import ctypes.wintypes as w
import hashlib
import html
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import cast, override
from unittest.mock import patch

import mss
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from scripts import simple_scroll_app as app
from scripts import simple_scroll_native as native
from scripts.hosted_display_scale import target_dpi
from scripts.scrolling_header_probe import _scroll_info, probe_target
from scripts.scrolling_item_witness import (
    ListItemWitness,
    header_layout,
    list_layout,
    raw_bounds,
)
from scripts.scrolling_oracle import verify_sequence
from scripts.simple_scroll_handoff import Desktop

from verdiclip.capture.grabber import FrozenScreen
from verdiclip.capture.scroll_helper import _watch_parent
from verdiclip.capture.scrolling import END_PERCENT, ScrollPosition, ScrollResult
from verdiclip.geometry import Point, Rect
from verdiclip.platform.scrolling import UiaScrollTarget, _Interface
from verdiclip.platform.windows import WindowInfo, WindowLocator

_WAIT_TIMEOUT = 0x00000102
_MIDDLE_SETUP_RANGE = 5.0
_INVERSE_EXPERIMENT = "explorer-inverse-restore"
_GEOMETRY_EXPERIMENT = "explorer-step-geometry"
_HELPER_OUTPUT = "explorer-helper-output"
_EXPERIMENTS = (_INVERSE_EXPERIMENT, _GEOMETRY_EXPERIMENT)
_GEOMETRY_WRITES = 4
_GEOMETRY_PAIR = 2
_EXACT_REGION_TRIALS = 3
_LIST_ITEM_ROLE = 50007
_SCROLLBAR_ROLE = 50014
_SB_CTL = 2
_MAX_SCROLLBAR_CHILDREN = 64
_MAX_SCROLLBAR_ANCESTORS = 8
_TALL_EXPLORER_HEIGHT = 1000
_SCALED_FIXTURE_DPI = 192
_SCALED_FIXTURE_DPIS = (120, _SCALED_FIXTURE_DPI)
_FIXTURE_CSS_BORDER = 3
_FIXTURE_CSS_DPI = 96
_FOCUSED_PANELS = ("edge-panel-focused-click", "edge-panel-focused-enter")
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
        f"border:{_FIXTURE_CSS_BORDER}px solid #b1b5ba;padding:12px;overflow:scroll}}"
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
    if report.get("explorer_layout") is not None:
        _taller_target_layout(output, report)
    report.update(verify_sequence(result.image, output))
    native._require(
        report["oracle_frames"] == result.frames,
        "Whole-image oracle did not retain every accepted frame",
    )


def _taller_target_layout(output: Path, report: dict[str, object]) -> None:
    """Record actual H0/header/two-rail geometry, without assumed pixel sizes."""
    geometry = json.loads((output / "header-geometry.json").read_text())
    target = json.loads((output / "target.json").read_text())
    area, header, footer, rail = list_layout(geometry)
    report["explorer_target_layout"] = {
        "area": asdict(area),
        "header": asdict(header),
        "footer": None if footer is None else asdict(footer),
        "rail": None if rail is None else asdict(rail),
        "horizontal": target["initial_position"]["horizontal"],
        "window_dpi": target["window_dpi"],
    }
    native._require(
        target["initial_position"]["horizontal"] == 0.0
        and geometry["position"]["horizontal"] == 0.0
        and target["area"] == asdict(area)
        and footer is not None
        and rail is not None,
        "Taller Explorer did not expose the measured H0/two-rail layout",
    )
    if footer is not None:
        report["explorer_body_height"] = footer.top - header.bottom


def _case(kind: str, output: Path, report: dict[str, object]) -> None:
    report["oracle"] = "capture/restoration smoke only; inspect exact PNG content"
    report["gesture_hold_ms"] = 120
    if kind in _EXPLORER_STARTS:
        ExplorerStartCapture(kind, output, report).run()
    elif kind in _FOCUSED_PANELS:
        report["expected_target_dpi"] = target_dpi(
            int(os.environ.get("DISPLAY_SCALE", "200"))
        )
        EdgeFocusedCapture(kind, output, report).run()
    else:
        app._case(kind, output, report)


def _scaled_fixture_ring_restored(
    first: QImage, after: QImage, *, dpi: int = _SCALED_FIXTURE_DPI
) -> bool:
    """Prove this supported-DPI CSS fixture's original ring and exact complement.

    This is a fixture-only color oracle, independent of production registration.
    The original 3 CSS pixel border is rectangular; focused corner antialiasing
    may change only its original members. No rectangular inset is ignored.
    """
    gray = 0xFFB1B5BA
    width, height = first.width(), first.height()
    # Round upward only for a conservative physical cap, never to select pixels.
    # Actual side widths (3 pixels in the retained 120-DPI evidence) come below.
    border = math.ceil(_FIXTURE_CSS_BORDER * dpi / _FIXTURE_CSS_DPI)
    if (
        dpi not in _SCALED_FIXTURE_DPIS
        or first.size() != after.size()
        or min(width, height) <= 2 * (border + 1)
    ):
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
        while end <= onset + border and pixel(end) == gray:
            end += 1
        return onset, end

    left, right = (side(width, vertical=False, reverse=r) for r in (False, True))
    top, bottom = (side(height, vertical=True, reverse=r) for r in (False, True))
    if any(not 1 <= end - start <= border for start, end in (left, right, top, bottom)):
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
    dpi = target.get("window_dpi")
    if capture.kind.startswith("edge-panel-") and dpi in _SCALED_FIXTURE_DPIS:
        native._require(
            _scaled_fixture_ring_restored(first, after, dpi=dpi),
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


def _native_rail(
    rows: list[dict[str, object]], handle: int, horizontal: int | None = None
) -> dict[str, int]:
    candidates = [row for row in rows if row["bar"] == _SB_CTL]
    expected = {handle} if horizontal is None else {handle, horizontal}
    native._require(
        len(candidates) == len(expected)
        and {row["native_handle"] for row in candidates} == expected,
        "Restoration lacks one exact native ScrollBar",
    )
    row = next(row for row in candidates if row["native_handle"] == handle)
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
    area, header, footer, rail = list_layout(geometry)
    if rail is None:
        raise RuntimeError("Restoration requires a separately identified native rail")
    bars = [
        node
        for node in geometry["uia"]["nodes"]
        if node["parent"] == 0
        and node["control_type"] == _SCROLLBAR_ROLE
        and not node["offscreen"]
    ]
    native._require(
        len(bars) == (1 if footer is None else 2) and 0 < header.width < area.width,
        "Restoration requires a separately identified native rail",
    )
    handle = next(
        node["native_handle"] for node in bars if node["bounds"] == asdict(rail)
    )
    horizontal = next(
        (
            node["native_handle"]
            for node in bars
            if footer is not None and node["bounds"] == asdict(footer)
        ),
        None,
    )
    original = _native_rail(
        cast("list[dict[str, object]]", state["initial_native"]), handle, horizontal
    )
    final = _native_rail(
        cast("list[dict[str, object]]", state["final_native"]), handle, horizontal
    )
    frames = json.loads((capture.output / "frames.json").read_text())
    terminal = frames[-1]
    endpoint = _native_rail(terminal["native_scroll_info"], handle, horizontal)
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
        == after.copy(0, 0, round(header.width), after.height())
        and (
            footer is None
            or before.copy(0, round(rail.height), before.width(), round(footer.height))
            == after.copy(0, round(rail.height), after.width(), round(footer.height))
        ),
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


def _focused_panel_evidence(
    before: QImage,
    first: QImage,
    second: QImage,
    *,
    dpi: int = _SCALED_FIXTURE_DPI,
) -> dict[str, object]:
    """Independently prove a changed original ring and unchanged focused sides."""
    native._require(
        not before.isNull()
        and before.size() == first.size() == second.size()
        and before != first
        and _scaled_fixture_ring_restored(before, first, dpi=dpi),
        "Focus setup did not change only the original fixture border ring",
    )
    gray = 0xFFB1B5BA
    width, height = first.width(), first.height()
    middle = range(height // 4, 3 * height // 4)
    border = math.ceil(_FIXTURE_CSS_BORDER * dpi / _FIXTURE_CSS_DPI)
    sides = (
        range(border + 1),
        range(width - border - 1, width),
    )
    changed: list[list[int]] = []
    for columns in sides:
        ring = [x for x in columns if all(before.pixel(x, y) == gray for y in middle)]
        outline = [x for x in ring if first.pixel(x, height // 2) != gray]
        native._require(
            bool(outline)
            and all(
                first.pixel(x, y) == first.pixel(x, height // 2)
                for x in outline
                for y in middle
            )
            and all(
                first.pixel(x, y) == second.pixel(x, y)
                for x in ring
                for y in range(height)
            ),
            "The initial focused side outline was absent or changed while scrolling",
        )
        changed.append(outline)
    return {
        "changed_side_columns": changed,
        "initial_ring_changed_pixels": sum(
            before.pixel(x, y) != first.pixel(x, y)
            for y in range(height)
            for x in range(width)
        ),
        "nonborder_pixels": "exact",
        "focused_sides": "unchanged through frame 1",
    }


class EdgeFocusedCapture(app.AppCapture):
    """One ordinary focus attempt before the unchanged production chooser flow."""

    def _focus_owner(self) -> None:
        native._require(
            self.kind in _FOCUSED_PANELS
            and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Focus setup requires the owned hosted Edge panel case",
        )
        native._require(
            app._window() == self.window
            and self.desktop.foreground() == self.window.handle
            and self.window.bounds.contains(self.point),
            "The owned focus setup window or point changed",
        )
        hit = self.desktop.user.WindowFromPoint(
            w.POINT(round(self.point.x), round(self.point.y))
        )
        native._require(
            self.desktop.user.GetAncestor(hit, 2) == self.window.handle,
            "The focus setup point is not covered by the owned window",
        )

    def _focus_gesture(self, keys: tuple[int, ...]) -> None:
        self._focus_owner()
        if keys:
            try:
                for key in keys:
                    self.desktop.user.keybd_event(key, 0, 0, 0)
                QTest.qWait(120)
            finally:
                for key in reversed(keys):
                    self.desktop.user.keybd_event(key, 0, 2, 0)
        else:
            native._require(
                self.desktop.user.SetCursorPos(
                    round(self.point.x), round(self.point.y)
                ),
                "Could not position the owned focus setup pointer",
            )
            try:
                self.desktop.user.mouse_event(2, 0, 0, 0, 0)
                QTest.qWait(120)
            finally:
                self.desktop.user.mouse_event(4, 0, 0, 0, 0)

    @override
    def _settle_target_foreground(self) -> None:
        super()._settle_target_foreground()
        self._focus_owner()
        self._focus_before = self.source.freeze()
        native._require(
            self._focus_before.image.save(str(self.output / "focus-before.png")),
            "Could not retain the actual pre-focus desktop",
        )
        self.report["focus_setup_point"] = asdict(
            Point(round(self.point.x), round(self.point.y))
        )
        self.report["focus_setup"] = {
            "attempts": 1,
            "gestures": ["click", "Shift+Tab", "Tab"],
        }
        for keys in ((), (0x10, 0x09), (0x09,)):
            self._focus_gesture(keys)
        # This is setup evidence only. Production still takes its own input tick
        # after the later real chooser gesture, without substituting this value.
        self.report["focus_setup_completed_input_tick"] = self.desktop.input_tick()
        QTest.qWait(120)
        self._focus_owner()
        self._focus_after = self.source.freeze()
        native._require(
            self._focus_after.image.save(str(self.output / "focus-after.png")),
            "Could not retain the actual post-focus desktop",
        )

    @override
    def _verify_restored(self, after: QImage) -> None:
        target = json.loads((self.output / "target.json").read_text(encoding="utf-8"))
        state = json.loads(
            (self.output / "restoration.json").read_text(encoding="utf-8")
        )
        area = Rect(**target["area"])
        first = QImage(str(self.output / "frame-0.png"))
        second = QImage(str(self.output / "frame-1.png"))
        before, focused = self._focus_before.crop(area), self._focus_after.crop(area)
        expected_dpi = self.report.get("expected_target_dpi", _SCALED_FIXTURE_DPI)
        native._require(
            target["window_dpi"] == expected_dpi
            and target["window"] == self.window.handle
            and area.contains(
                Point(**cast("dict[str, float]", self.report["focus_setup_point"]))
            )
            and target["initial_position"] == state["initial"] == state["final"]
            and state["initial"]["horizontal"] == state["initial"]["vertical"] == 0.0
            and (first.width(), first.height()) == (area.width, area.height)
            and first == focused,
            "The actual first frame did not retain the focused "
            f"{expected_dpi}-DPI starting state",
        )
        evidence = _focused_panel_evidence(
            before,
            first,
            second,
            # Preserve the prior 96-DPI focused-start check without extending it.
            dpi=_SCALED_FIXTURE_DPI
            if target["window_dpi"] == _FIXTURE_CSS_DPI
            else target["window_dpi"],
        )
        native._require(
            before.save(str(self.output / "focus-panel-before.png"))
            and focused.save(str(self.output / "focus-panel-after.png")),
            "Could not retain the focus setup target crops",
        )
        self.report["focused_start_evidence"] = evidence
        super()._verify_restored(after)


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
        elif request["kind"] == _GEOMETRY_EXPERIMENT:
            _explorer_step_geometry(Point(**request["point"]), output, owner, setup)
        else:
            _position_explorer_start(
                request["kind"], Point(**request["point"]), output, owner, setup
            )
    except Exception as error:
        setup["error"] = str(error)
        raise
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

    def __init__(
        self, target: UiaScrollTarget, pid: int, *, two_rails: bool = False
    ) -> None:
        self.target, self.pid = target, pid
        self.geometry = probe_target(target)
        self.area, self.header = header_layout(self.geometry)
        self.rail = Rect(
            self.header.right,
            self.area.top,
            self.area.right - self.header.right,
            self.area.height,
        )
        self.footer = None
        if two_rails:
            area, header, footer, rail = list_layout(self.geometry)
            native._require(
                area == self.area
                and header == self.header
                and footer is not None
                and rail is not None
                and target.footer == footer
                and target.vertical_scrollbar == rail,
                "Geometry experiment requires the exact two-rail partition",
            )
            self.footer, self.rail = footer, cast("Rect", rail)
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
        if two_rails:
            tree = cast("dict[str, object]", self.geometry["uia"])
            nodes = cast("list[dict[str, object]]", tree["nodes"])
            native._require(
                len(candidates) == _GEOMETRY_PAIR
                and {(part.bounds(), part.native_handle()) for part in candidates}
                == {
                    (raw_bounds(node["bounds"]), node["native_handle"])
                    for node in nodes
                    if node["parent"] == 0
                    and node["control_type"] == _SCROLLBAR_ROLE
                    and not node["offscreen"]
                },
                "Geometry experiment two-rail identity changed",
            )
            candidates = [part for part in candidates if part.bounds() == self.rail]
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
                and (not two_rails or node["bounds"] == asdict(self.rail))
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
            and (self.footer is None or target.footer == self.footer)
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


def _geometry_pair(witness: ListItemWitness) -> list[dict[str, object]]:
    """Record retained raw rectangles and their independently verified hit areas."""
    result: list[dict[str, object]] = []
    for anchor in witness._anchors:
        bounds = witness._visible(anchor.element)
        witness._confirm(anchor.element, bounds)
        visible = witness._visible_bounds(bounds)
        native._require(
            bounds == anchor.bounds and visible is not None,
            "Geometry experiment visible item changed during observation",
        )
        result.append({"raw": asdict(bounds), "visible": asdict(cast("Rect", visible))})
    native._require(
        len(result) == _GEOMETRY_PAIR, "Geometry experiment lacks two visible items"
    )
    return result


def _geometry_step(
    body: Rect,
    before: _InverseSample,
    after: _InverseSample,
    witness: dict[str, object],
) -> dict[str, object]:
    """Compare exact PNG overlap at the raw-item delta, without pixel registration."""
    items = cast("list[dict[str, object]]", witness["items"])
    native._require(
        len(items) == _GEOMETRY_PAIR, "Geometry step lacks two raw witnesses"
    )
    deltas = []
    previous_bottom = -math.inf
    for item in items:
        first, last = raw_bounds(item["before"]), raw_bounds(item["after"])
        visible_first = raw_bounds(item["visible_before"])
        visible_last = raw_bounds(item["visible_after"])
        native._require(
            item["same_identity"] is True
            and item["same_parent"] is True
            and (first.x, first.width, first.height)
            == (last.x, last.width, last.height)
            and first.height > 0
            and first.width > 0
            and first.top >= previous_bottom
            and all(
                visible == raw.intersected(body)
                and not visible.is_empty
                and visible.left == raw.left
                and visible.top == raw.top
                and visible.height == raw.height
                for raw, visible in ((first, visible_first), (last, visible_last))
            ),
            "Geometry step has invalid visible/raw item evidence",
        )
        previous_bottom = first.bottom
        deltas.append(first.top - last.top)
    native._require(
        deltas[0] == deltas[1] == round(deltas[0]) and 0 < deltas[0] < body.height,
        "Geometry step has invalid raw displacement",
    )
    delta = round(deltas[0])
    rows, width = round(body.height) - delta, round(body.width)
    # The caller supplies body-only images, preserving full-width saved originals.
    native._require(
        all(
            not sample.image.isNull()
            and (sample.image.width(), sample.image.height()) == (width, body.height)
            for sample in (before, after)
        ),
        "Geometry step requires exact body images",
    )
    exact = before.image.copy(0, delta, width, rows) == after.image.copy(
        0, 0, width, rows
    )
    expected = (
        body.height
        * (END_PERCENT - before.position.view_size)
        / before.position.view_size
        * (after.position.vertical - before.position.vertical)
        / END_PERCENT
    )
    result: dict[str, object] = {
        "raw_displacements": deltas,
        "png_overlap_rows": rows,
        "png_overlap_exact": exact,
        "uia_predicted_pixels": expected,
        "raw_minus_uia_pixels": delta - expected,
        "native_units_are_pixels": False,
    }
    return result


@contextmanager
def _exact_region_source() -> Generator[mss.MSS]:
    """Own a temporary public mss handle without masking an experiment failure."""
    source = mss.MSS()
    try:
        yield source
    finally:
        original = sys.exception()
        try:
            source.close()
        except Exception as error:
            if original is None:
                raise
            original.add_note(f"Exact-region handle cleanup also failed: {error}")


def _exact_region_rect(area: Rect) -> tuple[int, int, int, int]:
    """Reject fractional or missing physical pixels, never round or clip."""
    values = (area.left, area.top, area.right, area.bottom)
    native._require(
        not area.is_empty
        and all(math.isfinite(value) and value == int(value) for value in values),
        "Exact-region experiment requires integral, nonempty physical bounds",
    )
    return int(area.left), int(area.top), int(area.right), int(area.bottom)


def _exact_region_frame(
    source: mss.MSS, capture: Rect, area: Rect
) -> tuple[QImage, dict[str, float]]:
    """Time only public acquisition, RGB32 detachment, and the existing crop."""
    rectangle = _exact_region_rect(capture)
    _exact_region_rect(area)
    native._require(
        capture.intersected(area) == area,
        "Exact-region experiment would clip the verified area",
    )
    start = time.perf_counter()
    shot = source.grab(rectangle)
    acquired = time.perf_counter()
    native._require(
        (shot.left, shot.top, shot.width, shot.height)
        == (capture.x, capture.y, capture.width, capture.height)
        and len(shot.raw) == shot.width * shot.height * 4,
        "Exact-region experiment received missing or shifted pixels",
    )
    start_detach = time.perf_counter()
    image = QImage(
        shot.raw, shot.width, shot.height, shot.width * 4, QImage.Format.Format_RGB32
    ).copy()
    detached = time.perf_counter()
    crop_seconds = 0.0
    if capture != area:
        start_crop = time.perf_counter()
        image = FrozenScreen(image, capture).crop(area)
        crop_seconds = time.perf_counter() - start_crop
    native._require(
        not image.isNull()
        and (image.width(), image.height()) == (area.width, area.height),
        "Exact-region experiment lost decoded pixels",
    )
    return image, {
        "acquire_seconds": acquired - start,
        "detach_seconds": detached - start_detach,
        "crop_seconds": crop_seconds,
    }


class _StepGeometry:
    """Own four bounded writes and independent observations, never a capture."""

    def __init__(
        self,
        owner: ExplorerCase,
        bar: _InverseScrollBar,
        output: Path,
        evidence: dict[str, object],
    ) -> None:
        self.owner, self.bar, self.output, self.evidence = owner, bar, output, evidence
        self.window = owner._verified_window()
        self.writes: list[dict[str, object]] = []
        evidence["write_requests"] = self.writes
        self.body = Rect(
            bar.area.left,
            bar.header.bottom,
            bar.header.width,
            bar.rail.bottom - bar.header.bottom,
        )

    def guard(self, sample: _InverseSample) -> None:
        native._require(
            self.owner._verified_window() == self.window,
            "Geometry experiment fixture identity changed",
        )
        native._require(self.bar.read() == sample, "Geometry experiment sample changed")

    def phase(self, name: str) -> _InverseSample:
        self.evidence["phase"] = name
        sample = self.bar.settle(f"geometry-{name}", self.output, self.evidence)
        geometry = probe_target(self.bar.target)
        self.evidence[f"{name}_geometry"] = geometry
        native._require(
            list_layout(geometry)
            == (self.bar.area, self.bar.header, self.bar.footer, self.bar.rail)
            and geometry["window_dpi"] == _SCALED_FIXTURE_DPI
            and self.bar.target.registration_dpi == _SCALED_FIXTURE_DPI
            and geometry["position"] == asdict(sample.position)
            and (name != "original" or geometry["uia"] == self.bar.geometry["uia"])
            and sample.position.horizontal == 0.0,
            "Geometry experiment Header/footer/DPI/H changed",
        )
        self.guard(sample)
        return sample

    def exact_region(self, original: _InverseSample) -> None:
        """Temporary diagnostic: prune after native equivalence/speed evidence.

        One warm triplet and three measured triplets, with no scroll writes.
        Separate handles retain each allocation size; this is not a production
        capture API or a timing claim about guards, settling, or total frames.
        """
        target, area = self.bar.target, self.bar.area
        rectangle = _exact_region_rect(area)
        display = target._observe_display()
        if display is None:
            raise RuntimeError("Exact-region display evidence missing")
        geometry = self.evidence["original_geometry"]
        trials: list[dict[str, object]] = []
        result: dict[str, object] = {
            "passed": False,
            "physical_rectangle": rectangle,
            "display": asdict(display),
            "warm_triplets": 1,
            "measured_triplets": _EXACT_REGION_TRIALS,
            "trials": trials,
        }
        self.evidence["exact_region"] = result

        def unchanged() -> None:
            self.guard(original)
            native._require(
                probe_target(target) == geometry
                and target._observe_display() == display,
                "Exact-region geometry/monitor/DPI/native state changed",
            )
            self.bar.check()

        unchanged()
        with _exact_region_source() as full, _exact_region_source() as exact:
            monitor = full.monitors[0]
            desktop = Rect(
                monitor["left"], monitor["top"], monitor["width"], monitor["height"]
            )
            native._require(
                full.monitors == exact.monitors and desktop.intersected(area) == area,
                "Exact-region desktop bounds differ or would clip the area",
            )
            result["desktop_bounds"] = asdict(desktop)
            for trial in range(_EXACT_REGION_TRIALS + 1):
                timings: dict[str, object] = {}
                for label, source, bounds in (
                    ("A_full", full, desktop),
                    ("B_exact", exact, area),
                    ("C_full", full, desktop),
                ):
                    self.bar.check()
                    image, timings[label] = _exact_region_frame(source, bounds, area)
                    self.bar.check()
                    native._require(
                        image == original.image,
                        f"Exact-region {label} pixels differ from stable original",
                    )
                    if trial == _EXACT_REGION_TRIALS:
                        native._require(
                            image.save(str(self.output / f"exact-region-{label}.png")),
                            "Could not retain the final exact-region triplet",
                        )
                unchanged()
                if trial:
                    trials.append(timings)
        unchanged()
        result["passed"] = True

    def move(self, name: str, percent: float, before: _InverseSample) -> _InverseSample:
        self.guard(before)
        native._require(
            len(self.writes) < _GEOMETRY_WRITES
            and math.isfinite(percent)
            and 0 <= percent <= END_PERCENT,
            "Geometry experiment write is out of bounds",
        )
        write: dict[str, object] = {
            "phase": name,
            "requested_percent": percent,
            "outcome": "unresolved",
        }
        self.writes.append(write)
        self.bar.target.move(percent)
        after = self.phase(name)
        write.update(outcome="observed", observed=after.record())
        return after

    def fixed(self, original: _InverseSample, current: _InverseSample) -> None:
        width, height = round(self.bar.header.width), round(self.bar.header.height)
        footer_top = round(self.bar.rail.height)
        checks = {
            "header_pixels_exact": original.image.copy(0, 0, width, height)
            == current.image.copy(0, 0, width, height),
            "footer_and_corner_pixels_exact": original.image.copy(
                0,
                footer_top,
                original.image.width(),
                original.image.height() - footer_top,
            )
            == current.image.copy(
                0,
                footer_top,
                current.image.width(),
                current.image.height() - footer_top,
            ),
        }
        self.evidence[f"{self.evidence['phase']}_fixed_checks"] = checks
        native._require(
            all(checks.values()),
            "Geometry experiment Header/footer/corner pixels changed",
        )

    def half_steps(self, original: _InverseSample) -> _InverseSample:
        witness = ListItemWitness(self.bar.target, self.bar.geometry)
        try:
            witness.observe(original.position)
            self.evidence["original_visible_pair"] = _geometry_pair(witness)
            self.guard(original)
            current = original
            for index in range(2):
                percent = current.position.vertical + (
                    50.0
                    * current.position.view_size
                    / (100.0 - current.position.view_size)
                )
                native._require(
                    current.position.vertical < percent < END_PERCENT,
                    "Geometry experiment half-view request is invalid",
                )
                following = self.move(f"step-{index + 1}", percent, current)
                native._require(
                    following.position.view_size == current.position.view_size
                    and current.position.vertical
                    < following.position.vertical
                    < END_PERCENT,
                    "Geometry experiment half-view page or movement is uncertain",
                )
                self.fixed(original, following)
                observed = witness.observe(following.position)
                self.evidence[f"step-{index + 1}_items"] = observed
                native._require(
                    observed["from_frame"] == index, "Wrong witness continuity"
                )
                offset = round(self.bar.header.height)
                width, height = round(self.body.width), round(self.body.height)
                proof = _geometry_step(
                    self.body,
                    _InverseSample(
                        current.position,
                        current.native,
                        current.image.copy(0, offset, width, height),
                    ),
                    _InverseSample(
                        following.position,
                        following.native,
                        following.image.copy(0, offset, width, height),
                    ),
                    observed,
                )
                self.evidence[f"step-{index + 1}_overlap"] = proof
                native._require(
                    proof["png_overlap_exact"] is True,
                    "Raw item displacement does not match exact PNG overlap",
                )
                self.guard(following)
                current = following
            return current
        finally:
            witness.close()

    def finish(self, original: _InverseSample, current: _InverseSample) -> None:
        endpoint = self.move("endpoint", END_PERCENT, current)
        native._require(
            endpoint.position.vertical == END_PERCENT,
            "Geometry experiment endpoint is not terminal",
        )
        self.fixed(original, endpoint)
        # The old pair has been released. This fresh pair makes no continuity claim.
        geometry = cast("dict[str, object]", self.evidence["endpoint_geometry"])
        tree = cast("dict[str, object]", geometry["uia"])
        nodes = cast("list[dict[str, object]]", tree["nodes"])
        self.evidence["endpoint_raw_list_items"] = [
            node
            for node in nodes
            if node["parent"] == 0 and node["control_type"] == _LIST_ITEM_ROLE
        ]
        witness = ListItemWitness(self.bar.target, geometry)
        try:
            witness._deadline = time.monotonic() + 1.0
            witness._seed()
            self.evidence["endpoint_visible_pair"] = _geometry_pair(witness)
            self.evidence["endpoint_item_continuity"] = False
        finally:
            witness.close()
        self.guard(endpoint)
        requested = _native_inverse_percent(endpoint.native, original.native["pos"])
        self.evidence["computed_restore_percent"] = requested
        final = self.move("restore", requested, endpoint)
        self.fixed(original, final)
        checks = {
            **cast("dict[str, bool]", self.evidence["restore_fixed_checks"]),
            "original_native_position": final.native["pos"] == original.native["pos"],
            "endpoint_page_preserved": final.native["page"] == endpoint.native["page"],
            "original_horizontal": final.position.horizontal
            == original.position.horizontal,
            "all_nonrail_pixels_exact": original.image.copy(
                0, 0, round(self.bar.header.width), original.image.height()
            )
            == final.image.copy(
                0, 0, round(self.bar.header.width), final.image.height()
            ),
        }
        self.evidence["final_checks"] = checks
        native._require(
            all(checks.values()), "Geometry experiment restoration mismatch"
        )
        self.guard(final)
        self.bar.target.check()


def _explorer_step_geometry(
    point: Point, output: Path, owner: ExplorerCase, setup: dict[str, object]
) -> None:
    """Observe two half-views, one endpoint, one native-position restore, then stop."""
    setup.update(
        success=False, product_capture_qualification=False, experiment_passed=False
    )
    evidence: dict[str, object] = {"native_units_are_pixels": False}
    setup["step_geometry"] = evidence
    native._require(
        os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
        "Geometry experiment requires a disposable hosted runner",
    )
    window = owner._verified_window()
    if owner.pid is None:
        raise RuntimeError("Geometry experiment lacks the retained fixture PID")
    with UiaScrollTarget(point, window=window.handle) as target:
        bar = _InverseScrollBar(target, owner.pid, two_rails=True)
        experiment = _StepGeometry(owner, bar, output, evidence)
        native._require(
            experiment.window == window == owner.window,
            "Geometry experiment window changed before the original sample",
        )
        original = experiment.phase("original")
        native._require(
            original.native["pos"] == original.native["min"]
            and original.position.vertical == 0.0
            and 0 < original.position.view_size < END_PERCENT,
            "Geometry experiment requires a fresh original top view",
        )
        experiment.exact_region(original)
        current = experiment.half_steps(original)
        experiment.finish(original, current)
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

    def __init__(self, *, taller: bool = False) -> None:
        self.taller = taller
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
        return self._prepare_taller(current) if self.taller else self.prepare(current)

    def _prepare_taller(self, window: WindowInfo) -> WindowInfo:
        native._require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Taller Explorer setup requires a disposable hosted runner",
        )
        user = c.WinDLL("user32", use_last_error=True)
        user.SystemParametersInfoW.argtypes = (w.UINT, w.UINT, c.c_void_p, w.UINT)
        user.SystemParametersInfoW.restype = w.BOOL
        user.GetWindowRect.argtypes = (w.HWND, c.POINTER(w.RECT))
        user.GetWindowRect.restype = w.BOOL
        work = w.RECT()
        native._require(
            user.SystemParametersInfoW(48, 0, c.byref(work), 0),
            "Could not read the taller fixture work area",
        )
        area = Rect(work.left, work.top, work.right - work.left, work.bottom - work.top)
        requested = Rect(
            area.left + 10,
            area.top + 10,
            min(native._WINDOW_WIDTH, area.width - 20),
            min(_TALL_EXPLORER_HEIGHT, area.height - 20),
        )
        native._require(
            requested.width > 0 and requested.height > native._WINDOW_HEIGHT,
            "Work area cannot fit a taller representative Explorer window",
        )
        # Reuse exactly the owned fixture positioning path; only its height changes.
        with patch.object(native, "_WINDOW_HEIGHT", _TALL_EXPLORER_HEIGHT):
            prepared = self.prepare(window)
        current = self._verified_window()
        bounds = w.RECT()
        native._require(
            user.GetWindowRect(current.handle, c.byref(bounds)),
            "Could not read the taller fixture outer rectangle",
        )
        actual = Rect(
            bounds.left,
            bounds.top,
            bounds.right - bounds.left,
            bounds.bottom - bounds.top,
        )
        self.report["explorer_layout"] = {
            "name": "taller-h0",
            "work_area": asdict(area),
            "requested_outer": asdict(requested),
            "actual_outer": asdict(actual),
            "visible_bounds": asdict(current.bounds),
        }
        native._require(
            prepared == current
            and actual == requested
            and area.intersected(actual) == actual
            and area.intersected(current.bounds) == current.bounds,
            "Taller fixture geometry changed or exceeded its verified work area",
        )
        return current

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
            if kind == _HELPER_OUTPUT:
                _helper_output_case(self, output, report)
            elif kind in _EXPERIMENTS:
                self._inverse_case(output, report, kind=kind)
            else:
                _case(kind, output, report)

    def _inverse_case(
        self,
        output: Path,
        report: dict[str, object],
        *,
        kind: str = _INVERSE_EXPERIMENT,
    ) -> None:
        """Run a fresh fixture experiment, never the product capture qualification."""
        native._require(
            os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Explorer inverse experiment requires a disposable hosted runner",
        )
        window = self._prepare(self._verified_window())
        report.update(
            window=asdict(window),
            oracle=(
                "test-only four-write step geometry experiment"
                if kind == _GEOMETRY_EXPERIMENT
                else "test-only one-write inverse-native-position experiment"
            ),
            success=False,
            experiment_passed=False,
            product_capture_qualification=False,
        )
        request = {
            "kind": kind,
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
            key = (
                "step_geometry_experiment"
                if kind == _GEOMETRY_EXPERIMENT
                else "inverse_restore_experiment"
            )
            report[key] = json.loads(evidence.read_text(encoding="utf-8"))
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


def _helper_pixels(before: QImage, after: QImage, width: int, footer: int) -> None:
    """Require the exact common prefix and entire footer, never align or repair."""
    height = min(before.height(), after.height()) - footer
    native._require(
        not before.isNull()
        and not after.isNull()
        and before.width() == after.width()
        and 0 < width < before.width()
        and 0 < footer < min(before.height(), after.height())
        and before.copy(0, 0, width, height) == after.copy(0, 0, width, height)
        and before.copy(0, before.height() - footer, before.width(), footer)
        == after.copy(0, after.height() - footer, after.width(), footer),
        "Helper output has different Header/body/focus/footer/corner pixels",
    )


def _helper_observe(
    bar: _InverseScrollBar, output: Path, phase: str
) -> tuple[_InverseSample, dict[str, object]]:
    """Reuse retained ownership for two fixed reads outside helper timing."""
    first = bar.read()
    geometry = probe_target(bar.target)
    rows = native._restoration_native(bar.target, geometry)
    second = bar.read()
    repeated = probe_target(bar.target)
    evidence = {"geometry": geometry, "native_rows": rows, **first.record()}
    (output / f"{phase}.json").write_text(
        json.dumps({**evidence, "repeat": repeated, "repeat_state": second.record()}),
        encoding="utf-8",
    )
    native._require(first.image.save(str(output / f"{phase}.png")), "PNG save failed")
    native._require(
        second.image.save(str(output / f"{phase}-repeat.png")), "PNG save failed"
    )
    native._require(
        first == second
        and geometry == repeated
        and geometry["position"] == asdict(first.position)
        and list_layout(geometry) == (bar.area, bar.header, bar.footer, bar.rail)
        and geometry["window_dpi"] == bar.target.registration_dpi == _SCALED_FIXTURE_DPI
        and first.position.horizontal == first.position.vertical == 0.0,
        "Helper baseline/restoration changed during repeated observation",
    )
    return first, evidence


def _helper_match(
    before: tuple[_InverseSample, dict[str, object]],
    after: tuple[_InverseSample, dict[str, object]],
) -> tuple[int, int]:
    """Prove same native owners/range, raw item order and common rendering."""
    first, original = before
    last, current = after
    geometry = cast("dict[str, object]", original["geometry"])
    changed = cast("dict[str, object]", current["geometry"])
    area, header, footer, rail = list_layout(geometry)
    small, head, foot, side = list_layout(changed)
    if footer is None or foot is None or rail is None or side is None:
        raise RuntimeError("Helper comparison requires the measured two-rail layout")
    native._require(
        (area.x, area.y, area.width) == (small.x, small.y, small.width)
        and area.height >= small.height
        and header == head
        and (footer.x, footer.width, footer.height) == (foot.x, foot.width, foot.height)
        and (rail.x, rail.y, rail.width) == (side.x, side.y, side.width)
        and geometry["window_dpi"] == changed["window_dpi"] == _SCALED_FIXTURE_DPI
        and first.position.horizontal == last.position.horizontal == 0.0
        and first.position.vertical == last.position.vertical == 0.0,
        "Helper reference layout/DPI/H/start differs",
    )
    rows = cast("list[dict[str, object]]", current["native_rows"])
    previous = cast("list[dict[str, object]]", original["native_rows"])
    native._failure_native_identity(rows, previous)
    for row, old in zip(rows, previous, strict=True):
        if row["bar"] == _SB_CTL:
            native._require(
                all(
                    type(row[key]) is int and row[key] == old[key]
                    for key in ("min", "max", "pos")
                )
                and all(cast("int", row[key]) > 0 for key in ("parent", "root", "pid")),
                "Helper reference native range/position/identity differs",
            )
    body = Rect(small.left, head.bottom, head.width, foot.top - head.bottom)

    def items(value: dict[str, object]) -> list[Rect]:
        tree = cast("dict[str, object]", value["uia"])
        nodes = cast("list[dict[str, object]]", tree["nodes"])
        return [
            raw_bounds(node["bounds"])
            for node in nodes
            if node["parent"] == 0
            and node["control_type"] == _LIST_ITEM_ROLE
            and not node["offscreen"]
            and not raw_bounds(node["bounds"]).intersected(body).is_empty
        ]

    raw = items(geometry)
    native._require(
        len(raw) >= _GEOMETRY_PAIR
        and raw == items(changed)
        and all(
            a.bottom <= b.top and (a.x, a.width, a.height) == (b.x, b.width, b.height)
            for a, b in pairwise(raw)
        ),
        "Helper reference raw visible-item shape/order differs",
    )
    width, fixed = round(head.width), round(foot.height)
    _helper_pixels(first.image, last.image, width, fixed)
    return width, fixed


def _helper_inventory(folder: Path) -> list[tuple[str, int, int, str]]:
    """Keep file content and displayed modification metadata in the same fixture."""
    return [
        (
            path.name,
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(folder.iterdir())
    ]


def _helper_shrink(owner: ExplorerCase, window: WindowInfo) -> WindowInfo:
    """Change only the current owned window's height, without focus/input writes."""
    native._require(owner._verified_window() == window, "Reference owner changed")
    layout = cast("dict[str, object]", owner.report["explorer_layout"])
    outer = raw_bounds(layout["actual_outer"])
    user = c.WinDLL("user32", use_last_error=True)
    user.MoveWindow.argtypes = (w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.BOOL)
    user.MoveWindow.restype = w.BOOL
    native._require(
        user.MoveWindow(
            window.handle,
            round(outer.x),
            round(outer.y),
            round(outer.width),
            native._WINDOW_HEIGHT,
            1,
        ),
        "Could not shrink the same fixture height",
    )
    time.sleep(1)
    small = owner._verified_window()
    user.GetWindowRect.argtypes = (w.HWND, c.POINTER(w.RECT))
    user.GetWindowRect.restype = w.BOOL
    bounds = w.RECT()
    native._require(
        user.GetWindowRect(small.handle, c.byref(bounds))
        and (bounds.left, bounds.top, bounds.right, bounds.bottom)
        == (outer.left, outer.top, outer.right, outer.top + native._WINDOW_HEIGHT)
        and small.handle == window.handle,
        "Height-only fixture resize differs",
    )
    return small


def _helper_output_case(
    owner: ExplorerCase, output: Path, report: dict[str, object]
) -> None:
    """One fresh traced reference then one ordinary helper, never a chooser claim."""
    report.update(
        success=False,
        helper_output_passed=False,
        product_capture_qualification=False,
        oracle="fresh-reference final-output/helper-lifecycle smoke",
        vertical_rail_raster_provenance="unverified across heights; full PNG retained",
    )
    window = owner._prepare_taller(owner._verified_window())
    report["window"] = asdict(window)
    if owner.folder is None or owner.pid is None:
        raise RuntimeError("Helper output case lacks its owned folder/PID")
    inventory = _helper_inventory(owner.folder)
    report["fixture_inventory"] = inventory
    reference_image = QImage()
    reference_before = None
    for phase in ("reference", "tiny"):
        untraced = phase == "tiny"
        if untraced:
            window = _helper_shrink(owner, window)
        directory = output / phase
        directory.mkdir()
        with UiaScrollTarget(window.bounds.center, window=window.handle) as target:
            bar = _InverseScrollBar(target, owner.pid, two_rails=True)
            before = _helper_observe(bar, directory, "before")
            if reference_before is not None:
                native._require(
                    before[0].image.height() < reference_before[0].image.height(),
                    "Tiny helper viewport did not shrink",
                )
                _helper_match(reference_before, before)
            native._require(
                owner._verified_window() == window
                and _helper_inventory(owner.folder) == inventory,
                "Helper fixture changed",
            )
            lifecycle: dict[str, object] = {}
            report[phase] = lifecycle
            bar.check()
            result = native._capture(
                window.bounds.center,
                directory,
                _HELPER_OUTPUT,
                window=window.handle,
                input_tick=target._input_tick,
                helper_factory=native.ScrollHelper if untraced else None,
                lifecycle=lifecycle,
            )
            # Observer stays idle until actual finished; the traced child has exited.
            lifecycle.update(
                message=result.message,
                frames=result.frames,
                restoration=result.restoration,
            )
            try:
                restored = _helper_observe(bar, directory, "restored")
            finally:
                if result.image is not None:
                    native._require(
                        result.image.save(str(directory / "result.png")),
                        "Output PNG save failed",
                    )
            width, footer = _helper_match(before, restored)
            native._require(
                result.image is not None and result.restoration == "restored",
                result.message,
            )
            image = cast("QImage", result.image)
            if untraced:
                native._require(
                    image.size() == reference_image.size(),
                    "Helper output dimensions differ",
                )
                _helper_pixels(reference_image, image, width, footer)
            else:
                oracle = verify_sequence(image, directory)
                lifecycle.update(oracle)
                native._require(
                    oracle["oracle_frames"] == result.frames,
                    "Reference lacks every frame",
                )
                _helper_pixels(
                    before[0].image,
                    QImage(str(directory / "frame-0.png")),
                    width,
                    footer,
                )
                reference_image, reference_before = image, before
            native._require(
                owner._verified_window() == window
                and _helper_inventory(owner.folder) == inventory,
                "Helper fixture changed",
            )
            # File/oracle/shell work must not hide a late eligibility change.
            bar.check()
            native._require(
                target.position() == restored[0].position,
                "Helper position changed before final acceptance",
            )
            bar.check()
    report.update(
        success=True,
        helper_output_passed=True,
        restored_pixels="exact non-rail and full footer; native position/H exact",
        result_width=image.width(),
        result_height=image.height(),
    )


def main() -> None:
    """Keep diagnostics separate from the colored-row qualification oracle."""
    kind = sys.argv[1]
    layout = kind.removesuffix("-click").removesuffix("-enter")
    if kind in _FOCUSED_PANELS:
        layout = "edge-panel"
    native._require(
        kind in (*_EXPERIMENTS, _HELPER_OUTPUT)
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
    is_explorer = layout.startswith("explorer")
    taller = sys.argv[2:] == ["--taller-explorer"]
    native._require(
        not sys.argv[2:] or (taller and is_explorer and kind not in _EXPERIMENTS),
        "Only Explorer capture cases accept --taller-explorer",
    )
    explorer = ExplorerCase(taller=taller)
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
            gui=kind not in (*_EXPERIMENTS, _HELPER_OUTPUT),
            case=explorer.run if is_explorer else _case,
        )


if __name__ == "__main__":
    main()
